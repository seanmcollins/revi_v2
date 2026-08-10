"""Soft-archive sessions, and persist turn idempotency receipts.

Two session-lifecycle gaps, closed together because both are
session-schema state and both are additive.

``sessions.archived_at`` — the session list had no way to dismiss a
session, so it grew forever. NULL is an active session and a timestamp is
a dismissed one; nothing is deleted, because a session owns
investigations, traces, frames and cohorts that other reads still resolve
through it, and a hard delete would turn a tidy-up into dangling lineage.

``turn_receipts`` — the idempotency key was honored from a process-local
dict, so a restart or a second worker turned a retried POST into a second
EXECUTION of the same turn. The stored row carries the serialized
``TurnResponse``, so a replay returns the original payload rather than a
re-run that could differ from it.

Both are reversible and neither backfills: existing sessions read as
active (NULL), and a deployment with no receipts simply executes every
turn as it did before.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        schema="revi_session",
    )
    op.create_table(
        "turn_receipts",
        sa.Column("tenant", sa.Text, primary_key=True),
        sa.Column("session_id", sa.Text, primary_key=True),
        sa.Column("idempotency_key", sa.Text, primary_key=True),
        sa.Column("response", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="revi_session",
    )


def downgrade() -> None:
    op.drop_table("turn_receipts", schema="revi_session")
    op.drop_column("sessions", "archived_at", schema="revi_session")
