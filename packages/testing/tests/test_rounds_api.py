"""The Rounds surface over the wire, plus create-by-intent end to end.

Two halves:

* **the routes** — every Rounds endpoint through the real HTTP transport
  with real bearer auth, so tenant scoping is proved on the door a client
  actually uses rather than only on the in-process one;
* **create-by-intent** — "watch X" as an ordinary turn. The lead-in is
  read, the remainder is classified and interpreted exactly as a bare
  question would be, the turn ANSWERS, and that answer is the baseline the
  watch starts from. A declaration that cannot be compiled clarifies and
  registers nothing, because a watch pinned to a spec nobody confirmed
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

AUTH_SECRET = "rounds-signing-secret"
AUTH_ENV = {"REVI_AUTH_SECRET": AUTH_SECRET}
TENANT = "demo"

#: The subject a watch declaration reduces to. Canned in the mock exactly
#: as it will arrive — the point of the test is that the lead-in was
#: stripped before interpretation ever saw the sentence.
WATCH_SUBJECT = "denial rate by payer"
UNMAPPABLE_SUBJECT = "flurb index"


def _token(tenant: str = TENANT) -> str:
    return TokenSigner(AUTH_SECRET).issue(tenant=tenant, subject="rounds-suite")


def _llm() -> MockLanguageModel:
    llm = MockLanguageModel()
    llm.text_chunks = ("Denial rate by payer, measured over the last full month.",)
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.95, "clarification_question": None},
        matcher=lambda p: WATCH_SUBJECT in p,
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
        matcher=lambda p: WATCH_SUBJECT in p,
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
                "me to watch?",
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
            "/v1/rounds/pins", json={"spec": _spec(), "label": "denied dollars by payer"}
        )
        assert created.status_code == 201, created.text
        pin = created.json()
        assert pin["window_mode"] == "relative"
        assert pin["scope"] == "tenant", "v1 Rounds are tenant-scoped, and the wire says so"
        assert pin["spec"]["metric_ids"] == ["denied_dollars"]

        listed = await http.get("/v1/rounds/pins")
        assert listed.status_code == 200
        assert [p["pin_id"] for p in listed.json()["pins"]] == [pin["pin_id"]]

        removed = await http.delete(f"/v1/rounds/pins/{pin['pin_id']}")
        assert removed.status_code == 204
        assert (await http.get("/v1/rounds/pins")).json()["pins"] == []

    async def test_a_body_naming_neither_source_is_refused(
        self, http: httpx.AsyncClient
    ) -> None:
        response = await http.post("/v1/rounds/pins", json={"label": "nothing"})
        assert response.status_code == 400
        assert response.json()["code"] == "POLICY_DENIED"

    async def test_an_unknown_pin_is_a_404_with_the_stable_envelope(
        self, http: httpx.AsyncClient
    ) -> None:
        response = await http.delete("/v1/rounds/pins/pin_nope")
        assert response.status_code == 404
        assert response.json()["code"] == "REFERENT_NOT_FOUND"

    async def test_another_tenants_pin_is_refused_not_disguised_as_missing(
        self, http: httpx.AsyncClient
    ) -> None:
        """Pin ids are not secrets, so a cross-tenant read is refused rather
        than dressed up as a 404 — the same rule the session reads follow."""
        created = await http.post("/v1/rounds/pins", json={"spec": _spec()})
        pin_id = created.json()["pin_id"]

        response = await http.delete(
            f"/v1/rounds/pins/{pin_id}",
            headers={"authorization": f"Bearer {_token('other-tenant')}"},
        )
        assert response.status_code == 403
        assert response.json()["code"] == "POLICY_DENIED"

    async def test_rounds_and_brief_serve_the_current_load(
        self, http: httpx.AsyncClient
    ) -> None:
        await http.post("/v1/rounds/pins", json={"spec": _spec(), "label": "denied dollars"})

        surface = await http.get("/v1/rounds")
        assert surface.status_code == 200
        body = surface.json()
        assert body["tenant"] == TENANT
        assert len(body["tiles"]) == 1
        tile = body["tiles"][0]
        assert tile["integrity"]["things_to_know"] == len(tile["warnings_v2"])
        assert tile["investigation_id"]

        brief = await http.get("/v1/rounds/brief")
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
        response = await http.get("/v1/rounds/brief", params={"since": "wm_nope"})
        assert response.status_code == 404
        assert response.json()["code"] == "REFERENT_NOT_FOUND"

    async def test_lead_patch_and_read(self, http: httpx.AsyncClient) -> None:
        patched = await http.patch(
            "/v1/rounds/leads/ANM-021",
            json={"status": "working", "note": "Sarah has it"},
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["status"] == "working"
        assert patched.json()["confirmations_required"] == 2

        read = await http.get("/v1/rounds/leads/ANM-021")
        assert read.status_code == 200
        assert read.json()["history"][-1]["to"] == "working"

    async def test_a_person_cannot_patch_a_confirmation(
        self, http: httpx.AsyncClient
    ) -> None:
        response = await http.patch(
            "/v1/rounds/leads/ANM-021", json={"status": "resolved_confirmed"}
        )
        # A closed literal on the request model: the status is not one a
        # person may name at all, so the body itself is malformed.
        assert response.status_code == 422

    async def test_every_rounds_route_requires_a_credential(self) -> None:
        app = create_app(_service(), env=AUTH_ENV)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as anon:
            for method, path in (
                ("GET", "/v1/rounds"),
                ("GET", "/v1/rounds/brief"),
                ("GET", "/v1/rounds/pins"),
                ("POST", "/v1/rounds/pins"),
                ("GET", "/v1/rounds/leads/ANM-021"),
            ):
                response = await anon.request(method, path, json={})
                assert response.status_code == 401, f"{method} {path}"


class TestCreateByIntent:
    async def test_a_watch_declaration_answers_once_and_registers_the_watch(self) -> None:
        """The declaration IS an ordinary turn. The answer doubles as the
        baseline, so the analyst sees what they are now watching and what it
        currently reads before they walk away from it."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="rounds-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(utterance=f"watch {WATCH_SUBJECT}"),
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.watch is not None
        declaration = answer.watch
        assert declaration.statement.startswith("Watching:")
        assert WATCH_SUBJECT in declaration.label
        assert declaration.matched_phrase == "watch"
        # The spec is published: a watch whose definition the analyst
        # cannot check is a watch they cannot trust.
        assert declaration.spec.metric_ids == ["denial_rate"]
        assert declaration.baseline_watermark_id
        assert "governed threshold" in declaration.threshold_statement
        # The platform says what it READ rather than asserting it read intent.
        assert any("watch declaration" in w for w in answer.warnings)

        pins = await service.rounds.list_pins(caller)
        assert len(pins.pins) == 1
        pin = pins.pins[0]
        assert pin.pin_id == declaration.pin_id
        assert pin.created_from_kind == "intent"
        assert pin.created_from_investigation_id == answer.investigation_id
        # The baseline is captured at the declaration load: the analyst saw
        # this number and said "watch that".
        assert pin.baseline_value is not None

    async def test_a_stated_sensitivity_is_carried_onto_the_watch(self) -> None:
        service = _service()
        caller = Principal(tenant=TENANT, subject="rounds-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        answer = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(
                utterance=f"watch {WATCH_SUBJECT}, tell me if it rises more than 2 points"
            ),
        )

        assert isinstance(answer, TurnAnswer), answer
        assert answer.watch is not None
        watch = answer.watch.watch
        assert watch.mode == "delta_gte"
        assert watch.unit == "points"
        assert watch.value == pytest.approx(2.0)
        assert watch.direction == "up"
        assert "2" in answer.watch.threshold_statement

    async def test_a_declaration_that_cannot_compile_clarifies_and_watches_nothing(
        self,
    ) -> None:
        """A watch registered against a spec nobody confirmed would brief
        the wrong number every morning, forever. So it clarifies exactly as
        the same words without the lead-in would, and nothing is stored."""
        service = _service()
        caller = Principal(tenant=TENANT, subject="rounds-suite")
        session = await service.open_session(caller, OpenSessionRequest())

        outcome = await service.submit_turn(
            caller,
            session.session_id,
            TurnRequest(utterance=f"watch {UNMAPPABLE_SUBJECT}"),
        )

        assert isinstance(outcome, TurnClarification), outcome
        assert (await service.rounds.list_pins(caller)).pins == []

    async def test_an_ordinary_question_registers_no_watch(self) -> None:
        service = _service()
        caller = Principal(tenant=TENANT, subject="rounds-suite")
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
        assert answer.watch is None
        assert (await service.rounds.list_pins(caller)).pins == []
