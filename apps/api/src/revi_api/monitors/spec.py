"""Resolving a stored investigation into a re-runnable typed spec."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from revi_api.monitors.common import _date_range_phrase, _plural
from revi_investigation.application.dto_mapping import refinement_to_dto
from revi_investigation.application.rendering import (
    render_row_label,
)
from revi_investigation.domain.context import AnalysisSpec
from revi_investigation.domain.refinements import AddFilter
from revi_investigation_contracts.api import (
    TypedInvestigationSpec,
)
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    AddFilterModel,
    WindowSpecModel,
)
from revi_kernel.filters import iter_predicates
from revi_kernel.scope import ComparisonKind

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    pass


#: Comparison kinds a typed spec can express. ``CUSTOM`` is a pair of
#: literal dates that meant something on the turn that set it; carrying it
#: onto a monitor would freeze a comparison window while the primary window
#: moved, which is a different measurement every load.
_COMPARISON_LITERALS = {
    ComparisonKind.PRIOR_PERIOD: "prior_period",
    ComparisonKind.PRIOR_YEAR: "prior_year",
}


def typed_spec_from_analysis(spec: AnalysisSpec) -> tuple[TypedInvestigationSpec, str, list[str]]:
    """The stored ``AnalysisSpec`` as a re-runnable typed spec.

    This is what "pin-from-investigation resolves the stored spec
    server-side" means concretely: the investigation already holds the
    disposed, validated spec its answer was computed from, so the pin is
    built from THAT and no text is re-interpreted and no model is called.
    Re-deriving the spec from the question would be a second, worse answer
    to a question already answered — and it would drift the day the
    interpreter improved.

    Returns the spec, its ``window_mode``, and any notes about what could
    not be carried across. The notes matter: a monitor that silently dropped
    a scope clause would measure a different population from the answer the
    analyst was looking at when they pinned it.
    """
    notes: list[str] = []
    window = spec.context.window
    # A RELATIVE window re-anchors per load, so the monitor tracks a moving
    # period and a delta is a real movement. An absolute one re-measures
    # fixed dates, so a delta is late-arriving data. Both are legitimate
    # monitors; only one of them is a movement, which is why the mode is
    # published rather than inferred by a reader.
    if window.requested is not None:
        window_model: WindowSpecModel | AbsoluteWindowModel = WindowSpecModel(
            quantity=str(window.requested.quantity),
            unit=window.requested.unit.value,
            mode=window.requested.mode.value,
        )
        window_mode = "relative"
    else:
        window_model = AbsoluteWindowModel(start=window.range.start, end=window.range.end)
        window_mode = "absolute"

    filters: list[AddFilterModel] = []
    for predicate in iter_predicates(spec.context.scope):
        dto = refinement_to_dto(AddFilter(predicate))
        assert isinstance(dto, AddFilterModel)  # AddFilter maps to exactly this
        filters.append(dto)
    # Pins (session-sticky scope) are deliberately NOT carried: they belong
    # to the session that declared them, and a monitor that inherited another
    # conversation's sticky filter would narrow every future load by a
    # decision nobody made about this monitor.
    if spec.context.pins:
        notes.append(
            "this monitor did not carry "
            f"{_plural(len(spec.context.pins), 'sticky filter', 'sticky filters')} the "
            "conversation was holding: a filter set in a conversation belongs to that "
            "conversation, and inheriting one here would narrow every future load by a "
            "decision nobody made about this monitor"
        )
    if spec.context.cohort is not None:
        notes.append(
            "the answer this monitor was created from was measured over a population frozen at "
            "one data load — a fixed list, picked once. This monitor carries the scope that "
            "picked that list instead, so it re-selects the population at every load rather "
            "than re-reading a frozen one"
        )

    comparison: str | None = None
    if spec.context.comparison is not None:
        comparison = _COMPARISON_LITERALS.get(spec.context.comparison.kind)
        if comparison is None:
            notes.append(
                "the answer's custom comparison window was not carried onto this monitor: it is "
                "a pair of literal dates, and holding it fixed while the primary window moves "
                "would make every load a different measurement"
            )

    typed = TypedInvestigationSpec(
        metric_ids=[measure.id for measure in spec.measures],
        dimensions=[dimension.id for dimension in spec.dimensions],
        filters=filters,
        window=window_model,
        basis=window.basis.id,
        comparison=comparison,  # type: ignore[arg-type]
    )
    return typed, window_mode, notes


def _eq_filters_of(spec: TypedInvestigationSpec) -> tuple[tuple[str, str], ...]:
    """The single-value equality filters on a spec, as ``(dimension, value)``.

    These are what makes a spec name ONE cell rather than a ranking, so they
    are what the label, the spec summary and the subject-identity guard all
    read.
    """
    return tuple(
        (f.dimension, str(f.values[0]))
        for f in spec.filters
        if f.predicate_op == "eq" and len(f.values) == 1
    )


def _narrowed_to_cell(
    spec: TypedInvestigationSpec, cell: Sequence[tuple[str, str]]
) -> TypedInvestigationSpec:
    """The same spec, restricted to one cell of its own breakdown.

    The dimensions are KEPT. A monitor narrowed to ``payer = Pinnacle Health
    Plan`` and still broken out by payer evaluates to a one-row breakdown
    whose finding names that payer — so the tile's own headline states the
    subject, and label and value cannot come apart. Dropping the dimension
    would answer with a bare scalar ("denial rate is 22.9%") and throw away
    the very fact the narrowing exists to keep.
    """
    existing = {(f.dimension, str(f.values[0])) for f in _iter_eq(spec)}
    additions = [
        AddFilterModel(
            op="add_filter", dimension=dimension, predicate_op="eq", values=[value]
        )
        for dimension, value in cell
        if (dimension, value) not in existing
    ]
    if not additions:
        return spec
    return spec.model_copy(update={"filters": [*spec.filters, *additions]})


def _iter_eq(spec: TypedInvestigationSpec) -> list[AddFilterModel]:
    return [f for f in spec.filters if f.predicate_op == "eq" and len(f.values) == 1]


def _cell_phrase(cell: Sequence[tuple[str, str]], pack: Any) -> str:
    """A cell in the reader's words, with codes rendered as codes.

    ``payer`` + ``group_code`` + ``carc`` reads "Bluestone Mutual / CO / 16
    — Claim/service lacks information" rather than three raw values, using
    the same governed renderer the findings themselves use — so a monitor
    label and the finding it was pinned from name the cell identically.
    """
    if not cell:
        return ""
    dimensions = [dimension for dimension, _ in cell]
    values: dict[str, Any] = dict(cell)
    return render_row_label(pack, dimensions, values)


#: The window units a spec can carry, singular and plural. Tabulated rather
#: than pluralised by appending an "s" to the stored token: the token is an
#: internal id, and "the last 3 days" is the reader's phrase whatever the
#: id happens to spell.
_WINDOW_UNIT_WORDS: dict[str, tuple[str, str]] = {
    "day": ("day", "days"),
    "week": ("week", "weeks"),
    "month": ("month", "months"),
    "quarter": ("quarter", "quarters"),
    "year": ("year", "years"),
}


def _window_phrase(spec: TypedInvestigationSpec, window_mode: str) -> str:
    """The monitor's window, said the way somebody would say it."""
    window = spec.window
    if isinstance(window, WindowSpecModel):
        quantity = str(window.quantity)
        singular, plural = _WINDOW_UNIT_WORDS.get(
            str(window.unit), ("period", "periods")
        )
        period = singular if quantity in ("1", "1.0") else f"{quantity} {plural}"
        moving = "the last full " if window.mode == "full_periods" else "the last "
        return f"{moving}{period}, re-anchored at every load"
    if isinstance(window, AbsoluteWindowModel):
        return (
            f"the fixed dates {_date_range_phrase(window.start, window.end)}, re-measured at "
            "every load"
        )
    return "the window stored with this monitor"


