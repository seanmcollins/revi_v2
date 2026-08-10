"""FIX-8: a chart's category axis may not be one of its own numbers.

Verified live this hour. The paid turn "Why did our denial rate go up in
July 2026?" published a chart whose entire category axis was the single
tick ``0.127591`` — the answer's own denial rate, filed as though it were
the name of a group. A tenant-wide scan found 18 of 85 stored chart_specs
in that state, including "What is my denial rate?" and "days in A/R for
Atlas Commercial" (axis ``179.468320``). On the two-window comparison it
was worse than cosmetic: the PRIOR series (0.091386) was filed under the
CURRENT value's category key, so both series collided on one bogus key and
a renderer keying by ``(x, series)``… had two marks claiming to be the same
category. Root cause, one line: ``x = time_col or (dims[0] if dims else
value)`` — with no dimension and no time bucket the MEASURE column became
the x column, so ``row[index_of(x)]`` was the measured number itself.

The work order this file tests, quoted:

    "When a frame has no dimension, the category axis is the WINDOW:
    period labels ('Jul 2026' / 'Jun 2026') for a comparison, one labelled
    column for a scalar — never a formatted value as a category key, never
    two series sharing a key derived from one of their values. Decide chart
    kind server-side from frame shape (1 category -> stat figure, 2 periods
    -> paired bars, >=3 ordered -> line). Contract assertion: no chart
    rows[].x parses as a bare number when the frame has a unit."

The related half is the same surface: 16 stored specs declared chart_type
``line`` with two categories or fewer (the web client silently drew bars,
so the wire and the screen disagreed about what the answer was), and lines
were drawn across ``payer`` and ``facility`` axes, asserting movement
between categories that are in no order at all.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_presentation import (
    ChartWindow,
    RecipeSpec,
    build_chart_spec,
    build_chart_specs,
    period_label,
)
from revi_presentation.charts import (
    CURRENT_SERIES,
    DEFAULT_WINDOW_KEY,
    PERIOD_SERIES_COLUMN,
    PRIOR_SERIES,
    UNNAMED_CURRENT_LABEL,
    UNNAMED_PRIOR_LABEL,
    window_from_facts,
)

WATERMARK = DataWatermark(
    id="wm_fix8", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)

#: The live turn's own figures, to the digit.
DENIAL_RATE_JULY = 0.127591
DENIAL_RATE_JUNE = 0.091386
#: "days in A/R for Atlas Commercial" — the other axis the scan found.
DAYS_IN_AR = 179.468320

JULY = ChartWindow(current_label="Jul 2026", prior_label="Jun 2026")


class _Header:
    """The four dates a turn's context header carries (:class:`WindowFacts`).

    A stand-in for ``ContextHeaderPayload``, because presentation takes
    those dates structurally and must not import the contract to read them.
    """

    def __init__(
        self,
        window_start: date,
        window_end: date,
        comparison_start: date | None = None,
        comparison_end: date | None = None,
    ) -> None:
        self.window_start = window_start
        self.window_end = window_end
        self.comparison_start = comparison_start
        self.comparison_end = comparison_end


def _frame(
    columns: tuple[FrameColumn, ...], rows: tuple[tuple[object, ...], ...]
) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


def _scalar_rate() -> EvidenceFrame:
    """"What is my denial rate?" — one measure, no dimension, no bucket."""
    return _frame(
        (FrameColumn("denial_rate", MetricRef("denial_rate"), 1, "rate"),),
        ((DENIAL_RATE_JULY,),),
    )


def _compared_rate() -> EvidenceFrame:
    """"Why did our denial rate go up in July 2026?" — July against June."""
    return _frame(
        (
            FrameColumn("denial_rate", MetricRef("denial_rate"), 1, "rate"),
            FrameColumn("denial_rate__prior", MetricRef("denial_rate"), 1, "rate"),
            FrameColumn("denial_rate__delta", MetricRef("denial_rate"), 1, "rate"),
        ),
        ((DENIAL_RATE_JULY, DENIAL_RATE_JUNE, DENIAL_RATE_JULY - DENIAL_RATE_JUNE),),
    )


def _parses_as_a_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


class TestTheCategoryAxisIsTheWindow:
    """"When a frame has no dimension, the category axis is the WINDOW …
    never a formatted value as a category key.\""""

    def test_a_scalar_frame_charts_one_row_labelled_with_its_period(self) -> None:
        spec = build_chart_spec("main", _scalar_rate(), window=JULY)

        assert spec is not None
        assert spec.x == PERIOD_SERIES_COLUMN
        assert [(row.x, row.value) for row in spec.rows] == [("Jul 2026", DENIAL_RATE_JULY)]
        # The defect, stated as the assertion that would have caught it.
        assert spec.rows[0].x != str(DENIAL_RATE_JULY)
        assert not _parses_as_a_number(spec.rows[0].x)

    def test_a_comparison_charts_two_rows_on_two_different_period_labels(self) -> None:
        """"never two series sharing a key derived from one of their
        values" — live, both marks were filed under ``0.127591``."""
        spec = build_chart_spec("main__compare", _compared_rate(), window=JULY)

        assert spec is not None
        drawn = [(row.x, row.series, row.value) for row in spec.rows]
        # Chronological: two ticks on one axis read left to right as time.
        assert drawn == [
            ("Jun 2026", PRIOR_SERIES, DENIAL_RATE_JUNE),
            ("Jul 2026", CURRENT_SERIES, DENIAL_RATE_JULY),
        ]
        assert len({row.x for row in spec.rows}) == 2
        assert not any(_parses_as_a_number(row.x) for row in spec.rows)
        # …and the rows stay uniquely addressable by the axes the spec
        # declares, which is what the collision destroyed.
        assert len({(row.x, row.series) for row in spec.rows}) == len(spec.rows)

    def test_the_frame_has_a_unit_and_no_row_x_parses_as_a_bare_number(self) -> None:
        """The contract assertion from the work order, over both shapes and
        the second axis the scan named (``179.468320``)."""
        days = _frame(
            (FrameColumn("days_in_ar", MetricRef("days_in_ar"), 1, "days"),),
            ((DAYS_IN_AR,),),
        )
        for frame in (_scalar_rate(), _compared_rate(), days):
            spec = build_chart_spec("main", frame, window=JULY)
            assert spec is not None and spec.unit is not None
            assert not any(_parses_as_a_number(row.x) for row in spec.rows)

    def test_with_no_window_the_axis_is_honest_prose_never_a_number(self) -> None:
        """A turn with no context header still charts; what it must not do
        is fall back to anything derived from the numbers."""
        spec = build_chart_spec("main__compare", _compared_rate())

        assert spec is not None
        assert [row.x for row in spec.rows] == [UNNAMED_PRIOR_LABEL, UNNAMED_CURRENT_LABEL]
        assert not any(_parses_as_a_number(row.x) for row in spec.rows)

    def test_a_comparison_with_no_baseline_dates_keeps_the_current_label(self) -> None:
        spec = build_chart_spec(
            "main__compare", _compared_rate(), window=ChartWindow(current_label="Jul 2026")
        )

        assert spec is not None
        assert [row.x for row in spec.rows] == [UNNAMED_PRIOR_LABEL, "Jul 2026"]

    def test_the_period_axis_carries_no_drill_handle(self) -> None:
        """A period label is not a dimension value: a referent looked up
        for it would compile to a DrillInto on a group that does not
        exist."""
        spec = build_chart_spec(
            "main__compare",
            _compared_rate(),
            row_referents={("period", "Jul 2026"): "D1", ("denial_rate", "0.127591"): "D2"},
            window=JULY,
        )

        assert spec is not None
        assert [row.referent_id for row in spec.rows] == [None, None]

    def test_both_windows_keep_their_series_so_a_renderer_can_colour_them(self) -> None:
        spec = build_chart_spec("main__compare", _compared_rate(), window=JULY)

        assert spec is not None
        assert spec.series == PERIOD_SERIES_COLUMN
        assert {row.series for row in spec.rows} == {CURRENT_SERIES, PRIOR_SERIES}

    def test_the_comparison_annotation_names_the_two_windows(self) -> None:
        """The dimension-axis wording ("two series per category") is false
        here — these marks are not two series on ONE category — so the
        period case says what it actually drew."""
        spec = build_chart_spec("main__compare", _compared_rate(), window=JULY)

        assert spec is not None
        note = next(a for a in spec.annotations if a.startswith("comparison:"))
        assert "Jul 2026" in note and "Jun 2026" in note
        assert "two series per category" not in note
        assert "not summed" in note

    def test_a_dimension_axis_is_untouched(self) -> None:
        """The rule fires only where there is no category to key marks by:
        a frame WITH a dimension still charts that dimension, and its
        comparison still pairs both windows inside one category."""
        frame = _frame(
            (
                FrameColumn("payer", DimensionRef("payer")),
                FrameColumn("denial_rate", MetricRef("denial_rate"), 1, "rate"),
                FrameColumn("denial_rate__prior", MetricRef("denial_rate"), 1, "rate"),
            ),
            (("Atlas Commercial", DENIAL_RATE_JULY, DENIAL_RATE_JUNE),),
        )

        spec = build_chart_spec("main__compare", frame, window=JULY)

        assert spec is not None and spec.x == "payer"
        assert [row.x for row in spec.rows] == ["Atlas Commercial", "Atlas Commercial"]
        assert any("two series per category" in a for a in spec.annotations)

    def test_a_dimensionless_frame_is_not_told_its_measured_zero_is_an_absence(
        self,
    ) -> None:
        """``prior-only categories`` counts join keys the current window
        never returned. With no dimension there is no join key, so a
        current 0 is a measured 0 and calling it an absence is the
        opposite error."""
        frame = _frame(
            (
                FrameColumn("denied_dollars", MetricRef("denied_dollars"), 1, "money_cents"),
                FrameColumn(
                    "denied_dollars__prior", MetricRef("denied_dollars"), 1, "money_cents"
                ),
            ),
            ((0, 250_000),),
        )

        spec = build_chart_spec("main__compare", frame, window=JULY)

        assert spec is not None
        assert not any("prior-only" in a for a in spec.annotations)


