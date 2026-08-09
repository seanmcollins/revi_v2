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
from revi_adapter_claude.envelope import LlmEnvelope, is_transient
from revi_investigation.application.llm.schemas import RefinementEmissionResponse
from revi_investigation.application.ports import (
    LlmFailureKind,
    StructuredLlmRequest,
    TextLlmRequest,
)
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
    assert result.failure is None
    assert result.usage.model == PIN
    assert result.usage.cost_usd == Decimal("0.0123")
    # Every prompt token, not the uncached remainder: the provider's own
    # `input_tokens` is 7 here and its cache holds the other 2500. Publishing
    # the 7 is what produced live turns reading `input_tokens: 4` beside 953
    # output tokens — see LlmUsage.
    assert result.usage.input_tokens == 2507
    assert result.usage.cache_read_tokens == 2500
    assert result.usage.cache_creation_tokens == 0
    assert result.usage.output_tokens == 431
    assert result.usage.schema_retries == 0
    assert result.usage.duration_ms == 4200


async def test_usage_counts_cache_creation_as_input_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first call of a session WRITES the cache instead of reading it.

    Both buckets are prompt tokens the call actually read, and both are
    billed — the write at a premium. Counting one and not the other would
    make a cold turn look ~free and a warm one expensive.
    """
    _install_query(
        monkeypatch,
        [
            _result(
                usage={
                    "input_tokens": 4,
                    "output_tokens": 953,
                    "cache_creation_input_tokens": 3120,
                    "cache_read_input_tokens": 0,
                }
            )
        ],
    )
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    result = await adapter.structured(_structured_request())

    assert result.usage.input_tokens == 3124
    assert result.usage.cache_creation_tokens == 3120
    assert result.usage.cache_read_tokens == 0
    assert result.usage.output_tokens == 953


async def test_structured_output_none_is_a_result_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spike trap #4: subtype=success with structured_output=None is a schema
    failure the caller treats as clarification — never an exception."""
    _install_query(monkeypatch, [_result(structured_output=None)])
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    result = await adapter.structured(_structured_request())

    assert result.output is None
    # DECLINED, not SCHEMA: the model ran to completion and said nothing.
    # The caller turns that into "rephrase", where a SCHEMA failure would
    # (rightly) have asked for the same question again.
    assert result.failure is LlmFailureKind.DECLINED
    assert result.usage.cost_usd == Decimal("0.0123")


async def test_structured_non_mapping_output_is_treated_as_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_query(monkeypatch, [_result(structured_output=["not", "an", "object"])])
    adapter = ClaudeAgentSdkLanguageModel(PIN)

    result = await adapter.structured(_structured_request())

    assert result.output is None
    # something arrived; it was just not the shape the schema asked for
    assert result.failure is LlmFailureKind.SCHEMA


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
        assert result.failure is LlmFailureKind.SCHEMA
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


# ---------------------------------------------------------------------------
# the operational envelope (review finding D10)
#
# The adapter shipped with no wall-clock bound, no retry, and no bound on
# concurrent CLI subprocesses. These tests pin the policy that replaced that,
# and — as importantly — pin what is NOT retried: a schema failure is the
# model's problem, a budget error is a policy decision, and a 4xx is a request
# problem. Retrying any of them spends money to get the same answer.
#
# Every test fakes the SDK. Nothing here contacts a model.


class _Transient(Exception):
    """Stands in for a connection/spawn failure (matched by MRO name)."""


ConnectError = _Transient  # the classifier matches on the class NAME
_Transient.__name__ = "ConnectError"


