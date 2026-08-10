"""Rounds: pinned specs, per-load tile results, load census, lead lifecycle.

Rounds is the proactive surface — Revi walks it every data load and briefs
what changed — and it is a new capability, so it gets its own
capability-named schema (``revi_rounds``) exactly as the design's §15
structure asks. Four tables, and each one exists because a specific
question is otherwise unanswerable:

``pins``        — the TYPED SPEC of a watched artifact, never a snapshot of
                  it. This is what makes a tile a watch: every load re-runs
                  the stored spec at the new watermark, so the tile shows a
                  current value with a current grade instead of a number
                  frozen the day somebody pinned it.
``pin_results`` — one evaluated tile per (pin, load). A load-over-load delta
                  needs something to diff against, and recomputing the prior
                  load on demand would re-read a warehouse snapshot to
                  answer a question already answered.
``loads``       — the detection feed's census per (tenant, load). The feed
                  is read per snapshot, so a lead that was fixed simply
                  stops appearing; without a stored census, "new lead" and
                  "self-resolved" cannot be decided at all.
``leads``       — lifecycle state keyed by the detector's own anomaly id, so
                  a status survives the card object being rebuilt every load.

Every table is tenant-scoped and nothing is user-scoped: v1 Rounds are
shared within a tenant (the AUTH DEBT recorded on
``revi_investigation.application.ports``). Per-user pins are a column and a
filter away, and ``ix_revi_rounds_pins_tenant`` is already the index that
would carry them.

Additive and reversible: a deployment that never pins anything is
indistinguishable from one running the previous revision, and the downgrade
drops the schema whole because nothing outside it references these rows.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

_SCHEMA = "revi_rounds"


def upgrade() -> None:
    op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{_SCHEMA}"'))

    op.create_table(
        "pins",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("presentation", sa.Text(), nullable=False),
        sa.Column("window_mode", sa.Text(), nullable=False),
        sa.Column("spec", JSONB(), nullable=False),
        sa.Column("watch", JSONB(), nullable=True),
        sa.Column("created_from_kind", sa.Text(), nullable=False, server_default="spec"),
        sa.Column("created_from_investigation_id", sa.Text(), nullable=True),
        sa.Column("created_from_referent", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        # The baseline this watch started from, captured once at first
        # evaluation. Text rather than numeric: a baseline that round-trips
        # through a float is not the number that was measured.
        sa.Column("baseline_watermark_id", sa.Text(), nullable=True),
        sa.Column("baseline_value", sa.Text(), nullable=True),
        sa.Column("baseline_unit", sa.Text(), nullable=True),
        sa.Column("baseline_captured_at", sa.DateTime(timezone=True), nullable=True),
        schema=_SCHEMA,
    )
    op.create_index("ix_revi_rounds_pins_tenant", "pins", ["tenant"], schema=_SCHEMA)

    op.create_table(
        "pin_results",
        sa.Column("pin_id", sa.Text(), primary_key=True),
        sa.Column("watermark_id", sa.Text(), primary_key=True),
        sa.Column("tenant", sa.Text(), nullable=False),
        sa.Column("watermark_loaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        schema=_SCHEMA,
    )
    # "The prior load" is ordered by the WATERMARK's own clock, never by id
    # order: ``wm_001 < wm_002`` is a coincidence of this warehouse's naming.
    op.create_index(
        "ix_revi_rounds_pin_results_pin_loaded",
        "pin_results",
        ["pin_id", "watermark_loaded_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "loads",
        sa.Column("tenant", sa.Text(), primary_key=True),
        sa.Column("watermark_id", sa.Text(), primary_key=True),
        sa.Column("watermark_loaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        schema=_SCHEMA,
    )
    op.create_index(
        "ix_revi_rounds_loads_tenant_loaded",
        "loads",
        ["tenant", "watermark_loaded_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "leads",
        sa.Column("tenant", sa.Text(), primary_key=True),
        sa.Column("anomaly_id", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at_watermark", sa.Text(), nullable=True),
        sa.Column("baseline_cents", sa.BigInteger(), nullable=True),
        sa.Column("baseline_basis", sa.Text(), nullable=False, server_default=""),
        sa.Column("confirming_watermarks", JSONB(), nullable=False),
        sa.Column("verification_note", sa.Text(), nullable=False, server_default=""),
        sa.Column("history", JSONB(), nullable=False),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("leads", schema=_SCHEMA)
    op.drop_index("ix_revi_rounds_loads_tenant_loaded", "loads", schema=_SCHEMA)
    op.drop_table("loads", schema=_SCHEMA)
    op.drop_index("ix_revi_rounds_pin_results_pin_loaded", "pin_results", schema=_SCHEMA)
    op.drop_table("pin_results", schema=_SCHEMA)
    op.drop_index("ix_revi_rounds_pins_tenant", "pins", schema=_SCHEMA)
    op.drop_table("pins", schema=_SCHEMA)
    op.execute(sa.text(f'DROP SCHEMA IF EXISTS "{_SCHEMA}"'))
