"""CLI: ``python -m revi_scheduler.sweep [--now ISO-8601] [--dry-run]`` — the cohort TTL sweep.

Pinning a cohort materializes a TABLE in the DuckDB warehouse
(``cohort_store.cohort_<hex>``) that outlives the turn — and the session, and
the process — that pinned it. The TTL is the only thing bounding the
warehouse's cohort schema; this sweep is what enforces it, and it is the whole
of the scheduler app.

This is the **operator-invoked** path. The API process runs the same
reclamation on its own schedule (``revi_api.cohort_sweep``), so a deployment
does not depend on anyone remembering to run this; the CLI is for one-off
reclamation, dry-run inspection, and warehouses no API process owns.

The sweep has two halves and reports them separately, because they can
legitimately disagree — a table can already be gone, and metadata is simply
absent when running without a database:

* **warehouse** — ``DuckDbAnalyticalRepository.sweep_cohorts(now)`` is
  authoritative. It reads ``cohort_store.registry`` — a table *in the
  warehouse* — so a freshly started sweep process sees every cohort any
  process ever materialized, and it additionally reclaims ``cohort_*`` tables
  with no registry row at all (orphans from a crashed process, or from a
  build that predates the registry). The two causes are reported separately:
  expired is the TTL working, orphaned is storage nothing was tracking.
* **metadata** — ``CohortStore.expired(now)`` reports which cohort records in
  the *application-state* store have expired. It needs ``REVI_DATABASE_URL``;
  when that is unset or unreachable the sweep logs loudly and SKIPS this half
  rather than reporting a zero it never measured. The warehouse half does not
  depend on this one.

``--dry-run`` reports exactly what a real run would drop, from the registry
and the table listing, and writes nothing — it opens the warehouse read-only.

Configuration mirrors ``revi_api.wiring`` — same variables, same defaults, same
loud logging of every choice: ``REVI_WAREHOUSE_PATH`` (default
``<repo root>/data/revi_warehouse.duckdb``) and ``REVI_DATABASE_URL``. Nothing
else is read; the sweep loads no catalog, no pack and no model.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

from revi_catalog_contracts.model import CalendarDef, CatalogSnapshot
from revi_connector_duckdb import CohortInventory, CohortSweepResult, DuckDbAnalyticalRepository
from revi_investigation.application.ports import CohortStore
from revi_kernel.errors import SourceUnavailableError

logger = logging.getLogger("revi.scheduler.sweep")

# apps/scheduler/src/revi_scheduler/sweep.py → repo root
_REPO_ROOT = Path(__file__).resolve().parents[4]

# ``drop_expired_cohorts`` compiles no probe, so the catalog and metric
# contracts the repository constructor demands are never consulted; the sweep
# hands over an empty catalog rather than loading catalog YAML it never reads.
_EMPTY_CATALOG = CatalogSnapshot(
    entities=(),
    dimensions=(),
    measures=(),
    date_bases=(),
    calendar=CalendarDef(
        table="unused",
        date_column="unused",
        range_start=date(1970, 1, 1),
        range_end=date(1970, 1, 1),
    ),
)


def _no_metrics(metric_id: str) -> None:
    """No metric contract is ever resolved: the sweep compiles no probes."""
    return None


class CohortSweeper(Protocol):
    """The slice of the DuckDB repository the sweep uses."""

    async def sweep_cohorts(self, now: datetime, *, dry_run: bool = False) -> CohortSweepResult: ...

    async def cohort_inventory(self) -> CohortInventory: ...


@dataclass(frozen=True)
class CohortStoreChoice:
    """The metadata half's outcome of environment resolution. ``store`` is
    ``None`` when the half is skipped; ``detail`` says why, for the report."""

    store: CohortStore | None
    detail: str


@dataclass(frozen=True)
class SweepReport:
    """What the sweep actually did — never more than it measured."""

    now: datetime
    dry_run: bool
    store_detail: str
    result: CohortSweepResult  # cohort TABLES reclaimed in the warehouse
    before: CohortInventory  # warehouse census before the sweep
    after: CohortInventory  # …and after (identical on a dry run)
    expired: tuple[str, ...] | None  # expired cohort METADATA; None ⇒ half skipped

    @property
    def dropped(self) -> tuple[str, ...]:
        return self.result.dropped

    def summary(self) -> tuple[str, ...]:
        verb = "would drop" if self.dry_run else "dropped"
        lines = [f"cohort TTL sweep @ {self.now.isoformat()}" + (" [DRY RUN]" if self.dry_run else "")]
        lines.append(f"  before:    {_census(self.before)}")
        lines.append(
            f"  warehouse: {verb} {len(self.result.expired)} expired + "
            f"{len(self.result.orphaned)} orphaned cohort table(s)"
        )
        if self.result.orphaned:
            lines.append(
                f"             orphaned = a cohort table with no registry row: nothing but this "
                f"enumeration could ever have reclaimed it{_ids(self.result.orphaned, limit=5)}"
            )
        lines.append(f"  after:     {_census(self.after)}")
        if self.expired is None:
            lines.append(f"  metadata:  skipped — {self.store_detail}")
        else:
            lines.append(
                f"  metadata:  {len(self.expired)} expired cohort record(s){_ids(self.expired)} "
                f"[{self.store_detail}]"
            )
            if not self.dry_run:
                lines.extend(self._reconciliation())
        return tuple(lines)

    def _reconciliation(self) -> tuple[str, ...]:
        """Name where the two halves disagree rather than averaging it away."""
        expired = set(self.expired or ())
        dropped = set(self.dropped)
        lines: list[str] = []
        if metadata_only := sorted(expired - dropped):
            lines.append(
                f"  note:      {len(metadata_only)} expired record(s) had no table dropped here "
                f"(already gone, or dropped by an earlier sweep): {', '.join(metadata_only)}"
            )
        if warehouse_only := sorted(dropped - expired):
            lines.append(
                f"  note:      {len(warehouse_only)} dropped table(s) had no expired record "
                f"(metadata missing or not yet expired){_ids(tuple(warehouse_only), limit=5)}"
            )
        return tuple(lines)


def _census(inventory: CohortInventory) -> str:
    return (
        f"{inventory.tables:,} cohort table(s), {inventory.rows:,} row(s), "
        f"{inventory.registered:,} registry row(s)"
    )


def _ids(ids: tuple[str, ...], *, limit: int | None = None) -> str:
    """Render ids for the report, eliding past ``limit`` so a sweep that
    reclaims hundreds of orphans does not print hundreds of lines."""
    if not ids:
        return ""
    if limit is not None and len(ids) > limit:
        return f": {', '.join(ids[:limit])}, … (+{len(ids) - limit} more)"
    return f": {', '.join(ids)}"


# ----------------------------------------------------------------- environment


def warehouse_path(env: Mapping[str, str]) -> Path:
    """``REVI_WAREHOUSE_PATH``, defaulted exactly as ``revi_api.wiring`` does."""
    return Path(env.get("REVI_WAREHOUSE_PATH", str(_REPO_ROOT / "data/revi_warehouse.duckdb")))


def build_cohort_store(env: Mapping[str, str]) -> CohortStoreChoice:
    """Postgres cohort store when ``REVI_DATABASE_URL`` is set *and* reachable;
    otherwise no store at all, so the sweep skips the metadata half rather than
    reporting an in-memory store's empty answer as the truth."""
    url = env.get("REVI_DATABASE_URL", "").strip()
    if not url:
        logger.warning(
            "REVI_DATABASE_URL unset — SKIPPING the cohort-metadata half of the sweep "
            "(warehouse tables can still be dropped; expired metadata cannot be reported)"
        )
        return CohortStoreChoice(None, "no database configured (REVI_DATABASE_URL unset)")
    try:
        import sqlalchemy

        from revi_store_postgres import PostgresCohortStore, create_engine

        engine = create_engine(url)
        with engine.connect() as connection:  # reachability probe
            connection.execute(sqlalchemy.text("SELECT 1"))
        logger.info("using POSTGRES cohort store at %s", url.split("@")[-1])
        return CohortStoreChoice(PostgresCohortStore(engine), "postgres")
    except Exception as exc:
        logger.error(
            "REVI_DATABASE_URL set but unreachable (%s) — SKIPPING the cohort-metadata half",
            exc,
        )
        return CohortStoreChoice(None, "database configured but unreachable")


