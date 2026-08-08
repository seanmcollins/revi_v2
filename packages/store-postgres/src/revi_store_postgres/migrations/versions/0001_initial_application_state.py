"""Initial application-state schemas and tables.

Capability-named schemas (design §15, structurally): ``revi_session``,
``revi_trace``, ``revi_cohort``, ``revi_pack`` (reserved, no tables yet),
``revi_cache`` — plus the session/trace/cohort/cache tables and their
indexes. Mirrors ``revi_store_postgres.tables`` exactly.

Revision ID: 0001
Revises:
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

_SCHEMAS = ("revi_session", "revi_trace", "revi_cohort", "revi_pack", "revi_cache")


def upgrade() -> None:
    for schema in _SCHEMAS:
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))

    op.create_table(
        "sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant", sa.Text(), nullable=False),
        sa.Column("pack_id", sa.Text(), nullable=False),
        sa.Column("pack_version", sa.Text(), nullable=False),
        sa.Column("epochs", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="revi_session",
    )

    op.create_table(
        "referents",
        sa.Column("session_id", sa.Text(), primary_key=True),
        sa.Column("referent_id", sa.Text(), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        schema="revi_session",
    )

    op.create_table(
        "investigations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Text(), nullable=True),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("turn_class", sa.Text(), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("spec", JSONB(), nullable=False),
        sa.Column("plan_hash", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("findings", JSONB(), nullable=False),
        sa.Column("frame_refs", JSONB(), nullable=False),
        sa.Column("warnings", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="revi_trace",
    )
    op.create_index(
        "ix_revi_trace_investigations_session_id",
        "investigations",
        ["session_id"],
        schema="revi_trace",
    )

    op.create_table(
        "edges",
        sa.Column("parent_id", sa.Text(), nullable=False),
        sa.Column("child_id", sa.Text(), primary_key=True),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("operators", JSONB(), nullable=False),
        schema="revi_trace",
    )
    op.create_index("ix_revi_trace_edges_parent_id", "edges", ["parent_id"], schema="revi_trace")

    op.create_table(
        "traces",
        sa.Column("trace_id", sa.Text(), primary_key=True),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("investigation_id", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        schema="revi_trace",
    )
    op.create_index("ix_revi_trace_traces_session_id", "traces", ["session_id"], schema="revi_trace")
    op.create_index(
        "ix_revi_trace_traces_investigation_id",
        "traces",
        ["investigation_id"],
        schema="revi_trace",
    )

    op.create_table(
        "frames",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("frame", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="revi_trace",
    )

    op.create_table(
        "cohorts",
        sa.Column("cohort_id", sa.Text(), primary_key=True),
        sa.Column("tenant", sa.Text(), nullable=False),
        sa.Column("session_id", sa.Text(), nullable=False),
        sa.Column("definition", JSONB(), nullable=False),
        sa.Column("origin", JSONB(), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("pinned", JSONB(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="revi_cohort",
    )
    op.create_index(
        "ix_revi_cohort_cohorts_session_id", "cohorts", ["session_id"], schema="revi_cohort"
    )
    op.create_index(
        "ix_revi_cohort_cohorts_expires_at", "cohorts", ["expires_at"], schema="revi_cohort"
    )

    op.create_table(
        "evidence",
        sa.Column("probe_hash", sa.Text(), primary_key=True),
        sa.Column("watermark_id", sa.Text(), primary_key=True),
        sa.Column("pack_snapshot_id", sa.Text(), primary_key=True),
        sa.Column("frame", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="revi_cache",
    )


def downgrade() -> None:
    for schema in reversed(_SCHEMAS):
        op.execute(sa.text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
