"""``panel`` — the multi-measure scorecard assembly (design §6.5).

A scorecard is not a reshape of one result set. It is N governed measures,
each read per entity by its own probe over its own window on its own date
basis, laid side by side: one row per entity, one column per measure, and
one ordering per measure.

That is why this is its own operator rather than an argument to ``pivot``.
``pivot`` spreads ONE measure across the values of one column — a
presentation reshape inside a single frame. The scorecard's measures live
in different frames because they are different measurements: posted cash
sits at transaction grain on the posting date, the denial rate at claim
grain on the remittance date, open A/R at a snapshot. Joining them is the
answer; dividing them into each other is the defect the payer scorecard's
own scope notes warn about.

**One ordering per measure, in the direction the contract declares.**
A rank is only meaningful once someone has said which end is good, and
that fact belongs to the metric contract (``sign``), never to this
operator. The caller passes the two families —
``better_high`` (higher is better) and ``better_low`` (lower is better) —
and a measure named by neither gets no ordering at all. That silence is
correct and load-bearing: charges and claim volume are ``neutral``, and a
"rank" over them would assert that billing more is better.

**A ceiling still ranks here, and is still excluded upstream.** This
operator orders every non-null cell, exactly as ``rank`` does, because it
has no suppression threshold and cannot tell a measurement from a bound.
Which cells may carry a PUBLISHED position is decided where the bounds are
known — the findings stage, by the same rule it already applies to a
ranked frame.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from revi_calculation.operators.base import (
    OperatorVersion,
    output_frame,
)
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameRow
from revi_kernel.refs import DimensionRef, MetricRef

PANEL_OP = OperatorVersion("panel", "1.0.0")

#: The unit a rank column carries. An ordinal position is a count of
#: places, not a quantity of anything the metric measures — stamping it
#: with the measure's own unit is how "#1" renders as "$1.00".
RANK_UNIT = "count"

#: Separator marking a column as a measure's anatomy rather than a measure.
#: Mirrors :data:`revi_kernel.frame._ANATOMY_MARKER`; a reader counts cells
#: of the measure, never of its parts.
_ANATOMY_MARKER = "__"


def _entity_column(frame: EvidenceFrame, entity: str) -> int:
    """Index of the panel's entity column on one input frame."""
    dimensions = [
        col.name for col in frame.schema.columns if isinstance(col.ref, DimensionRef)
    ]
    if dimensions != [entity]:
        raise ValueError(
            f"panel input frame is cut by {dimensions or ['nothing']!r}, and a panel row is "
            f"one {entity!r}. A frame at a finer cut is a different population, and joining "
            "it onto an entity row would publish one of its cells as the whole."
        )
    return frame.schema.index_of(entity)


def _measure_columns(frame: EvidenceFrame) -> tuple[tuple[str, FrameColumn], ...]:
    """Every metric column on an input frame, anatomy included.

    The anatomy (``denial_rate__num`` / ``__den``) travels with its measure
    on purpose: it is what lets a downstream reader ask whether a cell is a
    measurement or a ceiling. Dropping it here would make every bound on a
    scorecard indistinguishable from a measured rate.
    """
    return tuple(
        (col.name, col) for col in frame.schema.columns if isinstance(col.ref, MetricRef)
    )


def _ordering(
    values: Sequence[tuple[Scalar, str]], *, descending: bool
) -> dict[int, int]:
    """1-based positions over the cells that carry a number.

    Ties break on the entity label so two runs of the same data never
    disagree about who is third — and the tie-break runs in the SAME
    direction whichever way the measure improves, which is why it is a
    separate stable pass rather than a component of one sort key. A null
    cell gets no position: there is nothing to put in order.
    """
    numbered = [
        (position, value, label)
        for position, (value, label) in enumerate(values)
        if isinstance(value, (int, Decimal)) and not isinstance(value, bool)
    ]
    numbered.sort(key=lambda item: item[2])
    numbered.sort(key=lambda item: item[1], reverse=descending)
    return {position: place for place, (position, _, _) in enumerate(numbered, start=1)}


def panel(
    *frames: EvidenceFrame,
    entity: str,
    better_high: tuple[str, ...] = (),
    better_low: tuple[str, ...] = (),
) -> EvidenceFrame:
    """Assemble N per-entity measurements into one scorecard frame.

    Rows are the union of entity values across the inputs, in the order
    they were first measured. A measure absent for an entity is NULL, which
    is a different fact from zero and is published as one.
    """
    if not frames:
        raise ValueError("panel requires at least one input frame")

    order: list[Scalar] = []
    seen: set[Scalar] = set()
    entity_column: FrameColumn | None = None
    # measure name → (column declaration, {entity value: cell})
    cells: dict[str, dict[Scalar, Scalar]] = {}
    declarations: dict[str, FrameColumn] = {}

    for frame in frames:
        index = _entity_column(frame, entity)
        if entity_column is None:
            entity_column = frame.schema.columns[index]
        measures = _measure_columns(frame)
        for name, column in measures:
            if name in declarations:
                raise ValueError(
                    f"panel measure {name!r} is produced by two of this scorecard's checks; "
                    "one column cannot hold two measurements of the same thing"
                )
            declarations[name] = column
            cells[name] = {}
        positions = tuple((name, frame.schema.index_of(name)) for name, _ in measures)
        for row in frame.rows:
            key = row[index]
            if key not in seen:
                seen.add(key)
                order.append(key)
            for name, at in positions:
                cells[name][key] = row[at]

    assert entity_column is not None  # at least one frame, each with the column

    published = [name for name in declarations if _ANATOMY_MARKER not in name]
    high, low = frozenset(better_high), frozenset(better_low)
    overlap = high & low
    if overlap:
        raise ValueError(
            f"panel measures {sorted(overlap)!r} are declared better-high and better-low at "
            "once; a measure has one improvement direction or none"
        )

    ranks: dict[str, dict[int, int]] = {}
    for name in published:
        if name not in high and name not in low:
            continue
        column_cells = cells[name]
        ranks[name] = _ordering(
            [(column_cells.get(key), str(key)) for key in order],
            descending=name in high,
        )

    columns: tuple[FrameColumn, ...] = (
        entity_column,
        *(declarations[name] for name in declarations),
        *(
            FrameColumn(
                f"{name}{_ANATOMY_MARKER}rank",
                declarations[name].ref,
                declarations[name].contract_version,
                RANK_UNIT,
            )
            for name in ranks
        ),
    )
    rows: tuple[FrameRow, ...] = tuple(
        (
            key,
            *(cells[name].get(key) for name in declarations),
            *(ranks[name].get(position) for name in ranks),
        )
        for position, key in enumerate(order)
    )
    return output_frame(PANEL_OP, columns, rows, *frames)
