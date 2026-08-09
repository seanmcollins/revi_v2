"""API contract suite: one contract, two transports (in-process + HTTP/ASGI).

Every behavior is asserted through the ``InvestigationApi`` surface with a
canned LLM on a small generated warehouse: session open, NL turn, typed
gesture, idempotency, clarification outcome, error-envelope stability,
lineage, capabilities, portfolio — plus SSE event ordering on the HTTP
transport (the only one with a wire).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx
import pytest

from revi_api.app import create_app
from revi_api.auth import Principal, TokenSigner
from revi_api.clients import HttpInvestigationClient, InProcessInvestigationClient
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation_contracts.api import (
    OpenSessionRequest,
    TurnAnswer,
    TurnClarification,
    TurnError,
    TurnRequest,
    TypedInvestigationSpec,
)
from revi_investigation_contracts.refinements import SetDimensionsModel, WindowSpecModel
from revi_testing.mock_llm import MockLanguageModel

#: The HTTP transport runs with real bearer auth on, so the suite exercises
#: the credential path rather than a bypass — the tenant a turn executes
#: under has to come from a signed token, and these tests are where that is
#: proved for every route.
AUTH_SECRET = "test-signing-secret"
AUTH_ENV = {"REVI_AUTH_SECRET": AUTH_SECRET}
TENANT = "demo"


def token_for(tenant: str = TENANT) -> str:
    return TokenSigner(AUTH_SECRET).issue(tenant=tenant, subject="contract-suite")


T1 = "Why did cash decline last week?"
UNKNOWN_METRIC_Q = "how are the flurbs"
CONFUSED_Q = "mumble"

NARRATIVE = (
    "Posted cash fell versus the prior week; the largest driver is F1, ",
    "with F2 and F3 close behind. See the payer chart for the split.",
)


def _canned_llm() -> MockLanguageModel:
    llm = MockLanguageModel()
    llm.text_chunks = NARRATIVE
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.94, "clarification_question": None},
        matcher=lambda p: T1 in p,
    )
    llm.respond(
        "interpret_question",
        {
            "intent_summary": "cash decline",
            "metric_ids": [],
            "dimension_ids": ["payer"],
            "concept_ids": [],
            "playbook_id": "cash_decline",
            "window": {"quantity": "1", "unit": "week", "mode": "full_periods"},
            "basis": "post",
            "comparison": "prior_period",
            "scope": [],
            "clarification": None,
            "definitional_terms": [],
        },
        matcher=lambda p: T1 in p,
    )
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.94, "clarification_question": None},
        matcher=lambda p: UNKNOWN_METRIC_Q in p,
    )
    llm.respond(
        "interpret_question",
        {
            "intent_summary": "unknown",
            "metric_ids": ["flurb_rate"],  # not in the pack → UNSUPPORTED_CONCEPT
            "dimension_ids": [],
            "concept_ids": [],
            "playbook_id": None,
            "window": None,
            "basis": None,
            "comparison": None,
            "scope": [],
            "clarification": None,
            "definitional_terms": [],
        },
        matcher=lambda p: UNKNOWN_METRIC_Q in p,
    )
    # A session's FIRST utterance is classified by construction (no model
    # call), so an unreadable one has to fail at INTERPRETATION for the
    # clarification path to be exercised at all.
    llm.respond("classify_turn", None, matcher=lambda p: CONFUSED_Q in p)
    llm.respond("interpret_question", None, matcher=lambda p: CONFUSED_Q in p)
    return llm


@pytest.fixture(scope="module")
def small_warehouse_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from revi_warehouse.config import GeneratorConfig
    from revi_warehouse.generate import run_generation

    out = tmp_path_factory.mktemp("warehouse") / "revi_small.duckdb"
    return run_generation(GeneratorConfig.small(), out).db_path


@pytest.fixture(scope="module")
def service(small_warehouse_path: Path) -> ApiService:
    components = build_components(
        {"REVI_WAREHOUSE_PATH": str(small_warehouse_path)}, llm=_canned_llm()
    )
    return ApiService(components)


@pytest.fixture(params=["in_process", "http"])
async def client(
    request: pytest.FixtureRequest, service: ApiService
) -> AsyncIterator[InProcessInvestigationClient | HttpInvestigationClient]:
    if request.param == "in_process":
        yield InProcessInvestigationClient(service, Principal(tenant=TENANT, subject="tests"))
        return
    transport = httpx.ASGITransport(app=create_app(service, env=AUTH_ENV))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        yield HttpInvestigationClient(raw, token_for())


Client = InProcessInvestigationClient | HttpInvestigationClient


class TestApiContract:
    async def test_open_session_pins_pack_and_watermark(self, client: Client) -> None:
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        assert session.watermark_id == "wm_003"
        assert session.pack_id == "base-rcm" and session.epoch == 0

    async def test_list_sessions_names_each_session_by_its_first_question(
        self, client: Client
    ) -> None:
        """Both doors, one rule: the list is the caller's own sessions,
        titled by what was actually asked in them."""
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        answer = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))
        assert isinstance(answer, TurnAnswer)

        listing = await client.list_sessions()

        assert listing.tenant == TENANT
        assert listing.total >= 1
        row = next(r for r in listing.sessions if r.session_id == session.session_id)
        assert row.title == T1
        assert row.turn_count == 1
        assert row.last_activity >= row.created_at
        # Newest activity first — the session just answered leads the list.
        assert listing.sessions[0].session_id == session.session_id

    async def test_nl_turn_answers_with_full_payload(self, client: Client) -> None:
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        response = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))
        assert isinstance(response, TurnAnswer)
        assert response.outcome == "answer"
        assert [f.referent for f in response.findings] == ["F1", "F2", "F3"]
        header = response.context_header
        assert header is not None and header.watermark_id == "wm_003"
        assert response.chart_specs and all(spec.rows for spec in response.chart_specs)
        assert any(
            row.referent_id for spec in response.chart_specs for row in spec.rows
        )  # clicks compile to DrillInto
        assert response.narrative is not None and "F1" in response.narrative
        assert response.plan_hash is not None
        # Interpretation plus the narrative. Classification is decided by
        # construction on a session's first utterance and costs nothing.
        assert response.usage.llm_calls >= 1

    async def test_typed_first_turn_opens_an_investigation_with_no_model_call(
        self, client: Client
    ) -> None:
        """A ``TypedInvestigationSpec`` is a NEW_INVESTIGATION by
        construction: no parent, no classification, no interpretation — but
        the same planning, §6.6 validation, execution and findings a
        sentence would have earned. This is what lets a portfolio card or a
        chart click in a fresh session open a thread instead of being told
        there is nothing to refine."""
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        response = await client.submit_turn(
            session.session_id,
            TurnRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["cash_posted"],
                    dimensions=["payer"],
                    filters=[],
                    window=WindowSpecModel(quantity="1", unit="week", mode="full_periods"),
                    basis="post",
                )
            ),
        )
        assert isinstance(response, TurnAnswer), response
        assert response.turn_class == "new_investigation"
        assert response.usage.llm_calls == 0  # the whole point: zero model calls
        assert response.plan_hash is not None
        assert response.findings and response.chart_specs
        header = response.context_header
        assert header is not None and header.basis == "post"

        lineage = await client.get_session_lineage(session.session_id)
        [investigation] = lineage.investigations
        assert investigation.parent_id is None and lineage.edges == []

    async def test_typed_first_turn_validates_ids_like_an_interpreted_one(
        self, client: Client
    ) -> None:
        """It skips the guessing, never the governance: an id the pack does
        not define is the same §12 refusal a hallucinated one gets."""
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        response = await client.submit_turn(
            session.session_id,
            TurnRequest(
                spec=TypedInvestigationSpec(
                    metric_ids=["flurbs_posted"],
                    window=WindowSpecModel(quantity="1", unit="week", mode="full_periods"),
                )
            ),
        )
        assert isinstance(response, TurnError), response
        assert response.error.code == "UNSUPPORTED_CONCEPT"

    async def test_typed_refinement_turn(self, client: Client) -> None:
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        first = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))
        assert isinstance(first, TurnAnswer)
        gesture = TurnRequest(
            refinements=[SetDimensionsModel(op="set_dimensions", dimensions=["payer"])]
        )
        second = await client.submit_turn(session.session_id, gesture)
        assert isinstance(second, TurnAnswer)
        assert second.turn_class == "refinement"
        assert second.reconciliation is not None and "passed" in second.reconciliation
        lineage = await client.get_session_lineage(session.session_id)
        assert len(lineage.investigations) == 2
        [edge] = lineage.edges
        assert edge.operators == [{"op": "set_dimensions", "dimensions": ["payer"]}]

    async def test_idempotency_key_returns_the_stored_turn(self, client: Client) -> None:
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        request = TurnRequest(utterance=T1, idempotency_key="idem-1")
        first = await client.submit_turn(session.session_id, request)
        second = await client.submit_turn(session.session_id, request)
        assert isinstance(first, TurnAnswer) and isinstance(second, TurnAnswer)
        assert first.investigation_id == second.investigation_id
        lineage = await client.get_session_lineage(session.session_id)
        assert len(lineage.investigations) == 1  # nothing re-executed or re-persisted

    async def test_clarification_is_a_successful_outcome(self, client: Client) -> None:
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        response = await client.submit_turn(session.session_id, TurnRequest(utterance=CONFUSED_Q))
        assert isinstance(response, TurnClarification)
        assert response.outcome == "clarification_required"
        assert response.question

    async def test_error_envelope_is_stable_across_transports(self, client: Client) -> None:
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        response = await client.submit_turn(
            session.session_id,
            TurnRequest(utterance=UNKNOWN_METRIC_Q, correlation_id="corr-test"),
        )
        assert isinstance(response, TurnError)
        assert response.error.code == "UNSUPPORTED_CONCEPT"
        assert "flurb_rate" in response.error.message
        assert response.error.correlation_id == "corr-test"

    async def test_capabilities(self, client: Client) -> None:
        capabilities = await client.get_capabilities()
        assert capabilities.repository["cohort_semijoin"] is True
        assert capabilities.pack_id == "base-rcm"
        assert capabilities.newest_watermark_id == "wm_003"

    async def test_portfolio_serves_ranked_anomaly_population(self, client: Client) -> None:
        """The warehouse ships a baked anomaly population (33 records at
        wm_003); the portfolio serves them ranked with decomposed score
        components — never a black-box ordering."""
        portfolio = await client.get_portfolio()
        assert portfolio.status == "ok"
        assert portfolio.formula_version == "anomaly_priority@2"
        assert len(portfolio.items) >= 20
        # @2 publishes the arithmetic and the lane, not just the result.
        assert portfolio.compliance_floor_basis in ("relative_median", "governed_absolute")
        assert {lane.id for lane in portfolio.lanes} <= {"compliance", "value"}
        assert sum(lane.item_count for lane in portfolio.lanes) == len(portfolio.items)
        # Governed priority orders within each half of the list; drillable
        # cards come first, because a worklist that opens with work nobody
        # can start is not a worklist (round-1 review D5).
        drillable = [i.priority_score for i in portfolio.items if i.drillable]
        blocked = [i.priority_score for i in portfolio.items if not i.drillable]
        assert drillable == sorted(drillable, reverse=True)
        assert blocked == sorted(blocked, reverse=True)
        assert [i.drillable for i in portfolio.items] == sorted(
            (i.drillable for i in portfolio.items), reverse=True
        )
        for item in portfolio.items:
            assert item.drillable is (item.drill_unavailable_reason is None)
        top = portfolio.items[0]
        assert top.impact_cents != 0
        assert top.actionability_rationale
        assert top.age_days >= 0
        # The score is reproducible from its own published terms.
        terms = top.priority
        recomputed = (
            terms.impact_term + terms.recency_term + terms.actionability_term
        ) / terms.weight_sum
        # Every published term is rounded to six places, so the sum can
        # only be checked to the precision the payload actually carries.
        assert recomputed == pytest.approx(terms.score_before_floor, abs=5e-6)
        assert top.priority_score == pytest.approx(
            max(terms.score_before_floor, terms.floor_value)
            if terms.floor_applied
            else terms.score_before_floor,
            abs=1e-6,
        )
        # And every card states its relationship to this platform's own
        # re-derivation of the same cell — never silence (review F1).
        for item in portfolio.items:
            assert item.impact_agreement in (
                "agreed",
                "diverged",
                # A snapshot contract against a windowed detector figure:
                # both honest, not two measurements of one thing, so no
                # percentage delta is published (round-2 FN-2).
                "not_comparable",
                "unavailable",
            )
            assert item.impact_reconciliation_note
            if item.impact_agreement != "unavailable":
                assert item.reconciled_impact_cents is not None
            if item.impact_agreement == "not_comparable":
                assert item.impact_delta_fraction is None

    async def test_get_investigation_roundtrip(self, client: Client) -> None:
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        answer = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))
        assert isinstance(answer, TurnAnswer)
        investigation = await client.get_investigation(answer.investigation_id)
        assert investigation.plan_hash == answer.plan_hash
        assert investigation.status == "complete"


class TestHttpOnly:
    @pytest.fixture
    async def http(self, service: ApiService) -> AsyncIterator[HttpInvestigationClient]:
        transport = httpx.ASGITransport(app=create_app(service, env=AUTH_ENV))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            raw.headers["authorization"] = f"Bearer {token_for()}"
            self.raw = raw
            yield HttpInvestigationClient(raw, token_for())

    async def test_sse_event_ordering(self, http: HttpInvestigationClient) -> None:
        session = await http.open_session(OpenSessionRequest(tenant="demo"))
        kinds: list[str] = []
        final: dict[str, Any] | None = None
        async for kind, payload in http.stream_turn(
            session.session_id, TurnRequest(utterance=T1)
        ):
            kinds.append(kind)
            if kind == "turn_complete":
                final = payload
        assert kinds[-1] == "turn_complete" and kinds.count("turn_complete") == 1
        order = ["stage", "context_header", "finding", "chart_spec", "narrative_delta"]
        positions = {k: [i for i, kind in enumerate(kinds) if kind == k] for k in order}
        for earlier, later in pairwise(order):
            assert positions[earlier] and positions[later], (earlier, later, kinds)
            assert max(positions[earlier]) < min(positions[later]), kinds
        assert final is not None and final["outcome"] == "answer"
        assert [f["referent"] for f in final["findings"]] == ["F1", "F2", "F3"]

    async def test_http_404_serves_the_error_envelope(
        self, http: HttpInvestigationClient
    ) -> None:
        response = await self.raw.get("/v1/investigations/inv_missing")
        assert response.status_code == 404
        body = response.json()
        # a missing investigation id is a missing *handle*, not an
        # inexpressible concept — the spec declares ErrorEnvelope on 404
        assert body["code"] == "REFERENT_NOT_FOUND" and "correlation_id" in body

    async def test_malformed_body_is_422_and_not_the_envelope(
        self, http: HttpInvestigationClient
    ) -> None:
        """One status, one model. Request-shape failures stay FastAPI's
        HTTPValidationError at 422; §12 domain failures are ErrorEnvelope at
        400/404/409/503. A generated client never has to guess which."""
        session = await http.open_session(OpenSessionRequest(tenant="demo"))
        response = await self.raw.post(
            f"/v1/sessions/{session.session_id}/turns",
            json={"refinements": [{"op": "not_an_operator"}]},
        )
        assert response.status_code == 422
        assert "detail" in response.json()  # HTTPValidationError, not the envelope

    async def test_health(self, http: HttpInvestigationClient) -> None:
        response = await self.raw.get("/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok" and body["watermark"] == "wm_003"


class TestPublishedSpec:
    """The OpenAPI document is the frontend's pinned contract; these guard
    the parts a client cannot discover any other way."""

    def spec(self) -> dict[str, Any]:
        document: dict[str, Any] = create_app(None, env=AUTH_ENV).openapi()
        return document

    def test_error_envelope_is_declared_on_every_v1_route(self) -> None:
        spec = self.spec()
        assert "ErrorEnvelope" in spec["components"]["schemas"]
        ref = "#/components/schemas/ErrorEnvelope"
        for path, operations in spec["paths"].items():
            assert path.startswith("/v1/"), path
            for method, operation in operations.items():
                responses = operation["responses"]
                for status in ("400", "404", "409", "503"):
                    schema = responses[status]["content"]["application/json"]["schema"]
                    assert schema["$ref"] == ref, (path, method, status)

    def test_sse_frames_are_published_for_the_turn_route(self) -> None:
        spec = self.spec()
        frame = spec["components"]["schemas"]["TurnStreamEvent"]
        # exactly the kinds the API actually emits — no aspirational ones
        assert set(frame["properties"]["event"]["enum"]) == {
            "stage",
            "warning",
            "clarification",
            "context_header",
            "finding",
            "chart_spec",
            "narrative_delta",
            "error",
            "turn_complete",
        }
        content = spec["paths"]["/v1/sessions/{session_id}/turns"]["post"]["responses"]["200"][
            "content"
        ]
        assert content["text/event-stream"]["schema"] == {
            "$ref": "#/components/schemas/TurnStreamEvent"
        }
        # the blocking transport keeps its own (discriminated) shape
        assert "oneOf" in content["application/json"]["schema"]

    def test_anomaly_cards_publish_provenance_instead_of_a_grade(self) -> None:
        card = self.spec()["components"]["schemas"]["AnomalyCard"]
        assert "grade" not in card["properties"]
        for field in ("provenance", "priority_formula_version", "source_watermark_id"):
            assert field in card["required"], field
        assert card["properties"]["provenance"]["const"] == "external_detection"
