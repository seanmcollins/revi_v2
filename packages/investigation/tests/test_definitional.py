"""The DEFINITIONAL zero-probe path and clarification outcomes, end to end
through ``SubmitTurnService`` on the real pack with a stub repository."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pytest

from revi_investigation.application.submit_turn import SubmitTurnRequest
from revi_investigation.domain.records import InvestigationStatus
from revi_investigation.domain.turns import TurnClass
from revi_kernel.errors import DateBasisInvalidError, UnsupportedConceptError
from revi_kernel.watermark import DataWatermark
from revi_testing.engine_wiring import WiredEngine, build_engine
from revi_testing.fakes import StubAnalyticalRepository
from revi_testing.mock_llm import MockLanguageModel

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
        llm = MockLanguageModel()
        _classify(llm, "definitional")
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
        # trace persisted with the definitional payload and no probes
        trace = await engine.trace_store.get(outcome.trace_id)
        assert trace is not None
        assert trace.payload["definitional"]["terms"]
        assert trace.payload["probes"] == []

    async def test_definitional_terms_from_interpretation_also_zero_probes(self) -> None:
        llm = MockLanguageModel()
        _classify(llm, "new_investigation")
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
        llm = MockLanguageModel()
        _classify(llm, "definitional")
        engine = _engine(llm)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="what is flurbotron")
        )
        assert outcome.definitional is None
        assert outcome.clarification is not None
        assert outcome.investigation.status is InvestigationStatus.CLARIFICATION_REQUIRED
        assert engine.repository.execute_count == 0


class TestClarificationOutcomes:
    async def test_structured_output_none_never_guesses(self) -> None:
        llm = MockLanguageModel()
        llm.respond("classify_turn", None)  # model failed the schema
        engine = _engine(llm)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="mumble mumble")
        )
        assert outcome.clarification is not None
        assert outcome.investigation.status is InvestigationStatus.CLARIFICATION_REQUIRED
        assert engine.repository.execute_count == 0

    async def test_low_confidence_classification_clarifies(self) -> None:
        llm = MockLanguageModel()
        _classify(llm, "new_investigation", confidence=0.2)
        engine = _engine(llm)
        outcome = await engine.submit.submit(SubmitTurnRequest(tenant="demo", question="hm?"))
        assert outcome.clarification is not None
        assert engine.repository.execute_count == 0

    async def test_interpretation_clarification_is_an_outcome(self) -> None:
        llm = MockLanguageModel()
        _classify(llm, "new_investigation")
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

    async def test_follow_up_class_on_first_turn_clarifies(self) -> None:
        llm = MockLanguageModel()
        _classify(llm, "refinement")
        engine = _engine(llm)
        outcome = await engine.submit.submit(
            SubmitTurnRequest(tenant="demo", question="drill into F1")
        )
        assert outcome.clarification is not None
        assert engine.repository.execute_count == 0


class TestInterpretationValidation:
    async def test_unknown_metric_id_is_unsupported_concept(self) -> None:
        llm = MockLanguageModel()
        _classify(llm, "new_investigation")
        llm.respond("interpret_question", _interpretation(metric_ids=["made_up_metric"]))
        engine = _engine(llm)
        with pytest.raises(UnsupportedConceptError):
            await engine.submit.submit(SubmitTurnRequest(tenant="demo", question="made up?"))

    async def test_unknown_playbook_id_is_unsupported_concept(self) -> None:
        llm = MockLanguageModel()
        _classify(llm, "new_investigation")
        llm.respond("interpret_question", _interpretation(playbook_id="made_up_playbook"))
        engine = _engine(llm)
        with pytest.raises(UnsupportedConceptError):
            await engine.submit.submit(SubmitTurnRequest(tenant="demo", question="run it"))

    async def test_illegal_basis_is_date_basis_invalid(self) -> None:
        llm = MockLanguageModel()
        _classify(llm, "new_investigation")
        llm.respond(
            "interpret_question",
            _interpretation(metric_ids=["cash_posted"], basis="discharge"),
        )
        engine = _engine(llm)
        with pytest.raises(DateBasisInvalidError):
            await engine.submit.submit(
                SubmitTurnRequest(tenant="demo", question="cash by discharge date")
            )

    async def test_prompts_carry_vocabulary_not_data(self) -> None:
        llm = MockLanguageModel()
        _classify(llm, "new_investigation")
        llm.respond("interpret_question", _interpretation(clarification="which?"))
        engine = _engine(llm)
        await engine.submit.submit(SubmitTurnRequest(tenant="demo", question="numbers please"))
        [interpret_call] = llm.calls_for("interpret_question")
        prompt = interpret_call.rendered_prompt
        assert "- cash_posted:" in prompt  # metric vocabulary present
        assert "- payer:" in prompt  # dimension vocabulary present
        assert "- cash_decline:" in prompt  # playbook vocabulary present
