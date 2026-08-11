"""The generalized angle grammar: what a plan is allowed to name.

The grammar is the reason a model cannot invent an analysis. Every test here
is about the boundary: a shape outside the enumeration does not exist, a
measure this deployment does not carry does not exist, a cut a contract does
not declare is dropped, and everything that survives is legal by
construction rather than by inspection later.

The other half is the claim the module makes about itself — that the v1
recovery families are *instances* of the generalized shapes rather than a
parallel system. That claim is checkable, so it is checked.
"""

from __future__ import annotations

import pytest

from revi_investigation.application.deep_research.general import (
    MAX_ANGLES,
    MAX_CUTS,
    RECOVERY_SHAPES,
    AngleShape,
    AngleVocabulary,
    MeasureAngle,
    PlannedAngle,
    ResearchWalk,
    TimeStep,
    WalkStep,
    dedupe,
    normalize_measure_angle,
    walk_fingerprint,
)
from revi_investigation.application.deep_research.grammar import (
    AngleFamily,
    ResearchAngle,
    Stratum,
    TargetPopulation,
)


def vocabulary() -> AngleVocabulary:
    return AngleVocabulary(
        measures={
            "denial_rate": frozenset({"payer", "facility", "service_line"}),
            "denied_dollars": frozenset({"payer", "facility"}),
            "ar_over_90_pct": frozenset({"payer", "facility"}),
        },
        bases={
            "denial_rate": frozenset({"remit", "service"}),
            "denied_dollars": frozenset({"remit"}),
            "ar_over_90_pct": frozenset({"service"}),
        },
        kinds={
            "denial_rate": "flow",
            "denied_dollars": "flow",
            "ar_over_90_pct": "snapshot",
        },
        units={
            "denial_rate": "ratio",
            "denied_dollars": "money_cents",
            "ar_over_90_pct": "ratio",
        },
        rate_like=frozenset({"denial_rate"}),
    )


class TestTheRecoveryFamiliesAreInstances:
    def test_every_v1_family_declares_a_shape(self) -> None:
        assert set(RECOVERY_SHAPES) == set(AngleFamily)

    def test_the_shapes_it_declares_are_shapes(self) -> None:
        assert set(RECOVERY_SHAPES.values()) <= set(AngleShape)

    def test_a_recovery_angle_rides_the_generalized_plan_unchanged(self) -> None:
        recovery = ResearchAngle(
            family=AngleFamily.PAYER_CONTRAST, within=(Stratum.RECOVERY_CLASS,)
        )
        planned = PlannedAngle(
            shape=RECOVERY_SHAPES[AngleFamily.PAYER_CONTRAST],
            reason="the recovery domain's own contrast",
            recovery=recovery,
        )
        assert planned.recovery is recovery
        assert planned.measure is None
        assert planned.subject == "payer_contrast"


class TestAnAngleIsOneKindOrTheOther:
    def test_naming_neither_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            PlannedAngle(shape=AngleShape.TREND, reason="")

    def test_naming_both_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly one"):
            PlannedAngle(
                shape=AngleShape.TREND,
                reason="",
                recovery=ResearchAngle(family=AngleFamily.PAYER_CONTRAST),
                measure=MeasureAngle(metric_id="denial_rate"),
            )


