"""The DEFINITIONAL zero-probe path and clarification outcomes, end to end
through ``SubmitTurnService`` on the real pack with a stub repository."""

from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING, Any

import pytest

from revi_investigation.application.submit_turn import SubmitTurnRequest
from revi_investigation.domain.records import InvestigationStatus
from revi_investigation.domain.turns import TurnClass
from revi_kernel.errors import DateBasisInvalidError, UnsupportedConceptError
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import WiredEngine, build_engine
from revi_testing.fakes import StubAnalyticalRepository
from revi_testing.mock_llm import MockLanguageModel

if TYPE_CHECKING:
    from conftest import SeedPriorTurn

WATERMARK = DataWatermark(
    id="wm_test", loaded_at=datetime(2026, 8, 3, 4, 10), newest_data_date=date(2026, 8, 2)
)


def _engine(llm: MockLanguageModel) -> WiredEngine:
    return build_engine(
        repository=StubAnalyticalRepository(watermarks=(WATERMARK,)), llm=llm
    )


def _classify(llm: MockLanguageModel, turn_class: str, confidence: float = 0.95) -> None:
    llm.respond(
        "classify_turn",
        {"turn_class": turn_class, "confidence": confidence, "clarification_question": None},
    )


def _interpretation(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "intent_summary": "test",
        "metric_ids": [],
        "dimension_ids": [],
        "concept_ids": [],
        "playbook_id": None,
        "window": None,
        "basis": None,
        "comparison": None,
        "scope": [],
        "clarification": None,
        "definitional_terms": [],
    }
    base.update(overrides)
    return base


class TestDefinitionalPath:
    async def test_what_is_pr3_answers_from_pack_with_zero_probes(self) -> None:
        """A first-turn definitional question, classified by construction.

        Zero model calls, not one: a governed lead-in over a term the pack
        resolves whole is a definitional question by lookup, and a lookup
        against governed content does not need a model to confirm it."""
        llm = MockLanguageModel()
        engine = _engine(llm)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="what is pr3")
        )
        assert outcome.clarification is None
        answer = outcome.definitional
        assert answer is not None
        kinds = {(term.kind, term.term) for term in answer.terms}
        assert ("code:group_code", "PR") in kinds
        assert ("code:carc", "3") in kinds
        # provenance: governed pack content, pinned version
        assert answer.pack_id == "base-rcm" and answer.pack_version == "1.0.0"
        assert answer.pack_snapshot_id == engine.pack_port.snapshot_id
        # THE zero-probe assertion: no warehouse work at all
        assert engine.repository.execute_count == 0
        assert outcome.investigation.status is InvestigationStatus.COMPLETE
        assert outcome.investigation.turn_class is TurnClass.DEFINITIONAL
        assert llm.structured_calls == [], "a pack lookup needs no model call"
        # trace persisted with the definitional payload and no probes
        trace = await engine.trace_store.get(outcome.trace_id)
        assert trace is not None
        assert trace.payload["definitional"]["terms"]
        assert trace.payload["probes"] == []

    async def test_definitional_terms_from_interpretation_also_zero_probes(self) -> None:
        llm = MockLanguageModel()
        llm.respond(
            "interpret_question", _interpretation(definitional_terms=["denial rate"])
        )
        engine = _engine(llm)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="how do you define denial rate?")
        )
        assert outcome.definitional is not None
        assert any(term.kind == "metric" for term in outcome.definitional.terms)
        assert engine.repository.execute_count == 0

    async def test_unknown_term_becomes_clarification(self) -> None:
        """A lead-in over a term the pack does not know is NOT definitional
        by construction — it falls through to interpretation, which is
        where a question that names nothing governed belongs."""
        llm = MockLanguageModel()
        llm.respond("interpret_question", _interpretation(definitional_terms=["flurbotron"]))
        engine = _engine(llm)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="what is flurbotron")
        )
        assert outcome.definitional is None
        assert outcome.clarification is not None
        assert outcome.investigation.status is InvestigationStatus.CLARIFICATION_REQUIRED
        assert engine.repository.execute_count == 0


