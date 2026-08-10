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
Nulling it censors exactly the cells doing best: a payer's denial rate over
214 adjudicated claims — 9 denials, 4.21% — vanishes entirely, and "which
payer has the lowest denial rate" answers with a payer at 8.2%, confidently
and wrongly, over a population where four of twelve are censored and all
four are among the best.

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
from revi_kernel.frame import (
    EvidenceFrame,
    FrameRow,
    ProbeProvenance,
    withheld_row_indices,
)
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import DimensionRef, MetricRef
from revi_kernel.watermark import DataWatermark

#: How many bounded cells the disclosure names before it summarises.
_MAX_NAMED_BOUNDS = 8

_NUMERATOR_SUFFIX = "__num"
_DENOMINATOR_SUFFIX = "__den"
_COUNT_UNIT = "count"


@dataclass(frozen=True, slots=True)
class BoundedCell:
    """A numerator withheld and published as an upper bound instead.

    Carries what a reader needs to judge the answer: which cell, which
    metric, the population the bound is taken over, and the bound itself.

    ``row_index`` is what makes the bound *addressable*. A bound that exists
    only inside a warning sentence leaves the frame publishing
    ``(threshold - 1) / population`` as a measured point value, so "45.5%
    denial rate" reaches findings, charts, rankings and CSV at grade
    ``direct`` and confidence ``high`` over a figure that is 10/22. The row
    index lets every downstream consumer ask "is THIS cell a measurement?"
    instead of reading prose.
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
    #: Position of the bounded row in the frame it was derived from.
    row_index: int = -1


@dataclass(frozen=True, slots=True)
class SuppressionCensus:
    """How many cells a frame has, and what the policy did to each.

    Computed once, at frame level. The alternative is letting each surface
    derive it, and a narrator deriving it writes "the three payers named
    here are only part of a fifteen-cell set in which several cells were
    withheld" over 12 payer cells of which zero were withheld — three
    surfaces (narrative, chart annotation, probe metadata) publishing three
    different numbers for one control.

    A *cell* here is a row: the unit the reader counts. ``suppressed_cells``
    on the frame counts nulled VALUES, several per row, which is why it can
    never be quoted as a population.
    """

    #: Rows in the frame — every cell the answer is about.
    total: int
    #: Rows carrying an upper bound on at least one measure.
    bounded: int
    #: Rows whose every measure was nulled by the small-population rule.
    withheld: int

    @property
    def measured(self) -> int:
        return max(self.total - self.bounded - self.withheld, 0)

    def as_payload(self) -> dict[str, int]:
        return {
            "total_cells": self.total,
            "bounded_cells": self.bounded,
            "withheld_cells": self.withheld,
            "measured_cells": self.measured,
        }


#: The one plain-English phrase for "the group behind this number is under
#: the publication threshold". Every answer-facing sentence about a ceiling
#: uses it verbatim. Carrying three words for two ideas ("upper bound",
#: "ceiling", "measurement"), printing ``cell(s)`` in machine voice and
#: stating the census twice makes the most important paragraph on the page
#: unreadable; the full technical census lives on the trace instead and the
#: page says it once, in words. :mod:`revi_presentation.narrative` matches
#: this phrase to know the engine has already counted (it may not import
#: this package).
TOO_SMALL_TO_MEASURE = "too small to measure exactly"


def _distinct_cells(cells: Sequence[BoundedCell]) -> list[BoundedCell]:
    """One row per bounded cell, in a stable order.

    Keyed on ``(metric_id, label)`` — the cell, not the probe that produced
    it. A plan that reads one window through two nodes hands the same cell
    in twice, and the disclosure then names a payer twice as if two
    different groups of theirs had been suppressed.
    """
    by_key: dict[tuple[str, str], BoundedCell] = {}
    for cell in sorted(cells, key=lambda c: (c.metric_id, c.label)):
        by_key.setdefault((cell.metric_id, cell.label), cell)
    return list(by_key.values())


def _named_bounds(cells: Sequence[BoundedCell]) -> str:
    named = "; ".join(
        f"{cell.label or 'the whole population'} "
        f"({metric_label(cell.metric_id)} ≤ {ratio_pct(cell.bound)} "
        f"over {cell.population:,} entities)"
        for cell in cells[:_MAX_NAMED_BOUNDS]
    )
    if len(cells) > _MAX_NAMED_BOUNDS:
        # Naming every bounded cell can put >100 parenthesised figures into
        # one mandatory disclosure — a sentence nobody finishes is a
        # disclosure nobody reads. The full set is on the frame and in the
        # chart.
        named += (
            f"; and {len(cells) - _MAX_NAMED_BOUNDS} more, each shown as a bound in the chart"
        )
    return named


def bounded_cells_warning(
    cells: Sequence[BoundedCell],
    threshold: int,
    *,
    census: SuppressionCensus | None = None,
    #: Ceilings from a window this turn read as CONTEXT rather than as the
    #: answer. They belong in the disclosure and not in its count — see
    #: below.
    comparison_cells: Sequence[BoundedCell] = (),
    #: What the rows ARE, in the analyst's word ("payers"). The generic
    #: fallback is what a reader gets when the answer has no cut to name.
    #: Two sentences on one card calling the same four rows "groups" and
    #: "payers" contradict each other about one population.
    noun: str = "groups",
) -> str | None:
    """The sentence a bounded answer owes its reader, or ``None``.

    Says the three things a bound is useless without: that the figure is an
    upper bound rather than a measurement, which cells it applies to, and
    what is left over — because a ranking that silently mixes measured and
    bounded values is exactly as misleading as one that drops the bounded
    rows.

    Two claims this sentence must not make. "Every other figure here is
    measured" is false on a frame where 147 of 150 values are bounds and the
    remaining 3 are zeros; and stating the count without the withheld cells
    beside it lets a reader add the two disclosures and get a population
    that does not exist.

    **The sentence must agree with itself**, three ways:

    * *"4 of 12 groups"* over a list of **five** rows — the count taken from
      the census while the list comes from every probe the plan ran. The
      count is derived from the list it introduces, so the two cannot drift.
    * *the same payer twice*, the second row being that payer's cell in the
      COMPARISON window. Cells are deduplicated by cell key, and a window
      this turn read as context gets its own labelled clause instead of
      being folded into the answer's own census.
    * *"fewer than 11 things sit behind each of those numbers"* printed over
      a row of **214** entities. The §15 rule suppresses the NUMERATOR — the
      events being counted — over a population that is published in full;
      the population floor is a different rule, and stating it here refutes
      the very figures beside it.
    """
    current = _distinct_cells(cells)
    prior = _distinct_cells(comparison_cells)
    if not current and not prior:
        return None
    withheld = (
        f" A further {census.withheld} could not be published at all."
        if census is not None and census.withheld
        else ""
    )
    # The comparison window is a second population and never enters the
    # answer's own arithmetic — it is named, labelled and left there.
    context = (
        ""
        if not prior
        else (
            f" In the comparison window {len(prior)} of them are bounded the same way: "
            f"{_named_bounds(prior)} — those ceilings bound the prior period, not this one."
        )
    )
    #: What a ceiling MEANS, in the terms the policy actually applies.
    rule = (
        f"fewer than {threshold} of the events being counted landed in each, over a "
        "population this answer publishes in full"
    )
    if not current:
        return (
            f"suppression_bounded: every figure this answer publishes for the current window "
            f"is measured. The comparison window is not: {len(prior)} of its "
            f"cells are {TOO_SMALL_TO_MEASURE} — {rule} — so each shows a ceiling instead of "
            f"a figure: {_named_bounds(prior)}. The true value is at or below the ceiling and "
            f"is not a measurement.{withheld}"
        )
    # Stated from the list this sentence prints. The census supplies the
    # population the count is taken OUT of, and can never contradict it: a
    # denominator smaller than its own numerator is not a census.
    bounded = len(current)
    total = max(census.total if census is not None else bounded, bounded)
    group = noun[:-1] if total == 1 and noun.endswith("s") else noun
    return (
        f"suppression_bounded: {bounded} of {total} {group} here are {TOO_SMALL_TO_MEASURE} — "
        f"{rule} — so each shows a ceiling instead of a figure: {_named_bounds(current)}. "
        f"The true value is at or below the ceiling and is not a measurement."
        f"{withheld}{context}"
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
    for row_index, row in enumerate(frame.rows):
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
                    row_index=row_index,
                )
            )
    return tuple(cells)


def bound_index(frame: EvidenceFrame, threshold: int) -> dict[int, dict[str, BoundedCell]]:
    """``{row index: {metric id: bound}}`` for a post-policy frame.

    The addressable form of :func:`bounded_cells_of`, so a findings or
    chart row can ask whether the value it is about to publish is a
    measurement without re-deriving the policy. Derived structurally from
    the frame's own ``__num``/``__den`` columns, which every kernel
    operator carries forward (``ratio`` and ``rank`` append; they never
    drop), so it works the same on a probe frame and on a ranked one.
    """
    index: dict[int, dict[str, BoundedCell]] = {}
    for cell in bounded_cells_of(frame, threshold):
        if cell.row_index < 0:  # pragma: no cover - always set by bounded_cells_of
            continue
        index.setdefault(cell.row_index, {})[cell.metric_id] = cell
    return index


def suppression_census(frame: EvidenceFrame, threshold: int) -> SuppressionCensus:
    """Count this frame's cells once: total, bounded, withheld.

    Defining a withheld row as one whose EVERY metric column came back NULL
    — anatomy columns (``denial_rate__num``, ``denial_rate__den``) included
    — and only on a frame that admits ``suppressed_cells`` counts a trend
    cell whose rate was nulled while its numerator survived as *measured*.
    One payload then publishes "0 were withheld outright" beside a chart
    annotation reading "1 of 8 cells were withheld outright" about the same
    eight rows.

    The rule lives in :func:`revi_kernel.frame.withheld_row_indices` and is
    asked here and by the chart builder alike: a row the answer publishes
    no value for is withheld, whatever nulled it. That is the count a reader
    can check against the marks in front of them.
    """
    bounded_rows = set(bound_index(frame, threshold))
    withheld = len(withheld_row_indices(frame) - frozenset(bounded_rows))
    return SuppressionCensus(
        total=len(frame.rows), bounded=len(bounded_rows), withheld=withheld
    )


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
