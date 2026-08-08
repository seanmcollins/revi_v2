"""M7 acceptance: the §10.3 five-turn golden conversation on the real
generated warehouse (wm_003), real base pack, real catalog, canned LLM.

T1 cash decline → T2 payer breakdown (reconciled) → T3 drill into the top
three payers (ONE pinned cohort, CARC mix at the denial grain) → T4 custom
Q1 comparison (comparison-side probes only) → T5 META citing recorded
provenance. Plus: 5-node lineage, stable wm_003, and a full REPLAY from
the recorded operators via the typed-gesture path (§18.1-15) reproducing
identical plan hashes and findings with zero refinement-LLM calls.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from revi_investigation.application.llm.schemas import (
    AnyRefinementOperator,
    RefinementEmissionResponse,
)
from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.records import InvestigationStatus
from revi_kernel.filters import (
    Predicate,
    PredicateOp,
    iter_cohorts,
    iter_predicates,
)
from revi_kernel.probes import AggregationProbe
from revi_kernel.refs import DimensionRef, EntityGrain
from revi_testing.engine_wiring import WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"
ANSWER_KEY = REPO_ROOT / "data" / "answer_key.json"

pytestmark = [
    pytest.mark.golden,
    pytest.mark.skipif(
        not (WAREHOUSE.is_file() and ANSWER_KEY.is_file()),
        reason="generated warehouse missing — run: "
        "uv run python -m revi_warehouse.generate --out data/revi_warehouse.duckdb",
    ),
]

T1_Q = "Why did cash decline last week?"
T2_Q = "Break that down by payer"
T3_Q = "Just the top three payers - what's the CARC mix on their denials?"
T4_Q = "Compare that to Q1"
T5_Q = "Why do you say F2?"

T1_INTERPRETATION = {
    "intent_summary": "Investigate last week's posted-cash decline by payer",
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
}


def _conversation_llm() -> MockLanguageModel:
    llm = MockLanguageModel()
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.94, "clarification_question": None},
        matcher=lambda p: T1_Q in p,
    )
    llm.respond("interpret_question", T1_INTERPRETATION, matcher=lambda p: T1_Q in p)
    llm.respond(
        "classify_turn",
        {"turn_class": "refinement", "confidence": 0.93, "clarification_question": None},
        matcher=lambda p: T2_Q in p,
    )
    llm.respond("resolve_referents", {"resolutions": []}, matcher=lambda p: T2_Q in p)
    llm.respond(
        "emit_refinements",
        {
            "operators": [{"op": "set_dimensions", "dimensions": ["payer"]}],
            "rationale": "split the decline by payer",
        },
        matcher=lambda p: T2_Q in p,
    )
    llm.respond(
        "classify_turn",
        {"turn_class": "refinement", "confidence": 0.92, "clarification_question": None},
        matcher=lambda p: "top three payers" in p,
    )
    llm.respond(
        "resolve_referents",
        {
            "resolutions": [
                {"mention": "the top three payers", "referent_id": "F1", "confidence": 0.95},
                {"mention": "the top three payers", "referent_id": "F2", "confidence": 0.95},
                {"mention": "the top three payers", "referent_id": "F3", "confidence": 0.95},
            ]
        },
        matcher=lambda p: "top three payers" in p,
    )
    llm.respond(
        "emit_refinements",
        {
            "operators": [
                {"op": "drill_into", "target": "F1"},
                {"op": "drill_into", "target": "F2"},
                {"op": "drill_into", "target": "F3"},
                {"op": "pivot", "measures": ["denied_dollars"]},
                {"op": "set_dimensions", "dimensions": ["carc"]},
            ],
            "rationale": "pin the top-three payer cohort; denied dollars by CARC",
        },
        matcher=lambda p: "top three payers" in p,
    )
    llm.respond(
        "classify_turn",
        {"turn_class": "refinement", "confidence": 0.9, "clarification_question": None},
        matcher=lambda p: T4_Q in p,
    )
    llm.respond("resolve_referents", {"resolutions": []}, matcher=lambda p: T4_Q in p)
    llm.respond(
        "emit_refinements",
        {
            "operators": [
                {
                    "op": "set_comparison",
                    "kind": None,
                    "custom": {"start": "2026-01-01", "end": "2026-03-31"},
                }
            ],
            "rationale": "compare the decline week against calendar Q1",
        },
        matcher=lambda p: T4_Q in p,
    )
    llm.respond(
        "classify_turn",
        {"turn_class": "meta", "confidence": 0.95, "clarification_question": None},
        matcher=lambda p: T5_Q in p,
    )
    return llm


@dataclass
class Conversation:
    engine: WiredEngine
    outcomes: list[TurnOutcome]
    executes_after: list[int]  # cumulative repository execute count per turn


@pytest.fixture(scope="module")
def answer_key() -> dict[str, Any]:
    scenario = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    return scenario["scenarios"]["3_cash_decline"]["snap_003"]  # wm_003


@pytest.fixture(scope="module")
def conversation() -> Conversation:
    engine = build_duckdb_engine(warehouse_path=WAREHOUSE, llm=_conversation_llm())

    async def run() -> Conversation:
        outcomes: list[TurnOutcome] = []
        executes: list[int] = []
        session_id: str | None = None
        for question in (T1_Q, T2_Q, T3_Q, T4_Q, T5_Q):
            outcome = await engine.submit.submit(
                SubmitTurnRequest(tenant="demo", question=question, session_id=session_id)
            )
            session_id = outcome.session.id
            outcomes.append(outcome)
            executes.append(engine.repository.execute_count)
        return Conversation(engine=engine, outcomes=outcomes, executes_after=executes)

    return asyncio.run(run())


async def _trace_payload(conversation: Conversation, turn: int) -> dict[str, Any]:
    trace = await conversation.engine.trace_store.get(conversation.outcomes[turn].trace_id)
    assert trace is not None
    return dict(trace.payload)


class TestTurnByTurn:
    async def test_t1_baseline(self, conversation: Conversation) -> None:
        t1 = conversation.outcomes[0]
        assert t1.investigation.status is InvestigationStatus.COMPLETE
        assert [f.referent.value for f in t1.findings] == ["F1", "F2", "F3"]
        assert t1.header is not None and t1.header.watermark_id == "wm_003"

    async def test_t2_diff_executes_nothing_new(self, conversation: Conversation) -> None:
        t2 = conversation.outcomes[1]
        assert t2.investigation.status is InvestigationStatus.COMPLETE
        assert t2.diff is not None
        assert t2.diff.added == () and t2.diff.removed == ()
        # only the changed probe set executes — here that set is empty
        assert conversation.executes_after[1] == conversation.executes_after[0]

    async def test_t2_reconciles_children_to_t1_totals(
        self, conversation: Conversation, answer_key: dict[str, Any]
    ) -> None:
        t2 = conversation.outcomes[1]
        assert t2.reconciliation is not None and "passed" in t2.reconciliation
        by_payer = dict(t2.frames)["cash_by_payer__compare"]
        current_sum = sum(
            v for v in by_payer.column("cash_posted") if isinstance(v, int)
        )
        prior_sum = sum(
            v for v in by_payer.column("cash_posted__prior") if isinstance(v, int)
        )
        assert current_sum == answer_key["week_decline"]["payer_cash_cents"]  # 132,844,152
        assert prior_sum == answer_key["week_prior"]["payer_cash_cents"]  # 152,196,731
        # payer rows are addressable referents
        assert any(
            entry.dimension_value is not None and entry.dimension_value[0] == "payer"
            for entry in t2.referents
        )

    async def test_t3_pins_one_cohort_and_cuts_carc_at_denial_grain(
        self, conversation: Conversation
    ) -> None:
        t3 = conversation.outcomes[2]
        assert t3.investigation.status is InvestigationStatus.COMPLETE
        cohort = t3.investigation.spec.context.cohort
        assert cohort is not None
        assert cohort.pinned is not None
        assert cohort.pinned.watermark.id == "wm_003"
        assert cohort.size > 0 and cohort.pinned.size == cohort.size
        # ONE cohort for the whole gesture, saved with metadata
        stored = await conversation.engine.cohort_store.get(cohort.id)
        assert stored is not None
        # the cohort definition names exactly the three payers
        [cohort_predicate] = [
            p
            for p in iter_predicates(cohort.definition.scope)
            if p.dimension.id == "payer"
        ]
        assert cohort_predicate.op is PredicateOp.IN
        assert set(cohort_predicate.values) == {
            "State Medicaid",
            "Atlas Commercial",
            "Meridian Health",
        }
        # probes ran at the denial grain through the claim-cohort semi-join
        executed = conversation.engine.repository.executed_probes[
            conversation.executes_after[1] : conversation.executes_after[2]
        ]
        assert executed, "T3 executed the cohort-scoped probes"
        for probe in executed:
            assert isinstance(probe, AggregationProbe)
            assert probe.grain.entity is EntityGrain.DENIAL
            assert any(iter_cohorts(probe.scope))
        # CARC mix comes back non-empty
        carc_frame = dict(t3.frames)["main__compare"]
        assert carc_frame.row_count > 0
        assert "carc" in carc_frame.schema.names
        # the context header shows the cohort
        assert t3.header is not None and t3.header.cohort == cohort.id
        assert f"cohort: {cohort.id}" in t3.header.display

    async def test_t3_cohort_semijoin_equals_payer_predicate(
        self, conversation: Conversation
    ) -> None:
        """The pinned cohort restricts exactly to the three payers' claims."""
        engine = conversation.engine
        executed = engine.repository.executed_probes[
            conversation.executes_after[1] : conversation.executes_after[2]
        ]
        probe = next(p for p in executed if isinstance(p, AggregationProbe))
        payers = ("State Medicaid", "Atlas Commercial", "Meridian Health")
        equivalent = replace(
            probe,
            scope=Predicate(DimensionRef("payer"), PredicateOp.IN, payers),
        )
        watermarks = await engine.repository.list_watermarks()
        cohort_frame = await engine.repository.execute(probe, watermark=watermarks[-1])
        predicate_frame = await engine.repository.execute(equivalent, watermark=watermarks[-1])
        assert cohort_frame.rows == predicate_frame.rows

    async def test_t4_executes_only_comparison_side(self, conversation: Conversation) -> None:
        t4 = conversation.outcomes[3]
        assert t4.investigation.status is InvestigationStatus.COMPLETE
        assert t4.header is not None
        assert t4.header.comparison_kind == "custom"
        assert t4.header.comparison_start == date(2026, 1, 1)
        assert t4.header.comparison_end == date(2026, 3, 31)
        executed = conversation.engine.repository.executed_probes[
            conversation.executes_after[2] : conversation.executes_after[3]
        ]
        assert executed, "the Q1 side had to execute"
        for probe in executed:
            assert isinstance(probe, AggregationProbe)
            assert probe.window.range.start == date(2026, 1, 1)
            assert probe.window.range.end == date(2026, 3, 31)
        # the primary side was served entirely from the evidence cache
        trace = await _trace_payload(conversation, 3)
        primary = [p for p in trace["probes"] if not p["id"].endswith("__prior")]
        assert primary and all(p["cache_hit"] is True for p in primary)

    async def test_t5_meta_cites_t1_provenance_with_zero_probes(
        self, conversation: Conversation
    ) -> None:
        t1, t5 = conversation.outcomes[0], conversation.outcomes[4]
        assert conversation.executes_after[4] == conversation.executes_after[3]  # zero probes
        assert t5.meta is not None
        assert t5.meta.referent == "F2"
        assert t5.meta.investigation_id == t1.investigation.id
        assert "Atlas Commercial" in t5.meta.label
        t1_trace = await _trace_payload(conversation, 0)
        assert [p["hash"] for p in t5.meta.probes] == [p["hash"] for p in t1_trace["probes"]]
        assert t5.meta.operators and all("version" in op for op in t5.meta.operators)
        assert t5.meta.grades  # evidence grades behind the claim
        assert dict(t5.meta.finding_values)["delta_cents"] == t1.findings[1].value("delta_cents")