class TestChartKindFollowsTheShape:
    """"Decide chart kind server-side from frame shape (1 category -> stat
    figure, 2 periods -> paired bars, >=3 ordered -> line).\""""

    def test_a_recipe_cannot_force_a_line_onto_a_single_point(self) -> None:
        """The live case exactly: the pack's ``denial_rate_trend`` recipe
        asks for a line on every ``denial_rate`` frame, and this frame is
        one point. The shape rule runs last and overrides the recipe."""
        recipe = RecipeSpec(
            id="denial_rate_trend", applies_to="denial_rate", chart_type="line"
        )

        spec = build_chart_spec("main", _scalar_rate(), recipes=(recipe,), window=JULY)

        assert spec is not None
        assert spec.chart_type == "bar"
        # The recipe still WON selection — it is named on the wire — so the
        # demotion is stated rather than leaving the two contradicting.
        assert spec.recipe_id == "denial_rate_trend"
        assert any(a.startswith("chart type: drawn as bar") for a in spec.annotations)

    def test_two_periods_are_paired_bars_not_a_line(self) -> None:
        recipe = RecipeSpec(
            id="denial_rate_trend", applies_to="denial_rate", chart_type="line"
        )

        spec = build_chart_spec(
            "main__compare", _compared_rate(), recipes=(recipe,), window=JULY
        )

        assert spec is not None and spec.chart_type == "bar"
        assert len(spec.rows) == 2

    def test_a_line_is_refused_over_an_axis_with_no_order(self) -> None:
        """Live, lines were drawn across ``payer`` and ``facility``: the
        order of those categories is whatever the sort left them in, and
        the slope between two of them is a rate of change of nothing."""
        payers = _frame(
            (
                FrameColumn("payer", DimensionRef("payer")),
                FrameColumn("denial_rate", MetricRef("denial_rate"), 1, "rate"),
            ),
            (("Atlas Commercial", 0.11), ("State Medicaid", 0.09), ("Lakewood", 0.14)),
        )
        recipe = RecipeSpec(
            id="denial_rate_trend", applies_to="denial_rate", chart_type="line"
        )

        spec = build_chart_spec("main", payers, recipes=(recipe,))

        assert spec is not None and spec.chart_type == "bar"

    def test_three_ordered_points_are_still_a_line(self) -> None:
        """The rule must not cost the product the charts that are true:
        three weekly buckets on a time axis remain a trend."""
        weekly = _frame(
            (
                FrameColumn("week", DimensionRef("time_bucket:week")),
                FrameColumn("denial_rate", MetricRef("denial_rate"), 1, "rate"),
            ),
            ((date(2026, 7, 6), 0.09), (date(2026, 7, 13), 0.10), (date(2026, 7, 20), 0.12)),
        )

        spec = build_chart_spec("trend", weekly)

        assert spec is not None and spec.chart_type == "line"

    def test_two_buckets_on_a_time_axis_are_bars(self) -> None:
        """A segment between two points asserts a direction and nothing
        else — sixteen stored specs did this and the client redrew them as
        bars, so the wire and the screen disagreed."""
        fortnight = _frame(
            (
                FrameColumn("week", DimensionRef("time_bucket:week")),
                FrameColumn("denial_rate", MetricRef("denial_rate"), 1, "rate"),
            ),
            ((date(2026, 7, 13), 0.10), (date(2026, 7, 20), 0.12)),
        )

        spec = build_chart_spec("trend", fortnight)

        assert spec is not None and spec.chart_type == "bar"


