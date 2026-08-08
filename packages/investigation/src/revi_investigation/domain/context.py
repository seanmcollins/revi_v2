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

from revi_kernel.cohort import CohortRef
from revi_kernel.filters import EMPTY_SCOPE, FilterExpr, Predicate, and_merge
from revi_kernel.refs import DimensionRef, Grain, MetricRef
from revi_kernel.scope import Comparison, TimeWindow
from revi_kernel.watermark import DataWatermark


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
