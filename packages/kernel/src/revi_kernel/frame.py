"""EvidenceFrame — the typed interchange structure (design §6.4).

Probes and transforms exchange exactly this structure. Frames are where
small-cell suppression is enforced, where truncation is made visible rather
than silent, and what findings, charts, and the narrative composer consume.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from revi_kernel.filters import Scalar
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, FieldRef, MetricRef
from revi_kernel.watermark import DataWatermark


@dataclass(frozen=True, slots=True)
class FrameColumn:
    name: str
    ref: DimensionRef | MetricRef | FieldRef
    contract_version: int | None = None
    unit: str | None = None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("FrameColumn.name must be non-empty")


@dataclass(frozen=True, slots=True)
class FrameSchema:
    columns: tuple[FrameColumn, ...]

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate column names in frame schema: {names}")

    def index_of(self, name: str) -> int:
        for i, col in enumerate(self.columns):
            if col.name == name:
                return i
        raise KeyError(f"no column named {name!r}")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)


FrameRow = tuple[Scalar, ...]


@dataclass(frozen=True, slots=True)
class ProbeProvenance:
    """This frame came straight from a repository probe execution."""

    probe_id: str
    probe_hash: str
    repository_query_id: str | None = None
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class TransformProvenance:
    """This frame was produced by a versioned kernel operator."""

    operator: str
    operator_version: str
    inputs: tuple[ProvenanceRef, ...]


ProvenanceRef = Union[ProbeProvenance, TransformProvenance]  # noqa: UP007


@dataclass(frozen=True, slots=True)
class EvidenceFrame:
    schema: FrameSchema
    rows: tuple[FrameRow, ...]
    watermark: DataWatermark
    provenance: ProvenanceRef
    evidence_grade: EvidenceGrade
    truncated: bool = False
    suppressed_cells: int = 0

    def __post_init__(self) -> None:
        width = len(self.schema.columns)
        for i, row in enumerate(self.rows):
            if len(row) != width:
                raise ValueError(f"row {i} has {len(row)} values, schema has {width} columns")
        if self.suppressed_cells < 0:
            raise ValueError("suppressed_cells must be >= 0")

    def column(self, name: str) -> tuple[Scalar, ...]:
        idx = self.schema.index_of(name)
        return tuple(row[idx] for row in self.rows)

    @property
    def row_count(self) -> int:
        return len(self.rows)


#: Separator the kernel operators use for a measure's anatomy columns
#: (``denial_rate__num``, ``denial_rate__den``, ``…__prior``, ``…__delta``).
#: A reader counts cells of the measure, never of its parts.
_ANATOMY_MARKER = "__"

#: The unit a money measure carries. Preferred as a frame's subject when a
#: frame publishes several: it is the figure the answer is about.
_MONEY_UNIT = "money_cents"


def published_measures(frame: EvidenceFrame) -> tuple[str, ...]:
    """The measure columns a reader sees, in schema order.

    Anatomy columns are excluded: ``denial_rate__num`` is not a second
    denial rate, and counting a row as "measured" because its numerator
    survived is how one frame came to be described two ways on one card
    (round-6 A-02).
    """
    return tuple(
        col.name
        for col in frame.schema.columns
        if isinstance(col.ref, MetricRef) and _ANATOMY_MARKER not in col.name
    )


def primary_measure(frame: EvidenceFrame) -> str | None:
    """The one measure a frame is *about*: money first, else the first."""
    measures = published_measures(frame)
    for name in measures:
        if frame.schema.columns[frame.schema.index_of(name)].unit == _MONEY_UNIT:
            return name
    return measures[0] if measures else None


def withheld_row_indices(
    frame: EvidenceFrame, measure: str | None = None
) -> frozenset[int]:
    """Rows this frame publishes no value for — the withheld cells.

    **One rule, one home** (round-6 A-02). The narrative counted a
    null-valued row as *measured* — its census required EVERY metric column
    including the ``__num``/``__den`` anatomy to be null, and required the
    frame to admit ``suppressed_cells`` — while the chart annotation over
    the same frame counted the same row as *withheld* on the value column
    alone. Live, one payload said "0 were withheld outright" and "1 of 8
    cells were withheld outright" about one 8-cell frame.

    The reader's rule wins, because it is the one a reader can check
    against the marks in front of them: a drawn row with no value is
    withheld, never measured. Both surfaces now ask this function.
    """
    column = measure if measure is not None else primary_measure(frame)
    if column is None or column not in frame.schema.names:
        return frozenset()
    index = frame.schema.index_of(column)
    return frozenset(i for i, row in enumerate(frame.rows) if row[index] is None)
