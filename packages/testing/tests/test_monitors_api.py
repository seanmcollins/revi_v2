"""The Monitors surface over the wire, plus create-by-intent end to end.

Two halves:

* **the routes** — every Monitors endpoint through the real HTTP transport
  with real bearer auth, so tenant scoping is proved on the door a client
  actually uses rather than only on the in-process one;
* **create-by-intent** — "monitor X" as an ordinary turn. The lead-in is
  read, the remainder is classified and interpreted exactly as a bare
  question would be, the turn ANSWERS, and that answer is the baseline the
  monitor starts from. A declaration that cannot be compiled clarifies and
  registers nothing, because a monitor pinned to a spec nobody confirmed
  briefs the wrong number every morning, silently, forever.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from revi_api.app import create_app
from revi_api.auth import Principal, TokenSigner
from revi_api.service import ApiService
from revi_api.warning_codes import unconserved
from revi_api.wiring import build_components
from revi_investigation_contracts.api import (
    OpenSessionRequest,
    TurnAnswer,
    TurnClarification,
    TurnRequest,
    TypedInvestigationSpec,
)
from revi_investigation_contracts.refinements import WindowSpecModel
from revi_testing.mock_llm import MockLanguageModel

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

AUTH_SECRET = "monitors-signing-secret"
AUTH_ENV = {"REVI_AUTH_SECRET": AUTH_SECRET}
TENANT = "demo"

#: The subject a monitor declaration reduces to. Canned in the mock exactly
#: as it will arrive — the point of the test is that the lead-in was
#: stripped before interpretation ever saw the sentence.
MONITOR_SUBJECT = "denial rate by payer"
UNMAPPABLE_SUBJECT = "flurb index"


def _token(tenant: str = TENANT) -> str:
    return TokenSigner(AUTH_SECRET).issue(tenant=tenant, subject="monitors-suite")


def _llm() -> MockLanguageModel:
    llm = MockLanguageModel()
    llm.text_chunks = ("Denial rate by payer, measured over the last full month.",)
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.95, "clarification_question": None},
        matcher=lambda p: MONITOR_SUBJECT in p,
    )
    llm.respond(
        "interpret_question",
        {
            "intent_summary": "denial rate by payer",
            "metric_ids": ["denial_rate"],
            "dimension_ids": ["payer"],
            "concept_ids": [],
            "playbook_id": None,
            "window": {"quantity": "1", "unit": "month", "mode": "full_periods"},
            "basis": "service",
            "comparison": None,
            "scope": [],
            "clarification": None,
            "definitional_terms": [],
        },
        matcher=lambda p: MONITOR_SUBJECT in p,
    )
    # A declaration the platform cannot compile: it clarifies exactly as
    # the same words without the lead-in would.
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.9, "clarification_question": None},
        matcher=lambda p: UNMAPPABLE_SUBJECT in p,
    )
    llm.respond(
        "interpret_question",
        {
            "intent_summary": "unmappable",
            "metric_ids": [],
            "dimension_ids": [],
            "concept_ids": [],
            "playbook_id": None,
            "window": {"quantity": "1", "unit": "month", "mode": "full_periods"},
            "basis": None,
            "comparison": None,
            "scope": [],
            "clarification": {
                "question": "I don't have a governed measure for that. What would you like "
                "me to monitor?",
                "reason": "UNSUPPORTED_CONCEPT",
            },
            "definitional_terms": [],
        },
        matcher=lambda p: UNMAPPABLE_SUBJECT in p,
    )
    return llm


def _service() -> ApiService:
    return ApiService(
        build_components({"REVI_WAREHOUSE_PATH": str(WAREHOUSE)}, llm=_llm())
    )


@pytest.fixture
async def http() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(_service(), env=AUTH_ENV)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.headers["authorization"] = f"Bearer {_token()}"
        yield client


def _spec() -> dict[str, Any]:
    return TypedInvestigationSpec(
        metric_ids=["denied_dollars"],
        dimensions=["payer"],
        window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
    ).model_dump(mode="json")


class TestRoutes:
    async def test_pin_create_list_and_delete(self, http: httpx.AsyncClient) -> None:
        created = await http.post(
            "/v1/monitors/pins", json={"spec": _spec(), "label": "denied dollars by payer"}
        )
        assert created.status_code == 201, created.text
        pin = created.json()
        assert pin["window_mode"] == "relative"
        assert pin["scope"] == "tenant", "v1 Monitors are tenant-scoped, and the wire says so"
        assert pin["spec"]["metric_ids"] == ["denied_dollars"]

        listed = await http.get("/v1/monitors/pins")
        assert listed.status_code == 200
        assert [p["pin_id"] for p in listed.json()["pins"]] == [pin["pin_id"]]

        removed = await http.delete(f"/v1/monitors/pins/{pin['pin_id']}")
        assert removed.status_code == 204
        assert (await http.get("/v1/monitors/pins")).json()["pins"] == []

    async def test_a_body_naming_neither_source_is_refused(
        self, http: httpx.AsyncClient
    ) -> None:
        response = await http.post("/v1/monitors/pins", json={"label": "nothing"})
        assert response.status_code == 400
        assert response.json()["code"] == "POLICY_DENIED"

    async def test_an_unknown_pin_is_a_404_with_the_stable_envelope(
        self, http: httpx.AsyncClient
    ) -> None:
        response = await http.delete("/v1/monitors/pins/pin_nope")
        assert response.status_code == 404
        assert response.json()["code"] == "REFERENT_NOT_FOUND"

    async def test_another_tenants_pin_is_refused_not_disguised_as_missing(
        self, http: httpx.AsyncClient
    ) -> None:
        """Pin ids are not secrets, so a cross-tenant read is refused rather
        than dressed up as a 404 — the same rule the session reads follow."""
        created = await http.post("/v1/monitors/pins", json={"spec": _spec()})
        pin_id = created.json()["pin_id"]

        response = await http.delete(
            f"/v1/monitors/pins/{pin_id}",
            headers={"authorization": f"Bearer {_token('other-tenant')}"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "POLICY_DENIED"

    async def test_monitors_and_brief_serve_the_current_load(
        self, http: httpx.AsyncClient
    ) -> None:
        await http.post("/v1/monitors/pins", json={"spec": _spec(), "label": "denied dollars"})

        surface = await http.get("/v1/monitors")
        assert surface.status_code == 200
        body = surface.json()
        assert body["tenant"] == TENANT
        assert len(body["tiles"]) == 1
        tile = body["tiles"][0]
        assert tile["integrity"]["things_to_know"] == len(tile["warnings_v2"])
        assert tile["investigation_id"]

        brief = await http.get("/v1/monitors/brief")
        assert brief.status_code == 200
        payload = brief.json()
        assert payload["status"] in ("first_load", "nothing_material", "material_changes")
        assert payload["headline"]
        # The gate that produced this brief rides on it, so a reader can
        # check it rather than trust it.
        assert payload["materiality"]["content_hash"]
        assert payload["materiality"]["max_entries"] > 0

    async def test_a_since_naming_an_unevaluated_load_is_a_404(
        self, http: httpx.AsyncClient
    ) -> None:
        response = await http.get("/v1/monitors/brief", params={"since": "wm_nope"})
        assert response.status_code == 404
        assert response.json()["code"] == "REFERENT_NOT_FOUND"

    async def test_lead_patch_and_read(self, http: httpx.AsyncClient) -> None:
        patched = await http.patch(
            "/v1/monitors/leads/ANM-021",
            json={"status": "working", "note": "Sarah has it"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["status"] == "working"
        assert patched.json()["confirmations_required"] == 2

        read = await http.get("/v1/monitors/leads/ANM-021")
        assert read.status_code == 200
        assert read.json()["history"][-1]["to"] == "working"

    async def test_a_person_cannot_patch_a_confirmation(
        self, http: httpx.AsyncClient
    ) -> None:
        response = await http.patch(
            "/v1/monitors/leads/ANM-021", json={"status": "resolved_confirmed"}
        )
        # A closed literal on the request model: the status is not one a
        # person may name at all, so the body itself is malformed.
        assert response.status_code == 422

    async def test_every_monitors_route_requires_a_credential(self) -> None:
        app = create_app(_service(), env=AUTH_ENV)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
            for method, path in (
                ("GET", "/v1/monitors"),
                ("GET", "/v1/monitors/brief"),
                ("GET", "/v1/monitors/pins"),
                ("POST", "/v1/monitors/pins"),
                ("GET", "/v1/monitors/leads/ANM-021"),
            ):
                response = await anon.request(method, path, json={})
                assert response.status_code == 401, f"{method} {path}"


class TestCreateByIntent:
    async def test_a_monitor_declaration_answers_once_and_registers_the_monitor(self) -> None:
        """The declaration IS an ordinary turn. The answer doubles as the
        baseline, so the analyst sees what they are now monitoring and what it
        currently reads before they walk away from it."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(utterance=f"monitor {MONITOR_SUBJECT}"),
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.monitor is not None
        declaration = answer.monitor
        assert declaration.statement.startswith("Monitoring:")
        assert MONITOR_SUBJECT in declaration.label
        assert declaration.matched_phrase == "monitor"
        # The spec is published: a monitor whose definition the analyst
        # cannot check is a monitor they cannot trust.
        assert declaration.spec.metric_ids == ["denial_rate"]
        assert declaration.baseline_watermark_id
        assert "governed threshold" in declaration.threshold_statement
        # The platform says what it READ rather than asserting it read intent.
        assert any("monitor declaration" in w for w in answer.warnings)

        pins = await service.monitors.list_pins(caller)
        assert len(pins.pins) == 1
        pin = pins.pins[0]
        assert pin.pin_id == declaration.pin_id
        assert pin.created_from_kind == "intent"
        assert pin.created_from_investigation_id == answer.investigation_id
        # The baseline is captured at the declaration load: the analyst saw
        # this number and said "monitor that".
        assert pin.baseline_value is not None

    async def test_a_stated_sensitivity_is_carried_onto_the_monitor(self) -> None:
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(
                utterance=f"monitor {MONITOR_SUBJECT}, tell me if it rises more than 2 points"
            ),
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.monitor is not None
        monitor = answer.monitor.monitor
        assert monitor.mode == "delta_gte"
        assert monitor.unit == "points"
        assert monitor.value == pytest.approx(2.0)
        assert monitor.direction == "up"
        assert "2" in answer.monitor.threshold_statement

    async def test_a_declaration_that_cannot_compile_clarifies_and_monitors_nothing(
        self,
    ) -> None:
        """A monitor registered against a spec nobody confirmed would brief
        the wrong number every morning, forever. So it clarifies exactly as
        the same words without the lead-in would, and nothing is stored."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        outcome = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(utterance=f"monitor {UNMAPPABLE_SUBJECT}"),
        )

        assert isinstance(outcome, TurnClarification), outcome
        assert (await service.monitors.list_pins(caller)).pins == []

    async def test_an_ordinary_question_registers_no_monitor(self) -> None:
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denied_dollars"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                )
            ),
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.monitor is None
        assert (await service.monitors.list_pins(caller)).pins == []


class TestARefusedMonitorReachesTheReader:
    """Round-7 FN-3. Two reviewers scored the SAME behaviour opposite ways
    and both were right: the server-side refusal is exemplary, and the
    client never showed it.

    ``_register_declared_monitor`` appended the refusal to ``warnings`` alone,
    after the assembler had already built ``warnings_v2`` — and every client
    renders the structured list whenever it is non-empty. So the payload
    carried the sentence, the integrity line counted five warnings where
    there were six, and the screen showed an ordinary answer with no
    indication that nothing was being monitored. It was also mis-coded as a
    ``population_caveat``, which is a statement about who is in a number.
    """

    async def test_an_illegal_threshold_lands_where_the_confirmation_would_have(
        self,
    ) -> None:
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            # rcm-exec's own utterance: dollars against a rate contract.
            TurnRequest(utterance=f"monitor {MONITOR_SUBJECT}, alert me if it moves more than $5,000"),
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.monitor is None
        assert answer.monitor_refused is not None
        assert answer.monitor_refused.reason_code == "threshold_illegal"
        assert "'money_cents'" in answer.monitor_refused.reason
        # A refusal with no way forward is a wall.
        assert answer.monitor_refused.legal_alternatives
        assert any("points" in phrase for phrase in answer.monitor_refused.legal_alternatives)
        assert (await service.monitors.list_pins(caller)).pins == []

    async def test_the_refusal_is_classified_and_conserved(self) -> None:
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(utterance=f"monitor {MONITOR_SUBJECT}, alert me if it moves more than $5,000"),
        )

        assert isinstance(answer, TurnAnswer), answer
        codes = [w.code for w in answer.warnings_v2]
        assert "MONITOR_NOT_CREATED" in codes
        [refusal] = [w for w in answer.warnings_v2 if w.code == "MONITOR_NOT_CREATED"]
        assert refusal.severity == "caution"
        # Conservation: every prose sentence has a classified twin, so this
        # whole class of drop cannot recur silently.
        assert unconserved(answer.warnings, answer.warnings_v2) == ()

    async def test_an_unreadable_sensitivity_is_refused_rather_than_defaulted(
        self,
    ) -> None:
        """Round-7 FN-6. "more than half a point" registered the governed
        default with ``value: null`` and a confirmation that never mentioned
        the instruction — so "three points" would silently brief at 0.5."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(
                utterance=f"monitor {MONITOR_SUBJECT}, tell me if it moves more than a smidgen"
            ),
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.monitor is None
        assert answer.monitor_refused is not None
        assert answer.monitor_refused.reason_code == "threshold_unreadable"
        assert "smidgen" in answer.monitor_refused.reason
        assert (await service.monitors.list_pins(caller)).pins == []

    async def test_the_shapes_people_type_now_reach_the_monitor(self) -> None:
        """The other half of FN-6: refusing more is only honest if the
        grammar first reads what people actually type."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(
                utterance=f"monitor {MONITOR_SUBJECT}, tell me if it moves more than half a point"
            ),
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.monitor is not None
        assert answer.monitor.monitor.mode == "delta_gte"
        assert answer.monitor.monitor.value == pytest.approx(0.5)
        assert answer.monitor.monitor.unit == "points"
        assert "0.50 points" in answer.monitor.threshold_statement

    async def test_a_bare_percent_on_a_rate_names_the_other_reading(self) -> None:
        """"more than 2%" on a RATE compiles to a fraction of the current
        value — about half a point on a 25.9% base, four times tighter than
        the pack's own gate — and the governed fatigue advisory then told
        the analyst to tighten thresholds they never loosened."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(utterance=f"monitor {MONITOR_SUBJECT}, tell me if it moves more than 2%"),
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.monitor is not None
        assert answer.monitor.monitor.unit == "relative_pct"
        assert answer.monitor.threshold_alternative
        assert "2 points" in answer.monitor.threshold_alternative
        assert answer.monitor.threshold_alternative in answer.monitor.statement


