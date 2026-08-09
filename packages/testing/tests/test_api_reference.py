"""Reference-over-HTTP: the five-turn conversation through the HTTP client
(ASGI transport) with the scripted demo LLM — identical findings and plan
hashes as the in-process run on the real generated warehouse."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from revi_api.app import create_app
from revi_api.auth import Principal, TokenSigner
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
    TypedInvestigationSpec,
)
from revi_investigation_contracts.refinements import (
    AbsoluteWindowModel,
    RemoveFilterModel,
    WindowSpecModel,
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


AUTH_SECRET = "reference-signing-secret"
AUTH_ENV = {"REVI_AUTH_SECRET": AUTH_SECRET}
TENANT = "demo"


def _token(tenant: str = TENANT) -> str:
    return TokenSigner(AUTH_SECRET).issue(tenant=tenant, subject="reference-suite")


def _app(service: ApiService) -> Any:
    return create_app(service, env=AUTH_ENV)


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
        transport = httpx.ASGITransport(app=_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            http_answers = await _run_conversation(HttpInvestigationClient(raw, _token()))
        in_process_answers = await _run_conversation(
            InProcessInvestigationClient(_service(), Principal(tenant=TENANT, subject="tests"))
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
        # 4 flow probes + 4 comparison twins: `lag_distribution_compare`
        # joined the plan when §6.6 started negotiating derived measures
        # with the repository instead of guessing from the catalog (§6.3).
        assert len(t5.meta_answer.probes) == 8


class TestGovernedBenchmarksReachTheWire:
    """Round-1 review D9: 19 sourced, cohort-labelled, caution-annotated
    benchmark figures were authored and reached nobody.

    `assembly.py` passed `benchmarks=()` as a literal; `TurnOutcome` had no
    field to carry them; `FindingPayload` and `TurnAnswer` had none either;
    and the generated OpenAPI contained zero schema keys matching
    "benchmark". Fixing the one hardcoded line would not have been enough,
    so this asserts the whole path."""

    async def test_a_finding_carries_its_metrics_governed_ranges(self) -> None:
        transport = httpx.ASGITransport(app=_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw, _token())
            session = await client.open_session(OpenSessionRequest())
            response = await client.submit_turn(
                session.session_id,
                TurnRequest(
                    spec=TypedInvestigationSpec(
                        metric_ids=["denial_rate"],
                        dimensions=["payer"],
                        window=AbsoluteWindowModel(start="2026-05-01", end="2026-08-02"),
                        basis="service",
                    )
                ),
            )
        assert isinstance(response, TurnAnswer), response
        assert response.benchmarks, "denial_rate has a governed benchmark; it must ship"
        [benchmark] = [b for b in response.benchmarks if b.metric_id == "denial_rate"]
        # a range, never a point target, and never without its context
        assert benchmark.value_low and benchmark.value_high
        assert benchmark.cohort_label and benchmark.period and benchmark.authority
        assert benchmark.sources, "an unsourced benchmark is not governed content"
        # nothing in KB wave 1 is certified, and the wire says so
        assert benchmark.review_status == "machine_researched"
        # ...and the same content hangs off the finding that cites the metric
        assert response.findings
        assert [b.id for b in response.findings[0].benchmarks] == [benchmark.id]

    async def test_the_published_spec_models_benchmarks(self) -> None:
        spec = _app(_service()).openapi()
        assert "BenchmarkPayload" in spec["components"]["schemas"]
        finding = spec["components"]["schemas"]["FindingPayload"]["properties"]
        assert "benchmarks" in finding


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
        transport = httpx.ASGITransport(app=_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            portfolio = await HttpInvestigationClient(raw, _token()).get_portfolio()

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
            # Normally the card's own metric; where governed content
            # repoints the drill (a ratio contract reporting dollars) the
            # substitution is declared on the card rather than hidden.
            if card.drill_repointed_from is None:
                assert spec.metric_ids == [card.metric_id]
            else:
                assert card.drill_repointed_from == card.metric_id
                assert spec.metric_ids != [card.metric_id]
                assert card.drill_repoint_rationale
            # Normally the card's own detected cut. Where governed content
            # repoints a DIMENSION — the feed cuts procedures at the
            # line-grain `proc_group` and a claim-grain contract has no
            # legal procedure cut — the substitution is declared on the
            # card rather than hidden, exactly like the metric repoint.
            swaps = {r.from_dimension: r.to_dimension for r in card.drill_dimension_repoints}
            assert spec.dimensions == [
                swaps.get(d.dimension, d.dimension) for d in card.dimensions
            ]
            for repoint in card.drill_dimension_repoints:
                assert repoint.from_dimension in {d.dimension for d in card.dimensions}
                assert repoint.rationale
            # The scope follows the same substitution, carrying the VALUE
            # across unchanged — the repointed dimension shares the
            # source's value domain, and inventing a value would be a
            # different claim rather than a wider one.
            assert [(f.dimension, f.predicate_op, f.values) for f in spec.filters] == [
                (swaps.get(d.dimension, d.dimension), "eq", [d.value])
                for d in card.dimensions
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
        transport = httpx.ASGITransport(app=_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw, _token())
            portfolio = await client.get_portfolio()
            card = _drillable_card(portfolio)
            session = await client.open_session(OpenSessionRequest(tenant="demo"))
            response = await client.submit_turn(
                session.session_id, TurnRequest(spec=card.drill_spec)
            )
            lineage = await client.get_session_lineage(session.session_id)

        assert isinstance(response, TurnAnswer), response
        assert response.turn_class == "new_investigation"
        # Typed in, typed out: the INVESTIGATION took no model call. The one
        # counted call is the narrative — a real generation this turn paid
        # for, which the envelope used to drop entirely (it runs after the
        # engine writes its trace, and the summary was read from that trace).
        assert response.usage.llm_calls == 1
        assert response.usage.output_tokens > 0
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
        assert finding.metric_ids == card.drill_spec.metric_ids
        assert finding.grade == "direct"
        assert finding.impact_cents is not None and finding.impact_cents > 0
        assert all(d.value in finding.title for d in card.dimensions)

        # one investigation, no parent: a card starts a thread, it does not
        # continue one that was never there
        [investigation] = lineage.investigations
        assert investigation.parent_id is None
        assert investigation.turn_class == "new_investigation"
        assert lineage.edges == []

    async def test_every_card_the_worklist_calls_drillable_actually_drills(self) -> None:
        """The property, across the whole population — not one cherry-picked
        card.

        Round-1 review D5: 33 cards, 6 drillable, the first that opened was
        rank 17, and ~90% of the ranked dollars sat behind an error dialog.
        The repo's own test at the time opened a single known-good card,
        which is exactly the shape of test that lets that happen. This one
        posts **every** card's own unmodified handle and holds the wire
        flag to it in both directions."""
        transport = httpx.ASGITransport(app=_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw, _token())
            portfolio = await client.get_portfolio()
            results: list[tuple[AnomalyCard, TurnAnswer | object]] = []
            for card in portfolio.items:
                session = await client.open_session(OpenSessionRequest())
                results.append(
                    (
                        card,
                        await client.submit_turn(
                            session.session_id, TurnRequest(spec=card.drill_spec)
                        ),
                    )
                )

        for card, response in results:
            if card.drillable:
                assert isinstance(response, TurnAnswer), (
                    f"{card.anomaly_id} is published as drillable but refused: {response}"
                )
                assert card.drill_unavailable_reason is None
            else:
                assert not isinstance(response, TurnAnswer), (
                    f"{card.anomaly_id} is published as undrillable but answered"
                )
                assert card.drill_unavailable_reason, card.anomaly_id

        drillable = [c for c, _ in results if c.drillable]
        assert len(drillable) >= 15, "the repointed denial_rate cards must be openable"
        # the worklist opens with work somebody can start
        assert portfolio.items[0].drillable
        # ...and it says out loud how much of the ranked impact it cannot
        # investigate, rather than letting the user discover it card by
        # card. Asserted in BOTH directions: since the claim-grain
        # procedure cut landed (primary_proc_group, 2026-08-09) every card
        # opens, and a worklist that still warned about un-investigable
        # cards while having none would be as wrong as one that stayed
        # silent while having some.
        blocked = [c for c, _ in results if not c.drillable]
        warned = any("not investigable at this catalog" in w for w in portfolio.warnings)
        assert warned is bool(blocked), (
            f"{len(blocked)} undrillable card(s) but warning present={warned}"
        )

    async def test_a_card_drill_is_an_anchor_later_refinements_can_land_on(self) -> None:
        """The point of the anchor: after a cold-start drill, the ordinary
        typed refinement path works — including inside a session that was
        already investigating something else, where the card correctly
        starts a NEW thread rather than silently narrowing the old one."""
        transport = httpx.ASGITransport(app=_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw, _token())
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


class TestEvidenceReachesTheWire:
    """The gap this closes: ``answer.evidence`` was never populated, so the
    web's evidence drawer, reconciliation banner and "no new queries" chip
    only ever rendered against mock fixtures. Everything they need was
    already recorded — per-probe purpose, rows, truncation, suppression,
    cache hits, grades, the §7.8 verdict — and reachable only through the
    debug-trace door.

    So the assertions are about *agreement*, not presence: the analyst's
    bundle and the operator's trace are two projections of one stored
    record, and if they can disagree the drawer is decoration. The same
    bundle must also come back from ``GET /v1/investigations/{id}``, or a
    turn restored when a session is re-opened shows an empty drawer and
    implies the answer read nothing.
    """

    async def test_the_bundle_agrees_with_the_trace_for_the_same_turn(self) -> None:
        transport = httpx.ASGITransport(app=_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw, _token())
            answers = await _run_conversation(client)
            traces = [await client.get_trace(a.investigation_id) for a in answers]
            restored = [await client.get_investigation(a.investigation_id) for a in answers]

        for turn, (answer, trace, stored) in enumerate(
            zip(answers, traces, restored, strict=True), start=1
        ):
            evidence = answer.evidence
            assert len(evidence.probes) == len(trace.probes), f"turn {turn} probe count"
            for shown, traced in zip(evidence.probes, trace.probes, strict=True):
                assert shown.id == traced.id, f"turn {turn}"
                assert shown.hash == traced.hash, f"turn {turn} {shown.id}"
                assert shown.purpose == traced.purpose, f"turn {turn} {shown.id}"
                assert shown.rows == traced.rows, f"turn {turn} {shown.id} rows"
                assert shown.cache_hit == traced.cache_hit, f"turn {turn} {shown.id}"
                assert shown.truncated == traced.truncated, f"turn {turn} {shown.id}"
                assert shown.suppressed_cells == traced.suppressed_cells, f"turn {turn}"
                assert shown.grade == traced.grade, f"turn {turn} {shown.id} grade"
            # the verdict is one recorded string, split for display, not re-judged
            if evidence.reconciliation is not None:
                assert evidence.reconciliation.summary == trace.reconciliation
                assert evidence.reconciliation.summary == answer.reconciliation
            else:
                assert answer.reconciliation is None, f"turn {turn} verdict dropped"
            # and the restored turn carries the identical bundle
            assert stored.evidence == evidence, f"turn {turn} rehydrated evidence"
            # ...and its charts, rebuilt from the frames the turn persisted.
            # Identical objects, not merely similar: they are built by the
            # same function over the same frames. The narrative is the one
            # thing that cannot come back — nothing stores the prose.
            assert stored.chart_specs == answer.chart_specs, f"turn {turn} restored charts"

    async def test_the_reference_turns_publish_real_working(self) -> None:
        """Anchors on the answer key's own conversation: T1 plans the cash
        playbook against the warehouse, T2 decomposes it by payer and
        reconciles to T1's totals while reusing T1's frames, and T5 (META)
        answers out of the session trace without touching the warehouse."""
        transport = httpx.ASGITransport(app=_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            answers = await _run_conversation(HttpInvestigationClient(raw, _token()))

        t1 = answers[0].evidence
        assert t1.probes, "the cash-decline playbook runs probes; the drawer must show them"
        assert t1.warehouse_queries > 0 and t1.zero_probe_turn is False
        assert all(p.purpose for p in t1.probes), "a probe with no stated purpose is a black box"
        assert all(p.kind == "aggregation" for p in t1.probes)
        assert {m.id for p in t1.probes for m in p.metrics} >= {"cash_posted"}
        assert all(
            m.contract_version is not None for p in t1.probes for m in p.metrics
        ), "an aggregate without its contract version is not auditable"
        # a first turn HAS a verdict: "nothing to reconcile to, and here is why"
        assert t1.reconciliation is not None
        assert t1.reconciliation.status == "not_applicable"
        assert "first turn" in (t1.reconciliation.detail or "")

        t2 = answers[1].evidence
        assert t2.reconciliation is not None and t2.reconciliation.status == "passed"
        assert t2.cache_hits > 0, "the split reuses T1's frames; the drawer says so"

        t5 = answers[4].evidence
        assert t5.probes == [] and t5.zero_probe_turn is True
        assert t5.warehouse_queries == 0
        # a META turn reached no reconciliation check at all — recorded as
        # absence, never as a reassuring "not applicable"
        assert t5.reconciliation is None

    async def test_the_published_spec_models_the_bundle(self) -> None:
        spec = _app(_service()).openapi()
        schemas = spec["components"]["schemas"]
        assert "EvidencePayload" in schemas
        assert "evidence" in schemas["TurnAnswer"]["properties"]
        assert "evidence" in schemas["InvestigationResponse"]["properties"]
        probe = schemas["EvidenceProbePayload"]["properties"]
        for field in ("purpose", "kind", "rows", "truncated", "suppressed_cells", "grade"):
            assert field in probe, field


class TestScalarQuestionsAnswer:
    """The plainest shape there is, over HTTP, on the real warehouse.

    "What is our net collection rate over the last 90 days?" plans one
    probe with no dimensions and no comparison. That frame has no dimension
    column, so both older finding shapes declined it, `findings` came back
    empty, the narrative stage short-circuits on no findings, and the whole
    answer was a chart of a number nobody was told. Typed in so the model
    is out of the loop entirely: what is asserted here is the engine's
    behaviour, not a script's.
    """

    @staticmethod
    def _spec(metric_id: str, comparison: str | None = None) -> TypedInvestigationSpec:
        return TypedInvestigationSpec(
            metric_ids=[metric_id],
            window=WindowSpecModel(quantity="90", unit="day", mode="trailing"),
            comparison=comparison,
        )

    async def _answer(self, spec: TypedInvestigationSpec) -> TurnAnswer:
        transport = httpx.ASGITransport(app=_app(_service()))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            client = HttpInvestigationClient(raw, _token())
            session = await client.open_session(OpenSessionRequest(tenant="demo"))
            response = await client.submit_turn(
                session.session_id, TurnRequest(spec=spec)
            )
        assert isinstance(response, TurnAnswer), response
        return response

    async def test_an_ungrouped_metric_question_yields_a_finding(self) -> None:
        response = await self._answer(self._spec("net_collection_rate"))
        assert response.usage.llm_calls == 1  # the narrative only; the spec was typed
        assert response.findings, "a computed scalar must reach the user as a finding"
        finding = response.findings[0]
        assert finding.referent == "F1"
        assert finding.metric_ids == ["net_collection_rate"]
        assert finding.grade == "direct"
        # unit-honest per the metric contract: a ratio is a percentage
        assert "%" in finding.title
        assert "Decimal" not in finding.title

    async def test_the_scalar_finding_reaches_the_narrative_stage(self) -> None:
        """The second half of the same defect: the narrative composer is
        skipped when a turn has no findings, so a findingless scalar was
        also a silent one."""
        response = await self._answer(self._spec("net_collection_rate"))
        assert response.narrative, "a turn with findings must compose a narrative"
        assert response.findings[0].referent in response.narrative

    async def test_a_scalar_with_a_comparison_states_both_sides(self) -> None:
        response = await self._answer(
            self._spec("net_collection_rate", comparison="prior_period")
        )
        assert response.findings
        title = response.findings[0].title
        assert "from" in title  # "…, up from X vs prior period"
        names = {value.name for value in response.findings[0].values}
        assert "net_collection_rate__prior" in names

    async def test_a_metric_whose_primary_basis_is_unbound_still_answers(self) -> None:
        """``denial_rate`` asks for the ``remit`` basis, which this
        warehouse binds nowhere on the claim entity. §5.3 permits an
        allowed alternate, labeled — so the answer exists and says which
        basis produced it, instead of erroring out of the SQL compiler."""
        response = await self._answer(self._spec("denial_rate", comparison="prior_year"))
        assert response.findings, "the flagship comparison must answer"
        assert response.context_header is not None
        assert response.context_header.basis == "service"
        assert any(
            w.startswith("alternate_basis_used:") and "remit" in w for w in response.warnings
        ), response.warnings
