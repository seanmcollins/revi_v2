"""The canonical effective-context header (design §7.2, hard requirement).

Every answer carries this header, exactly as displayed: window + basis,
comparison, filters (with the turn that introduced each), cohort label and
size, watermark. ``build_header_payload`` is the single source of truth
for the canonical strings — the engine builds its outcome headers here,
the API serves the same payload, and traces record the same display text.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from revi_kernel.cohort import CohortRef
from revi_kernel.filters import Predicate
from revi_kernel.scope import Comparison, TimeWindow


class FilterChip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    op: str
    values: list[str]
    origin_turn: str | None = None
    pinned: bool = False

    @property
    def label(self) -> str:
        return f"{self.dimension} {self.op} [{', '.join(self.values)}]"


class ContextHeaderPayload(BaseModel):
    """Structured chips plus the canonical one-line display string."""

    model_config = ConfigDict(extra="forbid")

    window_start: date
    window_end: date
    basis: str
    comparison_kind: str | None = None
    comparison_start: date | None = None
    comparison_end: date | None = None
    filters: list[str] = Field(default_factory=list)
    filter_chips: list[FilterChip] = Field(default_factory=list)
    cohort: str | None = None
    cohort_size: int | None = None
    watermark_id: str = ""
    display: str = ""


def predicate_chip(predicate: Predicate, *, pinned: bool = False) -> FilterChip:
    return FilterChip(
        dimension=predicate.dimension.id,
        op=predicate.op.value,
        values=[str(v) for v in predicate.values],
        origin_turn=predicate.origin_turn,
        pinned=pinned,
    )


def build_header_payload(
    *,
    window: TimeWindow,
    comparison: Comparison | None,
    predicates: Sequence[Predicate],
    pinned_predicates: Sequence[Predicate] = (),
    cohort: CohortRef | None = None,
    watermark_id: str,
) -> ContextHeaderPayload:
    """Assemble the canonical header from kernel scope objects."""
    chips = [predicate_chip(p) for p in predicates]
    chips.extend(predicate_chip(p, pinned=True) for p in pinned_predicates)
    filters = [chip.label for chip in chips]

    parts = [f"{window.range.start.isoformat()}..{window.range.end.isoformat()} ({window.basis.id})"]
    if comparison is not None:
        parts.append(
            f"vs {comparison.window.range.start.isoformat()}.."
            f"{comparison.window.range.end.isoformat()}"
        )
    if filters:
        parts.append("filters: " + "; ".join(filters))
    if cohort is not None:
        parts.append(f"cohort: {cohort.id} ({cohort.size} claims)")
    parts.append(f"watermark {watermark_id}")

    return ContextHeaderPayload(
        window_start=window.range.start,
        window_end=window.range.end,
        basis=window.basis.id,
        comparison_kind=comparison.kind.value if comparison is not None else None,
        comparison_start=comparison.window.range.start if comparison is not None else None,
        comparison_end=comparison.window.range.end if comparison is not None else None,
        filters=filters,
        filter_chips=chips,
        cohort=cohort.id if cohort is not None else None,
        cohort_size=cohort.size if cohort is not None else None,
        watermark_id=watermark_id,
        display=" · ".join(parts),
    )
