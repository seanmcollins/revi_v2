"""Monitor surface integrity: what the SURFACE serves, and whose turn it is.

Each test is written against the seam its defect actually crossed: a repair pass
rewrote the PIN while the surface renders the TILE; the subject-identity guard failed
OPEN when neither side recorded a subject, so a rank flip published as a movement with
a causal explanation attached; ``days`` was legal in the engine and missing from the
wire's enum, so one stored monitor 500'd a whole tenant's pin list; and a turn envelope
carried a concurrent caller's session and investigation ids.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from revi_api.auth import Principal
from revi_api.monitors import _stale_result_reason, _subject_mismatch
from revi_api.scripted_llm import demo_language_model
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation.application.ports import (
    MONITOR_THRESHOLD_UNITS,
    Monitor,
    MonitorsPin,
    MonitorsPinResult,
)
from revi_investigation_contracts.api import (
    MonitorModel,
    MonitorUnit,
    OpenSessionRequest,
    TurnAnswer,
    TurnRequest,
    TypedInvestigationSpec,
)
from revi_investigation_contracts.monitors import (
    CreateMonitorsPinRequest,
    MonitorsTilePayload,
)
from revi_investigation_contracts.refinements import WindowSpecModel
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
CALLER = Principal(tenant=TENANT, subject="monitors-surface-suite")


def _service() -> ApiService:
    env = {"REVI_WAREHOUSE_PATH": str(WAREHOUSE), "REVI_LLM_MOCK": "1"}
    return ApiService(build_components(env, llm=demo_language_model()))


async def _watermarks(service: ApiService) -> tuple[DataWatermark, ...]:
    return await service.components.repository.list_watermarks()


def _ranked_spec() -> TypedInvestigationSpec:
    """Denial rate BY PAYER — the ranked breakdown whose leader changes
    between wm_002 and wm_003, which is the subject of the rank-flip tests."""
    return TypedInvestigationSpec(
        metric_ids=["denial_rate"],
        dimensions=["payer"],
        window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
        basis="service",
    )


class TestARepairedMonitorChangesWhatTheSurfaceServes:
    """The repair pass narrowed specs, recomposed labels and cleared
    baselines — and ``GET /v1/monitors`` went on serving the tiles those
    monitors had before the repair, because evaluation reuses any stored
    result for the current watermark. A repair that does not change what the
    surface serves is not a repair."""

    async def test_the_served_tile_is_re_derived_in_the_repair_s_own_pass(
        self,
    ) -> None:
        service = _service()
        _, _, newest = await _watermarks(service)
        session = await service.open_session(CALLER, OpenSessionRequest())
        answer = await service.submit_turn(
            CALLER, session.session_id, TurnRequest(spec=_ranked_spec())
        )
        assert isinstance(answer, TurnAnswer), answer
        # A monitor in the shape the store once held: the whole ranking,
        # titled with one cell's finding.
        second = answer.findings[1]
        created = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                investigation_id=answer.investigation_id, referent=second.referent
            ),
        )
        stored_pin = await service.components.monitors_pins.get(created.pin_id)
        assert stored_pin is not None
        await service.components.monitors_pins.save(
            replace(stored_pin, spec=_ranked_spec(), label=second.title)
        )
        # ...and its pre-repair TILE, already stored at the load the surface
        # is about: another cell's number under this monitor's old title, with
        # no recorded subject. This row is what the live defect was made of,
        # and it is the row `evaluate_load` used to republish unconditionally.
        stale_tile = MonitorsTilePayload(
            pin_id=created.pin_id,
            label=second.title,
            presentation="finding",
            status="ok",
            watermark_id=newest.id,
            evaluated_at=_now(),
            value_text="29.5%",
            headline_title="State Medicaid MCO: 29.5% denial rate",
        )
        await service.components.monitors_results.put(
            MonitorsPinResult(
                pin_id=created.pin_id,
                tenant=TENANT,
                watermark_id=newest.id,
                watermark_loaded_at=newest.loaded_at,
                evaluated_at=_now(),
                payload=stale_tile.model_dump(mode="json"),
            )
        )

        after = await service.monitors.monitors_at(CALLER, newest)

        [tile] = [t for t in after.tiles if t.pin_id == created.pin_id]
        repaired = await service.components.monitors_pins.get(created.pin_id)
        assert repaired is not None
        cell = second.title.split(":")[0]
        assert [(f.dimension, list(f.values)) for f in repaired.spec.filters] == [
            ("payer", [cell])
        ]
        # THE SERVED TILE, not the pin row — the assertion that was missing.
        assert tile.label == repaired.label
        assert tile.label != stale_tile.label
        assert tile.headline_subject_label == cell
        assert tile.headline_subject == {"payer": cell}
        assert tile.evaluated_at is not None and stale_tile.evaluated_at is not None
        assert tile.evaluated_at > stale_tile.evaluated_at

    async def test_a_tile_that_no_longer_matches_its_monitor_is_not_republished(
        self,
    ) -> None:
        """The belt to the repair's braces: reuse is conditional on the
        stored tile still being an evaluation OF THIS MONITOR. Anything that
        edits a pin between loads — a repair, a relabelling, a narrowing —
        is covered by one rule rather than by remembering to invalidate."""
        service = _service()
        _, _, newest = await _watermarks(service)
        created = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(spec=_ranked_spec(), label="denial rate by payer"),
        )
        await service.monitors.monitors_at(CALLER, newest)
        first = await service.components.monitors_results.get(created.pin_id, newest.id)
        assert first is not None
        pin = await service.components.monitors_pins.get(created.pin_id)
        assert pin is not None
        await service.components.monitors_pins.save(replace(pin, label="renamed by its owner"))

        surface = await service.monitors.monitors_at(CALLER, newest)

        [tile] = [t for t in surface.tiles if t.pin_id == created.pin_id]
        assert tile.label == "renamed by its owner"
        second = await service.components.monitors_results.get(created.pin_id, newest.id)
        assert second is not None and second.evaluated_at > first.evaluated_at

    async def test_an_unchanged_monitor_still_reuses_its_stored_tile(self) -> None:
        """Idempotence is the reason this loop is cheap, and it survives."""
        service = _service()
        _, _, newest = await _watermarks(service)
        created = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(spec=_ranked_spec(), label="denial rate by payer"),
        )
        await service.monitors.monitors_at(CALLER, newest)
        first = await service.components.monitors_results.get(created.pin_id, newest.id)
        assert first is not None

        await service.monitors.monitors_at(CALLER, newest)

        again = await service.components.monitors_results.get(created.pin_id, newest.id)
        assert again is not None and again.evaluated_at == first.evaluated_at

    def test_a_stored_tile_with_no_recorded_subject_is_stale(self) -> None:
        """Named as a unit so the rule reads without a warehouse: a
        breakdown tile that does not say which cell it headlined is exactly
        what every tile stored before subjects existed is, and it is what
        makes the comparability guard downstream unreachable."""
        pin = MonitorsPin(
            id="pin_x",
            tenant=TENANT,
            label="denial rate by payer",
            spec=_ranked_spec(),
            presentation="chart",
            window_mode="relative",
            created_at=_now(),
        )
        subjectless = MonitorsPinResult(
            pin_id=pin.id,
            tenant=TENANT,
            watermark_id="wm_003",
            watermark_loaded_at=_now(),
            evaluated_at=_now(),
            payload=MonitorsTilePayload(
                pin_id=pin.id, label=pin.label, presentation="chart", status="ok"
            ).model_dump(mode="json"),
        )

        assert _stale_result_reason(pin, subjectless) is not None
        with_subject = replace(
            subjectless,
            payload=MonitorsTilePayload(
                pin_id=pin.id,
                label=pin.label,
                presentation="chart",
                status="ok",
                headline_subject={"payer": "Atlas Commercial"},
                headline_subject_label="Atlas Commercial",
            ).model_dump(mode="json"),
        )
        assert _stale_result_reason(pin, with_subject) is None


class TestARankFlipIsNeverAMovement:
    """The one branch of the subject guard that failed open covered the
    entire installed base, and the live brief published "29.5%, up 3.6
    points from 25.9%... adjudication run-out" for two different payers."""

    async def test_two_blank_subjects_are_not_comparable(self) -> None:
        pin = MonitorsPin(
            id="pin_x",
            tenant=TENANT,
            label="denial rate by payer",
            spec=_ranked_spec(),
            presentation="chart",
            window_mode="relative",
            created_at=_now(),
        )

        reason = _subject_mismatch(pin, "", "")

        assert reason is not None
        assert "neither load recorded" in reason

    async def test_the_brief_publishes_the_flip_and_never_a_movement(self) -> None:
        """Driven across the warehouse's real loads. The leader of this
        breakdown changes, so the honest entry is a change of subject —
        with no delta, and no run-out clause attached to one."""
        service = _service()
        first, second, third = await _watermarks(service)
        await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(spec=_ranked_spec(), label="denial rate by payer"),
        )

        await service.monitors.brief_at(CALLER, first)
        await service.monitors.brief_at(CALLER, second)
        brief = await service.monitors.brief_at(CALLER, third)

        movements = [e for e in brief.entries if e.kind == "pin_movement"]
        for entry in movements:
            assert entry.delta is not None and entry.delta.comparable
            # A movement may only be published between two RECORDED and
            # EQUAL subjects. Anything else is the phantom.
            assert entry.delta.subject_label
            assert entry.delta.subject_label == entry.delta.prior_subject_label
            if not entry.delta.same_window:
                assert "run-out" not in entry.statement
        # The headline the fabricated movement was standing in for. Written,
        # tested and never once fired before this fix, because the guard that
        # produces it was unreachable on tiles with no recorded subject.
        [flip] = [e for e in brief.entries if e.kind == "rank_flip"]
        assert flip.delta is None
        assert "change of subject and not a movement" in flip.statement
        assert "overtook" in flip.statement
        assert "run-out" not in flip.statement

    async def test_a_delta_over_unrecorded_subjects_publishes_no_percentage(
        self,
    ) -> None:
        """The precise live shape: two stored tiles, both with blank
        subjects, on a monitor over a ranked breakdown. The numbers stay on
        the payload — both are real — and only the difference is withheld."""
        service = _service()
        first, _, third = await _watermarks(service)
        created = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(spec=_ranked_spec(), label="denial rate by payer"),
        )
        await service.monitors.monitors_at(CALLER, first)
        await service.monitors.monitors_at(CALLER, third)
        pin = await service.components.monitors_pins.get(created.pin_id)
        assert pin is not None
        for watermark in (first, third):
            stored = await service.components.monitors_results.get(pin.id, watermark.id)
            assert stored is not None
            tile = MonitorsTilePayload.model_validate(stored.payload)
            await service.components.monitors_results.put(
                replace(
                    stored,
                    payload=tile.model_copy(
                        update={"headline_subject": {}, "headline_subject_label": ""}
                    ).model_dump(mode="json"),
                )
            )

        delta = await service.monitors._delta_against(
            pin,
            MonitorsTilePayload.model_validate(
                (
                    await service.components.monitors_results.get(pin.id, third.id)
                ).payload  # type: ignore[union-attr]
            ),
            first.id,
        )

        assert delta is not None
        assert delta.comparable is False
        assert delta.delta is None and delta.delta_text == ""
        assert delta.same_window is False, (
            "a same-window run-out clause may never ride out beside a withheld delta"
        )
        assert delta.prior_value is not None and delta.value is not None


