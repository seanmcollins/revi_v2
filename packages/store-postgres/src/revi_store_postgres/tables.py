"""SQLAlchemy Core tables in capability-named Postgres schemas.

One schema per capability (design §15, structurally): ``revi_session``,
``revi_trace``, ``revi_cohort``, ``revi_pack`` (reserved for the pack
registry milestone — no tables yet), ``revi_cache``. Typed columns cover
what queries filter and index on; full-fidelity domain objects live in
JSONB columns holding :mod:`revi_store_postgres.serde` envelopes.

The Alembic migration under ``migrations/`` is the authoritative DDL; this
metadata mirrors it exactly (index names included) for query construction.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

SESSION_SCHEMA = "revi_session"
TRACE_SCHEMA = "revi_trace"
COHORT_SCHEMA = "revi_cohort"
PACK_SCHEMA = "revi_pack"
CACHE_SCHEMA = "revi_cache"

ALL_SCHEMAS: tuple[str, ...] = (
    SESSION_SCHEMA,
    TRACE_SCHEMA,
    COHORT_SCHEMA,
    PACK_SCHEMA,
    CACHE_SCHEMA,
)

metadata = sa.MetaData()

sessions = sa.Table(
    "sessions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant", sa.Text, nullable=False),
    sa.Column("pack_id", sa.Text, nullable=False),
    sa.Column("pack_version", sa.Text, nullable=False),
    sa.Column("epochs", JSONB, nullable=False),
    # Nullable so a row written before session settings existed reads back
    # as "the defaults" rather than as a decode failure.
    sa.Column("settings", JSONB, nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    # The session list's only filter (migration 0003): without it, "which
    # sessions does this tenant own?" scans every session in the deployment.
    sa.Index("ix_revi_session_sessions_tenant", "tenant"),
    schema=SESSION_SCHEMA,
)

referents = sa.Table(
    "referents",
    metadata,
    sa.Column("session_id", sa.Text, primary_key=True),
    sa.Column("referent_id", sa.Text, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    schema=SESSION_SCHEMA,
)

investigations = sa.Table(
    "investigations",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("session_id", sa.Text, nullable=False),
    sa.Column("parent_id", sa.Text, nullable=True),
    sa.Column("turn_id", sa.Text, nullable=False),
    sa.Column("turn_class", sa.Text, nullable=False),
    sa.Column("question", sa.Text, nullable=True),
    sa.Column("spec", JSONB, nullable=False),
    sa.Column("plan_hash", sa.Text, nullable=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("findings", JSONB, nullable=False),
    sa.Column("frame_refs", JSONB, nullable=False),
    sa.Column("warnings", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Index("ix_revi_trace_investigations_session_id", "session_id"),
    schema=TRACE_SCHEMA,
)

edges = sa.Table(
    "edges",
    metadata,
    sa.Column("parent_id", sa.Text, nullable=False),
    sa.Column("child_id", sa.Text, primary_key=True),
    sa.Column("turn_id", sa.Text, nullable=False),
    sa.Column("operators", JSONB, nullable=False),
    sa.Index("ix_revi_trace_edges_parent_id", "parent_id"),
    schema=TRACE_SCHEMA,
)

traces = sa.Table(
    "traces",
    metadata,
    sa.Column("trace_id", sa.Text, primary_key=True),
    sa.Column("session_id", sa.Text, nullable=False),
    sa.Column("investigation_id", sa.Text, nullable=False),
    sa.Column("turn_id", sa.Text, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Index("ix_revi_trace_traces_session_id", "session_id"),
    sa.Index("ix_revi_trace_traces_investigation_id", "investigation_id"),
    schema=TRACE_SCHEMA,
)

frames = sa.Table(
    "frames",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("frame", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    schema=TRACE_SCHEMA,
)

cohorts = sa.Table(
    "cohorts",
    metadata,
    sa.Column("cohort_id", sa.Text, primary_key=True),
    sa.Column("tenant", sa.Text, nullable=False),
    sa.Column("session_id", sa.Text, nullable=False),
    sa.Column("definition", JSONB, nullable=False),
    sa.Column("origin", JSONB, nullable=False),
    sa.Column("size", sa.BigInteger, nullable=False),
    sa.Column("pinned", JSONB, nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_revi_cohort_cohorts_session_id", "session_id"),
    sa.Index("ix_revi_cohort_cohorts_expires_at", "expires_at"),
    schema=COHORT_SCHEMA,
)

evidence = sa.Table(
    "evidence",
    metadata,
    sa.Column("probe_hash", sa.Text, primary_key=True),
    sa.Column("watermark_id", sa.Text, primary_key=True),
    sa.Column("pack_snapshot_id", sa.Text, primary_key=True),
    sa.Column("frame", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    schema=CACHE_SCHEMA,
)
