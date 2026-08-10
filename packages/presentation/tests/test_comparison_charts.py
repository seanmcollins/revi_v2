"""Round-5 D-02: a month-over-month answer draws a month-over-month chart.

Three personas, verified at source. ``build_chart_specs`` skipped the
``__prior`` frame with the comment *"the comparison rides inside the
compare output"* — and it did not: ``build_chart_spec`` charted the CURRENT
column off the compare frame and nothing else. Live, a July-vs-June close
question yielded ``chart_main`` and ``chart_main__compare`` with
byte-identical rows (Lakewood 10240987 in both, while the finding reported
``prior_cents`` 1978647), the client recognised the identical twin and
dropped it, and the reader ended up with ONE chart of July only on an
answer whose title, header chip and every warning were about June against
July. rcm-analyst: *"not a chart I can use at close at all."*

The client's dedupe was also enumerated against a suffix list the server
had already outgrown — ``["__compare", "__prior"]`` against a server
emitting ``denial_code_mix__compare__share`` — so two byte-identical 34-row
stacked bars rendered under one composed title on the flagship premise
answer. Both halves are fixed at the emitter: the comparison is DRAWN, the
superseded frame is not emitted, and whatever survives is deduplicated on
content rather than on a suffix nobody can keep up to date.
"""

from __future__ import annotations

from datetime import date, datetime

from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_presentation import build_chart_spec, build_chart_specs
from revi_presentation.charts import CURRENT_SERIES, PERIOD_SERIES_COLUMN, PRIOR_SERIES

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
THRESHOLD = 11

#: rcm-analyst's own row, at his own figures.
LAKEWOOD = ("Lakewood Medicaid MCO", 10_240_987, 1_978_647)


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


def _current_only() -> EvidenceFrame:
    return _frame(
        (
            FrameColumn("payer", DimensionRef("payer")),
            FrameColumn("denied_dollars", MetricRef("denied_dollars"), 1, "money_cents"),
        ),
        ((LAKEWOOD[0], LAKEWOOD[1]), ("Atlas Commercial", 400_000)),
    )


def _compare(rows: tuple[tuple[object, ...], ...] | None = None) -> EvidenceFrame:
    return _frame(
        (
            FrameColumn("payer", DimensionRef("payer")),
            FrameColumn("denied_dollars", MetricRef("denied_dollars"), 1, "money_cents"),
            FrameColumn("denied_dollars__prior", MetricRef("denied_dollars"), 1, "money_cents"),
            FrameColumn("denied_dollars__delta", MetricRef("denied_dollars"), 1, "money_cents"),
        ),
        rows
        or (
            (LAKEWOOD[0], LAKEWOOD[1], LAKEWOOD[2], LAKEWOOD[1] - LAKEWOOD[2]),
            ("Atlas Commercial", 400_000, 500_000, -100_000),
        ),
    )


class TestTheComparisonIsDrawn:
    def test_both_windows_reach_the_wire_as_two_series(self) -> None:
        spec = build_chart_spec("main__compare", _compare())

        assert spec is not None
        assert spec.series == PERIOD_SERIES_COLUMN
        drawn = {(row.x, row.series): row.value for row in spec.rows}
        assert drawn[(LAKEWOOD[0], CURRENT_SERIES)] == LAKEWOOD[1]
        assert drawn[(LAKEWOOD[0], PRIOR_SERIES)] == LAKEWOOD[2]
        assert any("two series per category" in note for note in spec.annotations)

    def test_a_frame_with_no_prior_is_untouched(self) -> None:
        spec = build_chart_spec("main", _current_only())

        assert spec is not None
        assert spec.series is None
        assert [row.series for row in spec.rows] == [None, None]

    def test_a_frame_already_grouping_by_a_second_dimension_keeps_that_grouping(
        self,
    ) -> None:
        """Two dimensions plus two windows is a third axis, and inventing
        one is worse than drawing the current window and saying so."""
        frame = _frame(
            (
                FrameColumn("payer", DimensionRef("payer")),
                FrameColumn("service_line", DimensionRef("service_line")),
                FrameColumn("denied_dollars", MetricRef("denied_dollars"), 1, "money_cents"),
                FrameColumn(
                    "denied_dollars__prior", MetricRef("denied_dollars"), 1, "money_cents"
                ),
            ),
            (("Lakewood Medicaid MCO", "Imaging", 10, 5),),
        )

        spec = build_chart_spec("main__compare", frame)

        assert spec is not None
        assert spec.series == "service_line"
        assert len(spec.rows) == 1

    def test_a_prior_only_category_is_counted_rather_than_drawn_as_a_collapse(
        self,
    ) -> None:
        """product-designer's mirror case: ``chart_main`` had 31 rows and
        ``chart_main__compare`` 33, the two extras carrying ``value: 0``
        and appearing nowhere on the current side, with nothing on the
        answer explaining the gap."""
        spec = build_chart_spec(
            "main__compare",
            _compare(
                (
                    (LAKEWOOD[0], LAKEWOOD[1], LAKEWOOD[2], LAKEWOOD[1] - LAKEWOOD[2]),
                    ("Retired Plan", 0, 250_000, -250_000),
                )
            ),
        )

        assert spec is not None
        assert any("prior-only categories: 1 of 2" in note for note in spec.annotations)


class TestOneFigurePerFact:
    def test_the_compare_frame_supersedes_the_frame_it_compared(self) -> None:
        """``chart_main`` and ``chart_main__compare`` were byte-identical;
        the client dropped one and the reader got July on a June-vs-July
        answer. The emitter no longer produces the twin."""
        specs = build_chart_specs(
            (("main", _current_only()), ("main__prior", _current_only()),
             ("main__compare", _compare()))
        )

        assert [s.frame_id for s in specs] == ["main__compare"]
        assert any(row.series == PRIOR_SERIES for row in specs[0].rows)

    def test_a_suffix_the_server_invented_cannot_produce_a_second_identical_figure(
        self,
    ) -> None:
        """``denial_code_mix__compare__share`` matched neither entry of the
        client's ``COMPARE_SUFFIXES`` list, so both rendered. A CONTENT key
        cannot be outgrown by the next suffix the planner invents."""
        share = _compare()
        specs = build_chart_specs(
            (("main__compare", _compare()), ("main__compare__share", share))
        )

        assert len(specs) == 1
        assert any("is therefore not drawn twice" in note for note in specs[0].annotations)

    def test_two_genuinely_different_figures_both_survive(self) -> None:
        """The dedupe must not cost the answer a figure that says something
        else: same shape, different rows."""
        other = _frame(
            (
                FrameColumn("service_line", DimensionRef("service_line")),
                FrameColumn("denied_dollars", MetricRef("denied_dollars"), 1, "money_cents"),
            ),
            (("Imaging", 999),),
        )
        specs = build_chart_specs((("main", _current_only()), ("cut", other)))

        assert {s.frame_id for s in specs} == {"main", "cut"}
