"""The generalized planner: what the model is shown, and what it may return.

The recovery planner could be closed at the schema — six families, eight
breakdowns, all of it content this repo ships. This one cannot: which
measures exist is a fact about one deployment's definitions library. So the
guarantee moves one layer down, and these tests are where that move is
checked.

**What goes out.** The prompt carries the orientation's own sentences, the
consulted background notes as prose, and the vocabulary this deployment
actually carries — and it carries no data row, no query and no figure the
platform did not publish.

**What may come back.** The shape is closed by the schema. Everything else
is re-resolved against the vocabulary, and an invented measure, a stray
breakdown or a trend over an as-of level is dropped rather than downgraded.

**What a reader sees.** A reason is client copy whoever wrote it, so a
model sentence carrying an internal identifier is turned into words before
it reaches a card.

**What the content still decides.** The model proposes chases; the research
thresholds gate them. A narrowing into a population nothing separated is
dropped with the rule, its numbers and its owner said out loud.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from revi_investigation.application.deep_research.general import (
    AngleShape,
    AngleVocabulary,
    MeasureAngle,
    PlannedAngle,
    TimeStep,
)
from revi_investigation.application.deep_research.general_llm import (
    MAX_REASON_CHARS,
    LlmGeneralPlanner,
    orientation_block,
    planned_angle_of,
    reason_words,
    results_block,
    vocabulary_block,
    window_words,
)
from revi_investigation.application.deep_research.grammar import TargetPopulation
from revi_investigation.application.deep_research.knowledge import (
    ConsultedEntry,
    KnowledgeConsultation,
)
from revi_investigation.application.deep_research.loop import (
    Lead,
    Orientation,
    ResearchRound,
    gate_chases,
    leads_of,
    round_words,
    validate_angles,
)
from revi_investigation.application.deep_research.measures import MeasureCell, MeasureResult
from revi_investigation.application.deep_research.policy import ResearchPolicy
from revi_investigation.application.discovery import DiscoveryKind, DiscoveryNote
from revi_investigation.application.llm.schemas import (
    GeneralizedAngleModel,
    GeneralizedResearchPlanResponse,
    ResearchWithinModel,
    sanitize_json_schema,
)
from revi_investigation.application.ports import (
    LlmFailureKind,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)
from revi_kernel.scope import AbsoluteRange
from revi_testing.fakes import make_usage

WINDOW = AbsoluteRange(start=date(2025, 9, 1), end=date(2026, 8, 2))

VOCABULARY = AngleVocabulary(
    measures={
        "ar_over_90_pct": frozenset({"payer", "facility", "financial_class"}),
        "denial_rate": frozenset({"payer", "carc"}),
        "denied_dollars": frozenset({"payer", "carc"}),
    },
    bases={
        "ar_over_90_pct": frozenset({"service"}),
        "denial_rate": frozenset({"service", "submission"}),
        "denied_dollars": frozenset({"service"}),
    },
    kinds={
        "ar_over_90_pct": "snapshot",
        "denial_rate": "flow",
        "denied_dollars": "flow",
    },
    units={
        "ar_over_90_pct": "percent",
        "denial_rate": "percent",
        "denied_dollars": "money_cents",
    },
    rate_like=frozenset({"denial_rate"}),
    descriptions={
        "ar_over_90_pct": "Share of open A/R that has been outstanding more than 90 days.",
        "denial_rate": "Share of adjudicated claims that came back denied.",
        "denied_dollars": "Charged amount on denied claims.",
    },
)

KNOWLEDGE = KnowledgeConsultation(
    question="why is A/R over 90 climbing",
    terms=("aged ar",),
    entries=(
        ConsultedEntry(
            id="benchmark.aged_ar_over_90",
            title="Aged A/R over 90 days",
            summary="Aged A/R is the lagging indicator of everything upstream.",
            key_points=("A climbing over-90 is usually denials, underpayments or posting.",),
            cautions=("Read it beside days in A/R; a growing denominator hides a climb.",),
            review_status="reviewed",
            matched_on=("aged ar",),
            score=30,
        ),
    ),
    corpus_size=42,
    statement="I read 1 background note from your definitions library before choosing.",
)


def orientation(
    *,
    question: str = "why has our A/R over 90 been climbing",
    measures: tuple[str, ...] = ("ar_over_90_pct", "denial_rate"),
    knowledge: KnowledgeConsultation = KNOWLEDGE,
    policy: ResearchPolicy | None = None,
) -> Orientation:
    return Orientation(
        question=question,
        population=TargetPopulation(),
        window=WINDOW,
        vocabulary=VOCABULARY,
        notes=(
            DiscoveryNote(
                kind=DiscoveryKind.DIMENSION_CENSUS,
                subject="payer",
                statement="Payer is populated on 99.8% of claims and takes 14 values here.",
                request_key="census:payer",
            ),
            DiscoveryNote(
                kind=DiscoveryKind.MEASURE_AVAILABILITY,
                subject="everything in your data",
                statement="21 standard measures can be read over this population.",
                request_key="availability:all",
            ),
        ),
        concepts=("coordination_of_benefits",),
        measures=measures,
        cut_for={"ar_over_90_pct": "payer"},
        knowledge=knowledge,
        policy=policy or ResearchPolicy(),
    )


# ---------------------------------------------------------------------------
# doubles


@dataclass
class FixedLlm:
    """A one-answer ``LanguageModelPort`` that records what it was asked."""

    result: StructuredLlmResult
    prompt: str = ""
    schema: dict[str, Any] | None = None

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        self.prompt = request.rendered_prompt
        self.schema = dict(request.schema)
        return self.result

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        raise AssertionError("the planner never streams")

    async def last_usage(self):
        return None


def answering(**payload: Any) -> FixedLlm:
    body = GeneralizedResearchPlanResponse.model_validate(payload).model_dump(mode="json")
    return FixedLlm(StructuredLlmResult(output=body, usage=make_usage()))


def empty_handed(kind: LlmFailureKind | None = LlmFailureKind.DECLINED) -> FixedLlm:
    return FixedLlm(StructuredLlmResult(output=None, usage=make_usage(), failure=kind))


def cell(label: str, value: str | None, *, dimension: str = "payer", **kw: Any) -> MeasureCell:
    return MeasureCell(
        label=label,
        parts=((dimension, label),),
        value=None if value is None else Decimal(value),
        **kw,
    )


def reading(
    title: str,
    cells: tuple[MeasureCell, ...],
    *,
    metric_id: str = "denied_dollars",
    shape: AngleShape = AngleShape.MEASURE_PROFILE,
    cut_by: tuple[str, ...] = ("payer",),
    unit: str = "money_cents",
) -> MeasureResult:
    return MeasureResult(
        angle=PlannedAngle(
            shape=shape,
            reason="because the question asked",
            measure=MeasureAngle(metric_id=metric_id, cut_by=cut_by),
        ),
        metric_id=metric_id,
        title=title,
        unit=unit,
        grade="direct",
        cells=cells,
        window=WINDOW,
        basis="service",
        read_fingerprint="f" * 64,
    )


# ---------------------------------------------------------------------------
# what the model is shown


class TestThePrompt:
    def test_every_placeholder_is_filled(self) -> None:
        """A template and its call site drift loudly or not at all."""
        planner = LlmGeneralPlanner(empty_handed())
        prompt = planner.render(orientation(), (), round_index=0, budget=3)
        assert "{" not in prompt.replace("{dimension}", "")
        assert prompt.rstrip().endswith("why has our A/R over 90 been climbing")

    def test_it_carries_the_orientation_sentences_verbatim(self) -> None:
        planner = LlmGeneralPlanner(empty_handed())
        prompt = planner.render(orientation(), (), round_index=0, budget=3)
        assert "Payer is populated on 99.8% of claims and takes 14 values here." in prompt

    def test_it_carries_the_consulted_knowledge_as_prose(self) -> None:
        """The pack's RCM brains finally reaching the thing that plans."""
        planner = LlmGeneralPlanner(empty_handed())
        prompt = planner.render(orientation(), (), round_index=0, budget=3)
        assert "Aged A/R over 90 days" in prompt
        assert "usually denials, underpayments or posting" in prompt
        assert "a growing denominator hides a climb" in prompt

    def test_it_carries_this_deployments_own_vocabulary(self) -> None:
        planner = LlmGeneralPlanner(empty_handed())
        prompt = planner.render(orientation(), (), round_index=0, budget=3)
        assert "`ar_over_90_pct`" in prompt
        assert "`financial_class`" in prompt
        assert "Share of adjudicated claims that came back denied." in prompt

    def test_the_measure_the_question_names_is_listed_first(self) -> None:
        block = vocabulary_block(VOCABULARY, ("denial_rate",))
        assert block.index("`denial_rate`") < block.index("`ar_over_90_pct`")

    def test_an_as_of_level_is_named_as_one_and_a_rate_as_one(self) -> None:
        block = vocabulary_block(VOCABULARY, ())
        aged, denial = block.split("`denial_rate`")[0], block.split("`denial_rate`")[1]
        assert "no trend" in aged
        assert "may be stratified and tested" in denial

    def test_the_opening_round_says_nothing_has_run(self) -> None:
        assert "opening read" in results_block((), (), round_index=0)

    def test_a_later_round_quotes_its_own_certified_cells(self) -> None:
        rounds = (
            ResearchRound(
                index=0,
                results=(
                    reading(
                        "denied dollars by payer",
                        (cell("Atlas Commercial", "410000"), cell("Northbridge", "120000")),
                    ),
                ),
            ),
        )
        block = results_block(rounds, (), round_index=1)
        assert "denied dollars by payer" in block
        assert "$4,100.00" in block

    def test_a_withheld_cell_is_described_and_its_number_is_not_in_the_prompt(self) -> None:
        """The disclosure rules do not have an exemption for prompts."""
        rounds = (
            ResearchRound(
                index=0,
                results=(
                    reading(
                        "denied dollars by payer",
                        (cell("Tiny Plan", None, withheld=True),),
                    ),
                ),
            ),
        )
        block = results_block(rounds, (), round_index=1)
        assert "too small to publish" in block

    def test_only_admitted_leads_are_offered_as_places_to_go_inside(self) -> None:
        leads = (
            Lead(
                title="denied dollars by payer",
                dimension="payer",
                value="Veritas Comp Fund",
                shown="Veritas Comp Fund",
                why="the payer spread was wide",
            ),
        )
        block = results_block((ResearchRound(index=0, results=()),), leads, round_index=1)
        assert "`payer` = `Veritas Comp Fund`" in block
        assert "the payer spread was wide" in block

    def test_with_nothing_separated_the_planner_is_told_not_to_narrow(self) -> None:
        block = results_block((ResearchRound(index=0, results=()),), (), round_index=1)
        assert "do not use `within` this round" in block

    def test_the_period_reads_as_a_date_not_a_range_literal(self) -> None:
        assert window_words(WINDOW) == "Sep 1, 2025 through Aug 2, 2026"

    def test_an_orientation_that_found_nothing_says_so(self) -> None:
        bare = Orientation(
            question="q",
            population=TargetPopulation(),
            window=WINDOW,
            vocabulary=VOCABULARY,
            notes=(),
            concepts=(),
            measures=(),
            cut_for={},
            knowledge=KNOWLEDGE,
            policy=ResearchPolicy(),
        )
        assert "Nothing could be established" in orientation_block(bare)


