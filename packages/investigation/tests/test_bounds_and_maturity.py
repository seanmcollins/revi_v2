"""Round-3 regressions: a bound that survives delivery, a ranking that
refuses to order ceilings, a series that will not close on an unsettled
bucket, and a window that states what was actually read.

Every test here encodes a live defect the round-3 review recomputed
against the warehouse. The engine already knew each of these facts; the
artifacts lost them between the frame and the reader.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from revi_investigation.application.execution import (
    bound_index,
    bounded_cells_of,
    bounded_cells_warning,
    suppression_census,
)
from revi_investigation.application.findings import (
    MAX_BOUNDED_SHARE_FOR_RANKING,
    PREMISE_MAGNITUDE_BAND,
    TrendShape,
    bound_text,
    terminal_bucket_censoring,
)
from revi_investigation.application.interpretation import (
    Coverage,
    asserted_multiple,
    recognize_relative_period,
    requested_finding_limit,
    window_coverage,
)
from revi_kernel.frame import (
    EvidenceFrame,
    FrameColumn,
    FrameSchema,
    ProbeProvenance,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.scope import AbsoluteRange
from revi_kernel.watermark import DataWatermark

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
THRESHOLD = 11


def _rate_frame(rows: tuple[tuple[object, ...], ...]) -> EvidenceFrame:
    """payer / denial_rate__num / denial_rate__den — the shape §15 polices."""
    return EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn("payer", DimensionRef("payer")),
                FrameColumn("denial_rate__num", MetricRef("denial_rate"), 1, "count"),
                FrameColumn("denial_rate__den", MetricRef("denial_rate"), 1, "count"),
            )
        ),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
        suppressed_cells=2,
    )


class TestBoundsSurviveDelivery:
    """R3-01. The ceiling existed as structured data and reached the wire
    as a measured point value: ``Dr. Casey Quarry (143): 45.5% denial
    rate`` over 10/22, ``metric_caveats: []``, grade direct, confidence
    high."""

    def test_a_bound_is_addressable_by_row(self) -> None:
        frame = _rate_frame(
            (
                ("Federal Medicare", 10, 214),  # bounded: 10 over a big panel
                ("Atlas Commercial", 40, 300),  # measured
            )
        )

        index = bound_index(frame, THRESHOLD)

        assert set(index) == {0}
        bound = index[0]["denial_rate"]
        assert bound.population == 214
        assert bound.bound == Decimal(10) / Decimal(214)
        assert bound.row_index == 0

    def test_the_bound_renders_with_its_glyph(self) -> None:
        assert bound_text(Decimal("0.454545"), "ratio", bounded=True).startswith("≤ ")
        assert not bound_text(Decimal("0.454545"), "ratio", bounded=False).startswith("≤")

    def test_the_census_counts_cells_not_nulled_values(self) -> None:
        """R3-18. ``suppressed_cells`` counts VALUES, several per row, and
        quoting it as a population is how "3 of 15 cells" was published
        over 12 cells of which none were withheld."""
        frame = _rate_frame((("A", 10, 214), ("B", 40, 300), ("C", 3, 260)))

        census = suppression_census(frame, THRESHOLD)

        assert census.total == 3
        assert census.bounded == 2
        assert census.withheld == 0
        assert census.measured == 1

    def test_the_warning_no_longer_claims_everything_else_is_measured(self) -> None:
        """R3-02. That sentence shipped on a frame where 147 of 150 values
        were bounds and the other three were zeros."""
        frame = _rate_frame((("A", 10, 214), ("B", 40, 300)))
        cells = bounded_cells_of(frame, THRESHOLD)

        text = bounded_cells_warning(
            cells, THRESHOLD, census=suppression_census(frame, THRESHOLD)
        )

        assert text is not None
        assert "every other figure here is measured" not in text
        assert "is NOT a measurement" in text
        assert "Of 2 cell(s) on this answer, 1 carry an upper bound" in text


class TestRankingRefusesCeilings:
    """R3-02. Of 150 published values, 147 were exactly ``10/n`` and the
    sort key was therefore ascending panel size, narrated "ranks #1 by
    denial rate (worst first, as asked)"."""

    def test_the_governed_share_is_a_majority(self) -> None:
        assert MAX_BOUNDED_SHARE_FOR_RANKING == 0.5


