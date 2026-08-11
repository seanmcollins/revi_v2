"""Closed Pydantic response models for every structured LLM call site.

Pydantic is allowed HERE ONLY within ``revi_investigation`` (architecture
rule: the domain is frozen dataclasses; the LLM boundary is JSON-schema
constrained). Every model uses ``extra="forbid"`` so the emitted schema
carries ``additionalProperties: false``, and every enum-ish field is a
``Literal`` mirroring the corresponding closed domain set exactly. Nothing
a model returns is trusted until it is re-validated against pack, catalog,
or referent-registry content.

The twelve-operator refinement DTO union is the platform's *public* wire
shape (typed gestures, traces, replay), so `revi_investigation_contracts`
owns it; this module re-exports it so engine code has a single schema
import site, and layers `RefinementEmissionResponse` on top for the
`emit_refinements` call site.

``sanitize_json_schema`` strips the OpenAPI-style ``discriminator`` keyword
Pydantic emits beside ``oneOf`` for discriminated unions: the Claude CLI's
strict JSON-Schema validator rejects the keyword (SDK spike RESULTS.md,
trap #1). The strip is lossless — ``oneOf`` plus each variant's ``const``
``op`` still pins the union.

``clarification_options`` trims the recovery options a model may propose
alongside a clarification. They are the only free text here that reaches a
UI affordance rather than a sentence, so the trim is deterministic and
tight — see the function.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    AnchoredWindowModel,
    AnyRefinementOperator,
    ComparisonLiteral,
    DrillIntoModel,
    EntityGrainLiteral,
    ExpandModel,
    ExplainModel,
    PivotModel,
    PredicateOpLiteral,
    RangeModeLiteral,
    RankByModel,
    RefinementOperatorModel,
    RemoveFilterModel,
    ResetContextModel,
    ScalarValue,
    SetComparisonModel,
    SetDimensionsModel,
    SetGrainModel,
    SetWindowModel,
    TimeBucketLiteral,
    TimeUnitLiteral,
    WindowSpecModel,
)

__all__ = [
    "MAX_CLARIFICATION_OPTIONS",
    "AbsoluteWindowModel",
    "AddFilterModel",
    "AnchoredWindowModel",
    "AnswerShapeLiteral",
    "AnyRefinementOperator",
    "AskedDirectionLiteral",
    "AskedMagnitudeLiteral",
    "AskedOrderLiteral",
    "ChartSuggestionResponse",
    "ComparisonLiteral",
    "DeepResearchPlanResponse",
    "DeepResearchRequestModel",
    "DrillIntoModel",
    "EntityGrainLiteral",
    "ExpandModel",
    "ExplainModel",
    "GroundedOptionModel",
    "InterpretationResponse",
    "PivotModel",
    "PredicateOpLiteral",
    "RangeModeLiteral",
    "RankByModel",
    "ReferentResolutionModel",
    "ReferentResolutionResponse",
    "RefinementEmissionResponse",
    "RefinementOperatorModel",
    "RemoveFilterModel",
    "ResearchAngleModel",
    "ResearchBasisLiteral",
    "ResearchFamilyLiteral",
    "ResearchPopulationLiteral",
    "ResearchStratumLiteral",
    "ResetContextModel",
    "ScalarValue",
    "ScopePredicateModel",
    "SetComparisonModel",
    "SetDimensionsModel",
    "SetGrainModel",
    "SetWindowModel",
    "TimeBucketLiteral",
    "TimeUnitLiteral",
    "TurnClassLiteral",
    "TurnClassificationResponse",
    "WindowSpecModel",
    "clarification_options",
    "sanitize_json_schema",
]

# ---------------------------------------------------------------------------
# shared closed vocabularies (mirroring kernel / domain enums)

TurnClassLiteral = Literal[
    "new_investigation",
    "refinement",
    "presentation_only",
    "context_control",
    "meta",
    "clarification_response",
    "definitional",
]

#: What the question wants its FIRST SENTENCE to be. A closed set, and
#: deliberately about the answer's shape rather than about the question's
#: grammar: "do we owe refunds?", "are we at risk of X?" and "do I have a
#: COB problem?" all want a yes or a no before anything else, and none of
#: them produces a premise object, so nothing downstream could tell they
#: were the same shape.
#:
#: Mirrors :class:`revi_investigation.domain.context.AnswerShape` exactly.
AnswerShapeLiteral = Literal[
    "verdict",
    "entity",
    "scalar",
    "cause",
    "trend",
    "comparison",
    "definition",
    "worklist",
]

#: Mirrors :class:`revi_investigation.domain.context.AskedDirection` exactly.
AskedDirectionLiteral = Literal["increase", "decrease", "worsened", "improved"]
#: Mirrors :class:`revi_investigation.domain.context.AskedMagnitude` exactly.
AskedMagnitudeLiteral = Literal["largest", "smallest"]
#: Mirrors :class:`revi_investigation.domain.context.AskedOrder` exactly.
AskedOrderLiteral = Literal["best_first", "worst_first"]


class _Closed(BaseModel):
    """Base: closed models only — unknown keys are schema violations."""

    model_config = ConfigDict(extra="forbid")


#: Chips on a clarification card, not a menu. Four is what fits before the
#: analyst is reading a list instead of choosing from one.
MAX_CLARIFICATION_OPTIONS = 4
_OPTION_MAX_CHARS = 120


def clarification_options(raw: Sequence[str]) -> tuple[str, ...]:
    """Deterministically trim the recovery options a model proposed.

    A model that asks for clarification may also propose ways forward —
    restatements it could answer, or governed concepts it thinks were meant.
    They are suggestions, so they stay free text (exactly like the
    clarification question itself); what is *not* left to the model is how
    many arrive or how long they are. Options are whitespace-flattened,
    clipped, emptied of blanks, de-duplicated case-insensitively in
    first-seen order, and cut to :data:`MAX_CLARIFICATION_OPTIONS`.
    """
    seen: set[str] = set()
    options: list[str] = []
    for candidate in raw:
        flat = " ".join(candidate.split())[:_OPTION_MAX_CHARS].strip()
        if not flat or flat.casefold() in seen:
            continue
        seen.add(flat.casefold())
        options.append(flat)
        if len(options) == MAX_CLARIFICATION_OPTIONS:
            break
    return tuple(options)


# ---------------------------------------------------------------------------
# classify_turn


class TurnClassificationResponse(_Closed):
    turn_class: TurnClassLiteral
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_question: str | None = None
    #: Optional recovery chips beside the question — trimmed by
    #: :func:`clarification_options` before anything renders them.
    clarification_options: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# interpret_question


class ScopePredicateModel(_Closed):
    dimension: str
    op: PredicateOpLiteral
    values: list[ScalarValue] = Field(default_factory=list)


class GroundedOptionModel(_Closed):
    """One recovery option, stated in the governed vocabulary.

    A clarification option is the one piece of free text that becomes a
    *button*: an analyst who taps it is promised the platform can answer it.
    A bare sentence does not resolve against a pack, so "compare denial
    rates across all Medicare Advantage payers" can be offered and then
    refused one turn later, having named a capability nobody checked.

    So an option carries the ids it would use alongside the label it shows,
    and every one of them goes through the same disposal an interpretation
    does (:meth:`InterpretQuestionService.ground_option`). An option that
    fails is dropped rather than shown; the label is never the contract.
    """

    #: What the analyst reads and may send back verbatim.
    label: str
    metric_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    playbook_id: str | None = None
    scope: list[ScopePredicateModel] = Field(default_factory=list)


#: Which denials a deep-research run would cover. Mirrors
#: :class:`revi_investigation.application.deep_research.grammar.PopulationKind`.
ResearchPopulationLiteral = Literal["all_open", "payer", "recovery_class", "facility"]


class DeepResearchRequestModel(_Closed):
    """A request to launch the recoverability mode over a population.

    The analyst asked for a deep look at what is recoverable, not for a
    figure. The model's whole job here is to say WHICH denials — the
    population and its values — and every value is re-checked against the
    data before anything runs. It never selects the analysis: that comes
    from the closed angle catalogue, chosen separately.
    """

    population: ResearchPopulationLiteral = "all_open"
    #: Names to narrow to, exactly as the analyst wrote them. Empty for
    #: every open denial.
    values: list[str] = Field(default_factory=list)


class InterpretationResponse(_Closed):
    intent_summary: str
    #: The shape the answer's FIRST SENTENCE owes the question. Closed set;
    #: the composer is told it and told to answer in it, so a yes/no
    #: question stops being answered with a concentration ranking and a
    #: how-much question stops being answered with three shares of a total
    #: it never prints.
    answer_shape: AnswerShapeLiteral | None = None
    #: The metric the question is ABOUT, out of ``metric_ids`` or the
    #: playbook's own probes. Re-validated against the pack and dropped if
    #: it does not resolve. Without it nothing downstream can prefer the
    #: asked metric's frame over a sibling frame, which is how "where are
    #: denials rising" gets answered in denied dollars by CARC while
    #: ``denial_rate`` is charted and publishes no finding.
    subject_metric: str | None = None
    metric_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    playbook_id: str | None = None
    #: Three closed window shapes, and the analyst decides which applies:
    #: a *relative* span ("the last 6 months"), a *named* calendar period
    #: ("June 2026", "Q2 2026", "2025"), or an explicit pair of dates. The
    #: named shape is the one the chat box could not previously say — see
    #: :class:`~revi_investigation_contracts.refinements.AnchoredWindowModel`.
    window: WindowSpecModel | AnchoredWindowModel | AbsoluteWindowModel | None = None
    #: The time bucket a "by month/week/day" breakdown asks for. It is a
    #: *grain*, not a dimension. Without a field to carry the bucketing
    #: axis, "denial rate by month for the last 6 months" resolves to one
    #: six-month scalar with no series and no warning (design §6.1: entity
    #: grain and time bucket are orthogonal).
    time_grain: TimeBucketLiteral | None = None
    basis: str | None = None
    comparison: ComparisonLiteral | None = None
    scope: list[ScopePredicateModel] = Field(default_factory=list)
    #: The movement the question asks about, when it asks about one
    #: ("biggest increase", "which payers got worse"). Closed set; never
    #: inferred here — an unasserted direction stays ``None``.
    direction: AskedDirectionLiteral | None = None
    #: The extremity the question phrases over that direction.
    magnitude: AskedMagnitudeLiteral | None = None
    #: ``true`` when the question states the movement as a FACT it wants
    #: explained ("why did denials double") rather than asking which cells
    #: moved that way ("which payers rose most"). A stated movement is a
    #: premise, and a premise gets verified against the aggregate before
    #: anything is offered as its cause.
    direction_asserted: bool = False
    #: The SIZE that movement asserts, as a multiple of the prior level: 2
    #: for "doubled", 0.5 for "halved", 4 for "quadruple", 3 for "jumped
    #: 200%". A closed word table alone leaves gaps — holding "halved" but
    #: not "halve" publishes "Premise confirmed … -8.0%" for "why did cash
    #: collections HALVE in July?". The table is still the deterministic
    #: override for every word it holds; this field covers the phrasings a
    #: table cannot. Null when no size was asserted.
    asserted_multiple: float | None = Field(default=None, gt=0)
    #: The order a ranking was asked to arrive in ("best to worst" →
    #: ``best_first``). Closed set; never inferred — an unstated order stays
    #: ``None`` and the pack's own default applies.
    order: AskedOrderLiteral | None = None
    clarification: str | None = None
    #: Optional recovery chips beside ``clarification``, each stated in the
    #: governed vocabulary — validated and dropped on failure before
    #: anything renders them (design §2.8; see :class:`GroundedOptionModel`).
    clarification_options: list[GroundedOptionModel] = Field(default_factory=list)
    definitional_terms: list[str] = Field(default_factory=list)
    #: Set only when the analyst asked for the recoverability mode by name
    #: — "run deep research on…", "do a deep dive on what we can recover
    #: from…". It names the population, never the analysis, and the turn
    #: it rides on still answers the question it was asked: this is an
    #: offer to launch, not a launch.
    deep_research: DeepResearchRequestModel | None = None


# ---------------------------------------------------------------------------
# resolve_referents


class ReferentResolutionModel(_Closed):
    mention: str
    referent_id: str
    confidence: float = Field(ge=0.0, le=1.0)


class ReferentResolutionResponse(_Closed):
    resolutions: list[ReferentResolutionModel] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# emit_refinements — the twelve-operator closed set, discriminated on "op"


class RefinementEmissionResponse(_Closed):
    operators: list[RefinementOperatorModel]
    rationale: str
    #: Optional recovery chips for the "no operators" case — trimmed by
    #: :func:`clarification_options` before anything renders them.
    clarification_options: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# plan_deep_research — the closed angle catalogue

#: The research angles a run may contain. Mirrors
#: :class:`revi_investigation.application.deep_research.grammar.AngleFamily`.
ResearchFamilyLiteral = Literal[
    "outcome_by_stratum",
    "payer_contrast",
    "class_contrast",
    "timeliness_curve",
    "deadline_interaction",
    "expected_recovery",
]

#: The populations an angle may cut by. Mirrors
#: :class:`revi_investigation.application.deep_research.grammar.Stratum`.
ResearchStratumLiteral = Literal[
    "payer",
    "plan",
    "recovery_class",
    "age_band",
    "dollar_band",
    "delay_band",
    "filing_position",
    "filing_rule",
]

#: Mirrors
#: :class:`revi_investigation.application.deep_research.grammar.RateBasisChoice`.
ResearchBasisLiteral = Literal["decided", "pursuit"]


class ResearchAngleModel(_Closed):
    """One angle, chosen from the catalogue and cut by named populations."""

    family: ResearchFamilyLiteral
    stratify_by: list[ResearchStratumLiteral] = Field(default_factory=list)
    within: list[ResearchStratumLiteral] = Field(default_factory=list)
    basis: ResearchBasisLiteral = "decided"


class DeepResearchPlanResponse(_Closed):
    """Which angles a run should look at, and why.

    Selection only. Nothing here produces a number, and an angle naming
    anything outside the catalogue does not become a weaker analysis — it
    is dropped before a single denial is read.
    """

    research_question: str
    angles: list[ResearchAngleModel] = Field(default_factory=list)
    rationale: str = ""


# ---------------------------------------------------------------------------
# suggest_chart


class ChartSuggestionResponse(_Closed):
    chart_type: Literal["bar", "line", "area", "pie", "table"]
    x: str
    series: str | None = None
    value: str


# ---------------------------------------------------------------------------
# schema sanitization (spike RESULTS.md trap #1)


def sanitize_json_schema(schema: Any) -> Any:
    """Recursively strip the ``discriminator`` keyword from a JSON schema.

    Pydantic emits an OpenAPI-style ``discriminator`` next to ``oneOf`` for
    discriminated unions; the Claude CLI's strict JSON-Schema validator
    rejects unknown keywords. Removal is lossless: the ``oneOf`` plus each
    variant's ``const`` discriminator field still pins the union.
    """
    if isinstance(schema, dict):
        return {
            key: sanitize_json_schema(value)
            for key, value in schema.items()
            if key != "discriminator"
        }
    if isinstance(schema, list):
        return [sanitize_json_schema(item) for item in schema]
    return schema
