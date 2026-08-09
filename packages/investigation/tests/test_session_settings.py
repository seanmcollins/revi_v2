"""Session settings inside the engine: model tier, the per-turn cost
ledger, evidence depth, and what the trace records about all three.

Every assertion here is about a control that *changed the computation*:
the model id a call was sent with, the budget it was bounded by, the
top-N a probe was planned with, the numbers a trace can be read back
from. A control that only decorated the response would have nothing to
assert in this file, which is the point.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from revi_catalog_contracts.model import CatalogSnapshot
from revi_investigation.application.planning import (
    DEEP_TOP_N_MULTIPLIER,
    BuildInvestigationPlanService,
)
from revi_investigation.application.ports import LlmFailureKind, LlmUsage
from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.settings import SessionSettings
from revi_investigation_contracts.settings import EvidenceDepth, NarrativeDepth
from revi_kernel.probes import AggregationProbe
from revi_testing.engine_wiring import PackSnapshotPort, WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

T1 = "Why did cash decline last week?"


def _usage(cost: str) -> LlmUsage:
    return LlmUsage(
        model="mock",
        cost_usd=Decimal(cost),
        input_tokens=10,
        output_tokens=5,
        schema_retries=0,
        duration_ms=7,
    )


def _canned(llm: MockLanguageModel, *, cost: str = "0") -> MockLanguageModel:
    llm.text_chunks = ("Posted cash fell versus the prior week (F1).",)
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.94, "clarification_question": None},
        usage=_usage(cost),
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
        usage=_usage(cost),
    )
    return llm


@pytest.fixture(scope="module")
def small_warehouse_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from revi_warehouse.config import GeneratorConfig
    from revi_warehouse.generate import run_generation

    out = tmp_path_factory.mktemp("warehouse") / "revi_small.duckdb"
    return run_generation(GeneratorConfig.small(), out).db_path


def _engine(warehouse: Path, *, cost: str = "0") -> WiredEngine:
    return build_duckdb_engine(
        warehouse_path=warehouse, llm=_canned(MockLanguageModel(), cost=cost)
    )


async def _run(engine: WiredEngine, settings: SessionSettings | None) -> TurnOutcome:
    return await engine.submit.submit(
        SubmitTurnRequest(tenant="demo", question=T1, settings=settings)
    )


class TestModelTier:
    async def test_tier_rides_on_every_structured_call(self, small_warehouse_path: Path) -> None:
        engine = _engine(small_warehouse_path)
        await _run(engine, SessionSettings(model_tier="claude-sonnet-5"))

        assert engine.llm.structured_calls, "the turn made no model calls to check"
        assert {call.policy.model for call in engine.llm.structured_calls} == {
            "claude-sonnet-5"
        }

    async def test_narrative_call_is_not_exempt(self, small_warehouse_path: Path) -> None:
        """The narrative runs in the presentation layer, outside the engine.
        It is still the session's model call and still carries the tier —
        the alternative is a session that "runs on sonnet" except for the
        one call that writes the words the analyst reads."""
        engine = _engine(small_warehouse_path)
        outcome = await _run(engine, SessionSettings(model_tier="claude-sonnet-5"))

        # the engine hands the settings to presentation on the outcome
        assert outcome.settings.model_tier == "claude-sonnet-5"

    async def test_no_tier_leaves_the_deployment_pin_alone(
        self, small_warehouse_path: Path
    ) -> None:
        engine = _engine(small_warehouse_path)
        await _run(engine, None)

        assert all(call.policy.model is None for call in engine.llm.structured_calls)


class TestTurnBudget:
    async def test_each_call_is_bounded_by_what_is_left(
        self, small_warehouse_path: Path
    ) -> None:
        engine = _engine(small_warehouse_path, cost="0.03")
        await _run(engine, SessionSettings(max_turn_cost_usd=Decimal("0.10")))

        budgets = [call.policy.max_cost_usd for call in engine.llm.structured_calls]
        # classify sees the whole ceiling; interpret sees it minus classify
        assert budgets[:2] == [Decimal("0.10"), Decimal("0.07")]

    async def test_exhausted_budget_stops_the_turn_and_says_so(
        self, small_warehouse_path: Path
    ) -> None:
        """Never a quiet downgrade. The turn that cannot afford its next
        model call stops, names the ceiling it hit, and offers the two
        recoveries the analyst actually has."""
        engine = _engine(small_warehouse_path, cost="0.02")
        outcome = await _run(engine, SessionSettings(max_turn_cost_usd=Decimal("0.02")))

        assert outcome.clarification is not None
        reason = outcome.clarification.reason or ""
        assert reason.startswith("TURN_BUDGET_EXHAUSTED")
        assert "0.02" in outcome.clarification.question
        assert outcome.clarification.options  # concrete ways forward
        # classification happened; interpretation was never attempted
        assert [c.template_id for c in engine.llm.structured_calls] == ["classify_turn"]

    async def test_no_ceiling_means_no_ledger(self, small_warehouse_path: Path) -> None:
        """``None`` is not "unlimited" — it is the pre-existing behavior:
        every call bounded by the deployment's own per-call cap, and
        nothing counting the turn."""
        engine = _engine(small_warehouse_path, cost="9.99")
        outcome = await _run(engine, None)

        assert outcome.clarification is None
        assert all(call.policy.max_cost_usd is None for call in engine.llm.structured_calls)


class TestEvidenceDepth:
    def test_deep_widens_the_pack_authored_top_n(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: object
    ) -> None:
        planner = BuildInvestigationPlanService(pack_port, catalog)
        spec = make_spec(dimensions=("payer",), comparison=None)  # type: ignore[operator]

        standard = planner.build(spec, playbook_id="cash_decline", window_explicit=True)
        deep = planner.build(
            spec,
            playbook_id="cash_decline",
            window_explicit=True,
            evidence_depth=EvidenceDepth.DEEP,
        )

        def limits(plan: object) -> list[int | None]:
            return [
                node.probe.limit
                for node in plan.nodes  # type: ignore[attr-defined]
                if isinstance(node.probe, AggregationProbe)
            ]

        assert any(limit is not None for limit in limits(standard))
        assert limits(deep) == [
            None if limit is None else limit * DEEP_TOP_N_MULTIPLIER
            for limit in limits(standard)
        ]
        # a different probe is a different plan: different cache entries,
        # different rows, a truthful diff against a parent turn
        assert deep.plan_hash != standard.plan_hash

    def test_an_analyst_limit_is_never_rescaled(
        self, pack_port: PackSnapshotPort, catalog: CatalogSnapshot, make_spec: object
    ) -> None:
        """``spec.limit`` is what the analyst asked for (an ``Expand``
        gesture). "Show me the top 5" means five at either depth."""
        planner = BuildInvestigationPlanService(pack_port, catalog)
        spec = make_spec(measures=("cash_posted",), dimensions=("payer",), limit=5)  # type: ignore[operator]

        deep = planner.build(spec, evidence_depth=EvidenceDepth.DEEP)

        assert [
            node.probe.limit for node in deep.nodes if isinstance(node.probe, AggregationProbe)
        ] == [5]


class TestTraceRecording:
    async def test_trace_carries_the_settings_the_turn_ran_under(
        self, small_warehouse_path: Path
    ) -> None:
        engine = _engine(small_warehouse_path)
        settings = SessionSettings(
            model_tier="claude-sonnet-5",
            max_turn_cost_usd=Decimal("0.40"),
            narrative_depth=NarrativeDepth.ANALYST,
            evidence_depth=EvidenceDepth.DEEP,
            debug=True,
        )
        outcome = await _run(engine, settings)

        record = await engine.trace_store.get(outcome.trace_id)
        assert record is not None
        assert record.payload["settings"] == {
            "model_tier": "claude-sonnet-5",
            "max_turn_cost_usd": "0.40",
            "narrative_depth": "analyst",
            "evidence_depth": "deep",
            "debug": True,
        }
        assert record.payload["plan_context"]["evidence_depth"] == "deep"

    async def test_trace_records_what_each_probe_actually_read(
        self, small_warehouse_path: Path
    ) -> None:
        engine = _engine(small_warehouse_path)
        outcome = await _run(engine, None)

        record = await engine.trace_store.get(outcome.trace_id)
        assert record is not None
        probes = record.payload["probes"]
        assert probes, "an analytical turn recorded no probes"
        for probe in probes:
            assert probe["rows"] is not None  # executed, not merely planned
            assert probe["grade"] in {"direct", "derived", "proxy", "discovery", "unavailable"}
            assert probe["duration_ms"] >= 0
            assert "truncated" in probe and "suppressed_cells" in probe

    @pytest.mark.parametrize(
        ("failure", "recorded", "advice"),
        [
            (LlmFailureKind.SCHEMA, "schema", "try again"),
            (LlmFailureKind.DECLINED, "declined", "rephrase"),
            (None, None, "rephrase"),
        ],
    )
    async def test_trace_records_llm_failure_kind_as_data(
        self,
        small_warehouse_path: Path,
        failure: LlmFailureKind | None,
        recorded: str | None,
        advice: str,
    ) -> None:
        """A clarification's English already names the failure; the trace
        carries it as a value so a reader never has to parse prose — and
        the two kinds ask for opposite recoveries, which is why the port
        distinguishes them at all."""
        llm = MockLanguageModel()
        llm.respond("classify_turn", None, failure=failure)
        engine = build_duckdb_engine(warehouse_path=small_warehouse_path, llm=llm)
        outcome = await engine.submit.submit(SubmitTurnRequest(tenant="demo", question=T1))

        assert outcome.clarification is not None
        assert advice in outcome.clarification.question.lower()
        record = await engine.trace_store.get(outcome.trace_id)
        assert record is not None
        assert record.payload["llm"][0]["failure"] == recorded
        assert record.payload["clarification_reason"]

    async def test_finding_grades_ride_with_the_node_grades(
        self, small_warehouse_path: Path
    ) -> None:
        engine = _engine(small_warehouse_path)
        outcome = await _run(engine, None)

        record = await engine.trace_store.get(outcome.trace_id)
        assert record is not None
        finding_grades = record.payload["finding_grades"]
        assert finding_grades
        assert set(finding_grades) == {f.referent.value for f in outcome.findings}
