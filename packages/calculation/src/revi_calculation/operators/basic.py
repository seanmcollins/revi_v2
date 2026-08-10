"""Core transforms: ratio, compare, share_of_total, top_k, rank, pivot.

The slicing law is structural here: ``ratio`` computes ratio-of-sums per
cell from additive component columns — average-of-ratios has no code path.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from revi_calculation.operators.base import (
    OperatorVersion,
    as_decimal,
    dimension_names,
    measure_names,
    output_frame,
    quantize_ratio,
    rows_by_key,
)
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameRow
from revi_kernel.refs import MetricRef

RATIO_OP = OperatorVersion("ratio", "1.0.0")
COMPARE_OP = OperatorVersion("compare", "1.0.0")
SHARE_OP = OperatorVersion("share_of_total", "1.0.0")
TOP_K_OP = OperatorVersion("top_k", "1.0.0")
RANK_OP = OperatorVersion("rank", "1.0.0")
PIVOT_OP = OperatorVersion("pivot", "1.0.0")

_ZERO_FILL_UNITS = {"money_cents", "count"}


#: What a ratio-of-sums measures when the metric contract declares nothing
#: else. It is a fallback, never an assertion about the metric.
DEFAULT_RATIO_UNIT = "ratio"


def ratio(
    frame: EvidenceFrame,
    *,
    numerator: str,
    denominator: str,
    out: str,
    out_ref: MetricRef,
    contract_version: int | None = None,
    unit: str | None = None,
) -> EvidenceFrame:
    """Per-cell ratio-of-sums. Denominator 0 (or NULL) yields NULL, never ∞.

    ``unit`` is the metric CONTRACT's declared unit, threaded in by the
    caller that resolved the contract. A ratio is a shape, not a unit: the
    days-unit metrics (``days_in_ar``, ``avg_days_to_pay``,
    ``charge_lag_days``, ``bill_lag_days``) are numerator/denominator shaped
    like every percentage, and hardcoding ``unit="ratio"`` here renders them
    down the percentage path ("15,941.2%" for days in A/R).

    What the quotient means is a contract declaration, carried on the output
    column so every downstream operator, finding, chart and export reads it
    off the frame instead of re-deriving it from the arithmetic — the same
    rule the additive path follows.
    """
    n_idx = frame.schema.index_of(numerator)
    d_idx = frame.schema.index_of(denominator)
    declared = unit or DEFAULT_RATIO_UNIT
    columns = (*frame.schema.columns, FrameColumn(out, out_ref, contract_version, unit=declared))
    rows: list[FrameRow] = []
    for row in frame.rows:
        num, den = row[n_idx], row[d_idx]
        if num is None or den is None or as_decimal(den, context=denominator) == 0:
            value: Scalar = None
        else:
            value = quantize_ratio(as_decimal(num, context=numerator) / as_decimal(den, context=denominator))
        rows.append((*row, value))
    return output_frame(RATIO_OP, columns, tuple(rows), frame)


def compare(
    current: EvidenceFrame,
    prior: EvidenceFrame,
    *,
    join_on: tuple[str, ...] | None = None,
    measures: tuple[str, ...] | None = None,
) -> EvidenceFrame:
    """Cell-aligned comparison: value, prior, delta, pct_change per measure.

    Cells present on one side only are kept: additive units (cents, counts)
    fill the missing side with 0; other units leave it NULL. ``pct_change``
    is NULL when the prior side is NULL or 0 (never a division blowup).

    Antisymmetry (property-tested): swapping current/prior negates deltas.
    """
    keys = join_on if join_on is not None else dimension_names(current)
    if set(keys) != {k for k in keys if k in prior.schema.names}:
        missing = [k for k in keys if k not in prior.schema.names]
        raise ValueError(f"prior frame lacks join columns {missing}")
    measure_list = measures if measures is not None else tuple(
        m for m in measure_names(current) if m in prior.schema.names
    )
    if not measure_list:
        raise ValueError("no shared measure columns to compare")

    cur_by_key = rows_by_key(current, keys)
    prior_by_key = rows_by_key(prior, keys)
    all_keys = list(cur_by_key.keys()) + [k for k in prior_by_key if k not in cur_by_key]

    unit_by_measure: dict[str, str | None] = {}
    columns: list[FrameColumn] = [
        current.schema.columns[current.schema.index_of(k)] for k in keys
    ]
    for m in measure_list:
        col = current.schema.columns[current.schema.index_of(m)]
        unit_by_measure[m] = col.unit
        columns.extend(
            (
                col,
                FrameColumn(f"{m}__prior", col.ref, col.contract_version, col.unit),
                FrameColumn(f"{m}__delta", col.ref, col.contract_version, col.unit),
                FrameColumn(f"{m}__pct_change", col.ref, col.contract_version, "ratio"),
            )
        )

    cur_measure_idx = {m: current.schema.index_of(m) for m in measure_list}
    prior_measure_idx = {m: prior.schema.index_of(m) for m in measure_list}

    def side_value(row: FrameRow | None, idx: dict[str, int], m: str) -> Scalar:
        if row is None:
            return 0 if unit_by_measure[m] in _ZERO_FILL_UNITS else None
        return row[idx[m]]

    rows: list[FrameRow] = []
    for key in all_keys:
        c_row = cur_by_key.get(key)
        p_row = prior_by_key.get(key)
        out: list[Scalar] = list(key)
        for m in measure_list:
            c_val = side_value(c_row, cur_measure_idx, m)
            p_val = side_value(p_row, prior_measure_idx, m)
            if c_val is None or p_val is None:
                delta: Scalar = None
            elif isinstance(c_val, Decimal) or isinstance(p_val, Decimal):
                delta = as_decimal(c_val, context=m) - as_decimal(p_val, context=m)
            else:
                delta = as_decimal(c_val, context=m) - as_decimal(p_val, context=m)
                delta = int(delta)
            if delta is None or p_val is None or as_decimal(p_val, context=m) == 0:
                pct: Scalar = None
            else:
                pct = quantize_ratio(as_decimal(delta, context=m) / as_decimal(p_val, context=m))
            out.extend((c_val, p_val, delta, pct))
        rows.append(tuple(out))
    return output_frame(COMPARE_OP, tuple(columns), tuple(rows), current, prior)


def share_of_total(
    frame: EvidenceFrame,
    *,
    measure: str,
    within: tuple[str, ...] = (),
) -> EvidenceFrame:
    """Per-row share of the measure's total (optionally within groups).

    Shares are computed over *visible* rows: with suppressed cells the
    shares of visible rows sum to < 1, which is honest — the frame's
    ``suppressed_cells`` count travels with it. Property test: shares sum
    to 1 (per group) exactly when ``suppressed_cells == 0`` and no NULLs.
    """
    m_idx = frame.schema.index_of(measure)
    group_idx = tuple(frame.schema.index_of(g) for g in within)
    totals: dict[tuple[Scalar, ...], Decimal] = {}
    for row in frame.rows:
        g = tuple(row[i] for i in group_idx)
        v = row[m_idx]
        if v is not None:
            totals[g] = totals.get(g, Decimal(0)) + as_decimal(v, context=measure)
    src = frame.schema.columns[m_idx]
    share_col = FrameColumn(f"{measure}__share", src.ref, src.contract_version, "ratio")
    columns = (*frame.schema.columns, share_col)
    rows: list[FrameRow] = []
    for row in frame.rows:
        g = tuple(row[i] for i in group_idx)
        v = row[m_idx]
        total = totals.get(g, Decimal(0))
        share: Scalar = (
            None if v is None or total == 0 else quantize_ratio(as_decimal(v, context=measure) / total)
        )
        rows.append((*row, share))
    return output_frame(SHARE_OP, columns, tuple(rows), frame)


def _sort_key(
    frame: EvidenceFrame, by: str, dims: tuple[str, ...]
) -> Callable[[FrameRow], tuple[bool, Decimal, tuple[str, ...]]]:
    by_idx = frame.schema.index_of(by)
    dim_idx = tuple(frame.schema.index_of(d) for d in dims)

    def key(row: FrameRow) -> tuple[bool, Decimal, tuple[str, ...]]:
        v = row[by_idx]
        # NULLs sort last regardless of direction; deterministic dim tie-break
        magnitude = -as_decimal(v, context=by) if v is not None else Decimal(0)
        return (v is None, magnitude, tuple(str(row[i]) for i in dim_idx))

    return key


def top_k(
    frame: EvidenceFrame,
    *,
    by: str,
    k: int,
    per_group: tuple[str, ...] | None = None,
) -> EvidenceFrame:
    """Keep the k highest rows by a measure (optionally per group).

    Descending only — "bottom k" is ``rank`` + presentation. Ties break
    deterministically on dimension values. Dropping rows sets ``truncated``.
    """
    if k <= 0:
        raise ValueError("k must be positive")
    dims = dimension_names(frame)
    sort_key = _sort_key(frame, by, dims)
    if per_group:
        group_idx = tuple(frame.schema.index_of(g) for g in per_group)
        groups: dict[tuple[Scalar, ...], list[FrameRow]] = {}
        for row in frame.rows:
            groups.setdefault(tuple(row[i] for i in group_idx), []).append(row)
        kept: list[FrameRow] = []
        for g in groups.values():
            kept.extend(sorted(g, key=sort_key)[:k])
    else:
        kept = sorted(frame.rows, key=sort_key)[:k]
    dropped = len(frame.rows) - len(kept)
    result = output_frame(TOP_K_OP, frame.schema.columns, tuple(kept), frame)
    if dropped > 0 and not result.truncated:
        result = EvidenceFrame(
            schema=result.schema,
            rows=result.rows,
            watermark=result.watermark,
            provenance=result.provenance,
            evidence_grade=result.evidence_grade,
            truncated=True,
            suppressed_cells=result.suppressed_cells,
        )
    return result


def rank(frame: EvidenceFrame, *, by: str, descending: bool = True) -> EvidenceFrame:
    """Add a 1-based ordinal rank column (deterministic dim tie-break)."""
    dims = dimension_names(frame)
    sort_key = _sort_key(frame, by, dims)
    ordered = sorted(frame.rows, key=sort_key)
    if not descending:
        non_null = [r for r in ordered if r[frame.schema.index_of(by)] is not None]
        nulls = [r for r in ordered if r[frame.schema.index_of(by)] is None]
        ordered = list(reversed(non_null)) + nulls
    positions = {id(row): i + 1 for i, row in enumerate(ordered)}
    src = frame.schema.columns[frame.schema.index_of(by)]
    columns = (*frame.schema.columns, FrameColumn(f"{by}__rank", src.ref, src.contract_version, "count"))
    rows = tuple((*row, positions[id(row)]) for row in frame.rows)
    return output_frame(RANK_OP, columns, rows, frame)


def pivot(
    frame: EvidenceFrame,
    *,
    index: tuple[str, ...],
    column: str,
    measure: str,
) -> EvidenceFrame:
    """Presentation-bound reshape: one output column per value of ``column``."""
    col_idx = frame.schema.index_of(column)
    m_idx = frame.schema.index_of(measure)
    idx_idx = tuple(frame.schema.index_of(i) for i in index)
    src = frame.schema.columns[m_idx]

    values_seen: list[Scalar] = []
    for row in frame.rows:
        v = row[col_idx]
        if v not in values_seen:
            values_seen.append(v)
    values_sorted = sorted(values_seen, key=lambda v: (v is None, str(v)))

    cells: dict[tuple[Scalar, ...], dict[Scalar, Scalar]] = {}
    order: list[tuple[Scalar, ...]] = []
    for row in frame.rows:
        key = tuple(row[i] for i in idx_idx)
        if key not in cells:
            cells[key] = {}
            order.append(key)
        if row[col_idx] in cells[key]:
            raise ValueError(f"pivot cell collision at {key!r} / {row[col_idx]!r}")
        cells[key][row[col_idx]] = row[m_idx]

    columns = tuple(frame.schema.columns[frame.schema.index_of(i)] for i in index) + tuple(
        FrameColumn(f"{measure}[{v}]", src.ref, src.contract_version, src.unit) for v in values_sorted
    )
    rows = tuple(
        (*key, *(cells[key].get(v) for v in values_sorted)) for key in order
    )
    return output_frame(PIVOT_OP, columns, rows, frame)
