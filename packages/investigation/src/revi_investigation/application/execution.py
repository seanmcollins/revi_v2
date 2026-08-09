"""Probe execution: evidence cache first, then the repository, then
small-cell suppression — frames never leave this service unsuppressed.

**Cache.** Keys are ``(probe_hash, watermark id, pack snapshot id)`` per
design §7.9. Hits are re-stamped ``cache_hit=True`` in provenance; misses
are executed as-of the session watermark, suppressed, then cached (the
suppressed frame is cached: suppression is deterministic per catalog
policy, so caching post-policy frames keeps cache reads zero-work).

**Grades.** The repository stamps what an adapter can know — catalog
certification. The planner's §6.6 grade additionally knows the pack's
concept bindings, so it is applied here, after the cache, weakest-wins.
Cache entries therefore stay concept-independent while each answer still
carries the grade its question earned.

**Small-cell suppression policy** (design §15: "aggregates leak in small
cohorts"). The subject of the rule is the **entity population a cell
exposes**, and a cell is never silently dropped.

Frames without a count-unit measure column carry no per-row population
evidence and pass through untouched. Dimension columns are never touched —
the cell's existence may be known; its magnitude may not. Rows with zero
counts stay: "nothing happened" is not a disclosure.

Count columns are then read as two different facts, because they are:

*Population counts* — the denominator of a ratio (``X__den``), or any
standalone count metric. This is the group the row is about. When a
population count ``c`` satisfies ``0 < c < threshold`` the row is genuinely
small and ALL its measure values are nulled, the count included.

*Subset counts* — a ratio's numerator (``X__num``) whose ``X__den`` is
itself a count at or above the threshold. A small numerator over a large
population is not a small cohort: it is a **well-measured, good** cohort.
Nulling it censored exactly the cells doing best. Live: Federal Medicare's
denial rate over 214 adjudicated claims — 9 denials, 4.21% — vanished
entirely, and "which payer has the lowest denial rate" answered *Atlas
Commercial at 8.2%*, confidently and wrongly, with four of twelve payers
censored and all four of them among the best.

So a small numerator over a disclosable population is **bounded, not
dropped**. The numerator cell is replaced by ``threshold - 1`` — the
largest value it could have held, a constant that reveals nothing the act
of suppressing had not already revealed — so every measure derived from it
(the ratio, downstream) computes as a true upper bound rather than NULL.
The cell stays in the frame, the ranking stays complete, and the bound is
recorded on the executed probe (:class:`BoundedCell`) so the turn states
what it bounded and by how much. ``suppressed_cells`` counts bounded cells
alongside nulled ones: in both cases the true value was withheld.

The disclosure guarantee is unchanged. What leaves the engine about a
small numerator is still only "it is under the threshold" — the same fact
full suppression published, minus the collateral censorship of every figure
that shared the row.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.planning import InvestigationPlan
from revi_investigation.application.ports import EvidenceCache, TurnEvent, TurnEventBus
from revi_investigation.application.rendering import metric_label, ratio_pct
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame, FrameRow, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark

_NUMERATOR_SUFFIX = "__num"
_DENOMINATOR_SUFFIX = "__den"
_COUNT_UNIT = "count"


@dataclass(frozen=True, slots=True)
class BoundedCell:
    """A numerator withheld and published as an upper bound instead.

    Carries what a reader needs to judge the answer: which cell, which
    metric, the population the bound is taken over, and the bound itself.
    """

    #: Dimension values identifying the row ("Federal Medicare"), in
    #: schema order. Empty for an ungrouped frame.
    label: str
    #: The ratio metric whose numerator was withheld.
    metric_id: str
    #: The population the numerator sits in — at or above the threshold, or
    #: this would have been a full suppression.
    population: int
    #: The tight upper bound on the ratio: ``(threshold - 1) / population``.
    bound: Decimal


def bounded_cells_warning(cells: Sequence[BoundedCell], threshold: int) -> str | None:
    """The sentence a bounded answer owes its reader, or ``None``.

    Says the two things a bound is useless without: that the figure is an
    upper bound rather than a measurement, and which cells it applies to —
    because a ranking that silently mixes measured and bounded values is
    exactly as misleading as one that drops the bounded rows.
    """
    if not cells:
        return None
    named = "; ".join(
        f"{cell.label or 'the whole population'} "
        f"({metric_label(cell.metric_id)} ≤ {ratio_pct(cell.bound)} "
        f"over {cell.population:,} entities)"
        for cell in sorted(cells, key=lambda c: (c.metric_id, c.label))
    )
    noun = "cell" if len(cells) == 1 else "cells"
    return (
        f"suppression_bounded: {len(cells)} {noun} had fewer than {threshold} entities in the "
        "numerator over a population large enough to publish. Rather than withhold them — which "
        "removes the best-performing cells from a ranking and says nothing — each is shown as an "
        f"UPPER BOUND of at most {threshold - 1} over its own population: {named}. The true "
        "figure is at or below the bound; every other figure here is measured."
    )


def _population_index(frame: EvidenceFrame) -> tuple[list[int], dict[int, tuple[str, int]]]:
    """Split count columns into populations and ratio numerators.

    Returns ``(population_indices, {numerator_index: (metric_id,
    denominator_index)})``. A numerator only counts as a subset when its own
    denominator is a count column in the same frame: without one there is no
    population to bound against, and treating it as a subset would publish a
    bound over a denominator nobody checked.
    """
    by_name = {col.name: i for i, col in enumerate(frame.schema.columns)}
    counts = [
        i
        for i, col in enumerate(frame.schema.columns)
        if isinstance(col.ref, MetricRef) and col.unit == _COUNT_UNIT
    ]
    numerators: dict[int, tuple[str, int]] = {}
    for i in counts:
        name = frame.schema.columns[i].name
        if not name.endswith(_NUMERATOR_SUFFIX):
            continue
        metric_id = name[: -len(_NUMERATOR_SUFFIX)]
        den = by_name.get(f"{metric_id}{_DENOMINATOR_SUFFIX}")
        if den is None or den not in counts:
            continue
        numerators[i] = (metric_id, den)
    populations = [i for i in counts if i not in numerators]
    return populations, numerators


def bounded_cells_of(frame: EvidenceFrame, threshold: int) -> tuple[BoundedCell, ...]:
    """Which cells of a POST-policy frame carry a bound instead of a value.

    Derived from the policed frame rather than remembered from the pass
    that produced it, and therefore identical on a cache hit — the evidence
    cache stores post-policy frames (see the module docstring), and a
    warning that appeared only on a cache miss would make the same question
    honest or silent depending on who asked it first.

    Exact by construction: a bounded numerator holds ``threshold - 1``,
    which is itself under the threshold, so it is recognized by the same
    predicate that bounded it — and a numerator that genuinely equalled
    ``threshold - 1`` would have been bounded to the same value anyway.
    """
    _, numerators = _population_index(frame)
    cells: list[BoundedCell] = []
    dimension_idx = [
        i for i, col in enumerate(frame.schema.columns) if isinstance(col.ref, DimensionRef)
    ]
    for row in frame.rows:
        for i, (metric_id, den) in numerators.items():
            numerator, population = row[i], row[den]
            if not _is_count(numerator) or not _is_count(population):
                continue
            assert isinstance(numerator, int) and isinstance(population, int)
            if not (0 < numerator < threshold) or population < threshold:
                continue
            cells.append(
                BoundedCell(
                    label=" / ".join(
                        str(row[j]) for j in dimension_idx if row[j] is not None
                    ),
                    metric_id=metric_id,
                    population=population,
                    bound=Decimal(threshold - 1) / Decimal(population),
                )
            )
    return tuple(cells)


def _is_count(value: Scalar) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def apply_small_cell_suppression(frame: EvidenceFrame, threshold: int) -> EvidenceFrame:
    """Apply the §15 policy documented in the module docstring.

    Pure; returns the input frame unchanged when nothing needs suppressing.
    Idempotent: re-applying it to its own output changes nothing, which is
    what lets the evidence cache hold post-policy frames.
    """
    measure_idx = [
        i for i, col in enumerate(frame.schema.columns) if isinstance(col.ref, MetricRef)
    ]
    populations, numerators = _population_index(frame)
    if not populations and not numerators:
        return frame

    def is_small(value: Scalar) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 < value < threshold

    def as_count(value: Scalar) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return value

    suppressed_cells = 0
    rows: list[FrameRow] = []
    changed = False
    for row in frame.rows:
        # A small POPULATION is the disclosure the rule exists for: the
        # whole row goes, exactly as it always has.
        if any(is_small(row[i]) for i in populations):
            new_row = list(row)
            for i in measure_idx:
                if new_row[i] is not None:
                    new_row[i] = None
                    suppressed_cells += 1
            rows.append(tuple(new_row))
            changed = True
            continue
        # A small numerator over a large population is bounded, not dropped:
        # the true value is withheld and replaced by the largest value it
        # could have held, so every measure derived from it stays a number.
        small = [
            i
            for i, (_, den) in numerators.items()
            if is_small(row[i])
            and (population := as_count(row[den])) is not None
            and population >= threshold
            and row[i] != threshold - 1  # already at the bound: nothing withheld twice
        ]
        if not small:
            rows.append(row)
            continue
        new_row = list(row)
        for i in small:
            new_row[i] = threshold - 1
            suppressed_cells += 1
        rows.append(tuple(new_row))
        changed = True
    if not changed:
        return frame
    return replace(
        frame,
        rows=tuple(rows),
        suppressed_cells=frame.suppressed_cells + suppressed_cells,
    )


@dataclass(frozen=True, slots=True)
class ExecutedProbe:
    node_id: str
    frame: EvidenceFrame
    cache_hit: bool
    #: Wall clock for this probe, cache lookup included. Recorded because
    #: "which probe was slow?" is the first question a stalled turn raises
    #: and the trace could not answer it: stage timings covered the whole
    #: execute stage, whatever it contained.
    duration_ms: int = 0
    #: Numerators this probe withheld and published as upper bounds. Carried
    #: on the probe rather than the frame so no cross-package frame shape
    #: changes; the turn assembles them into one warning.
    bounded_cells: tuple[BoundedCell, ...] = ()


class ExecuteInvestigationService:
    """Execute every probe node of a validated plan, cache-first."""

    def __init__(
        self,
        repository: AnalyticalRepository,
        cache: EvidenceCache,
        events: TurnEventBus,
        catalog: CatalogSnapshot,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._events = events
        self._threshold = catalog.suppression.threshold

    @property
    def suppression_threshold(self) -> int:
        """The §15 threshold this engine executes under.

        Published because the layer that *states* the policy to the analyst
        is the turn, not the executor, and a turn that had to reach for its
        own catalog to say "fewer than 11" would be a second place the
        number lives.
        """
        return self._threshold

    async def execute(
        self,
        plan: InvestigationPlan,
        *,
        watermark: DataWatermark,
        pack_snapshot_id: str,
        turn_id: str,
        grades: Mapping[str, EvidenceGrade] | None = None,
    ) -> tuple[ExecutedProbe, ...]:
        executed: list[ExecutedProbe] = []
        total = len(plan.nodes)
        for index, node in enumerate(plan.nodes):
            started = time.monotonic()
            digest = node.hash
            cached = await self._cache.get(digest, watermark.id, pack_snapshot_id)
            if cached is not None:
                frame = cached
                if isinstance(frame.provenance, ProbeProvenance) and not frame.provenance.cache_hit:
                    frame = replace(frame, provenance=replace(frame.provenance, cache_hit=True))
                hit = True
            else:
                frame = await self._repository.execute(node.probe, watermark=watermark)
                frame = apply_small_cell_suppression(frame, self._threshold)
                await self._cache.put(digest, watermark.id, pack_snapshot_id, frame)
                hit = False
            # The §6.6 grade lands here, AFTER the cache: the adapter can
            # only see catalog certification, while binding strength depends
            # on the concept being asked about. Caching the adapter's frame
            # keeps entries concept-independent (the same bytes serve a
            # denial question and a COB question); the weaker of the two
            # grades is what the answer is allowed to claim.
            planned = (grades or {}).get(node.id)
            if planned is not None and planned.strength < frame.evidence_grade.strength:
                frame = replace(frame, evidence_grade=planned)
            await self._events.publish(
                TurnEvent(
                    kind="stage",
                    turn_id=turn_id,
                    payload={
                        "stage": "executing",
                        "probe_id": node.id,
                        "i": index + 1,
                        "n": total,
                        "cache_hit": hit,
                    },
                )
            )
            executed.append(
                ExecutedProbe(
                    node_id=node.id,
                    frame=frame,
                    cache_hit=hit,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    # Derived from the policed frame, so a cache hit says
                    # exactly what a cache miss says.
                    bounded_cells=bounded_cells_of(frame, self._threshold),
                )
            )
        return tuple(executed)
