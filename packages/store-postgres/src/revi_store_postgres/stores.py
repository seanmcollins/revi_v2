"""Postgres implementations of the application-state ports.

Each store satisfies the corresponding Protocol in
``revi_investigation.application.ports``. All SQL is sync SQLAlchemy Core
executed off the event loop via ``asyncio.to_thread`` (rationale in
:mod:`revi_store_postgres.engine`). Full-fidelity domain objects are stored
as :mod:`revi_store_postgres.serde` envelopes in JSONB; typed columns exist
for what queries filter on.

Datetime convention: typed ``timestamptz`` columns (``created_at``,
``expires_at``) round-trip aware datetimes exactly (equality is
instant-based); naive datetimes are interpreted as UTC and come back
UTC-aware. Datetimes inside JSONB envelopes round-trip losslessly, tzinfo
included.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from revi_investigation.application.ports import (
    EMPTY_SESSION_TITLE,
    RegisteredReferent,
    SessionPage,
    SessionSummary,
    TraceRecord,
)
from revi_investigation.domain.context import AnalysisSpec, PackVersionRef
from revi_investigation.domain.records import (
    Finding,
    Investigation,
    InvestigationStatus,
    RefinementEdge,
    Session,
    SessionLineage,
)
from revi_investigation.domain.refinements import Refinement
from revi_investigation.domain.settings import DEFAULT_SESSION_SETTINGS, SessionSettings
from revi_investigation.domain.turns import TurnClass
from revi_kernel.cohort import CohortDefinition, CohortMaterialization, CohortRef
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import ReferentId
from revi_kernel.watermark import WatermarkEpoch
from revi_store_postgres import tables as t
from revi_store_postgres.serde import from_stored, from_stored_as, to_stored


def _utc(value: datetime) -> datetime:
    """Interpret naive datetimes as UTC (the typed-column convention)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --- sessions ---------------------------------------------------------------