def spec_hash(spec: TypedInvestigationSpec, presentation: str) -> str:
    """A stable identity for "what this monitor measures, and how it renders".

    Normalised so two specs that differ only in the ORDER somebody named
    their metrics, dimensions or filters hash the same: they are one
    measurement, and treating them as two is how a department of eight
    directors ends up with six copies of the same monitor briefing the same
    movement six times.
    """
    payload = {
        "metric_ids": sorted(spec.metric_ids),
        "dimensions": sorted(spec.dimensions),
        "filters": sorted(
            f"{f.dimension}|{f.predicate_op}|{'|'.join(sorted(str(v) for v in f.values))}"
            for f in spec.filters
        ),
        "window": spec.window.model_dump(mode="json"),
        "basis": spec.basis or "",
        "comparison": spec.comparison or "",
        "presentation": presentation,
    }
    return hashlib.sha256(repr(sorted(payload.items())).encode("utf-8")).hexdigest()[:16]


_WINDOW_NOTES = {
    "relative": (
        "This monitor re-anchors its window to each load's newest data date, so it usually "
        "tracks a moving period. Where two loads land inside the same period it measures the "
        "same dates, and the change between them is late-arriving data rather than a movement. "
        "Every reading shows the dates it measured, so you can tell which of the two you are "
        "looking at."
    ),
    "absolute": (
        "This monitor re-measures the same fixed dates every load. A change from one load to "
        "the next is therefore late-arriving data — adjudication run-out, back-dated charges — "
        "rather than a movement in the period itself."
    ),
}
_WINDOW_NOTES["anchored"] = _WINDOW_NOTES["absolute"]


#: The sentence a movement earns when both loads measured the SAME dates.
#: Not a caveat about the number — the number is right — but about what the
#: change MEANS, which is the thing a delta on a daily surface is read for.
#:
#: Opens by naming WHICH COMPARISON it qualifies. A brief entry can carry
#: two window clauses — one for the prior-load delta and one for the
#: baseline — and unattributed they read as a paragraph contradicting
#: itself: "different date ranges" three words after "the same dates".
SAME_WINDOW_NOTE = (
    "Against the previous load, both readings measured the same dates ({dates}), so this "
    "change is late-arriving data settling — adjudication run-out, back-dated charges — "
    "rather than a movement in the period itself."
)
