"""The versioned, closed transform-operator set (design §6.5).

Operators are kernel code: packs cannot define arithmetic, and the learning
loop cannot propose new operators.
"""

from revi_calculation.operators.base import FrameMeta, OperatorVersion, combine_meta
from revi_calculation.operators.basic import (
    COMPARE_OP,
    PIVOT_OP,
    RANK_OP,
    RATIO_OP,
    SHARE_OP,
    TOP_K_OP,
    compare,
    pivot,
    rank,
    ratio,
    share_of_total,
    top_k,
)
from revi_calculation.operators.decompose import (
    COMPONENT_RATE,
    COMPONENT_VOLUME_MIX,
    COMPONENT_VOLUME_SCALE,
    DECOMPOSE_OP,
    decompose,
)
from revi_calculation.operators.panel import (
    PANEL_OP,
    RANK_UNIT,
    panel,
)
from revi_calculation.operators.projection import (
    PROJECT_OP,
    TOTAL_LABEL,
    project_lagged_realization,
)
from revi_calculation.operators.reconcile import (
    RECONCILE_OP,
    MeasureReconciliation,
    ReconciliationResult,
    ReconciliationStatus,
    reconcile,
)

__all__ = [
    "COMPARE_OP",
    "COMPONENT_RATE",
    "COMPONENT_VOLUME_MIX",
    "COMPONENT_VOLUME_SCALE",
    "DECOMPOSE_OP",
    "PANEL_OP",
    "PIVOT_OP",
    "PROJECT_OP",
    "RANK_OP",
    "RANK_UNIT",
    "RATIO_OP",
    "RECONCILE_OP",
    "SHARE_OP",
    "TOP_K_OP",
    "TOTAL_LABEL",
    "FrameMeta",
    "MeasureReconciliation",
    "OperatorVersion",
    "ReconciliationResult",
    "ReconciliationStatus",
    "combine_meta",
    "compare",
    "decompose",
    "panel",
    "pivot",
    "project_lagged_realization",
    "rank",
    "ratio",
    "reconcile",
    "share_of_total",
    "top_k",
]
