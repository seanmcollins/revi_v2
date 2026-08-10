"""Monitors across real loads: wm_001 → wm_002 → wm_003, end to end.

Monitors is a load-over-load product, so the only test that proves it is one
that drives real loads. The generated warehouse holds three
(``wm_001``/``wm_002``/``wm_003``) with genuinely different detection
feeds — ANM-034 appears at the second, ANM-031/032/033 leave at the third,
and several cards' figures move — so every behaviour below is measured
against data rather than a fixture that agrees with the code.

What the suite holds, and why each one is the difference between a
proactive surface and a notification feed:

* a monitor is a SPEC re-run per load, not a snapshot, and its tile carries
  the same grade, caveats and bounds a live answer would;
* the FIRST load is a first load, and says so, rather than briefing
  thirty-three "new" leads that are only new to the platform;
* "nothing material changed" is a real, proud answer with the counts that
  back it;
* a claimed resolution is CONFIRMED by re-measurement across consecutive
  loads, or it is not confirmed at all;
* everything the gate holds back is counted on the payload.
"""

from __future__ import annotations

import shutil
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from revi_api.auth import Principal
from revi_api.monitors import typed_spec_from_analysis
from revi_api.scripted_llm import demo_language_model
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation.application.ports import MonitorsPin
from revi_investigation_contracts.api import (
    MonitorModel,
    OpenSessionRequest,
    TurnAnswer,
    TurnRequest,
    TypedInvestigationSpec,
)
from revi_investigation_contracts.monitors import (
    CreateMonitorsPinRequest,
    MonitorsLeadPatchRequest,
)
from revi_investigation_contracts.refinements import WindowSpecModel
from revi_kernel.errors import PolicyDeniedError, ReviError
from revi_kernel.watermark import DataWatermark

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        not WAREHOUSE.is_file(),
        reason="generated warehouse missing — run: "
        "uv run python -m revi_warehouse.generate --out data/revi_warehouse.duckdb",
    ),
]

TENANT = "demo"
CALLER = Principal(tenant=TENANT, subject="monitors-suite")


def _service(*, pack_dir: Path | None = None) -> ApiService:
    """A service on the real warehouse with no model in the loop.

    ``pack_dir`` swaps the governed content — the knob a deployment turns,
    exercised as a deployment would turn it rather than by patching a
    constant the pack is supposed to own.
    """
    env = {"REVI_WAREHOUSE_PATH": str(WAREHOUSE), "REVI_LLM_MOCK": "1"}
    if pack_dir is not None:
        env["REVI_PACK_DIR"] = str(pack_dir)
    return ApiService(build_components(env, llm=demo_language_model()))


async def _watermarks(service: ApiService) -> tuple[DataWatermark, ...]:
    return await service.components.repository.list_watermarks()


#: A monitor over a MOVING window, so a load-over-load delta is a real
#: movement in the business rather than late-arriving data. The distinction
#: is published on every pin (``window_mode``) and is the thing a reader
#: most needs in order to read a delta correctly.
def _moving_spec() -> TypedInvestigationSpec:
    return TypedInvestigationSpec(
        metric_ids=["denied_dollars"],
        dimensions=["payer"],
        window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
    )


class TestPinIsAMonitor:
    """A pin persists the typed SPEC, and the spec is what re-runs."""

    async def test_a_pin_from_an_investigation_resolves_its_stored_spec(self) -> None:
        """Pin-from-investigation reads the STORED spec server-side. No text
        is re-interpreted and no model is called: the spec already exists,
        and re-deriving it from the question would be a second, worse answer
        to a question already answered."""
        service = _service()
        session = await service.open_session(CALLER, OpenSessionRequest())
        answer = await service.submit_turn(
            CALLER, session.session_id, TurnRequest(spec=_moving_spec())
        )
        assert isinstance(answer, TurnAnswer), answer

        pin = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                investigation_id=answer.investigation_id,
                referent=answer.findings[0].referent if answer.findings else None,
                presentation="chart",
            ),
        )

        assert pin.created_from_kind == "artifact"
        assert pin.created_from_investigation_id == answer.investigation_id
        assert pin.spec.metric_ids == ["denied_dollars"]
        # A moving window: the tile tracks a period, so a delta is a real
        # movement. The payload SAYS so rather than leaving a reader to
        # infer it from the spec.
        assert pin.window_mode == "relative"
        assert "moving period" in pin.window_note
        # The label is the analyst's own artifact, never a generated one.
        assert pin.label and pin.label != "Monitored spec"

    async def test_an_absolute_window_is_pinned_as_one_and_says_what_that_means(
        self,
    ) -> None:
        """A monitor over fixed dates re-measures the SAME period every load,
        so its delta is late-arriving data rather than a movement. Both are
        legitimate monitors; only one of them is a movement."""
        service = _service()
        session = await service.open_session(CALLER, OpenSessionRequest())
        answer = await service.submit_turn(
            CALLER,
            session.session_id,
            TurnRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denial_rate"],
                    dimensions=["payer"],
                    window={"start": "2026-05-01", "end": "2026-08-02"},  # type: ignore[arg-type]
                    basis="service",
                )
            ),
        )
        assert isinstance(answer, TurnAnswer), answer

        pin = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(investigation_id=answer.investigation_id)
        )

        assert pin.window_mode == "absolute"
        assert "late-arriving data" in pin.window_note

    async def test_the_stored_spec_carries_scope_and_names_what_it_dropped(self) -> None:
        """A monitor that silently dropped a scope clause would measure a
        different population from the answer the analyst was looking at."""
        service = _service()
        session = await service.open_session(CALLER, OpenSessionRequest())
        answer = await service.submit_turn(
            CALLER,
            session.session_id,
            TurnRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denied_dollars"],
                    dimensions=["payer"],
                    filters=[
                        {
                            "op": "add_filter",
                            "dimension": "service_line",
                            "predicate_op": "eq",
                            "values": ["Laboratory"],
                        }
                    ],  # type: ignore[list-item]
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                )
            ),
        )
        assert isinstance(answer, TurnAnswer), answer
        investigation = await service.components.investigations.get(answer.investigation_id)
        assert investigation is not None

        spec, window_mode, notes = typed_spec_from_analysis(investigation.spec)

        assert window_mode == "relative"
        assert [(f.dimension, tuple(f.values)) for f in spec.filters] == [
            ("service_line", ("Laboratory",))
        ]
        assert notes == []

    async def test_un_pinning_is_soft_and_keeps_the_permalink(self) -> None:
        service = _service()
        pin = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars")
        )

        await service.monitors.archive_pin(CALLER, pin.pin_id)

        listed = await service.monitors.list_pins(CALLER)
        assert listed.pins == []
        stored = await service.components.monitors_pins.get(pin.pin_id)
        assert stored is not None and stored.archived_at is not None

    async def test_a_pin_is_refused_for_another_tenant(self) -> None:
        service = _service()
        pin = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="mine")
        )
        intruder = Principal(tenant="other", subject="intruder")

        with pytest.raises(ReviError) as refusal:
            await service.monitors.archive_pin(intruder, pin.pin_id)

        assert "another tenant" in refusal.value.message

    async def test_a_body_naming_both_sources_is_refused(self) -> None:
        service = _service()
        with pytest.raises(PolicyDeniedError):
            await service.monitors.create_pin(
                CALLER,
                CreateMonitorsPinRequest(investigation_id="inv_x", spec=_moving_spec()),
            )


