"""Rename the ``revi_rounds`` schema and its "watch" vocabulary to Monitors.

The proactive surface shipped under the working name "Rounds": a
capability schema ``revi_rounds``, three indexes prefixed
``ix_revi_rounds_*``, and a ``pins.watch`` column. The product noun is now
**monitor**, and a schema that still says "rounds" would make every future
reader translate between two vocabularies to answer a question about one
table.

Renames only. No data moves, nothing is copied, and no column changes
type: ``ALTER SCHEMA … RENAME`` carries its tables, indexes and rows with
it, so a deployment holding a tenant's pins keeps every one of them. There
is no external consumer of the wire or the database yet, so there is no
compatibility view and no dual-read window; ``downgrade`` puts every name
back.

``ix_*`` names are renamed explicitly because ``ALTER SCHEMA`` moves an
index without renaming it: left alone, ``revi_monitors`` would contain
three indexes still called ``ix_revi_rounds_*``.

**The stored JSON moves too, and it has to.** ``pin_results.payload`` and
``loads.payload`` are evaluated tiles and load censuses serialised by the
very models this rename touched, so a row written before it holds
``"threshold_source": "watch"`` and a key called
``watches_below_governed_gate``. Both are CLOSED contracts: the first is a
``Literal`` that now reads ``"monitor"``, the second a field that no longer
exists under that name — so renaming only the schema makes the first read
that reaches back to a prior load fail validation. The same token rewrite
the source tree took is applied to the payload text (``watch`` →
``monitor``, and its inflections), which leaves the database saying
precisely what the renamed code would now write. ``watermark`` is
untouched: it shares no prefix with ``watch`` and it is a data-load
concept, not a monitor.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-10
"""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None

_OLD_SCHEMA = "revi_rounds"
_NEW_SCHEMA = "revi_monitors"

#: ``(table, old index name, new index name)``.
_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("pins", "ix_revi_rounds_pins_tenant", "ix_revi_monitors_pins_tenant"),
    (
        "pin_results",
        "ix_revi_rounds_pin_results_pin_loaded",
        "ix_revi_monitors_pin_results_pin_loaded",
    ),
    ("loads", "ix_revi_rounds_loads_tenant_loaded", "ix_revi_monitors_loads_tenant_loaded"),
)


#: The payload columns holding serialised model output, and the primary key
#: that identifies a row of each.
_JSON_COLUMNS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("pin_results", "payload", ("pin_id", "watermark_id")),
    ("loads", "payload", ("tenant", "watermark_id")),
)

#: Longest inflection first, each anchored at a word start so ``swatch``
#: and a trailing ``…_watch`` id are left alone.
_TO_MONITOR: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![a-zA-Z])watches"), "monitors"),
    (re.compile(r"(?<![a-zA-Z])watched"), "monitored"),
    (re.compile(r"(?<![a-zA-Z])watching"), "monitoring"),
    (re.compile(r"(?<![a-zA-Z])watch"), "monitor"),
    (re.compile(r"Watches"), "Monitors"),
    (re.compile(r"Watched"), "Monitored"),
    (re.compile(r"Watching"), "Monitoring"),
    (re.compile(r"Watch"), "Monitor"),
)

_TO_WATCH: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![a-zA-Z])monitors"), "watches"),
    (re.compile(r"(?<![a-zA-Z])monitored"), "watched"),
    (re.compile(r"(?<![a-zA-Z])monitoring"), "watching"),
    (re.compile(r"(?<![a-zA-Z])monitor"), "watch"),
    (re.compile(r"Monitors"), "Watches"),
    (re.compile(r"Monitored"), "Watched"),
    (re.compile(r"Monitoring"), "Watching"),
    (re.compile(r"Monitor"), "Watch"),
)


def _rewrite_payloads(rules: tuple[tuple[re.Pattern[str], str], ...]) -> None:
    conn = op.get_bind()
    for table, column, key in _JSON_COLUMNS:
        keys = ", ".join(key)
        rows = conn.execute(
            sa.text(f'SELECT {keys}, {column}::text FROM "{_NEW_SCHEMA}".{table}')
        ).all()
        where = " AND ".join(f"{name} = :{name}" for name in key)
        update = sa.text(
            f'UPDATE "{_NEW_SCHEMA}".{table} SET {column} = CAST(:payload AS jsonb) '
            f"WHERE {where}"
        )
        for row in rows:
            text = row[-1]
            rewritten = text
            for pattern, replacement in rules:
                rewritten = pattern.sub(replacement, rewritten)
            if rewritten == text:
                continue
            conn.execute(
                update, {**dict(zip(key, row[: len(key)], strict=True)), "payload": rewritten}
            )


def upgrade() -> None:
    op.execute(sa.text(f'ALTER SCHEMA "{_OLD_SCHEMA}" RENAME TO "{_NEW_SCHEMA}"'))
    for _table, old, new in _INDEXES:
        op.execute(sa.text(f'ALTER INDEX "{_NEW_SCHEMA}"."{old}" RENAME TO "{new}"'))
    op.alter_column("pins", "watch", new_column_name="monitor", schema=_NEW_SCHEMA)
    _rewrite_payloads(_TO_MONITOR)


def downgrade() -> None:
    _rewrite_payloads(_TO_WATCH)
    op.alter_column("pins", "monitor", new_column_name="watch", schema=_NEW_SCHEMA)
    for _table, old, new in _INDEXES:
        op.execute(sa.text(f'ALTER INDEX "{_NEW_SCHEMA}"."{new}" RENAME TO "{old}"'))
    op.execute(sa.text(f'ALTER SCHEMA "{_NEW_SCHEMA}" RENAME TO "{_OLD_SCHEMA}"'))