class TestWholeSession:
    async def test_lineage_is_a_five_node_dag_with_typed_edges(
        self, conversation: Conversation
    ) -> None:
        session_id = conversation.outcomes[0].session.id
        lineage = await conversation.engine.investigation_store.lineage(session_id)
        assert lineage is not None
        assert len(lineage.investigations) == 5
        assert len(lineage.edges) == 4
        ids = [outcome.investigation.id for outcome in conversation.outcomes]
        chain = [(edge.parent_id, edge.child_id) for edge in lineage.edges]
        assert chain == [
            (ids[0], ids[1]),
            (ids[1], ids[2]),
            (ids[2], ids[3]),
            (ids[3], ids[4]),
        ]
        op_names = [
            [type(op).__name__ for op in edge.operators] for edge in lineage.edges
        ]
        assert op_names == [
            ["SetDimensions"],
            ["DrillInto", "DrillInto", "DrillInto", "Pivot", "SetDimensions"],
            ["SetComparison"],
            [],
        ]

    async def test_every_answer_carries_the_context_header_at_wm003(
        self, conversation: Conversation
    ) -> None:
        for outcome in conversation.outcomes:
            assert outcome.header is not None
            assert outcome.header.watermark_id == "wm_003"
            assert outcome.watermark_stale is False


