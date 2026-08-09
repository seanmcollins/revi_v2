"""Session-scoped settings on the session record.

One nullable JSONB column beside ``epochs``, holding the serde envelope
for :class:`revi_investigation.domain.settings.SessionSettings` — the
model tier, per-turn cost ceiling, narrative and evidence depth, and debug
flag a session runs under.

Nullable on purpose, and never backfilled: a session written before this
column existed ran under the defaults, and ``NULL`` says exactly that. A
backfill would write values those sessions never had, into the one record
whose job is to explain how their turns were computed.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("settings", JSONB(), nullable=True),
        schema="revi_session",
    )


def downgrade() -> None:
    op.drop_column("sessions", "settings", schema="revi_session")
