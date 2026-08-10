"""Chart-spec generation: recipe precedence, suggestion tie-break,
heuristics, referent-bearing rows, truncation/suppression annotations."""

from __future__ import annotations

from datetime import date, datetime

from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_presentation import ChartSuggestion, RecipeSpec, build_chart_spec, build_chart_specs

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _frame(
    columns: tuple[FrameColumn, ...],
    rows: tuple[tuple[object, ...], ...],
    *,
    truncated: bool = False,
    suppressed: int = 0,
) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
        truncated=truncated,
        suppressed_cells=suppressed,
    )


def _payer_cash(truncated: bool = False, suppressed: int = 0) -> EvidenceFrame:
    return _frame(
        (
            FrameColumn("payer", DimensionRef("payer")),
            FrameColumn("cash_posted", MetricRef("cash_posted"), 1, "money_cents"),
        ),
        (("Atlas Commercial", 100), ("State Medicaid", 50)),
        truncated=truncated,
        suppressed=suppressed,
    )


def _weekly_cash() -> EvidenceFrame:
    """Three weekly buckets — the one shape a line may be drawn over.

    Fewer points, or an axis with no order, and the shape rule demotes it
    (see ``test_period_axis.py``), so the selection tests below exercise
    recipe/suggestion precedence on a frame that can actually carry the
    type they ask for.
    """
    return _frame(
        (
            FrameColumn("week", DimensionRef("time_bucket:week")),
            FrameColumn("cash_posted", MetricRef("cash_posted"), 1, "money_cents"),
        ),
        ((date(2026, 7, 13), 90), (date(2026, 7, 20), 110), (date(2026, 7, 27), 100)),
    )


class TestChartSelection:
    def test_recipe_takes_precedence(self) -> None:
        recipe = RecipeSpec(id="cash_trend", applies_to="cash_posted", chart_type="line")
        spec = build_chart_spec("main", _weekly_cash(), recipes=(recipe,))
        assert spec is not None
        assert spec.chart_type == "line" and spec.recipe_id == "cash_trend"

    def test_playbook_recipe_matches_and_type_aliases_map(self) -> None:
        recipe = RecipeSpec(
            id="scorecard_table", applies_to="scorecard", chart_type="table_bars"
        )
        spec = build_chart_spec(
            "main", _payer_cash(), recipes=(recipe,), playbook_id="scorecard"
        )
        assert spec is not None
        assert spec.chart_type == "table"  # table_bars maps into the closed set

    def test_suggestion_breaks_ties_only_without_recipe(self) -> None:
        suggestion = ChartSuggestion(chart_type="stacked_bar", x="payer", value="cash_posted")
        spec = build_chart_spec("main", _payer_cash(), suggestion=suggestion)
        assert spec is not None and spec.chart_type == "stacked_bar"
        # ``waterfall`` rather than ``line``: a line over two payer names is
        # demoted by the shape rule after selection, which would hide the
        # thing this test is about (see ``test_period_axis.py``).
        recipe = RecipeSpec(id="r", applies_to="cash_posted", chart_type="waterfall")
        overridden = build_chart_spec(
            "main", _payer_cash(), recipes=(recipe,), suggestion=suggestion
        )
        assert overridden is not None and overridden.chart_type == "waterfall"

    def test_heuristics_time_line_dim_bar(self) -> None:
        line = build_chart_spec("trend", _weekly_cash())
        assert line is not None and line.chart_type == "line" and line.x == "week"
        bar = build_chart_spec("main", _payer_cash())
        assert bar is not None and bar.chart_type == "bar"

    def test_measure_only_frame_is_one_labelled_column(self) -> None:
        """A frame with no dimension has one mark, and its category is the
        WINDOW. It was once typed ``table``, which said nothing about the
        shape; it is now a single-category ``bar`` — the closed
        ``ChartType`` set's spelling of a stat figure — on the ``period``
        axis rather than on the measure column."""
        totals = _frame(
            (FrameColumn("cash_posted", MetricRef("cash_posted"), 1, "money_cents"),),
            ((100,),),
        )
        spec = build_chart_spec("totals", totals)
        assert spec is not None and spec.chart_type == "bar" and spec.x == "period"


class TestRowsAndAnnotations:
    def test_rows_carry_referent_ids_for_drill_into(self) -> None:
        referents = {("payer", "Atlas Commercial"): "D1", ("payer", "State Medicaid"): "D2"}
        spec = build_chart_spec("main", _payer_cash(), row_referents=referents)
        assert spec is not None
        assert [(r.x, r.referent_id) for r in spec.rows] == [
            ("Atlas Commercial", "D1"),
            ("State Medicaid", "D2"),
        ]

    def test_truncation_and_suppression_annotated(self) -> None:
        spec = build_chart_spec("main", _payer_cash(truncated=True, suppressed=3))
        assert spec is not None
        assert any("truncated" in a for a in spec.annotations)
        assert any("suppression: 3" in a for a in spec.annotations)

    def test_build_chart_specs_skips_prior_and_rank_frames(self) -> None:
        frames = (
            ("main", _payer_cash()),
            ("main__prior", _payer_cash()),
            ("main__compare__rank", _payer_cash()),
        )
        specs = build_chart_specs(frames)
        assert [s.frame_id for s in specs] == ["main"]

    def test_dimensionless_frames_rank_after_dimensional(self) -> None:
        totals = _frame(
            (FrameColumn("cash_posted", MetricRef("cash_posted"), 1, "money_cents"),),
            ((100,),),
        )
        specs = build_chart_specs((("totals", totals), ("main", _payer_cash())), limit=2)
        assert [s.frame_id for s in specs] == ["main", "totals"]


class TestResolvedSortReachesTheSpec:
    """The findings obeyed "best to worst" while the chart directly beneath
    them was drawn in frame order, because the ordering the plan resolved
    never reached the renderer. It is passed through here — never derived —
    so a chart with no plan ordering says so rather than implying one."""

    def test_the_resolved_ordering_is_published(self) -> None:
        spec = build_chart_spec("main", _payer_cash(), sort=("cash_posted", True))
        assert spec is not None
        assert spec.sort is not None
        assert (spec.sort.by, spec.sort.direction) == ("cash_posted", "desc")

    def test_an_ascending_resolution_survives(self) -> None:
        spec = build_chart_spec("main", _payer_cash(), sort=("cash_posted", False))
        assert spec is not None and spec.sort is not None
        assert spec.sort.direction == "asc"

    def test_no_ordering_publishes_none(self) -> None:
        spec = build_chart_spec("main", _payer_cash())
        assert spec is not None and spec.sort is None

    def test_a_column_this_chart_does_not_draw_is_refused(self) -> None:
        """A sort naming a column the renderer cannot see is a hint it must
        either ignore or obey wrongly, and both are worse than none."""
        spec = build_chart_spec("main", _payer_cash(), sort=("denied_dollars", True))
        assert spec is not None and spec.sort is None

    def test_the_batch_builder_routes_each_frames_own_ordering(self) -> None:
        specs = build_chart_specs(
            (("main", _payer_cash()),), sorts={"main": ("cash_posted", True)}
        )
        assert [s.sort and s.sort.by for s in specs] == ["cash_posted"]

    def test_a_frame_absent_from_the_map_publishes_none(self) -> None:
        specs = build_chart_specs(
            (("main", _payer_cash()),), sorts={"other": ("cash_posted", True)}
        )
        assert [s.sort for s in specs] == [None]
