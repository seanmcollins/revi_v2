"""The closed evidence-probe union (design §6.2) and canonical probe hashing.

Probes express retrieval shapes the semantic layer can fully verify;
transforms (calculation kernel) express analysis. ``order_by`` + ``limit``
live on the probe because server-side top-N over high-cardinality dimensions
is a pure win; per-group top-N and all other ranking are transform-layer.

``probe_hash`` is the evidence-cache key component: a stable SHA-256 over a
canonical serialization. Cache keys are ``(probe_hash, watermark, pack
version)`` (design §7.9). Volatile bookkeeping fields (materialization
timestamps/TTLs) are excluded so re-materializing an identical cohort does
not needlessly split the cache.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Union

from revi_kernel.filters import FilterExpr, PredicateOp, Scalar
from revi_kernel.refs import DateBasisRef, DimensionRef, FieldRef, Grain, MetricRef
from revi_kernel.scope import TimeWindow


@dataclass(frozen=True, slots=True)
class MeasurePredicate:
    """Post-aggregation filter (HAVING)."""

    measure: MetricRef
    op: PredicateOp
    values: tuple[Scalar, ...]

    def __post_init__(self) -> None:
        if self.op in (PredicateOp.IS_NULL, PredicateOp.CONTAINS):
            raise ValueError(f"{self.op.value} is not a valid measure predicate")


@dataclass(frozen=True, slots=True)
class Ordering:
    by: MetricRef | DimensionRef
    descending: bool = True


class SampleMethod(StrEnum):
    RESERVOIR = "reservoir"


@dataclass(frozen=True, slots=True)
class SamplePolicy:
    n: int
    method: SampleMethod = SampleMethod.RESERVOIR
    seed: int = 20260807

    def __post_init__(self) -> None:
        if self.n <= 0:
            raise ValueError("SamplePolicy.n must be positive")


@dataclass(frozen=True, slots=True)
class AggregationProbe:
    """Flow aggregation over a window."""

    measures: tuple[MetricRef, ...]
    dimensions: tuple[DimensionRef, ...]
    scope: FilterExpr
    window: TimeWindow
    grain: Grain
    having: tuple[MeasurePredicate, ...] = ()
    order_by: tuple[Ordering, ...] = ()
    limit: int | None = None

    def __post_init__(self) -> None:
        if not self.measures:
            raise ValueError("AggregationProbe requires at least one measure")
        if self.limit is not None and self.limit <= 0:
            raise ValueError("AggregationProbe.limit must be positive when set")


@dataclass(frozen=True, slots=True)
class SnapshotProbe:
    """State as-of a date: AR aging, open inventory, work-in-progress."""

    measures: tuple[MetricRef, ...]
    dimensions: tuple[DimensionRef, ...]
    scope: FilterExpr
    as_of: date
    grain: Grain
    aging_basis: DateBasisRef | None = None

    def __post_init__(self) -> None:
        if not self.measures:
            raise ValueError("SnapshotProbe requires at least one measure")


@dataclass(frozen=True, slots=True)
class RowEvidenceProbe:
    """Row-level examples. Authorization-gated, sampled, purpose recorded."""

    columns: tuple[FieldRef, ...]
    scope: FilterExpr
    sample: SamplePolicy
    purpose: str
    window: TimeWindow | None = None

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError("RowEvidenceProbe requires at least one column")
        if not self.purpose.strip():
            raise ValueError("RowEvidenceProbe.purpose must be stated (recorded in trace)")


EvidenceProbe = Union[AggregationProbe, SnapshotProbe, RowEvidenceProbe]  # noqa: UP007

# Fields excluded from canonical serialization: volatile bookkeeping that does
# not change what a probe *retrieves* at a given (watermark, pack version).
_CANONICAL_EXCLUDED_FIELDS = frozenset({"created_at", "ttl_seconds"})


def _canonical(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        payload: dict[str, object] = {"__type__": type(value).__name__}
        for f in fields(value):
            if f.name in _CANONICAL_EXCLUDED_FIELDS:
                continue
            payload[f.name] = _canonical(getattr(value, f.name))
        return payload
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return f"decimal:{value}"
    if isinstance(value, datetime):
        return f"datetime:{value.isoformat()}"
    if isinstance(value, date):
        return f"date:{value.isoformat()}"
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"cannot canonicalize {type(value).__name__}")


def canonical_json(probe: EvidenceProbe) -> str:
    """Stable canonical JSON for a probe (sorted keys, typed scalars)."""
    return json.dumps(_canonical(probe), sort_keys=True, separators=(",", ":"))


def probe_hash(probe: EvidenceProbe) -> str:
    """SHA-256 hex digest of the canonical serialization."""
    return hashlib.sha256(canonical_json(probe).encode("utf-8")).hexdigest()