# ---------------------------------------------------------------------------
# what may come back


class TestWhatSurvives:
    async def test_a_legal_plan_becomes_planned_angles_with_their_reasons(self) -> None:
        llm = answering(
            angles=[
                {
                    "shape": "trend",
                    "metric_id": "denial_rate",
                    "step": "month",
                    "reason": "whether the climb is recent or has been running all year",
                },
                {
                    "shape": "contrast",
                    "metric_id": "denial_rate",
                    "cut_by": ["payer"],
                    "reason": "whether one payer is carrying it",
                },
            ],
            rationale="Read the movement first, then who is behind it.",
        )
        planner = LlmGeneralPlanner(llm)
        angles, rationale = await planner.open(orientation(), budget=3)
        legal = validate_angles(angles, orientation())
        assert [angle.shape for angle in legal] == [AngleShape.TREND, AngleShape.CONTRAST]
        assert legal[0].measure is not None and legal[0].measure.step is TimeStep.MONTH
        assert legal[0].reason.startswith("whether the climb")
        assert rationale == "Read the movement first, then who is behind it."

    async def test_an_invented_measure_does_not_become_a_weaker_reading(self) -> None:
        llm = answering(
            angles=[
                {
                    "shape": "measure_profile",
                    "metric_id": "profit_margin",
                    "reason": "how profitable we are",
                }
            ]
        )
        angles, _ = await LlmGeneralPlanner(llm).open(orientation(), budget=3)
        assert validate_angles(angles, orientation()) == ()

    async def test_a_stray_breakdown_is_trimmed_and_the_reading_survives(self) -> None:
        llm = answering(
            angles=[
                {
                    "shape": "measure_profile",
                    "metric_id": "denied_dollars",
                    "cut_by": ["payer", "moon_phase"],
                    "reason": "where the denied dollars sit",
                }
            ]
        )
        angles, _ = await LlmGeneralPlanner(llm).open(orientation(), budget=3)
        legal = validate_angles(angles, orientation())
        assert len(legal) == 1
        assert legal[0].measure is not None
        assert legal[0].measure.cut_by == ("payer",)

    async def test_a_trend_over_an_as_of_level_is_dropped(self) -> None:
        """A/R over 90 is a level. Bucketing one is a category error the
        adapter refuses, and catching it at plan time turns a mid-run
        failure into a reading that never existed."""
        llm = answering(
            angles=[
                {
                    "shape": "trend",
                    "metric_id": "ar_over_90_pct",
                    "step": "month",
                    "reason": "how the aging has moved",
                }
            ]
        )
        angles, _ = await LlmGeneralPlanner(llm).open(orientation(), budget=3)
        assert validate_angles(angles, orientation()) == ()

    async def test_a_rate_shape_over_a_measure_with_no_population_is_dropped(self) -> None:
        llm = answering(
            angles=[
                {
                    "shape": "stratified_rates",
                    "metric_id": "denied_dollars",
                    "cut_by": ["payer"],
                    "reason": "denied dollars by payer, as a rate",
                }
            ]
        )
        angles, _ = await LlmGeneralPlanner(llm).open(orientation(), budget=3)
        assert validate_angles(angles, orientation()) == ()

    async def test_a_basis_the_measure_does_not_declare_falls_back_to_its_own(self) -> None:
        llm = answering(
            angles=[
                {
                    "shape": "measure_profile",
                    "metric_id": "denied_dollars",
                    "cut_by": ["payer"],
                    "basis": "remit",
                    "reason": "where the denied dollars sit",
                }
            ]
        )
        angles, _ = await LlmGeneralPlanner(llm).open(orientation(), budget=3)
        legal = validate_angles(angles, orientation())
        assert legal[0].measure is not None and legal[0].measure.basis is None

    @pytest.mark.parametrize(
        "kind",
        [LlmFailureKind.SCHEMA, LlmFailureKind.DECLINED, LlmFailureKind.OFF_SCRIPT, None],
    )
    async def test_an_empty_handed_call_returns_nothing_rather_than_guessing(
        self, kind: LlmFailureKind | None
    ) -> None:
        angles, rationale = await LlmGeneralPlanner(empty_handed(kind)).open(
            orientation(), budget=3
        )
        assert angles == ()
        assert rationale == ""

    async def test_an_unreadable_body_returns_nothing(self) -> None:
        llm = FixedLlm(
            StructuredLlmResult(output={"angles": "not a list"}, usage=make_usage())
        )
        angles, _ = await LlmGeneralPlanner(llm).open(orientation(), budget=3)
        assert angles == ()

    async def test_the_schema_that_goes_out_closes_the_shape(self) -> None:
        llm = empty_handed()
        await LlmGeneralPlanner(llm).open(orientation(), budget=3)
        assert llm.schema is not None
        rendered = repr(llm.schema)
        assert "measure_profile" in rendered and "stratified_rates" in rendered
        assert "additionalProperties" in rendered
        assert "discriminator" not in rendered

    def test_the_response_schema_carries_no_numeric_field(self) -> None:
        """Selection only. A planner that could return a figure would be a
        planner that could publish an uncertified one."""
        schema = sanitize_json_schema(GeneralizedResearchPlanResponse.model_json_schema())

        def types_in(node: Any) -> set[str]:
            if isinstance(node, dict):
                found = {node["type"]} if isinstance(node.get("type"), str) else set()
                for value in node.values():
                    found |= types_in(value)
                return found
            if isinstance(node, list):
                return {t for item in node for t in types_in(item)}
            return set()

        assert not types_in(schema) & {"number", "integer"}


