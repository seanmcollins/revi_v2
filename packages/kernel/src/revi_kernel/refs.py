"""Reference types that resolve against the semantic catalog and pack.

Refs are opaque identifiers — the kernel never knows what a dimension or
metric *means*; the catalog and pack do. ``EntityGrain`` and ``TimeBucket``
are true closed sets (design §6.1: the two orthogonal grain axes).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class DimensionRef:
    id: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("DimensionRef.id must be non-empty")


@dataclass(frozen=True, slots=True)
class MetricRef:
    id: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("MetricRef.id must be non-empty")


@dataclass(frozen=True, slots=True)
class FieldRef:
    """A raw catalog field (row-evidence columns, uncertified discovery)."""

    id: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("FieldRef.id must be non-empty")


@dataclass(frozen=True, slots=True)
class DateBasisRef:
    """A date basis (design §6.1). Standard bases below; tenants may add more."""

    id: str

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("DateBasisRef.id must be non-empty")


SERVICE = DateBasisRef("service")
POST = DateBasisRef("post")
SUBMISSION = DateBasisRef("submission")
REMIT = DateBasisRef("remit")
DISCHARGE = DateBasisRef("discharge")

STANDARD_DATE_BASES: tuple[DateBasisRef, ...] = (SERVICE, POST, SUBMISSION, REMIT, DISCHARGE)


class EntityGrain(StrEnum):
    """The fan-out axis: what one row *is* (design §6.1)."""

    CLAIM = "claim"
    LINE = "line"
    ENCOUNTER = "encounter"
    TRANSACTION = "transaction"
    REMIT = "remit"
    DENIAL = "denial"


class TimeBucket(StrEnum):
    """The bucketing axis: how time is grouped (design §6.1)."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class Grain:
    entity: EntityGrain
    time_bucket: TimeBucket | None = None


class ReferentKind(StrEnum):
    FINDING = "finding"
    COHORT = "cohort"
    CHART_SERIES = "chart_series"
    TABLE_ROW = "table_row"
    DIMENSION_VALUE = "dimension_value"


@dataclass(frozen=True, slots=True)
class ReferentId:
    """A stable, analyst-visible handle (F1, F2, …) — design §7.6."""

    value: str
    kind: ReferentKind

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("ReferentId.value must be non-empty")