class TestNormalization:
    def test_a_measure_the_deployment_does_not_carry_does_not_exist(self) -> None:
        angle, reason = normalize_measure_angle(
            MeasureAngle(metric_id="invented_metric"),
            AngleShape.MEASURE_PROFILE,
            vocabulary(),
        )
        assert angle is None
        assert "definitions library" in reason

    def test_a_cut_the_contract_does_not_declare_is_dropped_with_the_reason(self) -> None:
        angle, reason = normalize_measure_angle(
            MeasureAngle(metric_id="denial_rate", cut_by=("payer", "carc")),
            AngleShape.MEASURE_PROFILE,
            vocabulary(),
        )
        assert angle is not None
        assert angle.cut_by == ("payer",)
        assert "carc" in reason

    def test_a_plan_is_not_lost_over_a_stray_cut(self) -> None:
        """Surplus is trimmed, not refused: a plan that loses an angle over
        a stray field answers less than it could have."""
        angle, _ = normalize_measure_angle(
            MeasureAngle(metric_id="denied_dollars", cut_by=("nonsense", "payer")),
            AngleShape.MEASURE_PROFILE,
            vocabulary(),
        )
        assert angle is not None and angle.cut_by == ("payer",)

    def test_cuts_are_capped_and_deduplicated_in_order(self) -> None:
        angle, _ = normalize_measure_angle(
            MeasureAngle(
                metric_id="denial_rate",
                cut_by=("payer", "payer", "facility", "service_line"),
            ),
            AngleShape.MEASURE_PROFILE,
            vocabulary(),
        )
        assert angle is not None
        assert angle.cut_by == ("payer", "facility")
        assert len(angle.cut_by) == MAX_CUTS

    def test_a_trend_over_an_as_of_measure_is_dropped_at_plan_time(self) -> None:
        """A snapshot is a point in time; bucketing one is a category error
        the adapter refuses. Catching it here turns a mid-run failure into a
        plan-time drop with a reason a reader can read."""
        angle, reason = normalize_measure_angle(
            MeasureAngle(metric_id="ar_over_90_pct", step=TimeStep.MONTH),
            AngleShape.TREND,
            vocabulary(),
        )
        assert angle is None
        assert "as-of figure" in reason

    def test_a_trend_defaults_to_months(self) -> None:
        angle, _ = normalize_measure_angle(
            MeasureAngle(metric_id="denial_rate"), AngleShape.TREND, vocabulary()
        )
        assert angle is not None and angle.step is TimeStep.MONTH

    def test_a_step_on_a_non_trend_shape_is_dropped(self) -> None:
        angle, _ = normalize_measure_angle(
            MeasureAngle(metric_id="denial_rate", step=TimeStep.WEEK, cut_by=("payer",)),
            AngleShape.MEASURE_PROFILE,
            vocabulary(),
        )
        assert angle is not None and angle.step is None

    def test_a_date_basis_the_contract_forbids_falls_back_to_its_own(self) -> None:
        angle, _ = normalize_measure_angle(
            MeasureAngle(metric_id="denied_dollars", basis="discharge", cut_by=("payer",)),
            AngleShape.MEASURE_PROFILE,
            vocabulary(),
        )
        assert angle is not None and angle.basis is None

    def test_a_comparison_with_nothing_to_compare_across_is_refused(self) -> None:
        angle, reason = normalize_measure_angle(
            MeasureAngle(metric_id="denial_rate"), AngleShape.CONTRAST, vocabulary()
        )
        assert angle is None
        assert "breakdown to compare" in reason

    def test_a_stratified_rate_over_a_measure_that_is_not_a_rate_is_refused(self) -> None:
        """Over anything but a ratio counting its own population, the same
        arithmetic produces a percentage with no population behind it."""
        angle, reason = normalize_measure_angle(
            MeasureAngle(metric_id="denied_dollars", cut_by=("payer",)),
            AngleShape.STRATIFIED_RATES,
            vocabulary(),
        )
        assert angle is None
        assert "not a rate" in reason

    def test_a_composition_needs_something_to_divide_by(self) -> None:
        angle, reason = normalize_measure_angle(
            MeasureAngle(metric_id="denied_dollars"),
            AngleShape.COMPOSITION,
            vocabulary(),
        )
        assert angle is None
        assert "share of a total" in reason


