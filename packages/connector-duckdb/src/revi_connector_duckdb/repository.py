"""DuckDB :class:`AnalyticalRepository` adapter (design §6.3).

The contract-test twin and replay backend. As-of reads select a snapshot
schema via ``main.watermarks`` (the design's "snapshot copies" posture — no
time-travel emulation). All DuckDB work runs in ``asyncio.to_thread``; probes
use a short-lived **read-only** connection per call, and cohort DDL uses a
separate short-lived read-write connection. No DuckDB type, SQL string, or
driver exception ever crosses the port: every ``duckdb.Error`` is logged and
re-raised as a sanitized :class:`SourceUnavailableError`.

Frame conventions: money lands as ``int`` cents (BIGINT/HUGEINT → int),
dates as ``datetime.date``, DECIMALs as :class:`decimal.Decimal`; PHI-classed
row-evidence columns are masked (``revi_catalog_contracts.masking``) before
the frame is built; ``suppressed_cells`` is always 0 here — small-cell
suppression is applied by the execution service, not the adapter.

Cohort storage is the one place this adapter *writes* to the warehouse.
Without the three rules below, orphan cohort tables accumulate without bound
and nothing can reclaim them (measured at 214 tables / 11.9M rows / 145MB in
one development warehouse):

1. **Content-addressed ids.** ``cohort_id`` is a digest of the compiled
   selection (entity, base view, primary key, WHERE clause, bound
   parameters) *and* the watermark. Re-drilling the same population at the
   same watermark reuses the existing table instead of minting another one,
   so a replayed session costs one table rather than one table per replay.
2. **A durable registry.** ``cohort_store.registry`` records every
   materialization (id, watermark, size, created_at, expires_at) **in the
   warehouse itself**, so the sweep has an authoritative list even when the
   application-state database is absent, unreachable, or lost. A
   process-local record cannot do this: it reports "dropped 0" while another
   process's tables sit on disk.
3. **An authoritative sweep.** :meth:`sweep_cohorts` drops every registered
   cohort whose ``expires_at`` has passed *and* every ``cohort_*`` table with
   no registry row at all (an orphan from a crashed process, or from a build
   that predates the registry). Both halves are reported separately.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import threading
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from revi_calculation_contracts.contract import MetricContract
from revi_catalog_contracts.masking import mask_value
from revi_catalog_contracts.model import CatalogSnapshot, PhiClass
from revi_connector_duckdb.compile import (
    CompiledQuery,
    ProbeCompiler,
    derived_measure_capabilities,
)
from revi_kernel.capabilities import RepositoryCapabilities
from revi_kernel.cohort import CohortDefinition, CohortMaterialization
from revi_kernel.errors import (
    QueryBudgetExceededError,
    SourceUnavailableError,
    WatermarkStaleError,
)
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame, FrameSchema, ProbeProvenance
from revi_kernel.probes import EvidenceProbe, probe_hash
from revi_kernel.watermark import DataWatermark

logger = logging.getLogger(__name__)

_COHORT_SCHEMA = "cohort_store"
_COHORT_TTL_SECONDS = 86_400
_COHORT_REGISTRY = f"{_COHORT_SCHEMA}.registry"

#: Cohort table names this adapter is willing to drop. The width range is
#: deliberate: content-addressed ids are 16 hex characters, but warehouses
#: written before content addressing carry 12-hex random ids, and the sweep
#: must reclaim those too — they are precisely the orphans it exists to remove.
_COHORT_ID_RE = re.compile(r"^cohort_[0-9a-f]{12,32}$")

#: Bumped whenever the compiled cohort SQL changes shape, so a stale table
#: written by an older compiler is never reused under a matching digest.
_COHORT_ADDRESS_VERSION = "v1"

_WATERMARKS_SQL = (
    "SELECT watermark_id, schema_name, loaded_at, newest_data_date "
    "FROM main.watermarks ORDER BY loaded_at, watermark_id"
)

#: Timestamps are plain ``TIMESTAMP`` holding **naive UTC**, not
#: ``TIMESTAMPTZ``: binding a tz-aware Python datetime makes DuckDB import
#: ``pytz``, which is not a dependency of this project and whose absence
#: turns every cohort write into a sanitized ``SourceUnavailableError``. The
#: adapter normalizes on the way in and re-attaches UTC on the way out, so
#: nothing tz-naive escapes the module.
_REGISTRY_DDL = f"""
CREATE TABLE IF NOT EXISTS {_COHORT_REGISTRY} (
    cohort_id     VARCHAR PRIMARY KEY,
    watermark_id  VARCHAR   NOT NULL,
    entity        VARCHAR   NOT NULL,
    size          BIGINT    NOT NULL,
    created_at    TIMESTAMP NOT NULL,
    expires_at    TIMESTAMP NOT NULL,
    ttl_seconds   BIGINT    NOT NULL
)
"""


def _naive_utc(value: datetime) -> datetime:
    """Any datetime → naive UTC, for binding against the registry's columns."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class CohortSweepResult:
    """What one sweep actually reclaimed, split by how it was identified.

    The halves are reported separately because they mean different things to
    an operator: ``expired`` is the TTL working as designed, while
    ``orphaned`` is storage that nothing was tracking — a crashed process, or
    a warehouse written before the registry existed.
    """

    expired: tuple[str, ...] = ()
    orphaned: tuple[str, ...] = ()

    @property
    def dropped(self) -> tuple[str, ...]:
        return self.expired + self.orphaned


