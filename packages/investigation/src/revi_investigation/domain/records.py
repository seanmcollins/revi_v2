"""Session and investigation records (design §7.1): a session is a DAG of
immutable investigations linked by typed refinement edges."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from revi_investigation.domain.context import AnalysisSpec, PackVersionRef
from revi_investigation.domain.refinements import Refinement
from revi_investigation.domain.turns import TurnClass
from revi_kernel.filters import Scalar
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef, ReferentId
from revi_kernel.watermark import DataWatermark, WatermarkEpoch


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    tenant: str
    pack_version: PackVersionRef
    epochs: tuple[WatermarkEpoch, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.epochs:
            raise ValueError("Session requires at least one watermark epoch")

    @property
    def watermark(self) -> DataWatermark:
        return self.epochs[-1].watermark

    def with_new_epoch(self, epoch: WatermarkEpoch) -> Session:
        if epoch.index != len(self.epochs):
            raise ValueError(f"epoch index must be {len(self.epochs)}, got {epoch.index}")
        return Session(
            id=self.id,
            tenant=self.tenant,
            pack_version=self.pack_version,
            epochs=(*self.epochs, epoch),
            created_at=self.created_at,
        )


class InvestigationStatus(StrEnum):
    COMPLETE = "complete"
    CLARIFICATION_REQUIRED = "clarification_required"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class Finding:
    """A certified, referent-addressable result the narrative may cite."""

    referent: ReferentId
    title: str
    statement: str
    metric_refs: tuple[MetricRef, ...]
    values: tuple[tuple[str, Scalar], ...]  # named values backing the statement
    grade: EvidenceGrade
    impact_cents: int | None = None
    confidence: str = "high"
    suggested_refinements: tuple[str, ...] = ()

    def value(self, name: str) -> Scalar:
        for key, val in self.values:
            if key == name:
                return val
        raise KeyError(name)


@dataclass(frozen=True, slots=True)
class RefinementEdge:
    parent_id: str
    child_id: str
    turn_id: str
    operators: tuple[Refinement, ...]


@dataclass(frozen=True, slots=True)
class Investigation:
    id: str
    session_id: str
    parent_id: str | None
    turn_id: str
    turn_class: TurnClass
    question: str | None
    spec: AnalysisSpec
    plan_hash: str | None
    status: InvestigationStatus
    findings: tuple[Finding, ...]
    created_at: datetime
    frame_refs: tuple[str, ...] = ()  # trace-store keys of persisted frames
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SessionLineage:
    session: Session
    investigations: tuple[Investigation, ...]
    edges: tuple[RefinementEdge, ...] = field(default=())

    def children_of(self, investigation_id: str) -> tuple[str, ...]:
        return tuple(e.child_id for e in self.edges if e.parent_id == investigation_id)
