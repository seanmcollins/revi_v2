"""Comparison integrity and display-scope recovery.

Four invariants, pinned as unit facts so a regression cannot hide behind a
live session: a whole calendar period against the whole one before it is a
SAME-KIND comparison and is exempt from the length-mismatch machinery; a
comparison cell missing from a TRUNCATED side is UNKNOWN rather than zero,
and the turn says how many; two windows settled to different degrees are not
comparable at high confidence, and the warning names both panels; and a
request that changes only how many rows are shown is decidable without a
model.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from revi_investigation.application.calculation_glue import CalculateMetricsService
from revi_investigation.application.comparison import (
    COMPARISON_MIN_PANEL_SHARE,
    ComparisonRendering,
    comparison_maturity,
    same_calendar_kind,
)
from revi_investigation.application.execution import ExecutedProbe
from revi_investigation.application.interpretation import display_scope_limit
from revi_investigation.application.planning import (
    InvestigationPlan,
    ProbeNode,
    TransformPlan,
    TransformPlanStep,
)
from revi_kernel.filters import EMPTY_SCOPE
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameSchema, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.probes import AggregationProbe
from revi_kernel.refs import REMIT, DimensionRef, EntityGrain, Grain, MetricRef
from revi_kernel.scope import AbsoluteRange, TimeWindow
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import CalculationTransforms, PackSnapshotPort

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


class TestCalendarPeriodsAreLikeForLike:
    """Every month-over-month turn came back with impact withheld and
    every finding title stamped "(30d vs 31d, not length-normalized)". A
    close is read month against month; as shipped, no month-end comparison
    could publish an impact figure at all."""

    def test_july_against_june_is_the_same_kind_of_period(self) -> None:
        july = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))
        june = AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))
        assert same_calendar_kind(july, june) == 1

    def test_a_quarter_against_a_quarter_is_too(self) -> None:
        q2 = AbsoluteRange(date(2026, 4, 1), date(2026, 6, 30))
        q1 = AbsoluteRange(date(2026, 1, 1), date(2026, 3, 31))
        assert same_calendar_kind(q2, q1) == 3

    def test_one_month_against_two_is_not(self) -> None:
        one = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))
        two = AbsoluteRange(date(2026, 5, 1), date(2026, 6, 30))
        assert same_calendar_kind(one, two) is None

    def test_an_arbitrary_range_is_not(self) -> None:
        a = AbsoluteRange(date(2026, 7, 5), date(2026, 8, 2))
        b = AbsoluteRange(date(2026, 6, 6), date(2026, 7, 4))
        assert same_calendar_kind(a, b) is None

    def test_a_same_kind_comparison_withholds_nothing(self) -> None:
        rendering = ComparisonRendering(
            phrase="vs prior month",
            range_text="2026-06-01..2026-06-30",
            current_days=31,
            comparison_days=30,
            calendar_span=1,
        )
        # the day counts really do differ and that is still disclosed…
        assert rendering.length_mismatch is True
        # …but 30-vs-31 is the calendar, not a distortion: no impact is
        # withheld and no finding is qualified for it.
        assert rendering.material_length_mismatch is False

    def test_seven_days_against_a_quarter_is_still_material(self) -> None:
        rendering = ComparisonRendering(
            phrase="x", range_text="y", current_days=7, comparison_days=90
        )
        assert rendering.material_length_mismatch is True


def _compare_frame(*, truncated: bool) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn("carc", DimensionRef("carc")),
                FrameColumn("denied_dollars", MetricRef("denied_dollars"), 2, "money_cents"),
            )
        ),
        rows=(("16", 4_191_823), ("50", 1_000_000)),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
        truncated=truncated,
    )


def _prior_frame(*, truncated: bool) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn("carc", DimensionRef("carc")),
                FrameColumn("denied_dollars", MetricRef("denied_dollars"), 2, "money_cents"),
            )
        ),
        # "16" is absent: outside the prior side's top-N, not absent from
        # the world. "50" is present with a real prior.
        rows=(("50", 900_000),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p2", probe_hash="i" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
        truncated=truncated,
    )


def _compare_plan() -> InvestigationPlan:
    probe = AggregationProbe(
        measures=(MetricRef("denied_dollars"),),
        dimensions=(DimensionRef("carc"),),
        scope=EMPTY_SCOPE,
        window=TimeWindow(basis=REMIT, range=AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))),
        grain=Grain(EntityGrain.CLAIM),
    )
    return InvestigationPlan(
        nodes=(
            ProbeNode(id="main", probe=probe, purpose="t"),
            ProbeNode(id="main__prior", probe=probe, purpose="b"),
        ),
        transforms=TransformPlan(
            steps=(
                TransformPlanStep(
                    id="main__compare", operator="compare", inputs=("main", "main__prior")
                ),
            )
        ),
    )


def _calculate(
    pack_port: PackSnapshotPort, *, truncated: bool
) -> tuple[EvidenceFrame, tuple[str, ...]]:
    service = CalculateMetricsService(CalculationTransforms(), pack_port)
    plan = _compare_plan()
    executed = (
        ExecutedProbe(node_id="main", frame=_compare_frame(truncated=truncated), cache_hit=False),
        ExecutedProbe(
            node_id="main__prior", frame=_prior_frame(truncated=truncated), cache_hit=False
        ),
    )
    result = service.calculate(plan, executed)
    return result.frame("main__compare"), result.warnings


class TestATruncatedPriorIsUnknownNotZero:
    """A defect that survived three fix waves:
    "CO / 16 — denied dollars moved from $0.00 to $41,918.23" at direct/high
    with an impact figure, over a warehouse in which CO/16 had FALLEN
    $15,780. The prior was never retrieved; it was not zero."""

    def test_a_complete_prior_still_fills_a_real_absence_with_zero(
        self, pack_port: PackSnapshotPort
    ) -> None:
        frame, warnings = _calculate(pack_port, truncated=False)
        row = next(r for r in frame.rows if r[0] == "16")
        prior = row[frame.schema.index_of("denied_dollars__prior")]
        # read whole, "16" genuinely had none last period
        assert prior == 0
        assert not warnings

    def test_a_truncated_prior_publishes_no_figure_and_no_movement(
        self, pack_port: PackSnapshotPort
    ) -> None:
        frame, warnings = _calculate(pack_port, truncated=True)
        row = next(r for r in frame.rows if r[0] == "16")
        for column in ("denied_dollars__prior", "denied_dollars__delta",
                       "denied_dollars__pct_change"):
            assert row[frame.schema.index_of(column)] is None, column
        # the cell that WAS on both sides keeps its real movement
        kept = next(r for r in frame.rows if r[0] == "50")
        assert kept[frame.schema.index_of("denied_dollars__prior")] == 900_000
        assert kept[frame.schema.index_of("denied_dollars__delta")] == 100_000
        assert len(warnings) == 1
        assert warnings[0].startswith("comparison_prior_unknown:")
        assert "UNKNOWN, not zero" in warnings[0]


def _panel_compare_frame(current_panel: int, prior_panel: int) -> EvidenceFrame:
    return EvidenceFrame(
        schema=FrameSchema(
            (
                FrameColumn("payer", DimensionRef("payer")),
                FrameColumn("denial_rate", MetricRef("denial_rate"), 2, "ratio"),
                FrameColumn("denial_rate__den", MetricRef("denial_rate"), 2, "count"),
                FrameColumn("denial_rate__den__prior", MetricRef("denial_rate"), 2, "count"),
            )
        ),
        rows=(("Atlas", Decimal("0.128"), current_panel, prior_panel),),
        watermark=WATERMARK,
        provenance=ProbeProvenance(probe_id="p", probe_hash="h" * 64),
        evidence_grade=EvidenceGrade.DIRECT,
    )


class TestComparisonsAreTestedForDataMaturity:
    """``terminal_bucket_censoring`` needs a three-point trend and has
    one call site inside the trend loop, so a July at 23% adjudicated was
    published against a June at 91% as "+73%" at direct/high."""

    def test_a_lopsided_panel_names_both_sides(self) -> None:
        frames = (("main__compare", _panel_compare_frame(1_544, 6_453)),)
        verdicts = comparison_maturity(frames)
        assert len(verdicts) == 1
        verdict = verdicts[0]
        assert verdict.current_panel == 1_544
        assert verdict.prior_panel == 6_453
        assert "1,544" in verdict.warning and "6,453" in verdict.warning
        assert verdict.warning.startswith("adjudication_incomplete:")
        assert verdict.share < COMPARISON_MIN_PANEL_SHARE

    def test_two_equally_settled_windows_say_nothing(self) -> None:
        frames = (("main__compare", _panel_compare_frame(6_133, 6_049)),)
        assert comparison_maturity(frames) == ()

    def test_a_frame_with_no_panel_yields_no_verdict(self) -> None:
        """An additive money measure has no adjudicated denominator to read,
        and inventing one would be worse than saying nothing."""
        frames = (("main__compare", _compare_frame(truncated=False)),)
        assert comparison_maturity(frames) == ()


class TestDisplayScopeIsDecidedWithoutAModel:
    """No reviewer got this through: "show me all twelve" went to the
    classifier, came back at 0.45-0.50 confidence, and ended in a
    clarification asking whether the twelve had already been computed."""

    def test_a_pure_count_request_is_recognised(self) -> None:
        assert display_scope_limit("show me all twelve, not just three") is not None
        assert display_scope_limit("show all of them") is not None
        assert display_scope_limit("give me the full list") == 100
        assert display_scope_limit("show me all 12") == 12

    def test_a_question_that_names_content_is_not_a_display_request(self) -> None:
        assert display_scope_limit("show me the top 5 payers by denial rate") is None
        assert display_scope_limit("which payers are driving denied dollars") is None
        assert display_scope_limit("show me the top 3 facilities") is None

    def test_a_sentence_with_no_count_is_not_one_either(self) -> None:
        assert display_scope_limit("show me more") is None
