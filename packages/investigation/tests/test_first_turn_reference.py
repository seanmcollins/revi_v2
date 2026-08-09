"""M5 acceptance: "Why did cash decline last week?" end to end against the
real generated warehouse (wm_003), the real base pack, the real catalog,
and the real DuckDB repository — with a canned LLM and faked stores.

Expected numbers come from ``data/answer_key.json`` (never hardcoded): at
wm_003 payer cash declined 152,196,731 → 132,844,152 cents (≈ -12.7%);
the top payer-level declines are State Medicaid and Atlas Commercial.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.records import InvestigationStatus
from revi_kernel.filters import iter_predicates
from revi_kernel.refs import EntityGrain
from revi_testing.engine_wiring import WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"
ANSWER_KEY = REPO_ROOT / "data" / "answer_key.json"

pytestmark = [
    pytest.mark.reference,
    pytest.mark.skipif(
        not (WAREHOUSE.is_file() and ANSWER_KEY.is_file()),
        reason="generated warehouse missing — run: "
        "uv run python -m revi_warehouse.generate --out data/revi_warehouse.duckdb",
    ),
]

QUESTION = "Why did cash decline last week?"


@pytest.fixture(scope="module")
def answer_key() -> dict[str, Any]:
    scenario = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    return scenario["scenarios"]["3_cash_decline"]["snap_003"]  # wm_003


@pytest.fixture(scope="module")
def engine() -> WiredEngine:
    llm = MockLanguageModel()
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.94, "clarification_question": None},
    )
    llm.respond(
        "interpret_question",
        {
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
        },
    )
    return build_duckdb_engine(warehouse_path=WAREHOUSE, llm=llm)


@pytest.fixture(scope="module")
def outcome(engine: WiredEngine) -> TurnOutcome:
    return asyncio.run(
        engine.submit.submit(SubmitTurnRequest(tenant="demo", question=QUESTION))
    )


class TestReferenceFirstTurn:
    async def test_context_header(self, outcome: TurnOutcome) -> None:
        header = outcome.header
        assert header is not None
        assert header.window_start == date(2026, 7, 27)
        assert header.window_end == date(2026, 8, 2)
        assert header.basis == "post"
        assert header.comparison_kind == "prior_period"
        assert header.comparison_start == date(2026, 7, 20)
        assert header.comparison_end == date(2026, 7, 26)
        assert header.watermark_id == "wm_003"
        assert outcome.session.watermark.id == "wm_003"

    async def test_total_decline_matches_answer_key(
        self, outcome: TurnOutcome, answer_key: dict[str, Any]
    ) -> None:
        totals = dict(outcome.frames)["weekly_cash_trend__compare"]
        [row] = totals.rows
        current = row[totals.schema.index_of("cash_posted")]
        prior = row[totals.schema.index_of("cash_posted__prior")]
        delta = row[totals.schema.index_of("cash_posted__delta")]
        pct = row[totals.schema.index_of("cash_posted__pct_change")]
        assert current == answer_key["week_decline"]["payer_cash_cents"]
        assert prior == answer_key["week_prior"]["payer_cash_cents"]
        assert delta == answer_key["delta_cents"]
        assert isinstance(pct, Decimal)
        assert abs(pct - Decimal(str(answer_key["delta_pct"]))) < Decimal("0.000001")
        # ≈ -12.7%
        assert pct.quantize(Decimal("0.001")) == Decimal("-0.127")

    async def test_findings_match_answer_key_exactly(
        self, outcome: TurnOutcome, answer_key: dict[str, Any]
    ) -> None:
        expected = sorted(answer_key["by_payer"], key=lambda entry: entry["delta_cents"])
        assert outcome.investigation.status is InvestigationStatus.COMPLETE
        assert [f.referent.value for f in outcome.findings] == ["F1", "F2", "F3"]
        for finding, entry in zip(outcome.findings, expected[:3], strict=False):
            assert entry["payer_name"] in finding.title
            assert finding.impact_cents == entry["delta_cents"]
            assert finding.value("delta_cents") == entry["delta_cents"]
            assert finding.value("current_cents") == entry["week_decline_cents"]
            assert finding.value("prior_cents") == entry["week_prior_cents"]
            assert finding.grade.value == "direct"
            assert finding.confidence == "high"
        assert expected[0]["payer_name"] == "State Medicaid"  # F1
        assert expected[1]["payer_name"] == "Atlas Commercial"  # F2

    async def test_payer_compare_frame_covers_all_payers(
        self, outcome: TurnOutcome, answer_key: dict[str, Any]
    ) -> None:
        by_payer = dict(outcome.frames)["cash_by_payer__compare"]
        payers = set(by_payer.column("payer"))
        assert payers == {entry["payer_name"] for entry in answer_key["by_payer"]}
        idx = by_payer.schema.index_of("cash_posted__delta")
        deltas = {
            row[by_payer.schema.index_of("payer")]: row[idx] for row in by_payer.rows
        }
        for entry in answer_key["by_payer"]:
            assert deltas[entry["payer_name"]] == entry["delta_cents"]

    async def test_findings_are_drillable_with_registered_referents(
        self, engine: WiredEngine, outcome: TurnOutcome
    ) -> None:
        for finding in outcome.findings:
            registered = await engine.referent_registry.resolve(
                outcome.session.id, finding.referent
            )
            assert registered is not None
            definition = registered.cohort_definition
            assert definition is not None
            assert definition.entity is EntityGrain.CLAIM
            predicates = list(iter_predicates(definition.scope))
            assert any(
                p.dimension.id == "payer" and p.values[0] in finding.title for p in predicates
            )
            assert definition.window is not None
            assert definition.window.range.start == date(2026, 7, 27)
            assert definition.window.range.end == date(2026, 8, 2)
        # every compare row is addressable as a dimension-value referent
        registered_values = {
            entry.referent.value
            for entry in await engine.referent_registry.list_for_session(outcome.session.id)
        }
        assert {"F1", "F2", "F3", "D1", "D12"} <= registered_values

    async def test_trace_record_persisted_per_design_14(
        self, engine: WiredEngine, outcome: TurnOutcome
    ) -> None:
        trace = await engine.trace_store.get(outcome.trace_id)
        assert trace is not None
        payload = trace.payload
        # The first utterance of a session is classified by construction
        # (F11): no model call, and therefore full confidence in a decision
        # nothing guessed at.
        assert payload["classification"] == {"turn_class": "new_investigation", "confidence": 1.0}
        assert payload["interpretation"]["playbook_id"] == "cash_decline"
        assert payload["plan_hash"] == outcome.investigation.plan_hash
        assert payload["watermark"]["id"] == "wm_003"
        probes = payload["probes"]
        # 4 flow probes + 4 comparison twins. The playbook's
        # `lag_distribution_compare` sums the probe-time derived
        # `payment_lag_days`; it was pruned for as long as §6.6 decided
        # answerability from the catalog alone, and runs now that the
        # repository advertises what it computes (§6.3 negotiation).
        assert len(probes) == 8
        assert all(len(p["hash"]) == 64 for p in probes)
        assert all(p["cache_hit"] is False for p in probes)  # first run: all misses
        assert payload["grades"] and set(payload["grades"].values()) == {"direct"}
        operators = {op["operator"] for op in payload["operators"]}
        assert "compare" in operators and "rank" in operators
        assert payload["findings"] == ["F1", "F2", "F3"]
        # It RAN: no omission or skip warning names it. (It publishes no
        # finding, which the coverage disclosure states separately — see
        # ``probe_families_empty`` — and that is a different claim.)
        assert not any(
            "lag_distribution_compare" in w
            and ("omitted" in w or "skipped" in w or "not executable" in w)
            for w in payload["warnings"]
        )
        llm_entries = payload["llm"]
        # The ledger lists calls that HAPPENED. A first turn's class is
        # decided by construction, so no classification call appears — a
        # zero-cost entry for a call nobody made would be a fiction in the
        # one record an auditor reads for what the turn spent.
        assert {entry["template"] for entry in llm_entries} == {"interpret_question"}
        assert all(entry["schema_retries"] == 0 for entry in llm_entries)
        # Transport attempts ride the trace beside cost, so a degrading
        # provider is visible where the spend is (review finding D10).
        assert all(entry["attempts"] == 1 for entry in llm_entries)
        assert payload["timings_ms"]  # per-stage latencies recorded

    async def test_second_identical_run_hits_the_evidence_cache(
        self, engine: WiredEngine, outcome: TurnOutcome
    ) -> None:
        executes_before = engine.repository.execute_count
        rerun = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question=QUESTION, session_id=outcome.session.id
            )
        )
        # identical probes at the same (watermark, pack) — zero new queries
        assert engine.repository.execute_count == executes_before
        assert rerun.investigation.plan_hash == outcome.investigation.plan_hash
        trace = await engine.trace_store.get(rerun.trace_id)
        assert trace is not None
        assert trace.payload["probes"] and all(
            p["cache_hit"] is True for p in trace.payload["probes"]
        )
        # and the numbers are reproduced exactly
        assert [f.impact_cents for f in rerun.findings] == [
            f.impact_cents for f in outcome.findings
        ]