class TestMonitorThresholds:
    """A monitor's own sensitivity, and the unit honesty that keeps it real."""

    async def test_a_points_threshold_on_a_money_contract_is_refused(self) -> None:
        """Refused at CREATION with the reason, never coerced into the
        nearest legal unit: a threshold in the wrong unit is a monitor that
        never fires — or always does — for a reason nobody can see."""
        service = _service()
        with pytest.raises(PolicyDeniedError) as refusal:
            await service.monitors.create_pin(
                CALLER,
                CreateMonitorsPinRequest(
                    spec=_moving_spec(),
                    monitor=MonitorModel(mode="delta_gte", value=0.5, unit="points"),
                ),
            )
        assert "only honest for a 'ratio' contract" in refusal.value.message
        assert "this monitor measures 'money_cents'" in refusal.value.message
        # The refusal names the legal alternatives rather than leaving the
        # caller to guess which unit would have been accepted.
        assert "relative_pct" in refusal.value.message

    async def test_a_points_threshold_on_a_rate_is_accepted(self) -> None:
        service = _service()
        pin = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denial_rate"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                ),
                label="denial rate",
                monitor=MonitorModel(
                    mode="delta_gte", value=2.0, unit="points", direction="up"
                ),
            ),
        )
        assert pin.monitor is not None
        assert pin.monitor.mode == "delta_gte" and pin.monitor.unit == "points"

    async def test_a_threshold_mode_with_no_value_is_refused(self) -> None:
        service = _service()
        with pytest.raises(PolicyDeniedError) as refusal:
            await service.monitors.create_pin(
                CALLER,
                CreateMonitorsPinRequest(
                    spec=_moving_spec(),
                    monitor=MonitorModel(mode="delta_gte"),
                ),
            )
        assert "needs a threshold value" in refusal.value.message


