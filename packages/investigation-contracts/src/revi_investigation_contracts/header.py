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

#: Date bases, in the words an analyst uses for them. The bare token
#: ("remit", "service") is a modeling word; the phrase is what a reader
#: recognises off a remittance advice. See docs/client-language.md.
_BASIS_PHRASES: dict[str, str] = {
    "remit": "on the remittance date",
    "service": "on the service date",
    "submission": "on the submission date",
    "posting": "on the posting date",
    "accrual": "on the accrual date",
}


def basis_phrase(basis_id: str) -> str:
    """The date basis in English, or the id when this table has no phrase."""
    return _BASIS_PHRASES.get(basis_id, basis_id)


#: Predicate operators in English. The wire keeps the token on
#: :attr:`FilterChip.op`; only the human label is translated here.
_OP_PHRASES: dict[str, str] = {
    "in": "is one of",
    "not_in": "is not one of",
    "eq": "is",
    "neq": "is not",
    "gte": "is at least",
    "lte": "is at most",
    "gt": "is more than",
    "lt": "is less than",
}


class FilterChip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: str
    op: str
    #: The predicate the engine ACTUALLY RAN, after §6.6 value resolution.
    #: Publishing the user's spelling instead makes the one field whose job
    #: is to state which population ran state a value that does not exist in
    #: the warehouse — a turn that corrects ``'lakewood medicaid mco'`` to
    #: the payer ``'Lakewood Medicaid MCO'`` queries the corrected value and
    #: must say so.
    values: list[str]
    #: What the user typed, when the engine corrected it. Empty when nothing
    #: was corrected, so a chip carrying this is exactly a chip whose
    #: predicate differs from the request.
    requested_values: list[str] = Field(default_factory=list)
    origin_turn: str | None = None
    pinned: bool = False

    @property
    def label(self) -> str:
        """The chip as a reader sees it.

        The dimension id is de-snaked and the operator spelled in English:
        ``payer_type in [HMO]`` is our vocabulary, ``payer type is one of
        HMO`` is theirs. The machine-readable ``dimension``/``op``/``values``
        fields above are untouched, so a client that branches on them is
        unaffected (docs/client-language.md).
        """
        name = self.dimension.replace("_", " ")
        op = _OP_PHRASES.get(self.op, self.op)
        return f"{name} {op} {', '.join(self.values)}"


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
    #: — an as-of balance that applies no start..end window.
    #: ``window_start``/``window_end`` stay populated (they are what the
    #: turn's cohort and charts were scoped by), but the DISPLAY says "as
    #: of", because a header that announces a range the metric never
    #: applied is a claim about the number that is not true.
    as_of: date | None = None
    #: Set when at least one probe on this turn read a window OTHER than the
    #: one named above — a playbook probe template may declare its own
    #: (``daily_portfolio``'s denial-rate probe reads four full weeks), and
    #: the planner applies it whenever the analyst named no window.
    #: ``window_start``/``window_end`` stay the investigation window (the
    #: cohort, the charts and every drill are scoped by it); this sentence
    #: says that not every figure below was computed over it, and each
    #: finding states the period it WAS computed over. Without it, "denial
    #: rate: 14.3% (2026-07-01..2026-07-31)" sat over a number derived
    #: across 2026-07-06..2026-08-02.
    window_note: str | None = None
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
    window_note: str | None = None,
    corrections: Mapping[str, Mapping[str, object]] | None = None,
) -> ContextHeaderPayload:
    """Assemble the canonical header from kernel scope objects.

    ``as_of`` is set for a turn whose measures are all ``kind: snapshot``:
    those contracts read a balance standing at the watermark and apply no
    window at all, so announcing ``2026-07-01..2026-07-31`` beside the
    number states a scoping that did not happen.

    ``window_note`` is set when a probe on this turn read a window other
    than the one this header names — a playbook probe may declare its own,
    and a header that announces one period over figures computed across
    another is the same claim-that-is-not-true in the other axis. It rides
    on the display string as well as the field, because the display string
    is what the trace, the export and every stored answer carry.

    ``corrections`` carries the §6.6 value resolutions so the chips state
    the predicate that RAN.
    """
    chips = [predicate_chip(p, corrections=corrections) for p in predicates]
    chips.extend(
        predicate_chip(p, pinned=True, corrections=corrections) for p in pinned_predicates
    )
    filters = [chip.label for chip in chips]

    basis = basis_phrase(window.basis.id)
    parts = [
        f"as of {as_of.isoformat()} ({basis})"
        if as_of is not None
        else f"{window.range.start.isoformat()}..{window.range.end.isoformat()} "
        f"({basis})"
    ]
    if comparison is not None:
        parts.append(
            f"vs {comparison.window.range.start.isoformat()}.."
            f"{comparison.window.range.end.isoformat()}"
        )
    if filters:
        parts.append("filters: " + "; ".join(filters))
    if cohort is not None:
        parts.append(f"population of {cohort.size} claims")
    parts.append("this data load")
    if window_note:
        parts.append(window_note)

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
        window_note=window_note,
        display=" · ".join(parts),
    )
