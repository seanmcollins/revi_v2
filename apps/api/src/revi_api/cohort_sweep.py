"""Cohort reclamation inside the API process (review finding D6).

Pinning a cohort writes a TABLE into the analytical warehouse. Until now the
only thing that could reclaim one was ``python -m revi_scheduler.sweep``, a
short-lived CLI that somebody had to remember to run — and the long-lived API
process, the only thing that ever *creates* cohort tables in a deployment,
never called the sweep at all. The measured consequence in a development
warehouse was 214 cohort tables holding 11.9M rows, with the garbage
collector structurally unable to reach any of it.

So the process that makes the garbage now also collects it: one sweep at
startup (which is what reclaims whatever the last run leaked) and one every
``REVI_COHORT_SWEEP_INTERVAL_SECONDS`` thereafter. The CLI remains — for
one-off reclamation, for dry-run inspection, and for warehouses no API
process owns — and both paths call the identical connector primitive, so they
cannot drift.

Three deliberate choices:

* **Never fatal.** A sweep failure is logged and the loop continues. Reclaiming
  storage must not be able to take down the surface that answers questions;
  the next tick tries again.
* **Structurally typed.** This module names no connector. It asks the wired
  repository for a ``drop_expired_cohorts`` method and says so plainly when
  the repository has none (an in-memory test double, or a future read-only
  connector that cannot materialize cohorts and therefore has none to drop).
* **Switch-offable.** ``REVI_COHORT_SWEEP_INTERVAL_SECONDS=0`` disables the
  loop entirely, for a deployment that runs reclamation out of band.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

logger = logging.getLogger("revi.api.cohort_sweep")

SWEEP_INTERVAL_ENV = "REVI_COHORT_SWEEP_INTERVAL_SECONDS"

#: Hourly. The cohort TTL is 24h, so an hourly tick reclaims an expired table
#: well inside its first idle day while costing one cheap metadata query.
DEFAULT_SWEEP_INTERVAL_SECONDS = 3600.0


@runtime_checkable
class CohortSweeper(Protocol):
    """The one repository capability this module needs.

    Structural on purpose: ``AnalyticalRepository`` does not declare cohort
    reclamation (a source that cannot materialize cohorts has none to drop),
    so the API asks for the capability rather than assuming it.
    """

    async def drop_expired_cohorts(self, now: datetime) -> tuple[str, ...]: ...


def sweep_interval_seconds(env: Mapping[str, str]) -> float:
    """``REVI_COHORT_SWEEP_INTERVAL_SECONDS``; <= 0 disables the loop.

    An unparseable value falls back to the default *loudly* rather than
    silently disabling reclamation — a typo must not quietly turn the garbage
    collector off, which is the failure mode this whole module exists for.
    """
    raw = env.get(SWEEP_INTERVAL_ENV, "").strip()
    if not raw:
        return DEFAULT_SWEEP_INTERVAL_SECONDS
    try:
        return float(raw)
    except ValueError:
        logger.error(
            "%s=%r is not a number — falling back to %.0fs rather than leaving cohort "
            "storage unreclaimed",
            SWEEP_INTERVAL_ENV,
            raw,
            DEFAULT_SWEEP_INTERVAL_SECONDS,
        )
        return DEFAULT_SWEEP_INTERVAL_SECONDS


async def sweep_once(repository: object, *, now: datetime | None = None) -> tuple[str, ...]:
    """Reclaim expired and orphaned cohort tables once. Never raises.

    Returns the dropped cohort ids — empty both when there was nothing to
    reclaim and when the sweep could not run, so callers log rather than
    infer.
    """
    if not isinstance(repository, CohortSweeper):
        logger.warning(
            "the wired analytical repository (%s) exposes no drop_expired_cohorts — "
            "cohort reclamation is NOT running in this process",
            type(repository).__name__,
        )
        return ()
    instant = now if now is not None else datetime.now(UTC)
    try:
        dropped = await repository.drop_expired_cohorts(instant)
    except Exception:
        # Deliberately broad: reclaiming storage must never take down the
        # surface that answers questions. The next tick tries again.
        logger.exception("cohort sweep failed at %s — the next tick will retry", instant.isoformat())
        return ()
    if dropped:
        logger.info("cohort sweep dropped %d table(s) at %s", len(dropped), instant.isoformat())
    else:
        logger.debug("cohort sweep found nothing to reclaim at %s", instant.isoformat())
    return dropped


class CohortSweepScheduler:
    """Startup sweep plus a periodic one, bound to the API process lifespan."""

    def __init__(self, repository: object, *, interval_seconds: float) -> None:
        self._repository = repository
        self._interval = interval_seconds
        self._task: asyncio.Task[None] | None = None

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    async def start(self) -> tuple[str, ...]:
        """Sweep once now, then start the periodic task. Returns the startup
        sweep's dropped ids — the number an operator reads a restart by."""
        if not self.enabled:
            logger.warning(
                "%s=%s — periodic cohort reclamation is DISABLED in this process; "
                "expired cohort tables will accumulate unless `make sweep` is run out of band",
                SWEEP_INTERVAL_ENV,
                self._interval,
            )
            return ()
        logger.info("cohort reclamation enabled: startup sweep + every %.0fs", self._interval)
        dropped = await sweep_once(self._repository)
        self._task = asyncio.create_task(self._loop(), name="revi-cohort-sweep")
        return dropped

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
            await sweep_once(self._repository)
