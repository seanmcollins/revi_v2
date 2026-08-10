"""FN-17 — the warehouse-diff harness: a structurally independent audit path.

THE GUARANTEE THIS PACKAGE EXISTS TO ENFORCE
============================================
Every published finding value equals its governed contract's definition,
recomputed by a **structurally independent** path, anchored at
human-verified points.

Two paths meet here and must agree:

1. **The product path** (the thing audited): probe compiler
   (``revi_connector_duckdb.compile``) → operators → calculation kernel →
   findings, stored in Postgres as investigations + evidence frames.
2. **The audit path** (this package): a naive SQL deriver that reads *only*

   * the metric contract YAML — ``packs/base-rcm/metrics/*.yaml``
   * the semantic catalog — ``warehouse/catalog/*.yaml``
   * the published answer's own context — window, date basis, scope
     filters, cohort, watermark, as published in the investigation's
     context header

   and emits the dumbest correct SQL directly against
   ``data/revi_warehouse.duckdb``.

INDEPENDENCE IS THE ENTIRE POINT
================================
No module in this package may import ``revi_connector_duckdb``,
``revi_investigation``, ``revi_calculation``, ``revi_calculation_contracts``,
``revi_catalog``, ``revi_catalog_contracts``, ``revi_pack``,
``revi_presentation``, ``revi_api`` or ``revi_kernel``. If the audit path
imported the compiler it would be re-deriving the product's answer with the
product's own code and would agree with any bug the compiler has. The rule is
enforced two ways by ``packages/testing/tests/test_warehouse_diff.py``:

* a static check that walks every module's AST for import statements, and
* a subprocess check that imports this package cold and asserts none of the
  forbidden roots landed in ``sys.modules``.

The package deliberately lives as its own top-level module (rather than under
``revi_testing``) so that not even a parent package initialiser can drag a
product module into the audit process. It sits in ``packages/testing/src/``
because that directory is already on ``sys.path`` for the workspace, which
keeps the harness runnable with **zero** edits to root config owned by other
lanes (``pyproject.toml``, ``uv.lock``).

WHAT v1 DERIVES, AND WHAT IT REFUSES
====================================
Derived (see :mod:`revi_warehouse_diff.deriver`):

* additive measures — ``sum`` / ``count`` / ``count_distinct`` with the
  catalog measure's governed row filter, the contract's ``filtered`` inner
  scope, and the contract's ``exclusions``, over the catalog-declared base
  view for the resolved snapshot schema;
* ratios — numerator and denominator as two separate aggregates, including
  cross-entity ratios where the two sides sit at different grains;
* the probe-time derived measures the pack declares in its own registry
  (``packs/base-rcm/NOTES.md``, "Derived measure registry") for the
  aggregation shape: ``payment_lag_days``, ``submission_lag_days``,
  ``charge_entry_lag_days``, ``late_charge_cents``, ``underpayment_cents``.

Refused, counted, and named — never silently skipped:

* ``kind: snapshot`` contracts (their open-inventory population is an
  as-of convention of the snapshot builder, not contract YAML);
* the snapshot-shape derived measures (``ar_age_days_billed_cents``,
  ``credit_balance_cents``, ``days_to_filing_deadline``);
* derived-bucket dimensions (``ar_age_bucket``, ``filing_runway_bucket``);
* finding value shapes that are not a metric quantity (ranks, premise
  verdicts, bound populations, …).

Every refusal carries a machine-readable reason code and is counted in the
report, so coverage is never overstated.

THE CORPUS IS A HISTORICAL RECORD, AND IS DATED
===============================================
The replay reads every investigation the product has ever stored, and those
were published by whatever engine was running that day. The disclosure
contract this audit holds answers to has changed since — see
:mod:`revi_warehouse_diff.archaeology`, which carries the dated list — so
every divergence is classified ``live`` or ``archaeology`` against the
investigation's own ``created_at``, and **the verdict fails on live
divergences only**.

Two changes account for nearly the whole of the first run's fix queue:

* **Unmarked bounds (pre-M19/M20).** Findings stored before 2026-08-09
  17:25 UTC publish the k=10 suppression floor as a measured value with no
  ``__is_bound`` marker, ranked first — "Veritas Comp Fund: 76.9% denial
  rate" over a truth of 15.4%. All three of the first run's examples were
  re-run **verbatim on the live engine** and came back as marked ceilings
  ("≤ 76.9% denial rate (upper bound)"), published unranked, with
  ``__is_bound`` / ``__bound`` / ``__bound_population`` on the values and an
  explicit "this is a ceiling and not a measurement" statement. They are
  fossils. Anything published under the current contract with an unmarked
  bound is a live divergence and fails the run.
* **Undisclosed probe windows (pre-wave-E2).** A playbook probe template may
  declare its own window; findings stored before that was disclosed carry the
  INVESTIGATION window in their titles over numbers computed across the
  probe's. Findings published since state their own window in the title and
  the statement, publish it as ``<metric>__window_start`` /
  ``__window_end`` (and ``__prior_window_start`` / ``__prior_window_end``
  when the comparison moved with it), and the context header says some
  checks ran their own periods. **The replay derives over the window the
  finding publishes** — auditing such a cell over the header's window audits
  a number nobody published.

§6.6 VALUE RESOLUTION IS RE-IMPLEMENTED, NOT IMPORTED
=====================================================
The product resolves a filter value against the certified domain before it
queries: ``'general surgery'`` runs as ``'General Surgery'``, and the answer
publishes the corrected value with the analyst's original beside it. The
stored SPEC keeps the analyst's spelling, so a literal replay of the spec
selects an empty population and reports a divergence about a population the
engine never read. :meth:`~revi_warehouse_diff.replay.CorpusReplay._resolve_predicates`
applies the same narrow rule the product's own warning states — *the closest
match in this data differs only in case or punctuation* — reading the domain
from the WAREHOUSE, and records every resolution it makes on the audited
value. A value that is ambiguous case-insensitively is left alone and
diverges as before.
"""

from __future__ import annotations

__all__ = [
    "FORBIDDEN_IMPORT_ROOTS",
]

#: Import roots the audit path must never touch. Asserted by the test suite.
FORBIDDEN_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "revi_connector_duckdb",
        "revi_investigation",
        "revi_investigation_contracts",
        "revi_calculation",
        "revi_calculation_contracts",
        "revi_catalog",
        "revi_catalog_contracts",
        "revi_pack",
        "revi_pack_contracts",
        "revi_pack_learning",
        "revi_presentation",
        "revi_kernel",
        "revi_api",
        "revi_store_postgres",
        "revi_testing",
        "revi_warehouse",
        "revi_adapter_claude",
        "revi_scheduler",
    }
)
