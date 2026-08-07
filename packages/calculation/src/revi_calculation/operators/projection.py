"""``project_lagged_realization`` v0 — deterministic cash outlook.

Methodology: docs/operator-algebra-v0.md §2. This is not a forecast model:
it is arithmetic over empirical realization curves, presented as a
DERIVED-grade estimate with explicit drivers and an honest refusal path
(``INSUFFICIENT_EVIDENCE``) when curve coverage is inadequate.
"""

from __future__ import annotations

from decimal import Decimal

from revi_calculation.operators.base import (
    OperatorVersion,
    as_decimal,
    output_frame,
    round_half_up_int,
    rows_by_key,
)
from revi_kernel.errors import InsufficientEvidenceError
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameRow
from revi_kernel.grades import EvidenceGrade, min_grade
from revi_kernel.refs import DimensionRef, MetricRef

PROJECT_OP = OperatorVersion("project_lagged_realization", "1.0.0")

TOTAL_LABEL = "__total__"

# Column conventions (produced by the cash_outlook playbook's probes):
# inventory: dims (payer, age_bucket) + expected_open_cents
# curves:    dims (payer, age_bucket) + realize_frac / realize_frac_low / realize_frac_high
# inflow:    dims (payer) + weekly_expected_cents + new_realize_frac{,_low,_high}
# baseline:  dims (payer) + baseline_cash_cents


