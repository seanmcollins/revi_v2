"""Two invariants every published chart must hold (round-4 R4-09, R4-14).

The chart builder picks an x and a series from a frame's dimension columns
and then writes one wire row per FRAME row. Nothing checked that those two
axes actually key the rows — and when a frame carries a third grouping
column, they do not:

* live, ``denial_concentration`` published 30 rows totalling $441,807.98
  over ``x=month`` (3 values) by ``series=payer`` (1 value): 3 distinct keys.
  A renderer keying rows by ``(x, series)`` keeps the last write and drops
  99.2% of the money — $848.50 in the May cell where the true May total is
  $160,744.15, a 190x understatement, under a preamble carrying the
  watermark, the pack version, the investigation id and a full CAVEATS
  block that says nothing about it;
* ``chart_breach_confirmation``: 492 rows over 34 distinct
  ``(group_code, carc)`` keys, ``('CO','16')`` appearing 29 times with a
  different value each. Row ``bounded`` flags are sticky across colliders
  and ``referent_id`` is overwritten by the last one, so a bar's
  drill-through points at the wrong cell.

The wire was complicit: the server declared the axes and then sent rows
indistinguishable under them. So the repair belongs here, before publish,
and it is stated rather than performed silently — a chart that had to be
folded says so, and a chart that cannot be folded honestly is dropped with
its reason and its total named.

The second invariant is the ordinal one. ``InvestigationPlan.bucket_orders``
has carried the catalog's declared order for every ordinal bucket dimension
since round 3 and reached only the findings path, so ``ar_age_bucket`` came
off this API alphabetically — ``120+`` in slot two — while the browser
repaired it client-side and every CSV export did not. The declared order is
published on the spec (``axis_order``) and the rows are emitted in it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from revi_investigation_contracts.api import ChartRow, ChartSpec
from revi_kernel.frame import EvidenceFrame
from revi_kernel.refs import DimensionRef

#: Units whose cells may be added together. Folding two rows of a rate is
#: not a fold, it is a different number.
_ADDITIVE_UNITS = frozenset({"money_cents", "count"})

#: How a composite key reads on a legend chip.
_COMPOSITE_JOIN = " / "


def _dimension_columns(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(c.name for c in frame.schema.columns if isinstance(c.ref, DimensionRef))


def _dimension_id(frame: EvidenceFrame, column: str) -> str | None:
    if column not in frame.schema.names:
        return None
    ref = frame.schema.columns[frame.schema.index_of(column)].ref
    return ref.id if isinstance(ref, DimensionRef) else None


def _keys(rows: Sequence[ChartRow]) -> list[tuple[str, str | None]]:
    return [(row.x, row.series) for row in rows]


def _numeric(value: str | int | float | None) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    return None


def _total(rows: Sequence[ChartRow]) -> Decimal:
    return sum((v for row in rows if (v := _numeric(row.value)) is not None), Decimal(0))


def _fold(rows: Sequence[ChartRow]) -> ChartRow:
    """One row from several that share a key, for an additive unit.

    The sum is the honest cell: those rows are parts of it. Everything
    row-identifying that the colliders disagree about is dropped rather
    than inherited from whichever one came last — a drill handle that
    points at one of 29 cells is worse than no drill handle.
    """
    values = [v for row in rows if (v := _numeric(row.value)) is not None]
    total = sum(values, Decimal(0)) if values else None
    referents = {row.referent_id for row in rows if row.referent_id is not None}
    populations = [row.bound_population for row in rows if row.bound_population is not None]
    return ChartRow(
        x=rows[0].x,
        series=rows[0].series,
        value=float(total) if total is not None else None,
        referent_id=referents.pop() if len(referents) == 1 else None,
        is_bound=any(row.is_bound for row in rows),
        bound_population=sum(populations) if populations else None,
        provisional=any(row.provisional for row in rows),
    )


def enforce_row_keys(
    spec: ChartSpec, frame: EvidenceFrame | None
) -> tuple[ChartSpec | None, str | None]:
    """Make every row uniquely addressable by the axes the spec declares.

    Three outcomes, in order of preference:

    1. **Already unique** — the common case; the spec passes through.
    2. **Re-keyable** — the frame carries grouping columns the spec did not
       declare, so they are folded into ``series`` as a composite key and
       the chart now says what it draws.
    3. **Not re-keyable** — the frame is at a finer grain than any of its
       dimensions describe. An additive measure is summed per key (stated);
       anything else drops the chart, because a rate cannot be folded and a
       last-write-wins rate is a fabricated measurement.
    """
    rows = list(spec.rows)
    if not rows:
        return spec, None
    keys = _keys(rows)
    if len(set(keys)) == len(keys):
        return spec, None

    declared = {name for name in (spec.x, spec.series) if name}
    undeclared = (
        tuple(c for c in _dimension_columns(frame) if c not in declared)
        if frame is not None
        else ()
    )
    if undeclared:
        indices = tuple(frame.schema.index_of(c) for c in undeclared)  # type: ignore[union-attr]
        assert frame is not None
        extra = [
            _COMPOSITE_JOIN.join(str(row[i]) for i in indices) for row in frame.rows
        ]
        if len(extra) == len(rows):
            rows = [
                row.model_copy(
                    update={
                        "series": (
                            f"{row.series}{_COMPOSITE_JOIN}{tail}" if row.series else tail
                        )
                    }
                )
                for row, tail in zip(rows, extra, strict=True)
            ]
            spec = spec.model_copy(
                update={
                    "series": _COMPOSITE_JOIN.join(
                        [*( [spec.series] if spec.series else [] ), *undeclared]
                    ),
                    "rows": rows,
                }
            )
            keys = _keys(rows)
            if len(set(keys)) == len(keys):
                return spec, None

    # Still colliding: the frame is finer than its own dimensions.
    grouped: dict[tuple[str, str | None], list[ChartRow]] = {}
    for row in rows:
        grouped.setdefault((row.x, row.series), []).append(row)
    dropped = len(rows) - len(grouped)
    if spec.unit in _ADDITIVE_UNITS:
        folded = [_fold(members) for members in grouped.values()]
        return (
            spec.model_copy(
                update={
                    "rows": folded,
                    "annotations": [
                        *spec.annotations,
                        f"folded: {len(rows)} frame rows share {len(grouped)} distinct "
                        f"({spec.x}, {spec.series or '—'}) keys and were summed to that grain; "
                        "drill handles are dropped where the colliders disagreed",
                    ],
                }
            ),
            f"chart_rows_collapsed: {spec.id} declared x={spec.x} and "
            f"series={spec.series or 'none'} over {len(rows)} rows that share only "
            f"{len(grouped)} distinct keys. The rows were summed to the declared grain "
            f"(total {_total(rows)} preserved) rather than letting a renderer keep the last "
            "one and drop the rest.",
        )
    return None, (
        f"chart_rows_collapsed: {spec.id} was not published. It declared x={spec.x} and "
        f"series={spec.series or 'none'} over {len(rows)} rows that share only {len(grouped)} "
        f"distinct keys, so {dropped} row(s) are indistinguishable under its own axes, and "
        f"its unit ({spec.unit or 'unknown'}) cannot be summed to fold them. The figures are "
        "in the findings and the evidence drawer; the chart would have implied a measurement "
        "nobody made."
    )


def apply_axis_order(
    spec: ChartSpec,
    frame: EvidenceFrame | None,
    bucket_orders: Mapping[str, Sequence[str]],
) -> ChartSpec:
    """Publish (and apply) the catalog's declared order for an ordinal axis.

    ``sort`` is cleared when an axis order applies: the plan's value
    ordering ranks the FINDINGS, and an aging axis ordered by dollars is
    not an aging axis. Values the catalog does not declare keep their wire
    order, after the declared ones — a bucket the pack has not heard of is
    a fact about the data, not a licence to reorder the ones it has.
    """
    if not bucket_orders or not spec.rows:
        return spec
    dimension = _dimension_id(frame, spec.x) if frame is not None else spec.x
    order = bucket_orders.get(dimension or spec.x) or bucket_orders.get(spec.x)
    if not order:
        return spec
    rank = {value: index for index, value in enumerate(order)}
    ordered = sorted(
        spec.rows,
        key=lambda row: (rank.get(row.x, len(rank)), row.x if row.x not in rank else ""),
    )
    return spec.model_copy(
        update={"rows": ordered, "axis_order": list(order), "sort": None}
    )
