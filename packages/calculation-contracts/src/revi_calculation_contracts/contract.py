"""Metric contracts — what a number *means* (design §2.3, §5.2, §5.3).

A contract fixes formula, entity grain, date basis, denominator, exclusions,
and sign. The analyst freely chooses *where to point it* (dimensions,
population, window); the planner validates that scope against
``scope_dimensions`` and ``allowed_date_bases``.

``MeasureExpr`` is deliberately tiny — enough for every base-pack metric.
Adapters compile it to SQL; the kernel never computes it row-by-row.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Union

from revi_kernel.filters import FilterExpr
from revi_kernel.probes import canonicalize
from revi_kernel.refs import DateBasisRef, DimensionRef, EntityGrain, FieldRef


class MetricKind(StrEnum):
    FLOW = "flow"  # aggregations over a window (AggregationProbe)
    SNAPSHOT = "snapshot"  # state as-of a date (SnapshotProbe)


class SignConvention(StrEnum):
    HIGHER_IS_GOOD = "higher_is_good"
    HIGHER_IS_BAD = "higher_is_bad"
    NEUTRAL = "neutral"


class MetricUnit(StrEnum):
    MONEY_CENTS = "money_cents"
    RATIO = "ratio"
    DAYS = "days"
    COUNT = "count"


@dataclass(frozen=True, slots=True)
class Sum:
    field: FieldRef


@dataclass(frozen=True, slots=True)
class Count:
    pass


@dataclass(frozen=True, slots=True)
class CountDistinct:
    field: FieldRef


@dataclass(frozen=True, slots=True)
class Filtered:
    """An inner measure restricted by a contract-internal filter."""

    inner: Sum | Count | CountDistinct
    where: FilterExpr


MeasureExpr = Union[Sum, Count, CountDistinct, Filtered]  # noqa: UP007


@dataclass(frozen=True, slots=True)
class MetricContract:
    id: str
    version: int
    kind: MetricKind
    entity_grain: EntityGrain
    numerator: MeasureExpr
    denominator: MeasureExpr | None  # None ⇒ additive measure
    primary_date_basis: DateBasisRef
    allowed_date_bases: tuple[DateBasisRef, ...]
    scope_dimensions: tuple[DimensionRef, ...]
    sign: SignConvention
    unit: MetricUnit
    exclusions: FilterExpr | None = None
    description: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("MetricContract.id must be non-empty")
        if self.version < 1:
            raise ValueError("MetricContract.version must be >= 1")
        if self.primary_date_basis not in self.allowed_date_bases:
            raise ValueError(
                f"{self.id}: primary date basis {self.primary_date_basis.id!r} "
                "must be in allowed_date_bases"
            )
        if self.denominator is None and self.unit is MetricUnit.RATIO:
            raise ValueError(f"{self.id}: RATIO metrics require a denominator")

    @property
    def is_ratio(self) -> bool:
        return self.denominator is not None

    def allows_dimension(self, dimension: DimensionRef) -> bool:
        return dimension in self.scope_dimensions

    def allows_date_basis(self, basis: DateBasisRef) -> bool:
        return basis in self.allowed_date_bases

    @property
    def fingerprint(self) -> str:
        """Semantic fingerprint (design §5.2): stable hash over meaning.

        ``description`` is excluded — prose is not meaning. Any change to a
        fingerprinted field requires a new contract version; the pack loader
        enforces that two versions of one id never share a fingerprint and
        that a changed fingerprint bumps the version.
        """
        payload = {
            "id": self.id,
            "kind": self.kind.value,
            "entity_grain": self.entity_grain.value,
            "numerator": canonicalize(self.numerator),
            "denominator": canonicalize(self.denominator) if self.denominator else None,
            "primary_date_basis": self.primary_date_basis.id,
            "allowed_date_bases": [b.id for b in self.allowed_date_bases],
            "scope_dimensions": sorted(d.id for d in self.scope_dimensions),
            "sign": self.sign.value,
            "unit": self.unit.value,
            "exclusions": canonicalize(self.exclusions) if self.exclusions else None,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# Component-column naming convention shared between adapters (which produce
# component sums) and the kernel (which computes ratio-of-sums per cell).
def numerator_column(metric_id: str) -> str:
    return f"{metric_id}__num"


def denominator_column(metric_id: str) -> str:
    return f"{metric_id}__den"