def project_lagged_realization(
    inventory: EvidenceFrame,
    curves: EvidenceFrame,
    inflow: EvidenceFrame,
    baseline: EvidenceFrame,
    *,
    horizon_weeks: int,
    coverage_min: Decimal = Decimal("0.8"),
) -> EvidenceFrame:
    """Project posted cash over the horizon from in-flight inventory plus
    assumed inflow, using per-payer empirical realization curves.

    Raises ``InsufficientEvidenceError`` when the curves cover less than
    ``coverage_min`` of inventory dollars (per payer), naming the payers —
    the honest non-answer path, never an extrapolation.
    """
    if horizon_weeks <= 0:
        raise ValueError("horizon_weeks must be positive")

    curve_by_cell = rows_by_key(curves, ("payer", "age_bucket"))
    inflow_by_payer = rows_by_key(inflow, ("payer",))
    baseline_by_payer = rows_by_key(baseline, ("payer",))

    c_frac = curves.schema.index_of("realize_frac")
    c_low = curves.schema.index_of("realize_frac_low")
    c_high = curves.schema.index_of("realize_frac_high")
    i_expected = inventory.schema.index_of("expected_open_cents")
    i_payer = inventory.schema.index_of("payer")
    i_bucket = inventory.schema.index_of("age_bucket")
    f_weekly = inflow.schema.index_of("weekly_expected_cents")
    f_frac = inflow.schema.index_of("new_realize_frac")
    f_low = inflow.schema.index_of("new_realize_frac_low")
    f_high = inflow.schema.index_of("new_realize_frac_high")
    b_cash = baseline.schema.index_of("baseline_cash_cents")

    per_payer: dict[str, dict[str, Decimal]] = {}
    uncovered: dict[str, Decimal] = {}
    inventory_total: dict[str, Decimal] = {}

    def bucket_of(payer: Scalar) -> dict[str, Decimal]:
        key = str(payer)
        if key not in per_payer:
            per_payer[key] = {
                "inflight": Decimal(0),
                "inflight_low": Decimal(0),
                "inflight_high": Decimal(0),
                "inflow": Decimal(0),
                "inflow_low": Decimal(0),
                "inflow_high": Decimal(0),
                "baseline": Decimal(0),
            }
        return per_payer[key]

    for row in inventory.rows:
        payer, age_bucket = row[i_payer], row[i_bucket]
        expected = row[i_expected]
        if expected is None:
            continue
        expected_d = as_decimal(expected, context="expected_open_cents")
        inventory_total[str(payer)] = inventory_total.get(str(payer), Decimal(0)) + expected_d
        curve = curve_by_cell.get((payer, age_bucket))
        if curve is None:
            uncovered[str(payer)] = uncovered.get(str(payer), Decimal(0)) + expected_d
            continue
        acc = bucket_of(payer)
        acc["inflight"] += expected_d * as_decimal(curve[c_frac], context="realize_frac")
        acc["inflight_low"] += expected_d * as_decimal(curve[c_low], context="realize_frac_low")
        acc["inflight_high"] += expected_d * as_decimal(curve[c_high], context="realize_frac_high")

    insufficient = {
        payer: uncovered_d / inventory_total[payer]
        for payer, uncovered_d in uncovered.items()
        if inventory_total.get(payer)
        and (Decimal(1) - uncovered_d / inventory_total[payer]) < coverage_min
    }
    if insufficient:
        raise InsufficientEvidenceError(
            "realization curves cover too little of the open inventory to project",
            details={
                "uncovered_share_by_payer": {
                    p: str(v.quantize(Decimal("0.001"))) for p, v in insufficient.items()
                },
                "coverage_min": str(coverage_min),
                "resolution": "extend the remit history window or exclude these payers explicitly",
            },
        )

    horizon = Decimal(horizon_weeks)
    for (payer,), row in inflow_by_payer.items():
        weekly = row[f_weekly]
        if weekly is None:
            continue
        acc = bucket_of(payer)
        weekly_d = as_decimal(weekly, context="weekly_expected_cents") * horizon
        acc["inflow"] += weekly_d * as_decimal(row[f_frac], context="new_realize_frac")
        acc["inflow_low"] += weekly_d * as_decimal(row[f_low], context="new_realize_frac_low")
        acc["inflow_high"] += weekly_d * as_decimal(row[f_high], context="new_realize_frac_high")

    for (payer,), row in baseline_by_payer.items():
        cash = row[b_cash]
        if cash is not None:
            bucket_of(payer)["baseline"] = as_decimal(cash, context="baseline_cash_cents")

    payer_dim = DimensionRef("payer")
    money = "money_cents"
    columns = (
        FrameColumn("payer", payer_dim, None, None),
        FrameColumn("projected_cash_cents", MetricRef("projected_cash"), None, money),
        FrameColumn("projected_low_cents", MetricRef("projected_cash_low"), None, money),
        FrameColumn("projected_high_cents", MetricRef("projected_cash_high"), None, money),
        FrameColumn("driver_inflight_cents", MetricRef("projected_from_inflight"), None, money),
        FrameColumn("driver_assumed_inflow_cents", MetricRef("projected_from_inflow"), None, money),
        FrameColumn("baseline_cash_cents", MetricRef("baseline_cash"), None, money),
    )

    rows: list[FrameRow] = []
    totals = {k: Decimal(0) for k in next(iter(per_payer.values()))} if per_payer else {}
    for payer in sorted(per_payer):
        acc = per_payer[payer]
        for k, v in acc.items():
            totals[k] += v
        rows.append(_payer_row(payer, acc))
    if per_payer:
        rows.append(_payer_row(TOTAL_LABEL, totals))

    frame = output_frame(PROJECT_OP, columns, tuple(rows), inventory, curves, inflow, baseline)
    # projections are estimates: DERIVED at best, weaker if inputs are weaker
    grade = min_grade(EvidenceGrade.DERIVED, frame.evidence_grade)
    return EvidenceFrame(
        schema=frame.schema,
        rows=frame.rows,
        watermark=frame.watermark,
        provenance=frame.provenance,
        evidence_grade=grade,
        truncated=frame.truncated,
        suppressed_cells=frame.suppressed_cells,
    )


def _payer_row(payer: str, acc: dict[str, Decimal]) -> FrameRow:
    projected = acc["inflight"] + acc["inflow"]
    low = acc["inflight_low"] + acc["inflow_low"]
    high = acc["inflight_high"] + acc["inflow_high"]
    return (
        payer,
        round_half_up_int(projected),
        round_half_up_int(low),
        round_half_up_int(high),
        round_half_up_int(acc["inflight"]),
        round_half_up_int(acc["inflow"]),
        round_half_up_int(acc["baseline"]),
    )