class TestDaysIsALegalThresholdEverywhere:
    """One token of enum skew: a 500 for the whole tenant, a monitor
    stored twice while reported not stored, and a disabled settings control
    on every tile."""

    def test_the_wire_enum_and_the_engine_list_agree(self) -> None:
        from typing import get_args

        assert set(get_args(MonitorUnit)) == set(MONITOR_THRESHOLD_UNITS)

    async def test_a_days_monitor_survives_a_round_trip_through_the_pin_list(
        self,
    ) -> None:
        service = _service()
        created = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["days_in_ar"],
                    dimensions=["payer"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                ),
                label="days in A/R by payer",
                monitor=MonitorModel(mode="delta_gte", value=2, unit="days"),
            ),
        )

        listed = await service.monitors.list_pins(CALLER)

        [row] = [p for p in listed.pins if p.pin_id == created.pin_id]
        assert row.monitor is not None and row.monitor.unit == "days"
        assert listed.unreadable == []
        assert listed.total == len(listed.pins)

    async def test_one_undescribable_monitor_cannot_fail_the_whole_tenant_s_list(
        self,
    ) -> None:
        """The failure that actually shipped, generalised: whatever the next
        skew is, it costs its own row and not every other monitor on the
        tenant."""
        service = _service()
        good = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(spec=_ranked_spec(), label="denial rate by payer"),
        )
        bad = await service.monitors.create_pin(
            CALLER,
            CreateMonitorsPinRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denied_dollars"],
                    dimensions=["payer"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                ),
                label="denied dollars by payer",
            ),
        )
        stored = await service.components.monitors_pins.get(bad.pin_id)
        assert stored is not None
        # A unit no version of this wire has ever known.
        await service.components.monitors_pins.save(
            replace(
                stored, monitor=Monitor(mode="delta_gte", value=Decimal("2"), unit="furlongs")
            )
        )

        listed = await service.monitors.list_pins(CALLER)

        assert {p.pin_id for p in listed.pins} == {good.pin_id, bad.pin_id}
        [degraded] = [p for p in listed.pins if p.pin_id == bad.pin_id]
        assert degraded.monitor is None
        assert degraded.notes and "could not be described" in degraded.notes[0]
        assert degraded.spec.metric_ids == ["denied_dollars"], (
            "what a monitor MEASURES is the part a reader needs most and the part "
            "that never failed"
        )
        assert listed.unreadable == []

    async def test_a_declaration_over_a_monitored_spec_does_not_store_a_second_pin(
        self,
    ) -> None:
        """The declaration path bypassed the spec-hash dedupe the on-screen
        pin path runs, so one tenant held two identical days monitors, both
        briefing."""
        service = _service()
        session = await service.open_session(CALLER, OpenSessionRequest())
        answer = await service.submit_turn(
            CALLER, session.session_id, TurnRequest(spec=_ranked_spec())
        )
        assert isinstance(answer, TurnAnswer), answer
        outcome = await _outcome_for(service, session.session_id, answer)

        first = await service.monitors.register_intent_pin(
            CALLER, outcome, stated_subject="denial rate", monitor=None, matched_phrase="monitor"
        )
        second = await service.monitors.register_intent_pin(
            CALLER, outcome, stated_subject="denial rate", monitor=None, matched_phrase="monitor"
        )

        assert second.pin_id == first.pin_id
        assert "already monitoring this" in second.statement
        assert (await service.monitors.list_pins(CALLER)).total == 1


