"""DuckDB AnalyticalRepository adapter.

Probe compilation, as-of snapshot resolution, cohort materialization, and
the detected-anomaly source behind the portfolio surface.
"""

from revi_connector_duckdb.anomalies import DuckDbAnomalySource
from revi_connector_duckdb.repository import DuckDbAnalyticalRepository

__all__ = ["DuckDbAnalyticalRepository", "DuckDbAnomalySource"]
