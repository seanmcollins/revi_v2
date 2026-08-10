"""Per-turn model-spend ledger, so a FAILED turn can say what it cost.

A ``TurnError`` carries a code, a message and a correlation id, and used to
carry no usage at all — but a turn that classified the question, interpreted
it, planned it and *then* refused at §6.6 validation has already spent real
tokens. Reporting nothing there understates the deployment's cost by exactly
the turns most likely to be repeated.

The usage exists — every call returns an :class:`LlmUsage` — but it is
summed off the decision trace, and a turn that raises never writes one.
Rather than reach into the engine for it, the API wraps the language-model
port it composes and tallies every call as it happens::

    ledger, token = bind_ledger()   # contextvar, like the turn event bus
    try:    ... run the turn ...
    finally: unbind_ledger(token)

``contextvars`` is what makes this safe: the binding propagates into tasks
created inside the turn and is invisible to every other turn in the
process, so two concurrent turns cannot read each other's spend. That race
is what makes ``LanguageModelPort.last_usage()`` unusable here, and why this
is a ledger rather than a counter on the adapter.

On a SUCCESSFUL turn nothing here is published: the answer's usage is
still summed from the recorded trace, which is the auditable copy. The
ledger is the fallback for the path that has no trace to sum — and when
both exist they are two counts of the same calls, so a divergence is a
bug rather than a number to reconcile.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from decimal import Decimal

from revi_investigation.application.ports import (
    LanguageModelPort,
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)
from revi_investigation_contracts.api import UsageSummary


@dataclass
class UsageLedger:
    """Every model call made inside one bound scope."""

    calls: list[LlmUsage] = field(default_factory=list)

    def record(self, usage: LlmUsage | None) -> None:
        if usage is not None:
            self.calls.append(usage)

    def summary(self) -> UsageSummary:
        """The ledger as the wire shape.

        ``input_tokens`` is the whole prompt — the port already folds the
        provider's cached and uncached buckets together (see
        :class:`~revi_investigation.application.ports.LlmUsage`) — with the
        cached split carried alongside rather than lost.
        """
        cost = Decimal(0)
        summary = UsageSummary()
        input_tokens = output_tokens = cache_read = cache_creation = retries = 0
        for usage in self.calls:
            cost += usage.cost_usd
            input_tokens += usage.input_tokens
            output_tokens += usage.output_tokens
            cache_read += usage.cache_read_tokens
            cache_creation += usage.cache_creation_tokens
            retries += usage.schema_retries
        return summary.model_copy(
            update={
                "llm_calls": len(self.calls),
                "cost_usd": str(cost),
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_creation_tokens": cache_creation,
                "schema_retries": retries,
            }
        )


_LEDGER: ContextVar[UsageLedger | None] = ContextVar("revi_usage_ledger", default=None)


def bind_ledger() -> tuple[UsageLedger, Token[UsageLedger | None]]:
    ledger = UsageLedger()
    return ledger, _LEDGER.set(ledger)


def unbind_ledger(token: Token[UsageLedger | None]) -> None:
    _LEDGER.reset(token)


def _record(usage: LlmUsage | None) -> None:
    ledger = _LEDGER.get()
    if ledger is not None:
        ledger.record(usage)


class MeteredLanguageModel:
    """A :class:`LanguageModelPort` that tallies into the bound ledger.

    Pure decoration: every call is forwarded untouched and every result
    returned untouched. With no ledger bound — every code path outside a
    turn, including the portfolio's re-derivations — recording is a no-op
    and this is indistinguishable from the port it wraps.

    Attribute access falls through, so an adapter's own flags (notably
    ``applies_call_policy``, which the settings policy reads to decide
    whether a model tier is a real control) survive the wrapping.
    """

    def __init__(self, inner: LanguageModelPort) -> None:
        self._inner = inner

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        result = await self._inner.structured(request)
        _record(result.usage)
        return result

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        # The text path reports usage through the request's own sink, which
        # the caller may also be using. Chain rather than replace so both
        # readers survive: assembly still gets its copy for the answer's
        # envelope, and the ledger gets one for the failure path.
        caller_sink = request.usage_sink

        def sink(usage: LlmUsage) -> None:
            _record(usage)
            if caller_sink is not None:
                caller_sink(usage)

        return self._inner.stream_text(
            TextLlmRequest(
                template_id=request.template_id,
                template_version=request.template_version,
                rendered_prompt=request.rendered_prompt,
                policy=request.policy,
                usage_sink=sink,
            )
        )

    async def last_usage(self) -> LlmUsage | None:
        return await self._inner.last_usage()
