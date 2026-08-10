"""Monitors evaluation on watermark advance, inside the API process.

The exact shape of :mod:`revi_api.cohort_sweep`, for the same reason: the
process that owns the state is the process that maintains it. A proactive
surface whose evaluation only happens when somebody opens the app is not
proactive — and the deployment where that matters most is the idle one,
where nobody has opened the app since the load landed.

So the API process monitors for a watermark advance and evaluates every
tenant's Monitors at the new load: every active monitor re-run, every claimed
resolution verified, the detection-feed census recorded. The brief route
calls the *same* :meth:`~revi_api.monitors.MonitorsService.evaluate_load`
primitive, so a brief for a load the sweep has not reached yet is computed
rather than empty, and the two paths cannot drift.

Four deliberate choices, three of them inherited from the cohort sweep
because they were right there:

* **Watermark-triggered, not clock-triggered.** The interval decides how
  often the process ASKS whether a new load has landed; evaluation happens
  on advance. Re-evaluating the same load every tick would re-run every
  monitor against data that has not changed.
* **Never fatal.** A failed evaluation is logged and the loop continues. A
  proactive surface must not be able to take down the surface that answers
  questions; the next tick tries again, and evaluation is idempotent per
  (pin, load) so a partial run resumes rather than duplicating.
* **Switch-offable.** ``REVI_MONITORS_SWEEP_INTERVAL_SECONDS=0`` disables the
  loop for a deployment that evaluates out of band. The brief route still
  works — it evaluates what it needs.
* **Tenant-scoped by the store, not by a list here.** The pin store answers
  "which tenants hold an active monitor", so a tenant with nothing pinned
  costs no query and no session.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Protocol, runtime_checkable

from revi_kernel.watermark import DataWatermark

logger = logging.getLogger("revi.api.monitors_sweep")

SWEEP_INTERVAL_ENV = "REVI_MONITORS_SWEEP_INTERVAL_SECONDS"

#: Every fifteen minutes. Loads land nightly in this warehouse, so the tick
#: exists to notice one promptly rather than to do work: the check is a
#: single metadata read, and evaluation happens only when the watermark has
#: actually moved.
DEFAULT_SWEEP_INTERVAL_SECONDS = 900.0


@runtime_checkable
class MonitorsEvaluator(Protocol):
    """The slice of the Monitors service this loop needs.

    Structural on purpose, exactly like ``CohortSweeper``: this module
    names no service class, so a deployment that wires a different Monitors
    implementation (or none) is a configuration rather than an import.
    """

    async def evaluate_load(
        self, tenant: str, watermark: DataWatermark, *, force: bool = False
    ) -> object: ...


@runtime_checkable
class TenantSource(Protocol):
    async def tenants_with_pins(self) -> tuple[str, ...]: ...


@runtime_checkable
class WatermarkSource(Protocol):
    async def newest_watermark(self) -> DataWatermark: ...


def sweep_interval_seconds(env: Mapping[str, str]) -> float:
    """``REVI_MONITORS_SWEEP_INTERVAL_SECONDS``; <= 0 disables the loop.

    An unparseable value falls back to the default LOUDLY rather than
    silently disabling the surface — a typo must not quietly turn the
    proactive half of the product off, which is exactly the failure this
    module exists to prevent.
    """
    raw = env.get(SWEEP_INTERVAL_ENV, "").strip()
    if not raw:
        return DEFAULT_SWEEP_INTERVAL_SECONDS
    try:
        return float(raw)
    except ValueError:
        logger.error(
            "%s=%r is not a number — falling back to %.0fs rather than leaving Monitors "
            "un-evaluated",
            SWEEP_INTERVAL_ENV,
            raw,
            DEFAULT_SWEEP_INTERVAL_SECONDS,
        )
        return DEFAULT_SWEEP_INTERVAL_SECONDS


async def evaluate_once(
    monitors: object,
    pins: object,
    watermarks: object,
    *,
    last_watermark_id: str | None = None,
) -> str | None:
    """Evaluate every tenant's Monitors at the newest load. Never raises.

    Returns the watermark id that was evaluated (or the one already
    evaluated, unchanged), so the caller can track advance without keeping
    a second copy of the rule.
    """
    if not isinstance(monitors, MonitorsEvaluator) or not isinstance(pins, TenantSource):
        logger.warning(
            "Monitors evaluation is NOT running in this process: the wired components expose "
            "no evaluator (%s) or no pin store (%s)",
            type(monitors).__name__,
            type(pins).__name__,
        )
        return last_watermark_id
    if not isinstance(watermarks, WatermarkSource):  # pragma: no cover - defensive
        logger.warning("no watermark source is wired — Monitors evaluation is off")
        return last_watermark_id
    try:
        newest = await watermarks.newest_watermark()
    except Exception:
        logger.warning("could not read the newest watermark — Monitors evaluation deferred")
        return last_watermark_id
    if last_watermark_id == newest.id:
        logger.debug("monitors: watermark %s has not advanced; nothing to evaluate", newest.id)
        return last_watermark_id
    try:
        tenants = await pins.tenants_with_pins()
    except Exception:
        logger.exception("monitors: could not list tenants with monitors — the next tick retries")
        return last_watermark_id
    if not tenants:
        logger.info(
            "monitors: watermark advanced to %s and no tenant holds a monitor — nothing to walk",
            newest.id,
        )
        return newest.id
    evaluated = 0
    for tenant in tenants:
        try:
            await monitors.evaluate_load(tenant, newest)
            evaluated += 1
        except Exception:
            # Deliberately broad and deliberately per-tenant: one tenant's
            # broken monitor must not cost every other tenant their brief.
            logger.exception(
                "monitors: evaluation failed for tenant %s at %s — the next tick retries",
                tenant,
                newest.id,
            )
    logger.info(
        "monitors: walked %d of %d tenant(s) at watermark %s", evaluated, len(tenants), newest.id
    )
    # The watermark is recorded as reached even when some tenants failed:
    # evaluation is idempotent per (pin, load), so the next tick would only
    # re-run the tenants that failed anyway — and pretending the load was
    # never seen would re-walk every healthy tenant on every tick.
    return newest.id


class MonitorsSweepScheduler:
    """Startup evaluation plus a periodic one, bound to the app lifespan."""

    def __init__(
        self,
        monitors: object,
        pins: object,
        watermarks: object,
        *,
        interval_seconds: float,
    ) -> None:
        self._monitors = monitors
        self._pins = pins
        self._watermarks = watermarks
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._last: str | None = None

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    @property
    def last_watermark_id(self) -> str | None:
        return self._last

    async def start(self) -> str | None:
        """Evaluate once now, then start the periodic task.

        The startup pass is what makes a restarted deployment current: the
        load that landed while the process was down is walked before the
        first request arrives.
        """
        if not self.enabled:
            logger.warning(
                "%s=%s — periodic Monitors evaluation is DISABLED in this process; briefs are "
                "computed on request instead, which is correct but leaves an idle deployment "
                "un-walked until somebody opens it",
                SWEEP_INTERVAL_ENV,
                self._interval,
            )
            return None
        logger.info(
            "Monitors evaluation enabled: startup walk + a watermark check every %.0fs",
            self._interval,
        )
        self._last = await evaluate_once(
            self._monitors, self._pins, self._watermarks, last_watermark_id=self._last
        )
        self._task = asyncio.create_task(self._loop(), name="revi-monitors-sweep")
        return self._last

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            self._last = await evaluate_once(
                self._monitors, self._pins, self._watermarks, last_watermark_id=self._last
            )
