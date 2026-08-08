"""Live smoke tests against the real Claude Agent SDK.

Excluded from the default run; execute explicitly with:

    uv run pytest packages/adapter-claude -m live_llm -q

Auth: works with the local interactive Claude Code login — no
ANTHROPIC_API_KEY needed on a logged-in machine (spike RESULTS.md §1).
Total spend across both tests is asserted to stay under $0.30.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from revi_adapter_claude.adapter import ClaudeAgentSdkLanguageModel
from revi_investigation.application.llm.schemas import RefinementEmissionResponse
from revi_investigation.application.ports import StructuredLlmRequest, TextLlmRequest

pytestmark = pytest.mark.live_llm

LIVE_MODEL_PIN = "claude-sonnet-5"
TOTAL_BUDGET_USD = Decimal("0.30")

# The spike's refinement-compiler system prompt (RESULTS.md §4), including the
# DrillInto-over-AddFilter steering sentence.
SYSTEM_PROMPT = (
    "You are Revi's refinement compiler. The analyst is refining an existing investigation "
    "context. Translate the analyst's utterance into refinement operators from the closed set "
    "given by the output schema. Referents (F1, F2, ...) are stable ids for findings/rows the "
    "analyst was shown; resolve anaphora to referent ids. Each operator names exactly what it "
    "changes; everything else is inherited from the parent context. Emit only operators that "
    "the utterance justifies. When the analyst points at rows or findings they were shown "
    "(referents), prefer DrillInto with the referent id over AddFilter — DrillInto pins the "
    "cohort so child numbers reconcile with what the analyst saw."
)

# The spike's payer-table prompt.
ANALYST_PROMPT = (
    "The analyst was shown a payer-level cash decline table "
    "(referents F1=payer row Atlas, F2=payer row Meridian). "
    "They said: 'just the top two payers, show me their CARC mix monthly'."
)

_spent: list[Decimal] = []  # cost ledger shared across the two tests in this module


async def test_structured_round_trip_live() -> None:
    adapter = ClaudeAgentSdkLanguageModel(LIVE_MODEL_PIN, max_budget_usd=Decimal("0.15"))
    request = StructuredLlmRequest(
        template_id="emit_refinements",
        template_version="live-smoke",
        rendered_prompt=ANALYST_PROMPT,
        schema=RefinementEmissionResponse.model_json_schema(),  # adapter sanitizes
        system_prompt=SYSTEM_PROMPT,
    )

    result = await adapter.structured(request)
    _spent.append(result.usage.cost_usd)
    print(
        f"\n[live structured] model={result.usage.model} cost=${result.usage.cost_usd} "
        f"tokens(in/out)={result.usage.input_tokens}/{result.usage.output_tokens} "
        f"schema_retries={result.usage.schema_retries} duration={result.usage.duration_ms}ms"
    )

    assert result.output is not None, "structured_output was None (schema failure)"
    parsed = RefinementEmissionResponse.model_validate(result.output)  # union round-trip
    assert parsed.operators, "expected at least one refinement operator"
    print(f"[live structured] operators={[op.op for op in parsed.operators]}")
    assert result.usage.model == LIVE_MODEL_PIN
    assert result.usage.cost_usd > 0
    assert result.usage.duration_ms > 0
    assert result.usage.schema_retries == 0
    assert sum(_spent, Decimal("0")) <= TOTAL_BUDGET_USD


async def test_stream_text_live() -> None:
    adapter = ClaudeAgentSdkLanguageModel(LIVE_MODEL_PIN, max_budget_usd=Decimal("0.10"))
    request = TextLlmRequest(
        template_id="compose_narrative",
        template_version="live-smoke",
        rendered_prompt=(
            "In two short sentences, explain why a payer-level view of denial-rate "
            "trends matters to an RCM analyst."
        ),
        system_prompt="You are Revi's narrative composer. Be brief and concrete.",
    )

    deltas = [delta async for delta in adapter.stream_text(request)]
    usage = await adapter.last_usage()

    assert deltas, "expected at least one text delta"
    assert "".join(deltas).strip()
    assert usage is not None
    _spent.append(usage.cost_usd)
    print(
        f"\n[live stream] model={usage.model} cost=${usage.cost_usd} "
        f"tokens(in/out)={usage.input_tokens}/{usage.output_tokens} "
        f"duration={usage.duration_ms}ms deltas={len(deltas)}"
    )
    assert usage.cost_usd > 0
    assert usage.duration_ms > 0
    total = sum(_spent, Decimal("0"))
    print(f"[live smoke] total spend=${total}")
    assert total <= TOTAL_BUDGET_USD
