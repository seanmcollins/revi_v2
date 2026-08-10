"""Rounds across real loads against a REAL Postgres (``-m postgres``).

The memory-backed suite (``test_rounds_loads.py``) proves the behaviour.
This proves it survives the wire to a database — which is where a
load-over-load surface actually breaks:

* the tile payload is JSONB, so a value that does not round-trip comes back
  as a different tile and every delta computed from it is wrong;
* "the prior load" is an ORDER BY on the watermark's own ``loaded_at``, and
  an index or a sort that disagreed with the in-memory store would diff the
  wrong pair of loads silently;
* the lead lifecycle spans loads by definition, so a status that did not
  persist would make every confirmation a first confirmation, forever.

One test, driven end to end across wm_001 → wm_002 → wm_003 with the same
service the API serves — deliberately not a re-run of the whole memory
suite, which would pay a database round-trip to re-assert logic that has
nothing to do with the database.
"""

from __future__ import annotations

from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest

from revi_api.auth import Principal
from revi_api.scripted_llm import demo_language_model
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.refinements import WindowSpecModel
from revi_investigation_contracts.rounds import (
    CreateRoundsPinRequest,
    RoundsLeadPatchRequest,
    RoundsWatchModel,
)
from revi_testing.postgres_harness import ensure_postgres, throwaway_database

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(
        not WAREHOUSE.is_file(),
        reason="generated warehouse missing — run: "
        "uv run python -m revi_warehouse.generate --out data/revi_warehouse.duckdb",
    ),
]

TENANT = "demo"
CALLER = Principal(tenant=TENANT, subject="rounds-postgres-suite")


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    reason = ensure_postgres()
    if reason is not None:
        pytest.skip(reason)
    with throwaway_database() as url:
        yield url


@pytest.fixture
def service(database_url: str) -> ApiService:
    components = build_components(
        {
            "REVI_WAREHOUSE_PATH": str(WAREHOUSE),
            "REVI_DATABASE_URL": database_url,
            "REVI_LLM_MOCK": "1",
        },
        llm=demo_language_model(),
    )
    assert components.store_mode == "postgres", "the suite must exercise the real adapters"
    return ApiService(components)


class TestRoundsOnPostgres:
    async def test_the_whole_surface_survives_the_database(
        self, service: ApiService
    ) -> None:
        first, second, third = await service.components.repository.list_watermarks()

        pin = await service.rounds.create_pin(
            CALLER,
            CreateRoundsPinRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denied_dollars"],
                    dimensions=["payer"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                ),
                label="denied dollars by payer",
                watch=RoundsWatchModel(mode="delta_gte", value=5.0, unit="relative_pct"),
            ),
        )
        # The threshold is a Decimal in the store and comes back a Decimal:
        # a watch is a promise about a specific number, and a threshold that
        # round-tripped through a float in the DATABASE would not be the one
        # the analyst set. (The wire model carries it as a JSON number —
        # JSON has no decimal — so the boundary is the request body, not the
        # column.)
        stored = await service.components.rounds_pins.get(pin.pin_id)
        assert stored is not None and stored.watch is not None
        assert isinstance(stored.watch.value, Decimal)
        assert stored.watch.value == Decimal("5")

        # ---- load 1: the baseline ---------------------------------------
        opening = await service.rounds.brief_at(CALLER, first)
        assert opening.status == "first_load"
        assert opening.pins_evaluated == 1

        surface = await service.rounds.rounds_at(CALLER, first)
        [tile] = surface.tiles
        assert tile.status == "ok", tile.unavailable_reason
        assert tile.value is not None
        assert tile.integrity.checks > 0
        assert tile.integrity.things_to_know == len(tile.warnings_v2)

        # ---- load 2: a claim, and a movement ----------------------------
        await service.rounds.brief_at(CALLER, second)
        claimed = await service.rounds.patch_lead(
            CALLER,
            "ANM-031",
            RoundsLeadPatchRequest(status="resolved_claimed", note="reworked"),
            watermark=second,
        )
        assert claimed.status == "resolved_claimed"
        assert claimed.claimed_at_watermark == second.id

        moved = await service.rounds.rounds_at(CALLER, second)
        [tile_2] = moved.tiles
        assert tile_2.delta is not None
        # The prior load is chosen by the WATERMARK's own clock, and the
        # database has to agree with the in-memory store about which one
        # that is.
        assert tile_2.delta.prior_watermark_id == first.id

        # ---- load 3: the diff, and the verification ---------------------
        brief = await service.rounds.brief_at(CALLER, third)
        assert brief.prior_watermark_id == second.id
        assert brief.materiality.content_hash
        # ANM-031/032/033 leave the feed at wm_003; ANM-031 was claimed, so
        # it is a verification rather than a self-resolution.
        self_resolved = {
            entry.anomaly_id for entry in brief.entries if entry.kind == "self_resolved"
        }
        assert "ANM-031" not in self_resolved
        assert self_resolved & {"ANM-032", "ANM-033"}

        lead = await service.rounds.get_lead(CALLER, "ANM-031")
        assert lead.status in ("resolved_claimed", "resolved_confirmed")
        assert lead.confirming_watermarks == [third.id] or lead.status == "resolved_confirmed"
        assert lead.verification_note
        # The history survived two loads and a process-level round trip.
        assert [entry["to"] for entry in lead.history] == ["resolved_claimed"]

        # ---- the stored history reads back in the right order -----------
        history = await service.components.rounds_results.history(pin.pin_id)
        assert [r.watermark_id for r in history] == [third.id, second.id, first.id]
