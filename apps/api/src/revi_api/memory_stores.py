"""In-memory application-state stores for the API's fallback wiring.

Used when ``REVI_DATABASE_URL`` is unset or unreachable (logged loudly):
dict-backed, process-local, demo-grade. Kept here rather than importing
``revi_testing`` so the production app never depends on the test harness
(and the dependency graph stays acyclic — the test harness depends on
this package for its adapters).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from revi_investigation.application.ports import (
    EMPTY_SESSION_TITLE,
    RegisteredReferent,
    RoundsLead,
    RoundsLoad,
    RoundsPin,
    RoundsPinResult,
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
        #: Session id → when it was dismissed. Soft, like the Postgres
        #: column: an archived session keeps its lineage and stays
        #: fetchable by id, it simply leaves the list.
        self.archived: dict[str, datetime] = {}
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

    async def archive(self, session_id: str, *, archived: bool = True) -> None:
        if not archived:
            self.archived.pop(session_id, None)
        elif session_id not in self.archived:
            # Idempotent, and the FIRST dismissal keeps its timestamp.
            self.archived[session_id] = datetime.now(UTC)

    async def list_for_tenant(self, tenant: str, *, limit: int) -> SessionPage:
        owned = [
            s
            for s in self.sessions.values()
            if s.tenant == tenant and s.id not in self.archived
        ]
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


class MemoryTurnReceiptStore:
    """Process-local idempotency receipts (the fallback wiring's).

    Deliberately the same shape as the Postgres store rather than a dict
    the service reaches into: the demo wiring and the real one then differ
    in durability alone, and the service has one code path.
    """

    def __init__(self) -> None:
        self.receipts: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def get(self, tenant: str, session_id: str, key: str) -> dict[str, Any] | None:
        return self.receipts.get((tenant, session_id, key))

    async def put(
        self, tenant: str, session_id: str, key: str, response: dict[str, Any]
    ) -> None:
        # First write wins, like the Postgres ON CONFLICT DO NOTHING.
        self.receipts.setdefault((tenant, session_id, key), response)


# --- Rounds (the proactive surface) -----------------------------------------
#
# Ordering is the thing these four have to get exactly right, because the
# whole surface is load-over-load: "the prior load" is decided by the
# WATERMARK'S OWN ``loaded_at``, never by id order. ``wm_001`` sorting
# before ``wm_002`` is a coincidence of this warehouse's naming, and a store
# that relied on it would silently diff the wrong pair the first time a
# deployment used hashes or dates for watermark ids.


class MemoryRoundsPinStore:
    """``RoundsPinStore``: pinned typed specs, tenant-scoped."""

    def __init__(self) -> None:
        self.pins: dict[str, RoundsPin] = {}

    async def save(self, pin: RoundsPin) -> None:
        self.pins[pin.id] = pin

    async def get(self, pin_id: str) -> RoundsPin | None:
        return self.pins.get(pin_id)

    async def list_for_tenant(
        self, tenant: str, *, include_archived: bool = False
    ) -> tuple[RoundsPin, ...]:
        rows = [
            pin
            for pin in self.pins.values()
            if pin.tenant == tenant and (include_archived or pin.archived_at is None)
        ]
        rows.sort(key=lambda pin: (pin.created_at, pin.id))
        return tuple(rows)

    async def archive(self, pin_id: str) -> None:
        pin = self.pins.get(pin_id)
        # Idempotent, and the FIRST un-pin keeps its timestamp — the same
        # rule the session archive follows.
        if pin is not None and pin.archived_at is None:
            self.pins[pin_id] = replace(pin, archived_at=datetime.now(UTC))

    async def tenants_with_pins(self) -> tuple[str, ...]:
        return tuple(
            sorted({pin.tenant for pin in self.pins.values() if pin.archived_at is None})
        )


class MemoryRoundsPinResultStore:
    """``RoundsPinResultStore``: one evaluated tile per (pin, load)."""

    def __init__(self) -> None:
        self.results: dict[tuple[str, str], RoundsPinResult] = {}

    async def put(self, result: RoundsPinResult) -> None:
        self.results[(result.pin_id, result.watermark_id)] = result

    async def get(self, pin_id: str, watermark_id: str) -> RoundsPinResult | None:
        return self.results.get((pin_id, watermark_id))

    async def history(self, pin_id: str, *, limit: int = 12) -> tuple[RoundsPinResult, ...]:
        rows = [r for (pid, _), r in self.results.items() if pid == pin_id]
        rows.sort(key=lambda r: (r.watermark_loaded_at, r.watermark_id), reverse=True)
        return tuple(rows[:limit])


class MemoryRoundsLoadStore:
    """``RoundsLoadStore``: the detection-feed census per (tenant, load)."""

    def __init__(self) -> None:
        self.loads: dict[tuple[str, str], RoundsLoad] = {}

    async def put(self, load: RoundsLoad) -> None:
        self.loads[(load.tenant, load.watermark_id)] = load

    async def get(self, tenant: str, watermark_id: str) -> RoundsLoad | None:
        return self.loads.get((tenant, watermark_id))

    async def list_for_tenant(self, tenant: str, *, limit: int = 12) -> tuple[RoundsLoad, ...]:
        rows = [load for (t, _), load in self.loads.items() if t == tenant]
        rows.sort(key=lambda load: (load.watermark_loaded_at, load.watermark_id), reverse=True)
        return tuple(rows[:limit])


class MemoryRoundsLeadStore:
    """``RoundsLeadStore``: lead lifecycle, keyed by the detector's own id."""

    def __init__(self) -> None:
        self.leads: dict[tuple[str, str], RoundsLead] = {}

    async def put(self, lead: RoundsLead) -> None:
        self.leads[(lead.tenant, lead.anomaly_id)] = lead

    async def get(self, tenant: str, anomaly_id: str) -> RoundsLead | None:
        return self.leads.get((tenant, anomaly_id))

    async def list_for_tenant(self, tenant: str) -> tuple[RoundsLead, ...]:
        rows = [lead for (t, _), lead in self.leads.items() if t == tenant]
        rows.sort(key=lambda lead: lead.anomaly_id)
        return tuple(rows)
