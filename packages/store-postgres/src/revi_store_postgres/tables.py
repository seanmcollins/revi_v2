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
ROUNDS_SCHEMA = "revi_rounds"

ALL_SCHEMAS: tuple[str, ...] = (
    SESSION_SCHEMA,
    TRACE_SCHEMA,
    COHORT_SCHEMA,
    PACK_SCHEMA,
    CACHE_SCHEMA,
    ROUNDS_SCHEMA,
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
    # Soft archive (migration 0004). NULL is an active session; a timestamp
    # is one the analyst dismissed. Nothing is deleted: a session owns
    # investigations, traces, frames and cohorts that other reads still
    # resolve through it, and a hard delete would turn a tidy-up into
    # dangling lineage.
    sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    # The session list's only filter (migration 0003): without it, "which
    # sessions does this tenant own?" scans every session in the deployment.
    sa.Index("ix_revi_session_sessions_tenant", "tenant"),
    schema=SESSION_SCHEMA,
)

#: One executed turn, keyed by the caller's idempotency key (migration
#: 0004). The API used to hold these in a process-local dict, so a restart
#: — or a second worker — turned a retried POST into a second execution of
#: the same turn: fresh model spend, a second investigation in the DAG, and
#: two different answers to one request. The response is stored as the
#: serialized ``TurnResponse`` so a replay returns the ORIGINAL payload
#: rather than a re-run that could differ.
turn_receipts = sa.Table(
    "turn_receipts",
    metadata,
    sa.Column("tenant", sa.Text, primary_key=True),
    sa.Column("session_id", sa.Text, primary_key=True),
    sa.Column("idempotency_key", sa.Text, primary_key=True),
    sa.Column("response", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
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
    # Nullable and never backfilled: a turn that ran before 0006 did not
    # keep its prose, and writing an empty string there would say it
    # published none (round-10 R10-4).
    sa.Column("narrative", sa.Text, nullable=True),
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

# --- Rounds: the proactive surface (migration 0005) -------------------------

#: A pinned artifact = its TYPED SPEC, never a snapshot. ``spec`` holds the
#: ``TypedInvestigationSpec`` as JSON so every load can re-run it at the new
#: watermark; ``window_mode`` records whether that re-run tracks a moving
#: period or re-measures fixed dates, because the two make a load-over-load
#: delta mean different things.
#:
#: ``tenant`` is the scope in v1 (see the AUTH DEBT note on
#: ``revi_investigation.application.ports``): per-user pins are a column and
#: a filter away, and the index below is already the one they would need.
rounds_pins = sa.Table(
    "pins",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("tenant", sa.Text, nullable=False),
    sa.Column("label", sa.Text, nullable=False),
    sa.Column("presentation", sa.Text, nullable=False),
    sa.Column("window_mode", sa.Text, nullable=False),
    sa.Column("spec", JSONB, nullable=False),
    sa.Column("watch", JSONB, nullable=True),
    sa.Column("created_from_kind", sa.Text, nullable=False, server_default="spec"),
    sa.Column("created_from_investigation_id", sa.Text, nullable=True),
    sa.Column("created_from_referent", sa.Text, nullable=True),
    sa.Column("created_by", sa.Text, nullable=False, server_default=""),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    # Soft un-pin, exactly like the session archive: the evaluated history a
    # brief already published stays readable, and nothing dangles.
    sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    # The BASELINE: what this watch read at the load it was created on,
    # captured once and never rewritten. Stored on the pin rather than
    # derived from the oldest result because it is a fact about the WATCH
    # and must survive result history being trimmed. ``Text`` for the value
    # because a threshold or a baseline that round-trips through a float is
    # not the number that was measured.
    sa.Column("baseline_watermark_id", sa.Text, nullable=True),
    sa.Column("baseline_value", sa.Text, nullable=True),
    sa.Column("baseline_unit", sa.Text, nullable=True),
    sa.Column("baseline_captured_at", sa.DateTime(timezone=True), nullable=True),
    sa.Index("ix_revi_rounds_pins_tenant", "tenant"),
    schema=ROUNDS_SCHEMA,
)

#: One evaluated tile per (pin, load). ``watermark_loaded_at`` is stored
#: beside the id because "the prior load" is ordered by the load's own
#: clock — watermark ids are opaque strings and sorting them lexically is a
#: guess that happens to work on ``wm_001``.
rounds_pin_results = sa.Table(
    "pin_results",
    metadata,
    sa.Column("pin_id", sa.Text, primary_key=True),
    sa.Column("watermark_id", sa.Text, primary_key=True),
    sa.Column("tenant", sa.Text, nullable=False),
    sa.Column("watermark_loaded_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Index("ix_revi_rounds_pin_results_pin_loaded", "pin_id", "watermark_loaded_at"),
    schema=ROUNDS_SCHEMA,
)

#: The detection-feed census per (tenant, load). Without it "new lead" and
#: "self-resolved" are unanswerable: the feed is read per snapshot, so a
#: lead that was fixed simply stops appearing and there is nothing to
#: compare against.
rounds_loads = sa.Table(
    "loads",
    metadata,
    sa.Column("tenant", sa.Text, primary_key=True),
    sa.Column("watermark_id", sa.Text, primary_key=True),
    sa.Column("watermark_loaded_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("payload", JSONB, nullable=False),
    sa.Index("ix_revi_rounds_loads_tenant_loaded", "tenant", "watermark_loaded_at"),
    schema=ROUNDS_SCHEMA,
)

#: Lead lifecycle, keyed by the detection feed's own anomaly id so a status
#: survives the card object being rebuilt on every load.
rounds_leads = sa.Table(
    "leads",
    metadata,
    sa.Column("tenant", sa.Text, primary_key=True),
    sa.Column("anomaly_id", sa.Text, primary_key=True),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("note", sa.Text, nullable=False, server_default=""),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("claimed_at_watermark", sa.Text, nullable=True),
    sa.Column("baseline_cents", sa.BigInteger, nullable=True),
    sa.Column("baseline_basis", sa.Text, nullable=False, server_default=""),
    sa.Column("confirming_watermarks", JSONB, nullable=False),
    sa.Column("verification_note", sa.Text, nullable=False, server_default=""),
    sa.Column("history", JSONB, nullable=False),
    schema=ROUNDS_SCHEMA,
)