class TestDedupe:
    def test_the_first_of_each_distinct_angle_survives_in_order(self) -> None:
        first = PlannedAngle(
            shape=AngleShape.MEASURE_PROFILE,
            reason="first",
            measure=MeasureAngle(metric_id="denial_rate", cut_by=("payer",)),
        )
        same = PlannedAngle(
            shape=AngleShape.MEASURE_PROFILE,
            reason="second",
            measure=MeasureAngle(metric_id="denial_rate", cut_by=("payer",)),
        )
        other = PlannedAngle(
            shape=AngleShape.MEASURE_PROFILE,
            reason="third",
            measure=MeasureAngle(metric_id="denied_dollars", cut_by=("payer",)),
        )
        kept = dedupe([first, same, other])
        assert [angle.reason for angle in kept] == ["first", "third"]

    def test_an_angle_held_inside_a_population_is_a_different_angle(self) -> None:
        pooled = PlannedAngle(
            shape=AngleShape.MEASURE_PROFILE,
            reason="pooled",
            measure=MeasureAngle(metric_id="denial_rate", cut_by=("facility",)),
        )
        inside = PlannedAngle(
            shape=AngleShape.MEASURE_PROFILE,
            reason="chased",
            measure=MeasureAngle(
                metric_id="denial_rate",
                cut_by=("facility",),
                within=(("payer", "Atlas Commercial"),),
            ),
        )
        assert len(dedupe([pooled, inside])) == 2

    def test_two_angles_differing_only_in_how_many_cells_they_show_are_one(
        self,
    ) -> None:
        """``top_n`` is how much of a reading is printed, not which reading
        it is. Two angles differing only there are one read, and running it
        twice would spend a round on a page-length decision."""
        wide = PlannedAngle(
            shape=AngleShape.MEASURE_PROFILE,
            reason="wide",
            measure=MeasureAngle(metric_id="denial_rate", cut_by=("payer",), top_n=40),
        )
        narrow = PlannedAngle(
            shape=AngleShape.MEASURE_PROFILE,
            reason="narrow",
            measure=MeasureAngle(metric_id="denial_rate", cut_by=("payer",), top_n=5),
        )
        assert len(dedupe([wide, narrow])) == 1

    def test_the_cap_binds(self) -> None:
        angles = [
            PlannedAngle(
                shape=AngleShape.MEASURE_PROFILE,
                reason=str(index),
                measure=MeasureAngle(
                    metric_id="denial_rate", within=(("payer", f"P{index}"),)
                ),
            )
            for index in range(1, MAX_ANGLES + 5)
        ]
        assert len(dedupe(angles)) == MAX_ANGLES


class TestTheWalk:
    def _walk(self, *, reason: str = "because") -> ResearchWalk:
        return ResearchWalk(
            question="why is over-90 climbing",
            population=TargetPopulation(),
            angles=(
                PlannedAngle(
                    shape=AngleShape.MEASURE_PROFILE,
                    reason=reason,
                    measure=MeasureAngle(metric_id="ar_over_90_pct", cut_by=("payer",)),
                ),
            ),
            steps=(WalkStep(round=0, action="plan", subject="ar_over_90_pct", reason=reason),),
        )

    def test_the_fingerprint_addresses_what_was_executed(self) -> None:
        assert len(walk_fingerprint(self._walk())) == 64

    def test_the_fingerprint_ignores_what_the_model_said(self) -> None:
        """Reasons are what the model SAID. A report whose numbers changed
        because a sentence was worded differently would have a
        reproducibility claim it could not keep."""
        assert walk_fingerprint(self._walk(reason="one wording")) == walk_fingerprint(
            self._walk(reason="a completely different wording")
        )

    def test_a_different_population_is_a_different_walk(self) -> None:
        from revi_investigation.application.deep_research.grammar import PopulationKind

        narrowed = ResearchWalk(
            question="why is over-90 climbing",
            population=TargetPopulation(
                kind=PopulationKind.PAYER, values=("Atlas Commercial",)
            ),
            angles=self._walk().angles,
        )
        assert walk_fingerprint(narrowed) != walk_fingerprint(self._walk())

    def test_rounds_are_readable_in_order(self) -> None:
        walk = ResearchWalk(
            question="q",
            population=TargetPopulation(),
            angles=(
                PlannedAngle(
                    shape=AngleShape.TREND,
                    reason="opening",
                    round=0,
                    measure=MeasureAngle(metric_id="denial_rate", step=TimeStep.MONTH),
                ),
                PlannedAngle(
                    shape=AngleShape.MEASURE_PROFILE,
                    reason="chased",
                    round=1,
                    measure=MeasureAngle(metric_id="denial_rate", cut_by=("payer",)),
                ),
            ),
        )
        assert [index for index, _ in walk.by_round] == [0, 1]
