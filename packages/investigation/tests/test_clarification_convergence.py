"""Clarification dialogues that end (design §2.8).

A clarification is a first-class successful outcome and a dialogue move — but a
dialogue that only ever asks is not a dialogue. One question drew four consecutive
clarifications, one of them after the analyst replied with a VERBATIM option string,
and the turn that finally executed had dropped the original question. Two causes:
nothing told the classifier a clarification was outstanding, so ``CLARIFICATION_RESPONSE``
had no branch and a direct answer fell through to "an answer to a question I haven't
asked"; and nothing bounded the asking, so past ``MAX_CONSECUTIVE_CLARIFICATIONS`` the
engine now commits to its best reading and states the assumption before the numbers.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from revi_investigation.application.interpretation import (
    PendingClarification,
    render_pending_clarification,
)
from revi_investigation.application.submit_turn import (
    MAX_CONSECUTIVE_CLARIFICATIONS,
    SubmitTurnRequest,
    TurnOutcome,
)
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

QUESTION = "How did our denial rate in 2025 compare to 2024?"
OPTION = "Compare full-year 2025 against full-year 2024"

_INTERPRETATION = {
    "intent_summary": "Denial rate, 2025 versus 2024",
    "metric_ids": ["denial_rate"],
    "dimension_ids": [],
    "concept_ids": [],
    "playbook_id": None,
    "window": {"quantity": "1", "unit": "year", "mode": "full_periods"},
    "basis": None,
    "comparison": "prior_year",
    "scope": [],
    "clarification": None,
    "definitional_terms": [],
}


def _asked(utterance: str) -> Callable[[str], bool]:
    """Match on the prompt's UTTERANCE section, not on any occurrence.

    The pending-clarification block echoes the original question back into
    the prompt, so a naive substring matcher fires on the wrong turn — the
    same confusion the feature exists to remove, reproduced in the fixture.
    """

    def matcher(prompt: str) -> bool:
        _, _, tail = prompt.partition("Utterance:")
        return utterance in tail

    return matcher


def _clarifying_classification() -> dict[str, object]:
    return {
        "turn_class": "new_investigation",
        "confidence": 0.2,
        "clarification_question": "Which windows did you mean?",
        "clarification_options": [OPTION, "Compare the last 90 days against the prior 90"],
    }


@pytest.fixture
def engine() -> WiredEngine:
    return build_duckdb_engine(warehouse_path=WAREHOUSE, llm=MockLanguageModel())


async def _turn(engine: WiredEngine, session_id: str | None, question: str) -> TurnOutcome:
    return await engine.submit.submit(
        SubmitTurnRequest(tenant="demo", question=question, session_id=session_id)
    )


async def _opened(engine: WiredEngine, seed_prior_turn: SeedPriorTurn) -> str:
    """A session whose next turn is classified by the model, not by
    construction — every test below is about a classification decision, and
    a session's FIRST utterance is a new investigation without one."""
    session_id: str = await seed_prior_turn(engine)
    return session_id