class _Status(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


def _fast(**overrides: Any) -> LlmEnvelope:
    """A test envelope: tiny timeout, no real sleeping (the adapter's sleep
    and jitter are injected, so backoff is exercised without waiting)."""
    base: dict[str, Any] = {"timeout_seconds": 5.0, "max_retries": 2, "max_concurrency": 4}
    base.update(overrides)
    return LlmEnvelope(**base)


def _adapter(**kwargs: Any) -> ClaudeAgentSdkLanguageModel:
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    adapter = ClaudeAgentSdkLanguageModel(
        PIN, sleep=fake_sleep, jitter=lambda: 1.0, **kwargs  # jitter=1.0 pins full backoff
    )
    adapter.slept = slept  # type: ignore[attr-defined]
    return adapter


def _install_scripted_query(
    monkeypatch: pytest.MonkeyPatch, script: list[Any]
) -> list[int]:
    """Each entry is either an exception to raise or a ResultMessage to yield;
    entry N serves attempt N. Returns a one-element call counter."""
    calls = [0]

    async def fake_query(
        *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
    ) -> Any:
        index = calls[0]
        calls[0] += 1
        step = script[min(index, len(script) - 1)]
        if isinstance(step, BaseException):
            raise step
        yield step

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    return calls


class TestEnvelopePolicy:
    """The classification, read on its own."""

    def test_connection_failures_are_transient(self) -> None:
        assert is_transient(ConnectError("refused"))
        assert is_transient(ConnectionResetError())
        assert is_transient(claude_agent_sdk.CLIConnectionError("no handshake"))

    def test_a_missing_cli_is_terminal_despite_its_base_class(self) -> None:
        """CLINotFoundError subclasses CLIConnectionError. A naive MRO walk
        would retry a configuration failure three times."""
        assert not is_transient(claude_agent_sdk.CLINotFoundError("no cli"))

    def test_server_side_statuses_are_transient_and_client_side_are_not(self) -> None:
        assert is_transient(_Status(503))
        assert is_transient(_Status(429))
        assert is_transient(_Status(408))
        assert not is_transient(_Status(400))
        assert not is_transient(_Status(401))
        assert not is_transient(_Status(404))
        assert not is_transient(_Status(422))

    def test_an_unrecognized_failure_is_not_retried(self) -> None:
        assert not is_transient(ValueError("nonsense"))

    def test_backoff_is_exponential_and_capped(self) -> None:
        envelope = LlmEnvelope(initial_backoff_seconds=1.0, max_backoff_seconds=4.0)
        assert envelope.backoff_for(1, jitter=1.0) == 1.0
        assert envelope.backoff_for(2, jitter=1.0) == 2.0
        assert envelope.backoff_for(3, jitter=1.0) == 4.0
        assert envelope.backoff_for(9, jitter=1.0) == 4.0  # capped

    def test_full_jitter_spans_zero_to_the_backoff(self) -> None:
        envelope = LlmEnvelope(initial_backoff_seconds=1.0)
        assert envelope.backoff_for(1, jitter=0.0) == 0.0
        assert envelope.backoff_for(1, jitter=0.5) == 0.5

    def test_max_attempts_counts_the_first_try(self) -> None:
        assert LlmEnvelope(max_retries=2).max_attempts == 3
        assert LlmEnvelope(max_retries=0).max_attempts == 1

    def test_invalid_envelopes_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="timeout_seconds"):
            LlmEnvelope(timeout_seconds=0)
        with pytest.raises(ValueError, match="max_retries"):
            LlmEnvelope(max_retries=-1)
        with pytest.raises(ValueError, match="max_concurrency"):
            LlmEnvelope(max_concurrency=0)
        with pytest.raises(ValueError, match="max_backoff_seconds"):
            LlmEnvelope(initial_backoff_seconds=5.0, max_backoff_seconds=1.0)


