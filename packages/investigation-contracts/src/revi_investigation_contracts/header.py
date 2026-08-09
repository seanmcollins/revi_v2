"""The canonical effective-context header (design §7.2, hard requirement).

Every answer carries this header, exactly as displayed: window + basis,
comparison, filters (with the turn that introduced each), cohort label and
size, watermark. ``build_header_payload`` is the single source of truth
for the canonical strings — the engine builds its outcome headers here,
the API serves the same payload, and traces record the same display text.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from revi_kernel.cohort import CohortRef
from revi_kernel.filters import Predicate
from revi_kernel.scope import Comparison, TimeWindow


class FilterChip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    op: str
    #: The predicate the engine ACTUALLY RAN, after §6.6 value resolution.
    #: Round-2 FN-9: a turn that corrected ``'lakewood medicaid mco'`` to the
    #: payer ``'Lakewood Medicaid MCO'`` — and queried the corrected value —
    #: published the user's spelling here, so the one field whose whole job
    #: is to state which population ran stated a value that does not exist
    #: in the warehouse, two rows above the caution saying so.
    values: list[str]
    #: What the user typed, when the engine corrected it. Empty when nothing
    #: was corrected, so a chip carrying this is exactly a chip whose
    #: predicate differs from the request.
    requested_values: list[str] = Field(default_factory=list)
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
    #: Set when every measure on this turn is a ``kind: snapshot`` contract
    #: — an as-of balance that applies no start..end window (round-2 FN-2).
    #: ``window_start``/``window_end`` stay populated (they are what the
    #: turn's cohort and charts were scoped by), but the DISPLAY says "as
    #: of", because a header that announces a range the metric never
    #: applied is a claim about the number that is not true.
    as_of: date | None = None
    display: str = ""


def predicate_chip(
    predicate: Predicate,
    *,
    pinned: bool = False,
    corrections: Mapping[str, Mapping[str, object]] | None = None,
) -> FilterChip:
    """One chip for one predicate, stating the value that was queried.

    ``corrections`` is the §6.6 value-resolution map (dimension → {as
    typed: as queried}). Applied here rather than at the call site so that
    every producer of a chip — engine header, restored turn, trace — gets
    the same answer, and so the user's original is carried rather than
    thrown away: the Scope popover can still say "you typed ...".
    """
    per_dimension = (corrections or {}).get(predicate.dimension.id, {})
    queried: list[str] = []
    requested: list[str] = []
    corrected = False
    for value in predicate.values:
        text = str(value)
        replacement = per_dimension.get(text)
        requested.append(text)
        if replacement is None:
            queried.append(text)
        else:
            queried.append(str(replacement))
            corrected = True
    return FilterChip(
        dimension=predicate.dimension.id,
        op=predicate.op.value,
        values=queried,
        requested_values=requested if corrected else [],
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
    as_of: date | None = None,
    corrections: Mapping[str, Mapping[str, object]] | None = None,
) -> ContextHeaderPayload:
    """Assemble the canonical header from kernel scope objects.

    ``as_of`` is set for a turn whose measures are all ``kind: snapshot``:
    those contracts read a balance standing at the watermark and apply no
    window at all, so announcing ``2026-07-01..2026-07-31`` beside the
    number states a scoping that did not happen (round-2 FN-2).

    ``corrections`` carries the §6.6 value resolutions so the chips state
    the predicate that RAN (round-2 FN-9).
    """
    chips = [predicate_chip(p, corrections=corrections) for p in predicates]
    chips.extend(
        predicate_chip(p, pinned=True, corrections=corrections) for p in pinned_predicates
    )
    filters = [chip.label for chip in chips]

    parts = [
        f"as of {as_of.isoformat()} ({window.basis.id})"
        if as_of is not None
        else f"{window.range.start.isoformat()}..{window.range.end.isoformat()} "
        f"({window.basis.id})"
    ]
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
        as_of=as_of,
        display=" · ".join(parts),
    )