class TestPeriodLabel:
    """Whole periods read the way an analyst says them; anything else keeps
    its edges. Never a bare number except the whole-year form, which names
    a window rather than measuring anything."""

    def test_a_whole_calendar_month(self) -> None:
        assert period_label(date(2026, 7, 1), date(2026, 7, 31)) == "Jul 2026"
        assert period_label(date(2026, 2, 1), date(2026, 2, 28)) == "Feb 2026"

    def test_a_whole_quarter(self) -> None:
        assert period_label(date(2026, 4, 1), date(2026, 6, 30)) == "Q2 2026"
        assert period_label(date(2026, 10, 1), date(2026, 12, 31)) == "Q4 2026"

    def test_a_whole_year(self) -> None:
        assert period_label(date(2026, 1, 1), date(2026, 12, 31)) == "2026"

    def test_anything_else_keeps_its_edges(self) -> None:
        assert period_label(date(2026, 7, 1), date(2026, 8, 2)) == "Jul 1 — Aug 2, 2026"
        assert period_label(date(2026, 7, 6), date(2026, 7, 26)) == "Jul 6 — Jul 26, 2026"

    def test_a_range_across_a_year_boundary_names_both_years(self) -> None:
        assert (
            period_label(date(2025, 12, 15), date(2026, 1, 14))
            == "Dec 15, 2025 — Jan 14, 2026"
        )

    def test_one_day_is_one_date(self) -> None:
        assert period_label(date(2026, 8, 2), date(2026, 8, 2)) == "Aug 2, 2026"

    def test_a_partial_month_is_not_promoted_to_the_whole_month(self) -> None:
        """The window a turn ran is the window the axis names: July 1—30 is
        not July, and a label that rounded it up would retitle the answer."""
        assert period_label(date(2026, 7, 1), date(2026, 7, 30)) == "Jul 1 — Jul 30, 2026"