class TestSimulatedLoads:
    """The whole surface driven across the warehouse's three real loads."""

    async def test_the_first_load_says_it_is_the_first_load(self) -> None:
        """Thirty-three cards are not thirty-three things that changed. A
        brief with nothing to diff against says so and becomes the
        baseline."""
        service = _service()
        first, _, _ = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )

        brief = await service.monitors.brief_at(CALLER, first)

        assert brief.status == "first_load"
        assert brief.entries == []
        assert brief.watermark_id == first.id
        assert brief.prior_watermark_id is None
        assert "first load" in brief.headline
        assert brief.pins_evaluated == 1

    async def test_a_tile_carries_the_integrity_line_and_a_permalink(self) -> None:
        """A tile that renders the number and drops the marks undoes six
        adversarial monitors on the one surface a person looks at every
        morning without reading."""
        service = _service()
        first, _, _ = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )

        surface = await service.monitors.monitors_at(CALLER, first)

        assert len(surface.tiles) == 1
        [tile] = surface.tiles
        assert tile.status == "ok", tile.unavailable_reason
        assert tile.value is not None and tile.value_text.startswith("$")
        assert tile.unit == "money_cents"
        # The integrity atom: a grade, a count of things to know, the codes
        # behind that count, and the checks that were run.
        assert tile.integrity.grade in ("direct", "derived", "proxy")
        assert tile.integrity.things_to_know == len(tile.warnings_v2)
        assert tile.integrity.caveat_codes == list(
            dict.fromkeys(w.code for w in tile.warnings_v2)
        )
        assert tile.integrity.checks > 0
        # Every tile IS a real investigation: the tap-through is a
        # permalink, not a number computed off to the side.
        assert tile.investigation_id
        restored = await service.get_investigation(CALLER, tile.investigation_id)
        assert restored.watermark_id == first.id

    async def test_evaluation_is_idempotent_per_load(self) -> None:
        """The scheduled walk and the brief route call one primitive; a
        second call must reuse the stored tile rather than re-running every
        monitor against data that has not changed."""
        service = _service()
        first, _, _ = await _watermarks(service)
        pin = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars")
        )

        await service.monitors.evaluate_load(TENANT, first)
        stored = await service.components.monitors_results.get(pin.pin_id, first.id)
        assert stored is not None
        await service.monitors.evaluate_load(TENANT, first)
        again = await service.components.monitors_results.get(pin.pin_id, first.id)

        assert again is not None
        assert again.evaluated_at == stored.evaluated_at, "a stored tile was recomputed"

    async def test_a_monitored_spec_moves_across_loads_and_the_delta_is_unit_honest(
        self,
    ) -> None:
        service = _service()
        first, second, _ = await _watermarks(service)
        pin = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )

        await service.monitors.brief_at(CALLER, first)
        surface = await service.monitors.monitors_at(CALLER, second)

        [tile] = surface.tiles
        assert tile.pin_id == pin.pin_id
        assert tile.delta is not None
        assert tile.delta.prior_watermark_id == first.id
        assert tile.delta.unit == "money_cents"
        # Money keeps dollars. A rate would read "1.3 points" and never a
        # bare percentage — the ambiguity the platform refuses everywhere.
        assert tile.delta.delta_text.startswith("$")
        assert tile.delta.materiality_rule in (
            "money_relative_and_floor",
            "not_comparable",
        )
        assert tile.delta.materiality_note

    async def test_a_delta_says_whether_the_window_actually_moved(self) -> None:
        """A relative window usually moves and sometimes does not: two loads
        a day apart both resolve "last full month" to July. The number is
        right either way; what changes is what it MEANS, and only the
        resolved dates know which — so the tile publishes them and the
        sentence is written from them rather than from the window mode.
        """
        service = _service()
        first, second, _ = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars")
        )

        await service.monitors.monitors_at(CALLER, first)
        surface = await service.monitors.monitors_at(CALLER, second)

        [tile] = surface.tiles
        assert tile.window_start is not None and tile.window_end is not None
        assert tile.delta is not None
        # wm_001 (data through 07-31) and wm_002 (through 08-01) both resolve
        # "last full month" to July 2026 — the same dates.
        assert tile.delta.same_window is True
        assert tile.window_start.month == 7 and tile.window_end.month == 7

    async def test_a_brief_with_no_material_change_is_a_proud_answer(self) -> None:
        """Not an empty page: the counts that back the claim, and what was
        held back, are on the payload."""
        service = _service()
        first, second, _ = await _watermarks(service)
        # A monitor over a period neither load moves: fixed dates, and the
        # detection feed's only change between these two loads is one card
        # below the brief floor plus movements under the money gate.
        await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denied_dollars"],
                    window={"start": "2026-01-01", "end": "2026-03-31"},  # type: ignore[arg-type]
                ),
                label="Q1 denied dollars",
            ),
        )

        await service.monitors.brief_at(CALLER, first)
        brief = await service.monitors.brief_at(CALLER, second)

        assert brief.status in ("nothing_material", "material_changes")
        if brief.status == "nothing_material":
            assert brief.entries == []
            assert "Nothing material changed" in brief.headline
            assert brief.pins_evaluated == 1
            assert brief.immaterial.note
        # Whatever the verdict, the gate that produced it is published.
        assert brief.materiality.content_hash
        assert brief.materiality.unit_kinds["ratio"]["min_points"] == pytest.approx(0.005)
        assert brief.materiality.max_entries > 0

    async def test_new_and_self_resolved_leads_are_diffed_against_the_prior_load(
        self,
    ) -> None:
        """The detection feed is read per snapshot, so a lead that was fixed
        simply stops appearing. Without a stored census neither fact is
        decidable at all."""
        service = _service()
        first, second, third = await _watermarks(service)

        await service.monitors.brief_at(CALLER, first)
        await service.monitors.brief_at(CALLER, second)
        brief = await service.monitors.brief_at(CALLER, third)

        kinds = {entry.kind for entry in brief.entries}
        ids = {entry.anomaly_id for entry in brief.entries}
        # ANM-031/032/033 leave the feed at wm_003 and nobody claimed them.
        assert "self_resolved" in kinds, brief.headline
        assert ids & {"ANM-031", "ANM-032", "ANM-033"}
        for entry in brief.entries:
            # Every entry says which system asserted it and at which load.
            assert entry.provenance.source in ("detection_feed", "pinned_spec")
            assert entry.provenance.watermark_id == third.id
            assert entry.provenance.method

    async def test_the_brief_is_capped_and_says_what_it_withheld(self) -> None:
        service = _service()
        first, second, third = await _watermarks(service)

        await service.monitors.brief_at(CALLER, first)
        await service.monitors.brief_at(CALLER, second)
        brief = await service.monitors.brief_at(CALLER, third)

        assert len(brief.entries) <= brief.materiality.max_entries
        assert brief.entries_total >= len(brief.entries)
        withheld = brief.entries_total - len(brief.entries)
        assert brief.immaterial.entries_withheld_by_cap == withheld
        assert brief.immaterial.note

    async def test_a_since_naming_an_unevaluated_load_is_refused(self) -> None:
        """A brief that quietly diffed against a different load than the one
        it was asked for would misreport every entry on it."""
        service = _service()
        first, _, third = await _watermarks(service)
        await service.monitors.brief_at(CALLER, first)

        with pytest.raises(ReviError) as refusal:
            await service.monitors.brief_at(CALLER, third, since="wm_never_evaluated")

        assert "nothing to diff against" in refusal.value.message


