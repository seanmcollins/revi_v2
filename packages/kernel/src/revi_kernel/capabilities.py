"""Repository port and capability negotiation (design §6.3).

The planner checks capabilities at validation time, yielding
``SOURCE_CAPABILITY_UNSUPPORTED`` rather than a runtime adapter failure.
No warehouse cursor, driver type, identifier, or database exception may
cross this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from revi_kernel.cohort import CohortDefinition, CohortMaterialization
from revi_kernel.frame import EvidenceFrame
from revi_kernel.probes import EvidenceProbe
from revi_kernel.watermark import DataWatermark


@dataclass(frozen=True, slots=True)
class RepositoryCapabilities:
    as_of_reads: bool
    cohort_semijoin: bool
    max_cohort_size: int | None
    having_pushdown: bool
    server_side_top_n: bool


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