class TestAMonitorIsTitledByWhatItResolvedTo:
    """A monitor clarified off a hallucinated payer kept the wrong name
    permanently, and "monitor this" produced a monitor labelled ``this``."""

    async def test_the_label_comes_from_the_spec_and_the_confirmation_says_so(
        self,
    ) -> None:
        service = _service()
        session = await service.open_session(CALLER, OpenSessionRequest())
        answer = await service.submit_turn(
            CALLER, session.session_id, TurnRequest(spec=_ranked_spec())
        )
        assert isinstance(answer, TurnAnswer), answer
        outcome = await _outcome_for(service, session.session_id, answer)

        payload = await service.monitors.register_intent_pin(
            CALLER, outcome, stated_subject="this", monitor=None, matched_phrase="monitor this"
        )

        assert payload.label != "this"
        assert "denial rate" in payload.label.casefold()
        assert "You said 'this'; that resolved to" in payload.statement
        stored = await service.components.monitors_pins.get(payload.pin_id)
        assert stored is not None and stored.label == payload.label


class TestATurnEnvelopeIsItsOwnCallersEnvelope:
    """Two turns posted to one session came back naming another
    caller's session and investigations — the ids every client adopts for
    permalinks, "Copy link" and pin provenance."""

    async def test_interleaved_turns_across_sessions_keep_their_own_ids(
        self,
    ) -> None:
        service = _service()
        sessions = [
            (await service.open_session(CALLER, OpenSessionRequest())).session_id
            for _ in range(4)
        ]

        async def turn(session_id: str) -> tuple[str, TurnAnswer]:
            response = await service.submit_turn(
                CALLER, session_id, TurnRequest(spec=_ranked_spec())
            )
            assert isinstance(response, TurnAnswer), response
            return session_id, response

        results = await asyncio.gather(
            *[turn(s) for s in sessions], *[turn(s) for s in sessions]
        )

        for session_id, answer in results:
            assert answer.session_id == session_id
            investigation = await service.components.investigations.get(
                answer.investigation_id
            )
            assert investigation is not None
            assert investigation.session_id == session_id, (
                "the envelope's investigation must live in the envelope's session — this is "
                "what makes a permalink point at the caller's own transcript"
            )
        assert len({a.investigation_id for _, a in results}) == len(results)


async def _outcome_for(service: ApiService, session_id: str, answer: TurnAnswer):  # type: ignore[no-untyped-def]
    """Re-run the answered spec to hold a TurnOutcome, which is what the
    intent-pin path takes. The engine is the same one the turn used."""
    from revi_investigation.application.submit_turn import SubmitTurnRequest

    return await service.components.submit.submit(
        SubmitTurnRequest(
            tenant=TENANT,
            question="(typed investigation)",
            session_id=session_id,
            spec=_ranked_spec(),
        )
    )


def _now():  # type: ignore[no-untyped-def]
    from datetime import UTC, datetime

    return datetime.now(UTC)
