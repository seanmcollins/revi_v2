"""Repository port and capability negotiation (design §6.3).

The planner checks capabilities at validation time, yielding
``SOURCE_CAPABILITY_UNSUPPORTED`` rather than a runtime adapter failure.
No warehouse cursor, driver type, identifier, or database exception may
cross this boundary.

What a source *computes*, not only what it *supports*
====================================================
The original descriptor answered five yes/no questions about retrieval
mechanics (as-of reads, cohort semi-joins, HAVING pushdown, server-side
top-N, cohort size). That was the whole variation between adapters when
it was written, and it stopped being true the moment an adapter learned
to compute a measure at probe time rather than read it from a column.

An adapter that derives ``payment_lag_days`` from two governed date
columns, or aggregates a ratio whose numerator and denominator live at
different entity grains, can answer contracts the catalog alone says are
unanswerable — and a validator consulting only the catalog refuses them
with a sentence about the source the source disproves. The fix is not a
second hardcoded list inside the validator (that is what produced the
gap); it is the adapter declaring what it can do and the validator
reading the declaration.

Both additions default to "nothing extra", so an adapter that never
learned the new tricks keeps the old, honest refusal. Silence advertises
no capability — it is never read as permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from revi_kernel.cohort import CohortDefinition, CohortMaterialization
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import EvidenceProbe, ProbeShape
from revi_kernel.watermark import DataWatermark


@dataclass(frozen=True, slots=True)
class DerivedMeasure:
    """One measure a source computes at probe time rather than storing.

    ``field`` is the field id a metric contract names in a ``sum:`` or
    ``count_distinct:``; ``entity`` is the *catalog entity name* it is
    defined at (the same name the adapter checks the probe entity
    against); ``shapes`` are the probe shapes that can compute it.

    The shapes are load-bearing rather than decorative. A snapshot-time
    age (billed dollars weighted by days outstanding *as of* a date) has
    no meaning inside a flow aggregation, and an adapter that refuses it
    at execute time must be refusable at plan time for the same reason —
    otherwise the two layers disagree about the same probe and the
    analyst learns which one was right by clicking.
    """

    field: str
    entity: str
    shapes: frozenset[ProbeShape]

    def computable_in(self, shape: ProbeShape) -> bool:
        return shape in self.shapes


@dataclass(frozen=True, slots=True)
class RepositoryCapabilities:
    as_of_reads: bool
    cohort_semijoin: bool
    max_cohort_size: int | None
    having_pushdown: bool
    server_side_top_n: bool
    #: Probe-time derived measures this source computes (design §6.3).
    #: Empty means "everything must already be a catalog measure or a
    #: declared column", which is what every adapter advertised before
    #: any of them learned otherwise.
    derived_measures: tuple[DerivedMeasure, ...] = ()
    #: Whether one **aggregation** probe may sum components declared at
    #: different entity grains — the ratio-of-sums construction where each
    #: side aggregates the identical window, scope and group keys against
    #: its own base view and the sides are joined on those keys. False
    #: means a measure must live at the probe's own entity, full stop.
    #: Snapshots are excluded by construction: a snapshot aggregates one
    #: entity as-of a date.
    cross_entity_ratio_of_sums: bool = False

    def derived_at(self, field_id: str, entity_name: str) -> DerivedMeasure | None:
        """The advertised derivation of ``field_id`` at ``entity_name``."""
        for measure in self.derived_measures:
            if measure.field == field_id and measure.entity == entity_name:
                return measure
        return None

    def derived_anywhere(self, field_id: str) -> tuple[DerivedMeasure, ...]:
        """Every advertised derivation of ``field_id``, at any entity."""
        return tuple(m for m in self.derived_measures if m.field == field_id)


class AnalyticalRepository(Protocol):
    """The analytical data plane port. DuckDB now; Snowflake later."""

    def capabilities(self) -> RepositoryCapabilities: ...

    async def execute(self, probe: EvidenceProbe, *, watermark: DataWatermark) -> EvidenceFrame: ...

    async def materialize_cohort(
        self, definition: CohortDefinition, *, watermark: DataWatermark
    ) -> CohortMaterialization: ...

    async def list_watermarks(self) -> tuple[DataWatermark, ...]:
        """All completed loads, oldest first. Sessions pin the newest."""
        ...