@dataclass(frozen=True, slots=True)
class CohortInventory:
    """A census of the warehouse's cohort schema, for before/after reporting."""

    tables: int
    registered: int
    rows: int


def _coerce_scalar(value: object) -> Scalar:
    """DuckDB cell → kernel Scalar. BIGINT/HUGEINT arrive as int, DATE as
    date, DECIMAL as Decimal; datetimes collapse to their date; floats (never
    produced by our compiled aggregates, but defensively) become Decimal."""
    if value is None or isinstance(value, (str, bool, int, Decimal)):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, float):
        return Decimal(str(value))
    raise SourceUnavailableError(
        f"analytical source returned an unsupported value type {type(value).__name__}"
    )


class DuckDbAnalyticalRepository:
    """DuckDB implementation of the ``AnalyticalRepository`` protocol."""

    def __init__(
        self,
        warehouse_path: str | Path,
        catalog: CatalogSnapshot,
        metrics: Callable[[str], MetricContract | None],
        *,
        max_cohort_size: int = 100_000,
    ) -> None:
        self._path = str(Path(warehouse_path))
        self._catalog = catalog
        self._max_cohort_size = max_cohort_size
        self._compiler = ProbeCompiler(catalog, metrics)
        # Cohort ids this process has created but not yet registered. The
        # sweep drops unregistered cohort tables as orphans; without this,
        # a sweep interleaved with a materialization on another thread could
        # reclaim a table between its CREATE and its registry INSERT.
        self._in_flight: set[str] = set()
        self._in_flight_lock = threading.Lock()

    # ----------------------------------------------------------------- port

    def capabilities(self) -> RepositoryCapabilities:
        """What this source can do, including what it can *compute* (§6.3).

        The derived-measure list and the cross-entity flag are read from the
        compiler's own registry, so what the planner validates and what this
        adapter will build SQL for are one statement rather than two kept in
        step by hand.
        """
        return RepositoryCapabilities(
            as_of_reads=True,
            cohort_semijoin=True,
            max_cohort_size=self._max_cohort_size,
            having_pushdown=True,
            server_side_top_n=True,
            derived_measures=derived_measure_capabilities(),
            cross_entity_ratio_of_sums=True,
        )

    async def list_watermarks(self) -> tuple[DataWatermark, ...]:
        return await asyncio.to_thread(self._list_watermarks_sync)

    async def execute(self, probe: EvidenceProbe, *, watermark: DataWatermark) -> EvidenceFrame:
        return await asyncio.to_thread(self._execute_sync, probe, watermark)

    async def materialize_cohort(
        self, definition: CohortDefinition, *, watermark: DataWatermark
    ) -> CohortMaterialization:
        return await asyncio.to_thread(self._materialize_sync, definition, watermark)

    async def drop_expired_cohorts(self, now: datetime) -> tuple[str, ...]:
        """Drop reclaimable cohort tables (the ``AnalyticalRepository`` hook).

        Returns every dropped cohort id — expired *and* orphaned. Callers that
        need the two apart call :meth:`sweep_cohorts`.
        """
        return (await self.sweep_cohorts(now)).dropped

    async def sweep_cohorts(self, now: datetime, *, dry_run: bool = False) -> CohortSweepResult:
        """Authoritative reclamation, reported by cause.

        Drops (a) every cohort registered in ``cohort_store.registry`` whose
        ``expires_at`` has passed and (b) every ``cohort_*`` table with no
        registry row, which nothing can ever reclaim on its own. Idempotent:
        a second call over the same instant drops nothing.

        ``dry_run=True`` reports the identical answer without dropping
        anything or touching the registry — the registry is what makes a
        truthful dry run possible at all.
        """
        return await asyncio.to_thread(self._sweep_sync, now, dry_run)

    async def cohort_inventory(self) -> CohortInventory:
        """Census of the cohort schema (tables, registry rows, total rows) —
        the before/after number an operator reads a sweep by."""
        return await asyncio.to_thread(self._inventory_sync)

    # ---------------------------------------------------------- connections

    def _connect(self, *, read_only: bool) -> duckdb.DuckDBPyConnection:
        try:
            return duckdb.connect(self._path, read_only=read_only)
        except duckdb.Error as exc:
            logger.error("duckdb connect failed for %s: %s", self._path, exc)
            raise SourceUnavailableError("analytical source is unavailable") from None

    # ------------------------------------------------------------ internals

    def _list_watermarks_sync(self) -> tuple[DataWatermark, ...]:
        con = self._connect(read_only=True)
        try:
            rows = con.execute(_WATERMARKS_SQL).fetchall()
        except duckdb.Error as exc:
            logger.error("duckdb watermark listing failed: %s", exc)
            raise SourceUnavailableError("analytical source is unavailable") from None
        finally:
            con.close()
        return tuple(
            DataWatermark(id=row[0], loaded_at=row[2], newest_data_date=row[3]) for row in rows
        )

    def _schema_for(self, con: duckdb.DuckDBPyConnection, watermark: DataWatermark) -> str:
        try:
            rows = con.execute(_WATERMARKS_SQL).fetchall()
        except duckdb.Error as exc:
            logger.error("duckdb watermark lookup failed: %s", exc)
            raise SourceUnavailableError("analytical source is unavailable") from None
        for row in rows:
            if row[0] == watermark.id:
                return str(row[1])
        raise WatermarkStaleError(
            f"watermark {watermark.id!r} is not a completed load",
            details={"watermark": watermark.id, "known": [row[0] for row in rows]},
        )

    def _execute_sync(self, probe: EvidenceProbe, watermark: DataWatermark) -> EvidenceFrame:
        con = self._connect(read_only=True)
        try:
            schema = self._schema_for(con, watermark)
            compiled = self._compiler.compile(probe, schema=schema, watermark=watermark)
            try:
                if compiled.single_thread:
                    con.execute("SET threads TO 1")
                raw_rows = con.execute(compiled.sql, list(compiled.params)).fetchall()
            except duckdb.Error as exc:
                logger.error("duckdb probe execution failed: %s", exc)
                raise SourceUnavailableError("analytical query failed at the source") from None
        finally:
            con.close()
        return self._build_frame(probe, compiled, raw_rows, watermark)

    def _build_frame(
        self,
        probe: EvidenceProbe,
        compiled: CompiledQuery,
        raw_rows: list[tuple[object, ...]],
        watermark: DataWatermark,
    ) -> EvidenceFrame:
        truncated = False
        if compiled.row_limit is not None and len(raw_rows) > compiled.row_limit:
            raw_rows = raw_rows[: compiled.row_limit]
            truncated = True
        if compiled.sample_size is not None and len(raw_rows) >= compiled.sample_size:
            truncated = True  # the sample is (or may be) a strict subset
        mask: tuple[PhiClass, ...] = compiled.mask
        rows: list[tuple[Scalar, ...]] = []
        for raw in raw_rows:
            values = [_coerce_scalar(cell) for cell in raw]
            if mask:
                values = [mask_value(value, phi) for value, phi in zip(values, mask, strict=True)]
            rows.append(tuple(values))
        digest = probe_hash(probe)
        return EvidenceFrame(
            schema=FrameSchema(columns=compiled.columns),
            rows=tuple(rows),
            watermark=watermark,
            provenance=ProbeProvenance(
                probe_id=digest[:12],
                probe_hash=digest,
                repository_query_id=str(uuid.uuid4()),
            ),
            evidence_grade=compiled.grade,
            truncated=truncated,
            suppressed_cells=0,  # suppression is the execution service's job
        )

    # --------------------------------------------------------------- cohorts

    @staticmethod
    def _cohort_address(
        *,
        entity_name: str,
        base_view: str,
        primary_key: str,
        schema: str,
        where_sql: str,
        params: Sequence[object],
        watermark: DataWatermark,
    ) -> str:
        """Content address of a cohort table: a digest of everything that
        determines its rows.

        Two drills that select the same population at the same watermark
        therefore land on the same table and the second one costs nothing.
        The watermark is part of the address on purpose — a cohort pinned at
        an older load is a *different* set of entity ids, and reusing one
        across watermarks would silently re-date the analyst's population.
        """
        digest = hashlib.sha256()
        parts: list[str] = [
            _COHORT_ADDRESS_VERSION,
            entity_name,
            base_view,
            primary_key,
            schema,
            watermark.id,
            where_sql,
        ]
        parts.extend(f"{type(param).__name__}:{param!r}" for param in params)
        for part in parts:
            digest.update(part.encode("utf-8"))
            digest.update(b"\x1f")  # unambiguous separator: no part can forge another
        return f"cohort_{digest.hexdigest()[:16]}"

    def _materialize_sync(
        self, definition: CohortDefinition, watermark: DataWatermark
    ) -> CohortMaterialization:
        entity, where_sql, params = self._compiler.compile_cohort_selection(definition, watermark=watermark)
        con = self._connect(read_only=False)  # short-lived read-write: cohort DDL only
        try:
            schema = self._schema_for(con, watermark)
            cohort_id = self._cohort_address(
                entity_name=entity.name,
                base_view=entity.base_view,
                primary_key=entity.primary_key,
                schema=schema,
                where_sql=where_sql,
                params=params,
                watermark=watermark,
            )
            table = f"{_COHORT_SCHEMA}.{cohort_id}"
            with self._in_flight_lock:
                self._in_flight.add(cohort_id)
            try:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS {_COHORT_SCHEMA}")
                con.execute(_REGISTRY_DDL)
                reused = self._registered_materialization(con, cohort_id, watermark)
                if reused is not None:
                    self._guard_size(con, table, reused.size, drop_on_reject=False)
                    logger.debug("reusing content-addressed cohort %s (size %d)", cohort_id, reused.size)
                    return reused
                con.execute(
                    f'CREATE OR REPLACE TABLE {table} AS SELECT DISTINCT "{entity.primary_key}" '
                    f'AS entity_id FROM "{schema}"."{entity.base_view}" WHERE {where_sql}',
                    list(params),
                )
                row = con.execute(f"SELECT count(*) FROM {table}").fetchone()
                size = int(row[0]) if row is not None else 0
                self._guard_size(con, table, size, drop_on_reject=True)
                created_at = datetime.now(UTC)
                con.execute(
                    f"INSERT OR REPLACE INTO {_COHORT_REGISTRY} "
                    "(cohort_id, watermark_id, entity, size, created_at, expires_at, ttl_seconds) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        cohort_id,
                        watermark.id,
                        entity.name,
                        size,
                        _naive_utc(created_at),
                        _naive_utc(created_at + timedelta(seconds=_COHORT_TTL_SECONDS)),
                        _COHORT_TTL_SECONDS,
                    ],
                )
                return CohortMaterialization(
                    cohort_id=cohort_id,
                    watermark=watermark,
                    entity_ids_ref=f"{_COHORT_SCHEMA}.{cohort_id}",
                    size=size,
                    created_at=created_at,
                    ttl_seconds=_COHORT_TTL_SECONDS,
                )
            except duckdb.Error as exc:
                logger.error("duckdb cohort materialization failed: %s", exc)
                raise SourceUnavailableError("cohort materialization failed at the source") from None
            finally:
                with self._in_flight_lock:
                    self._in_flight.discard(cohort_id)
        finally:
            con.close()

    def _guard_size(
        self, con: duckdb.DuckDBPyConnection, table: str, size: int, *, drop_on_reject: bool
    ) -> None:
        """The ``max_cohort_size`` budget, enforced on fresh and reused tables
        alike — a limit lowered since a cohort was pinned must still bind."""
        if size <= self._max_cohort_size:
            return
        if drop_on_reject:
            con.execute(f"DROP TABLE IF EXISTS {table}")
        raise QueryBudgetExceededError(
            f"cohort size {size} exceeds the repository limit {self._max_cohort_size}",
            details={"size": size, "max_cohort_size": self._max_cohort_size},
        )

    def _registered_materialization(
        self, con: duckdb.DuckDBPyConnection, cohort_id: str, watermark: DataWatermark
    ) -> CohortMaterialization | None:
        """The registered materialization for ``cohort_id``, if its table is
        still there. A registry row whose table is gone is stale metadata and
        is deleted so the caller re-materializes rather than handing back a
        handle to nothing."""
        row = con.execute(
            f"SELECT size, created_at, ttl_seconds FROM {_COHORT_REGISTRY} WHERE cohort_id = ?",
            [cohort_id],
        ).fetchone()
        if row is None:
            return None
        if not self._table_exists(con, cohort_id):
            con.execute(f"DELETE FROM {_COHORT_REGISTRY} WHERE cohort_id = ?", [cohort_id])
            return None
        created_at = row[1].replace(tzinfo=UTC) if row[1].tzinfo is None else row[1]
        return CohortMaterialization(
            cohort_id=cohort_id,
            watermark=watermark,
            entity_ids_ref=f"{_COHORT_SCHEMA}.{cohort_id}",
            size=int(row[0]),
            created_at=created_at,
            ttl_seconds=int(row[2]),
        )

    @staticmethod
    def _table_exists(con: duckdb.DuckDBPyConnection, table_name: str) -> bool:
        row = con.execute(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = ? AND table_name = ?",
            [_COHORT_SCHEMA, table_name],
        ).fetchone()
        return bool(row and int(row[0]) > 0)

    # --------------------------------------------------------------- sweeping

    @staticmethod
    def _schema_exists(con: duckdb.DuckDBPyConnection, schema: str) -> bool:
        row = con.execute(
            "SELECT count(*) FROM information_schema.schemata WHERE schema_name = ?", [schema]
        ).fetchone()
        return bool(row and int(row[0]) > 0)

    @staticmethod
    def _cohort_table_names(con: duckdb.DuckDBPyConnection) -> tuple[str, ...]:
        rows = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = ? "
            "ORDER BY table_name",
            [_COHORT_SCHEMA],
        ).fetchall()
        return tuple(str(row[0]) for row in rows if _COHORT_ID_RE.match(str(row[0])))

    def _sweep_sync(self, now: datetime, dry_run: bool = False) -> CohortSweepResult:
        cutoff = _naive_utc(now)
        con = self._connect(read_only=dry_run)
        try:
            if not self._schema_exists(con, _COHORT_SCHEMA):
                return CohortSweepResult()  # nothing was ever materialized here
            try:
                if not self._table_exists(con, "registry"):
                    if dry_run:
                        # No registry to read, and a read-only connection
                        # cannot make one. Every cohort table is unregistered,
                        # which is exactly what an orphan is.
                        return CohortSweepResult(orphaned=self._cohort_table_names(con))
                    con.execute(_REGISTRY_DDL)
                tables = set(self._cohort_table_names(con))
                registered = {
                    str(row[0])
                    for row in con.execute(f"SELECT cohort_id FROM {_COHORT_REGISTRY}").fetchall()
                }
                expired = tuple(
                    sorted(
                        str(row[0])
                        for row in con.execute(
                            f"SELECT cohort_id FROM {_COHORT_REGISTRY} WHERE expires_at <= ?", [cutoff]
                        ).fetchall()
                    )
                )
                with self._in_flight_lock:
                    in_flight = set(self._in_flight)
                orphaned = tuple(sorted(tables - registered - in_flight))

                if not dry_run:
                    self._drop_tables(con, expired)
                    self._drop_tables(con, orphaned)
                    # Registry rows for expired cohorts go with their tables;
                    # rows whose table was already gone are cleaned up too, so
                    # a replayed sweep converges instead of re-reporting ids.
                    con.execute(f"DELETE FROM {_COHORT_REGISTRY} WHERE expires_at <= ?", [cutoff])
            except duckdb.Error as exc:
                logger.error("duckdb cohort sweep failed: %s", exc)
                raise SourceUnavailableError("cohort cleanup failed at the source") from None
        finally:
            con.close()
        if expired or orphaned:
            logger.info(
                "cohort sweep%s: %d expired and %d orphaned table(s)",
                " [dry run]" if dry_run else "",
                len(expired),
                len(orphaned),
            )
        return CohortSweepResult(expired=expired, orphaned=orphaned)

    @staticmethod
    def _drop_tables(con: duckdb.DuckDBPyConnection, cohort_ids: Iterable[str]) -> None:
        for cohort_id in cohort_ids:
            if not _COHORT_ID_RE.match(cohort_id):  # defense in depth: never interpolate raw
                continue
            con.execute(f"DROP TABLE IF EXISTS {_COHORT_SCHEMA}.{cohort_id}")

    def _inventory_sync(self) -> CohortInventory:
        con = self._connect(read_only=True)
        try:
            if not self._schema_exists(con, _COHORT_SCHEMA):
                return CohortInventory(tables=0, registered=0, rows=0)
            try:
                tables = self._cohort_table_names(con)
                rows = 0
                for name in tables:
                    row = con.execute(f'SELECT count(*) FROM {_COHORT_SCHEMA}."{name}"').fetchone()
                    rows += int(row[0]) if row is not None else 0
                registered = 0
                if self._table_exists(con, "registry"):
                    row = con.execute(f"SELECT count(*) FROM {_COHORT_REGISTRY}").fetchone()
                    registered = int(row[0]) if row is not None else 0
            except duckdb.Error as exc:
                logger.error("duckdb cohort inventory failed: %s", exc)
                raise SourceUnavailableError("cohort inventory failed at the source") from None
        finally:
            con.close()
        return CohortInventory(tables=len(tables), registered=registered, rows=rows)
