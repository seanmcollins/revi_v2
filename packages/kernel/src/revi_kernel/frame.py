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