class TestAMonitorSurvivesTheQuestionItTriggered:
    """Round-7 FN-5. ``_resolve_monitor_turn`` returned early on a
    clarification reply and nothing carried the parsed declaration across
    the turn boundary, so a declaration that clarified registered nothing
    and said nothing — in a pack that refuses any imprecise payer name BY
    DESIGN, which makes clarification the MODAL branch of the flagship
    acquisition path for the flagship surface."""

    async def test_the_clarification_says_the_monitor_is_not_created_yet(self) -> None:
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        outcome = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(utterance=f"monitor {UNMAPPABLE_SUBJECT}"),
        )

        assert isinstance(outcome, TurnClarification), outcome
        assert (await service.monitors.list_pins(caller)).pins == []
        # Silence is the one unacceptable option, and a clarification had no
        # channel to speak on until now.
        assert any("NOTHING is being monitored" in w for w in outcome.warnings)
        assert [w.code for w in outcome.warnings_v2] == ["MONITOR_PENDING_CLARIFICATION"]
        assert unconserved(outcome.warnings, outcome.warnings_v2) == ()

    async def test_the_declaration_is_registered_from_the_resolved_answer(self) -> None:
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())
        clarified = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(
                utterance=f"monitor {UNMAPPABLE_SUBJECT}, tell me if it moves more than 2 points"
            ),
        )
        assert isinstance(clarified, TurnClarification), clarified

        answer = await service.submit_turn(
            caller, session.session_id, TurnRequest(clarification_response=MONITOR_SUBJECT)
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.monitor is not None, "the declaration survived the question"
        # And the sensitivity the analyst stated survived with it.
        assert answer.monitor.monitor.mode == "delta_gte"
        assert answer.monitor.monitor.value == pytest.approx(2.0)
        assert (await service.monitors.list_pins(caller)).total == 1

    async def test_a_later_ordinary_turn_does_not_register_it_again(self) -> None:
        """The record is read from the newest investigation only, which is
        what makes it self-clearing: a declaration that registered a second
        monitor three turns later would be its own defect."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())
        await service.submit_turn(
            caller, session.session_id, TurnRequest(utterance=f"monitor {UNMAPPABLE_SUBJECT}")
        )
        await service.submit_turn(
            caller, session.session_id, TurnRequest(clarification_response=MONITOR_SUBJECT)
        )

        await service.submit_turn(
            caller, session.session_id, TurnRequest(utterance=MONITOR_SUBJECT)
        )

        assert (await service.monitors.list_pins(caller)).total == 1


class TestARefusalSurvivesTheReload:
    """Round-7 FN-3, restore half (reported by the web lane mid-fix).

    The engine stores the investigation with the warnings IT produced. The
    named-cut disclosure a monitor declaration earns and the refusal that says
    nothing is being monitored are both appended AFTER that, by the API — so a
    permalinked or re-opened turn restored four of six warnings and dropped
    exactly the two whose entire value is in being read later. The same
    class of drop, one layer down from the one this finding is about.
    """

    async def test_the_refusal_and_its_disclosure_are_still_there_on_restore(
        self,
    ) -> None:
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())
        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(utterance=f"monitor {MONITOR_SUBJECT}, alert me if it moves more than $5,000"),
        )
        assert isinstance(answer, TurnAnswer), answer
        assert answer.monitor_refused is not None

        restored = await service.get_investigation(caller, answer.investigation_id)

        assert set(answer.warnings) <= set(restored.warnings), (
            "what was published is what is stored"
        )
        codes = [w.code for w in restored.warnings_v2]
        assert "MONITOR_NOT_CREATED" in codes
        assert "NAMED_CUT_APPLIED" in codes, (
            "the disclosure of what the platform read as a monitor declaration is an API "
            "addition too, and it was going missing the same way"
        )
        assert unconserved(restored.warnings, restored.warnings_v2) == ()
        # And the refusal comes back as the SHAPE it renders as, not only as
        # a sentence a client would have to parse.
        assert restored.monitor_refused is not None
        assert restored.monitor_refused.reason_code == "threshold_illegal"
        assert restored.monitor_refused.legal_alternatives

    async def test_an_ordinary_turn_stores_no_extra_record(self) -> None:
        """The write is additive and conditional: a turn the API added
        nothing to is saved once, by the engine, exactly as before."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())
        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["denied_dollars"],
                    window=WindowSpecModel(quantity="1", unit="month", mode="full_periods"),
                )
            ),
        )
        assert isinstance(answer, TurnAnswer), answer

        restored = await service.get_investigation(caller, answer.investigation_id)

        assert restored.monitor_refused is None
        assert set(answer.warnings) <= set(restored.warnings)

    async def test_the_monitor_confirmation_disclosure_survives_too(self) -> None:
        """A successful declaration earns a NAMED_CUT_APPLIED sentence
        saying what the platform read. It is the analyst's evidence that the
        rewrite happened, and it was dropped on restore for the same
        reason."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="monitors-suite")
        session = await service.open_session(caller, OpenSessionRequest())
        answer = await service.submit_turn(
            caller, session.session_id, TurnRequest(utterance=f"monitor {MONITOR_SUBJECT}")
        )
        assert isinstance(answer, TurnAnswer), answer

        restored = await service.get_investigation(caller, answer.investigation_id)

        assert any("monitor declaration" in w for w in restored.warnings)
        assert "NAMED_CUT_APPLIED" in [w.code for w in restored.warnings_v2]
