"""Unit tests for the Claude Agent SDK adapter with the SDK faked at the
module boundary (``claude_agent_sdk.query`` monkeypatched with scripted
async generators). Covers every spike-documented trap without network access."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import claude_agent_sdk
import pytest
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, SystemMessage, TextBlock

from revi_adapter_claude.adapter import ClaudeAgentSdkLanguageModel
from revi_investigation.application.llm.schemas import RefinementEmissionResponse
from revi_investigation.application.ports import StructuredLlmRequest, TextLlmRequest
from revi_kernel.errors import QueryBudgetExceededError, SourceUnavailableError

PIN = "claude-test-pin"
PROMPT_MARKER = "SECRET-PROMPT-CONTENT"

# -- scripted SDK ------------------------------------------------------------


def _result(**overrides: Any) -> ResultMessage:
    base: dict[str, Any] = {
        "subtype": "success",
        "duration_ms": 4200,
        "duration_api_ms": 3900,
        "is_error": False,
        "num_turns": 2,
        "session_id": "sess-1",
        "total_cost_usd": 0.0123,
        "usage": {"input_tokens": 7, "output_tokens": 431, "cache_read_input_tokens": 2500},
        "structured_output": {"operators": [], "rationale": "canned"},
    }
    base.update(overrides)
    return ResultMessage(**base)


def _init_message() -> SystemMessage:
    return SystemMessage(subtype="init", data={"model": PIN})


def _install_query(
    monkeypatch: pytest.MonkeyPatch,
    messages: list[Any],
    *,
    raise_after: Exception | None = None,
    captured: list[tuple[str, ClaudeAgentOptions | None]] | None = None,
) -> None:
    async def fake_query(
        *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
    ) -> Any:
        if captured is not None:
            captured.append((prompt, options))
        for message in messages:
            yield message
        if raise_after is not None:
            raise raise_after

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)


def _structured_request(
    schema: dict[str, Any] | None = None, system_prompt: str | None = "compiler system prompt"
) -> StructuredLlmRequest:
    return StructuredLlmRequest(
        template_id="emit_refinements",
        template_version="v1",
        rendered_prompt=PROMPT_MARKER,
        schema=schema or {"type": "object", "properties": {}, "additionalProperties": False},
        system_prompt=system_prompt,
    )


def _text_request() -> TextLlmRequest:
    return TextLlmRequest(
        template_id="compose_narrative",
        template_version="v1",
        rendered_prompt=PROMPT_MARKER,
        system_prompt="narrative system prompt",
    )


def _assert_no_discriminator(node: Any) -> None:
    if isinstance(node, dict):
        assert "discriminator" not in node
        for value in node.values():
            _assert_no_discriminator(value)
    elif isinstance(node, list):
        for item in node:
            _assert_no_discriminator(item)


# -- structured: result shaping ----------------------------------------------


async def test_structured_success(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_query(monkeypatch, [_init_message(), _result()])
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    result = await adapter.structured(_structured_request())

    assert result.output == {"operators": [], "rationale": "canned"}
    assert result.usage.model == PIN
    assert result.usage.cost_usd == Decimal("0.0123")
    assert result.usage.input_tokens == 7
    assert result.usage.output_tokens == 431
    assert result.usage.schema_retries == 0
    assert result.usage.duration_ms == 4200


async def test_structured_output_none_is_a_result_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spike trap #4: subtype=success with structured_output=None is a schema
    failure the caller treats as clarification — never an exception."""
    _install_query(monkeypatch, [_result(structured_output=None)])
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    result = await adapter.structured(_structured_request())

    assert result.output is None
    assert result.usage.cost_usd == Decimal("0.0123")


async def test_structured_non_mapping_output_is_treated_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_query(monkeypatch, [_result(structured_output=["not", "an", "object"])])
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    result = await adapter.structured(_structured_request())

    assert result.output is None


