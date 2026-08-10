"""Carry the M32 entity-label rename into every stored payload.

M32 renamed twelve fictional entities in the warehouse generator to
collision-safe marks — two payers, one facility, nine plans — and
regenerated the warehouse. The rename is LABEL-ONLY: the seed, the
distributions and every figure are untouched, which the answer-key diff
proved and which this migration relies on. Nothing here changes a number.

Stored state did not move with it, and stored state is full of labels. An
investigation records the filter it ran (``payer_name = 'Meridian Health'``)
and the finding it published; a monitor stores the spec it re-runs, the tile
it published and the sentence its threshold was justified with; a trace
records the SQL that was executed and the frame that came back; the referent
registry records which cell every ``F1`` in every session was. All of it
names entities that the warehouse no longer holds, so:

* ``make warehouse-diff`` — the independent audit that re-derives every
  published value by its own SQL path — failed on 79 live divergences, each
  one a filter naming a retired label and therefore deriving 0 against a
  real published figure. Substituting the current label reproduces the
  published value exactly (``2861494`` for the finding this was first
  traced through), which is the proof that the rename was label-only and
  that this is a naming defect rather than an arithmetic one;
* monitors published tiles under retired names, and — until the fix that
  ships alongside this migration — went on publishing a stored VALUE for a
  payer the warehouse holds no rows for.

**Precision, not convenience.** The rules below replace FULL LABELS,
longest first, never a bare stem that a surviving entity still uses. That
distinction is the whole risk: ``Bluestone Mutual``, ``Bluestone Federal
PPO`` and the card title ``Bluestone PPO Imaging`` are all CURRENT, so a
``Bluestone`` → anything rule would rewrite live names into nonsense. Only
after the full labels are applied do three stem rules run, for
``Meridian`` / ``Pinnacle`` / ``Eastside`` — the three marks that were
retired ENTIRELY, appear nowhere in the regenerated warehouse, and survive
in stored prose the full labels cannot reach: a detector card title
("Posting stall on late-July remits: Pinnacle Oncology"), an analyst's own
rationale on a monitor ("Pinnacle is our JOC account — brief me on anything
over a point."), a refused sentence quoting the cell it named. Those are
about the renamed entity too, and leaving them would publish two names for
one payer on one screen.

**Symmetric.** ``downgrade`` applies the reverse map under the same
longest-first discipline, so a payload round-trips byte for byte (pinned by
an md5 round-trip in ``test_postgres_stores.py``). That is what makes the
rename reversible if the generator's names are ever reverted.

Every rewrite is one server-side ``UPDATE`` per column over the rows that
actually match, with the WHERE clause built from the same map as the SET
expression so the two cannot drift.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


#: ``(retired label, current label)``, LONGEST FIRST.
#:
#: Order is load-bearing rather than cosmetic: ``Pinnacle Health Plan`` has
#: to be spent before ``Pinnacle HMO`` can be considered, or a payer becomes
#: a plan. Applied in this order, each rule consumes a whole label and the
#: next rule sees only what the earlier ones did not name.
#:
#: The trailing three are STEMS, and they are deliberately last and
#: deliberately only these three: ``Meridian``, ``Pinnacle`` and ``Eastside``
#: name nothing in the regenerated warehouse, so anything still carrying one
#: after the full labels above have run is prose about a renamed entity.
#: There is no ``Bluestone`` stem rule for exactly the opposite reason — the
#: mark survives on entities this rename never touched.
_RENAMES: tuple[tuple[str, str], ...] = (
    # payers
    ("Pinnacle Health Plan", "Ashvale Health Plan"),
    ("Meridian Health", "Halvern Health"),
    # facility
    ("Eastside Medical Center", "Eastmere Medical Center"),
    # plans
    ("Meridian Exchange PPO", "Halvern Exchange PPO"),
    ("Meridian POS Choice", "Halvern POS Choice"),
    ("Meridian PPO Prime", "Halvern PPO Prime"),
    ("Meridian HMO Care", "Halvern HMO Care"),
    ("Bluestone PPO Blue", "Bluestone Preferred PPO"),
    ("Bluestone HMO Blue", "Bluestone Select HMO"),
    ("Pinnacle PPO", "Ashvale PPO"),
    ("Pinnacle HMO", "Ashvale HMO"),
    ("Pinnacle POS", "Ashvale POS"),
    # retired stems, in prose the full labels cannot reach
    ("Meridian", "Halvern"),
    ("Pinnacle", "Ashvale"),
    ("Eastside", "Eastmere"),
)


#: Every stored surface that carries an entity label, as
#: ``(schema, table, column, is_json)``. Assembled by reading the columns
#: rather than the code that writes them, because the point of a data
#: migration is the rows that are already there.
#:
#: What each one holds, and why it cannot be skipped:
#:
#: * ``monitors.pins`` — the label on the tile, the SPEC it re-runs every
#:   load (its filter values ARE labels), and the analyst's own sensitivity
#:   rationale, which is republished in the brief's materiality note;
#: * ``monitors.pin_results`` / ``monitors.loads`` — evaluated tiles and the
#:   per-load detection census: published values, subject labels, delta
#:   subject labels, card titles;
#: * ``monitors.leads`` — a lead's note, the platform's verification
#:   sentence and its history, all of which name the cell they are about;
#: * ``trace.investigations`` — the question as asked, the disposed spec,
#:   every published finding, the caveats and the narrative;
#: * ``trace.traces`` / ``trace.frames`` / ``trace.edges`` — the recorded
#:   audit path: executed probes, the SQL text, returned frames and the
#:   refinement operators between turns. This is what an auditor reads, so
#:   it is the last place a stale name may be left;
#: * ``cache.evidence`` — cached probe frames keyed by (probe, watermark,
#:   pack). Not rewritten, an answer served from cache prints the retired
#:   name beside a fresh one;
#: * ``cohort.cohorts`` — a pinned cohort's scope predicates and origin;
#: * ``session.referents`` — what every ``F1`` in every session was, which is
#:   how a follow-up resolves "that payer";
#: * ``session.turn_receipts`` — the stored response a retried turn replays
#:   verbatim.
_SURFACES: tuple[tuple[str, str, str, bool], ...] = (
    ("revi_monitors", "pins", "label", False),
    ("revi_monitors", "pins", "spec", True),
    ("revi_monitors", "pins", "monitor", True),
    ("revi_monitors", "pin_results", "payload", True),
    ("revi_monitors", "loads", "payload", True),
    ("revi_monitors", "leads", "note", False),
    ("revi_monitors", "leads", "verification_note", False),
    ("revi_monitors", "leads", "baseline_basis", False),
    ("revi_monitors", "leads", "history", True),
    ("revi_trace", "investigations", "question", False),
    ("revi_trace", "investigations", "spec", True),
    ("revi_trace", "investigations", "findings", True),
    ("revi_trace", "investigations", "warnings", True),
    ("revi_trace", "investigations", "narrative", False),
    ("revi_trace", "traces", "payload", True),
    ("revi_trace", "frames", "frame", True),
    ("revi_trace", "edges", "operators", True),
    ("revi_cache", "evidence", "frame", True),
    ("revi_cohort", "cohorts", "definition", True),
    ("revi_cohort", "cohorts", "origin", True),
    ("revi_cohort", "cohorts", "pinned", True),
    ("revi_session", "referents", "payload", True),
    ("revi_session", "turn_receipts", "response", True),
)


def rewrite(text: str, rules: tuple[tuple[str, str], ...]) -> str:
    """Apply one direction of the map to one string, longest label first.

    Exported for the round-trip test, and the same order the SQL below
    nests its ``replace()`` calls in — so what the test proves is what the
    database does.
    """
    for old, new in rules:
        text = text.replace(old, new)
    return text


def _set_expression(column: str, rules: tuple[tuple[str, str], ...]) -> str:
    """``replace(replace(col, …), …)`` — the map, as one SQL expression."""
    expression = f"{column}::text"
    for old, new in rules:
        expression = f"replace({expression}, '{old}', '{new}')"
    return expression


def _where_clause(column: str, rules: tuple[tuple[str, str], ...]) -> str:
    """Only the rows that carry a name this map renames.

    Built from the SAME map as the SET expression: a WHERE that named its
    own list of labels would eventually miss one and leave a row behind
    silently, which is the failure mode a data migration cannot afford.
    """
    stems = sorted({old for old, _ in rules})
    return " OR ".join(f"{column}::text LIKE '%{stem}%'" for stem in stems)


def _apply(rules: tuple[tuple[str, str], ...]) -> None:
    # These labels are code constants and are inlined into the statement, so
    # the one thing that would make that unsafe is checked rather than
    # assumed.
    assert not any("'" in name or "\\" in name for pair in rules for name in pair)
    conn = op.get_bind()
    for schema, table, column, is_json in _SURFACES:
        rewritten = _set_expression(column, rules)
        value = f"CAST({rewritten} AS jsonb)" if is_json else rewritten
        conn.execute(
            sa.text(
                f'UPDATE "{schema}".{table} SET {column} = {value} '
                f"WHERE {_where_clause(column, rules)}"
            )
        )


def upgrade() -> None:
    _apply(_RENAMES)


def downgrade() -> None:
    _apply(tuple((new, old) for old, new in _RENAMES))