# ---------------------------------------------------------------------------
# a reason is client copy


class TestReasonsAreClientCopy:
    def test_an_internal_identifier_becomes_words(self) -> None:
        assert (
            reason_words("ar_over_90_pct is climbing", "fallback")
            == "ar over 90 pct is climbing"
        )

    def test_whitespace_is_flattened(self) -> None:
        assert reason_words("one\n  two\t three", "fallback") == "one two three"

    def test_a_reason_longer_than_a_sentence_is_clipped_on_a_word(self) -> None:
        clipped = reason_words("word " * 200, "fallback")
        assert len(clipped) <= MAX_REASON_CHARS + 1
        assert clipped.endswith("…")

    def test_an_empty_reason_falls_back_rather_than_leaving_a_reading_uncaused(
        self,
    ) -> None:
        assert reason_words("   ", "how it has moved") == "how it has moved"

    async def test_a_reading_that_arrives_with_no_reason_gets_a_deterministic_one(
        self,
    ) -> None:
        llm = answering(
            angles=[
                {"shape": "measure_profile", "metric_id": "denied_dollars", "cut_by": ["payer"]}
            ]
        )
        angles, _ = await LlmGeneralPlanner(llm).open(orientation(), budget=3)
        assert angles[0].reason == "where denied dollars sits, broken out by payer"