def _load_session(conn: Connection, session_id: str) -> Session | None:
    row = (
        conn.execute(sa.select(t.sessions).where(t.sessions.c.id == session_id))
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    epochs = cast(tuple[WatermarkEpoch, ...], from_stored(row["epochs"]))
    # NULL is a row written before session settings existed: it reads back
    # as the defaults, which is exactly what that session ran under.
    stored_settings = row["settings"]
    settings = (
        cast(SessionSettings, from_stored(stored_settings))
        if stored_settings is not None
        else DEFAULT_SESSION_SETTINGS
    )
    return Session(
        id=row["id"],
        tenant=row["tenant"],
        pack_version=PackVersionRef(pack_id=row["pack_id"], version=row["pack_version"]),
        epochs=epochs,
        created_at=row["created_at"],
        settings=settings,
    )


def session_page_query(tenant: str, limit: int) -> sa.Select[Any]:
    """One tenant's session rows with their derived title, activity and
    turn count — newest activity first.

    Two LATERAL subqueries correlated to each session row, so the
    investigations table is reached only through
    ``ix_revi_trace_investigations_session_id`` for the tenant's own
    sessions; an uncorrelated ``GROUP BY session_id`` would aggregate every
    row in the table to answer a question about a handful of them.

    Reading ``revi_trace.investigations`` from the session store is the same
    cross-schema join :meth:`PostgresInvestigationStore.lineage` already
    makes — both schemas are the investigation capability's own application
    state (design §15). The alternative, a title and a last-activity column
    on the session row, would be a second copy of facts the turns already
    carry, and the copies would drift the first time a turn was written
    without touching the session.

    Module-level rather than inline so the SQL shape can be compiled and
    asserted without a database (``tests/test_session_list_sql.py``); the
    behavior itself is covered by the shared store contract under
    ``-m postgres``.
    """
    turn_stats = (
        sa.select(
            sa.func.count().label("turn_count"),
            sa.func.max(t.investigations.c.created_at).label("last_activity"),
        )
        .where(t.investigations.c.session_id == t.sessions.c.id)
        .lateral("turn_stats")
    )
    first_turn = (
        sa.select(t.investigations.c.question.label("question"))
        .where(
            t.investigations.c.session_id == t.sessions.c.id,
            t.investigations.c.question.is_not(None),
            t.investigations.c.question != "",
        )
        .order_by(t.investigations.c.created_at, t.investigations.c.id)
        .limit(1)
        .lateral("first_turn")
    )
    # A session with no turns has no activity but still has a row: it was
    # opened, and the list is how an analyst finds it again.
    activity = sa.func.coalesce(turn_stats.c.last_activity, t.sessions.c.created_at)
    return (
        sa.select(
            t.sessions.c.id,
            t.sessions.c.created_at,
            turn_stats.c.turn_count,
            activity.label("last_activity"),
            first_turn.c.question,
        )
        .select_from(
            t.sessions.join(turn_stats, sa.true()).join(first_turn, sa.true(), isouter=True)
        )
        # Archived sessions are dismissed, not deleted: they keep their
        # lineage and stay fetchable by id, and they leave the rail.
        .where(t.sessions.c.tenant == tenant, t.sessions.c.archived_at.is_(None))
        .order_by(activity.desc(), t.sessions.c.id)
        .limit(limit)
    )


def session_total_query(tenant: str) -> sa.Select[Any]:
    """Every ACTIVE session the tenant owns — the page's ``total``.

    Counted over the same predicate the page selects on: a total that
    included archived sessions would tell a client its truncated page was
    missing rows that are not in the list at all.
    """
    return (
        sa.select(sa.func.count())
        .select_from(t.sessions)
        .where(t.sessions.c.tenant == tenant, t.sessions.c.archived_at.is_(None))
    )


class PostgresSessionStore:
    """``SessionStore`` adapter."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def get(self, session_id: str) -> Session | None:
        return await asyncio.to_thread(self._get, session_id)

    async def save(self, session: Session) -> None:
        await asyncio.to_thread(self._save, session)

    async def list_for_tenant(self, tenant: str, *, limit: int) -> SessionPage:
        return await asyncio.to_thread(self._list_for_tenant, tenant, limit)

    async def archive(self, session_id: str, *, archived: bool = True) -> None:
        """Dismiss (or restore) a session without deleting anything.

        A session owns investigations, traces, frames and cohorts that
        other reads resolve through it, so the row stays and the list stops
        showing it. Idempotent: archiving an archived session is a no-op
        that keeps the ORIGINAL timestamp, because the second call did not
        dismiss anything.
        """
        await asyncio.to_thread(self._archive, session_id, archived)

    def _archive(self, session_id: str, archived: bool) -> None:
        stmt = (
            sa.update(t.sessions)
            .where(t.sessions.c.id == session_id)
            .values(archived_at=datetime.now(UTC) if archived else None)
        )
        if archived:
            stmt = stmt.where(t.sessions.c.archived_at.is_(None))
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, session_id: str) -> Session | None:
        with self._engine.connect() as conn:
            return _load_session(conn, session_id)

    def _list_for_tenant(self, tenant: str, limit: int) -> SessionPage:
        with self._engine.connect() as conn:
            rows = conn.execute(session_page_query(tenant, limit)).mappings().all()
            total = conn.execute(session_total_query(tenant)).scalar_one()
        return SessionPage(
            sessions=tuple(
                SessionSummary(
                    session_id=row["id"],
                    title=row["question"] or EMPTY_SESSION_TITLE,
                    created_at=row["created_at"],
                    last_activity=row["last_activity"],
                    turn_count=row["turn_count"],
                )
                for row in rows
            ),
            total=total,
        )

    def _save(self, session: Session) -> None:
        values = {
            "id": session.id,
            "tenant": session.tenant,
            "pack_id": session.pack_version.pack_id,
            "pack_version": session.pack_version.version,
            "epochs": to_stored(session.epochs),
            "settings": to_stored(session.settings),
            "created_at": _utc(session.created_at),
        }
        stmt = pg_insert(t.sessions).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.sessions.c.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)


# --- referent registry ------------------------------------------------------


class PostgresReferentRegistryStore:
    """``ReferentRegistryStore`` adapter (design §7.6)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def register(self, entries: tuple[RegisteredReferent, ...]) -> None:
        await asyncio.to_thread(self._register, entries)

    async def resolve(self, session_id: str, referent: ReferentId) -> RegisteredReferent | None:
        return await asyncio.to_thread(self._resolve, session_id, referent)

    async def update(self, entry: RegisteredReferent) -> None:
        await asyncio.to_thread(self._register, (entry,))

    async def list_for_session(self, session_id: str) -> tuple[RegisteredReferent, ...]:
        return await asyncio.to_thread(self._list_for_session, session_id)

    def _register(self, entries: tuple[RegisteredReferent, ...]) -> None:
        if not entries:
            return
        with self._engine.begin() as conn:
            for entry in entries:
                values = {
                    "session_id": entry.session_id,
                    "referent_id": entry.referent.value,
                    "kind": entry.referent.kind.value,
                    "payload": to_stored(entry),
                }
                stmt = pg_insert(t.referents).values(values)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[t.referents.c.session_id, t.referents.c.referent_id],
                    set_={"kind": values["kind"], "payload": values["payload"]},
                )
                conn.execute(stmt)

    def _resolve(self, session_id: str, referent: ReferentId) -> RegisteredReferent | None:
        stmt = sa.select(t.referents.c.payload).where(
            t.referents.c.session_id == session_id,
            t.referents.c.referent_id == referent.value,
        )
        with self._engine.connect() as conn:
            payload = conn.execute(stmt).scalar_one_or_none()
        if payload is None:
            return None
        entry = from_stored_as(RegisteredReferent, payload)
        return entry if entry.referent == referent else None

    def _list_for_session(self, session_id: str) -> tuple[RegisteredReferent, ...]:
        stmt = (
            sa.select(t.referents.c.payload)
            .where(t.referents.c.session_id == session_id)
            .order_by(t.referents.c.referent_id)
        )
        with self._engine.connect() as conn:
            payloads = conn.execute(stmt).scalars().all()
        return tuple(from_stored_as(RegisteredReferent, payload) for payload in payloads)


