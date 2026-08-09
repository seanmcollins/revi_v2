"""Per-turn model-spend ledger — so a FAILED turn can say what it cost.

Round-1 review F19, second half: a ``TurnError`` carried a code, a message
and a correlation id, and no usage at all. That is wrong in the one
direction that matters. A turn that classified the question, interpreted
it, planned it and *then* refused at §6.6 validation has already spent
real tokens; the envelope reported nothing, so the deployment's cost
ledger was quietly short by exactly the turns most likely to be repeated.
"Failures are free" is a claim, and it is false.

The usage exists — every call returns an :class:`LlmUsage` — but it is
summed off the decision trace, and a turn that raises never writes one.
Rather than reach into the engine for it, the API wraps the language-model
port it composes and tallies every call as it happens:

    ledger = UsageLedger()
    token = ledger.bind()          # contextvar, like the turn event bus
    try:    ... run the turn ...
    finally: ledger.unbind(token)

``contextvars`` is what makes this safe: the binding propagates into tasks
created inside the turn and is invisible to every other turn in the
process, so two concurrent turns cannot read each other's spend. That is
precisely the race that made ``LanguageModelPort.last_usage()`` unusable
for this, and it is why this ledger exists rather than a counter on the
adapter.

On a SUCCESSFUL turn nothing here is published: the answer's usage is
still summed from the recorded trace, which is the auditable copy. The
ledger is the fallback for the path that has no trace to sum — and when
both exist they are two counts of the same calls, so a divergence is a
bug worth seeing rather than a number worth reconciling.
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

        ``input_tokens`` is the whole prompt (the port already folds the
        provider's cached and uncached buckets together — see
        :class:`~revi_investigation.application.ports.LlmUsage`), with the
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
    returned untouched. With no ledger bound — which is every code path
    outside a turn, including the portfolio's re-derivations — recording
    is a no-op and this is indistinguishable from the port it wraps.

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
        # the caller may also be using. Chaining rather than replacing keeps
        # both readers: the assembly stage still gets its copy for the
        # answer's envelope, and the ledger gets one for the failure path.
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
