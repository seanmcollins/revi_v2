"""Application-layer ports (Protocols) for the investigation capability.

Infrastructure adapters implement these; application services depend only
on the Protocols. In-memory fakes for every port live in ``revi_testing``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from revi_investigation.domain.records import (
    Finding,
    Investigation,
    RefinementEdge,
    Session,
    SessionLineage,
)
from revi_kernel.cohort import CohortDefinition, CohortRef
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import ReferentId
from revi_kernel.watermark import DataWatermark

# --- language model ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StructuredLlmRequest:
    """A stateless, schema-constrained call. The prompt is already
    payload-guarded and PHI-masked by the caller; the schema is a JSON
    Schema derived from a closed Pydantic response model."""

    template_id: str
    template_version: str
    rendered_prompt: str
    schema: Mapping[str, Any]
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class TextLlmRequest:
    """Streaming free-text call — narrative composition only."""

    template_id: str
    template_version: str
    rendered_prompt: str
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class LlmUsage:
    model: str
    cost_usd: Decimal
    input_tokens: int
    output_tokens: int
    schema_retries: int
    duration_ms: int


@dataclass(frozen=True, slots=True)
class StructuredLlmResult:
    output: Mapping[str, Any] | None  # None = the model failed to satisfy the schema
    usage: LlmUsage


class LanguageModelPort(Protocol):
    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult: ...

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]: ...

    async def last_usage(self) -> LlmUsage | None:
        """Usage of the most recent stream_text call (structured calls carry
        usage inline)."""
        ...


# --- persistence ------------------------------------------------------------


class SessionStore(Protocol):
    async def get(self, session_id: str) -> Session | None: ...

    async def save(self, session: Session) -> None: ...


@dataclass(frozen=True, slots=True)
class RegisteredReferent:
    """What a referent id stands for, resolvable across turns (design §7.6)."""

    referent: ReferentId
    session_id: str
    investigation_id: str
    label: str
    cohort_definition: CohortDefinition | None = None  # drillable referents
    cohort: CohortRef | None = None  # pinned when first drilled
    finding: Finding | None = None
    dimension_value: tuple[str, str] | None = None  # (dimension id, value)


class ReferentRegistryStore(Protocol):
    async def register(self, entries: tuple[RegisteredReferent, ...]) -> None: ...

    async def resolve(self, session_id: str, referent: ReferentId) -> RegisteredReferent | None: ...

    async def update(self, entry: RegisteredReferent) -> None: ...

    async def list_for_session(self, session_id: str) -> tuple[RegisteredReferent, ...]: ...


class InvestigationStore(Protocol):
    async def save(self, investigation: Investigation, edge: RefinementEdge | None) -> None: ...

    async def get(self, investigation_id: str) -> Investigation | None: ...

    async def lineage(self, session_id: str) -> SessionLineage | None: ...


@dataclass(frozen=True, slots=True)
class TraceRecord:
    """One turn's full observability record (design §14). ``payload`` is a
    serializable mapping assembled by the engine; typed fields cover what
    queries need."""

    trace_id: str
    session_id: str
    investigation_id: str
    turn_id: str
    created_at: datetime
    payload: Mapping[str, Any]


class TraceStore(Protocol):
    async def save(self, record: TraceRecord) -> None: ...

    async def get(self, trace_id: str) -> TraceRecord | None: ...

    async def for_investigation(self, investigation_id: str) -> tuple[TraceRecord, ...]: ...


class FrameStore(Protocol):
    """Persisted evidence frames, addressed by trace-scoped keys."""

    async def save(self, key: str, frame: EvidenceFrame) -> None: ...

    async def get(self, key: str) -> EvidenceFrame | None: ...


class CohortStore(Protocol):
    """Cohort *metadata* (definitions, origins, TTLs). The entity-id sets
    live in the analytical repository's cohort store."""

    async def save(self, cohort: CohortRef, *, tenant: str, session_id: str) -> None: ...

    async def get(self, cohort_id: str) -> CohortRef | None: ...

    async def expired(self, now: datetime) -> tuple[CohortRef, ...]: ...


class EvidenceCache(Protocol):
    """Keyed on (probe hash, watermark, pack snapshot) — design §7.9."""

    async def get(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str
    ) -> EvidenceFrame | None: ...

    async def put(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None: ...


# --- turn progress events ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class TurnEvent:
    """A typed progress event, bridged to SSE by the API layer.

    ``kind`` ∈ {stage, context_header, finding, chart_spec, narrative_delta,
    clarification, warning, turn_complete, error}; ``payload`` is a small
    serializable mapping shaped per kind.
    """

    kind: str
    turn_id: str
    payload: Mapping[str, Any]


class TurnEventBus(Protocol):
    async def publish(self, event: TurnEvent) -> None: ...


# --- detected anomalies (the portfolio surface) -----------------------------


@dataclass(frozen=True, slots=True)
class AnomalyRecord:
    """One detected anomaly as persisted at a warehouse watermark.

    Mirrors the warehouse ``detected_anomalies`` shape: an external
    detection system writes these; the platform only reads and ranks them.
    ``dimensions`` is (dimension id, value) pairs — together with the
    window they form the drillable handle an anomaly card carries, so the
    UI can start an ordinary investigation turn from it.
    """

    anomaly_id: str
    detected_at: datetime
    category: str
    title: str
    description: str
    metric_id: str
    dimensions: tuple[tuple[str, str], ...]
    window_start: date
    window_end: date
    impact_cents: int
    severity: str
    confidence: str
    status: str
    evidence: Mapping[str, Any]


class AnomalySource(Protocol):
    """Read-only access to detected anomalies as-of a watermark. A
    prebuilt external detection system implements this port; locally the
    DuckDB connector serves the generator's planted scenarios."""

    async def list_anomalies(self, watermark: DataWatermark) -> tuple[AnomalyRecord, ...]: ...