class TestClarificationOutcomes:
    async def test_structured_output_none_never_guesses(
        self, seed_prior_turn: SeedPriorTurn
    ) -> None:
        llm = MockLanguageModel()
        llm.respond("classify_turn", None)  # model failed the schema
        engine = _engine(llm)
        session_id = await seed_prior_turn(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="mumble mumble", session_id=session_id)
        )
        assert outcome.clarification is not None
        assert outcome.investigation.status is InvestigationStatus.CLARIFICATION_REQUIRED
        assert engine.repository.execute_count == 0

    async def test_low_confidence_classification_clarifies(
        self, seed_prior_turn: SeedPriorTurn
    ) -> None:
        llm = MockLanguageModel()
        _classify(llm, "new_investigation", confidence=0.2)
        engine = _engine(llm)
        session_id = await seed_prior_turn(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="hm?", session_id=session_id)
        )
        assert outcome.clarification is not None
        assert engine.repository.execute_count == 0

    async def test_interpretation_clarification_is_an_outcome(self) -> None:
        llm = MockLanguageModel()
        llm.respond(
            "interpret_question",
            _interpretation(clarification="Which metric did you mean?"),
        )
        engine = _engine(llm)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="how are the numbers")
        )
        assert outcome.clarification is not None
        assert outcome.clarification.question == "Which metric did you mean?"
        clarification_events = [e for e in engine.event_bus.events if e.kind == "clarification"]
        assert clarification_events

    async def test_follow_up_class_on_a_session_with_no_answer_clarifies(
        self, seed_prior_turn: SeedPriorTurn
    ) -> None:
        """A refinement needs something to refine. The session has a turn
        (so classification runs) but no analytical answer, and the handle
        F1 was never published — both are said, neither is guessed."""
        llm = MockLanguageModel()
        _classify(llm, "refinement")
        engine = _engine(llm)
        session_id = await seed_prior_turn(engine)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="drill into F1", session_id=session_id)
        )
        assert outcome.clarification is not None
        assert engine.repository.execute_count == 0

    async def test_the_first_utterance_is_never_classified_by_a_model(self) -> None:
        """F11: a session with nothing behind it can only be starting one.

        Zero classification calls, and the taxonomy branch it would have
        picked cannot be wrong, because nothing picked it."""
        llm = MockLanguageModel()
        llm.respond("interpret_question", _interpretation(clarification="which metric?"))
        engine = _engine(llm)
        await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="how are we doing")
        )
        assert llm.calls_for("classify_turn") == ()


class TestInterpretationValidation:
    async def test_unknown_metric_id_is_unsupported_concept(self) -> None:
        llm = MockLanguageModel()
        llm.respond("interpret_question", _interpretation(metric_ids=["made_up_metric"]))
        engine = _engine(llm)
        with pytest.raises(UnsupportedConceptError):
            await engine.submit.submit(SubmitTurnRequest(tenant="demo", question="made up?"))

    async def test_unknown_playbook_id_is_unsupported_concept(self) -> None:
        llm = MockLanguageModel()
        llm.respond("interpret_question", _interpretation(playbook_id="made_up_playbook"))
        engine = _engine(llm)
        with pytest.raises(UnsupportedConceptError):
            await engine.submit.submit(SubmitTurnRequest(tenant="demo", question="run it"))

    async def test_illegal_basis_clarifies_with_bases_that_do_work(self) -> None:
        """Round-3 FN-8: ``DATE_BASIS_INVALID`` was the last dead end.

        It used to reach the analyst as a §12 banner whose copy recommended
        "service, submission or posting date" beside "(allowed: ['service',
        'submission'])" — a refusal that contradicted itself and offered no
        options. It now routes through the same ``clarification_for``
        machinery ``GRAIN_INCOMPATIBLE`` does, and every option names a
        basis the contract allows AND this warehouse binds.
        """
        llm = MockLanguageModel()
        llm.respond(
            "interpret_question",
            _interpretation(metric_ids=["cash_posted"], basis="discharge"),
        )
        engine = _engine(llm)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="cash by discharge date")
        )
        assert outcome.clarification is not None
        clarification = outcome.clarification
        assert "DATE_BASIS_INVALID_RECOVERABLE" in clarification.reason
        assert clarification.options, "a basis refusal must offer a basis that works"
        contract = engine.pack_port.metric("cash_posted")
        assert contract is not None
        allowed = {basis.id for basis in contract.allowed_date_bases}
        for option in clarification.options:
            named = option.removeprefix("Use the ").removesuffix(" date basis")
            assert named in allowed, option
            assert named != "discharge"

    async def test_a_basis_no_alternative_can_rescue_still_refuses(self) -> None:
        """The honesty half: no invented way out.

        ``resolve_answerable_basis`` still raises for a basis the contract
        forbids — the clarification is a *recovery* over that refusal, not a
        replacement for it, and a metric with nothing to offer must not be
        handed a menu of things that do not work.
        """
        from revi_investigation.application.date_basis import resolve_answerable_basis
        from revi_kernel.refs import DISCHARGE

        engine = _engine(MockLanguageModel())
        contract = engine.pack_port.metric("cash_posted")
        assert contract is not None
        with pytest.raises(DateBasisInvalidError):
            resolve_answerable_basis(contract, DISCHARGE, engine.catalog)

    async def test_prompts_carry_vocabulary_not_data(self) -> None:
        llm = MockLanguageModel()
        llm.respond("interpret_question", _interpretation(clarification="which?"))
        engine = _engine(llm)
        await engine.submit.submit(SubmitTurnRequest(tenant="demo", question="numbers please"))
        [interpret_call] = llm.calls_for("interpret_question")
        prompt = interpret_call.rendered_prompt
        assert "- cash_posted:" in prompt  # metric vocabulary present
        assert "- payer:" in prompt  # dimension vocabulary present
        assert "- cash_decline:" in prompt  # playbook vocabulary present