class TestPendingClarificationReachesTheClassifier:
    def test_the_prompt_block_carries_the_question_and_the_options(self) -> None:
        rendered = render_pending_clarification(
            PendingClarification(
                question="Which windows did you mean?",
                options=(OPTION,),
                original_question=QUESTION,
                streak=1,
            )
        )

        assert "Which windows did you mean?" in rendered
        assert OPTION in rendered
        assert QUESTION in rendered

    def test_no_pending_clarification_says_so_rather_than_going_quiet(self) -> None:
        """An empty block would read as "the section is missing"; the model
        needs to be told the utterance stands alone."""
        assert "No clarification is pending" in render_pending_clarification(None)

    async def test_the_second_turn_is_told_what_the_first_asked(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        engine.llm.respond("classify_turn", _clarifying_classification(), matcher=_asked(QUESTION))
        opened = await _opened(engine, seed_prior_turn)
        first = await _turn(engine, opened, QUESTION)
        assert first.clarification is not None  # premise

        engine.llm.respond(
            "classify_turn",
            {"turn_class": "clarification_response", "confidence": 0.95,
             "clarification_question": None},
            matcher=_asked(OPTION),
        )
        engine.llm.respond("interpret_question", _INTERPRETATION)
        await _turn(engine, first.session.id, OPTION)

        [_, second_classify] = engine.llm.calls_for("classify_turn")
        assert "A clarification IS pending" in second_classify.rendered_prompt
        assert "Which windows did you mean?" in second_classify.rendered_prompt
        assert OPTION in second_classify.rendered_prompt

    async def test_a_verbatim_option_reply_answers_the_original_question(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """The loop, closed. The reply resolves the PENDING intent instead of
        being read as a standalone utterance — and the original question is
        carried into it rather than dropped."""
        engine.llm.respond("classify_turn", _clarifying_classification(), matcher=_asked(QUESTION))
        opened = await _opened(engine, seed_prior_turn)
        first = await _turn(engine, opened, QUESTION)

        engine.llm.respond(
            "classify_turn",
            {"turn_class": "clarification_response", "confidence": 0.95,
             "clarification_question": None},
            matcher=_asked(OPTION),
        )
        engine.llm.respond("interpret_question", _INTERPRETATION)
        second = await _turn(engine, first.session.id, OPTION)

        assert second.clarification is None, "a verbatim option reply must not re-clarify"
        assert second.findings, "the answer the analyst originally asked for"
        # both halves of the resolved utterance are the analyst's own words
        interpret = engine.llm.calls_for("interpret_question")[0].rendered_prompt
        assert QUESTION in interpret
        assert OPTION in interpret
        # and the engine says out loud how it read the reply
        assert any("Read as an answer" in w for w in second.investigation.warnings)

    async def test_an_answer_with_nothing_pending_is_still_refused_honestly(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """The old fallback is right when it is right: with no question
        outstanding, an answer really is an answer to nothing."""
        engine.llm.respond(
            "classify_turn",
            {"turn_class": "clarification_response", "confidence": 0.9,
             "clarification_question": None},
        )
        opened = await _opened(engine, seed_prior_turn)

        outcome = await _turn(engine, opened, "the second one")

        assert outcome.clarification is not None
        assert outcome.clarification.reason is not None
        assert "no clarification pending" in outcome.clarification.reason


class TestConvergence:
    async def test_the_engine_commits_after_two_consecutive_clarifications(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        engine.llm.respond("classify_turn", _clarifying_classification())
        engine.llm.respond("interpret_question", _INTERPRETATION)

        opened = await _opened(engine, seed_prior_turn)
        first = await _turn(engine, opened, QUESTION)
        second = await _turn(engine, first.session.id, "the first one")
        assert first.clarification is not None
        assert second.clarification is not None  # two is the allowance

        third = await _turn(engine, first.session.id, QUESTION)

        assert third.clarification is None, (
            "after MAX_CONSECUTIVE_CLARIFICATIONS the engine must answer, not ask again"
        )
        assert third.findings

    async def test_the_committed_turn_states_its_assumption_first(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """A committed interpretation that is not stated is a guess. The
        assumption leads the warnings, ahead of the plan's own notes."""
        engine.llm.respond("classify_turn", _clarifying_classification())
        engine.llm.respond("interpret_question", _INTERPRETATION)

        opened = await _opened(engine, seed_prior_turn)
        first = await _turn(engine, opened, QUESTION)
        await _turn(engine, first.session.id, "the first one")
        third = await _turn(engine, first.session.id, QUESTION)

        assert third.warnings
        assert third.warnings[0].startswith("Assumed:")
        assert QUESTION in third.warnings[0]

    async def test_the_allowance_is_what_the_constant_says(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """Pinned so the threshold cannot drift silently: two clarifications
        are a dialogue, a third is the platform talking to itself."""
        assert MAX_CONSECUTIVE_CLARIFICATIONS == 2

        engine.llm.respond("classify_turn", _clarifying_classification())
        engine.llm.respond("interpret_question", _INTERPRETATION)

        session_id: str | None = await _opened(engine, seed_prior_turn)
        outcomes: list[TurnOutcome] = []
        for _ in range(MAX_CONSECUTIVE_CLARIFICATIONS + 1):
            outcome = await _turn(engine, session_id, QUESTION)
            session_id = outcome.session.id
            outcomes.append(outcome)

        clarified = [o for o in outcomes if o.clarification is not None]
        assert len(clarified) == MAX_CONSECUTIVE_CLARIFICATIONS

    async def test_an_answered_turn_resets_the_streak(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """The rule counts *consecutive* clarifications. A turn that lands
        clears the count, so a later ambiguity gets its full allowance
        rather than being committed on immediately."""
        engine.llm.respond("classify_turn", _clarifying_classification(), matcher=_asked(QUESTION))
        engine.llm.respond(
            "classify_turn",
            {"turn_class": "new_investigation", "confidence": 0.95,
             "clarification_question": None},
            matcher=_asked("denial rate please"),
        )
        engine.llm.respond("interpret_question", _INTERPRETATION)

        opened = await _opened(engine, seed_prior_turn)
        first = await _turn(engine, opened, QUESTION)
        answered = await _turn(engine, first.session.id, "denial rate please")
        assert answered.clarification is None  # premise: the streak is broken

        again = await _turn(engine, first.session.id, QUESTION)

        assert again.clarification is not None, "a fresh ambiguity gets a fresh allowance"

    async def test_an_unmappable_question_is_bounded_rather_than_forced(
        self, engine: WiredEngine, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """The rule stops the cycle; it does not manufacture an answer.

        When it is *interpretation* that cannot proceed — the question maps
        onto no governed metric — committing would mean inventing one,
        which is the "confident answer over missing coverage" §2.8 forbids.
        So the ask survives, but past the allowance it stops being a
        variation on the same question and names the impasse instead.
        """
        engine.llm.respond(
            "classify_turn",
            {"turn_class": "new_investigation", "confidence": 0.95,
             "clarification_question": None},
        )
        engine.llm.respond(
            "interpret_question",
            {**_INTERPRETATION, "metric_ids": [], "clarification": "Which metric?"},
        )

        session_id: str | None = await _opened(engine, seed_prior_turn)
        outcomes: list[TurnOutcome] = []
        for _ in range(MAX_CONSECUTIVE_CLARIFICATIONS + 1):
            outcome = await _turn(engine, session_id, QUESTION)
            session_id = outcome.session.id
            outcomes.append(outcome)

        assert all(o.clarification is not None for o in outcomes), "never a guessed answer"
        last = outcomes[-1].clarification
        assert last is not None and last.reason is not None
        assert "CLARIFICATION_NOT_CONVERGING" in last.reason
        assert QUESTION in last.question  # the thread's own starting point, not a new topic
        earlier = outcomes[0].clarification
        assert earlier is not None and last.question != earlier.question
