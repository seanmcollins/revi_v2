"""M2a de-risking spike: Claude Agent SDK structured output with Revi's refinement-operator union.

Proves, against the *installed* SDK (claude-agent-sdk 0.2.132, bundled CLI 2.1.224):

1. Auth: whether ``claude_agent_sdk.query()`` works with the local Claude Code login when
   ``ANTHROPIC_API_KEY`` is not set.
2. Structured output: a Pydantic discriminated union (12 refinement operators, ``op`` literal
   discriminator) passed as ``output_format={"type": "json_schema", "schema": ...}`` on
   ``ClaudeAgentOptions``, with the response parsed back through the same union.
3. Pure-LLM mode: no tools (``tools=[]`` — NOT ``disallowed_tools=["*"]``, which blocks the
   CLI's internal ``StructuredOutput`` delivery tool and dooms every call), pinned model,
   minimal turns, hard per-call budget cap.
4. Cost/latency/token measurement over repeated trials, plus one deliberately-impossible
   schema to observe the ``error_max_structured_output_retries`` result subtype.

Run from the repo root:

    uv run python packages/adapter-claude/spikes/structured_output_spike.py

Findings are documented in ``RESULTS.md`` next to this file. Total spend is hard-capped at
``TOTAL_BUDGET_USD``; each call is additionally capped via ``max_budget_usd``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKError,
    ResultMessage,
    SystemMessage,
    query,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

# --------------------------------------------------------------------------------------
# Budget guards (task requirement: total spike spend <= $1)
# --------------------------------------------------------------------------------------

TOTAL_BUDGET_USD = 1.00
PER_CALL_BUDGET_USD = 0.10
IMPOSSIBLE_CALL_BUDGET_USD = 0.25  # retries cost more; still capped

PINNED_MODEL = "claude-sonnet-5"

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

ANALYST_PROMPT = (
    "The analyst was shown a payer-level cash decline table "
    "(referents F1=payer row Atlas, F2=payer row Meridian). "
    "They said: 'just the top two payers, show me their CARC mix monthly'."
)


# --------------------------------------------------------------------------------------
# Refinement operator union (mirrors design doc section 7.4; simplified field types)
# --------------------------------------------------------------------------------------


class _Op(BaseModel):
    """Base for all operators: closed schemas so pydantic emits additionalProperties: false."""

    model_config = ConfigDict(extra="forbid")


class SetDimensions(_Op):
    op: Literal["set_dimensions"] = "set_dimensions"
    dimensions: list[str] = Field(description="Dimension names to group by, e.g. ['carc']")


class AddFilter(_Op):
    op: Literal["add_filter"] = "add_filter"
    dimension: str
    operator: Literal["eq", "neq", "in", "not_in"] = "eq"
    values: list[str]


class RemoveFilter(_Op):
    op: Literal["remove_filter"] = "remove_filter"
    dimension: str


class SetWindow(_Op):
    op: Literal["set_window"] = "set_window"
    window: str = Field(description="Named or explicit window, e.g. 'last_6_months'")


class SetComparison(_Op):
    op: Literal["set_comparison"] = "set_comparison"
    comparison: Literal["prior_period", "prior_year", "none"]


class SetGrain(_Op):
    op: Literal["set_grain"] = "set_grain"
    grain: Literal["daily", "weekly", "monthly", "quarterly"]


class DrillInto(_Op):
    op: Literal["drill_into"] = "drill_into"
    target: str = Field(description="Referent id of the finding/row/element to drill into, e.g. 'F1'")


class Pivot(_Op):
    op: Literal["pivot"] = "pivot"
    measures: list[str] = Field(description="Measure names; same cohort, different measure family")


class Explain(_Op):
    op: Literal["explain"] = "explain"
    target: str = Field(description="Referent id of the finding to decompose")


class RankBy(_Op):
    op: Literal["rank_by"] = "rank_by"
    measure: str
    direction: Literal["asc", "desc"] = "desc"
    limit: int | None = None


class Expand(_Op):
    op: Literal["expand"] = "expand"
    axis: str = Field(description="Axis along which to widen scope, e.g. a dimension name")


class ResetContext(_Op):
    op: Literal["reset_context"] = "reset_context"


Refinement = Annotated[
    SetDimensions
    | AddFilter
    | RemoveFilter
    | SetWindow
    | SetComparison
    | SetGrain
    | DrillInto
    | Pivot
    | Explain
    | RankBy
    | Expand
    | ResetContext,
    Field(discriminator="op"),
]


class RefinementResponse(_Op):
    operators: list[Refinement]
    rationale: str = Field(description="One or two sentences on why these operators")


def sanitize_schema(node: Any) -> Any:
    """Strip keywords the bundled CLI's strict JSON-Schema validator rejects.

    Pydantic emits an OpenAPI-style ``discriminator`` next to ``oneOf`` for discriminated
    unions; the CLI (2.1.224) rejects it with ``strict mode: unknown keyword: "discriminator"``.
    Removing it is lossless for constraint purposes — the ``oneOf`` + per-variant ``op``
    const still fully pins the union; the discriminator only speeds up validator dispatch.
    """
    if isinstance(node, dict):
        return {k: sanitize_schema(v) for k, v in node.items() if k != "discriminator"}
    if isinstance(node, list):
        return [sanitize_schema(item) for item in node]
    return node


# A schema no output can satisfy: `answer` must equal both "yes" and "no".
IMPOSSIBLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"answer": {"allOf": [{"const": "yes"}, {"const": "no"}]}},
    "required": ["answer"],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------------------
# Trial harness
# --------------------------------------------------------------------------------------


@dataclass
class Trial:
    label: str
    wall_s: float = 0.0
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    num_turns: int | None = None
    subtype: str = "?"
    is_error: bool = True
    model_seen: str | None = None
    structured_output: Any = None
    parsed: RefinementResponse | None = None
    parse_error: str | None = None
    notes: list[str] = field(default_factory=list)


def _build_options(
    schema: dict[str, Any],
    *,
    model: str | None,
    max_budget_usd: float,
    max_turns: int | None = 2,
) -> ClaudeAgentOptions:
    """Pure-LLM options: empty base tool list, minimal turns, hard budget cap.

    Empirical constraints discovered by this spike (CLI 2.1.224):

    - ``tools=[]`` is the reliable pure-LLM mode. ``disallowed_tools=["*"]`` blocks the
      internal ``StructuredOutput`` tool that delivers the structured result, producing
      ``error_max_structured_output_retries`` (5 attempts) or ``error_max_turns``.
      ``allowed_tools=["StructuredOutput"]`` does NOT override the disallow-all.
    - Structured output consumes an extra turn: a successful no-tool call reports
      ``num_turns == 2``, so ``max_turns=1`` is too tight once anything retries.
    - Leaving the default tool preset loaded (no ``tools=[]``) inflates cost ~10-40x
      (full Claude Code system prompt + tool schemas) and can blow the budget cap.
    """
    return ClaudeAgentOptions(
        tools=[],
        allowed_tools=[],
        permission_mode="dontAsk",
        system_prompt=SYSTEM_PROMPT,
        model=model,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        output_format={"type": "json_schema", "schema": schema},
    )


async def _run_trial(
    label: str,
    prompt: str,
    options: ClaudeAgentOptions,
    *,
    parse: bool,
) -> Trial:
    trial = Trial(label=label)
    start = time.monotonic()
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, SystemMessage) and message.subtype == "init":
                trial.model_seen = message.data.get("model")
            elif isinstance(message, AssistantMessage):
                trial.model_seen = trial.model_seen or message.model
            elif isinstance(message, ResultMessage):
                trial.subtype = message.subtype
                trial.is_error = message.is_error
                trial.cost_usd = message.total_cost_usd
                trial.num_turns = message.num_turns
                trial.structured_output = message.structured_output
                if message.usage:
                    trial.input_tokens = message.usage.get("input_tokens")
                    trial.output_tokens = message.usage.get("output_tokens")
                    trial.cache_read_tokens = message.usage.get("cache_read_input_tokens")
    except ClaudeSDKError as exc:
        trial.notes.append(f"SDK error: {type(exc).__name__}: {exc}")
    except Exception as exc:  # the SDK raises a bare Exception AFTER yielding an error ResultMessage
        trial.notes.append(f"post-result error: {type(exc).__name__}: {exc}")
    trial.wall_s = time.monotonic() - start

    if parse and trial.structured_output is not None:
        try:
            trial.parsed = RefinementResponse.model_validate(trial.structured_output)
        except ValidationError as exc:
            trial.parse_error = str(exc)
    return trial


def _check_expected_operators(trial: Trial) -> None:
    """Soft semantic checks: correct output drills into F1/F2, sets carc dims, monthly grain."""
    if trial.parsed is None:
        return
    ops = trial.parsed.operators
    op_names = [op.op for op in ops]
    drill_targets = sorted(op.target for op in ops if isinstance(op, DrillInto))
    has_carc = any(
        isinstance(op, SetDimensions) and any("carc" in d.lower() for d in op.dimensions) for op in ops
    )
    has_monthly = any(isinstance(op, SetGrain) and op.grain == "monthly" for op in ops)
    trial.notes.append(f"ops={op_names}")
    trial.notes.append(f"drill_targets={drill_targets} carc_dim={has_carc} monthly_grain={has_monthly}")
    if drill_targets != ["F1", "F2"]:
        trial.notes.append("WARN: expected DrillInto targets ['F1', 'F2']")
    if not has_carc:
        trial.notes.append("WARN: expected a SetDimensions naming carc")
    if not has_monthly:
        trial.notes.append("WARN: expected SetGrain(monthly)")


def _print_trial(trial: Trial) -> None:
    cost = f"${trial.cost_usd:.4f}" if trial.cost_usd is not None else "n/a"
    print(
        f"[{trial.label}] subtype={trial.subtype} error={trial.is_error} "
        f"wall={trial.wall_s:.1f}s cost={cost} "
        f"tokens(in/out/cache_read)={trial.input_tokens}/{trial.output_tokens}/{trial.cache_read_tokens} "
        f"turns={trial.num_turns} model={trial.model_seen}"
    )
    if trial.parsed is not None:
        print(f"  parsed OK through Pydantic union; rationale: {trial.parsed.rationale!r}")
    if trial.parse_error:
        print(f"  PARSE FAILED:\n{trial.parse_error}")
    if trial.structured_output is not None and trial.parsed is None:
        print(f"  raw structured_output: {json.dumps(trial.structured_output)[:500]}")
    for note in trial.notes:
        print(f"  {note}")


async def main() -> int:
    schema = sanitize_schema(RefinementResponse.model_json_schema())
    print(f"Union schema: {len(json.dumps(schema))} bytes, {len(schema.get('$defs', {}))} $defs")

    trials: list[Trial] = []
    total_cost = 0.0

    def spent(trial: Trial) -> float:
        return trial.cost_usd or 0.0

    # -- Auth reality check + trial 1 ------------------------------------------------
    first = await _run_trial(
        "trial-1-pinned",
        ANALYST_PROMPT,
        _build_options(schema, model=PINNED_MODEL, max_budget_usd=PER_CALL_BUDGET_USD),
        parse=True,
    )
    if first.subtype == "?":
        print("BLOCKER: claude_agent_sdk.query() failed before returning a result (see error above).")
        _print_trial(first)
        print(
            "If the error is auth-related: log in interactively (`claude` -> /login) or set "
            "ANTHROPIC_API_KEY in the environment / .env, then re-run this spike."
        )
        return 1
    _check_expected_operators(first)
    _print_trial(first)
    trials.append(first)
    total_cost += spent(first)

    # -- Trials 2-3: variance on the pinned model ------------------------------------
    for i in (2, 3):
        if total_cost + PER_CALL_BUDGET_USD > TOTAL_BUDGET_USD:
            print("Stopping: total budget would be exceeded.")
            break
        trial = await _run_trial(
            f"trial-{i}-pinned",
            ANALYST_PROMPT,
            _build_options(schema, model=PINNED_MODEL, max_budget_usd=PER_CALL_BUDGET_USD),
            parse=True,
        )
        _check_expected_operators(trial)
        _print_trial(trial)
        trials.append(trial)
        total_cost += spent(trial)

    # -- No model pin: observe the default -------------------------------------------
    if total_cost + PER_CALL_BUDGET_USD <= TOTAL_BUDGET_USD:
        unpinned = await _run_trial(
            "trial-4-unpinned",
            ANALYST_PROMPT,
            _build_options(schema, model=None, max_budget_usd=PER_CALL_BUDGET_USD),
            parse=True,
        )
        _check_expected_operators(unpinned)
        _print_trial(unpinned)
        trials.append(unpinned)
        total_cost += spent(unpinned)

    # -- Impossible schema: observe structured-output retry behavior ------------------
    if total_cost + IMPOSSIBLE_CALL_BUDGET_USD <= TOTAL_BUDGET_USD:
        # max_turns=None: with a turn cap the run dies as error_max_turns before the
        # structured-output retry limit (5 attempts) is reached; budget still bounds it.
        impossible = await _run_trial(
            "trial-5-impossible-schema",
            "Answer the question: is water wet?",
            _build_options(
                IMPOSSIBLE_SCHEMA,
                model=PINNED_MODEL,
                max_budget_usd=IMPOSSIBLE_CALL_BUDGET_USD,
                max_turns=None,
            ),
            parse=False,
        )
        _print_trial(impossible)
        trials.append(impossible)
        total_cost += spent(impossible)

    # -- Summary ----------------------------------------------------------------------
    print(f"\nTotal spend: ${total_cost:.4f} (cap ${TOTAL_BUDGET_USD:.2f})")
    union_trials = [t for t in trials if t.label.startswith(("trial-1", "trial-2", "trial-3", "trial-4"))]
    ok = [t for t in union_trials if t.parsed is not None]
    print(f"Union parse success: {len(ok)}/{len(union_trials)}")
    return 0 if len(ok) == len(union_trials) and union_trials else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
