"""Reusable contract suite for the four Monitors stores (design §15, §18.1).

The same discipline as :mod:`revi_testing.store_contract`: every
implementation of the Monitors ports — the API's in-memory fallback and the
Postgres adapters alike — passes one behavioural suite, so a divergence
between the two backings fails here rather than in production.

Three properties this suite exists to hold, each of which a plausible
implementation gets wrong:

* **Round-trip fidelity of the SPEC.** A pin's typed spec is the thing that
  decides what a tile measures every morning. A store that dropped a filter
  or floated a Decimal would produce a monitor that quietly measures
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
    Monitor,
    MonitorsLead,
    MonitorsLeadStore,
    MonitorsLoad,
    MonitorsLoadStore,
    MonitorsPin,
    MonitorsPinResult,
    MonitorsPinResultStore,
    MonitorsPinStore,
)
from revi_investigation_contracts.api import TypedInvestigationSpec
from revi_investigation_contracts.refinements import AddFilterModel, WindowSpecModel

_T0 = datetime(2026, 8, 7, 12, 0, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class MonitorsStores:
    """The four Monitors ports over one shared backing state."""

    pins: MonitorsPinStore
    results: MonitorsPinResultStore
    loads: MonitorsLoadStore
    leads: MonitorsLeadStore


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


def _pin(token: str, *, tenant: str = "demo-tenant", suffix: str = "") -> MonitorsPin:
    return MonitorsPin(
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
        monitor=Monitor(
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


def _result(token: str, pin_id: str, index: int) -> MonitorsPinResult:
    watermark_id, loaded_at = _LOADS[index]
    return MonitorsPinResult(
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


def _load(token: str, index: int, *, tenant: str = "demo-tenant") -> MonitorsLoad:
    watermark_id, loaded_at = _LOADS[index]
    return MonitorsLoad(
        tenant=tenant,
        watermark_id=f"{watermark_id}_{token}",
        watermark_loaded_at=loaded_at,
        evaluated_at=loaded_at + timedelta(hours=1),
        payload={"leads": {"ANM-001": {"title": "Denial spike", "ranked_impact_cents": 123456}}},
    )


def _lead(token: str, *, tenant: str = "demo-tenant", anomaly_id: str = "ANM-001") -> MonitorsLead:
    return MonitorsLead(
        tenant=tenant,
        anomaly_id=anomaly_id,
        status="resolved_claimed",
        updated_at=_T0,
        note=f"worked by the denials team ({token})",
        claimed_at_watermark="wm_zeta",
        baseline_cents=178_216_82,
        baseline_basis="this platform's own re-derivation at the claim load",
        confirming_watermarks=("wm_alpha",),
        verification_note="1 of the 2 consecutive loads Revi requires",
        history=(
            {"at": _T0.isoformat(), "from": "open", "to": "working", "by": "analyst-1"},
            {"at": _T0.isoformat(), "from": "working", "to": "resolved_claimed", "by": "analyst-1"},
        ),
    )


class MonitorsStoreContract:
    """Port-semantics suite for the four Monitors stores.

    Subclass (with a ``Test``-prefixed name) and provide ``monitors``.
    """

    @pytest.fixture
    def monitors(self) -> MonitorsStores:
        raise NotImplementedError("contract subclasses must provide a monitors fixture")

    # ------------------------------------------------------------- 1. pins

    async def test_pin_round_trip_keeps_the_whole_spec(self, monitors: MonitorsStores) -> None:
        """The spec is what decides what a tile measures every morning: a
        store that dropped a filter would produce a monitor that quietly
        measures a different population."""
        pin = _pin(_token())
        await monitors.pins.save(pin)

        loaded = await monitors.pins.get(pin.id)

        assert loaded == pin
        assert loaded is not None
        assert loaded.spec == _spec()
        assert loaded.monitor is not None
        # Exact, not approximately: a threshold that round-trips through a
        # float is not the threshold the analyst set.
        assert loaded.monitor.value == Decimal("0.5")
        assert isinstance(loaded.monitor.value, Decimal)

    async def test_a_pin_with_no_monitor_reads_back_without_one(
        self, monitors: MonitorsStores
    ) -> None:
        """``None`` means "the governed default", which is a different fact
        from a monitor with default fields — and the two must not converge."""
        token = _token()
        pin = MonitorsPin(
            id=f"pin_{token}",
            tenant="demo-tenant",
            label="bare",
            spec=_spec(),
            presentation="scalar",
            window_mode="absolute",
            created_at=_T0,
        )
        await monitors.pins.save(pin)

        loaded = await monitors.pins.get(pin.id)

        assert loaded == pin
        assert loaded is not None and loaded.monitor is None
        assert loaded.created_from_kind == "spec"

    async def test_pin_get_missing_returns_none(self, monitors: MonitorsStores) -> None:
        assert await monitors.pins.get(f"pin_missing_{_token()}") is None

    async def test_listing_is_scoped_to_one_tenant(self, monitors: MonitorsStores) -> None:
        token = _token()
        mine = _pin(token, tenant=f"t_mine_{token}")
        theirs = _pin(token, tenant=f"t_theirs_{token}", suffix="_x")
        await monitors.pins.save(mine)
        await monitors.pins.save(theirs)

        listed = await monitors.pins.list_for_tenant(mine.tenant)

        assert [pin.id for pin in listed] == [mine.id]

    async def test_archiving_is_soft_idempotent_and_keeps_the_first_timestamp(
        self, monitors: MonitorsStores
    ) -> None:
        """Un-pinning keeps the row: the evaluated history a brief already
        published stays readable, and a permalink into a tile's
        investigation does not 404 because somebody tidied their Monitors."""
        token = _token()
        pin = _pin(token, tenant=f"t_{token}")
        await monitors.pins.save(pin)

        await monitors.pins.archive(pin.id)
        archived = await monitors.pins.get(pin.id)
        assert archived is not None and archived.archived_at is not None
        first_stamp = archived.archived_at

        await monitors.pins.archive(pin.id)  # idempotent
        again = await monitors.pins.get(pin.id)
        assert again is not None and again.archived_at == first_stamp

        assert await monitors.pins.list_for_tenant(pin.tenant) == ()
        assert [p.id for p in await monitors.pins.list_for_tenant(
            pin.tenant, include_archived=True
        )] == [pin.id]

    async def test_archiving_an_unknown_pin_is_a_no_op(self, monitors: MonitorsStores) -> None:
        await monitors.pins.archive(f"pin_missing_{_token()}")  # must not raise

    async def test_tenants_with_pins_excludes_archived_and_deduplicates(
        self, monitors: MonitorsStores
    ) -> None:
        """What a scheduled walk iterates. A tenant whose only monitor was
        un-pinned costs a session and a query for no tile."""
        token = _token()
        tenant = f"t_{token}"
        first = _pin(token, tenant=tenant)
        second = _pin(token, tenant=tenant, suffix="_b")
        await monitors.pins.save(first)
        await monitors.pins.save(second)

        assert (await monitors.pins.tenants_with_pins()).count(tenant) == 1

        await monitors.pins.archive(first.id)
        await monitors.pins.archive(second.id)
        assert tenant not in await monitors.pins.tenants_with_pins()

    # ---------------------------------------------------------- 2. results

    async def test_result_round_trip_and_key_isolation(self, monitors: MonitorsStores) -> None:
        token = _token()
        pin_id = f"pin_{token}"
        first = _result(token, pin_id, 0)
        await monitors.results.put(first)

        assert await monitors.results.get(pin_id, first.watermark_id) == first
        assert await monitors.results.get(pin_id, f"wm_other_{token}") is None
        assert await monitors.results.get(f"pin_other_{token}", first.watermark_id) is None

    async def test_re_evaluating_a_load_replaces_its_tile(self, monitors: MonitorsStores) -> None:
        """Last write wins, unlike the evidence cache: re-evaluating a load
        is legitimate (a redeployed pack, a repaired snapshot) and the newer
        tile is the one the platform stands behind. The key still asserts
        (pin, load), so there is never more than one tile per pair."""
        token = _token()
        pin_id = f"pin_{token}"
        original = _result(token, pin_id, 0)
        await monitors.results.put(original)
        replacement = MonitorsPinResult(
            pin_id=original.pin_id,
            tenant=original.tenant,
            watermark_id=original.watermark_id,
            watermark_loaded_at=original.watermark_loaded_at,
            evaluated_at=original.evaluated_at + timedelta(hours=2),
            payload={"pin_id": pin_id, "value": 0.99},
        )
        await monitors.results.put(replacement)

        assert await monitors.results.get(pin_id, original.watermark_id) == replacement
        assert len(await monitors.results.history(pin_id)) == 1

    async def test_history_is_newest_first_by_the_loads_own_clock(
        self, monitors: MonitorsStores
    ) -> None:
        """``wm_alpha`` sorts BEFORE ``wm_zeta`` lexically and AFTER it in
        time. A store that ordered by id would hand a brief the wrong prior
        load and misreport every delta on it."""
        token = _token()
        pin_id = f"pin_{token}"
        older, newer = _result(token, pin_id, 0), _result(token, pin_id, 1)
        await monitors.results.put(newer)
        await monitors.results.put(older)

        history = await monitors.results.history(pin_id)

        assert [r.watermark_id for r in history] == [newer.watermark_id, older.watermark_id]
        assert history[0].watermark_loaded_at > history[1].watermark_loaded_at

    async def test_history_honors_its_limit(self, monitors: MonitorsStores) -> None:
        token = _token()
        pin_id = f"pin_{token}"
        await monitors.results.put(_result(token, pin_id, 0))
        await monitors.results.put(_result(token, pin_id, 1))

        assert len(await monitors.results.history(pin_id, limit=1)) == 1

    async def test_history_of_an_unknown_pin_is_empty(self, monitors: MonitorsStores) -> None:
        assert await monitors.results.history(f"pin_missing_{_token()}") == ()

    # ------------------------------------------------------------ 3. loads

    async def test_load_round_trip_and_tenant_scoping(self, monitors: MonitorsStores) -> None:
        token = _token()
        mine = _load(token, 0, tenant=f"t_mine_{token}")
        theirs = _load(token, 0, tenant=f"t_theirs_{token}")
        await monitors.loads.put(mine)
        await monitors.loads.put(theirs)

        assert await monitors.loads.get(mine.tenant, mine.watermark_id) == mine
        assert [load.tenant for load in await monitors.loads.list_for_tenant(mine.tenant)] == [
            mine.tenant
        ]
        assert await monitors.loads.get(mine.tenant, f"wm_missing_{token}") is None

    async def test_loads_list_newest_first_by_the_loads_own_clock(
        self, monitors: MonitorsStores
    ) -> None:
        token = _token()
        tenant = f"t_{token}"
        older = _load(token, 0, tenant=tenant)
        newer = _load(token, 1, tenant=tenant)
        await monitors.loads.put(older)
        await monitors.loads.put(newer)

        listed = await monitors.loads.list_for_tenant(tenant)

        assert [load.watermark_id for load in listed] == [newer.watermark_id, older.watermark_id]
        assert len(await monitors.loads.list_for_tenant(tenant, limit=1)) == 1

    async def test_re_evaluating_a_load_replaces_its_census(
        self, monitors: MonitorsStores
    ) -> None:
        token = _token()
        tenant = f"t_{token}"
        first = _load(token, 0, tenant=tenant)
        await monitors.loads.put(first)
        second = MonitorsLoad(
            tenant=tenant,
            watermark_id=first.watermark_id,
            watermark_loaded_at=first.watermark_loaded_at,
            evaluated_at=first.evaluated_at + timedelta(hours=3),
            payload={"leads": {}},
        )
        await monitors.loads.put(second)

        assert await monitors.loads.get(tenant, first.watermark_id) == second
        assert len(await monitors.loads.list_for_tenant(tenant)) == 1

    # ------------------------------------------------------------- 4. leads

    async def test_lead_round_trip_with_history_and_streak(
        self, monitors: MonitorsStores
    ) -> None:
        """The lifecycle's whole point is that it is auditable: who claimed
        what, when, and which loads have verified it so far."""
        token = _token()
        lead = _lead(token, tenant=f"t_{token}")
        await monitors.leads.put(lead)

        loaded = await monitors.leads.get(lead.tenant, lead.anomaly_id)

        assert loaded == lead
        assert loaded is not None
        assert loaded.confirming_watermarks == ("wm_alpha",)
        assert len(loaded.history) == 2
        assert loaded.history[-1]["to"] == "resolved_claimed"

    async def test_lead_get_missing_returns_none(self, monitors: MonitorsStores) -> None:
        assert await monitors.leads.get(f"t_{_token()}", "ANM-999") is None

    async def test_leads_are_tenant_scoped_and_ordered_by_id(
        self, monitors: MonitorsStores
    ) -> None:
        token = _token()
        tenant = f"t_{token}"
        second = _lead(token, tenant=tenant, anomaly_id="ANM-002")
        first = _lead(token, tenant=tenant, anomaly_id="ANM-001")
        other = _lead(token, tenant=f"t_other_{token}", anomaly_id="ANM-003")
        await monitors.leads.put(second)
        await monitors.leads.put(first)
        await monitors.leads.put(other)

        listed = await monitors.leads.list_for_tenant(tenant)

        assert [lead.anomaly_id for lead in listed] == ["ANM-001", "ANM-002"]

    async def test_a_lead_update_replaces_its_row(self, monitors: MonitorsStores) -> None:
        token = _token()
        tenant = f"t_{token}"
        lead = _lead(token, tenant=tenant)
        await monitors.leads.put(lead)
        confirmed = MonitorsLead(
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
        await monitors.leads.put(confirmed)

        assert await monitors.leads.get(tenant, lead.anomaly_id) == confirmed
        assert len(await monitors.leads.list_for_tenant(tenant)) == 1


__all__ = ["MonitorsStoreContract", "MonitorsStores"]
