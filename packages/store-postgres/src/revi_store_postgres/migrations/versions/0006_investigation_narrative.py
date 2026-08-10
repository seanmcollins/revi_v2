"""The composed narrative on the investigation record.

The prose a turn publishes was the one part of an answer nothing kept: a
cold open of ``/s/{sid}``, or any refresh, rendered *"The written analysis
was not stored for this turn…"* in place of every narrative in the session.

One nullable ``TEXT`` column, never backfilled, for the same reason ``0002``
gives about session settings: a turn that ran before this column existed
did not keep its prose, and writing an empty string there would assert that
it published none. ``NULL`` means "not stored", never "said nothing" — the
restoration note downstream is written from exactly that distinction.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "investigations",
        sa.Column("narrative", sa.Text(), nullable=True),
        schema="revi_trace",
    )


def downgrade() -> None:
    op.drop_column("investigations", "narrative", schema="revi_trace")