class TestLeadLifecycle:
    """A person may claim a fix. Only the platform may confirm one."""

    async def test_claiming_then_confirming_across_two_loads(self) -> None:
        """ANM-031 is detected at wm_001 and wm_002 and gone at wm_003. A
        claim at wm_002 is verified at wm_003 — and with the governed rule
        asking for two consecutive loads, one verifying load is not enough."""
        service = _service()
        first, second, third = await _watermarks(service)
        await service.monitors.brief_at(CALLER, first)
        await service.monitors.brief_at(CALLER, second)

        claimed = await service.monitors.patch_lead(
            CALLER,
            "ANM-031",
            MonitorsLeadPatchRequest(status="resolved_claimed", note="reworked by the AR team"),
            watermark=second,
        )
        assert claimed.status == "resolved_claimed"
        assert claimed.claimed_at_watermark == second.id
        assert claimed.confirmations_required == 2
        assert claimed.history[-1]["to"] == "resolved_claimed"

        await service.monitors.brief_at(CALLER, third)
        after_one = await service.monitors.get_lead(CALLER, "ANM-031")

        # One verifying load is a coincidence: a card can drop out of a
        # single snapshot because a window moved.
        assert after_one.status == "resolved_claimed"
        assert after_one.confirming_watermarks == [third.id]
        assert "1 of the 2 consecutive loads" in after_one.verification_note

    async def test_a_second_verifying_load_confirms_with_a_measured_sentence(self) -> None:
        service = _service()
        first, second, third = await _watermarks(service)
        await service.monitors.brief_at(CALLER, first)
        await service.monitors.patch_lead(
            CALLER,
            "ANM-031",
            MonitorsLeadPatchRequest(status="resolved_claimed"),
            watermark=first,
        )
        # Two loads at which the claim can be verified. ANM-031 is still in
        # the feed at wm_002, so the first verification is a MEASUREMENT of
        # the lead's own drill rather than the card's absence.
        await service.monitors.brief_at(CALLER, second)
        brief = await service.monitors.brief_at(CALLER, third)

        lead = await service.monitors.get_lead(CALLER, "ANM-031")
        if lead.status == "resolved_confirmed":
            assert len(lead.confirming_watermarks) == 2
            assert lead.verification_note.startswith("Confirmed:")
            assert third.id in lead.verification_note
            # A confirmation is news, so it reaches the brief.
            confirmations = [
                e for e in brief.entries if e.kind == "resolution_confirmed"
            ]
            assert any(e.anomaly_id == "ANM-031" for e in confirmations)
        else:
            # The honest alternative: still detected and not measurably
            # cleared. Never confirmed on an assertion, and it says why.
            assert lead.status in ("resolved_claimed", "regressed")
            assert lead.verification_note

    async def test_the_confirmation_sentence_names_what_it_measured(
        self, tmp_path: Path
    ) -> None:
        """The reference warehouse cannot reach a confirmation under the
        SHIPPED rule: two consecutive verifying loads are required and every
        lead that leaves the feed does so at the last of three loads. So the
        governed knob — which is pack content precisely so a deployment can
        set it — is set to one load here, and the sentence the shipped rule
        will produce at two is asserted against real data.
        """
        pack = tmp_path / "base-rcm"
        shutil.copytree(REPO_ROOT / "packs" / "base-rcm", pack)
        monitors_yaml = pack / "monitors.yaml"
        monitors_yaml.write_text(
            monitors_yaml.read_text().replace(
                "consecutive_loads_required: 2", "consecutive_loads_required: 1"
            ),
            encoding="utf-8",
        )
        service = _service(pack_dir=pack)
        assert service.components.monitors_policy.resolution.consecutive_loads_required == 1
        _, second, third = await _watermarks(service)

        await service.monitors.brief_at(CALLER, second)
        await service.monitors.patch_lead(
            CALLER,
            "ANM-031",
            MonitorsLeadPatchRequest(status="resolved_claimed", note="rebilled"),
            watermark=second,
        )
        brief = await service.monitors.brief_at(CALLER, third)

        lead = await service.monitors.get_lead(CALLER, "ANM-031")
        assert lead.status == "resolved_confirmed"
        assert lead.verification_note.startswith("Confirmed: ANM-031")
        assert "no longer in the detection feed at wm_003" in lead.verification_note
        assert "for 1 load, wm_003" in lead.verification_note
        # A confirmation is news, and it reaches the brief with its own
        # provenance rather than only living on the lead record.
        [entry] = [e for e in brief.entries if e.kind == "resolution_confirmed"]
        assert entry.anomaly_id == "ANM-031"
        assert entry.statement == lead.verification_note
        assert entry.provenance.source == "pinned_spec"
        assert "re-derived by this platform at each load" in entry.provenance.method
        # And the lead that was CLAIMED is not also reported as having
        # resolved itself — that would be the same fact told twice, once
        # crediting nobody.
        assert "ANM-031" not in {
            e.anomaly_id for e in brief.entries if e.kind == "self_resolved"
        }

    async def test_a_person_cannot_assert_a_confirmation(self) -> None:
        """"Mark as resolved" everywhere else in this category is a
        checkbox, and a checkbox is an opinion."""
        service = _service()
        with pytest.raises(PolicyDeniedError) as refusal:
            await service.monitors.patch_lead(
                CALLER,
                "ANM-031",
                MonitorsLeadPatchRequest.model_construct(status="resolved_confirmed"),
            )
        assert "verdict this platform reaches from data" in refusal.value.message

    async def test_a_lead_not_in_the_feed_cannot_be_patched(self) -> None:
        service = _service()
        with pytest.raises(ReviError) as refusal:
            await service.monitors.patch_lead(
                CALLER, "ANM-999", MonitorsLeadPatchRequest(status="working")
            )
        assert "not in the detection feed" in refusal.value.message

    async def test_lead_status_rides_onto_the_portfolio_cards(self) -> None:
        """Additive fields, so the rail renders the lifecycle from the
        payload it already fetches and a card and a brief entry cannot
        disagree about who is working what."""
        service = _service()
        await service.monitors.patch_lead(
            CALLER,
            "ANM-021",
            MonitorsLeadPatchRequest(status="working", note="Sarah has it"),
        )

        portfolio = await service.get_portfolio(CALLER)

        card = next(c for c in portfolio.items if c.anomaly_id == "ANM-021")
        assert card.lead_status == "working"
        assert card.lead_status_note == "Sarah has it"
        assert card.lead_updated_at is not None
        # Every other card is untouched and reads as the default.
        others = [c for c in portfolio.items if c.anomaly_id != "ANM-021"]
        assert all(c.lead_status == "open" for c in others)


