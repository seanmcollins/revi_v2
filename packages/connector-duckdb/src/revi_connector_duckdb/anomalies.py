"""DuckDB-backed ``AnomalySource``: reads ``<snapshot>.detected_anomalies``.

The warehouse generator persists planted scenarios as-if an external
anomaly-detection system wrote them, one table per snapshot schema — so
reading per-snapshot naturally excludes anomalies that are absent (or
self-resolved away) at the pinned watermark. A snapshot schema need not carry
the table: its absence yields an EMPTY result, never an error (callers
surface a warning).

Row shape: anomaly_id, detected_at, category, title, description,
metric_id, dimensions (JSON object of dimension → value), window_start,
window_end, impact_cents, severity, confidence, status, evidence (JSON
object of facts).

**Onset.** For this feed ``window_start`` *is* the onset of the observation
window: each scenario is planted with an ``onset`` date written as the window
start, and the detector only counts events inside
``window_start..window_end``. ``detected_at`` is the load timestamp of the
snapshot that observed it — identical for every row — so it says when the
feed ran, never when the problem began. The source therefore publishes an
``onset_date`` evidence fact derived from ``window_start``.

That derivation belongs in the source, not the consumer: the source is the
only component that knows this feed's row shape, and the governed
``anomaly_priority`` formula must stay expressed in evidence facts rather
than learning DuckDB column names. An explicit ``onset_date`` already present
in the row's evidence always wins — a detection feed that knows its own onset
must not be overwritten by this derivation.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from revi_investigation.application.ports import AnomalyRecord
from revi_kernel.errors import SourceUnavailableError, WatermarkStaleError
from revi_kernel.watermark import DataWatermark

logger = logging.getLogger(__name__)

_WATERMARKS_SQL = (
    "SELECT watermark_id, schema_name FROM main.watermarks ORDER BY loaded_at, watermark_id"
)
_COLUMNS = (
    "anomaly_id, detected_at, category, title, description, metric_id, dimensions, "
    "window_start, window_end, impact_cents, severity, confidence, status, evidence"
)


def _json_object(raw: object) -> dict[str, Any]:
    if isinstance(raw, dict):
        return {str(k): v for k, v in raw.items()}
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return {str(k): v for k, v in parsed.items()}
    return {}


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise SourceUnavailableError("detected_anomalies returned a non-date window bound")


class DuckDbAnomalySource:
    """``AnomalySource`` over the generated warehouse's anomaly tables."""

    def __init__(self, warehouse_path: str | Path) -> None:
        self._path = str(Path(warehouse_path))

    async def list_anomalies(self, watermark: DataWatermark) -> tuple[AnomalyRecord, ...]:
        return await asyncio.to_thread(self._list_sync, watermark)

    # ------------------------------------------------------------ internals

    def _list_sync(self, watermark: DataWatermark) -> tuple[AnomalyRecord, ...]:
        try:
            con = duckdb.connect(self._path, read_only=True)
        except duckdb.Error as exc:
            logger.error("duckdb connect failed for %s: %s", self._path, exc)
            raise SourceUnavailableError("analytical source is unavailable") from None
        try:
            try:
                rows = con.execute(_WATERMARKS_SQL).fetchall()
            except duckdb.Error as exc:
                logger.error("duckdb watermark listing failed: %s", exc)
                raise SourceUnavailableError("analytical source is unavailable") from None
            schema: str | None = None
            for row in rows:
                if row[0] == watermark.id:
                    schema = str(row[1])
                    break
            if schema is None:
                raise WatermarkStaleError(
                    f"watermark {watermark.id!r} is not a completed load",
                    details={"watermark": watermark.id},
                )
            try:
                raw = con.execute(
                    f'SELECT {_COLUMNS} FROM "{schema}".detected_anomalies '
                    "ORDER BY impact_cents DESC, anomaly_id"
                ).fetchall()
            except duckdb.CatalogException:
                # a schema without the table is an empty portfolio, not an error
                logger.warning(
                    "no detected_anomalies table in schema %s — serving an empty portfolio",
                    schema,
                )
                return ()
            except duckdb.Error as exc:
                logger.error("duckdb anomaly read failed: %s", exc)
                raise SourceUnavailableError("anomaly read failed at the source") from None
        finally:
            con.close()

        records: list[AnomalyRecord] = []
        for row in raw:
            detected_at = row[1]
            if isinstance(detected_at, date) and not isinstance(detected_at, datetime):
                detected_at = datetime(detected_at.year, detected_at.month, detected_at.day)
            if not isinstance(detected_at, datetime):
                raise SourceUnavailableError("detected_anomalies returned a non-datetime")
            dimensions = _json_object(row[6])
            window_start = _as_date(row[7])
            evidence = _json_object(row[13])
            # window_start is this feed's onset (see the module docstring);
            # never clobber an onset the feed stated for itself
            evidence.setdefault("onset_date", window_start.isoformat())
            records.append(
                AnomalyRecord(
                    anomaly_id=str(row[0]),
                    detected_at=detected_at,
                    category=str(row[2]),
                    title=str(row[3]),
                    description=str(row[4]),
                    metric_id=str(row[5]),
                    dimensions=tuple(sorted((k, str(v)) for k, v in dimensions.items())),
                    window_start=window_start,
                    window_end=_as_date(row[8]),
                    impact_cents=int(row[9]),
                    severity=str(row[10]),
                    confidence=str(row[11]),
                    status=str(row[12]),
                    evidence=evidence,
                )
            )
        return tuple(records)
