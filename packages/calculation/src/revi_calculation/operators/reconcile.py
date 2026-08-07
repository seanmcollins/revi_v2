"""The reconciliation invariant (design §7.8).

When a drill-down decomposes an aggregate the analyst was shown, children
must sum to the parent within suppression tolerance, at the shared
watermark. Failure is *flagged in the answer* (``RECONCILIATION_FAILED``),
never silently displayed — this check is the single cheapest trust-building
mechanism in the conversational layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from revi_calculation.operators.base import OperatorVersion, as_decimal, combine_meta
from revi_kernel.frame import EvidenceFrame

RECONCILE_OP = OperatorVersion("reconcile", "1.0.0")


class ReconciliationStatus(StrEnum):
    PASSED = "passed"
    PASSED_WITH_SUPPRESSION = "passed_with_suppression"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MeasureReconciliation:
    measure: str
    parent_total: Decimal
    children_total: Decimal
    difference: Decimal
    tolerance: Decimal
    passed: bool


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    status: ReconciliationStatus
    measures: tuple[MeasureReconciliation, ...]
    operator: str = str(RECONCILE_OP)

    @property
    def passed(self) -> bool:
        return self.status is not ReconciliationStatus.FAILED


def reconcile(
    parent: EvidenceFrame,
    children: EvidenceFrame,
    *,
    measures: tuple[str, ...],
    suppression_allowance: Decimal = Decimal(0),
) -> ReconciliationResult:
    """Check that child cells sum to the parent total per measure.

    ``parent`` is expected to be a single-row (or totals-bearing) frame;
    when it has multiple rows the parent total is the column sum, which is
    correct for additive component columns and wrong for ratio columns —
    callers reconcile components, never ratios (the slicing law's dual).

    ``suppression_allowance`` is the maximum absolute mass the suppressed
    cells could carry (supplied by the suppression policy). With zero
    suppressed cells the tolerance is exactly zero.
    """
    combine_meta(parent, children)  # watermark agreement check

    suppressed = children.suppressed_cells + parent.suppressed_cells
    tolerance = suppression_allowance if suppressed > 0 else Decimal(0)

    results: list[MeasureReconciliation] = []
    for measure in measures:
        parent_total = _column_sum(parent, measure)
        children_total = _column_sum(children, measure)
        difference = children_total - parent_total
        passed = abs(difference) <= tolerance
        results.append(
            MeasureReconciliation(
                measure=measure,
                parent_total=parent_total,
                children_total=children_total,
                difference=difference,
                tolerance=tolerance,
                passed=passed,
            )
        )

    if all(r.passed for r in results):
        status = (
            ReconciliationStatus.PASSED_WITH_SUPPRESSION
            if suppressed > 0
            else ReconciliationStatus.PASSED
        )
    else:
        status = ReconciliationStatus.FAILED
    return ReconciliationResult(status=status, measures=tuple(results))


def _column_sum(frame: EvidenceFrame, measure: str) -> Decimal:
    idx = frame.schema.index_of(measure)
    total = Decimal(0)
    for row in frame.rows:
        v = row[idx]
        if v is not None:
            total += as_decimal(v, context=measure)
    return total
