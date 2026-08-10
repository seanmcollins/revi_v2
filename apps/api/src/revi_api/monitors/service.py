""":class:`MonitorsService` — the four capabilities assembled into one object."""

from __future__ import annotations

from typing import TYPE_CHECKING

from revi_api.auth import Principal
from revi_api.monitors_policy import (
    MonitorsPolicy,
)
from revi_api.warning_codes import structured_warnings
from revi_investigation.application.ports import (
    MonitorsLoad,
)
from revi_investigation_contracts.monitors import (
    MonitorsResponse,
    MonitorsTilePayload,
)
from revi_kernel.watermark import DataWatermark

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime only
    from revi_api.wiring import ApiComponents

from revi_api.monitors.brief import _BriefComposition
from revi_api.monitors.cards import _CardDecoration
from revi_api.monitors.common import PortfolioFor, _monitors_warnings, _MonitorsBase, _utc
from revi_api.monitors.leads import _LeadLifecycle
from revi_api.monitors.pins import _PinApi
from revi_api.monitors.tiles import _LoadEvaluation


class MonitorsService(
    _PinApi,
    _LoadEvaluation,
    _BriefComposition,
    _LeadLifecycle,
    _CardDecoration,
    _MonitorsBase,
):
    """Pins, per-load evaluation, the brief, and the lead lifecycle.

    One object, one seam per capability. Each base class above is a section
    of this service, kept in its own module so it can be read on its own:
    :mod:`~revi_api.monitors.pins`, :mod:`~revi_api.monitors.tiles`,
    :mod:`~revi_api.monitors.brief`, :mod:`~revi_api.monitors.leads`,
    :mod:`~revi_api.monitors.cards`. What stays here is the constructor and
    the read-only surface the API serves.
    """

    def __init__(
        self,
        components: ApiComponents,
        *,
        portfolio_for: PortfolioFor,
    ) -> None:
        self._components = components
        self._portfolio_for = portfolio_for

    @property
    def policy(self) -> MonitorsPolicy:
        return self._components.monitors_policy

    # ------------------------------------------------------------ the surface

    async def monitors(self, principal: Principal) -> MonitorsResponse:
        """Every active monitor, evaluated at the newest load."""
        return await self.monitors_at(
            principal, await self._components.open_session.newest_watermark()
        )

    async def monitors_at(
        self, principal: Principal, watermark: DataWatermark
    ) -> MonitorsResponse:
        """The surface AT a named load.

        The explicit-watermark form exists because a load-over-load product
        has to be testable across loads: the simulated-load suite drives
        wm_001 → wm_002 → wm_003 through this, which is the same code the
        newest-load route runs. A seam that tests exercise and production
        does not is a seam that proves nothing.
        """
        await self.evaluate_load(principal.tenant, watermark)
        pins = await self._components.monitors_pins.list_for_tenant(principal.tenant)
        tiles: list[MonitorsTilePayload] = []
        for pin in pins:
            stored = await self._components.monitors_results.get(pin.id, watermark.id)
            if stored is not None:
                tiles.append(MonitorsTilePayload.model_validate(stored.payload))
        prior = await self._prior_load(principal.tenant, watermark)
        warnings = _monitors_warnings(self.policy)
        return MonitorsResponse(
            tenant=principal.tenant,
            watermark_id=watermark.id,
            newest_data_date=watermark.newest_data_date,
            prior_watermark_id=prior.watermark_id if prior is not None else None,
            tiles=tiles,
            warnings=warnings,
            warnings_v2=structured_warnings(warnings),
        )

    async def _prior_load(
        self, tenant: str, watermark: DataWatermark
    ) -> MonitorsLoad | None:
        for load in await self._components.monitors_loads.list_for_tenant(tenant, limit=12):
            if _utc(load.watermark_loaded_at) < _utc(watermark.loaded_at):
                return load
        return None
