"""Domain → contract-DTO mapping for refinement operators.

The inverse of ``refinement_llm.to_domain_operators``: lineage edges and
replay payloads serialize the domain operators back into the public DTO
union. Mapping lives in the investigation application (not contracts)
because it touches domain types, which contracts must not import.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from revi_investigation.domain.refinements import (
    AddFilter,
    DrillInto,
    Expand,
    Explain,
    Pivot,
    RankBy,
    Refinement,
    RemoveFilter,
    ResetContext,
    SetComparison,
    SetDimensions,
    SetGrain,
    SetWindow,
)
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    AnyRefinementOperator,
    DrillIntoModel,
    ExpandModel,
    ExplainModel,
    PivotModel,
    RankByModel,
    RemoveFilterModel,
    ResetContextModel,
    ScalarValue,
    SetComparisonModel,
    SetDimensionsModel,
    SetGrainModel,
    SetWindowModel,
    WindowSpecModel,
)
from revi_kernel.filters import Scalar
from revi_kernel.scope import AbsoluteRange, ComparisonKind, RelativeRange


def _scalar_value(value: Scalar) -> ScalarValue:
    if isinstance(value, Decimal):
        return float(value)
    if value is None or isinstance(value, (str, int, bool, float)):
        return value
    return str(value)  # dates and other kernel scalars serialize as text


def _window_model(window: RelativeRange | AbsoluteRange) -> WindowSpecModel | AbsoluteWindowModel:
    if isinstance(window, AbsoluteRange):
        return AbsoluteWindowModel(start=window.start, end=window.end)
    return WindowSpecModel(
        quantity=str(window.quantity), unit=window.unit.value, mode=window.mode.value
    )


def refinement_to_dto(op: Refinement) -> AnyRefinementOperator:
    """Serialize one domain operator into its public DTO shape."""
    if isinstance(op, SetDimensions):
        return SetDimensionsModel(op="set_dimensions", dimensions=[d.id for d in op.dimensions])
    if isinstance(op, AddFilter):
        return AddFilterModel(
            op="add_filter",
            dimension=op.predicate.dimension.id,
            predicate_op=op.predicate.op.value,
            values=[_scalar_value(v) for v in op.predicate.values],
        )
    if isinstance(op, RemoveFilter):
        return RemoveFilterModel(op="remove_filter", dimension=op.dimension.id)
    if isinstance(op, SetWindow):
        return SetWindowModel(
            op="set_window",
            window=_window_model(op.window),
            basis=op.basis.id if op.basis is not None else None,
        )
    if isinstance(op, SetComparison):
        if op.kind is ComparisonKind.CUSTOM and op.custom is not None:
            return SetComparisonModel(
                op="set_comparison",
                kind=None,
                custom=AbsoluteWindowModel(start=op.custom.start, end=op.custom.end),
            )
        kind_literal: Literal["prior_period", "prior_year"] | None = None
        if op.kind is ComparisonKind.PRIOR_PERIOD:
            kind_literal = "prior_period"
        elif op.kind is ComparisonKind.PRIOR_YEAR:
            kind_literal = "prior_year"
        return SetComparisonModel(op="set_comparison", kind=kind_literal, custom=None)
    if isinstance(op, SetGrain):
        return SetGrainModel(
            op="set_grain",
            entity=op.grain.entity.value,
            time_bucket=(
                op.grain.time_bucket.value if op.grain.time_bucket is not None else None
            ),
        )
    if isinstance(op, DrillInto):
        return DrillIntoModel(op="drill_into", target=op.target.value)
    if isinstance(op, Pivot):
        return PivotModel(op="pivot", measures=[m.id for m in op.measures])
    if isinstance(op, Explain):
        return ExplainModel(op="explain", target=op.target.value)
    if isinstance(op, RankBy):
        return RankByModel(op="rank_by", by=op.by.id, descending=op.descending)
    if isinstance(op, Expand):
        return ExpandModel(op="expand", limit=op.limit)
    assert isinstance(op, ResetContext)  # the union is closed
    return ResetContextModel(op="reset_context", keep_pins=op.keep_pins)


def refinements_to_dto(operators: tuple[Refinement, ...]) -> tuple[AnyRefinementOperator, ...]:
    return tuple(refinement_to_dto(op) for op in operators)