# ----------------------------------------------------------------------- sweep


async def run_sweep(
    repository: CohortSweeper,
    choice: CohortStoreChoice,
    *,
    now: datetime,
    dry_run: bool,
) -> SweepReport:
    """Run both halves (metadata first, so a dry run still reports something)."""
    expired: tuple[str, ...] | None = None
    if choice.store is not None:
        records = await choice.store.expired(now)
        expired = tuple(record.id for record in records)
        logger.info("cohort metadata expired as of %s: %d record(s)", now.isoformat(), len(expired))

    before = await repository.cohort_inventory()
    result = await repository.sweep_cohorts(now, dry_run=dry_run)
    after = before if dry_run else await repository.cohort_inventory()
    logger.info(
        "warehouse half: %s %d expired + %d orphaned cohort table(s)",
        "would drop" if dry_run else "dropped",
        len(result.expired),
        len(result.orphaned),
    )

    return SweepReport(
        now=now,
        dry_run=dry_run,
        store_detail=choice.detail,
        result=result,
        before=before,
        after=after,
        expired=expired,
    )


# ------------------------------------------------------------------------- cli


def _iso_datetime(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not an ISO-8601 datetime: {raw!r}") from None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m revi_scheduler.sweep",
        description=(
            "Cohort TTL sweep: drop expired pinned-cohort tables from the DuckDB warehouse "
            "and report which cohort metadata records have expired."
        ),
    )
    parser.add_argument(
        "--now",
        type=_iso_datetime,
        default=None,
        metavar="ISO8601",
        help="evaluate TTLs as of this instant (naive values are read as UTC); default: now, UTC",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "report exactly what would be dropped, from the warehouse's own cohort registry, "
            "and write nothing (the warehouse is opened read-only)"
        ),
    )
    return parser


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    env = env if env is not None else dict(os.environ)
    now: datetime = args.now if args.now is not None else datetime.now(UTC)

    warehouse = warehouse_path(env)
    logger.info("warehouse=%s now=%s dry_run=%s", warehouse, now.isoformat(), args.dry_run)
    if not warehouse.is_file():
        logger.error(
            "warehouse not found at %s — generate it with `make warehouse`, or point "
            "REVI_WAREHOUSE_PATH at an existing file",
            warehouse,
        )
        return 2

    repository = DuckDbAnalyticalRepository(warehouse, _EMPTY_CATALOG, _no_metrics)
    choice = build_cohort_store(env)
    try:
        report = asyncio.run(run_sweep(repository, choice, now=now, dry_run=args.dry_run))
    except SourceUnavailableError as exc:
        logger.error("the analytical warehouse is unavailable (%s) — nothing was swept", exc)
        return 1

    for line in report.summary():
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