# --- investigations ---------------------------------------------------------


def _row_to_investigation(row: sa.RowMapping) -> Investigation:
    return Investigation(
        id=row["id"],
        session_id=row["session_id"],
        parent_id=row["parent_id"],
        turn_id=row["turn_id"],
        turn_class=TurnClass(row["turn_class"]),
        question=row["question"],
        spec=from_stored_as(AnalysisSpec, row["spec"]),
        plan_hash=row["plan_hash"],
        status=InvestigationStatus(row["status"]),
        findings=cast(tuple[Finding, ...], from_stored(row["findings"])),
        created_at=row["created_at"],
        frame_refs=tuple(row["frame_refs"]),
        warnings=tuple(row["warnings"]),
        narrative=row["narrative"],
    )


class PostgresInvestigationStore:
    """``InvestigationStore`` adapter: the session DAG of immutable
    investigations plus typed refinement edges."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def save(self, investigation: Investigation, edge: RefinementEdge | None) -> None:
        await asyncio.to_thread(self._save, investigation, edge)

    async def get(self, investigation_id: str) -> Investigation | None:
        return await asyncio.to_thread(self._get, investigation_id)

    async def lineage(self, session_id: str) -> SessionLineage | None:
        return await asyncio.to_thread(self._lineage, session_id)

    def _save(self, investigation: Investigation, edge: RefinementEdge | None) -> None:
        values = {
            "id": investigation.id,
            "session_id": investigation.session_id,
            "parent_id": investigation.parent_id,
            "turn_id": investigation.turn_id,
            "turn_class": investigation.turn_class.value,
            "question": investigation.question,
            "spec": to_stored(investigation.spec),
            "plan_hash": investigation.plan_hash,
            "status": investigation.status.value,
            "findings": to_stored(investigation.findings),
            "frame_refs": list(investigation.frame_refs),
            "warnings": list(investigation.warnings),
            "narrative": investigation.narrative,
            "created_at": _utc(investigation.created_at),
        }
        inv_stmt = pg_insert(t.investigations).values(values)
        inv_stmt = inv_stmt.on_conflict_do_update(
            index_elements=[t.investigations.c.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
        with self._engine.begin() as conn:
            conn.execute(inv_stmt)
            if edge is not None:
                edge_values = {
                    "parent_id": edge.parent_id,
                    "child_id": edge.child_id,
                    "turn_id": edge.turn_id,
                    "operators": to_stored(edge.operators),
                }
                edge_stmt = pg_insert(t.edges).values(edge_values)
                edge_stmt = edge_stmt.on_conflict_do_update(
                    index_elements=[t.edges.c.child_id],
                    set_={k: v for k, v in edge_values.items() if k != "child_id"},
                )
                conn.execute(edge_stmt)

    def _get(self, investigation_id: str) -> Investigation | None:
        stmt = sa.select(t.investigations).where(t.investigations.c.id == investigation_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else _row_to_investigation(row)

    def _lineage(self, session_id: str) -> SessionLineage | None:
        with self._engine.connect() as conn:
            session = _load_session(conn, session_id)
            if session is None:
                return None
            inv_rows = (
                conn.execute(
                    sa.select(t.investigations)
                    .where(t.investigations.c.session_id == session_id)
                    .order_by(t.investigations.c.created_at, t.investigations.c.id)
                )
                .mappings()
                .all()
            )
            session_investigations = sa.select(t.investigations.c.id).where(
                t.investigations.c.session_id == session_id
            )
            edge_rows = (
                conn.execute(
                    sa.select(t.edges)
                    .where(t.edges.c.child_id.in_(session_investigations))
                    .order_by(t.edges.c.child_id)
                )
                .mappings()
                .all()
            )
        return SessionLineage(
            session=session,
            investigations=tuple(_row_to_investigation(row) for row in inv_rows),
            edges=tuple(
                RefinementEdge(
                    parent_id=row["parent_id"],
                    child_id=row["child_id"],
                    turn_id=row["turn_id"],
                    operators=cast(tuple[Refinement, ...], from_stored(row["operators"])),
                )
                for row in edge_rows
            ),
        )


# --- traces -----------------------------------------------------------------


class PostgresTraceStore:
    """``TraceStore`` adapter (design §14)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def save(self, record: TraceRecord) -> None:
        await asyncio.to_thread(self._save, record)

    async def get(self, trace_id: str) -> TraceRecord | None:
        return await asyncio.to_thread(self._get, trace_id)

    async def for_investigation(self, investigation_id: str) -> tuple[TraceRecord, ...]:
        return await asyncio.to_thread(self._for_investigation, investigation_id)

    def _save(self, record: TraceRecord) -> None:
        values = {
            "trace_id": record.trace_id,
            "session_id": record.session_id,
            "investigation_id": record.investigation_id,
            "turn_id": record.turn_id,
            "created_at": _utc(record.created_at),
            "payload": to_stored(dict(record.payload)),
        }
        stmt = pg_insert(t.traces).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.traces.c.trace_id],
            set_={k: v for k, v in values.items() if k != "trace_id"},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    @staticmethod
    def _row_to_record(row: sa.RowMapping) -> TraceRecord:
        return TraceRecord(
            trace_id=row["trace_id"],
            session_id=row["session_id"],
            investigation_id=row["investigation_id"],
            turn_id=row["turn_id"],
            created_at=row["created_at"],
            payload=cast(dict[str, Any], from_stored(row["payload"])),
        )

    def _get(self, trace_id: str) -> TraceRecord | None:
        stmt = sa.select(t.traces).where(t.traces.c.trace_id == trace_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else self._row_to_record(row)

    def _for_investigation(self, investigation_id: str) -> tuple[TraceRecord, ...]:
        stmt = (
            sa.select(t.traces)
            .where(t.traces.c.investigation_id == investigation_id)
            .order_by(t.traces.c.created_at, t.traces.c.trace_id)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(self._row_to_record(row) for row in rows)


# --- frames -----------------------------------------------------------------


class PostgresFrameStore:
    """``FrameStore`` adapter: persisted evidence frames by trace-scoped key."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def save(self, key: str, frame: EvidenceFrame) -> None:
        await asyncio.to_thread(self._save, key, frame)

    async def get(self, key: str) -> EvidenceFrame | None:
        return await asyncio.to_thread(self._get, key)

    def _save(self, key: str, frame: EvidenceFrame) -> None:
        values = {
            "key": key,
            "frame": to_stored(frame),
            "created_at": datetime.now(UTC),
        }
        stmt = pg_insert(t.frames).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.frames.c.key],
            set_={"frame": values["frame"], "created_at": values["created_at"]},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    def _get(self, key: str) -> EvidenceFrame | None:
        stmt = sa.select(t.frames.c.frame).where(t.frames.c.key == key)
        with self._engine.connect() as conn:
            payload = conn.execute(stmt).scalar_one_or_none()
        return None if payload is None else from_stored_as(EvidenceFrame, payload)


# --- cohorts ----------------------------------------------------------------


class PostgresCohortStore:
    """``CohortStore`` adapter — cohort *metadata* only (design §7.5); the
    entity-id sets live in the analytical repository's cohort store.

    ``expires_at`` = pinned materialization ``created_at + ttl_seconds``
    (indexed); definitions without a pinned materialization never expire.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def save(self, cohort: CohortRef, *, tenant: str, session_id: str) -> None:
        await asyncio.to_thread(self._save, cohort, tenant, session_id)

    async def get(self, cohort_id: str) -> CohortRef | None:
        return await asyncio.to_thread(self._get, cohort_id)

    async def expired(self, now: datetime) -> tuple[CohortRef, ...]:
        return await asyncio.to_thread(self._expired, now)

    def _save(self, cohort: CohortRef, tenant: str, session_id: str) -> None:
        expires_at: datetime | None = None
        if cohort.pinned is not None:
            expires_at = _utc(cohort.pinned.created_at) + timedelta(seconds=cohort.pinned.ttl_seconds)
        values = {
            "cohort_id": cohort.id,
            "tenant": tenant,
            "session_id": session_id,
            "definition": to_stored(cohort.definition),
            "origin": to_stored(cohort.origin),
            "size": cohort.size,
            "pinned": None if cohort.pinned is None else to_stored(cohort.pinned),
            "expires_at": expires_at,
        }
        stmt = pg_insert(t.cohorts).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[t.cohorts.c.cohort_id],
            set_={k: v for k, v in values.items() if k != "cohort_id"},
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)

    @staticmethod
    def _row_to_cohort(row: sa.RowMapping) -> CohortRef:
        pinned = row["pinned"]
        return CohortRef(
            id=row["cohort_id"],
            definition=from_stored_as(CohortDefinition, row["definition"]),
            origin=from_stored_as(ReferentId, row["origin"]),
            size=row["size"],
            pinned=None if pinned is None else from_stored_as(CohortMaterialization, pinned),
        )

    def _get(self, cohort_id: str) -> CohortRef | None:
        stmt = sa.select(t.cohorts).where(t.cohorts.c.cohort_id == cohort_id)
        with self._engine.connect() as conn:
            row = conn.execute(stmt).mappings().one_or_none()
        return None if row is None else self._row_to_cohort(row)

    def _expired(self, now: datetime) -> tuple[CohortRef, ...]:
        stmt = (
            sa.select(t.cohorts)
            .where(t.cohorts.c.expires_at.is_not(None), t.cohorts.c.expires_at <= _utc(now))
            .order_by(t.cohorts.c.cohort_id)
        )
        with self._engine.connect() as conn:
            rows = conn.execute(stmt).mappings().all()
        return tuple(self._row_to_cohort(row) for row in rows)


# --- evidence cache ---------------------------------------------------------


class PostgresEvidenceCache:
    """``EvidenceCache`` adapter, keyed on (probe hash, watermark, pack
    snapshot) — design §7.9. ``put`` is idempotent: first write wins
    (``ON CONFLICT DO NOTHING``); identical keys must mean identical frames."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def get(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str
    ) -> EvidenceFrame | None:
        return await asyncio.to_thread(self._get, probe_hash, watermark_id, pack_snapshot_id)

    async def put(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None:
        await asyncio.to_thread(self._put, probe_hash, watermark_id, pack_snapshot_id, frame)

    def _get(self, probe_hash: str, watermark_id: str, pack_snapshot_id: str) -> EvidenceFrame | None:
        stmt = sa.select(t.evidence.c.frame).where(
            t.evidence.c.probe_hash == probe_hash,
            t.evidence.c.watermark_id == watermark_id,
            t.evidence.c.pack_snapshot_id == pack_snapshot_id,
        )
        with self._engine.connect() as conn:
            payload = conn.execute(stmt).scalar_one_or_none()
        return None if payload is None else from_stored_as(EvidenceFrame, payload)

    def _put(
        self, probe_hash: str, watermark_id: str, pack_snapshot_id: str, frame: EvidenceFrame
    ) -> None:
        stmt = pg_insert(t.evidence).values(
            probe_hash=probe_hash,
            watermark_id=watermark_id,
            pack_snapshot_id=pack_snapshot_id,
            frame=to_stored(frame),
            created_at=datetime.now(UTC),
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[
                t.evidence.c.probe_hash,
                t.evidence.c.watermark_id,
                t.evidence.c.pack_snapshot_id,
            ]
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)


# --- turn receipts (idempotency) --------------------------------------------


class PostgresTurnReceiptStore:
    """Executed-turn responses, keyed by the caller's idempotency key.

    The API honored idempotency keys from a process-local dict: correct
    within one process's lifetime and silently wrong outside it. A restart
    between a client's POST and its retry — or a second worker behind a
    load balancer — turned "return the stored response" into a second
    EXECUTION of the same turn: fresh model spend, a second investigation
    in the session DAG, and two different answers to one request.

    The stored value is the serialized ``TurnResponse``, so a replay
    returns the ORIGINAL payload. First write wins
    (``ON CONFLICT DO NOTHING``), which is what makes two concurrent
    retries of the same key converge on one answer instead of racing to
    overwrite each other.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    async def get(
        self, tenant: str, session_id: str, key: str
    ) -> dict[str, Any] | None:
        return await asyncio.to_thread(self._get, tenant, session_id, key)

    async def put(
        self, tenant: str, session_id: str, key: str, response: dict[str, Any]
    ) -> None:
        await asyncio.to_thread(self._put, tenant, session_id, key, response)

    def _get(self, tenant: str, session_id: str, key: str) -> dict[str, Any] | None:
        stmt = sa.select(t.turn_receipts.c.response).where(
            t.turn_receipts.c.tenant == tenant,
            t.turn_receipts.c.session_id == session_id,
            t.turn_receipts.c.idempotency_key == key,
        )
        with self._engine.connect() as conn:
            payload = conn.execute(stmt).scalar_one_or_none()
        return cast("dict[str, Any] | None", payload)

    def _put(
        self, tenant: str, session_id: str, key: str, response: dict[str, Any]
    ) -> None:
        stmt = pg_insert(t.turn_receipts).values(
            tenant=tenant,
            session_id=session_id,
            idempotency_key=key,
            response=response,
            created_at=datetime.now(UTC),
        )
        stmt = stmt.on_conflict_do_nothing(
            index_elements=[
                t.turn_receipts.c.tenant,
                t.turn_receipts.c.session_id,
                t.turn_receipts.c.idempotency_key,
            ]
        )
        with self._engine.begin() as conn:
            conn.execute(stmt)