@pytest.fixture(scope="module")
def replayed(conversation: Conversation) -> Conversation:
    """Replay all five turns on a FRESH engine: T2-T4 as typed gestures
    from the recorded operators (no refinement LLM), T1/T5 with only the
    classification/interpretation canned data."""
    llm = MockLanguageModel()
    llm.respond(
        "classify_turn",
        {
            "turn_class": "new_investigation",
            "confidence": 0.94,
            "clarification_question": None,
        },
        matcher=lambda p: T1_Q in p,
    )
    llm.respond("interpret_question", T1_INTERPRETATION, matcher=lambda p: T1_Q in p)
    llm.respond(
        "classify_turn",
        {"turn_class": "meta", "confidence": 0.95, "clarification_question": None},
        matcher=lambda p: T5_Q in p,
    )
    engine = build_duckdb_engine(warehouse_path=WAREHOUSE, llm=llm)

    async def run() -> Conversation:
        recorded: list[tuple[AnyRefinementOperator, ...]] = []
        for turn in (1, 2, 3):
            payload = await _trace_payload(conversation, turn)
            ops = RefinementEmissionResponse.model_validate(
                {"operators": payload["refinement"]["operators"], "rationale": "replay"}
            ).operators
            recorded.append(tuple(ops))
        outcomes: list[TurnOutcome] = []
        executes: list[int] = []
        t1 = await engine.submit.submit(SubmitTurnRequest(tenant="demo", question=T1_Q))
        outcomes.append(t1)
        executes.append(engine.repository.execute_count)
        for ops in recorded:
            outcome = await engine.submit.submit(
                SubmitTurnRequest(
                    tenant="demo",
                    question="(replayed gesture)",
                    session_id=t1.session.id,
                    refinements=ops,
                )
            )
            outcomes.append(outcome)
            executes.append(engine.repository.execute_count)
        t5 = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question=T5_Q, session_id=t1.session.id)
        )
        outcomes.append(t5)
        executes.append(engine.repository.execute_count)
        return Conversation(engine=engine, outcomes=outcomes, executes_after=executes)

    return asyncio.run(run())


