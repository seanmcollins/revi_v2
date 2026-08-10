"""Read-only DuckDB access for the audit path.

Nothing here knows what a probe is. It opens the warehouse file, resolves a
watermark id to its snapshot schema, introspects base-view columns (so the
deriver can refuse an unresolvable field instead of throwing SQL errors), and
runs scalar queries.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb

from revi_warehouse_diff.governed import DEFAULT_WAREHOUSE, Catalog


@dataclass(frozen=True)
class WatermarkRow:
    watermark_id: str
    schema_name: str
    loaded_at: dt.datetime
    newest_data_date: dt.date


class Warehouse:
    """A read-only handle on ``data/revi_warehouse.duckdb``."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_WAREHOUSE
        if not self.path.is_file():
            raise FileNotFoundError(
                f"warehouse missing at {self.path} — run: make warehouse"
            )
        self._con = duckdb.connect(str(self.path), read_only=True)
        self.queries = 0

    def close(self) -> None:
        self._con.close()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def watermarks(self) -> dict[str, WatermarkRow]:
        rows = self._con.execute(
            "SELECT watermark_id, schema_name, loaded_at, newest_data_date FROM main.watermarks"
        ).fetchall()
        return {r[0]: WatermarkRow(r[0], r[1], r[2], r[3]) for r in rows}

    def columns(self, schema: str, catalog: Catalog) -> dict[str, frozenset[str]]:
        """entity -> the columns its catalog base view actually carries."""
        out: dict[str, frozenset[str]] = {}
        for entity in catalog.entities:
            view = catalog.base_view(entity)
            if view is None:
                continue
            rows = self._con.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = ? AND table_name = ?",
                [schema, view],
            ).fetchall()
            out[entity] = frozenset(str(r[0]) for r in rows)
        return out

    def materialized_cohorts(self) -> frozenset[str]:
        """Cohort tables that still exist (the TTL sweep drops expired ones)."""
        rows = self._con.execute(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema = 'cohort_store'"
        ).fetchall()
        return frozenset(f"{r[0]}.{r[1]}" for r in rows)

    def scalar(self, sql: str) -> Decimal | None:
        self.queries += 1
        row = self._con.execute(sql).fetchone()
        if row is None or row[0] is None:
            return None
        return Decimal(str(row[0]))

    def rows(self, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        self.queries += 1
        return self._con.execute(sql, params or []).fetchall()
