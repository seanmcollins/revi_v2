"""Round-6 A-02/A-05: one census, and a trend that admits its ceilings.

**A-02** — two server strings stated different censuses of one frame. On a
live Veritas trend, one payload carried
``Of 8 cell(s) on this answer, 6 carry an upper bound, 0 were withheld
outright and 2 are measured`` and, on the chart over the same eight rows,
``withheld: 1 of 8 cells were withheld outright per the small-cell policy``.
(Both sentences have since been rewritten for a reader — see the round-6
answer-surface review — but the arithmetic they state is the invariant.)
The rows settled it: 2026-05 was the only measured cell and 2026-08-01
carried ``value: null``. Two derivations of one count, and the wrong one
was the one in the prose.

**A-05** — the SHAPE finding was the one family that never asked which of
its points are ceilings. The chart drew them as bounds; the title above it
said ``7.5% → 9.0% (up 1.5 points)`` — a measured-looking movement between
two ceilings — and the export, which prints that title verbatim, inherited
the claim.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from revi_investigation.application.execution import bounded_cells_warning, suppression_census
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
    primary_measure,
    published_measures,
    withheld_row_indices,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark
from revi_presentation.charts import build_chart_spec

THRESHOLD = 11
WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _trend() -> EvidenceFrame:
    """The live Veritas shape: eight monthly cells, six bounded, one nulled
    outright, one measured. The numerator survives on the withheld row —
    which is exactly what made the two counters disagree."""
    schema = FrameSchema(
        columns=(
            FrameColumn(name="month", ref=DimensionRef("time_bucket:month")),
            FrameColumn(name="denial_rate", ref=MetricRef("denial_rate"), unit="ratio"),
            FrameColumn(name="denial_rate__num", ref=MetricRef("denial_rate__num"), unit="count"),
            FrameColumn(name="denial_rate__den", ref=MetricRef("denial_rate__den"), unit="count"),
        )
    )
    rows = [
        (date(2026, m, 1), 0.5, 3, 20) for m in range(1, 7)
    ]  # six bounded cells: 0 < num < 11 <= den
    rows.insert(4, (date(2026, 5, 1), 0.25, 40, 160))  # the one measured cell
    rows.append((date(2026, 8, 1), None, None, None))  # withheld outright
    return EvidenceFrame(
        schema=schema,
        rows=tuple(rows),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h"),
        evidence_grade=EvidenceGrade.DIRECT,
        # The frame does NOT admit a suppressed cell: the row is empty at
        # source. The old census required this to be non-zero before it
        # would call anything withheld, which is how the disagreement began.
        suppressed_cells=0,
    )


class TestOneCensus:
    def test_the_published_measure_is_the_measure_a_reader_counts(self) -> None:
        """Anatomy columns are not a second metric: counting a row as
        measured because its numerator survived is the A-02 bug itself."""
        frame = _trend()
        assert published_measures(frame) == ("denial_rate",)
        assert primary_measure(frame) == "denial_rate"

    def test_a_drawn_row_with_no_value_is_withheld_whatever_nulled_it(self) -> None:
        frame = _trend()
        assert withheld_row_indices(frame) == frozenset({len(frame.rows) - 1})

    def test_the_engines_census_and_the_charts_annotation_agree(self) -> None:
        frame = _trend()
        census = suppression_census(frame, THRESHOLD)
        spec = build_chart_spec("chart_main", frame, suppression_threshold=THRESHOLD)

        assert spec is not None
        assert census.total == 8
        assert census.bounded == 6
        assert census.withheld == 1
        assert census.measured == 1
        withheld_annotations = [a for a in spec.annotations if a.startswith("withheld:")]
        assert withheld_annotations == [
            f"withheld: {census.withheld} of {census.total} groups are too small to publish "
            "at all and are drawn with no value"
        ]

    def test_the_census_sentence_states_the_same_arithmetic(self) -> None:
        """The sentence a reader compares against the chart caption."""
        frame = _trend()
        census = suppression_census(frame, THRESHOLD)
        from revi_investigation.application.execution import bounded_cells_of

        warning = bounded_cells_warning(
            bounded_cells_of(frame, THRESHOLD), THRESHOLD, census=census
        )

        assert warning is not None
        # One count, in words, and the withheld row named as a second fact
        # rather than as a second census (round-6 answer-surface review).
        assert "6 of 8 groups here are too small to measure exactly" in warning
        assert "A further 1 could not be published at all." in warning
        assert "cell(s)" not in warning


class TestATrendAdmitsItsCeilings:
    """A-05, over the reference conversation's own shapes."""

    @pytest.mark.reference
    def test_the_helper_that_marks_a_ceiling_is_the_one_the_trend_uses(self) -> None:
        from decimal import Decimal

        from revi_investigation.application.execution import BoundedCell
        from revi_investigation.application.findings import _bound_values, bound_text

        bound = BoundedCell(
            label="2026-07", metric_id="denial_rate", population=13, bound=Decimal("0.769")
        )
        values = dict(_bound_values("denial_rate", bound))

        assert values["denial_rate__is_bound"] is True
        assert values["denial_rate__bound_population"] == 13
        assert bound_text(Decimal("0.769"), "ratio", bounded=True).startswith("≤")
