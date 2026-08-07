"""Thin re-export — the metric-contract model lives in ``revi_calculation_contracts.contract``.

The contract model is the calculation capability's public contract: packs
hold metric contracts, and ``revi_pack`` may not import ``revi_calculation``
(capability independence, import-linter enforced). Existing imports of
``revi_calculation.contract`` keep working through this shim.
"""

from revi_calculation_contracts.contract import (
    Count,
    CountDistinct,
    Filtered,
    MeasureExpr,
    MetricContract,
    MetricKind,
    MetricUnit,
    SignConvention,
    Sum,
    denominator_column,
    numerator_column,
)

__all__ = [
    "Count",
    "CountDistinct",
    "Filtered",
    "MeasureExpr",
    "MetricContract",
    "MetricKind",
    "MetricUnit",
    "SignConvention",
    "Sum",
    "denominator_column",
    "numerator_column",
]
