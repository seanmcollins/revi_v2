"""Production Claude Agent SDK adapter implementing the ``LanguageModelPort``.

Every constraint in this module traces to the M2a spike
(``packages/adapter-claude/spikes/RESULTS.md``, SDK 0.2.132 / bundled CLI 2.1.224):

- ``tools=[]`` is the pure-LLM mode. ``disallowed_tools=["*"]`` would deny the
  CLI's internal ``StructuredOutput`` delivery tool and doom every structured
  call (trap #2); leaving the default tool preset loaded is a 10-40x cost
  multiplier (trap #5).
- ``output_format={"type": "json_schema", "schema": ...}`` delivers the parsed
  object on ``ResultMessage.structured_output``. The schema must be sanitized
  first (the ``discriminator`` keyword Pydantic emits is rejected by the CLI's
  strict JSON-Schema validator, trap #1) — reuse
  :func:`revi_investigation.application.llm.schemas.sanitize_json_schema`.
- Structured output consumes an extra turn: a clean single-shot call reports
  ``num_turns == 2``, so ``max_structured_turns`` defaults to 2 and each
  schema-validation retry burns one additional turn (trap #3). That is the
  retry signal the spike identified — ``schema_retries`` is derived as
  ``max(0, num_turns - 2)`` off the ``ResultMessage``.
- ``subtype == "success"`` with ``structured_output=None`` is a real outcome
  (trap #4). It is surfaced as a *non-exceptional*
  ``StructuredLlmResult(output=None)`` — callers treat it as a clarification.
- On error subtypes ``query()`` yields the ``ResultMessage`` first and then
  raises a bare ``Exception`` (not a ``ClaudeSDKError`` subclass). The adapter
  consumes the message, swallows the post-result raise, and translates from
  the subtype (RESULTS.md §2).

Error translation (provider exceptions never cross the port, design §12):

==============================================  =============================
Provider outcome                                Port outcome
==============================================  =============================
``subtype == "error_max_budget_usd"``           ``QueryBudgetExceededError``
``subtype == "success"``, output present        ``StructuredLlmResult(output=...)``
``subtype == "success"``, output ``None``       ``StructuredLlmResult(output=None)``
schema-failure subtypes (max turns / retries)   ``StructuredLlmResult(output=None)``
any other error subtype                         ``SourceUnavailableError``
exception before any ``ResultMessage``          ``SourceUnavailableError``
exception after the ``ResultMessage``           swallowed; result still used
==============================================  =============================

Messages attached to translated errors are sanitized: they carry the exception
*type* and result subtype only — never prompt text, provider payloads, or keys.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from decimal import Decimal
from typing import Any

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
)

from revi_investigation.application.llm.schemas import sanitize_json_schema
from revi_investigation.application.ports import (
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)
from revi_kernel.errors import QueryBudgetExceededError, SourceUnavailableError

DEFAULT_MAX_BUDGET_USD = Decimal("0.50")

_BUDGET_ERROR_SUBTYPE = "error_max_budget_usd"

# Subtypes that mean "the model could not satisfy the schema" rather than
# "the provider is unavailable". They map to output=None, not an exception
# (spike trap #4: even the harness-level retry exhaustion is a schema failure).
_SCHEMA_FAILURE_SUBTYPES = frozenset({"error_max_turns", "error_max_structured_output_retries"})

# A clean structured call is prompt turn + StructuredOutput delivery turn.
_BASELINE_STRUCTURED_TURNS = 2


class ClaudeAgentSdkLanguageModel:
    """``LanguageModelPort`` implementation on top of ``claude_agent_sdk.query``.

    Stateless per call except for the asyncio-lock-protected last-usage slot
    that backs :meth:`last_usage`; safe for concurrent use.
    """

    def __init__(
        self,
        model_pin: str,
        *,
        max_budget_usd: Decimal = DEFAULT_MAX_BUDGET_USD,
        max_structured_turns: int = 2,
    ) -> None:
        if not model_pin or not model_pin.strip():
            raise ValueError(
                "model_pin must be a non-empty model id (e.g. 'claude-sonnet-5'). "
                "An unpinned call inherits the local login's session default model, "
                "which is unpredictable and can cost ~4x (spike RESULTS.md §4)."
            )
        if max_budget_usd <= 0:
            raise ValueError("max_budget_usd must be positive")
        if max_structured_turns < _BASELINE_STRUCTURED_TURNS:
            raise ValueError(
                "max_structured_turns must be >= 2: structured output consumes an "
                "extra turn, and max_turns=1 fails every call (spike RESULTS.md trap #3)"
            )
        self._model_pin = model_pin.strip()
        self._max_budget_usd = max_budget_usd
        self._max_structured_turns = max_structured_turns
        self._last_usage: LlmUsage | None = None
        self._last_usage_lock = asyncio.Lock()

    # -- LanguageModelPort ---------------------------------------------------

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        sanitized: dict[str, Any] = sanitize_json_schema(dict(request.schema))
        options = self._pure_llm_options(
            system_prompt=request.system_prompt,
            max_turns=self._max_structured_turns,
            output_format={"type": "json_schema", "schema": sanitized},
        )
        result = await self._collect_result(request.rendered_prompt, options)
        result = self._translate_result(result, schema_failure_is_result=True)
        usage = self._usage_from_result(
            result,
            schema_retries=max(0, result.num_turns - _BASELINE_STRUCTURED_TURNS),
        )
        raw = result.structured_output
        output: Mapping[str, Any] | None = raw if isinstance(raw, Mapping) else None
        return StructuredLlmResult(output=output, usage=usage)

    async def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        options = self._pure_llm_options(
            system_prompt=request.system_prompt,
            max_turns=1,
            output_format=None,
        )
        result: ResultMessage | None = None
        try:
            async for message in claude_agent_sdk.query(
                prompt=request.rendered_prompt, options=options
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            yield block.text
                elif isinstance(message, ResultMessage):
                    result = message
        except Exception as exc:
            if result is None:
                raise self._source_unavailable(exc) from exc
            # SDK quirk: on error subtypes query() yields the ResultMessage,
            # then raises a bare Exception. The message is already in hand.
        result = self._translate_result(result, schema_failure_is_result=False)
        usage = self._usage_from_result(result, schema_retries=0)
        async with self._last_usage_lock:
            self._last_usage = usage

    async def last_usage(self) -> LlmUsage | None:
        async with self._last_usage_lock:
            return self._last_usage

    # -- internals -----------------------------------------------------------

    def _pure_llm_options(
        self,
        *,
        system_prompt: str | None,
        max_turns: int,
        output_format: dict[str, Any] | None,
    ) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            tools=[],  # pure-LLM mode; NEVER disallowed_tools=["*"] (kills StructuredOutput)
            allowed_tools=[],
            permission_mode="dontAsk",
            system_prompt=system_prompt,
            model=self._model_pin,
            max_turns=max_turns,
            max_budget_usd=float(self._max_budget_usd),
            output_format=output_format,
        )

    async def _collect_result(
        self, prompt: str, options: ClaudeAgentOptions
    ) -> ResultMessage | None:
        """Drain the query stream, keeping the ``ResultMessage``.

        Broadly catches ``Exception``: on error subtypes the SDK raises a bare
        ``Exception`` *after* yielding the ``ResultMessage``; if the message
        was already seen the raise is swallowed, otherwise it is translated.
        """
        result: ResultMessage | None = None
        try:
            async for message in claude_agent_sdk.query(prompt=prompt, options=options):
                if isinstance(message, ResultMessage):
                    result = message
        except Exception as exc:
            if result is None:
                raise self._source_unavailable(exc) from exc
        return result

    def _translate_result(
        self, result: ResultMessage | None, *, schema_failure_is_result: bool
    ) -> ResultMessage:
        if result is None:
            raise SourceUnavailableError(
                "Claude Agent SDK stream ended without a result message",
                details={"provider": "claude_agent_sdk"},
            )
        if result.subtype == _BUDGET_ERROR_SUBTYPE:
            raise QueryBudgetExceededError(
                "Claude Agent SDK call exceeded its per-call budget cap",
                details={
                    "provider": "claude_agent_sdk",
                    "subtype": result.subtype,
                    "max_budget_usd": float(self._max_budget_usd),
                    "cost_usd": result.total_cost_usd,
                },
            )
        if result.is_error and not (
            schema_failure_is_result and result.subtype in _SCHEMA_FAILURE_SUBTYPES
        ):
            raise SourceUnavailableError(
                f"Claude Agent SDK returned error result subtype '{result.subtype}'",
                details={"provider": "claude_agent_sdk", "subtype": result.subtype},
            )
        return result

    def _usage_from_result(self, result: ResultMessage, *, schema_retries: int) -> LlmUsage:
        usage_map: dict[str, Any] = result.usage or {}
        cost = result.total_cost_usd
        return LlmUsage(
            model=self._model_pin,
            cost_usd=Decimal(str(cost)) if cost is not None else Decimal("0"),
            input_tokens=int(usage_map.get("input_tokens") or 0),
            output_tokens=int(usage_map.get("output_tokens") or 0),
            schema_retries=schema_retries,
            duration_ms=int(result.duration_ms or 0),
        )

    @staticmethod
    def _source_unavailable(exc: Exception) -> SourceUnavailableError:
        # Sanitized on purpose: str(exc) can echo prompt or provider payload
        # content ("Claude Code returned an error result: ..."), which must
        # never cross the port. Only the exception type crosses.
        return SourceUnavailableError(
            f"Claude Agent SDK call failed ({type(exc).__name__})",
            details={"provider": "claude_agent_sdk", "exception_type": type(exc).__name__},
        )
