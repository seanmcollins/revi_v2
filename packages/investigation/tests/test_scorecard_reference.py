"""The two scorecard questions, end to end over the generated warehouse.

"What is my top performing payer?" and "Assess the performance of each
facility financially" (guide question G6) both routed correctly and both
dead-ended: their playbooks answer by a panel across measures, and that
step had never been built, so a well-formed question with six direct-grade
result sets behind it fell into the refusal machinery.

These run the real pipeline against the reference dataset and check the two
things a scorecard owes: that it answers at all, and that the verdict
sentence is arithmetic over the orderings beneath it rather than a score
nobody can inspect. Every figure comes from the same DuckDB file
``make warehouse`` writes the answer key from, so the population these
turns measure is checked against the key rather than against itself.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
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

PAYER_Q = "What is my top performing payer?"
FACILITY_Q = "Assess the performance of each facility financially"

PAYER_INTERPRETATION: dict[str, Any] = {
    "intent_summary": "Rate payers across the governed payer-performance measures",
    "metric_ids": [],
    "dimension_ids": ["payer"],
    "concept_ids": [],
    "playbook_id": "payer_scorecard",
    "window": None,
    "basis": None,
    "comparison": None,
    "scope": [],
    "clarification": None,
    "definitional_terms": [],
    # "which one" — the shape the composer leads with (design M45).
    "answer_shape": "entity",
}

FACILITY_INTERPRETATION: dict[str, Any] = {
    **PAYER_INTERPRETATION,
    "intent_summary": "Assess each facility across the governed scorecard measures",
    "dimension_ids": ["facility"],
    "playbook_id": "dimension_scorecard",
}


def _llm() -> MockLanguageModel:
    llm = MockLanguageModel()
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.95, "clarification_question": None},
    )
    # Matched on the UTTERANCE, not on a phrase in it: both playbooks now
    # declare triggers naming the other's subject, and those triggers are
    # rendered into the prompt, so "facility" matches the payer prompt too.
    llm.respond(
        "interpret_question", FACILITY_INTERPRETATION, matcher=lambda p: FACILITY_Q in p
    )
    llm.respond("interpret_question", PAYER_INTERPRETATION, matcher=lambda p: PAYER_Q in p)
    llm.respond("compose_narrative", {"narrative": "placeholder"})
    return llm


@dataclass
class Scorecards:
    payer: TurnOutcome
    facility: TurnOutcome
    engine: WiredEngine


@pytest.fixture(scope="module")
def scorecards() -> Scorecards:
    engine = build_duckdb_engine(warehouse_path=WAREHOUSE, llm=_llm())

    async def run() -> Scorecards:
        payer = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question=PAYER_Q)
        )
        facility = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question=FACILITY_Q)
        )
        return Scorecards(payer=payer, facility=facility, engine=engine)

    return asyncio.run(run())


@pytest.fixture(scope="module")
def answer_key() -> dict[str, Any]:
    scenario = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    return scenario["scenarios"]["3_cash_decline"]["snap_003"]


def _panel_frame(outcome: TurnOutcome):  # type: ignore[no-untyped-def]
    return next(frame for frame_id, frame in outcome.frames if frame_id.endswith("__panel"))


class TestTheScorecardAnswers:
    """The defect itself: a well-formed question with a real answer behind
    it, refused."""

    def test_the_payer_scorecard_answers_rather_than_refusing(
        self, scorecards: Scorecards
    ) -> None:
        assert scorecards.payer.clarification is None
        assert scorecards.payer.findings

    def test_the_facility_scorecard_answers_too(self, scorecards: Scorecards) -> None:
        """Guide question G6, at the same gap."""
        assert scorecards.facility.clarification is None
        assert scorecards.facility.findings

    def test_the_panel_is_one_row_per_entity_over_the_keys_own_payers(
        self, scorecards: Scorecards, answer_key: dict[str, Any]
    ) -> None:
        """The population the card rates is the population the answer key
        computed, not a set this turn assembled for itself."""
        frame = _panel_frame(scorecards.payer)
        rows = set(frame.column("payer"))

        assert rows <= {row["payer_name"] for row in answer_key["by_payer"]}
        assert len(rows) == len(frame.rows)  # one row per payer, no duplicates

    def test_each_entity_appears_once_on_the_facility_panel(
        self, scorecards: Scorecards
    ) -> None:
        frame = _panel_frame(scorecards.facility)
        assert len(set(frame.column("facility"))) == len(frame.rows)


class TestTheVerdictIsArithmeticOverTheOrderingsBeneathIt:
    def test_the_first_finding_is_the_verdict(self, scorecards: Scorecards) -> None:
        """F1 answers "which one", either by naming it or by saying that
        nobody leads. Both are answers; the composer leads with whichever
        this data supports."""
        first = scorecards.payer.findings[0].title

        assert first.startswith("No payer leads overall") or " leads on " in first

    def test_the_verdict_never_publishes_a_combined_score(
        self, scorecards: Scorecards
    ) -> None:
        """Averaging a denial rate against posted cash needs weights nobody
        authored. The verdict counts firsts instead, and says so."""
        verdict = scorecards.payer.findings[0]

        assert "score" not in verdict.title.lower()
        assert verdict.impact_cents is None
        assert "no combined score" in verdict.statement or "Different payers" in verdict.statement

    def test_the_count_ties_out_to_the_per_measure_findings(
        self, scorecards: Scorecards
    ) -> None:
        """The verdict and the columns under it can never name two different
        leaders: the count is read off the same published rows."""
        findings = scorecards.payer.findings
        verdict = findings[0]
        counted = {ref.id for ref in verdict.metric_refs}
        leaders = [
            f for f in findings[1:] if f.metric_refs[0].id in counted and "is first on" in f.statement
        ]

        assert len(leaders) == len(counted)
        if " leads on " in verdict.title:
            named = verdict.title.split(" leads on ")[0]
            wins = sum(1 for f in leaders if f.statement.startswith(named))
            assert f"leads on {wins} of {len(counted)} measures" in verdict.title

    def test_a_neutral_measure_nominates_nobody(self, scorecards: Scorecards) -> None:
        """Charges and claim volume are ``neutral`` in their contracts, so
        the panel gives them no ordering and no finding claims a leader on
        them. A "best" over charges asserts that billing more is better."""
        frame = _panel_frame(scorecards.payer)

        assert "charges" in frame.schema.names
        assert "charges__rank" not in frame.schema.names
        assert not any(
            "first on charges" in f.statement for f in scorecards.payer.findings
        )

    def test_a_dollar_column_is_a_notable_and_never_a_leader(
        self, scorecards: Scorecards
    ) -> None:
        """Posted cash scales with how much business a payer sends. Its head
        is the biggest payer, and the sentence says which."""
        cash = [
            f
            for f in scorecards.payer.findings[1:]
            if f.metric_refs[0].id == "cash_posted"
        ]
        for finding in cash:
            assert "is first on" not in finding.statement
            assert "names the biggest, not the best" in finding.statement


class TestTheScorecardChartsAsATable:
    def test_the_panel_charts_first_and_charts_as_a_table(
        self, scorecards: Scorecards
    ) -> None:
        """There is no bar chart of a scorecard: a denial rate and a dollar
        total share no axis. The table is the form, not the fallback."""
        from revi_presentation.charts import build_chart_specs

        specs = build_chart_specs(list(scorecards.payer.frames), suppression_threshold=11)

        assert specs[0].chart_type == "table"
        assert specs[0].x == "payer"
        # Long format: one mark per (entity, measure), which is how the wire
        # keys marks and how the row-key integrity check reads them.
        keys = [(row.x, row.series) for row in specs[0].rows]
        assert len(keys) == len(set(keys))

    def test_the_small_rankings_are_rates_before_dollars(
        self, scorecards: Scorecards
    ) -> None:
        """"First on the write-off rate" is the best payer; "first on posted
        cash" is the biggest one. When there is room for three, the three
        are rates."""
        from revi_presentation.charts import build_chart_specs

        specs = build_chart_specs(list(scorecards.payer.frames), suppression_threshold=11)
        small = [s for s in specs if s.chart_type == "bar" and s.frame_id.endswith("__panel")]

        assert small
        assert all(spec.unit != "money_cents" for spec in small)
        assert all(spec.sort is not None and spec.sort.by.endswith("__rank") for spec in small)
