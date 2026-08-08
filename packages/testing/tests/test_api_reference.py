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
    TurnClarification,
    TurnRequest,
)
from revi_investigation_contracts.refinements import SetWindowModel

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


class TestPortfolioDrillDown:
    """§18.1-10, daily-prioritization half.

    What holds today: the portfolio is produced by generic machinery (an
    external detection feed read as-of a watermark, ranked by the versioned
    ``anomaly_priority`` formula), every card declares its provenance rather
    than borrowing an evidence grade it did not earn, and every card carries
    a *complete, well-typed* drill handle — ordinary ``set_window`` +
    ``add_filter`` operators from the same closed 12-op set a chart click
    emits. Nothing about a card is portfolio-specific machinery.

    What does NOT hold yet, and is asserted here rather than glossed: those
    operators have nowhere to land. Refinements refine a parent
    investigation, and a portfolio card is not one — so posting a card's
    handle to a fresh session returns the designed CLARIFICATION_REQUIRED
    ("no prior answer in this session to refine yet"), not an answer. The
    missing piece is the one the build plan named and this milestone did not
    build: ``PortfolioResponse`` carrying a portfolio-anchored session id
    whose investigation the card's operators can refine. Recorded as the
    open gap on §18.1-10 in docs/acceptance-walkthrough.md.
    """

    async def test_cards_carry_complete_typed_drill_handles(self) -> None:
        transport = httpx.ASGITransport(app=create_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            portfolio = await HttpInvestigationClient(raw).get_portfolio()

        assert portfolio.status == "ok" and portfolio.items
        card = next(c for c in portfolio.items if c.drill_filters and c.drill_window)
        # provenance, not a grade — the card is an external detector's
        # record, and it says so (see AnomalyCard's docstring)
        assert card.provenance == "external_detection"
        assert card.priority_formula_version == portfolio.formula_version
        assert card.source_watermark_id == portfolio.watermark_id
        # the handle is real refinement operators over certified dimensions,
        # bounded by the anomaly's own window
        assert all(f.op == "add_filter" and f.values for f in card.drill_filters)
        assert card.drill_window.start <= card.drill_window.end
        assert {f.dimension for f in card.drill_filters} == {
            d.dimension for d in card.dimensions
        }

    async def test_drilling_a_card_from_a_fresh_session_clarifies(self) -> None:
        """The honest failure mode of the open gap: a clarification, never a
        wrong answer over an unanchored context."""
        transport = httpx.ASGITransport(app=create_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw)
            portfolio = await client.get_portfolio()
            card = next(c for c in portfolio.items if c.drill_filters and c.drill_window)
            session = await client.open_session(OpenSessionRequest(tenant="demo"))
            response = await client.submit_turn(
                session.session_id,
                TurnRequest(
                    refinements=[
                        SetWindowModel(op="set_window", window=card.drill_window),
                        *card.drill_filters,
                    ]
                ),
            )
        assert isinstance(response, TurnClarification), response
        assert response.reason == "refinement without a parent investigation"
        assert response.usage.llm_calls == 0  # zero probes, zero model calls

    async def test_a_card_refines_an_existing_investigation(self) -> None:
        """And the operators themselves are sound: applied to a session that
        HAS an investigation, the same handle narrows it exactly as a chart
        click would — proving the gap is the anchor, not the handle."""
        transport = httpx.ASGITransport(app=create_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw)
            portfolio = await client.get_portfolio()
            card = next(
                c
                for c in portfolio.items
                if c.drill_window
                and sorted(f.dimension for f in c.drill_filters) == ["payer", "service_line"]
            )
            session = await client.open_session(OpenSessionRequest(tenant="demo"))
            await client.submit_turn(
                session.session_id, TurnRequest(utterance=REFERENCE_QUESTIONS[0])
            )
            response = await client.submit_turn(
                session.session_id,
                TurnRequest(
                    refinements=[
                        SetWindowModel(op="set_window", window=card.drill_window),
                        *card.drill_filters,
                    ]
                ),
            )
        assert isinstance(response, TurnAnswer), response
        header = response.context_header
        assert header is not None
        # the drill lands at the portfolio's own watermark and window, and
        # the card's dimensions come back as visible chips (§7.2, §18.1-16)
        assert header.watermark_id == portfolio.watermark_id
        assert header.window_start == card.drill_window.start
        assert header.window_end == card.drill_window.end
        assert {chip.dimension for chip in header.filter_chips} == {"payer", "service_line"}