class TestPremiseMagnitude:
    """R3-03. ``holds`` tested the sign alone, so "why did denials
    double?" over +4.2% scored true and published nothing."""

    def test_a_doubling_is_read_off_the_utterance(self) -> None:
        assert asserted_multiple("why did our denials double in July", True) == 2
        assert asserted_multiple("why did denials triple", True) == 3
        assert asserted_multiple("why did collections fall by half", True) == Decimal("0.5")

    def test_a_magnitude_is_only_a_premise_when_one_is_asserted(self) -> None:
        assert asserted_multiple("which payer doubled?", False) is None

    def test_the_band_is_two_sided(self) -> None:
        """R4-05c. The predecessor was a one-sided FLOOR at half the
        asserted change, so +72.6% confirmed a doubling — and so would
        +900%. A quarter-band around the claim means +75%..+125% is a
        doubling and nothing else is."""
        assert Decimal("0.25") == PREMISE_MAGNITUDE_BAND


class TestTerminalBucketMaturity:
    """R3-06. ``7.3% → 12.8% (up 5.5 points)``, grade direct, confidence
    high, benchmarked — over a July point computed on 22.9% of July's
    claims."""

    def _series(self, rows: tuple[tuple[object, ...], ...]) -> TrendShape:
        frame = EvidenceFrame(
            schema=FrameSchema(
                (
                    FrameColumn("month", DimensionRef("time_bucket:month")),
                    FrameColumn("denial_rate", MetricRef("denial_rate"), 1, "ratio"),
                    FrameColumn("denial_rate__den", MetricRef("denial_rate"), 1, "count"),
                )
            ),
            rows=rows,  # type: ignore[arg-type]
            watermark=WATERMARK,
            provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
            evidence_grade=EvidenceGrade.DIRECT,
        )
        return TrendShape(
            frame_id="main", frame=frame, bucket_column="month", measure="denial_rate", unit="ratio"
        )

    def test_a_right_censored_terminal_bucket_is_named(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(measures=("denial_rate",), watermark=WATERMARK)
        shape = self._series(
            (
                ("2026-01-01", Decimal("0.055"), 6049),
                ("2026-05-01", Decimal("0.054"), 6133),
                ("2026-06-01", Decimal("0.103"), 5723),
                ("2026-07-01", Decimal("0.128"), 1544),  # 25% of the median panel
            )
        )

        censoring = terminal_bucket_censoring(shape, spec)

        assert censoring is not None
        assert censoring.bucket == "2026-07"
        assert censoring.population == 1544
        assert "adjudication_incomplete:" in censoring.warning
        assert "provisional" in censoring.warning

    def test_a_settled_series_is_left_alone(self, make_spec) -> None:  # type: ignore[no-untyped-def]
        spec = make_spec(measures=("denial_rate",), watermark=WATERMARK)
        shape = self._series(
            (
                ("2026-01-01", Decimal("0.055"), 6049),
                ("2026-02-01", Decimal("0.054"), 6133),
                ("2026-03-01", Decimal("0.103"), 5723),
            )
        )

        assert terminal_bucket_censoring(shape, spec) is None


class TestWindowVocabulary:
    """R3-05 and R3-16."""

    def test_a_partly_covered_window_is_partial(self) -> None:
        assert (
            window_coverage(AbsoluteRange(date(2026, 7, 1), date(2026, 9, 30)), WATERMARK)
            is Coverage.PARTIAL
        )

    @pytest.mark.parametrize(
        ("utterance", "quoted"),
        [
            ("what should my team work first this week", "this week"),
            ("denials in the last 90 days", "last 90 days"),
            ("MTD denial rate", "MTD"),
        ],
    )
    def test_relative_vocabulary_is_recognized_and_quoted(
        self, utterance: str, quoted: str
    ) -> None:
        found = recognize_relative_period(utterance)
        assert found is not None and found.quoted == quoted
        assert found.relative is not None

    def test_a_forward_horizon_is_not_a_measurement_window(self) -> None:
        found = recognize_relative_period("what is at risk in the next 30 days")
        assert found is not None and found.forward and found.relative is None

    def test_a_named_count_lifts_the_finding_limit(self) -> None:
        """R3-04. "show me all twelve payers, not just three" parsed
        perfectly and returned the identical three findings."""
        assert requested_finding_limit("show me all twelve payers, not just three") is not None
        assert requested_finding_limit("every one of our 12 payers") == 12
        assert requested_finding_limit("rank the top 5 payers") == 5
        assert requested_finding_limit("which payer is worst?") is None
