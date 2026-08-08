"""Deterministic in-memory fakes for every investigation application port
(``revi_investigation.application.ports``) plus analytical-repository test
doubles (a canned-frame stub and a call-counting spy).

All fakes are dict-backed and synchronous under the hood; the async port
surface is preserved exactly so services cannot tell them from adapters.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from revi_investigation.application.ports import (
    LlmUsage,
    RegisteredReferent,
    TextLlmRequest,
    TraceRecord,
    TurnEvent,
)
from revi_investigation.domain.records import (
    Investigation,
    RefinementEdge,
    Session,
    SessionLineage,
)
from revi_kernel.capabilities import AnalyticalRepository, RepositoryCapabilities
from revi_kernel.cohort import CohortDefinition, CohortMaterialization, CohortRef
from revi_kernel.errors import SourceUnavailableError
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import EvidenceProbe, probe_hash
from revi_kernel.refs import ReferentId
from revi_kernel.watermark import DataWatermark


class FakeSessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def save(self, session: Session) -> None:
        self.sessions[session.id] = session


class FakeReferentRegistryStore:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], RegisteredReferent] = {}

    async def register(self, entries: tuple[RegisteredReferent, ...]) -> None:
        for entry in entries:
            self.entries[(entry.session_id, entry.referent.value)] = entry

    async def resolve(self, session_id: str, referent: ReferentId) -> RegisteredReferent | None:
        return self.entries.get((session_id, referent.value))

    async def update(self, entry: RegisteredReferent) -> None:
        self.entries[(entry.session_id, entry.referent.value)] = entry

    async def list_for_session(self, session_id: str) -> tuple[RegisteredReferent, ...]:
        return tuple(
            entry for (sid, _), entry in self.entries.items() if sid == session_id
        )


class FakeInvestigationStore:
    def __init__(self, sessions: FakeSessionStore | None = None) -> None:
        self.investigations: dict[str, Investigation] = {}
        self.edges: list[RefinementEdge] = []
        self._sessions = sessions

    async def save(self, investigation: Investigation, edge: RefinementEdge | None) -> None:
        self.investigations[investigation.id] = investigation
        if edge is not None:
            self.edges.append(edge)

    async def get(self, investigation_id: str) -> Investigation | None:
        return self.investigations.get(investigation_id)

    async def lineage(self, session_id: str) -> SessionLineage | None:
        if self._sessions is None:
            return None
        session = self._sessions.sessions.get(session_id)
        if session is None:
            return None
        investigations = tuple(
            inv for inv in self.investigations.values() if inv.session_id == session_id
        )
        return SessionLineage(
            session=session, investigations=investigations, edges=tuple(self.edges)
        )


class FakeTraceStore:
    def __init__(self) -> None:
        self.records: dict[str, TraceRecord] = {}

    async def save(self, record: TraceRecord) -> None:
        self.records[record.trace_id] = record

    async def get(self, trace_id: str) -> TraceRecord | None:
        return self.records.get(trace_id)

    async def for_investigation(self, investigation_id: str) -> tuple[TraceRecord, ...]:
        return tuple(
            record
            for record in self.records.values()
            if record.investigation_id == investigation_id
        )


class FakeFrameStore:
    def __init__(self) -> None:
        self.frames: dict[str, EvidenceFrame] = {}

    async def save(self, key: str, frame: EvidenceFrame) -> None:
        self.frames[key] = frame

    async def get(self, key: str) -> EvidenceFrame | None:
        return self.frames.get(key)


class FakeCohortStore:
    def __init__(self) -> None:
        self.cohorts: dict[str, tuple[CohortRef, str, str]] = {}

    async def save(self, cohort: CohortRef, *, tenant: str, session_id: str) -> None:
        self.cohorts[cohort.id] = (cohort, tenant, session_id)

    async def get(self, cohort_id: str) -> CohortRef | None:
        entry = self.cohorts.get(cohort_id)
        return entry[0] if entry is not None else None

    async def expired(self, now: datetime) -> tuple[CohortRef, ...]:
        out: list[CohortRef] = []
        for cohort, _, _ in self.cohorts.values():
            pinned = cohort.pinned
            if pinned is not None and pinned.created_at + timedelta(
                seconds=pinned.ttl_seconds
            ) <= now:
                out.append(cohort)
        return tuple(out)


class FakeEvidenceCache:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], EvidenceFrame] = {}
        self.hits = 0
        self.misses = 0

    async def get(
        self, probe_hash_value: str, watermark_id: str, pack_snapshot_id: str
    ) -> EvidenceFrame | None:
        frame = self.entries.get((probe_hash_value, watermark_id, pack_snapshot_id))
        if frame is None:
            self.misses += 1
        else:
            self.hits += 1
        return frame

    async def put(
        self,
        probe_hash_value: str,
        watermark_id: str,
        pack_snapshot_id: str,
        frame: EvidenceFrame,
    ) -> None:
        self.entries[(probe_hash_value, watermark_id, pack_snapshot_id)] = frame


class FakeTurnEventBus:
    def __init__(self) -> None:
        self.events: list[TurnEvent] = []

    async def publish(self, event: TurnEvent) -> None:
        self.events.append(event)

    def kinds(self) -> tuple[str, ...]:
        return tuple(event.kind for event in self.events)


# ---------------------------------------------------------------------------
# analytical repository doubles (the kernel port)

_DEFAULT_CAPABILITIES = RepositoryCapabilities(
    as_of_reads=True,
    cohort_semijoin=True,
    max_cohort_size=100_000,
    having_pushdown=True,
    server_side_top_n=True,
)


@dataclass
class StubAnalyticalRepository:
    """Serves canned frames keyed by probe hash; raises on unknown probes."""

    watermarks: tuple[DataWatermark, ...]
    frames: dict[str, EvidenceFrame] = field(default_factory=dict)
    repository_capabilities: RepositoryCapabilities = _DEFAULT_CAPABILITIES

    def add_frame(self, probe: EvidenceProbe, frame: EvidenceFrame) -> None:
        self.frames[probe_hash(probe)] = frame

    def capabilities(self) -> RepositoryCapabilities:
        return self.repository_capabilities

    async def list_watermarks(self) -> tuple[DataWatermark, ...]:
        return self.watermarks

    async def execute(self, probe: EvidenceProbe, *, watermark: DataWatermark) -> EvidenceFrame:
        digest = probe_hash(probe)
        if digest not in self.frames:
            raise SourceUnavailableError(
                f"stub repository has no canned frame for probe {digest[:12]}"
            )
        return self.frames[digest]

    async def materialize_cohort(
        self, definition: CohortDefinition, *, watermark: DataWatermark
    ) -> CohortMaterialization:
        raise SourceUnavailableError("stub repository does not materialize cohorts")


class SpyAnalyticalRepository:
    """Wraps any repository, recording every ``execute`` call — the
    zero-probe and cache-hit assertions ride on this."""

    def __init__(self, inner: AnalyticalRepository) -> None:
        self._inner = inner
        self.executed_probes: list[EvidenceProbe] = []
        self.materialized: list[CohortDefinition] = []

    @property
    def execute_count(self) -> int:
        return len(self.executed_probes)

    def capabilities(self) -> RepositoryCapabilities:
        return self._inner.capabilities()

    async def list_watermarks(self) -> tuple[DataWatermark, ...]:
        return await self._inner.list_watermarks()

    async def execute(self, probe: EvidenceProbe, *, watermark: DataWatermark) -> EvidenceFrame:
        self.executed_probes.append(probe)
        return await self._inner.execute(probe, watermark=watermark)

    async def materialize_cohort(
        self, definition: CohortDefinition, *, watermark: DataWatermark
    ) -> CohortMaterialization:
        self.materialized.append(definition)
        return await self._inner.materialize_cohort(definition, watermark=watermark)


class FakeLanguageModelStream:
    """Helper async iterator for canned narrative text."""

    def __init__(self, chunks: tuple[str, ...]) -> None:
        self._chunks = list(chunks)

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str]:
        for chunk in self._chunks:
            yield chunk


__all__ = [
    "FakeCohortStore",
    "FakeEvidenceCache",
    "FakeFrameStore",
    "FakeInvestigationStore",
    "FakeLanguageModelStream",
    "FakeReferentRegistryStore",
    "FakeSessionStore",
    "FakeTraceStore",
    "FakeTurnEventBus",
    "SpyAnalyticalRepository",
    "StubAnalyticalRepository",
]


def make_usage(model: str = "mock", schema_retries: int = 0) -> LlmUsage:
    return LlmUsage(
        model=model,
        cost_usd=Decimal("0"),
        input_tokens=1,
        output_tokens=1,
        schema_retries=schema_retries,
        duration_ms=1,
    )


def make_text_request(prompt: str) -> TextLlmRequest:
    return TextLlmRequest(
        template_id="test", template_version="v1", rendered_prompt=prompt
    )
