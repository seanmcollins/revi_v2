"""Shared machinery for transform operators (design §6.5).

Operators are pure functions ``EvidenceFrame(s) → EvidenceFrame`` that:

- carry a semver ``OperatorVersion`` recorded in output provenance;
- enforce the grade law (output grade = weakest input grade);
- require all inputs at one watermark (cross-watermark arithmetic is a
  category error — comparisons across data states are epoch events, not
  transforms);
- propagate ``truncated`` (OR) and ``suppressed_cells`` (sum).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame, FrameColumn, FrameRow, TransformProvenance
from revi_kernel.grades import EvidenceGrade, min_grade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark


@dataclass(frozen=True, slots=True)
class OperatorVersion:
    name: str
    version: str  # semver

    def __str__(self) -> str:
        return f"{self.name}@{self.version}"


@dataclass(frozen=True, slots=True)
class FrameMeta:
    """Combined bookkeeping for an operator's output frame."""

    watermark: DataWatermark
    grade: EvidenceGrade
    truncated: bool
    suppressed_cells: int


def combine_meta(*frames: EvidenceFrame) -> FrameMeta:
    if not frames:
        raise ValueError("at least one input frame required")
    watermark = frames[0].watermark
    for f in frames[1:]:
        if f.watermark != watermark:
            raise ValueError(
                f"operator inputs span watermarks {watermark.id!r} and {f.watermark.id!r}; "
                "transforms require a single data state"
            )
    return FrameMeta(
        watermark=watermark,
        grade=min_grade(*(f.evidence_grade for f in frames)),
        truncated=any(f.truncated for f in frames),
        suppressed_cells=sum(f.suppressed_cells for f in frames),
    )


def provenance(op: OperatorVersion, *frames: EvidenceFrame) -> TransformProvenance:
    return TransformProvenance(
        operator=op.name, operator_version=op.version, inputs=tuple(f.provenance for f in frames)
    )


def output_frame(
    op: OperatorVersion,
    columns: tuple[FrameColumn, ...],
    rows: tuple[FrameRow, ...],
    *inputs: EvidenceFrame,
) -> EvidenceFrame:
    from revi_kernel.frame import FrameSchema

    meta = combine_meta(*inputs)
    return EvidenceFrame(
        schema=FrameSchema(columns),
        rows=rows,
        watermark=meta.watermark,
        provenance=provenance(op, *inputs),
        evidence_grade=meta.grade,
        truncated=meta.truncated,
        suppressed_cells=meta.suppressed_cells,
    )


def dimension_names(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(c.name for c in frame.schema.columns if isinstance(c.ref, DimensionRef))


def measure_names(frame: EvidenceFrame) -> tuple[str, ...]:
    return tuple(c.name for c in frame.schema.columns if isinstance(c.ref, MetricRef))


def rows_by_key(frame: EvidenceFrame, key_columns: tuple[str, ...]) -> dict[tuple[Scalar, ...], FrameRow]:
    indices = tuple(frame.schema.index_of(name) for name in key_columns)
    out: dict[tuple[Scalar, ...], FrameRow] = {}
    for row in frame.rows:
        key = tuple(row[i] for i in indices)
        if key in out:
            raise ValueError(f"duplicate cell {key!r} for key columns {key_columns}")
        out[key] = row
    return out


def as_decimal(value: Scalar, *, context: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise ValueError(f"{context}: expected numeric value, got {value!r}")
    return Decimal(value)


def round_half_up_int(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


RATIO_PLACES = Decimal("0.000001")


def quantize_ratio(value: Decimal) -> Decimal:
    return value.quantize(RATIO_PLACES, rounding=ROUND_HALF_UP)
