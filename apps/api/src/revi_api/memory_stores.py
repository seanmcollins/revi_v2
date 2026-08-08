"""In-memory application-state stores for the API's fallback wiring.

Used when ``REVI_DATABASE_URL`` is unset or unreachable (logged loudly):
dict-backed, process-local, demo-grade. Kept here rather than importing
``revi_testing`` so the production app never depends on the test harness
(and the dependency graph stays acyclic — the test harness depends on
this package for its adapters).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from revi_investigation.application.ports import (
    RegisteredReferent,
    TraceRecord,
)
from revi_investigation.domain.records import (
    Investigation,
    RefinementEdge,
    Session,
    SessionLineage,
)
from revi_kernel.cohort import CohortRef
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import ReferentId


class MemorySessionStore:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    async def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def save(self, session: Session) -> None:
        self.sessions[session.id] = session


class MemoryReferentRegistryStore:
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
        return tuple(entry for (sid, _), entry in self.entries.items() if sid == session_id)


class MemoryInvestigationStore:
    def __init__(self, sessions: MemorySessionStore) -> None:
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
        session = self._sessions.sessions.get(session_id)
        if session is None:
            return None
        investigations = tuple(
            inv for inv in self.investigations.values() if inv.session_id == session_id
        )
        ids = {inv.id for inv in investigations}
        edges = tuple(edge for edge in self.edges if edge.child_id in ids)
        return SessionLineage(session=session, investigations=investigations, edges=edges)


class MemoryTraceStore:
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


class MemoryFrameStore:
    def __init__(self) -> None:
        self.frames: dict[str, EvidenceFrame] = {}

    async def save(self, key: str, frame: EvidenceFrame) -> None:
        self.frames[key] = frame

    async def get(self, key: str) -> EvidenceFrame | None:
        return self.frames.get(key)


class MemoryCohortStore:
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


class MemoryEvidenceCache:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str, str], EvidenceFrame] = {}

    async def get(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str
    ) -> EvidenceFrame | None:
        return self.entries.get((probe_hash, watermark_id, pack_snapshot_id))

    async def put(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None:
        self.entries[(probe_hash, watermark_id, pack_snapshot_id)] = frame