class TestTimeout:
    async def test_a_hanging_call_is_cut_off_and_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_query(
            *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
        ) -> Any:
            await asyncio.sleep(30)  # never returns inside the envelope
            yield _result()

        monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
        adapter = _adapter(envelope=_fast(timeout_seconds=0.05))

        with pytest.raises(SourceUnavailableError) as excinfo:
            await adapter.structured(_structured_request())

        assert "wall clock" in str(excinfo.value)
        assert excinfo.value.details["timeout_seconds"] == 0.05
        assert PROMPT_MARKER not in str(excinfo.value)

    async def test_the_timeout_closes_the_sdk_stream(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """aclose() is what tears the CLI subprocess down; a generator left to
        the garbage collector leaves the process alive."""
        closed: list[bool] = []

        async def fake_query(
            *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
        ) -> Any:
            try:
                await asyncio.sleep(30)
                yield _result()
            finally:
                closed.append(True)

        monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
        adapter = _adapter(envelope=_fast(timeout_seconds=0.05))

        with pytest.raises(SourceUnavailableError):
            await adapter.structured(_structured_request())

        assert closed == [True]

    async def test_a_timeout_is_never_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Spending the caller's one wall-clock bound twice more would defeat
        the bound."""
        calls = [0]

        async def fake_query(
            *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
        ) -> Any:
            calls[0] += 1
            await asyncio.sleep(30)
            yield _result()

        monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
        adapter = _adapter(envelope=_fast(timeout_seconds=0.05))

        with pytest.raises(SourceUnavailableError):
            await adapter.structured(_structured_request())

        assert calls[0] == 1

    async def test_stream_text_is_bounded_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_query(
            *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
        ) -> Any:
            yield AssistantMessage(content=[TextBlock(text="partial")], model=PIN)
            await asyncio.sleep(30)
            yield _result()

        monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
        adapter = _adapter(envelope=_fast(timeout_seconds=0.05))

        deltas: list[str] = []
        with pytest.raises(SourceUnavailableError) as excinfo:
            async for delta in adapter.stream_text(_text_request()):
                deltas.append(delta)

        assert deltas == ["partial"]
        assert "wall clock" in str(excinfo.value)


class TestRetry:
    async def test_transient_then_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _install_scripted_query(monkeypatch, [ConnectError("refused"), _result()])
        adapter = _adapter(envelope=_fast())

        result = await adapter.structured(_structured_request())

        assert result.output == {"operators": [], "rationale": "canned"}
        assert calls[0] == 2
        assert result.usage.attempts == 2  # the recovery is recorded, not hidden
        assert adapter.slept == [0.5]  # one full-jitter backoff

    async def test_a_clean_call_records_one_attempt(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_scripted_query(monkeypatch, [_result()])
        adapter = _adapter(envelope=_fast())

        result = await adapter.structured(_structured_request())

        assert result.usage.attempts == 1
        assert adapter.slept == []

    async def test_retries_are_bounded_at_two(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _install_scripted_query(monkeypatch, [ConnectError("refused")])
        adapter = _adapter(envelope=_fast())

        with pytest.raises(SourceUnavailableError) as excinfo:
            await adapter.structured(_structured_request())

        assert calls[0] == 3  # first attempt + 2 retries, never more
        assert adapter.slept == [0.5, 1.0]  # exponential
        assert excinfo.value.details["exception_type"] == "ConnectError"

    async def test_backoff_is_skipped_when_it_would_not_fit_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The call fails now with the real error rather than later with a
        timeout that hides it."""
        calls = _install_scripted_query(monkeypatch, [ConnectError("refused")])
        envelope = _fast(
            timeout_seconds=0.2, initial_backoff_seconds=10.0, max_backoff_seconds=10.0
        )
        adapter = _adapter(envelope=envelope)

        with pytest.raises(SourceUnavailableError):
            await adapter.structured(_structured_request())

        assert calls[0] == 1
        assert adapter.slept == []

    async def test_a_non_transient_failure_is_not_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _install_scripted_query(monkeypatch, [ValueError("bad request shape")])
        adapter = _adapter(envelope=_fast())

        with pytest.raises(SourceUnavailableError):
            await adapter.structured(_structured_request())

        assert calls[0] == 1
        assert adapter.slept == []

    async def test_a_4xx_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _install_scripted_query(monkeypatch, [_Status(400)])
        adapter = _adapter(envelope=_fast())

        with pytest.raises(SourceUnavailableError):
            await adapter.structured(_structured_request())

        assert calls[0] == 1

    async def test_a_5xx_is_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _install_scripted_query(monkeypatch, [_Status(503), _result()])
        adapter = _adapter(envelope=_fast())

        assert (await adapter.structured(_structured_request())).usage.attempts == 2
        assert calls[0] == 2

    async def test_a_budget_error_is_never_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Retrying a budget refusal is how a cap becomes a suggestion."""
        calls = [0]

        async def fake_query(
            *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
        ) -> Any:
            calls[0] += 1
            yield _result(subtype="error_max_budget_usd", is_error=True, structured_output=None)
            raise Exception("Claude Code returned an error result: budget")

        monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
        adapter = _adapter(envelope=_fast())

        with pytest.raises(QueryBudgetExceededError):
            await adapter.structured(_structured_request())

        assert calls[0] == 1
        assert adapter.slept == []

    async def test_a_schema_failure_is_not_retried_by_the_adapter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The model saw the prompt and produced the wrong shape. Asking again
        is a transport answer to a model problem — it is counted, not retried."""
        calls = _install_scripted_query(
            monkeypatch, [_result(structured_output=None, num_turns=4)]
        )
        adapter = _adapter(envelope=_fast())

        result = await adapter.structured(_structured_request())

        assert calls[0] == 1
        assert result.output is None
        assert result.usage.schema_retries == 2  # counted…
        assert result.usage.attempts == 1  # …and distinct from transport attempts

    async def test_a_missing_cli_is_not_retried(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _install_scripted_query(
            monkeypatch, [claude_agent_sdk.CLINotFoundError("cli not on PATH")]
        )
        adapter = _adapter(envelope=_fast())

        with pytest.raises(SourceUnavailableError):
            await adapter.structured(_structured_request())

        assert calls[0] == 1

    async def test_stream_text_does_not_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Deltas already handed to the caller cannot be un-yielded."""
        calls = _install_scripted_query(monkeypatch, [ConnectError("refused"), _result()])
        adapter = _adapter(envelope=_fast())

        with pytest.raises(SourceUnavailableError):
            async for _ in adapter.stream_text(_text_request()):
                pass

        assert calls[0] == 1

    async def test_retry_logging_never_echoes_the_prompt(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        _install_scripted_query(monkeypatch, [ConnectError(PROMPT_MARKER), _result()])
        adapter = _adapter(envelope=_fast())

        with caplog.at_level("WARNING"):
            await adapter.structured(_structured_request())

        assert "claude adapter retry" in caplog.text
        assert PROMPT_MARKER not in caplog.text


class TestConcurrencyCap:
    async def test_only_max_concurrency_subprocesses_are_live_at_once(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        live = 0
        peak = 0

        async def fake_query(
            *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
        ) -> Any:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            try:
                await asyncio.sleep(0.02)
                yield _result()
            finally:
                live -= 1

        monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
        adapter = _adapter(envelope=_fast(max_concurrency=2))

        await asyncio.gather(*(adapter.structured(_structured_request()) for _ in range(8)))

        assert peak == 2  # eight turns, never more than two subprocesses

    async def test_the_cap_does_not_serialize_below_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A cap that never lets two calls overlap would be a lock, not a cap."""
        live = 0
        peak = 0

        async def fake_query(
            *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
        ) -> Any:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            try:
                await asyncio.sleep(0.02)
                yield _result()
            finally:
                live -= 1

        monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
        adapter = _adapter(envelope=_fast(max_concurrency=4))

        await asyncio.gather(*(adapter.structured(_structured_request()) for _ in range(4)))

        assert peak == 4

    async def test_streaming_calls_take_a_slot_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        live = 0
        peak = 0

        async def fake_query(
            *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
        ) -> Any:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            try:
                await asyncio.sleep(0.02)
                yield AssistantMessage(content=[TextBlock(text="hi")], model=PIN)
                yield _result(structured_output=None)
            finally:
                live -= 1

        monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
        adapter = _adapter(envelope=_fast(max_concurrency=1))

        async def drain() -> None:
            async for _ in adapter.stream_text(_text_request()):
                pass

        await asyncio.gather(*(drain() for _ in range(3)))

        assert peak == 1


class TestEnvelopeDefaults:
    def test_the_shipped_defaults_are_the_documented_ones(self) -> None:
        adapter = ClaudeAgentSdkLanguageModel(PIN)
        assert adapter.envelope.timeout_seconds == 120.0
        assert adapter.envelope.max_retries == 2
        assert adapter.envelope.max_concurrency == 4