class TestTimeToImpact:
    """Published context, per category, derived honestly or refused."""

    async def test_filing_leads_carry_a_real_deadline_date(self) -> None:
        service = _service()
        portfolio = await service.get_portfolio(CALLER)

        filing = [c for c in portfolio.items if c.category == "timely_filing"]
        assert filing, "the reference warehouse plants timely-filing cards"
        for card in filing:
            assert card.time_to_impact is not None
            assert card.time_to_impact.kind == "deadline"
            assert card.time_to_impact.lane == "pre_cash"
            # A date, not an estimate — and never marked provisional.
            assert card.time_to_impact.deadline_date is not None
            assert card.time_to_impact.provisional is False
            assert "days_to_deadline" in card.time_to_impact.method

    async def test_unbilled_inventory_is_a_projection_and_says_so(self) -> None:
        service = _service()
        portfolio = await service.get_portfolio(CALLER)

        dnfb = [c for c in portfolio.items if c.category == "dnfb"]
        assert dnfb
        for card in dnfb:
            assert card.time_to_impact is not None
            assert card.time_to_impact.kind == "projected"
            assert card.time_to_impact.lane == "pre_cash"
            # An estimate is never published as a date.
            assert card.time_to_impact.deadline_date is None
            assert card.time_to_impact.provisional is True
            assert "PROJECTION, not a deadline" in card.time_to_impact.method

    async def test_denials_have_already_hit_cash_and_carry_a_recovery_window(
        self,
    ) -> None:
        service = _service()
        portfolio = await service.get_portfolio(CALLER)

        denials = [c for c in portfolio.items if c.category == "denial_spike"]
        assert denials
        for card in denials:
            assert card.time_to_impact is not None
            assert card.time_to_impact.kind == "already_hit"
            assert card.time_to_impact.lane == "already_hit"
            assert card.time_to_impact.recovery_days is not None
            assert card.time_to_impact.recovery_label == "appeal window closes"

    async def test_a_category_with_no_honest_basis_publishes_null_with_a_reason(
        self,
    ) -> None:
        """A guessed "14 days" is indistinguishable on screen from the
        filing dates beside it, which is exactly why there is no guess."""
        service = _service()
        portfolio = await service.get_portfolio(CALLER)

        contractual = [c for c in portfolio.items if c.category == "contractual"]
        assert contractual
        for card in contractual:
            assert card.time_to_impact is not None
            assert card.time_to_impact.days is None
            assert card.time_to_impact.deadline_date is None
            assert card.time_to_impact.reason

    async def test_the_ranking_is_untouched_by_time_to_impact(self) -> None:
        """``anomaly_priority@3`` still decides the order. A rank change
        needs its own versioned formula decision; smuggling urgency into an
        existing version would make two builds of one dataset disagree with
        no version string to explain it."""
        service = _service()
        portfolio = await service.get_portfolio(CALLER)

        assert portfolio.formula_version == "anomaly_priority@3"
        ranked = [
            (not c.drillable, -c.priority_score, -abs(c.ranked_impact_cents), c.anomaly_id)
            for c in portfolio.items
        ]
        assert ranked == sorted(ranked), "time-to-impact silently re-ranked the worklist"


class TestAMonitorMeasuresTheCellThatWasPinned:
    """Round-7 FN-1 and FN-2 — the two P0s the buyers gated on.

    Both are the same mistake seen from two ends. A pin taken from one
    finding of a ranked breakdown stored the WHOLE ranking, and the tile
    then headlined whatever ranked first at each load under the pinned
    cell's title. Live, on the buyer's own screen, against these very
    loads: a tile reading "Pinnacle Health Plan: 22.9% denial rate" over
    State Medicaid MCO's 29.5%, certified ``grade: direct``; and a brief
    entry reporting Pinnacle "up 3.6 points" on a load where Pinnacle had
    FALLEN, with the fabricated rise explained as adjudication run-out.

    The suite below is deliberately the exec's own repro: pin the
    SECOND-ranked payer, walk all three loads, and check that the label and
    the number never name different subjects.
    """

    @staticmethod
    def _rate_by_payer() -> TypedInvestigationSpec:
        return TypedInvestigationSpec(
            metric_ids=["denial_rate"],
            dimensions=["payer"],
            window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
            basis="service",
        )

    async def _ranked_answer(self, service: ApiService) -> TurnAnswer:
        session = await service.open_session(CALLER, OpenSessionRequest())
        answer = await service.submit_turn(
            CALLER, session.session_id, TurnRequest(spec=self._rate_by_payer())
        )
        assert isinstance(answer, TurnAnswer), answer
        assert len(answer.findings) >= 2, "the repro needs a ranking to pin the #2 of"
        return answer

    async def test_pinning_a_non_first_finding_narrows_the_spec_to_that_cell(
        self,
    ) -> None:
        service = _service()
        answer = await self._ranked_answer(service)
        second = answer.findings[1]

        pin = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                investigation_id=answer.investigation_id,
                referent=second.referent,
                presentation="finding",
            ),
        )

        # The cell is on the SPEC now, not merely in the provenance.
        assert [(f.dimension, list(f.values)) for f in pin.spec.filters] == [
            ("payer", [second.title.split(":")[0]])
        ]
        # And the label is composed from that spec rather than copied from a
        # finding title, which carries the value it had on the load it was
        # written and goes stale on the tile's most prominent line.
        assert pin.label.startswith(second.title.split(":")[0])
        assert "%" not in pin.label
        # The panel headed "What this monitor measures" now has something to
        # render (FN-18).
        assert "denial rate" in pin.spec_summary.lower()
        assert "filtered to" in pin.spec_summary
        assert any("narrowed at creation" in note for note in pin.notes)

    async def test_the_tile_names_the_pinned_payer_at_every_load(self) -> None:
        """The gate, stated as the exec stated it: the tile must read
        Pinnacle's own number, on every load, including the one where
        somebody else took the top of the ranking."""
        service = _service()
        answer = await self._ranked_answer(service)
        second = answer.findings[1]
        subject = second.title.split(":")[0]
        pin = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                investigation_id=answer.investigation_id, referent=second.referent
            ),
        )

        for watermark in await _watermarks(service):
            surface = await service.monitors.monitors_at(CALLER, watermark)
            [tile] = [t for t in surface.tiles if t.pin_id == pin.pin_id]
            assert tile.status == "ok", tile.unavailable_reason
            assert tile.headline_subject == {"payer": subject}, watermark.id
            assert subject in tile.headline_title, watermark.id
            assert subject in tile.label

    async def test_a_narrowed_monitor_keeps_reporting_movement_across_a_rank_flip(
        self,
    ) -> None:
        """The other half of the exec's gate: the fix must not make the
        monitor go quiet. A monitor narrowed to its own cell is unaffected by
        what happens to the ranking above it."""
        service = _service()
        answer = await self._ranked_answer(service)
        pin = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                investigation_id=answer.investigation_id, referent=answer.findings[1].referent
            ),
        )
        first, second, third = await _watermarks(service)

        for watermark in (first, second, third):
            await service.monitors.monitors_at(CALLER, watermark)
        surface = await service.monitors.monitors_at(CALLER, third)
        [tile] = [t for t in surface.tiles if t.pin_id == pin.pin_id]

        assert tile.delta is not None
        assert tile.delta.comparable, tile.delta.not_comparable_reason
        assert tile.delta.subject_label == tile.delta.prior_subject_label

    async def test_a_breakdown_monitor_refuses_a_delta_across_a_rank_flip(self) -> None:
        """An un-narrowed breakdown monitor is a legitimate thing to monitor,
        and it is the one whose headline subject can change. When it does,
        the delta is refused BY NAME — and no same-window run-out
        attribution can ride on a movement that does not exist."""
        service = _service()
        answer = await self._ranked_answer(service)
        pin = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                investigation_id=answer.investigation_id,
                presentation="chart",
                label="Denial rate by payer",
            ),
        )
        watermarks = await _watermarks(service)

        flips = []
        for watermark in watermarks:
            surface = await service.monitors.monitors_at(CALLER, watermark)
            [tile] = [t for t in surface.tiles if t.pin_id == pin.pin_id]
            delta = tile.delta
            if (
                delta is not None
                and delta.prior_subject_label
                and delta.subject_label
                and delta.prior_subject_label != delta.subject_label
            ):
                flips.append((watermark.id, tile, delta))

        assert flips, (
            "the reference warehouse's third load flips the top payer — that is the "
            "condition this whole finding is about"
        )
        for _, _, delta in flips:
            assert not delta.comparable
            assert delta.delta is None and delta.delta_text == ""
            assert not delta.material, "a phantom movement may not clear the gate"
            assert not delta.same_window, "and may not carry run-out attribution"
            assert delta.prior_subject_label in (delta.not_comparable_reason or "")
            assert delta.subject_label in (delta.not_comparable_reason or "")

    async def test_the_flip_is_briefed_as_a_flip(self) -> None:
        """"State Medicaid MCO overtook Pinnacle as your worst payer" is the
        headline the fabricated movement was standing in for — two reviewers
        said so independently. It is its own entry kind, it names both
        subjects, and it carries no delta."""
        service = _service()
        answer = await self._ranked_answer(service)
        await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                investigation_id=answer.investigation_id,
                presentation="chart",
                label="Denial rate by payer",
            ),
        )
        watermarks = await _watermarks(service)
        for watermark in watermarks[:-1]:
            await service.monitors.monitors_at(CALLER, watermark)

        brief = await service.monitors.brief_at(CALLER, watermarks[-1])

        flips = [entry for entry in brief.entries if entry.kind == "rank_flip"]
        assert flips, brief.headline
        [flip] = flips
        assert "overtook" in flip.statement
        assert flip.delta is None
        assert "run-out" not in flip.statement and "late-arriving" not in flip.statement


