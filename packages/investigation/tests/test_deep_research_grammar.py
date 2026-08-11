"""The plan grammar: what the control plane may ask for, and what happens
to everything else.

The whole safety argument for letting a model choose the analysis rests on
this module refusing anything outside the catalogue. So these tests are
adversarial about it: an invented family, an invented population, a family
given parameters it does not take, a plan with the headline missing, a plan
long enough to be a data dump.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from revi_investigation.application.deep_research.grammar import (
    MAX_ANGLES,
    MAX_STRATIFIERS,
    STANDING_ANGLES,
    AngleFamily,
    PopulationKind,
    RateBasisChoice,
    ResearchAngle,
    Stratum,
    TargetPopulation,
    build_angle,
    plan_fingerprint,
    standing_plan,
    validate_plan,
)
from revi_investigation.application.deep_research.policy import (
    BandSpec,
    DeepResearchSettings,
)


def _settings(**overrides: object) -> DeepResearchSettings:
    base: dict[str, object] = {
        "min_cohort": 30,
        "min_cohort_label": "at least 30 of these denials have a final answer from the payer",
        "min_cohort_recommender": "Revi's recommended level for recovery rates",
        "confidence": Decimal("0.95"),
        "delay_bands": (BandSpec("0-14", 0, 15), BandSpec("15+", 15)),
        "dollar_bands": (BandSpec("under $500", 0, 50_000), BandSpec("over $500", 50_000)),
        "age_bands": (BandSpec("0-30 days", 0, 31), BandSpec("over 30 days", 31)),
    }
    base.update(overrides)
    return DeepResearchSettings(**base)  # type: ignore[arg-type]


class TestTheCatalogueIsClosed:
    def test_an_invented_family_does_not_become_a_weaker_analysis(self) -> None:
        assert build_angle("mine_the_data") is None
        assert build_angle("expected_recovery_but_optimistic") is None

    def test_an_invented_population_is_dropped_not_honoured(self) -> None:
        angle = build_angle(
            "outcome_by_stratum", stratify_by=["payer", "astrological_sign"]
        )
        assert angle is not None
        assert angle.stratify_by == (Stratum.PAYER,)

    def test_an_angle_left_with_nothing_legal_is_dropped_entirely(self) -> None:
        assert build_angle("outcome_by_stratum", stratify_by=["nonsense"]) is None

    def test_an_unknown_denominator_falls_back_to_the_answered_one(self) -> None:
        angle = build_angle("outcome_by_stratum", stratify_by=["payer"], basis="optimistic")
        assert angle is not None
        assert angle.basis is RateBasisChoice.DECIDED


class TestEachFamilyKeepsItsOwnShape:
    def test_pricing_is_always_over_the_answered_denial_rate(self) -> None:
        angle = build_angle("expected_recovery", stratify_by=["payer"], basis="pursuit")
        assert angle is not None
        assert angle.basis is RateBasisChoice.DECIDED

    def test_pricing_with_no_cut_named_gets_the_standing_one(self) -> None:
        angle = build_angle("expected_recovery")
        assert angle is not None
        assert angle.stratify_by == (Stratum.PAYER, Stratum.RECOVERY_CLASS)

    def test_a_cut_wider_than_two_populations_is_trimmed(self) -> None:
        angle = build_angle(
            "outcome_by_stratum", stratify_by=["payer", "recovery_class", "plan"]
        )
        assert angle is not None
        assert len(angle.stratify_by) == MAX_STRATIFIERS

    def test_a_payer_contrast_may_only_be_held_inside_a_denial_type(self) -> None:
        angle = build_angle("payer_contrast", within=["plan", "recovery_class"])
        assert angle is not None
        assert angle.within == (Stratum.RECOVERY_CLASS,)
        assert angle.stratify_by == ()

    def test_a_denial_type_contrast_may_only_be_held_inside_a_payer(self) -> None:
        angle = build_angle("class_contrast", within=["recovery_class", "payer"])
        assert angle is not None
        assert angle.within == (Stratum.PAYER,)

    def test_the_deadline_angle_takes_no_parameters_at_all(self) -> None:
        angle = build_angle(
            "deadline_interaction", stratify_by=["payer"], within=["plan"], basis="pursuit"
        )
        assert angle is not None
        assert angle.stratify_by == (Stratum.FILING_POSITION, Stratum.FILING_RULE)
        assert angle.within == ()
        assert angle.basis is RateBasisChoice.DECIDED


class TestDisposal:
    def test_the_headline_is_added_when_a_plan_leaves_it_out(self) -> None:
        plan = validate_plan(
            research_question="what should we work first?",
            angles=[ResearchAngle(family=AngleFamily.TIMELINESS_CURVE)],
        )
        assert plan.angles[0].family is AngleFamily.EXPECTED_RECOVERY
        assert plan.added_by_revi == (AngleFamily.EXPECTED_RECOVERY,)

    def test_an_added_angle_is_recorded_rather_than_presented_as_a_choice(self) -> None:
        plan = validate_plan(
            research_question="",
            angles=[ResearchAngle(family=AngleFamily.EXPECTED_RECOVERY)],
            authored_by="model",
        )
        assert plan.added_by_revi == ()
        assert plan.authored_by == "model"

    def test_the_headline_leads_however_it_was_ordered(self) -> None:
        plan = validate_plan(
            research_question="q",
            angles=[
                ResearchAngle(family=AngleFamily.DEADLINE_INTERACTION),
                ResearchAngle(
                    family=AngleFamily.EXPECTED_RECOVERY,
                    stratify_by=(Stratum.PAYER,),
                ),
            ],
        )
        assert plan.angles[0].family is AngleFamily.EXPECTED_RECOVERY

    def test_a_population_is_priced_once_at_the_finest_cut_asked_for(self) -> None:
        """Two totals for one population would be two answers to one question."""
        plan = validate_plan(
            research_question="q",
            angles=[
                ResearchAngle(
                    family=AngleFamily.EXPECTED_RECOVERY, stratify_by=(Stratum.PAYER,)
                ),
                ResearchAngle(
                    family=AngleFamily.EXPECTED_RECOVERY,
                    stratify_by=(Stratum.PAYER, Stratum.RECOVERY_CLASS),
                ),
                ResearchAngle(
                    family=AngleFamily.EXPECTED_RECOVERY,
                    stratify_by=(Stratum.RECOVERY_CLASS,),
                ),
            ],
        )
        pricing = [a for a in plan.angles if a.family is AngleFamily.EXPECTED_RECOVERY]
        assert len(pricing) == 1
        assert pricing[0].stratify_by == (Stratum.PAYER, Stratum.RECOVERY_CLASS)

    def test_duplicate_angles_collapse(self) -> None:
        angle = ResearchAngle(
            family=AngleFamily.OUTCOME_BY_STRATUM, stratify_by=(Stratum.PAYER,)
        )
        plan = validate_plan(research_question="q", angles=[angle, angle, angle])
        families = [a for a in plan.angles if a.family is AngleFamily.OUTCOME_BY_STRATUM]
        assert len(families) == 1

    def test_a_plan_is_capped_and_still_carries_the_headline(self) -> None:
        many = [
            ResearchAngle(family=AngleFamily.OUTCOME_BY_STRATUM, stratify_by=(stratum,))
            for stratum in Stratum
        ] * 3
        plan = validate_plan(research_question="q", angles=many)
        assert len(plan.angles) == MAX_ANGLES
        assert plan.angles[0].family is AngleFamily.EXPECTED_RECOVERY

    def test_an_empty_question_becomes_the_standing_one(self) -> None:
        plan = validate_plan(research_question="   ", angles=list(STANDING_ANGLES))
        assert plan.research_question.startswith("Of the denials still open")


class TestTheStandingPlan:
    def test_it_answers_the_question_on_its_own(self) -> None:
        plan = standing_plan(None)
        families = {angle.family for angle in plan.angles}
        assert families == set(AngleFamily)
        assert plan.authored_by == "revi"

    def test_it_keeps_the_analysts_own_question_when_there_is_one(self) -> None:
        plan = standing_plan("What can we get back from Lakewood?")
        assert plan.research_question == "What can we get back from Lakewood?"


class TestTheTargetPopulation:
    def test_every_open_denial_takes_no_values(self) -> None:
        with pytest.raises(ValueError, match="no values"):
            TargetPopulation(kind=PopulationKind.ALL_OPEN, values=("Anything",))

    def test_a_narrowed_population_needs_at_least_one(self) -> None:
        with pytest.raises(ValueError, match="at least one value"):
            TargetPopulation(kind=PopulationKind.PAYER)

    def test_each_selector_narrows_on_the_column_it_names(self) -> None:
        assert TargetPopulation().dimension is None
        assert (
            TargetPopulation(kind=PopulationKind.PAYER, values=("A",)).dimension == "payer"
        )
        assert (
            TargetPopulation(kind=PopulationKind.FACILITY, values=("A",)).dimension
            == "facility"
        )


class TestTheFingerprint:
    def test_the_same_plan_over_the_same_population_addresses_the_same(self) -> None:
        plan = standing_plan("q")
        assert plan_fingerprint(plan, TargetPopulation()) == plan_fingerprint(
            plan, TargetPopulation()
        )

    def test_a_different_population_addresses_differently(self) -> None:
        plan = standing_plan("q")
        narrowed = TargetPopulation(kind=PopulationKind.PAYER, values=("Atlas Commercial",))
        assert plan_fingerprint(plan, TargetPopulation()) != plan_fingerprint(plan, narrowed)

    def test_a_different_cut_addresses_differently(self) -> None:
        wide = validate_plan(
            research_question="q",
            angles=[
                ResearchAngle(
                    family=AngleFamily.EXPECTED_RECOVERY,
                    stratify_by=(Stratum.PAYER, Stratum.RECOVERY_CLASS),
                )
            ],
        )
        narrow = validate_plan(
            research_question="q",
            angles=[
                ResearchAngle(
                    family=AngleFamily.EXPECTED_RECOVERY, stratify_by=(Stratum.PLAN,)
                )
            ],
        )
        assert plan_fingerprint(wide, TargetPopulation()) != plan_fingerprint(
            narrow, TargetPopulation()
        )

    def test_a_pricing_cut_always_carries_the_kind_of_denial(self) -> None:
        """Payer-only pricing charges the winning classes' rate to the losing
        classes' dollars, so the cut is completed rather than honoured as asked."""
        plan = validate_plan(
            research_question="q",
            angles=[
                ResearchAngle(
                    family=AngleFamily.EXPECTED_RECOVERY, stratify_by=(Stratum.PAYER,)
                )
            ],
        )
        pricing = next(
            angle
            for angle in plan.angles
            if angle.family is AngleFamily.EXPECTED_RECOVERY
        )
        assert pricing.stratify_by == (Stratum.PAYER, Stratum.RECOVERY_CLASS)


class TestTheContentDecidesWhatCanBeCut:
    def test_a_banded_population_with_no_edges_cannot_be_cut_by(self) -> None:
        bare = _settings(delay_bands=(), dollar_bands=(), age_bands=())
        supported = set(bare.supported_stratifiers())
        assert Stratum.DELAY_BAND not in supported
        assert Stratum.DOLLAR_BAND not in supported
        assert Stratum.AGE_BAND not in supported
        assert Stratum.PAYER in supported

    def test_the_naming_floor_may_never_exceed_the_rate_floor(self) -> None:
        with pytest.raises(ValueError, match="naming floor"):
            _settings(min_cohort=5, disclosure_floor=11)

    def test_the_floor_is_stated_as_a_rule_with_its_number_and_its_owner(self) -> None:
        sentence = _settings().floor_sentence()
        assert "at least 30" in sentence
        assert "Revi's recommended level" in sentence
        assert "You can change this anytime." in sentence
