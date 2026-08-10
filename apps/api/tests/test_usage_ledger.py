"""A failed turn says what it spent.

"Failures are free" was a claim the envelope made by omission, and it is false:
a turn refused at §6.6 validation has already paid for classification and
interpretation. The properties that matter are that the meter is invisible when
nothing is bound, that it never changes what the port returns, and — the reason
this is a contextvar and not a counter on the adapter — that two concurrent
turns cannot read each other's spend.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

from revi_api.usage_ledger import (
    MeteredLanguageModel,
    bind_ledger,
    unbind_ledger,
)
from revi_investigation.application.ports import (
    LlmUsage,
    StructuredLlmRequest,
    StructuredLlmResult,
    TextLlmRequest,
)


def _usage(cost: str = "0.01", *, input_tokens: int = 1200) -> LlmUsage:
    return LlmUsage(
        model="claude-test",
        cost_usd=Decimal(cost),
        input_tokens=input_tokens,
        output_tokens=300,
        schema_retries=0,
        duration_ms=42,
        cache_read_tokens=900,
        cache_creation_tokens=100,
    )


class _Inner:
    """A minimal port double that also carries an adapter-level flag."""

    applies_call_policy = True

    def __init__(self) -> None:
        self.structured_calls: list[StructuredLlmRequest] = []
        self.text_calls: list[TextLlmRequest] = []

    async def structured(self, request: StructuredLlmRequest) -> StructuredLlmResult:
        self.structured_calls.append(request)
        return StructuredLlmResult(output={"ok": True}, usage=_usage())

    def stream_text(self, request: TextLlmRequest) -> AsyncIterator[str]:
        self.text_calls.append(request)

        async def stream() -> AsyncIterator[str]:
            yield "one "
            yield "two"
            if request.usage_sink is not None:
                request.usage_sink(_usage("0.05", input_tokens=4000))

        return stream()


def _request() -> StructuredLlmRequest:
    return StructuredLlmRequest(
        template_id="classify_turn",
        template_version="v1",
        rendered_prompt="p",
        schema={"type": "object"},
    )


async def test_structured_calls_are_tallied_and_returned_untouched() -> None:
    inner = _Inner()
    metered = MeteredLanguageModel(inner)
    ledger, token = bind_ledger()
    try:
        result = await metered.structured(_request())
    finally:
        unbind_ledger(token)

    assert result.output == {"ok": True}  # pure decoration
    summary = ledger.summary()
    assert summary.llm_calls == 1
    assert summary.cost_usd == "0.01"
    assert summary.input_tokens == 1200 and summary.output_tokens == 300
    assert summary.cache_read_tokens == 900 and summary.cache_creation_tokens == 100


async def test_the_text_stream_is_metered_without_stealing_the_callers_sink() -> None:
    """The assembly stage uses the sink for the answer's own envelope. The
    ledger chains onto it rather than replacing it, so both still read."""
    inner = _Inner()
    metered = MeteredLanguageModel(inner)
    seen: list[LlmUsage] = []
    ledger, token = bind_ledger()
    try:
        chunks = [
            chunk
            async for chunk in metered.stream_text(
                TextLlmRequest(
                    template_id="compose_narrative",
                    template_version="v1",
                    rendered_prompt="p",
                    usage_sink=seen.append,
                )
            )
        ]
    finally:
        unbind_ledger(token)

    assert "".join(chunks) == "one two"
    assert len(seen) == 1  # the caller still got its copy
    assert ledger.summary().cost_usd == "0.05"


async def test_two_concurrent_turns_cannot_read_each_others_spend() -> None:
    """The exact race that made ``last_usage()`` unusable for this."""
    metered = MeteredLanguageModel(_Inner())

    async def turn(calls: int) -> str:
        ledger, token = bind_ledger()
        try:
            for _ in range(calls):
                await metered.structured(_request())
                await asyncio.sleep(0)  # interleave
            return ledger.summary().cost_usd
        finally:
            unbind_ledger(token)

    one, two = await asyncio.gather(turn(1), turn(3))
    assert one == "0.01" and two == "0.03"


async def test_with_no_ledger_bound_the_meter_is_a_no_op() -> None:
    """Every path outside a turn — the portfolio's re-derivations, for one
    — must behave exactly as the unwrapped port."""
    inner = _Inner()
    metered = MeteredLanguageModel(inner)
    result = await metered.structured(_request())
    assert result.output == {"ok": True}
    assert len(inner.structured_calls) == 1


def test_adapter_flags_survive_the_wrapping() -> None:
    """``applies_call_policy`` decides whether a model tier is a real
    control; losing it to a wrapper would silently disable a setting."""
    metered = MeteredLanguageModel(_Inner())
    assert getattr(metered, "applies_call_policy", False) is True


def test_an_untouched_ledger_reports_a_measured_zero() -> None:
    ledger, token = bind_ledger()
    unbind_ledger(token)
    summary = ledger.summary()
    assert summary.llm_calls == 0 and summary.cost_usd == "0"
