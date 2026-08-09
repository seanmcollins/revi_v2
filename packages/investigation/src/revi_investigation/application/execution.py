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

**Small-cell suppression rule** (design §15: "aggregates leak in small
cohorts"): in frames whose schema contains a count-unit measure column, any
row where some count value ``c`` satisfies ``0 < c < threshold`` has ALL
measure values nulled (the count itself included — a small count is itself
a disclosure), and ``suppressed_cells`` grows by the number of cells that
changed from a value to NULL. Rows with zero counts stay: "nothing
happened" is not a disclosure. Frames without a count-unit measure column
carry no per-row population evidence and pass through untouched. Dimension
columns are never touched — the cell's existence may be known; its
magnitude may not.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, replace

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.planning import InvestigationPlan
from revi_investigation.application.ports import EvidenceCache, TurnEvent, TurnEventBus
from revi_kernel.capabilities import AnalyticalRepository
from revi_kernel.filters import Scalar
from revi_kernel.frame import EvidenceFrame, FrameRow, ProbeProvenance
from revi_kernel.grades import EvidenceGrade
from revi_kernel.refs import MetricRef
from revi_kernel.watermark import DataWatermark


def apply_small_cell_suppression(frame: EvidenceFrame, threshold: int) -> EvidenceFrame:
    """Apply the small-cell suppression rule documented in the module
    docstring. Pure; returns the input frame unchanged when nothing needs
    suppressing."""
    measure_idx = [
        i for i, col in enumerate(frame.schema.columns) if isinstance(col.ref, MetricRef)
    ]
    count_idx = [
        i
        for i, col in enumerate(frame.schema.columns)
        if isinstance(col.ref, MetricRef) and col.unit == "count"
    ]
    if not count_idx:
        return frame

    def is_small(value: Scalar) -> bool:
        return isinstance(value, int) and not isinstance(value, bool) and 0 < value < threshold

    suppressed_cells = 0
    rows: list[FrameRow] = []
    changed = False
    for row in frame.rows:
        if any(is_small(row[i]) for i in count_idx):
            new_row = list(row)
            for i in measure_idx:
                if new_row[i] is not None:
                    new_row[i] = None
                    suppressed_cells += 1
            rows.append(tuple(new_row))
            changed = True
        else:
            rows.append(row)
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
                )
            )
        return tuple(executed)