class TestWindowsReachTheBatchBuilder:
    def test_a_context_header_is_read_for_its_dates(self) -> None:
        """What the API passes: the payload the turn already holds, so the
        call site needs no presentation import."""
        header = _Header(date(2026, 7, 1), date(2026, 7, 31), date(2026, 6, 1), date(2026, 6, 30))

        specs = build_chart_specs((("main__compare", _compared_rate()),), windows=header)

        assert [row.x for row in specs[0].rows] == ["Jun 2026", "Jul 2026"]

    def test_a_header_with_no_comparison_names_only_this_window(self) -> None:
        header = _Header(date(2026, 7, 1), date(2026, 7, 31))

        assert window_from_facts(header) == ChartWindow(current_label="Jul 2026")

    def test_an_explicit_per_frame_mapping_wins_over_the_default_entry(self) -> None:
        specs = build_chart_specs(
            (("main", _scalar_rate()), ("other", _scalar_rate())),
            windows={
                "main": ChartWindow(current_label="Q2 2026"),
                DEFAULT_WINDOW_KEY: ChartWindow(current_label="Jul 2026"),
            },
        )

        assert {s.frame_id: s.rows[0].x for s in specs} == {
            "main": "Q2 2026",
            "other": "Jul 2026",
        }

    def test_no_windows_at_all_still_publishes_a_chart(self) -> None:
        specs = build_chart_specs((("main", _scalar_rate()),))

        assert [row.x for row in specs[0].rows] == [UNNAMED_CURRENT_LABEL]


def test_the_defect_axis_can_no_longer_be_produced() -> None:
    """The scan's own signature, over every shape this module charts: no
    published row key is the string form of the value beside it."""
    frames = (
        ("main", _scalar_rate()),
        ("main__compare", _compared_rate()),
    )
    specs = build_chart_specs(frames, windows=_Header(date(2026, 7, 1), date(2026, 7, 31)))

    assert specs
    for spec in specs:
        for row in spec.rows:
            assert row.x != str(row.value)
            assert not _parses_as_a_number(row.x)


@pytest.mark.parametrize("label", [UNNAMED_CURRENT_LABEL, UNNAMED_PRIOR_LABEL])
def test_the_fallback_labels_are_prose(label: str) -> None:
    assert label and not _parses_as_a_number(label)
