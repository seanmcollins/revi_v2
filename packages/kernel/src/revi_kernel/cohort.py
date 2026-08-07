"""Cohorts: intensional definitions and extensional pinned sets (design §7.5).

The duality is deliberate and must not be collapsed:

- **Within a session**, drill-downs use the **pinned** materialization so
  child numbers reconcile with what the analyst was shown.
- **Across sessions**, re-running an investigation re-evaluates the
  **definition** against fresh data.

Materializations are sensitive data (a pinned set of claim ids is itself
row-level evidence): tenant-scoped, access-controlled, TTL-bound.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

from revi_kernel.refs import EntityGrain, ReferentId
from revi_kernel.watermark import DataWatermark

if TYPE_CHECKING:
    from revi_kernel.filters import FilterExpr
    from revi_kernel.scope import TimeWindow


@dataclass(frozen=True, slots=True)
class CohortDefinition:
    """Intensional: the rule that selects the entity set."""

    entity: EntityGrain
    scope: FilterExpr
    window: TimeWindow | None = None


@dataclass(frozen=True, slots=True)
class CohortMaterialization:
    """Extensional: the pinned entity ids at a watermark.

    ``entity_ids_ref`` is a repository-scoped handle (e.g. a cohort-store
    table name) — entity ids themselves never travel through domain objects.
    """

    cohort_id: str
    watermark: DataWatermark
    entity_ids_ref: str
    size: int
    created_at: datetime
    ttl_seconds: int

    def __post_init__(self) -> None:
        if self.size < 0:
            raise ValueError("CohortMaterialization.size must be >= 0")
        if self.ttl_seconds <= 0:
            raise ValueError("CohortMaterialization.ttl_seconds must be positive")


@dataclass(frozen=True, slots=True)
class CohortRef:
    id: str
    definition: CohortDefinition
    origin: ReferentId
    size: int
    pinned: CohortMaterialization | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("CohortRef.id must be non-empty")
        if self.size < 0:
            raise ValueError("CohortRef.size must be >= 0")
        if self.pinned is not None and self.pinned.cohort_id != self.id:
            raise ValueError(
                f"pinned materialization belongs to cohort {self.pinned.cohort_id!r}, not {self.id!r}"
            )
