# M2a Spike Results — Claude Agent SDK structured output

Spike: `structured_output_spike.py` (run with `uv run python packages/adapter-claude/spikes/structured_output_spike.py` from the repo root).

Environment measured on 2026-08-07:

- `claude-agent-sdk` **0.2.132** (Python), bundled CLI **2.1.224** (`_bundled/claude`, arm64 Mach-O — the SDK spawns it as a subprocess; no separate Claude Code install needed).
- macOS, `ANTHROPIC_API_KEY` **not** set, interactive Claude Code login present.
- Total spend across all spike development + two full runs: **≈$0.68**, under the $1 cap
  (full runs: $0.1385 and $0.0990).

## 1. Auth outcome: local Claude Code login WORKS — no API key needed

`claude_agent_sdk.query()` succeeds with no `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` in the
environment. The bundled CLI resolves the machine's interactive Claude Code login on its own.
There is **no setup blocker**: no key needs to be placed in `.env` to run this spike or the
M9 adapter locally. (CI/servers will still need `ANTHROPIC_API_KEY`; the spike prints a clear
message and exits non-zero if no credential source works.)

Caveat: usage is billed to the logged-in Claude account (rate-limit events show on the stream as
`RateLimitEvent`), and the login's configured default model leaks into unpinned calls — see §4.

## 2. Installed API surface (facts, verified against 0.2.132 source)

`ClaudeAgentOptions` fields relevant to the adapter (dataclass, `claude_agent_sdk/types.py`):

| Field | Type | Notes |
|---|---|---|
| `output_format` | `dict[str, Any] \| None` | Exactly `{"type": "json_schema", "schema": {...}}`. The transport forwards **only** the `schema` value, as `--json-schema '<json>'`. Any other `type` is silently ignored. |
| `max_budget_usd` | `float \| None` | Forwarded as `--max-budget-usd`. Enforced between turns → can overshoot (observed $0.151 spent against a $0.05 cap when default tools were loaded). Result subtype on trip: `error_max_budget_usd`. |
| `tools` | `list[str] \| ToolsPreset \| None` | **`tools=[]` is the pure-LLM mode** (base tool list empty). |
| `allowed_tools` / `disallowed_tools` | `list[str]` | See the `disallowed_tools=["*"]` trap in §3. |
| `model` | `str \| None` | `"claude-sonnet-5"` accepted as a pin. |
| `fallback_model` | `str \| None` | Exists (untested in this spike). |
| `max_turns` | `int \| None` | Structured output consumes an extra turn — see §3. |
| `permission_mode` | Literal incl. `"dontAsk"` | Irrelevant once `tools=[]`, kept for belt-and-suspenders. |
| `system_prompt` | `str \| SystemPromptPreset \| SystemPromptFile \| None` | Plain string works. |
| `effort` | `Literal["low","medium","high","xhigh","max"] \| None` | Available; untested here. |
| `thinking` | `ThinkingConfigAdaptive \| Enabled \| Disabled \| None` | Available; untested here. |
| `betas` | `list[Literal["context-1m-2025-08-07"]]` | No structured-output beta exists or is needed. |

`ResultMessage` fields used: `subtype`, `is_error`, `total_cost_usd`, `usage` (dict with
`input_tokens`, `output_tokens`, `cache_read_input_tokens`, ...), `num_turns`,
**`structured_output`** (the parsed JSON object — this is where the structured result arrives),
`result` (plain text). Result subtypes confirmed present in the CLI binary:
`error_max_structured_output_retries`, `error_max_budget_usd`, `error_max_turns`, `success`.

**SDK error-surfacing quirk:** on any error subtype, `query()` first yields the `ResultMessage`
and then raises a **bare `Exception`** ("Claude Code returned an error result: ...") — not a
`ClaudeSDKError` subclass. The adapter must consume the
`ResultMessage` before the raise and catch broad `Exception`.

## 3. Structured output with the 12-operator discriminated union: WORKS (4/4 parses)

Pydantic v2 models mirroring design doc §7.4 (12 operators, `op` literal discriminator,
`RefinementResponse{operators, rationale}`, `extra="forbid"` so `additionalProperties: false` is
emitted). Schema: 4.8 KB, 12 `$defs`. All four live trials round-tripped
`ResultMessage.structured_output → RefinementResponse.model_validate()` cleanly.

Traps found (each cost a failed run; the spike now encodes the fixes):

1. **`discriminator` keyword rejected.** Pydantic emits an OpenAPI-style `discriminator` beside
   `oneOf` for discriminated unions; the CLI's strict JSON-Schema validator errors with
   `--json-schema is not a valid JSON Schema: strict mode: unknown keyword: "discriminator"`.
   Fix: recursively strip `discriminator` before passing (lossless — `oneOf` + per-variant `op`
   const still pins the union). See `sanitize_schema()`.
2. **`disallowed_tools=["*"]` breaks structured output entirely.** The CLI delivers the
   structured result via an internal **`StructuredOutput` tool**; disallow-all denies it, the
   harness retries 5 times, and the run dies as `error_max_structured_output_retries`
   ("Failed to provide valid structured output after 5 attempts") or `error_max_turns`.
   `allowed_tools=["StructuredOutput"]` does **not** override the disallow. Use `tools=[]`.
3. **Structured output consumes an extra turn.** A successful no-tool call reports
   `num_turns == 2`. `max_turns=1` + `output_format` errored in every configuration tried;
   `max_turns=2` is the working minimum for a single-shot call.