async def test_budget_error_subtype_raises_budget_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_query(
        monkeypatch,
        [_result(subtype="error_max_budget_usd", is_error=True, structured_output=None)],
        raise_after=Exception("Claude Code returned an error result: " + PROMPT_MARKER),
    )
    adapter = ClaudeAgentSdkLanguageModel(PIN, max_budget_usd=Decimal("0.05"))

    with pytest.raises(QueryBudgetExceededError) as excinfo:
        await adapter.structured(_structured_request())

    assert excinfo.value.details["subtype"] == "error_max_budget_usd"
    assert excinfo.value.details["max_budget_usd"] == 0.05
    assert PROMPT_MARKER not in str(excinfo.value)


async def test_bare_exception_after_result_message_still_returns_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SDK quirk: query() yields the ResultMessage, then raises a bare
    Exception. The adapter must keep the already-yielded result."""
    _install_query(
        monkeypatch,
        [_result()],
        raise_after=Exception("Claude Code returned an error result: boom"),
    )
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    result = await adapter.structured(_structured_request())

    assert result.output == {"operators": [], "rationale": "canned"}


async def test_schema_failure_subtypes_return_output_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Harness-level retry exhaustion is a schema failure, not provider loss."""
    for subtype in ("error_max_structured_output_retries", "error_max_turns"):
        _install_query(
            monkeypatch,
            [_result(subtype=subtype, is_error=True, structured_output=None, num_turns=7)],
            raise_after=Exception("Claude Code returned an error result: retries"),
        )
        adapter = ClaudeAgentSdkLanguageModel(PIN)

        result = await adapter.structured(_structured_request())

        assert result.output is None
        assert result.usage.schema_retries == 5  # num_turns=7 minus the 2-turn baseline


async def test_unknown_error_subtype_raises_source_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_query(
        monkeypatch,
        [_result(subtype="error_during_execution", is_error=True, structured_output=None)],
        raise_after=Exception("Claude Code returned an error result: exec"),
    )
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    with pytest.raises(SourceUnavailableError) as excinfo:
        await adapter.structured(_structured_request())

    assert excinfo.value.details["subtype"] == "error_during_execution"


async def test_exception_before_any_result_is_translated_and_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_query(
        monkeypatch,
        [],
        raise_after=RuntimeError(f"transport blew up while sending {PROMPT_MARKER} sk-ant-key"),
    )
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    with pytest.raises(SourceUnavailableError) as excinfo:
        await adapter.structured(_structured_request())

    message = str(excinfo.value)
    assert PROMPT_MARKER not in message
    assert "sk-ant" not in message
    assert excinfo.value.details["exception_type"] == "RuntimeError"
    assert PROMPT_MARKER not in str(excinfo.value.details)


async def test_stream_ending_without_result_message_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_query(monkeypatch, [_init_message()])
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    with pytest.raises(SourceUnavailableError):
        await adapter.structured(_structured_request())


# -- structured: option assembly and retry counting ---------------------------


async def test_option_assembly_pure_llm_mode_with_sanitized_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[str, ClaudeAgentOptions | None]] = []
    _install_query(monkeypatch, [_result()], captured=captured)
    adapter = ClaudeAgentSdkLanguageModel(
        PIN, max_budget_usd=Decimal("0.25"), max_structured_turns=3
    )

    raw_schema = RefinementEmissionResponse.model_json_schema()
    assert "discriminator" in str(raw_schema)  # premise: pydantic emits the keyword
    await adapter.structured(_structured_request(schema=raw_schema))

    prompt, options = captured[0]
    assert prompt == PROMPT_MARKER
    assert options is not None
    assert options.tools == []  # pure-LLM mode — NOT disallowed_tools=["*"]
    assert options.allowed_tools == []
    assert options.disallowed_tools == []
    assert options.permission_mode == "dontAsk"
    assert options.model == PIN
    assert options.max_turns == 3
    assert options.max_budget_usd == 0.25
    assert options.system_prompt == "compiler system prompt"
    assert options.output_format is not None
    assert options.output_format["type"] == "json_schema"
    _assert_no_discriminator(options.output_format["schema"])


async def test_schema_retries_counted_from_extra_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """The spike's retry signal: a clean structured call is num_turns == 2;
    each schema-validation retry burns one extra turn."""
    _install_query(monkeypatch, [_result(num_turns=4)])
    adapter = ClaudeAgentSdkLanguageModel(PIN, max_structured_turns=6)

    result = await adapter.structured(_structured_request())

    assert result.usage.schema_retries == 2