class TestOneReferenceFrame:
    """Round-7 FN-9. ``since=`` governed the lead census and not the monitor
    movements, so a week-long brief carried overnight deltas inside it — and
    "I was away for a week" is the highest-value read of a proactive
    surface. The route's own docstring states the invariant it broke."""

    async def test_every_entry_diffs_against_the_load_the_brief_names(self) -> None:
        service = _service()
        first, second, third = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )
        for watermark in (first, second, third):
            await service.monitors.monitors_at(CALLER, watermark)

        brief = await service.monitors.brief_at(CALLER, third, since=first.id)

        assert brief.prior_watermark_id == first.id
        assert brief.prior_newest_data_date == first.newest_data_date
        for entry in brief.entries:
            if entry.delta is not None:
                assert entry.delta.prior_watermark_id == first.id, entry.statement

    async def test_the_census_reconciles_to_its_parts(self) -> None:
        """Round-7 FN-12. Eighteen monitors were evaluated, one was briefed,
        two were counted as held back, and the other sixteen were neither —
        on a surface whose stated discipline is "withheld visibly, never
        silently"."""
        service = _service()
        first, _second, third = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )
        await service.monitors.monitors_at(CALLER, first)
        # A monitor created between loads: it has no evaluation at the load
        # the brief diffs from, which is exactly the population that went
        # uncounted.
        await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denial_rate"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                ),
                label="denial rate",
            ),
        )

        brief = await service.monitors.brief_at(CALLER, third, since=first.id)

        briefed = sum(
            1 for entry in brief.entries if entry.kind in ("pin_movement", "rank_flip")
        )
        withheld = brief.immaterial.entries_withheld_by_kind
        capped = withheld.get("pin_movement", 0) + withheld.get("rank_flip", 0)
        assert brief.pins_evaluated == (
            briefed
            + capped
            + brief.immaterial.pin_movements
            + brief.immaterial.not_yet_comparable
            + brief.immaterial.unavailable
        ), brief.immaterial

    async def test_a_first_reading_is_stated_rather_than_rendered_as_flat(self) -> None:
        """A tile with no prior sent ``null`` and the renderer drew nothing,
        so "never compared" and "did not move" looked identical — nine of
        twelve tiles on one live surface."""
        service = _service()
        first, _, _ = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )

        surface = await service.monitors.monitors_at(CALLER, first)

        [tile] = surface.tiles
        assert tile.delta is not None, "absence is read as absence only if something says so"
        assert not tile.delta.comparable
        assert "first reading" in (tile.delta.not_comparable_reason or "")