class TestReplayDeterminism:
    async def test_replay_reproduces_plan_hashes_and_findings(
        self, conversation: Conversation, replayed: Conversation
    ) -> None:
        for turn in range(4):  # T1-T4 are analytical
            original = conversation.outcomes[turn]
            replay = replayed.outcomes[turn]
            assert replay.investigation.status is InvestigationStatus.COMPLETE
            assert replay.investigation.plan_hash == original.investigation.plan_hash, (
                f"turn {turn + 1} plan hash drifted on replay"
            )
            assert [f.impact_cents for f in replay.findings] == [
                f.impact_cents for f in original.findings
            ]
            assert [f.title for f in replay.findings] == [f.title for f in original.findings]

    async def test_replay_used_no_refinement_llm(self, replayed: Conversation) -> None:
        llm = replayed.engine.llm
        assert llm.calls_for("resolve_referents") == ()
        assert llm.calls_for("emit_refinements") == ()
        # exactly two classifications (T1 + T5) and one interpretation
        assert len(llm.calls_for("classify_turn")) == 2
        assert len(llm.calls_for("interpret_question")) == 1

    async def test_replay_meta_cites_identical_provenance(
        self, conversation: Conversation, replayed: Conversation
    ) -> None:
        original_meta = conversation.outcomes[4].meta
        replay_meta = replayed.outcomes[4].meta
        assert original_meta is not None and replay_meta is not None
        assert [p["hash"] for p in replay_meta.probes] == [
            p["hash"] for p in original_meta.probes
        ]
        assert replay_meta.label == original_meta.label
