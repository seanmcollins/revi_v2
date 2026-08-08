"""The public refinement-operator DTO union (design §7.4, §12).

This is the wire shape of the closed twelve-operator set: typed-gesture
turn requests carry these models, the LLM's ``emit_refinements`` schema is
generated from them, and traces record them for replay. Contracts owns the
shape; ``revi_investigation.application.llm.schemas`` re-exports it so the
engine's schema module remains the single import site inside the engine.

Every model forbids unknown keys (``additionalProperties: false`` in the
emitted JSON schema) and every enum-ish field is a ``Literal`` mirroring
the corresponding closed kernel set.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

TimeUnitLiteral = Literal["day", "week", "month", "quarter", "year"]
RangeModeLiteral = Literal["trailing", "full_periods", "to_date"]
ComparisonLiteral = Literal["prior_period", "prior_year"]
PredicateOpLiteral = Literal["eq", "neq", "in", "not_in", "range", "is_null", "contains"]
EntityGrainLiteral = Literal["claim", "line", "encounter", "transaction", "remit", "denial"]
TimeBucketLiteral = Literal["day", "week", "month"]

ScalarValue = Union[str, int, float, bool, None]  # noqa: UP007 - schema union spelled out


class ClosedModel(BaseModel):
    """Base for closed DTOs: unknown keys are schema violations."""

    model_config = ConfigDict(extra="forbid")


class WindowSpecModel(ClosedModel):
    """A relative window; ``quantity`` is a decimal string ("1", "3.25")."""

    quantity: str
    unit: TimeUnitLiteral
    mode: RangeModeLiteral


class AbsoluteWindowModel(ClosedModel):
    start: date
    end: date


class SetDimensionsModel(ClosedModel):
    op: Literal["set_dimensions"]
    dimensions: list[str]


class AddFilterModel(ClosedModel):
    op: Literal["add_filter"]
    dimension: str
    predicate_op: PredicateOpLiteral
    values: list[ScalarValue] = Field(default_factory=list)


class RemoveFilterModel(ClosedModel):
    op: Literal["remove_filter"]
    dimension: str


class SetWindowModel(ClosedModel):
    op: Literal["set_window"]
    window: WindowSpecModel | AbsoluteWindowModel
    basis: str | None = None


class SetComparisonModel(ClosedModel):
    op: Literal["set_comparison"]
    kind: ComparisonLiteral | None = None
    custom: AbsoluteWindowModel | None = None


class SetGrainModel(ClosedModel):
    op: Literal["set_grain"]
    entity: EntityGrainLiteral
    time_bucket: TimeBucketLiteral | None = None


class DrillIntoModel(ClosedModel):
    op: Literal["drill_into"]
    target: str


class PivotModel(ClosedModel):
    op: Literal["pivot"]
    measures: list[str]


class ExplainModel(ClosedModel):
    op: Literal["explain"]
    target: str


class RankByModel(ClosedModel):
    op: Literal["rank_by"]
    by: str
    descending: bool = True


class ExpandModel(ClosedModel):
    op: Literal["expand"]
    limit: int = Field(gt=0)


class ResetContextModel(ClosedModel):
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