# -- streaming ----------------------------------------------------------------


async def test_stream_text_yields_deltas_and_records_last_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_query(
        monkeypatch,
        [
            _init_message(),
            AssistantMessage(content=[TextBlock(text="Hello "), TextBlock(text="wor")], model=PIN),
            AssistantMessage(content=[TextBlock(text="ld")], model=PIN),
            _result(structured_output=None, usage={"input_tokens": 3, "output_tokens": 9}),
        ],
    )
    adapter = ClaudeAgentSdkLanguageModel(PIN)
    assert await adapter.last_usage() is None

    deltas = [delta async for delta in adapter.stream_text(_text_request())]

    assert deltas == ["Hello ", "wor", "ld"]
    usage = await adapter.last_usage()
    assert usage is not None
    assert usage.model == PIN
    assert usage.input_tokens == 3
    assert usage.output_tokens == 9
    assert usage.schema_retries == 0
    assert usage.cost_usd == Decimal("0.0123")


async def test_stream_text_options_have_no_output_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[tuple[str, ClaudeAgentOptions | None]] = []
    _install_query(monkeypatch, [_result(structured_output=None)], captured=captured)
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    async for _ in adapter.stream_text(_text_request()):
        pass

    _, options = captured[0]
    assert options is not None
    assert options.output_format is None
    assert options.tools == []
    assert options.allowed_tools == []
    assert options.permission_mode == "dontAsk"
    assert options.model == PIN
    assert options.max_turns == 1
    assert options.system_prompt == "narrative system prompt"


async def test_stream_text_budget_error_raises_and_keeps_last_usage_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_query(
        monkeypatch,
        [
            AssistantMessage(content=[TextBlock(text="partial")], model=PIN),
            _result(subtype="error_max_budget_usd", is_error=True, structured_output=None),
        ],
        raise_after=Exception("Claude Code returned an error result: budget"),
    )
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    with pytest.raises(QueryBudgetExceededError):
        async for _ in adapter.stream_text(_text_request()):
            pass

    assert await adapter.last_usage() is None


async def test_stream_text_error_subtype_raises_source_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Text mode has no schema-failure escape hatch: any non-budget error
    subtype means the narrative call failed."""
    _install_query(
        monkeypatch,
        [_result(subtype="error_max_turns", is_error=True, structured_output=None)],
        raise_after=Exception("Claude Code returned an error result: turns"),
    )
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    with pytest.raises(SourceUnavailableError):
        async for _ in adapter.stream_text(_text_request()):
            pass


# -- concurrency ---------------------------------------------------------------


async def test_concurrent_structured_calls_do_not_share_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_query(
        *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
    ) -> Any:
        await asyncio.sleep(0.001 * len(prompt))  # deliberately interleave completions
        yield _result(structured_output={"echo": prompt}, total_cost_usd=float(len(prompt)))

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    prompts = [f"prompt-{'x' * n}" for n in (5, 1, 3, 7, 2)]
    results = await asyncio.gather(
        *(
            adapter.structured(
                StructuredLlmRequest(
                    template_id="t",
                    template_version="v",
                    rendered_prompt=prompt,
                    schema={"type": "object", "additionalProperties": False},
                )
            )
            for prompt in prompts
        )
    )

    for prompt, result in zip(prompts, results, strict=True):
        assert result.output == {"echo": prompt}
        assert result.usage.cost_usd == Decimal(str(float(len(prompt))))


# -- construction --------------------------------------------------------------


def test_constructor_rejects_empty_model_pin() -> None:
    with pytest.raises(ValueError, match="model_pin"):
        ClaudeAgentSdkLanguageModel("")
    with pytest.raises(ValueError, match="model_pin"):
        ClaudeAgentSdkLanguageModel("   ")


def test_constructor_rejects_bad_budget_and_turns() -> None:
    with pytest.raises(ValueError, match="max_budget_usd"):
        ClaudeAgentSdkLanguageModel(PIN, max_budget_usd=Decimal("0"))
    with pytest.raises(ValueError, match="max_structured_turns"):
        ClaudeAgentSdkLanguageModel(PIN, max_structured_turns=1)
