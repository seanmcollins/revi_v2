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
from revi_investigation.application.ports import (
    LlmFailureKind,
    LlmUsage,
    StructuredLlmRequest,
    TextLlmRequest,
)


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


# ---------------------------------------------------------------------------
# usage accounting
#
# Live turns published `usage.input_tokens: 4` beside `output_tokens: 953`.
# Two independent causes, both here: the provider reports the *uncached*
# prompt remainder as `input_tokens` and the cached bulk separately, and the
# streamed narrative call — the largest generation on most turns — ran after
# the engine wrote the trace the envelope was summed from, so it was counted
# nowhere at all. The scripted model reports usage in the same shape the live
# adapter does, so the summing is exercised without spending anything.


async def test_the_scripted_usage_reports_the_whole_prompt_not_the_uncached_part() -> None:
    model = demo_language_model()

    result = await model.structured(_request("classify_turn", REFERENCE_QUESTIONS[0]))

    usage = result.usage
    assert usage.input_tokens == usage.cache_read_tokens + usage.cache_creation_tokens + 1
    assert usage.cache_read_tokens > 0  # a split that a dropped bucket would expose


async def test_a_streamed_narrative_reports_its_usage_to_the_caller() -> None:
    """The sink is per request, so the tokens land on the turn that spent them.

    ``last_usage()`` is a process-wide slot: two turns narrating at once
    overwrite each other's entry, so a caller reading it back can attribute
    another session's tokens to this one.
    """
    model = demo_language_model()
    seen: list[LlmUsage] = []
    request = TextLlmRequest(
        template_id="compose_narrative",
        template_version="v1",
        rendered_prompt="Certified findings:\n- F1: something (grade direct, confidence high; x=1)\n",
        usage_sink=seen.append,
    )

    chunks = [chunk async for chunk in model.stream_text(request)]

    assert chunks, "premise: the demo narrator composed something"
    [usage] = seen
    assert usage.output_tokens > 0
    assert usage.input_tokens == usage.cache_read_tokens + usage.cache_creation_tokens + 1


async def test_a_narrative_without_a_sink_still_streams() -> None:
    """The sink is optional: a caller that does not want usage still narrates."""
    model = demo_language_model()
    request = TextLlmRequest(
        template_id="compose_narrative",
        template_version="v1",
        rendered_prompt="Certified findings:\n- F1: something (grade direct, confidence high; x=1)\n",
    )

    chunks = [chunk async for chunk in model.stream_text(request)]

    assert chunks
