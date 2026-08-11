"""Re-evaluate a tenant's monitor tiles at the newest load, ignoring cache.

``MonitorsService.evaluate_load`` is idempotent per ``(pin, watermark)``:
a stored result is reused rather than recomputed, which is what keeps the
scheduled sweep and the brief route to one evaluation between them. The
cost is that a tile stored before a copy change goes on serving the old
sentences for as long as the watermark stands — the demo tenant's tiles
were still saying "the governed gate of 0.5 points" after the language pass
had replaced that phrasing everywhere it is composed.

``force=True`` is the documented escape hatch for exactly this ("for a
redeployed pack, or a repaired snapshot"). This script is the one-line
operator entry point to it, so the recovery is a command rather than a
paragraph in a runbook.

    uv run python scripts/force_monitor_reevaluation.py --tenant demo

Nothing about the evaluation itself changes: the same pins, the same
warehouse, the same verification of claimed resolutions. Only the reuse is
skipped.
"""

from __future__ import annotations

import argparse
import asyncio

from revi_api.service import ApiService
from revi_api.wiring import build_components


async def _run(tenant: str) -> int:
    components = build_components()
    service = ApiService(components)
    # The newest load this warehouse holds. ``list_watermarks`` is the
    # repository's own accessor and returns them in order, so the last is
    # the one the API serves.
    watermark = (await components.repository.list_watermarks())[-1]
    load = await service.monitors.evaluate_load(tenant, watermark, force=True)
    pins = await components.monitors_pins.list_for_tenant(tenant)
    print(
        f"tenant={tenant} watermark={load.watermark_id} "
        f"evaluated_at={load.evaluated_at.isoformat()} pins={len(pins)}"
    )
    for pin in pins:
        result = await components.monitors_results.get(pin.id, watermark.id)
        print(f"  {pin.id}: {'re-evaluated' if result is not None else 'no result'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default="demo", help="tenant to re-evaluate")
    args = parser.parse_args()
    return asyncio.run(_run(args.tenant))


if __name__ == "__main__":  # pragma: no cover - operator entry point
    raise SystemExit(main())
