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
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
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
    "AbsoluteWindowModel",
    "AddFilterModel",
    "AnyRefinementOperator",
    "ChartSuggestionResponse",
    "ComparisonLiteral",
    "DrillIntoModel",
    "EntityGrainLiteral",
    "ExpandModel",
    "ExplainModel",
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