4. **`success` with `structured_output=None` is possible.** Against a deliberately
   unsatisfiable schema, the model argues with the validator, gives up, and the CLI returns
   `subtype="success"`, `structured_output=None`, prose in `.result` (reproduced 3/3). The
   `error_max_structured_output_retries` subtype fires only when the harness itself burns all 5
   attempts. **Adapter rule: `structured_output is None` ⇒ failure, regardless of subtype.**
5. **Default tool preset is a cost bomb.** Omitting `tools=[]` loads the full Claude Code
   system prompt + tool schemas: an identical one-liner cost **$0.151** (blowing its $0.05
   budget cap) vs **$0.0036** with `tools=[]` — a 10–40x multiplier and nondeterministic tool
   side-activity.

## 4. Trials — latency / cost / tokens (final run)

Prompt: payer-level cash decline table, referents F1=Atlas, F2=Meridian; "just the top two
payers, show me their CARC mix monthly". Pinned trials on `claude-sonnet-5`, `tools=[]`,
`max_turns=2`, `max_budget_usd=0.10`.

| Trial | Model | Wall | Cost | in/out/cache-read tokens | Subtype | Union parse | Operators emitted |
|---|---|---|---|---|---|---|---|
| 1 pinned | claude-sonnet-5 | 7.2 s | $0.0144 | 2 / 563 / 2425 | success | OK | DrillInto(F1), DrillInto(F2), SetDimensions(carc), SetGrain(monthly) |
| 2 pinned | claude-sonnet-5 | 5.5 s | $0.0082 | 2 / 439 / 3192 | success | OK | DrillInto(F1), DrillInto(F2), SetDimensions(carc), SetGrain(monthly) |
| 3 pinned | claude-sonnet-5 | 15.1 s | $0.0205 | 2 / 1257 / 3192 | success | OK | AddFilter(payer), SetDimensions, SetGrain(monthly) |
| 4 unpinned | **claude-fable-5** | 12.7 s | $0.0528 | 2 / 694 / 2361 | success | OK | DrillInto(F1), DrillInto(F2), SetDimensions(carc), SetGrain(monthly) |
| 5 impossible schema | claude-sonnet-5 | 20.5 s | $0.0426 | 8 / 1643 / 5473 | success (`structured_output=None`) | n/a | — |

A second full run reproduced 4/4 union parses at **$0.0990** total, with all three pinned trials
emitting the ideal set (DrillInto F1+F2, SetDimensions carc, SetGrain monthly; 6.6–10.1 s,
$0.0084–$0.0145 each) — the unpinned Fable trial was the one that chose AddFilter that time.

Variance notes:

- **Latency 5–15 s, cost $0.008–$0.021** per pinned refinement call. Output-token count is the
  swing factor (trial 3 emitted 2.8x the tokens of trial 2 — thinking/verbosity variance).
- **Schema fidelity was perfect in every trial** — zero validation retries observed on the real
  union (no retry turns; every success at `num_turns=2`). Fidelity is not the risk; operator
  *choice* is.
- **Semantic variance:** before a steering sentence was added to the system prompt, all trials
  compiled "the top two payers" to `AddFilter(payer in [Atlas, Meridian])` instead of
  `DrillInto(F1/F2)`. One sentence ("prefer DrillInto with the referent id over AddFilter when
  the analyst points at shown rows") flipped 3 of 4 trials to DrillInto. Operator preference is
  prompt-steerable but not deterministic at default settings — M9 evals should score operator
  choice, not just parseability.
- **Unpinned model = the user's Claude Code default.** Trial 4 ran on `claude-fable-5`
  ($10/$50 per MTok) at ~4x the cost of the pinned Sonnet call. **Always pin.**
- Prompt caching works across calls within/between runs (`cache_read_input_tokens` ≈ 2.4–3.2K
  after the first call; `input_tokens` drops to 2).

## 5. Recommended M9 adapter configuration

```python
ClaudeAgentOptions(
    tools=[],                      # pure-LLM; NEVER disallowed_tools=["*"] (blocks StructuredOutput)
    allowed_tools=[],
    permission_mode="dontAsk",     # defense in depth; moot with tools=[]
    model="claude-sonnet-5",       # always pin; unpinned inherits the login's default (Fable, 4x cost)
    max_turns=2,                   # structured output costs one extra turn; 1 always fails
    max_budget_usd=0.10,           # per-call cap; enforced between turns, so treat as soft
    system_prompt=REFINEMENT_COMPILER_PROMPT,  # include DrillInto-vs-AddFilter steering
    output_format={"type": "json_schema", "schema": sanitize_schema(RefinementResponse.model_json_schema())},
)
```

Adapter must also:

1. Sanitize `model_json_schema()` output (strip `discriminator`; keep `extra="forbid"` on all models).
2. Treat `structured_output is None` as failure even when `subtype == "success"`; re-prompt or surface.
3. Consume the `ResultMessage` before the SDK's post-result bare-`Exception` raise; catch `Exception`, not just `ClaudeSDKError`.
4. Validate through the Pydantic union (`RefinementResponse.model_validate`) — the CLI validates against the JSON schema, but the union parse is the contract the kernel trusts.
5. Consider `effort="low"` for latency (untested here — worth a follow-up measurement; the field exists on 0.2.132).

## 6. Blockers

None. Auth works via the local login; structured output with the full 12-operator union works
on the installed SDK. The only pre-existing risk to track: the internal `StructuredOutput` tool
and the `--json-schema` strict validator are CLI implementation details that could shift between
CLI versions — pin `claude-agent-sdk` and re-run this spike on upgrades.
