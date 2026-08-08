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
    AnomalyCard,
    OpenSessionRequest,
    PortfolioResponse,
    TurnAnswer,
    TurnRequest,
)
from revi_investigation_contracts.refinements import AbsoluteWindowModel, RemoveFilterModel

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


def _drillable_card(portfolio: PortfolioResponse) -> AnomalyCard:
    """A card cut by payer + service line whose metric the catalog can
    answer — the shape the walkthrough's §18.1-10 example uses."""
    return next(
        card
        for card in portfolio.items
        if card.drill_spec.metric_ids == ["denied_dollars"]
        and sorted(card.drill_spec.dimensions) == ["payer", "service_line"]
    )


class TestPortfolioDrillDown:
    """§18.1-10, daily-prioritization half — the drill-down anchor.

    The portfolio is generic machinery: an external detection feed read
    as-of a watermark, ranked by the versioned ``anomaly_priority``
    formula, every card declaring its provenance rather than borrowing an
    evidence grade it did not earn. What M13 could not do was *land* on a
    card. Its drill handle was a set of sound refinement operators with
    nowhere to go: a refinement refines a parent investigation, and a
    portfolio card is not one, so a cold-start drill returned
    CLARIFICATION_REQUIRED — honest, but not an answer.

    The fix is not a portfolio-anchored session (a hidden parent minted per
    surface). It is the typed FIRST turn: a card's handle is a complete
    ``TypedInvestigationSpec``, and a turn carrying one is a
    NEW_INVESTIGATION by construction — zero model calls, no parent
    required, the ordinary planning/§6.6-validation/execution pipeline
    after that. Chart clicks from a fresh session get it for free, because
    nothing about the machinery is portfolio-specific.
    """

    async def test_cards_carry_a_complete_typed_drill_handle(self) -> None:
        transport = httpx.ASGITransport(app=create_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            portfolio = await HttpInvestigationClient(raw).get_portfolio()

        assert portfolio.status == "ok" and portfolio.items
        for card in portfolio.items:
            # provenance, not a grade — the card is an external detector's
            # record, and it says so (see AnomalyCard's docstring)
            assert card.provenance == "external_detection"
            assert card.priority_formula_version == portfolio.formula_version
            assert card.source_watermark_id == portfolio.watermark_id
            # every card is executable: its own governed metric, its
            # dimensions as the breakdown AND at their detected values as
            # the scope, bounded by its own observation window
            spec = card.drill_spec
            assert spec.metric_ids == [card.metric_id]
            assert spec.dimensions == [d.dimension for d in card.dimensions]
            assert [(f.dimension, f.predicate_op, f.values) for f in spec.filters] == [
                (d.dimension, "eq", [d.value]) for d in card.dimensions
            ]
            assert isinstance(spec.window, AbsoluteWindowModel)
            assert spec.window.start == card.window_start
            assert spec.window.end == card.window_end
            # a card asserts a level, not a movement; comparison is one
            # ordinary refinement away — and now has a parent to land on
            assert spec.comparison is None

    async def test_drilling_a_card_from_a_fresh_session_answers(self) -> None:
        """The gap, closed: a cold-start drill is a real answer at the
        card's own watermark and window, with findings — not a
        clarification, and not a model call."""
        transport = httpx.ASGITransport(app=create_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw)
            portfolio = await client.get_portfolio()
            card = _drillable_card(portfolio)
            session = await client.open_session(OpenSessionRequest(tenant="demo"))
            response = await client.submit_turn(
                session.session_id, TurnRequest(spec=card.drill_spec)
            )
            lineage = await client.get_session_lineage(session.session_id)

        assert isinstance(response, TurnAnswer), response
        assert response.turn_class == "new_investigation"
        assert response.usage.llm_calls == 0  # typed in, typed out: no model
        assert response.plan_hash is not None  # a real plan really executed

        header = response.context_header
        assert header is not None
        assert header.watermark_id == portfolio.watermark_id
        assert header.window_start == card.window_start
        assert header.window_end == card.window_end
        # the detected cell comes back as visible chips (§7.2, §18.1-16)
        assert {chip.dimension for chip in header.filter_chips} == {"payer", "service_line"}

        # ... and it answers: a certified finding over the detector's cell,
        # re-derived from the platform's own metric contract, carrying the
        # evidence grade the card itself could not claim
        assert response.findings, "a drilled card must answer, not just execute"
        finding = response.findings[0]
        assert finding.referent == "F1"
        assert finding.metric_ids == [card.metric_id]
        assert finding.grade == "direct"
        assert finding.impact_cents is not None and finding.impact_cents > 0
        assert all(d.value in finding.title for d in card.dimensions)

        # one investigation, no parent: a card starts a thread, it does not
        # continue one that was never there
        [investigation] = lineage.investigations
        assert investigation.parent_id is None
        assert investigation.turn_class == "new_investigation"
        assert lineage.edges == []

    async def test_a_card_drill_is_an_anchor_later_refinements_can_land_on(self) -> None:
        """The point of the anchor: after a cold-start drill, the ordinary
        typed refinement path works — including inside a session that was
        already investigating something else, where the card correctly
        starts a NEW thread rather than silently narrowing the old one."""
        transport = httpx.ASGITransport(app=create_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw)
            portfolio = await client.get_portfolio()
            card = _drillable_card(portfolio)
            session = await client.open_session(OpenSessionRequest(tenant="demo"))
            unrelated = await client.submit_turn(
                session.session_id, TurnRequest(utterance=REFERENCE_QUESTIONS[0])
            )
            drilled = await client.submit_turn(
                session.session_id, TurnRequest(spec=card.drill_spec)
            )
            widened = await client.submit_turn(
                session.session_id,
                TurnRequest(
                    refinements=[RemoveFilterModel(op="remove_filter", dimension="service_line")]
                ),
            )
            lineage = await client.get_session_lineage(session.session_id)

        assert isinstance(unrelated, TurnAnswer) and isinstance(drilled, TurnAnswer)
        # the card did NOT refine the cash-decline answer that preceded it
        assert drilled.turn_class == "new_investigation"
        drilled_header = drilled.context_header
        assert drilled_header is not None
        assert drilled_header.window_start == card.window_start

        # and the refinement that follows lands on the CARD's investigation
        assert isinstance(widened, TurnAnswer), widened
        assert widened.turn_class == "refinement"
        widened_header = widened.context_header
        assert widened_header is not None
        assert widened_header.window_start == card.window_start
        assert {chip.dimension for chip in widened_header.filter_chips} == {"payer"}
        [edge] = lineage.edges
        assert edge.parent_id == drilled.investigation_id
        assert edge.child_id == widened.investigation_id
