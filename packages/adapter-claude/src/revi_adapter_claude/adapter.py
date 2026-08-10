"""Production Claude Agent SDK adapter implementing the ``LanguageModelPort``.

Every constraint in this module traces to the SDK spike
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
  Which *kind* of empty-handed it is rides along on ``failure``: a model that
  ran and delivered nothing is ``DECLINED``, a shape that never validated is
  ``SCHEMA``. The two read identically on the wire and want opposite advice
  from the analyst ("rephrase" vs "try that again"), so the port names them.
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
``subtype == "success"``, output ``None``       ``output=None, failure=DECLINED``
``subtype == "success"``, output not an object  ``output=None, failure=SCHEMA``
schema-failure subtypes (max turns / retries)   ``output=None, failure=SCHEMA``
any other error subtype                         ``SourceUnavailableError``
exception before any ``ResultMessage``          ``SourceUnavailableError``
exception after the ``ResultMessage``           swallowed; result still used
==============================================  =============================

Messages attached to translated errors are sanitized: they carry the exception
*type* and result subtype only — never prompt text, provider payloads, or keys.

**Operational envelope.** Every call runs inside a wall-clock deadline, a
bounded retry for transient transport failures only, and a concurrency cap
on live SDK subprocesses. The policy lives in
:mod:`revi_adapter_claude.envelope`; this module applies it:

- The deadline is set once per port call and *shared* by every attempt, so a
  retrying call can never outlast a non-retrying one. Exceeding it is a
  terminal ``SourceUnavailableError`` — never a retry.
- Cancellation is clean: the ``query()`` async generator is always closed via
  ``aclose()``, which is what tears the CLI subprocess down. Letting the
  generator fall to the garbage collector would leave the process alive.
- ``structured`` retries; ``stream_text`` does not. Deltas already handed to
  the caller cannot be un-yielded, so a mid-stream failure is reported rather
  than silently restarted with a duplicated prefix.
- Attempts are counted onto ``LlmUsage.attempts`` and surface in the turn
  trace, so a degrading provider is visible in the same place as its cost.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from decimal import Decimal
from typing import Any

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
)

from revi_adapter_claude.envelope import LlmEnvelope, default_jitter, is_transient
from revi_investigation.application.llm.schemas import sanitize_json_schema
from revi_investigation.application.ports import (
    DEFAULT_LLM_CALL_POLICY,
    LlmCallPolicy,
    LlmFailureKind,
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)
from revi_kernel.errors import QueryBudgetExceededError, SourceUnavailableError

logger = logging.getLogger("revi.adapter.claude")

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

    **Per-call policy.** A request may carry a :class:`LlmCallPolicy` — the
    session's model tier and what is left of its turn budget. The tier
    replaces the process pin for that call (never unpins it), the budget
    can only *tighten* the deployment's own cap, and the model actually
    used is what lands on ``LlmUsage.model``. Both values are bounded by
    the API layer before they arrive: this adapter applies a decision, it
    does not make one.
    """

    #: This adapter really does vary by ``LlmCallPolicy.model``. Read by
    #: the composition root so ``/v1/capabilities`` can tell a client
    #: whether a tier control would change anything at all.
    applies_call_policy = True

    def __init__(
        self,
        model_pin: str,
        *,
        max_budget_usd: Decimal = DEFAULT_MAX_BUDGET_USD,
        max_structured_turns: int = 2,
        envelope: LlmEnvelope | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[], float] | None = None,
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
        self._envelope = envelope if envelope is not None else LlmEnvelope()
        # Injected so tests pin the backoff instead of sleeping through it.
        self._sleep = sleep if sleep is not None else asyncio.sleep
        self._jitter = jitter if jitter is not None else default_jitter
        self._semaphore = asyncio.Semaphore(self._envelope.max_concurrency)
        self._last_usage: LlmUsage | None = None
        self._last_usage_lock = asyncio.Lock()

    @property
    def envelope(self) -> LlmEnvelope:
        return self._envelope

    # -- LanguageModelPort ---------------------------------------------------

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        sanitized: dict[str, Any] = sanitize_json_schema(dict(request.schema))
        options = self._pure_llm_options(
            system_prompt=request.system_prompt,
            max_turns=self._max_structured_turns,
            output_format={"type": "json_schema", "schema": sanitized},
            policy=request.policy,
        )
        raw_result, attempts = await self._collect_within_envelope(request.rendered_prompt, options)
        result = self._translate_result(raw_result, schema_failure_is_result=True)
        usage = self._usage_from_result(
            result,
            schema_retries=max(0, result.num_turns - _BASELINE_STRUCTURED_TURNS),
            attempts=attempts,
            model=self._model_for(request.policy),
        )
        raw = result.structured_output
        output: Mapping[str, Any] | None = raw if isinstance(raw, Mapping) else None
        return StructuredLlmResult(
            output=output, usage=usage, failure=self._failure_kind(result, output)
        )

    async def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        """Narrative streaming: timeout and concurrency cap, but **no retry**.

        A delta already handed to the caller cannot be un-yielded, so a
        mid-stream failure is reported rather than restarted with a duplicated
        prefix. ``attempts`` is therefore always 1 on this path, and says so.
        """
        options = self._pure_llm_options(
            system_prompt=request.system_prompt,
            max_turns=1,
            output_format=None,
            policy=request.policy,
        )
        deadline = time.monotonic() + self._envelope.timeout_seconds
        result: ResultMessage | None = None
        async with self._semaphore:
            stream = claude_agent_sdk.query(prompt=request.rendered_prompt, options=options)
            try:
                while True:
                    try:
                        message = await asyncio.wait_for(
                            stream.__anext__(), self._remaining(deadline)
                        )
                    except StopAsyncIteration:
                        break
                    except TimeoutError as exc:
                        raise self._timed_out() from exc
                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock) and block.text:
                                yield block.text
                    elif isinstance(message, ResultMessage):
                        result = message
            except SourceUnavailableError:
                raise  # our own translation (the deadline); never re-translate
            except Exception as exc:
                if result is None:
                    raise self._source_unavailable(exc) from exc
                # SDK quirk: on error subtypes query() yields the ResultMessage,
                # then raises a bare Exception. The message is already in hand.
            finally:
                await self._aclose(stream)
        result = self._translate_result(result, schema_failure_is_result=False)
        usage = self._usage_from_result(
            result, schema_retries=0, attempts=1, model=self._model_for(request.policy)
        )
        # Per-request first: ``last_usage`` is a process-wide slot two
        # concurrent narrations overwrite, so a caller that needs *this*
        # call's tokens gets them handed over directly.
        if request.usage_sink is not None:
            request.usage_sink(usage)
        async with self._last_usage_lock:
            self._last_usage = usage

    async def last_usage(self) -> LlmUsage | None:
        async with self._last_usage_lock:
            return self._last_usage

    # -- internals -----------------------------------------------------------

    def _model_for(self, policy: LlmCallPolicy) -> str:
        """The model this call runs on: the session's tier, or the pin.

        The tier arrives already checked against the deployment allowlist —
        this adapter applies a decision, it does not make one. An empty or
        whitespace tier is treated as absent rather than as an unpinned
        call, because unpinned is the one thing construction refuses.
        """
        tier = (policy.model or "").strip()
        return tier or self._model_pin

    def _budget_for(self, policy: LlmCallPolicy) -> Decimal:
        """The per-call cap: the tighter of the deployment's and the turn's.

        ``min`` on purpose — a session budget may shrink a call's ceiling
        and may never widen it past what the deployment configured.
        """
        if policy.max_cost_usd is None:
            return self._max_budget_usd
        return min(self._max_budget_usd, policy.max_cost_usd)

    def _pure_llm_options(
        self,
        *,
        system_prompt: str | None,
        max_turns: int,
        output_format: dict[str, Any] | None,
        policy: LlmCallPolicy = DEFAULT_LLM_CALL_POLICY,
    ) -> ClaudeAgentOptions:
        return ClaudeAgentOptions(
            tools=[],  # pure-LLM mode; NEVER disallowed_tools=["*"] (kills StructuredOutput)
            allowed_tools=[],
            permission_mode="dontAsk",
            system_prompt=system_prompt,
            model=self._model_for(policy),
            max_turns=max_turns,
            max_budget_usd=float(self._budget_for(policy)),
            output_format=output_format,
        )

    # -- the operational envelope ---------------------------------------------

    def _remaining(self, deadline: float) -> float:
        """Seconds left on the call's wall clock. Never zero or negative: a
        non-positive ``wait_for`` timeout is a different code path in asyncio,
        and we want the ordinary timeout translation."""
        return max(0.001, deadline - time.monotonic())

    def _timed_out(self) -> SourceUnavailableError:
        return SourceUnavailableError(
            f"Claude Agent SDK call exceeded its {self._envelope.timeout_seconds:g}s wall clock",
            details={
                "provider": "claude_agent_sdk",
                "timeout_seconds": self._envelope.timeout_seconds,
            },
        )

    @staticmethod
    async def _aclose(stream: AsyncIterator[Any]) -> None:
        """Close the SDK's async generator, which is what tears the CLI
        subprocess down. Left to the garbage collector the process survives
        the cancelled call — the leak a timeout exists to prevent."""
        closer = getattr(stream, "aclose", None)
        if closer is None:
            return
        try:
            await closer()
        except Exception:  # a failed close must never mask the real error
            logger.debug("closing the Claude Agent SDK stream raised", exc_info=True)

    async def _collect_within_envelope(
        self, prompt: str, options: ClaudeAgentOptions
    ) -> tuple[ResultMessage | None, int]:
        """Run the call under the deadline, retrying transient failures only.

        Returns the ``ResultMessage`` and the number of attempts made. The
        deadline is shared across attempts, and a backoff that would not fit
        inside what is left is not taken — the call fails now with the real
        error rather than failing later with a timeout that hides it.
        """
        deadline = time.monotonic() + self._envelope.timeout_seconds
        attempts = self._envelope.max_attempts
        for attempt in range(1, attempts + 1):
            try:
                return await self._attempt(prompt, options, deadline), attempt
            except (SourceUnavailableError, QueryBudgetExceededError):
                # Already translated by us — the deadline, or a provider
                # outcome the port has a name for. Terminal by construction.
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt >= attempts or not is_transient(exc):
                    raise self._source_unavailable(exc) from exc
                delay = self._envelope.backoff_for(attempt, jitter=self._jitter())
                if time.monotonic() + delay >= deadline:
                    raise self._source_unavailable(exc) from exc
                logger.warning(
                    "claude adapter retry attempt=%d/%d delay=%.3fs error=%s",
                    attempt,
                    attempts,
                    delay,
                    type(exc).__name__,  # type only: str(exc) can echo the prompt
                )
                await self._sleep(delay)
        raise AssertionError("retry loop exited without a result")  # pragma: no cover

    async def _attempt(
        self, prompt: str, options: ClaudeAgentOptions, deadline: float
    ) -> ResultMessage | None:
        """One bounded, concurrency-capped call.

        The semaphore is held per attempt rather than across the whole retry
        sequence, so a backing-off call does not sit on a slot another turn
        could be using.
        """
        async with self._semaphore:
            try:
                return await asyncio.wait_for(
                    self._collect_result(prompt, options), self._remaining(deadline)
                )
            except TimeoutError as exc:
                raise self._timed_out() from exc

    async def _collect_result(
        self, prompt: str, options: ClaudeAgentOptions
    ) -> ResultMessage | None:
        """Drain the query stream, keeping the ``ResultMessage``.

        The generator is held in a local and always ``aclose()``d, so a
        cancellation from the deadline above tears the subprocess down instead
        of orphaning it.

        Raises the provider's exception *unclassified* when no result arrived:
        the retry loop owns classification. On error subtypes the SDK raises a
        bare ``Exception`` after yielding the ``ResultMessage``; if the message
        was already seen that raise is swallowed.
        """
        result: ResultMessage | None = None
        stream = claude_agent_sdk.query(prompt=prompt, options=options)
        try:
            async for message in stream:
                if isinstance(message, ResultMessage):
                    result = message
        except Exception:
            if result is None:
                raise
        finally:
            await self._aclose(stream)
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

    @staticmethod
    def _failure_kind(
        result: ResultMessage, output: Mapping[str, Any] | None
    ) -> LlmFailureKind | None:
        """Name the failure that ``output=None`` on its own cannot express."""
        if output is not None:
            return None
        if result.subtype in _SCHEMA_FAILURE_SUBTYPES or result.structured_output is not None:
            # Either the CLI burned its own structured-output retries, or
            # something arrived that was not a JSON object. Both are the
            # answer failing to take the shape the schema asked for.
            return LlmFailureKind.SCHEMA
        # subtype == "success" with nothing delivered (spike trap #4): the
        # model was asked, ran to completion, and produced no structured
        # answer. That is a statement about the question, not the plumbing.
        return LlmFailureKind.DECLINED

    def _usage_from_result(
        self,
        result: ResultMessage,
        *,
        schema_retries: int,
        attempts: int,
        model: str | None = None,
    ) -> LlmUsage:
        usage_map: dict[str, Any] = result.usage or {}
        cost = result.total_cost_usd
        # The provider splits prompt tokens three ways and calls only the
        # UNCACHED remainder ``input_tokens``; the cached halves live in
        # ``cache_read_input_tokens`` and ``cache_creation_input_tokens``.
        # Copying the one field across published turns reading
        # ``input_tokens: 4`` beside ``output_tokens: 953`` — this pipeline
        # sends the whole governed vocabulary in every prompt, so almost all
        # of it is cache, and almost all of it was being dropped. The port's
        # ``input_tokens`` is the total; the split rides alongside it.
        uncached = int(usage_map.get("input_tokens") or 0)
        cache_read = int(usage_map.get("cache_read_input_tokens") or 0)
        cache_creation = int(usage_map.get("cache_creation_input_tokens") or 0)
        return LlmUsage(
            # The model the call actually ran on, not the process pin: a
            # trace that reports the pin while a session ran a cheaper tier
            # is a trace that cannot explain its own cost.
            model=model if model is not None else self._model_pin,
            cost_usd=Decimal(str(cost)) if cost is not None else Decimal("0"),
            input_tokens=uncached + cache_read + cache_creation,
            output_tokens=int(usage_map.get("output_tokens") or 0),
            schema_retries=schema_retries,
            duration_ms=int(result.duration_ms or 0),
            attempts=attempts,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
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