# ---------------------------------------------------------------------------
# the content still decides what is significant


class TestTheThresholdsGateTheChases:
    def test_a_chase_into_an_admitted_lead_runs(self) -> None:
        leads = (
            Lead(
                title="denied dollars by payer",
                dimension="payer",
                value="Veritas Comp Fund",
                shown="Veritas Comp Fund",
                why="the payer spread was wide",
            ),
        )
        proposed = (
            PlannedAngle(
                shape=AngleShape.MEASURE_PROFILE,
                round=1,
                reason="cutting inside the payer that separated",
                chases="denied dollars by payer",
                measure=MeasureAngle(
                    metric_id="denied_dollars",
                    cut_by=("carc",),
                    within=(("payer", "Veritas Comp Fund"),),
                ),
            ),
        )
        kept, dropped = gate_chases(proposed, leads, ResearchPolicy(), round_index=1)
        assert kept == proposed
        assert dropped == ()

    def test_a_chase_into_a_population_nothing_separated_is_dropped_with_the_rule(
        self,
    ) -> None:
        proposed = (
            PlannedAngle(
                shape=AngleShape.MEASURE_PROFILE,
                round=1,
                reason="Atlas looked interesting to me",
                measure=MeasureAngle(
                    metric_id="denied_dollars",
                    cut_by=("carc",),
                    within=(("payer", "Atlas Commercial"),),
                ),
            ),
        )
        kept, dropped = gate_chases(proposed, (), ResearchPolicy(), round_index=1)
        assert kept == ()
        assert len(dropped) == 1
        step = dropped[0]
        assert step.action == "drop"
        assert "Atlas Commercial" in step.reason
        # The rule, its numbers, its owner, and that it can be moved.
        assert "0.05" in step.reason and "1.5" in step.reason
        assert "Revi's recommended levels" in step.reason
        assert "You can change this anytime." in step.reason
        # What the model wanted is kept on the record, not thrown away.
        assert step.detail == "Atlas looked interesting to me"

    def test_a_broadening_asserts_nothing_and_passes_through(self) -> None:
        proposed = (
            PlannedAngle(
                shape=AngleShape.MEASURE_PROFILE,
                round=1,
                reason="nothing separated by payer — trying denial reason",
                measure=MeasureAngle(metric_id="denied_dollars", cut_by=("carc",)),
            ),
        )
        kept, dropped = gate_chases(proposed, (), ResearchPolicy(), round_index=1)
        assert kept == proposed and dropped == ()

    def test_a_lead_reads_the_raw_value_not_the_label_a_reader_saw(self) -> None:
        """A chase filters the column. The column holds ``16``; the reader
        saw ``16 — Missing or invalid information``."""
        coded = MeasureCell(
            label="16 — Missing or invalid information",
            parts=(("carc", "16"),),
            value=Decimal("900000"),
        )
        small = MeasureCell(label="45", parts=(("carc", "45"),), value=Decimal("100000"))
        middle = MeasureCell(label="97", parts=(("carc", "97"),), value=Decimal("300000"))
        rounds = (
            ResearchRound(
                index=0,
                results=(
                    reading(
                        "denied dollars by denial reason",
                        (coded, small, middle),
                        cut_by=("carc",),
                    ),
                ),
            ),
        )
        leads = leads_of(rounds, ResearchPolicy())
        assert len(leads) == 1
        assert leads[0].dimension == "carc"
        assert leads[0].value == "16"
        assert leads[0].shown == "16 — Missing or invalid information"

    def test_a_spread_under_the_level_is_not_a_lead(self) -> None:
        rounds = (
            ResearchRound(
                index=0,
                results=(
                    reading(
                        "denied dollars by payer",
                        (
                            cell("A", "100000"),
                            cell("B", "110000"),
                            cell("C", "120000"),
                        ),
                    ),
                ),
            ),
        )
        assert leads_of(rounds, ResearchPolicy()) == ()