class TestTheTileAndTheBriefTellOneStory:
    """Round-10 R10-2. ``pin_b616b7b89bde`` — the JOC account the demo is
    built around — was briefed "down 3.0 points from 25.9%" while its own
    tile, one screen below on the same load, said "first reading — nothing
    to compare against". The brief back-walked ``wm_002`` live; the tile
    served a cached evaluation written before that history existed, and
    nothing re-derived it, because staleness was judged on the MONITOR and
    never on the PAIR OF LOADS.

    Fresh-eyes has now filed five defects of this one shape — a read path
    and a write path drifting apart — so the contract is asserted rather
    than the symptom: for one pin at one load, the two surfaces say the
    same thing or the suite fails.
    """

    @staticmethod
    async def _tiles(service: ApiService, watermark: DataWatermark) -> dict[str, object]:
        surface = await service.monitors.monitors_at(CALLER, watermark)
        return {tile.pin_id: tile for tile in surface.tiles}

    async def test_a_tile_written_before_its_history_is_re_derived(self) -> None:
        """The live sequence, reproduced through the public routes: the
        newest load is evaluated first (as a monitor created after a load is),
        the earlier load arrives afterwards (as the restoration re-walk
        backfills it), and the tile must not keep publishing the answer it
        gave before its own past existed."""
        service = _service()
        _first, second, third = await _watermarks(service)
        created = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )
        # Evaluated at the newest load with no history at all: "first
        # reading — baseline set at this load".
        before = (await self._tiles(service, third))[created.pin_id]
        assert not before.delta.comparable

        # …and now the history arrives underneath it.
        await service.monitors.monitors_at(CALLER, second)

        after = (await self._tiles(service, third))[created.pin_id]
        assert after.delta is not None
        assert after.delta.prior_watermark_id == second.id, (
            "the tile still names the prior it was written against, not the one it has"
        )
        assert after.delta.comparable
        assert "first reading" not in (after.delta.not_comparable_reason or "")

    async def test_one_pin_is_one_fact_on_both_surfaces(self) -> None:
        """The contract itself, over every monitor on the default brief: same
        pin, same load, same delta text, same comparability."""
        service = _service()
        first, second, third = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )
        # One monitor with a HOLE in its history — evaluated at the first and
        # the third load and never at the second — which is the population
        # the two surfaces used to disagree about even when nothing was
        # stale.
        gapped = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denial_rate"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                ),
                label="denial rate",
            ),
        )
        await service.monitors.monitors_at(CALLER, first)
        await service.monitors.monitors_at(CALLER, second)
        await service.monitors.monitors_at(CALLER, third)

        tiles = await self._tiles(service, third)
        brief = await service.monitors.brief_at(CALLER, third)

        assert gapped.pin_id in tiles
        briefed = {
            entry.pin_id: entry
            for entry in brief.entries
            if entry.pin_id is not None and entry.delta is not None
        }
        assert briefed, "the brief must have something to say for this to prove anything"
        for pin_id, entry in briefed.items():
            tile = tiles[pin_id]
            assert tile.delta is not None, pin_id
            assert entry.delta.prior_watermark_id == tile.delta.prior_watermark_id, pin_id
            assert entry.delta.comparable == tile.delta.comparable, pin_id
            assert entry.delta.delta_text == tile.delta.delta_text, pin_id
            assert entry.delta.direction == tile.delta.direction, pin_id
            assert entry.delta.prior_value == tile.delta.prior_value, pin_id

    async def test_a_monitor_the_brief_cannot_compare_has_a_tile_that_agrees(self) -> None:
        """The other half of counting each pin once: a monitor the brief puts
        in "nothing to compare against yet" may not be showing a movement on
        its tile."""
        service = _service()
        _first, _second, third = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )

        tiles = await self._tiles(service, third)
        brief = await service.monitors.brief_at(CALLER, third)

        assert brief.immaterial.not_yet_comparable >= 1
        briefed = {entry.pin_id for entry in brief.entries if entry.pin_id is not None}
        for pin_id, tile in tiles.items():
            if pin_id in briefed or tile.delta is None:
                continue
            assert not tile.delta.comparable or not tile.delta.material, pin_id


class TestTheBriefSpeaksHumanWords:
    """Round-7 FN-8. The first sentence on the surface read "4 thing(s)
    changed between wm_002 and wm_003: 2 new lead, 1 pin movement, 1 self
    resolved" — raw enum ids, no pluralisation, warehouse handles, and
    "pin", a word this product's own naming rule bans. Nothing in it is
    wrong and a VP does not read past it."""

    async def test_no_warehouse_handle_or_machine_plural_reaches_the_surface(
        self,
    ) -> None:
        service = _service()
        first, second, third = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )
        for watermark in (first, second):
            await service.monitors.monitors_at(CALLER, watermark)

        brief = await service.monitors.brief_at(CALLER, third)

        surfaces = [brief.headline, brief.immaterial.note, *(e.statement for e in brief.entries)]
        for text in surfaces:
            assert "wm_" not in text, text
            assert "(s)" not in text and "(es)" not in text, text
            assert "pin movement" not in text.lower(), text
            assert " tile " not in text.lower(), text
        # And the load is named the way a reader names it.
        assert brief.prior_newest_data_date == second.newest_data_date
        assert f"{second.newest_data_date:%b}" in brief.headline

    async def test_the_held_back_line_is_not_printed_inside_the_headline(self) -> None:
        service = _service()
        first, second, _ = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars by payer")
        )
        await service.monitors.monitors_at(CALLER, first)

        brief = await service.monitors.brief_at(CALLER, second)

        assert "Held back" not in brief.headline


