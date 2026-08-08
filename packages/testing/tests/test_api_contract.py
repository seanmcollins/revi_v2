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
from revi_api.clients import HttpInvestigationClient, InProcessInvestigationClient
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation_contracts.api import (
    OpenSessionRequest,
    TurnAnswer,
    TurnClarification,
    TurnError,
    TurnRequest,
)
from revi_investigation_contracts.refinements import SetDimensionsModel
from revi_testing.mock_llm import MockLanguageModel

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
    llm.respond("classify_turn", None, matcher=lambda p: CONFUSED_Q in p)
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
        yield InProcessInvestigationClient(service)
        return
    transport = httpx.ASGITransport(app=create_app(service))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        yield HttpInvestigationClient(raw)


Client = InProcessInvestigationClient | HttpInvestigationClient


class TestApiContract:
    async def test_open_session_pins_pack_and_watermark(self, client: Client) -> None:
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        assert session.watermark_id == "wm_003"
        assert session.pack_id == "base-rcm" and session.epoch == 0

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
        assert response.usage.llm_calls >= 2

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
        assert portfolio.formula_version == "anomaly_priority@1"
        assert len(portfolio.items) >= 20
        scores = [item.priority_score for item in portfolio.items]
        assert scores == sorted(scores, reverse=True)
        top = portfolio.items[0]
        assert top.impact_cents != 0
        assert top.actionability_rationale
        assert top.age_days >= 0

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
        transport = httpx.ASGITransport(app=create_app(service))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            self.raw = raw
            yield HttpInvestigationClient(raw)

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
        assert body["code"] == "UNSUPPORTED_CONCEPT" and "correlation_id" in body

    async def test_health(self, http: HttpInvestigationClient) -> None:
        response = await self.raw.get("/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok" and body["watermark"] == "wm_003"
