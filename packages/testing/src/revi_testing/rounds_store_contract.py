"""Reusable contract suite for the four Rounds stores (design §15, §18.1).

The same discipline as :mod:`revi_testing.store_contract`: every
implementation of the Rounds ports — the API's in-memory fallback and the
Postgres adapters alike — passes one behavioural suite, so a divergence
between the backing a demo runs on and the backing a deployment runs on
fails here rather than in production.

Three properties this suite exists to hold, each of which a plausible
implementation gets wrong:

* **Round-trip fidelity of the SPEC.** A pin's typed spec is the thing that
  decides what a tile measures every morning. A store that dropped a filter
  or floated a Decimal would produce a watch that quietly measures
  something else.
* **Ordering by the LOAD's own clock.** "The prior load" is decided by
  ``watermark_loaded_at``, never by watermark id. The fixtures below name
  watermarks whose id order and clock order DISAGREE, so a store that sorts
  lexically fails.
* **Soft archive.** Un-pinning keeps the row and its evaluated history, and
  a second un-pin keeps the FIRST timestamp — the same rule the session
  archive follows, for the same reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from revi_investigation.application.ports import (
    RoundsLead,
    RoundsLeadStore,
    RoundsLoad,
    RoundsLoadStore,
    RoundsPin,
    RoundsPinResult,
    RoundsPinResultStore,
    RoundsPinStore,
    RoundsWatch,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.refinements import AddFilterModel, WindowSpecModel

_T0 = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class RoundsStores:
    """The four Rounds ports over one shared backing state."""

    pins: RoundsPinStore
    results: RoundsPinResultStore
    loads: RoundsLoadStore
    leads: RoundsLeadStore


def _token() -> str:
    return uuid4().hex[:12]


def _spec() -> TypedInvestigationSpec:
    """A spec exercising every part a store could drop: several metrics, a
    dimension, two filters with different predicate ops, a relative window,
    an explicit basis and a comparison."""
    return TypedInvestigationSpec(
        metric_ids=["denial_rate", "denied_dollars"],
        dimensions=["payer"],
        filters=[
            AddFilterModel(
                op="add_filter", dimension="payer", predicate_op="eq", values=["Meridian Health"]
            ),
            AddFilterModel(
                op="add_filter", dimension="carc", predicate_op="in", values=["197", "50"]
            ),
        ],
        window=WindowSpecModel(quantity="3.25", unit="month", mode="trailing"),
        basis="service",
        comparison="prior_period",
    )


def _pin(token: str, *, tenant: str = "demo-tenant", suffix: str = "") -> RoundsPin:
    return RoundsPin(
        id=f"pin_{token}{suffix}",
        tenant=tenant,
        label="Meridian denial rate",
        spec=_spec(),
        presentation="chart",
        window_mode="relative",
        created_at=_T0,
        created_from_kind="artifact",
        created_from_investigation_id=f"inv_{token}",
        created_from_referent="F1",
        watch=RoundsWatch(
            mode="delta_gte",
            value=Decimal("0.5"),
            unit="points",
            direction="up",
            note="only tell me when it gets worse",
        ),
        created_by="analyst-1",
    )


#: Two loads whose ID order and CLOCK order disagree, on purpose: a store
#: that orders history lexically returns them backwards and fails.
_LOADS: tuple[tuple[str, datetime], ...] = (
    ("wm_zeta", _T0),
    ("wm_alpha", _T0 + timedelta(days=1)),
)


def _result(token: str, pin_id: str, index: int) -> RoundsPinResult:
    watermark_id, loaded_at = _LOADS[index]
    return RoundsPinResult(
        pin_id=pin_id,
        tenant="demo-tenant",
        watermark_id=f"{watermark_id}_{token}",
        watermark_loaded_at=loaded_at,
        evaluated_at=loaded_at + timedelta(hours=1),
        payload={
            "pin_id": pin_id,
            "value": 0.1234,
            "unit": "ratio",
            "integrity": {"grade": "derived", "things_to_know": 2},
            "nested": {"list": [1, 2, 3], "null": None},
        },
    )


def _load(token: str, index: int, *, tenant: str = "demo-tenant") -> RoundsLoad:
    watermark_id, loaded_at = _LOADS[index]
    return RoundsLoad(
        tenant=tenant,
        watermark_id=f"{watermark_id}_{token}",
        watermark_loaded_at=loaded_at,
        evaluated_at=loaded_at + timedelta(hours=1),
        payload={"leads": {"ANM-001": {"title": "Denial spike", "ranked_impact_cents": 123456}}},
    )


def _lead(token: str, *, tenant: str = "demo-tenant", anomaly_id: str = "ANM-001") -> RoundsLead:
    return RoundsLead(
        tenant=tenant,
        anomaly_id=anomaly_id,
        status="resolved_claimed",
        updated_at=_T0,
        note=f"worked by the denials team ({token})",
        claimed_at_watermark="wm_zeta",
        baseline_cents=178_216_82,
        baseline_basis="this platform's own re-derivation at the claim load",
        confirming_watermarks=("wm_alpha",),
        verification_note="1 of the 2 consecutive loads the governed rule requires",
        history=(
            {"at": _T0.isoformat(), "from": "open", "to": "working", "by": "analyst-1"},
            {"at": _T0.isoformat(), "from": "working", "to": "resolved_claimed", "by": "analyst-1"},
        ),
    )


class RoundsStoreContract:
    """Port-semantics suite for the four Rounds stores.

    Subclass (with a ``Test``-prefixed name) and provide ``rounds``.
    """

    @pytest.fixture
    def rounds(self) -> RoundsStores:
        raise NotImplementedError("contract subclasses must provide a rounds fixture")

    # ------------------------------------------------------------- 1. pins

    async def test_pin_round_trip_keeps_the_whole_spec(self, rounds: RoundsStores) -> None:
        """The spec is what decides what a tile measures every morning: a
        store that dropped a filter would produce a watch that quietly
        measures a different population."""
        pin = _pin(_token())
        await rounds.pins.save(pin)

        loaded = await rounds.pins.get(pin.id)

        assert loaded == pin
        assert loaded is not None
        assert loaded.spec == _spec()
        assert loaded.watch is not None
        # Exact, not approximately: a threshold that round-trips through a
        # float is not the threshold the analyst set.
        assert loaded.watch.value == Decimal("0.5")
        assert isinstance(loaded.watch.value, Decimal)

    async def test_a_pin_with_no_watch_reads_back_without_one(
        self, rounds: RoundsStores
    ) -> None:
        """``None`` means "the governed default", which is a different fact
        from a watch with default fields — and the two must not converge."""
        token = _token()
        pin = RoundsPin(
            id=f"pin_{token}",
            tenant="demo-tenant",
            label="bare",
            spec=_spec(),
            presentation="scalar",
            window_mode="absolute",
            created_at=_T0,
        )
        await rounds.pins.save(pin)

        loaded = await rounds.pins.get(pin.id)

        assert loaded == pin
        assert loaded is not None and loaded.watch is None
        assert loaded.created_from_kind == "spec"

    async def test_pin_get_missing_returns_none(self, rounds: RoundsStores) -> None:
        assert await rounds.pins.get(f"pin_missing_{_token()}") is None

    async def test_listing_is_scoped_to_one_tenant(self, rounds: RoundsStores) -> None:
        token = _token()
        mine = _pin(token, tenant=f"t_mine_{token}")
        theirs = _pin(token, tenant=f"t_theirs_{token}", suffix="_x")
        await rounds.pins.save(mine)
        await rounds.pins.save(theirs)

        listed = await rounds.pins.list_for_tenant(mine.tenant)

        assert [pin.id for pin in listed] == [mine.id]

    async def test_archiving_is_soft_idempotent_and_keeps_the_first_timestamp(
        self, rounds: RoundsStores
    ) -> None:
        """Un-pinning keeps the row: the evaluated history a brief already
        published stays readable, and a permalink into a tile's
        investigation does not 404 because somebody tidied their Rounds."""
        token = _token()
        pin = _pin(token, tenant=f"t_{token}")
        await rounds.pins.save(pin)

        await rounds.pins.archive(pin.id)
        archived = await rounds.pins.get(pin.id)
        assert archived is not None and archived.archived_at is not None
        first_stamp = archived.archived_at

        await rounds.pins.archive(pin.id)  # idempotent
        again = await rounds.pins.get(pin.id)
        assert again is not None and again.archived_at == first_stamp

        assert await rounds.pins.list_for_tenant(pin.tenant) == ()
        assert [p.id for p in await rounds.pins.list_for_tenant(
            pin.tenant, include_archived=True
        )] == [pin.id]

    async def test_archiving_an_unknown_pin_is_a_no_op(self, rounds: RoundsStores) -> None:
        await rounds.pins.archive(f"pin_missing_{_token()}")  # must not raise

    async def test_tenants_with_pins_excludes_archived_and_deduplicates(
        self, rounds: RoundsStores
    ) -> None:
        """What a scheduled walk iterates. A tenant whose only watch was
        un-pinned costs a session and a query for no tile."""
        token = _token()
        tenant = f"t_{token}"
        first = _pin(token, tenant=tenant)
        second = _pin(token, tenant=tenant, suffix="_b")
        await rounds.pins.save(first)
        await rounds.pins.save(second)

        assert (await rounds.pins.tenants_with_pins()).count(tenant) == 1

        await rounds.pins.archive(first.id)
        await rounds.pins.archive(second.id)
        assert tenant not in await rounds.pins.tenants_with_pins()

    # ---------------------------------------------------------- 2. results

    async def test_result_round_trip_and_key_isolation(self, rounds: RoundsStores) -> None:
        token = _token()
        pin_id = f"pin_{token}"
        first = _result(token, pin_id, 0)
        await rounds.results.put(first)

        assert await rounds.results.get(pin_id, first.watermark_id) == first
        assert await rounds.results.get(pin_id, f"wm_other_{token}") is None
        assert await rounds.results.get(f"pin_other_{token}", first.watermark_id) is None

    async def test_re_evaluating_a_load_replaces_its_tile(self, rounds: RoundsStores) -> None:
        """Last write wins, unlike the evidence cache: re-evaluating a load
        is legitimate (a redeployed pack, a repaired snapshot) and the newer
        tile is the one the platform stands behind. The key still asserts
        (pin, load), so there is never more than one tile per pair."""
        token = _token()
        pin_id = f"pin_{token}"
        original = _result(token, pin_id, 0)
        await rounds.results.put(original)
        replacement = RoundsPinResult(
            pin_id=original.pin_id,
            tenant=original.tenant,
            watermark_id=original.watermark_id,
            watermark_loaded_at=original.watermark_loaded_at,
            evaluated_at=original.evaluated_at + timedelta(hours=2),
            payload={"pin_id": pin_id, "value": 0.99},
        )
        await rounds.results.put(replacement)

        assert await rounds.results.get(pin_id, original.watermark_id) == replacement
        assert len(await rounds.results.history(pin_id)) == 1

    async def test_history_is_newest_first_by_the_loads_own_clock(
        self, rounds: RoundsStores
    ) -> None:
        """``wm_alpha`` sorts BEFORE ``wm_zeta`` lexically and AFTER it in
        time. A store that ordered by id would hand a brief the wrong prior
        load and misreport every delta on it."""
        token = _token()
        pin_id = f"pin_{token}"
        older, newer = _result(token, pin_id, 0), _result(token, pin_id, 1)
        await rounds.results.put(newer)
        await rounds.results.put(older)

        history = await rounds.results.history(pin_id)

        assert [r.watermark_id for r in history] == [newer.watermark_id, older.watermark_id]
        assert history[0].watermark_loaded_at > history[1].watermark_loaded_at

    async def test_history_honors_its_limit(self, rounds: RoundsStores) -> None:
        token = _token()
        pin_id = f"pin_{token}"
        await rounds.results.put(_result(token, pin_id, 0))
        await rounds.results.put(_result(token, pin_id, 1))

        assert len(await rounds.results.history(pin_id, limit=1)) == 1

    async def test_history_of_an_unknown_pin_is_empty(self, rounds: RoundsStores) -> None:
        assert await rounds.results.history(f"pin_missing_{_token()}") == ()

    # ------------------------------------------------------------ 3. loads

    async def test_load_round_trip_and_tenant_scoping(self, rounds: RoundsStores) -> None:
        token = _token()
        mine = _load(token, 0, tenant=f"t_mine_{token}")
        theirs = _load(token, 0, tenant=f"t_theirs_{token}")
        await rounds.loads.put(mine)
        await rounds.loads.put(theirs)

        assert await rounds.loads.get(mine.tenant, mine.watermark_id) == mine
        assert [load.tenant for load in await rounds.loads.list_for_tenant(mine.tenant)] == [
            mine.tenant
        ]
        assert await rounds.loads.get(mine.tenant, f"wm_missing_{token}") is None

    async def test_loads_list_newest_first_by_the_loads_own_clock(
        self, rounds: RoundsStores
    ) -> None:
        token = _token()
        tenant = f"t_{token}"
        older = _load(token, 0, tenant=tenant)
        newer = _load(token, 1, tenant=tenant)
        await rounds.loads.put(older)
        await rounds.loads.put(newer)

        listed = await rounds.loads.list_for_tenant(tenant)

        assert [load.watermark_id for load in listed] == [newer.watermark_id, older.watermark_id]
        assert len(await rounds.loads.list_for_tenant(tenant, limit=1)) == 1

    async def test_re_evaluating_a_load_replaces_its_census(
        self, rounds: RoundsStores
    ) -> None:
        token = _token()
        tenant = f"t_{token}"
        first = _load(token, 0, tenant=tenant)
        await rounds.loads.put(first)
        second = RoundsLoad(
            tenant=tenant,
            watermark_id=first.watermark_id,
            watermark_loaded_at=first.watermark_loaded_at,
            evaluated_at=first.evaluated_at + timedelta(hours=3),
            payload={"leads": {}},
        )
        await rounds.loads.put(second)

        assert await rounds.loads.get(tenant, first.watermark_id) == second
        assert len(await rounds.loads.list_for_tenant(tenant)) == 1

    # ------------------------------------------------------------- 4. leads

    async def test_lead_round_trip_with_history_and_streak(
        self, rounds: RoundsStores
    ) -> None:
        """The lifecycle's whole point is that it is auditable: who claimed
        what, when, and which loads have verified it so far."""
        token = _token()
        lead = _lead(token, tenant=f"t_{token}")
        await rounds.leads.put(lead)

        loaded = await rounds.leads.get(lead.tenant, lead.anomaly_id)

        assert loaded == lead
        assert loaded is not None
        assert loaded.confirming_watermarks == ("wm_alpha",)
        assert len(loaded.history) == 2
        assert loaded.history[-1]["to"] == "resolved_claimed"

    async def test_lead_get_missing_returns_none(self, rounds: RoundsStores) -> None:
        assert await rounds.leads.get(f"t_{_token()}", "ANM-999") is None

    async def test_leads_are_tenant_scoped_and_ordered_by_id(
        self, rounds: RoundsStores
    ) -> None:
        token = _token()
        tenant = f"t_{token}"
        second = _lead(token, tenant=tenant, anomaly_id="ANM-002")
        first = _lead(token, tenant=tenant, anomaly_id="ANM-001")
        other = _lead(token, tenant=f"t_other_{token}", anomaly_id="ANM-003")
        await rounds.leads.put(second)
        await rounds.leads.put(first)
        await rounds.leads.put(other)

        listed = await rounds.leads.list_for_tenant(tenant)

        assert [lead.anomaly_id for lead in listed] == ["ANM-001", "ANM-002"]

    async def test_a_lead_update_replaces_its_row(self, rounds: RoundsStores) -> None:
        token = _token()
        tenant = f"t_{token}"
        lead = _lead(token, tenant=tenant)
        await rounds.leads.put(lead)
        confirmed = RoundsLead(
            tenant=tenant,
            anomaly_id=lead.anomaly_id,
            status="resolved_confirmed",
            updated_at=_T0 + timedelta(days=2),
            note=lead.note,
            claimed_at_watermark=lead.claimed_at_watermark,
            baseline_cents=lead.baseline_cents,
            baseline_basis=lead.baseline_basis,
            confirming_watermarks=("wm_alpha", "wm_beta"),
            verification_note="Confirmed for two consecutive loads",
            history=(*lead.history, {"at": "x", "from": "resolved_claimed", "to": "confirmed"}),
        )
        await rounds.leads.put(confirmed)

        assert await rounds.leads.get(tenant, lead.anomaly_id) == confirmed
        assert len(await rounds.leads.list_for_tenant(tenant)) == 1


__all__ = ["RoundsStoreContract", "RoundsStores"]
