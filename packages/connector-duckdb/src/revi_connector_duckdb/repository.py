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
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import duckdb

from revi_calculation_contracts.contract import MetricContract
from revi_catalog_contracts.masking import mask_value
from revi_catalog_contracts.model import CatalogSnapshot, PhiClass
from revi_connector_duckdb.compile import CompiledQuery, ProbeCompiler
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
_COHORT_ID_RE = re.compile(r"^cohort_[0-9a-f]{12}$")

_WATERMARKS_SQL = (
    "SELECT watermark_id, schema_name, loaded_at, newest_data_date "
    "FROM main.watermarks ORDER BY loaded_at, watermark_id"
)


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
        self._cohorts: dict[str, CohortMaterialization] = {}

    # ----------------------------------------------------------------- port

    def capabilities(self) -> RepositoryCapabilities:
        return RepositoryCapabilities(
            as_of_reads=True,
            cohort_semijoin=True,
            max_cohort_size=self._max_cohort_size,
            having_pushdown=True,
            server_side_top_n=True,
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
        """Drop cohort tables whose TTL elapsed at ``now`` (scheduler hook).
        Returns the dropped cohort ids."""
        return await asyncio.to_thread(self._drop_expired_sync, now)

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

    def _materialize_sync(
        self, definition: CohortDefinition, watermark: DataWatermark
    ) -> CohortMaterialization:
        entity, where_sql, params = self._compiler.compile_cohort_selection(definition, watermark=watermark)
        cohort_id = f"cohort_{uuid.uuid4().hex[:12]}"
        con = self._connect(read_only=False)  # short-lived read-write: cohort DDL only
        try:
            schema = self._schema_for(con, watermark)
            table = f"{_COHORT_SCHEMA}.{cohort_id}"
            try:
                con.execute(f"CREATE SCHEMA IF NOT EXISTS {_COHORT_SCHEMA}")
                con.execute(
                    f'CREATE TABLE {table} AS SELECT DISTINCT "{entity.primary_key}" AS entity_id '
                    f'FROM "{schema}"."{entity.base_view}" WHERE {where_sql}',
                    list(params),
                )
                row = con.execute(f"SELECT count(*) FROM {table}").fetchone()
                size = int(row[0]) if row is not None else 0
                if size > self._max_cohort_size:
                    con.execute(f"DROP TABLE IF EXISTS {table}")
                    raise QueryBudgetExceededError(
                        f"cohort size {size} exceeds the repository limit {self._max_cohort_size}",
                        details={"size": size, "max_cohort_size": self._max_cohort_size},
                    )
            except duckdb.Error as exc:
                logger.error("duckdb cohort materialization failed: %s", exc)
                raise SourceUnavailableError("cohort materialization failed at the source") from None
        finally:
            con.close()
        materialization = CohortMaterialization(
            cohort_id=cohort_id,
            watermark=watermark,
            entity_ids_ref=f"{_COHORT_SCHEMA}.{cohort_id}",
            size=size,
            created_at=datetime.now(UTC),
            ttl_seconds=_COHORT_TTL_SECONDS,
        )
        self._cohorts[cohort_id] = materialization
        return materialization

    def _drop_expired_sync(self, now: datetime) -> tuple[str, ...]:
        expired = [
            m
            for m in self._cohorts.values()
            if m.created_at + timedelta(seconds=m.ttl_seconds) <= now
        ]
        if not expired:
            return ()
        con = self._connect(read_only=False)
        dropped: list[str] = []
        try:
            for materialization in expired:
                if not _COHORT_ID_RE.match(materialization.cohort_id):  # defense in depth
                    continue
                try:
                    con.execute(f"DROP TABLE IF EXISTS {_COHORT_SCHEMA}.{materialization.cohort_id}")
                except duckdb.Error as exc:
                    logger.error("duckdb cohort drop failed: %s", exc)
                    raise SourceUnavailableError("cohort cleanup failed at the source") from None
                del self._cohorts[materialization.cohort_id]
                dropped.append(materialization.cohort_id)
        finally:
            con.close()
        return tuple(dropped)
