"""The closed refinement-operator set and the carryover laws (design §7.4, §7.7).

The LLM (or a UI gesture) may emit only these twelve operators. Everything
downstream of validated operators is deterministic.

Carryover laws implemented here:
1. Refinements inherit the full parent spec and change only what they name
   (``apply_refinement`` is pure; locality is property-tested).
2. Topic shift starts a fresh context (a classification concern — not here).
3. Cohort narrowing composes; widening is always explicit
   (``RemoveFilter`` / ``ResetContext``).
4. Contradictions are detected *before* execution → ``CONTEXT_CONFLICT``.
5. Session pins persist until explicitly cleared
   (``ResetContext(keep_pins=True)`` default; pins conjoin in effective scope).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Union

from revi_investigation.domain.context import AnalysisSpec
from revi_kernel.cohort import CohortRef
from revi_kernel.errors import ContextConflictError, ReferentNotFoundError
from revi_kernel.filters import (
    EMPTY_SCOPE,
    And,
    FilterExpr,
    Predicate,
    PredicateOp,
    and_merge,
    iter_predicates,
)
from revi_kernel.refs import DateBasisRef, DimensionRef, Grain, MetricRef, ReferentId
from revi_kernel.scope import (
    AbsoluteRange,
    ComparisonKind,
    RelativeRange,
    TimeWindow,
    derive_comparison,
    resolve_window,
)

# --- the closed operator union ---------------------------------------------


@dataclass(frozen=True, slots=True)
class SetDimensions:
    dimensions: tuple[DimensionRef, ...]


@dataclass(frozen=True, slots=True)
class AddFilter:
    predicate: Predicate


@dataclass(frozen=True, slots=True)
class RemoveFilter:
    """Remove scope clauses by dimension (all predicates on it)."""

    dimension: DimensionRef


@dataclass(frozen=True, slots=True)
class SetWindow:
    window: RelativeRange | AbsoluteRange
    basis: DateBasisRef | None = None  # None = keep current basis


@dataclass(frozen=True, slots=True)
class SetComparison:
    kind: ComparisonKind | None  # None clears the comparison
    custom: AbsoluteRange | None = None


@dataclass(frozen=True, slots=True)
class SetGrain:
    grain: Grain
    # triggers full plan re-validation downstream (design §7.4)


@dataclass(frozen=True, slots=True)
class DrillInto:
    target: ReferentId


@dataclass(frozen=True, slots=True)
class Pivot:
    measures: tuple[MetricRef, ...]


@dataclass(frozen=True, slots=True)
class Explain:
    target: ReferentId
    # dispatches to the decompose operator at planning time; no context edit


@dataclass(frozen=True, slots=True)
class RankBy:
    by: MetricRef
    descending: bool = True


@dataclass(frozen=True, slots=True)
class Expand:
    limit: int

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("Expand.limit must be positive")


@dataclass(frozen=True, slots=True)
class ResetContext:
    keep_pins: bool = True


Refinement = Union[  # noqa: UP007
    SetDimensions,
    AddFilter,
    RemoveFilter,
    SetWindow,
    SetComparison,
    SetGrain,
    DrillInto,
    Pivot,
    Explain,
    RankBy,
    Expand,
    ResetContext,
]

# Operators that touch no warehouse data when applied alone (design §7.9):
KERNEL_ONLY_OPERATORS = (RankBy, Expand)
PLAN_ONLY_OPERATORS = (Explain,)


# --- conflict detection (carryover law 4) -----------------------------------


def _values_of(p: Predicate) -> frozenset[object]:
    return frozenset(p.values)


def _certainly_empty(a: Predicate, b: Predicate) -> bool:
    """True when a AND b can never match (same dimension, disjoint demands)."""
    if a.dimension != b.dimension:
        return False
    pos = {PredicateOp.EQ, PredicateOp.IN}
    neg = {PredicateOp.NEQ, PredicateOp.NOT_IN}
    if a.op in pos and b.op in pos:
        return not (_values_of(a) & _values_of(b))
    if a.op in pos and b.op in neg:
        return _values_of(a) <= _values_of(b)
    if a.op in neg and b.op in pos:
        return _values_of(b) <= _values_of(a)
    if a.op is PredicateOp.IS_NULL and b.op in pos:
        return True
    return b.op is PredicateOp.IS_NULL and a.op in pos


def detect_conflict(spec: AnalysisSpec, new: Predicate) -> str | None:
    """Return a human-readable conflict description, or None.

    Checks the new predicate against: existing scope clauses, pinned
    predicates, and the active cohort's *definition* scope (the
    "exclude Medicaid inside a Medicaid-only cohort" case).
    """
    existing = list(iter_predicates(spec.context.effective_scope()))
    cohort = spec.context.cohort
    if cohort is not None:
        existing.extend(iter_predicates(cohort.definition.scope))
    for clause in existing:
        if _certainly_empty(clause, new):
            return (
                f"'{new.dimension.id} {new.op.value} {list(new.values)!r}' contradicts the "
                f"active constraint "
                f"'{clause.dimension.id} {clause.op.value} {list(clause.values)!r}'"
            )
    return None


# --- application (carryover law 1: pure, local) -----------------------------

CohortResolver = Callable[[ReferentId], CohortRef | None]


def apply_refinement(
    spec: AnalysisSpec,
    op: Refinement,
    *,
    turn_id: str,
    resolve_cohort: CohortResolver | None = None,
) -> AnalysisSpec:
    """Apply one operator to the parent spec. Pure; changes only the named
    component. Raises ``ContextConflictError`` (before any execution) on
    contradictions and ``ReferentNotFoundError`` on unresolvable targets.
    """
    ctx = spec.context

    if isinstance(op, SetDimensions):
        return replace(spec, dimensions=op.dimensions)

    if isinstance(op, AddFilter):
        predicate = replace(op.predicate, origin_turn=turn_id)
        conflict = detect_conflict(spec, predicate)
        if conflict is not None:
            raise ContextConflictError(
                f"refinement contradicts active context: {conflict}",
                details={"turn": turn_id, "conflict": conflict},
            )
        new_scope = and_merge(ctx.scope, predicate)
        return spec.with_context(replace(ctx, scope=new_scope))

    if isinstance(op, RemoveFilter):
        kept = tuple(
            clause
            for clause in _flat_clauses(ctx.scope)
            if not (isinstance(clause, Predicate) and clause.dimension == op.dimension)
        )
        return spec.with_context(replace(ctx, scope=And(kept) if len(kept) != 1 else kept[0]))

    if isinstance(op, SetWindow):
        basis = op.basis or ctx.window.basis
        if isinstance(op.window, AbsoluteRange):
            new_window = TimeWindow(
                basis=basis, range=op.window, requested=None, calendar=ctx.window.calendar
            )
        else:
            anchor = ctx.watermark.loaded_at.date()
            new_window = resolve_window(op.window, anchor, basis=basis, calendar=ctx.window.calendar)
        new_ctx = replace(ctx, window=new_window)
        # comparison derives from the window; re-derive to stay consistent
        if ctx.comparison is not None and ctx.comparison.kind is not ComparisonKind.CUSTOM:
            new_ctx = replace(new_ctx, comparison=derive_comparison(new_window, ctx.comparison.kind))
        return spec.with_context(new_ctx)

    if isinstance(op, SetComparison):
        if op.kind is None:
            return spec.with_context(replace(ctx, comparison=None))
        comparison = derive_comparison(ctx.window, op.kind, custom=op.custom)
        return spec.with_context(replace(ctx, comparison=comparison))

    if isinstance(op, SetGrain):
        return spec.with_context(replace(ctx, grain=op.grain))

    if isinstance(op, DrillInto):
        if resolve_cohort is None:
            raise ReferentNotFoundError(
                f"no referent registry available to resolve {op.target.value}",
                details={"referent": op.target.value},
            )
        cohort = resolve_cohort(op.target)
        if cohort is None:
            raise ReferentNotFoundError(
                f"referent {op.target.value} does not resolve to a drillable cohort",
                details={"referent": op.target.value},
            )
        # narrowing composes (law 3): drilling within a drill intersects via
        # the new cohort's definition, which the resolver builds against the
        # current context (incl. any active cohort)
        return spec.with_context(replace(ctx, cohort=cohort))

    if isinstance(op, Pivot):
        return replace(spec, measures=op.measures)

    if isinstance(op, Explain):
        return spec  # plan-level dispatch; context untouched

    if isinstance(op, RankBy):
        return replace(spec, rank_by=op.by, rank_descending=op.descending)

    if isinstance(op, Expand):
        return replace(spec, limit=op.limit)

    if isinstance(op, ResetContext):
        pins = ctx.pins if op.keep_pins else ()
        return replace(
            spec,
            context=replace(ctx, scope=EMPTY_SCOPE, cohort=None, comparison=None, pins=pins),
            dimensions=(),
            rank_by=None,
            rank_descending=True,
            limit=None,
        )

    raise TypeError(f"unknown refinement operator: {type(op).__name__}")  # pragma: no cover


def _flat_clauses(expr: FilterExpr) -> tuple[FilterExpr, ...]:
    return expr.clauses if isinstance(expr, And) else (expr,)


def apply_refinements(
    spec: AnalysisSpec,
    ops: tuple[Refinement, ...],
    *,
    turn_id: str,
    resolve_cohort: CohortResolver | None = None,
) -> AnalysisSpec:
    """Apply a turn's operators in order (a turn may carry several — e.g.
    §10.3 T3: DrillInto + Pivot + SetDimensions)."""
    for op in ops:
        spec = apply_refinement(spec, op, turn_id=turn_id, resolve_cohort=resolve_cohort)
    return spec
