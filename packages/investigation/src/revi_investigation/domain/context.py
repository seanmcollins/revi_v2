"""Explicit conversational context (design §7.2) and the analysis spec.

``InvestigationContext`` is exactly the design's §7.2 object. The design's
refinement operators also address plan-level components that are not part
of the context proper (dimensions, measures, ranking, limit) — those live
in ``AnalysisSpec``, which wraps the context plus the plan-shaping fields.
Refinement locality is stated over the spec: each operator changes only the
component(s) it names (property-tested).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from revi_calculation_contracts.contract import SignConvention
from revi_kernel.cohort import CohortRef
from revi_kernel.filters import EMPTY_SCOPE, FilterExpr, Predicate, and_merge
from revi_kernel.refs import DimensionRef, Grain, MetricRef
from revi_kernel.scope import Comparison, TimeWindow
from revi_kernel.watermark import DataWatermark


class AskedDirection(StrEnum):
    """The movement the analyst asked about, when they asked about one.

    "Which payers had the biggest **increase** in denials" is not the same
    question as "which payers moved most", and answering the second while
    the analyst asked the first is how a platform reports three improvements
    as if they were the problem. A closed set, mirrored by the
    interpretation schema's ``direction`` literal and carried onto the plan
    so the layer that *selects* rows can honor it.

    ``WORSENED``/``IMPROVED`` are polarity-relative: which sign of a delta
    counts as worse depends on the metric contract's
    :class:`~revi_calculation_contracts.contract.SignConvention`, so they
    resolve to a sign only against a contract (:func:`wanted_delta_sign`).
    """

    INCREASE = "increase"
    DECREASE = "decrease"
    WORSENED = "worsened"
    IMPROVED = "improved"


class AskedMagnitude(StrEnum):
    """The extremity the analyst phrased, when they phrased one.

    "The **biggest** increase" and "the **smallest** increase" pick opposite
    ends of the same direction-matched set. Absent phrasing means "no
    extremity was asserted" and the default (biggest first) applies.
    """

    LARGEST = "largest"
    SMALLEST = "smallest"


class AskedOrder(StrEnum):
    """Which end of a ranking the analyst asked to see first.

    "Rank payers best to worst" and "rank payers worst to best" are the same
    measurement in opposite orders, and the difference is not cosmetic:
    live, "ranked best to worst" returned the worst payer first and narrated
    it as *"State Medicaid MCO ranks first at a 29.5% denial rate"* — the
    ordering the analyst asked for, the ordering the rows arrived in, and
    the sentence describing them all disagreed.

    Like :class:`AskedDirection` this is polarity-relative: which end is
    "best" depends on the metric contract's
    :class:`~revi_calculation_contracts.contract.SignConvention`, so it
    resolves to a sort order only against a contract
    (:func:`descending_for_order`). Absent phrasing means the analyst
    asserted no order and the pack's own default applies.
    """

    BEST_FIRST = "best_first"
    WORST_FIRST = "worst_first"

    @property
    def phrase(self) -> str:
        """How to name this ordering in a sentence about rank #1."""
        return "best first" if self is AskedOrder.BEST_FIRST else "worst first"


def descending_for_order(order: AskedOrder | None, sign: SignConvention) -> bool | None:
    """Should a ranking on this metric sort descending to honor ``order``?

    ``None`` means the analyst asked for no order (or the metric has no
    polarity for "best" to mean anything against), in which case the
    caller's existing default stands — guessing an order is how a ranking
    ends up contradicting its own narration.
    """
    if order is None or sign is SignConvention.NEUTRAL:
        return None
    higher_is_better = sign is SignConvention.HIGHER_IS_GOOD
    if order is AskedOrder.BEST_FIRST:
        return higher_is_better
    return not higher_is_better


def adverse_delta_sign(sign: SignConvention) -> int | None:
    """Which way a movement in this metric is *bad*, or ``None`` if neither.

    The ordering an unprompted comparison should use: worst first. A
    higher-is-bad measure worsens by rising, a higher-is-good one by
    falling, and a neutral one has no worse direction to lead with.
    """
    if sign is SignConvention.HIGHER_IS_BAD:
        return 1
    if sign is SignConvention.HIGHER_IS_GOOD:
        return -1
    return None