class TestOneMonitorPerSpec:
    """Round-7 FN-18. Nothing anywhere checked for a duplicate spec, and the
    client asserted the opposite as the reason its affordance could fail
    open. Live: nine to eleven tiles rendering the identical figure, four to
    six on the byte-identical spec — every one re-evaluated every load, and
    able to brief one movement six times."""

    async def test_the_same_spec_returns_the_monitor_that_exists(self) -> None:
        service = _service()
        first = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars")
        )

        second = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="the same thing again")
        )

        assert second.pin_id == first.pin_id
        assert second.already_existed
        assert any("already monitoring" in note for note in second.notes)
        assert (await service.monitors.list_pins(CALLER)).total == 1

    async def test_two_different_cells_are_two_monitors(self) -> None:
        service = _service()
        session = await service.open_session(CALLER, OpenSessionRequest())
        answer = await service.submit_turn(
            CALLER,
            session.session_id,
            TurnRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denial_rate"],
                    dimensions=["payer"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                    basis="service",
                )
            ),
        )
        assert isinstance(answer, TurnAnswer), answer

        one = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                investigation_id=answer.investigation_id, referent=answer.findings[0].referent
            ),
        )
        other = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                investigation_id=answer.investigation_id, referent=answer.findings[1].referent
            ),
        )

        assert one.pin_id != other.pin_id
        assert one.spec.filters != other.spec.filters

    async def test_an_archived_monitor_does_not_block_a_new_one(self) -> None:
        service = _service()
        first = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars")
        )
        await service.monitors.archive_pin(CALLER, first.pin_id)

        second = await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars")
        )

        assert second.pin_id != first.pin_id and not second.already_existed


    async def test_a_different_sensitivity_over_the_same_spec_is_refused_by_name(
        self,
    ) -> None:
        """Returning the existing monitor would apply a threshold the caller
        did not ask for, and creating a second one would brief the same
        movement twice. Neither is silent."""
        service = _service()
        existing = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                spec=_moving_spec(),
                label="denied dollars",
                monitor=MonitorModel(mode="delta_gte", value=10.0, unit="relative_pct"),
            ),
        )

        with pytest.raises(PolicyDeniedError) as refusal:
            await service.monitors.create_pin(
                CALLER,
                CreateMonitorsPinRequest(
                    spec=_moving_spec(),
                    monitor=MonitorModel(mode="any_movement"),
                ),
            )

        assert existing.label in refusal.value.message
        assert refusal.value.details["pin_id"] == existing.pin_id
        assert (await service.monitors.list_pins(CALLER)).total == 1

    async def test_an_illegal_threshold_is_refused_even_when_the_spec_exists(
        self,
    ) -> None:
        service = _service()
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars")
        )

        with pytest.raises(PolicyDeniedError) as refusal:
            await service.monitors.create_pin(
                CALLER,
                CreateMonitorsPinRequest(
                    spec=_moving_spec(),
                    monitor=MonitorModel(mode="delta_gte", value=0.5, unit="points"),
                ),
            )

        assert "cannot be applied honestly" in refusal.value.message

class TestRepairingMonitorsPinnedBeforeTheCellRule:
    """A fix to the create path does not fix the rows already in the store,
    and leaving them is leaving the defect in production under a fix's name
    (round-7 FN-1, the re-migration half)."""

    async def test_a_repairable_monitor_is_narrowed_and_its_baseline_reset(self) -> None:
        service = _service()
        session = await service.open_session(CALLER, OpenSessionRequest())
        answer = await service.submit_turn(
            CALLER,
            session.session_id,
            TurnRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denial_rate"],
                    dimensions=["payer"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                    basis="service",
                )
            ),
        )
        assert isinstance(answer, TurnAnswer), answer
        second = answer.findings[1]
        # The shape the store held before this change: the whole ranking,
        # titled with one cell's finding, and a baseline captured from
        # whatever ranked first.
        broken = replace(
            await _stored_pin(
                service,
                answer.investigation_id,
                second.referent,
            ),
            spec=TypedInvestigationSpec(
                metric_ids=["denial_rate"],
                dimensions=["payer"],
                window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                basis="service",
            ),
            label=second.title,
            baseline_watermark_id="wm_001",
            baseline_value=Decimal("0.295082"),
            baseline_unit="ratio",
        )
        await service.components.monitors_pins.save(broken)

        report = await service.monitors.repair_pins(TENANT)

        assert report["repaired"] == [broken.id]
        repaired = await service.components.monitors_pins.get(broken.id)
        assert repaired is not None
        assert [(f.dimension, list(f.values)) for f in repaired.spec.filters] == [
            ("payer", [second.title.split(":")[0]])
        ]
        assert repaired.baseline_value is None, (
            "the old baseline was another cell's number; keeping it would measure this cell "
            "against that one"
        )
        assert "%" not in repaired.label

    async def test_an_unrepairable_monitor_is_stopped_and_says_why(self) -> None:
        service = _service()
        pin = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denial_rate"],
                    dimensions=["payer"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                    basis="service",
                ),
                label="Pinnacle Health Plan: 22.9% denial rate",
            ),
        )
        # A referent whose investigation is gone — the un-resolvable case.
        stored = await service.components.monitors_pins.get(pin.pin_id)
        assert stored is not None
        await service.components.monitors_pins.save(
            replace(stored, created_from_referent="F2", created_from_investigation_id="inv_gone")
        )

        report = await service.monitors.repair_pins(TENANT)

        assert report["archived"] == [pin.pin_id]
        archived = await service.components.monitors_pins.get(pin.pin_id)
        assert archived is not None and archived.archived_at is not None
        assert (await service.monitors.list_pins(CALLER)).total == 0

    async def test_repair_is_idempotent(self) -> None:
        service = _service()
        await service.monitors.create_pin(
            CALLER, CreateMonitorsPinRequest(spec=_moving_spec(), label="denied dollars")
        )

        assert await service.monitors.repair_pins(TENANT) == {"repaired": [], "archived": []}
        assert await service.monitors.repair_pins(TENANT) == {"repaired": [], "archived": []}


async def _stored_pin(
    service: ApiService, investigation_id: str, referent: str
) -> MonitorsPin:
    """A monitor created from a referent, read back as the store holds it."""
    created = await service.monitors.create_pin(
        CALLER,
        CreateMonitorsPinRequest(investigation_id=investigation_id, referent=referent),
    )
    stored = await service.components.monitors_pins.get(created.pin_id)
    assert stored is not None
    return stored
