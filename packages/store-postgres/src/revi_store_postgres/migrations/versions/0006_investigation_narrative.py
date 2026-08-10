"""The composed narrative on the investigation record.

Round-10 R10-4. "Copy link" shipped a page with the analysis removed: a
cold open of ``/s/{sid}`` rendered *"The written analysis was not stored
for this turn…"* where the live turn had published 1,900-2,400 characters
of prose, and a mid-demo refresh did the same to every turn in the session.
The narrative is the artifact a buyer forwards to a CFO, and it was the
one part of an answer that nothing kept — filed three times across five
review rounds before it was the cause of its own finding.

One nullable ``TEXT`` column. Nullable and never backfilled, for the same
reason ``0002`` gives about session settings: a turn that ran before this
column existed did not keep its prose, and writing an empty string there
would assert that it published none. A reader must read ``NULL`` as "not
stored", never as "said nothing" — the restoration note downstream is
written from exactly that distinction.

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
