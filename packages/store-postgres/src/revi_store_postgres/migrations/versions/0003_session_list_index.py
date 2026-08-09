"""Index the session list's filter column.

``GET /v1/sessions`` answers "which sessions does this tenant own?", and
that predicate had no index: every list read was a sequential scan of every
session in the deployment, growing with total traffic rather than with the
asking tenant's own history.

Additive and reversible — one btree index, no column and no backfill. The
other half of the list (turn count, last activity, first question) is read
through the existing ``ix_revi_trace_investigations_session_id``, so no
denormalized title or last-activity column is introduced: those would be a
second copy of facts the investigations already carry, and the copies would
drift the first time a turn was written without touching the session row.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_revi_session_sessions_tenant",
        "sessions",
        ["tenant"],
        schema="revi_session",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_revi_session_sessions_tenant", table_name="sessions", schema="revi_session"
    )
