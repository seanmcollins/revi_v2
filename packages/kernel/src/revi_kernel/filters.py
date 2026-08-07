"""The closed filter algebra (design §6.1).

``FilterExpr = And | Or | Not | Predicate | InCohort``

Every predicate clause carries the id of the turn that introduced it
(design §7.2: scope clauses are tagged with their origin), so context
headers and ``RemoveFilter`` refinements can address them.

``InCohort`` is the single mechanism for inter-probe data flow in an
investigation DAG — downstream probes consume upstream results only through
cohort references, never through ad-hoc value splicing.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Union

from revi_kernel.refs import DimensionRef

if TYPE_CHECKING:
    from revi_kernel.cohort import CohortRef

Scalar = str | int | bool | Decimal | date | None


class PredicateOp(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    NOT_IN = "not_in"
    RANGE = "range"
    IS_NULL = "is_null"
    CONTAINS = "contains"


_ARITY: dict[PredicateOp, tuple[int, int | None]] = {
    PredicateOp.EQ: (1, 1),
    PredicateOp.NEQ: (1, 1),
    PredicateOp.IN: (1, None),
    PredicateOp.NOT_IN: (1, None),
    PredicateOp.RANGE: (2, 2),
    PredicateOp.IS_NULL: (0, 0),
    PredicateOp.CONTAINS: (1, 1),
}


@dataclass(frozen=True, slots=True)
class Predicate:
    dimension: DimensionRef
    op: PredicateOp
    values: tuple[Scalar, ...] = ()
    origin_turn: str | None = None

    def __post_init__(self) -> None:
        low, high = _ARITY[self.op]
        n = len(self.values)
        if n < low or (high is not None and n > high):
            raise ValueError(f"{self.op.value} expects {low}..{high} values, got {n}")
        if self.op is PredicateOp.CONTAINS and not isinstance(self.values[0], str):
            raise ValueError("CONTAINS requires a string value")


@dataclass(frozen=True, slots=True)
class InCohort:
    """Membership in a prior result's entity set (design §6.1, §7.5)."""

    cohort: CohortRef
    origin_turn: str | None = None


@dataclass(frozen=True, slots=True)
class And:
    clauses: tuple[FilterExpr, ...] = ()


@dataclass(frozen=True, slots=True)
class Or:
    clauses: tuple[FilterExpr, ...] = ()

    def __post_init__(self) -> None:
        if not self.clauses:
            raise ValueError("Or requires at least one clause (empty Or is vacuously false)")


@dataclass(frozen=True, slots=True)
class Not:
    clause: FilterExpr


FilterExpr = Union[And, Or, Not, Predicate, InCohort]  # noqa: UP007 - Union spelled out for clarity

EMPTY_SCOPE = And(())


def is_empty(expr: FilterExpr) -> bool:
    return isinstance(expr, And) and not expr.clauses


def and_merge(*exprs: FilterExpr) -> FilterExpr:
    """Conjoin expressions, flattening nested ``And`` and dropping empties."""
    flat: list[FilterExpr] = []
    for expr in exprs:
        if isinstance(expr, And):
            flat.extend(c for c in expr.clauses if not is_empty(c))
        elif not is_empty(expr):
            flat.append(expr)
    if len(flat) == 1:
        return flat[0]
    return And(tuple(flat))


def iter_predicates(expr: FilterExpr) -> Iterator[Predicate]:
    """Yield every predicate in the expression tree (any polarity)."""
    if isinstance(expr, Predicate):
        yield expr
    elif isinstance(expr, (And, Or)):
        for clause in expr.clauses:
            yield from iter_predicates(clause)
    elif isinstance(expr, Not):
        yield from iter_predicates(expr.clause)


def iter_cohorts(expr: FilterExpr) -> Iterator[InCohort]:
    """Yield every cohort membership clause in the expression tree."""
    if isinstance(expr, InCohort):
        yield expr
    elif isinstance(expr, (And, Or)):
        for clause in expr.clauses:
            yield from iter_cohorts(clause)
    elif isinstance(expr, Not):
        yield from iter_cohorts(expr.clause)


def dimensions_used(expr: FilterExpr) -> frozenset[DimensionRef]:
    return frozenset(p.dimension for p in iter_predicates(expr))