# ---------------------------------------------------------------------------
# what a reader watching the run sees


class TestTheRoundIsNamedInPlainLanguage:
    def test_a_chase_round_quotes_the_reason_that_chose_it(self) -> None:
        planned = (
            PlannedAngle(
                shape=AngleShape.MEASURE_PROFILE,
                round=1,
                chases="denied dollars by payer",
                reason=(
                    "the payer spread was decisive — cutting inside Veritas Comp Fund next"
                ),
                measure=MeasureAngle(metric_id="denied_dollars", cut_by=("carc",)),
            ),
        )
        words = round_words(1, planned)
        assert words == (
            "Round 1 — chasing it: the payer spread was decisive — "
            "cutting inside Veritas Comp Fund next"
        )

    def test_a_broadening_round_says_it_is_trying_another_angle(self) -> None:
        planned = (
            PlannedAngle(
                shape=AngleShape.MEASURE_PROFILE,
                round=2,
                reason="nothing separated by payer — trying facility",
                measure=MeasureAngle(metric_id="denied_dollars", cut_by=("facility",)),
            ),
        )
        assert round_words(2, planned).startswith("Round 2 — trying another angle:")


# ---------------------------------------------------------------------------
# transport


class TestTheTransportReading:
    def test_a_within_pair_keeps_the_dimension_and_the_value_apart(self) -> None:
        angle = planned_angle_of(
            GeneralizedAngleModel(
                shape="measure_profile",
                metric_id="denied_dollars",
                cut_by=["carc"],
                within=[ResearchWithinModel(dimension="payer", value="Veritas Comp Fund")],
                reason="inside the payer that separated",
                chases="denied dollars by payer",
            ),
            round_index=1,
        )
        assert angle is not None and angle.measure is not None
        assert angle.measure.within == (("payer", "Veritas Comp Fund"),)
        assert angle.chases == "denied dollars by payer"
        assert angle.round == 1

    def test_a_reading_with_no_measure_named_is_nothing(self) -> None:
        assert (
            planned_angle_of(
                GeneralizedAngleModel(shape="trend", metric_id="  ", reason="x"),
                round_index=0,
            )
            is None
        )

    def test_a_duplicate_breakdown_is_asked_for_once(self) -> None:
        angle = planned_angle_of(
            GeneralizedAngleModel(
                shape="measure_profile",
                metric_id="denied_dollars",
                cut_by=["payer", "payer", "carc", "facility"],
                reason="x",
            ),
            round_index=0,
        )
        assert angle is not None and angle.measure is not None
        assert angle.measure.cut_by == ("payer", "carc")
