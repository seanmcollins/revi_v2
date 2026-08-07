"""``decompose`` v0 — midpoint (Bennet) volume/rate/mix attribution.

Methodology: docs/operator-algebra-v0.md §1. Properties (all tested):
- exact additivity: per-cell contributions sum to the cell's value delta;
- symmetry: swapping periods negates every contribution;
- cents-exact: largest-remainder rounding keeps sums exact in integer cents;
- grade law: output carries the weakest input grade.
"""

from __future__ import annotations

from decimal import Decimal

from revi_calculation.operators.base import (
    OperatorVersion,
    as_decimal,
    dimension_names,
    output_frame,
    rows_by_key,
)
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameRow
from revi_kernel.refs import DimensionRef

DECOMPOSE_OP = OperatorVersion("decompose", "1.0.0")

_COMPONENT_DIM = DimensionRef("decompose_component")
COMPONENT_VOLUME_SCALE = "volume_scale"
COMPONENT_VOLUME_MIX = "volume_mix"
COMPONENT_RATE = "rate"
_COMPONENTS = (COMPONENT_VOLUME_SCALE, COMPONENT_VOLUME_MIX, COMPONENT_RATE)

_TWO = Decimal(2)


def _largest_remainder_round(exact: dict[str, Decimal], target: int) -> dict[str, int]:
    """Round each component to integer cents so they sum exactly to target.

    Fractional-cent leftovers go to the largest fractional parts
    (deterministic key tie-break). If the exact contributions disagree with
    ``target`` by whole cents (only possible for malformed inputs where a
    cell carries value with zero volume, breaking value = volume x rate),
    the whole-cent discrepancy is assigned to the rate component so the
    cents-exact invariant holds unconditionally.
    """
    floored = {k: int(v.to_integral_value(rounding="ROUND_FLOOR")) for k, v in exact.items()}
    remainder = target - sum(floored.values())
    out = dict(floored)
    n = len(out)
    fractions = sorted(exact.keys(), key=lambda k: (exact[k] - floored[k], k), reverse=True)
    if 0 <= remainder <= n:
        for key in fractions[:remainder]:
            out[key] += 1
    else:
        # whole-cent discrepancy: expected fractional remainder is 0..n
        out[COMPONENT_RATE] += remainder
    return out


def decompose(
    current: EvidenceFrame,
    prior: EvidenceFrame,
    *,
    volume: str,
    value: str,
    cells: tuple[str, ...] | None = None,
) -> EvidenceFrame:
    """Attribute Δvalue between periods to volume-scale, mix, and rate.

    Both frames need per-cell additive columns ``volume`` (count) and
    ``value`` (integer cents). Cells absent on one side enter with
    volume 0 / value 0 (an appearing or disappearing cell is pure
    volume/mix movement).
    """
    keys = cells if cells is not None else dimension_names(current)
    cur = rows_by_key(current, keys)
    pri = rows_by_key(prior, keys)
    all_keys = list(cur.keys()) + [k for k in pri if k not in cur]

    v_idx_c = current.schema.index_of(volume)
    x_idx_c = current.schema.index_of(value)
    v_idx_p = prior.schema.index_of(volume)
    x_idx_p = prior.schema.index_of(value)

    def side(row: FrameRow | None, v_idx: int, x_idx: int) -> tuple[Decimal, Decimal]:
        if row is None:
            return Decimal(0), Decimal(0)
        v = row[v_idx]
        x = row[x_idx]
        return (
            as_decimal(v, context=volume) if v is not None else Decimal(0),
            as_decimal(x, context=value) if x is not None else Decimal(0),
        )

    # totals for the mix split
    v0_total = sum((side(pri.get(k), v_idx_p, x_idx_p)[0] for k in all_keys), Decimal(0))
    v1_total = sum((side(cur.get(k), v_idx_c, x_idx_c)[0] for k in all_keys), Decimal(0))
    dv_total = v1_total - v0_total
    v_bar_total = (v0_total + v1_total) / _TWO

    value_col = current.schema.columns[x_idx_c]
    columns = (
        *(current.schema.columns[current.schema.index_of(k)] for k in keys),
        FrameColumn("component", _COMPONENT_DIM, None, None),
        FrameColumn("contribution", value_col.ref, value_col.contract_version, value_col.unit),
        FrameColumn("delta_total", value_col.ref, value_col.contract_version, value_col.unit),
    )

    out_rows: list[FrameRow] = []
    for key in all_keys:
        v0, x0 = side(pri.get(key), v_idx_p, x_idx_p)
        v1, x1 = side(cur.get(key), v_idx_c, x_idx_c)
        r0 = x0 / v0 if v0 else Decimal(0)
        r1 = x1 / v1 if v1 else Decimal(0)
        dv, dr = v1 - v0, r1 - r0
        v_bar, r_bar = (v0 + v1) / _TWO, (r0 + r1) / _TWO

        rate_contribution = v_bar * dr
        # mix split of the volume contribution (dv * r_bar):
        s0 = v0 / v0_total if v0_total else Decimal(0)
        s1 = v1 / v1_total if v1_total else Decimal(0)
        s_bar, ds = (s0 + s1) / _TWO, s1 - s0
        scale_contribution = dv_total * s_bar * r_bar
        mix_contribution = v_bar_total * ds * r_bar
        # fold the second-order cross term into mix so scale+mix == dv*r_bar
        cross = dv * r_bar - scale_contribution - mix_contribution
        mix_contribution += cross

        delta_cents = int(x1 - x0)
        rounded = _largest_remainder_round(
            {
                COMPONENT_VOLUME_SCALE: scale_contribution,
                COMPONENT_VOLUME_MIX: mix_contribution,
                COMPONENT_RATE: rate_contribution,
            },
            delta_cents,
        )
        for component in _COMPONENTS:
            out_rows.append((*key, component, rounded[component], delta_cents))

    # order by |contribution| descending, deterministic tie-break on key+component
    contribution_idx = len(keys) + 1

    def order(row: FrameRow) -> tuple[Decimal, tuple[str, ...], str]:
        c = row[contribution_idx]
        magnitude = -abs(as_decimal(c, context="contribution")) if c is not None else Decimal(0)
        return (magnitude, tuple(str(v) for v in row[: len(keys)]), str(row[len(keys)]))

    out_rows.sort(key=order)
    return output_frame(DECOMPOSE_OP, columns, tuple(out_rows), current, prior)



VOLUME_SCALE_LABEL: dict[str, str] = {
    COMPONENT_VOLUME_SCALE: "overall volume",
    COMPONENT_VOLUME_MIX: "mix shift",
    COMPONENT_RATE: "rate",
}
