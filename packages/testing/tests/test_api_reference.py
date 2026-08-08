"""Reference-over-HTTP: the five-turn conversation through the HTTP client
(ASGI transport) with the scripted demo LLM — identical findings and plan
hashes as the in-process run on the real generated warehouse."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from revi_api.app import create_app
from revi_api.clients import HttpInvestigationClient, InProcessInvestigationClient
from revi_api.scripted_llm import REFERENCE_QUESTIONS, demo_language_model
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation_contracts.api import (
    OpenSessionRequest,
    TurnAnswer,
    TurnRequest,
)

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


def _service() -> ApiService:
    components = build_components(
        {"REVI_WAREHOUSE_PATH": str(WAREHOUSE)}, llm=demo_language_model()
    )
    return ApiService(components)


async def _run_conversation(
    client: HttpInvestigationClient | InProcessInvestigationClient,
) -> list[TurnAnswer]:
    session = await client.open_session(OpenSessionRequest(tenant="demo"))
    answers: list[TurnAnswer] = []
    for question in REFERENCE_QUESTIONS:
        response = await client.submit_turn(
            session.session_id, TurnRequest(utterance=question)
        )
        assert isinstance(response, TurnAnswer), (question, response)
        answers.append(response)
    return answers


class TestReferenceOverHttp:
    async def test_five_turns_match_the_in_process_run(self) -> None:
        transport = httpx.ASGITransport(app=create_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            http_answers = await _run_conversation(HttpInvestigationClient(raw))
        in_process_answers = await _run_conversation(
            InProcessInvestigationClient(_service())
        )

        assert len(http_answers) == len(in_process_answers) == 5
        for turn, (over_http, in_process) in enumerate(
            zip(http_answers, in_process_answers, strict=True), start=1
        ):
            assert over_http.plan_hash == in_process.plan_hash, f"turn {turn} plan hash"
            assert [f.referent for f in over_http.findings] == [
                f.referent for f in in_process.findings
            ], f"turn {turn} findings"
            assert [f.impact_cents for f in over_http.findings] == [
                f.impact_cents for f in in_process.findings
            ], f"turn {turn} impacts"

        # T1 anchors: the answer-key findings over the wire
        t1 = http_answers[0]
        assert [f.referent for f in t1.findings] == ["F1", "F2", "F3"]
        assert t1.findings[0].impact_cents == -9909308  # State Medicaid
        assert t1.findings[1].impact_cents == -4894041  # Atlas Commercial
        header = t1.context_header
        assert header is not None and header.watermark_id == "wm_003"
        assert t1.narrative is not None  # validated demo narrative survives grounding
        # T5 is the META turn: recorded provenance over the wire
        t5 = http_answers[4]
        assert t5.meta_answer is not None
        assert t5.meta_answer.referent == "F2"
        assert len(t5.meta_answer.probes) == 6