def wanted_delta_sign(direction: AskedDirection | None, sign: SignConvention) -> int | None:
    """The sign of a delta that answers ``direction`` for this metric.

    ``+1`` wants a rise, ``-1`` wants a fall, ``None`` means the analyst
    asserted no direction (or the metric has no polarity to read
    "worsened" against, in which case guessing would be worse than not
    filtering at all).
    """
    if direction is None:
        return None
    if direction is AskedDirection.INCREASE:
        return 1
    if direction is AskedDirection.DECREASE:
        return -1
    if sign is SignConvention.NEUTRAL:
        return None
    worse_is_up = sign is SignConvention.HIGHER_IS_BAD
    if direction is AskedDirection.WORSENED:
        return 1 if worse_is_up else -1
    return -1 if worse_is_up else 1  # IMPROVED


@dataclass(frozen=True, slots=True)
class PackVersionRef:
    pack_id: str
    version: str

    def __post_init__(self) -> None:
        if not self.pack_id or not self.version:
            raise ValueError("PackVersionRef requires pack_id and version")


@dataclass(frozen=True, slots=True)
class ContextPin:
    """A user-declared sticky scope element (design §7.2, carryover law 5).

    Pinned predicates are conjoined into effective scope on every turn and
    survive ``ResetContext(keep_pins=True)``; they clear only explicitly.
    """

    predicate: Predicate
    declared_at_turn: str


@dataclass(frozen=True, slots=True)
class InvestigationContext:
    window: TimeWindow
    comparison: Comparison | None
    scope: FilterExpr
    cohort: CohortRef | None
    grain: Grain
    watermark: DataWatermark
    pack_version: PackVersionRef
    pins: tuple[ContextPin, ...] = ()

    def effective_scope(self) -> FilterExpr:
        """Scope with pinned predicates conjoined (what probes actually see)."""
        pinned = tuple(pin.predicate for pin in self.pins)
        if not pinned:
            return self.scope
        return and_merge(self.scope, *pinned)


@dataclass(frozen=True, slots=True)
class AnalysisSpec:
    """Context plus the plan-shaping components refinements may edit."""

    context: InvestigationContext
    measures: tuple[MetricRef, ...]
    dimensions: tuple[DimensionRef, ...] = ()
    rank_by: MetricRef | None = None
    rank_descending: bool = True
    limit: int | None = None
    #: Pack concept ids this investigation is *about* (validated against the
    #: pack at interpretation time — a closed set, never free text). They
    #: carry across refinements because refining scope does not change what
    #: the question is about, and grading needs them: the same field is
    #: direct evidence for one concept and only a proxy for another (§5.5).
    concepts: tuple[str, ...] = ()
    #: The movement the analyst asked about, when they asked about one — a
    #: closed set, validated at interpretation time. It rides on the spec
    #: (and from there onto the plan) because *selection* has to know it:
    #: ranking a compare frame by delta without it answers "biggest
    #: increase" with the biggest decreases.
    direction: AskedDirection | None = None
    #: The extremity the analyst phrased ("biggest"/"smallest"), when they
    #: phrased one. Meaningless without a direction to take it over.
    magnitude: AskedMagnitude | None = None
    #: The order the analyst asked a ranking to arrive in, when they asked
    #: for one ("best to worst"). Honored in selection AND in narration —
    #: "ranks first" has to mean what the question asked it to mean.
    order: AskedOrder | None = None
    #: Whether ``direction`` was ASSERTED as fact by the question ("why did
    #: denials double") rather than asked as a query ("which payers rose
    #: most"). The two want opposite treatments: a query selects the cells
    #: that moved that way, while an assertion is a *premise* that has to be
    #: verified against the aggregate before any cell is offered as its
    #: explanation. Live, "why did denials at Federal Medicare double in
    #: July" was answered with the only three rising CARC cells — $3,204 of
    #: increases — inside a move from $58,983.54 to $10,915.24, a fall of
    #: 81% that the answer never mentioned.
    direction_asserted: bool = False

    def with_context(self, context: InvestigationContext) -> AnalysisSpec:
        return replace(self, context=context)


def empty_context(
    window: TimeWindow,
    grain: Grain,
    watermark: DataWatermark,
    pack_version: PackVersionRef,
) -> InvestigationContext:
    return InvestigationContext(
        window=window,
        comparison=None,
        scope=EMPTY_SCOPE,
        cohort=None,
        grain=grain,
        watermark=watermark,
        pack_version=pack_version,
    )
