"""Card and drill can no longer disagree in silence (review F1).

ANM-021 published $178,216.82 and its own drill answered $195,873.92 — a
9.9% gap on consecutive screens, with the only reconciliation on the
answer reading "not_applicable, first turn". These tests pin the two
halves of the fix: summing the platform's own figure off the drill's
frame, and describing the relationship between the two figures in words
that name both.
"""

from __future__ import annotations

from datetime import date, datetime

from revi_api.rederive import ReDerivedImpact, compare_impact, money_total
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark

WATERMARK = DataWatermark(
    id="wm_003", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)
WINDOW = (date(2026, 7, 3), date(2026, 7, 29))


def _frame(rows: tuple[tuple[object, ...], ...], *, unit: str = "money_cents") -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn(name="facility", ref=DimensionRef("facility")),
                FrameColumn(
                    name="dnfb_dollars",
                    ref=MetricRef("dnfb_dollars"),
                    contract_version=1,
                    unit=unit,
                ),
            )
        ),
        rows=rows,  # type: ignore[arg-type]
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="main_1", probe_hash="h"),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestMoneyTotal:
    def test_the_money_column_is_summed_and_named(self) -> None:
        frames = (("main_1", _frame((("Northgate Regional Hospital", 19_587_392),))),)
        assert money_total(frames) == (19_587_392, "dnfb_dollars", 1)

    def test_multiple_rows_sum(self) -> None:
        frames = (("main_1", _frame((("A", 100), ("B", 250), ("C", None)))),)
        cents, measure, rows = money_total(frames)
        assert (cents, measure, rows) == (350, "dnfb_dollars", 3)

    def test_the_last_money_frame_wins(self) -> None:
        """Frames are in creation order, so the final money-bearing frame
        is the one after every transform — the frame the findings read."""
        frames = (
            ("main_1", _frame(((("A"), 100),))),
            ("main_1__rank", _frame(((("A"), 999),))),
        )
        assert money_total(frames)[0] == 999

    def test_a_frame_with_no_money_column_yields_no_figure(self) -> None:
        """A ratio drill has nothing to compare against a dollar impact,
        and says so instead of coercing one."""
        frames = (("main_1", _frame(((("A"), 0.42),), unit="ratio")),)
        cents, measure, _ = money_total(frames)
        assert cents is None and measure is None


class TestComparison:
    def _diverged(self) -> object:
        return compare_impact(
            detector_cents=17_821_682,
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            rederived=ReDerivedImpact(cents=19_587_392, measure_id="dnfb_dollars", rows=1),
            unattempted_note="",
        )

    def test_the_reported_defect_is_reported(self) -> None:
        comparison = self._diverged()
        assert comparison.status == "diverged"
        assert comparison.delta_cents == 1_765_710
        assert round(comparison.delta_fraction, 3) == 0.099

    def test_both_figures_are_named_in_the_sentence(self) -> None:
        """A strip that says "these disagree" without saying by how much,
        from what, is the same silence in a different font."""
        note = self._diverged().note
        assert "$178,216.82" in note and "$195,873.92" in note
        assert "$17,657.10" in note  # the delta, in dollars
        assert "+9.9%" in note
        assert "detection system" in note and "governed" in note
        assert "dnfb_dollars" in note

    def test_a_small_difference_is_agreement_with_the_delta_still_published(self) -> None:
        comparison = compare_impact(
            detector_cents=1_000_000,
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            rederived=ReDerivedImpact(cents=1_004_000, measure_id="cash_posted", rows=1),
            unattempted_note="",
        )
        assert comparison.status == "agreed"
        assert comparison.delta_cents == 4_000  # the number is never hidden

    def test_no_re_derivation_is_unavailable_never_agreed(self) -> None:
        comparison = compare_impact(
            detector_cents=1_000_000,
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            rederived=None,
            unattempted_note="not attempted in this build",
        )
        assert comparison.status == "unavailable"
        assert comparison.platform_cents is None
        assert comparison.note == "not attempted in this build"

    def test_a_refused_drill_reports_the_platforms_own_refusal(self) -> None:
        comparison = compare_impact(
            detector_cents=1_000_000,
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            rederived=ReDerivedImpact(
                unavailable_reason="UNSUPPORTED_CONCEPT: nothing here measures that"
            ),
            unattempted_note="",
        )
        assert comparison.status == "unavailable"
        assert "UNSUPPORTED_CONCEPT" in comparison.note

    def test_a_zero_impact_card_does_not_divide_by_zero(self) -> None:
        comparison = compare_impact(
            detector_cents=0,
            window_start=WINDOW[0],
            window_end=WINDOW[1],
            rederived=ReDerivedImpact(cents=500, measure_id="cash_posted", rows=1),
            unattempted_note="",
        )
        assert comparison.status == "diverged" and comparison.delta_cents == 500
