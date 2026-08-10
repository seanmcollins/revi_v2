"""Clarifying a re-presentation must resume a re-presentation.

A twelve-row payer answer, then "sort them by percent change, largest
first", then the clarification's OWN first option — and the engine ran
``_new_investigation_turn``, re-planning the sentence from scratch: three
findings in the engine's order, a reconciliation reading "this is a first
turn", and a lineage jumping from ``presentation_only`` to
``new_investigation``. A clarification is an interruption, and what it
interrupts decides what resumes. Stated here over the real DuckDB pipeline,
with the classifier scripted to the confidences it actually returned.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from revi_investigation.application.submit_turn import SubmitTurnRequest, TurnOutcome
from revi_investigation.domain.turns import TurnClass
from revi_testing.engine_wiring import WiredEngine, build_duckdb_engine
from revi_testing.mock_llm import MockLanguageModel

if TYPE_CHECKING:
    from conftest import SeedPriorTurn

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

FIRST = "Which payers had the biggest change in denial rate in July 2026 versus June?"
#: Deliberately NOT a phrase the zero-LLM recognizer claims ("mover" is
#: outside its closed vocabulary): this test is about what happens when the
#: classifier does get the turn and comes back unsure, which is the state
#: this defect was found in.
SORT = "please sort them by percent change, biggest mover first"
OPTION = "Just re-sort the rows already shown by percent change, descending"

INTERPRETATION: dict[str, Any] = {
    "intent_summary": "denial rate by payer, July vs June 2026",
    "metric_ids": ["denial_rate"],
    "dimension_ids": ["payer"],
    "concept_ids": [],
    "playbook_id": None,
    "window": {"start": "2026-07-01", "end": "2026-07-31"},
    "basis": None,
    "comparison": "prior_period",
    "scope": [],
    "clarification": None,
    "clarification_options": [],
    "definitional_terms": [],
}


def _asked(utterance: str) -> Callable[[str], bool]:
    def matcher(prompt: str) -> bool:
        _, _, tail = prompt.partition("Utterance:")
        return utterance in tail

    return matcher


def _engine() -> WiredEngine:
    llm = MockLanguageModel()
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.99},
        matcher=_asked(FIRST),
    )
    # The live confidence, verbatim: presentation_only at 0.76, below the
    # classifier's threshold, so a clarification rides with it.
    llm.respond(
        "classify_turn",
        {
            "turn_class": "presentation_only",
            "confidence": 0.76,
            "clarification_question": (
                'Is "percent change" already a column in the results shown, or should it '
                "be computed as a new metric before sorting?"
            ),
            "clarification_options": [OPTION, "Add a percent change column, then sort"],
        },
        matcher=_asked(SORT),
    )
    llm.respond(
        "classify_turn",
        {"turn_class": "clarification_response", "confidence": 0.95},
        matcher=_asked(OPTION),
    )
    llm.respond("interpret_question", INTERPRETATION)
    return build_duckdb_engine(warehouse_path=WAREHOUSE, llm=llm)


async def _chain(
    engine: WiredEngine, seed_prior_turn: SeedPriorTurn
) -> tuple[TurnOutcome, TurnOutcome, TurnOutcome]:
    session_id = await seed_prior_turn(engine)
    first = await engine.submit.submit(
        SubmitTurnRequest(tenant="demo", question=FIRST, session_id=session_id)
    )
    asked = await engine.submit.submit(
        SubmitTurnRequest(tenant="demo", question=SORT, session_id=first.session.id)
    )
    resumed = await engine.submit.submit(
        SubmitTurnRequest(tenant="demo", question=OPTION, session_id=first.session.id)
    )
    return first, asked, resumed


class TestClarifyingAPresentationResumesThePresentation:
    async def test_the_served_finding_set_survives_the_clarification(
        self, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """The headline number: twelve rows in, twelve rows out. It used to
        be twelve in and three out, with no note of any kind."""
        engine = _engine()
        first, asked, resumed = await _chain(engine, seed_prior_turn)

        assert first.findings, "fixture: the parent must publish rows"
        assert asked.clarification is not None, "fixture: the sort must clarify"
        assert resumed.clarification is None, "the resume answers rather than asking again"
        assert {f.title for f in resumed.findings} == {f.title for f in first.findings}
        assert len(resumed.findings) == len(first.findings)

    async def test_the_resume_is_recorded_as_a_re_presentation_not_a_first_turn(
        self, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """Live, the resumed turn was a ``new_investigation`` whose parent
        was the clarification, and whose reconciliation line read "this is a
        first turn" on turn four of a thread."""
        engine = _engine()
        first, _asked, resumed = await _chain(engine, seed_prior_turn)

        assert resumed.investigation.turn_class is not TurnClass.NEW_INVESTIGATION
        assert resumed.investigation.parent_id == first.investigation.id, (
            "the answer this re-serves is its parent — not the question that interrupted it"
        )
        assert resumed.reconciliation is None or "first turn" not in resumed.reconciliation
        assert resumed.investigation.plan_hash == first.investigation.plan_hash

    async def test_the_resume_says_which_answer_it_re_serves(
        self, seed_prior_turn: SeedPriorTurn
    ) -> None:
        engine = _engine()
        _first, _asked, resumed = await _chain(engine, seed_prior_turn)

        leading = resumed.warnings[0]
        assert leading.startswith("Read as an answer to the question above:")
        assert "re-serves" in leading
        assert any(w.startswith("refinement_reused_plan:") for w in resumed.warnings)

    async def test_the_sort_the_analyst_asked_for_is_applied_to_those_rows(
        self, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """The request is applied and the note says it was.
        ``refinement_not_applied`` is what is owed when it CANNOT be
        applied, and only then."""
        engine = _engine()
        _first, _asked, resumed = await _chain(engine, seed_prior_turn)

        applied = [w for w in resumed.warnings if w.startswith("presentation_applied:")]
        assert applied, resumed.warnings
        assert "pct_change" in applied[0]
        assert "largest first" in applied[0]
        assert not [w for w in resumed.warnings if w.startswith("refinement_not_applied:")]

        values = [dict(f.values).get("pct_change") for f in resumed.findings]
        present = [v for v in values if v is not None]
        assert present == sorted(present, reverse=True), "descending, as asked"

    async def test_a_reply_that_is_a_new_question_is_still_run_as_one(
        self, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """The resume rule is narrow on purpose: a self-contained question
        that matches no option we offered is not an answer to it, whichever
        kind of turn asked."""
        engine = _engine()
        engine.llm.respond(
            "classify_turn",
            {"turn_class": "clarification_response", "confidence": 0.9},
            matcher=_asked("What is our denial rate for Atlas Commercial in June 2026?"),
        )
        first = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo", question=FIRST, session_id=await seed_prior_turn(engine)
            )
        )
        await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question=SORT, session_id=first.session.id)
        )

        fresh = await engine.submit.submit(
            SubmitTurnRequest(
                tenant="demo",
                question="What is our denial rate for Atlas Commercial in June 2026?",
                session_id=first.session.id,
            )
        )

        assert fresh.investigation.turn_class is not TurnClass.PRESENTATION_ONLY
