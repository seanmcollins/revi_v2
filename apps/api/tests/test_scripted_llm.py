"""The scripted demo model's honesty about *why* it has no answer.

Demo mode never guesses — that much was already true. What it now also does
is say which kind of nothing it is returning, so the clarification the
engine builds tells the analyst to rephrase (the script ran out) instead of
blaming plumbing that is not involved.
"""

from __future__ import annotations

from revi_api.scripted_llm import (
    REFERENCE_QUESTIONS,
    ScriptedLanguageModel,
    ScriptEntry,
    demo_language_model,
)
from revi_investigation.application.ports import LlmFailureKind, StructuredLlmRequest


def _request(template_id: str, prompt: str) -> StructuredLlmRequest:
    return StructuredLlmRequest(
        template_id=template_id,
        template_version="v1",
        rendered_prompt=prompt,
        schema={"type": "object", "additionalProperties": False},
    )


async def test_a_matched_entry_answers_and_reports_no_failure() -> None:
    model = demo_language_model()

    result = await model.structured(_request("classify_turn", REFERENCE_QUESTIONS[0]))

    assert result.output is not None
    assert result.output["turn_class"] == "new_investigation"
    assert result.failure is None


async def test_an_unscripted_question_is_off_script_not_a_model_judgement() -> None:
    model = demo_language_model()

    result = await model.structured(_request("classify_turn", "what is the airspeed of a swallow"))

    assert result.output is None
    assert result.failure is LlmFailureKind.OFF_SCRIPT


async def test_a_scripted_non_answer_is_a_declination() -> None:
    """An entry that matches and carries no response is the script saying
    "the model gives nothing here" — different from running off the end."""
    model = ScriptedLanguageModel(entries=[ScriptEntry("classify_turn", None, None)])

    result = await model.structured(_request("classify_turn", "anything at all"))

    assert result.output is None
    assert result.failure is LlmFailureKind.DECLINED
