"""Closed Pydantic response models for every structured LLM call site.

Pydantic is allowed HERE ONLY within ``revi_investigation`` (architecture
rule: the domain is frozen dataclasses; the LLM boundary is JSON-schema
constrained). Every model uses ``extra="forbid"`` so the emitted schema
carries ``additionalProperties: false``, and every enum-ish field is a
``Literal`` mirroring the corresponding closed domain set exactly. Nothing
a model returns is trusted until it is re-validated against pack, catalog,
or referent-registry content.

``RefinementEmissionResponse`` mirrors the twelve-operator closed set of
``revi_investigation.domain.refinements`` with an ``op`` discriminator. It
is defined now (single schema source) although the refinement path itself
lands with the conversational core milestone.

``sanitize_json_schema`` strips the OpenAPI-style ``discriminator`` keyword
Pydantic emits beside ``oneOf`` for discriminated unions: the Claude CLI's
strict JSON-Schema validator rejects the keyword (SDK spike RESULTS.md,
trap #1). The strip is lossless — ``oneOf`` plus each variant's ``const``
``op`` still pins the union.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

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

TimeUnitLiteral = Literal["day", "week", "month", "quarter", "year"]
RangeModeLiteral = Literal["trailing", "full_periods", "to_date"]
ComparisonLiteral = Literal["prior_period", "prior_year"]
PredicateOpLiteral = Literal["eq", "neq", "in", "not_in", "range", "is_null", "contains"]
EntityGrainLiteral = Literal["claim", "line", "encounter", "transaction", "remit", "denial"]
TimeBucketLiteral = Literal["day", "week", "month"]

ScalarValue = Union[str, int, float, bool, None]  # noqa: UP007 - schema union spelled out


class _Closed(BaseModel):
    """Base: closed models only — unknown keys are schema violations."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# classify_turn


class TurnClassificationResponse(_Closed):
    turn_class: TurnClassLiteral
    confidence: float = Field(ge=0.0, le=1.0)
    clarification_question: str | None = None


# ---------------------------------------------------------------------------
# interpret_question


class WindowSpecModel(_Closed):
    """A relative window; ``quantity`` is a decimal string ("1", "3.25")."""

    quantity: str
    unit: TimeUnitLiteral
    mode: RangeModeLiteral


class ScopePredicateModel(_Closed):
    dimension: str
    op: PredicateOpLiteral
    values: list[ScalarValue] = Field(default_factory=list)


class InterpretationResponse(_Closed):
    intent_summary: str
    metric_ids: list[str] = Field(default_factory=list)
    dimension_ids: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    playbook_id: str | None = None
    window: WindowSpecModel | None = None
    basis: str | None = None
    comparison: ComparisonLiteral | None = None
    scope: list[ScopePredicateModel] = Field(default_factory=list)
    clarification: str | None = None
    definitional_terms: list[str] = Field(default_factory=list)


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


class AbsoluteWindowModel(_Closed):
    start: date
    end: date


class SetDimensionsModel(_Closed):
    op: Literal["set_dimensions"]
    dimensions: list[str]


class AddFilterModel(_Closed):
    op: Literal["add_filter"]
    dimension: str
    predicate_op: PredicateOpLiteral
    values: list[ScalarValue] = Field(default_factory=list)


class RemoveFilterModel(_Closed):
    op: Literal["remove_filter"]
    dimension: str


class SetWindowModel(_Closed):
    op: Literal["set_window"]
    window: WindowSpecModel | AbsoluteWindowModel
    basis: str | None = None


class SetComparisonModel(_Closed):
    op: Literal["set_comparison"]
    kind: ComparisonLiteral | None = None
    custom: AbsoluteWindowModel | None = None


class SetGrainModel(_Closed):
    op: Literal["set_grain"]
    entity: EntityGrainLiteral
    time_bucket: TimeBucketLiteral | None = None


class DrillIntoModel(_Closed):
    op: Literal["drill_into"]
    target: str


class PivotModel(_Closed):
    op: Literal["pivot"]
    measures: list[str]


class ExplainModel(_Closed):
    op: Literal["explain"]
    target: str


class RankByModel(_Closed):
    op: Literal["rank_by"]
    by: str
    descending: bool = True


class ExpandModel(_Closed):
    op: Literal["expand"]
    limit: int = Field(gt=0)


class ResetContextModel(_Closed):
    op: Literal["reset_context"]
    keep_pins: bool = True


# The plain twelve-variant union (typed-gesture requests carry these
# directly); the Annotated form below adds the discriminator for parsing.
AnyRefinementOperator = Union[  # noqa: UP007 - closed union spelled out for clarity
    SetDimensionsModel,
    AddFilterModel,
    RemoveFilterModel,
    SetWindowModel,
    SetComparisonModel,
    SetGrainModel,
    DrillIntoModel,
    PivotModel,
    ExplainModel,
    RankByModel,
    ExpandModel,
    ResetContextModel,
]

RefinementOperatorModel = Annotated[AnyRefinementOperator, Field(discriminator="op")]


class RefinementEmissionResponse(_Closed):
    operators: list[RefinementOperatorModel]
    rationale: str


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
