"""DuckDB AnalyticalRepository adapter.

Probe compilation, as-of snapshot resolution, cohort materialization, and
the detected-anomaly source behind the portfolio surface.
"""

from revi_connector_duckdb.anomalies import DuckDbAnomalySource
from revi_connector_duckdb.repository import (
    CohortInventory,
    CohortSweepResult,
    DuckDbAnalyticalRepository,
)

__all__ = [
    "CohortInventory",
    "CohortSweepResult",
    "DuckDbAnalyticalRepository",
    "DuckDbAnomalySource",
]
