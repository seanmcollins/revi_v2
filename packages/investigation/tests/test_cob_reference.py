"""§18.1-10 acceptance: "Do I have a COB problem?" end to end.

The point is not that COB works — it is that COB works through the SAME
machinery as every other question. Nothing in the engine knows what
coordination of benefits is; pack content carries it (two metric contracts, a
playbook, and a binding table grading each field as evidence for the `cob`
concept). Two properties are asserted together because either alone would be a
lie: the DIRECT branch's numbers equal ``data/answer_key.json``, and the
CARC-concentration branch is *graded* proxy — certified data that is still only
suggestive must not certify a conclusion about coverage (§5.5).
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
from revi_kernel.grades import EvidenceGrade
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

QUESTION = "Do I have a COB problem?"
SCENARIO_PAYER = "Silverline Medicare Advantage"


@pytest.fixture(scope="module")
def answer_key() -> dict[str, Any]:
    scenarios = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))["scenarios"]
    key: dict[str, Any] = scenarios["2_cob_silverline"]["snap_003"]
    return key


@pytest.fixture(scope="module")
def engine() -> WiredEngine:
    llm = MockLanguageModel()
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.93, "clarification_question": None},
    )
    llm.respond(
        "interpret_question",
        {
            "intent_summary": "Assess whether a coordination-of-benefits problem exists",
            "metric_ids": [],
            "dimension_ids": ["payer"],
            # the concept id is what makes grading concept-aware; it is
            # validated against the pack before it reaches the planner
            "concept_ids": ["cob"],
            "playbook_id": "cob_investigation",
            "window": {"quantity": "4", "unit": "month", "mode": "full_periods"},
            "basis": None,
            "comparison": None,
            "scope": [],
            "clarification": None,
            "definitional_terms": [],
        },
    )
    return build_duckdb_engine(warehouse_path=WAREHOUSE, llm=llm)


@pytest.fixture(scope="module")
def outcome(engine: WiredEngine) -> TurnOutcome:
    return asyncio.run(engine.submit.submit(SubmitTurnRequest(tenant="demo", question=QUESTION)))


class TestCobInvestigation:
    async def test_completes_with_the_playbook_window(self, outcome: TurnOutcome) -> None:
        assert outcome.investigation.status is InvestigationStatus.COMPLETE
        header = outcome.header
        assert header is not None
        # the playbook's own 4-month window applies: the analyst named none
        assert header.window_start == date(2026, 4, 1)
        assert header.window_end == date(2026, 7, 31)
        assert header.watermark_id == "wm_003"

    async def test_direct_branch_matches_the_answer_key(
        self, outcome: TurnOutcome, answer_key: dict[str, Any]
    ) -> None:
        frames = dict(outcome.frames)
        direct = frames["cob_mismatch_by_payer"]
        assert direct.evidence_grade is EvidenceGrade.DIRECT
        rows = {
            row[direct.schema.index_of("payer")]: row for row in direct.rows
        }
        row = rows[SCENARIO_PAYER]
        assert row[direct.schema.index_of("cob_mismatch_claims")] == (
            answer_key["cob_mismatch_claims"]
        )
        assert row[direct.schema.index_of("claim_volume")] == (
            answer_key["silverline_claims_in_window"]
        )
        rate = row[direct.schema.index_of("cob_mismatch_rate")]
        assert isinstance(rate, Decimal)
        assert abs(rate - Decimal(str(answer_key["cob_mismatch_share"]))) < Decimal("0.000001")

    async def test_findings_come_from_the_direct_branch(
        self, outcome: TurnOutcome, answer_key: dict[str, Any]
    ) -> None:
        # no comparison anywhere in this playbook: these findings exist only
        # because the concentration path exists (§18.1-10)
        assert outcome.findings, "a ranked playbook must still answer"
        top = outcome.findings[0]
        assert top.referent.value == "F1"
        assert SCENARIO_PAYER in top.title
        assert top.grade is EvidenceGrade.DIRECT
        assert top.confidence == "high"  # DIRECT clears cob_confirmed_claim
        assert top.value("cob_mismatch_claims") == answer_key["cob_mismatch_claims"]
        assert top.value("rank") == 1
        # a claim count is not dollars — impact stays honestly unset
        assert top.impact_cents is None

    async def test_proxy_branch_is_graded_proxy(self, outcome: TurnOutcome) -> None:
        frames = dict(outcome.frames)
        proxy = frames["cob_code_proxy"]
        assert proxy.evidence_grade is EvidenceGrade.PROXY
        # and the grade law carries it through every derived frame
        assert frames["cob_code_proxy__share"].evidence_grade is EvidenceGrade.PROXY
        assert frames["cob_code_proxy__share__rank"].evidence_grade is EvidenceGrade.PROXY

    async def test_trace_records_both_grades(
        self, engine: WiredEngine, outcome: TurnOutcome
    ) -> None:
        trace = await engine.trace_store.get(outcome.trace_id)
        assert trace is not None
        assert trace.payload["grades"] == {
            "cob_mismatch_by_payer": "direct",
            "cob_code_proxy": "proxy",
            # The playbook's rebill-timing probe sums the derived
            # `submission_lag_days`; §6.6 pruned it until the repository
            # began advertising what it computes (§6.3).
            "cob_rebill_timing": "direct",
        }
        assert trace.payload["interpretation"]["playbook_id"] == "cob_investigation"
        assert trace.payload["watermark"]["id"] == "wm_003"

    async def test_findings_are_drillable(
        self, engine: WiredEngine, outcome: TurnOutcome
    ) -> None:
        for finding in outcome.findings:
            registered = await engine.referent_registry.resolve(
                outcome.session.id, finding.referent
            )
            assert registered is not None
            assert registered.cohort_definition is not None
