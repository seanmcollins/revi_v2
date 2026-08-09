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
    EMPTY_SESSION_TITLE,
    RegisteredReferent,
    SessionPage,
    SessionSummary,
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
        # The other half of the list join. A session row carries no title
        # and no last-activity of its own: both come from the turns the
        # investigation store holds, exactly as the Postgres adapter reads
        # them out of ``revi_trace.investigations``. Bound at construction
        # by MemoryInvestigationStore so the pair cannot be assembled with
        # only one side wired.
        self._investigations: MemoryInvestigationStore | None = None

    def bind_investigations(self, investigations: MemoryInvestigationStore) -> None:
        self._investigations = investigations

    async def get(self, session_id: str) -> Session | None:
        return self.sessions.get(session_id)

    async def save(self, session: Session) -> None:
        self.sessions[session.id] = session

    async def list_for_tenant(self, tenant: str, *, limit: int) -> SessionPage:
        owned = [s for s in self.sessions.values() if s.tenant == tenant]
        rows = [self._summarize(session) for session in owned]
        # Stable two-pass sort: newest activity first, ties broken by id
        # ASCENDING (a single reverse=True sort would reverse the tiebreak
        # too, and the Postgres adapter orders the same way).
        rows.sort(key=lambda row: row.session_id)
        rows.sort(key=lambda row: row.last_activity, reverse=True)
        return SessionPage(sessions=tuple(rows[:limit]), total=len(owned))

    def _summarize(self, session: Session) -> SessionSummary:
        turns = sorted(
            (
                inv
                for inv in (
                    self._investigations.investigations.values()
                    if self._investigations is not None
                    else ()
                )
                if inv.session_id == session.id
            ),
            key=lambda inv: (inv.created_at, inv.id),
        )
        first_question = next(
            (inv.question for inv in turns if inv.question), None
        )
        return SessionSummary(
            session_id=session.id,
            title=first_question or EMPTY_SESSION_TITLE,
            created_at=session.created_at,
            last_activity=turns[-1].created_at if turns else session.created_at,
            turn_count=len(turns),
        )


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
        # Listing sessions joins the two stores; binding here means the
        # join is wired wherever the pair is built, never forgotten at one
        # of several call sites.
        sessions.bind_investigations(self)

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
        # First write wins, exactly like the Postgres cache's ON CONFLICT
        # DO NOTHING: the key already asserts (probe, watermark, pack), so
        # a second frame under the same key is either identical or wrong,
        # and overwriting would let the wrong one replace a cached answer
        # other turns already cited.
        self.entries.setdefault((probe_hash, watermark_id, pack_snapshot_id), frame)
