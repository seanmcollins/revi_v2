> **Note (2026-08-11):** this tour predates the statistics plane
> (`packages/statistics*`), the discovery API and deep-research engine
> (`packages/investigation/.../discovery/`, `.../deep_research/`), and the
> Monitors-into-Home consolidation. Those areas are mapped in
> `docs/HANDOFF.md` and `docs/agentic-resolution.md`; this tour's full
> refresh is the digestibility pass's closing deliverable.

# Code tour — a reading order for a human reviewer

This is a map for someone reading Revi for the first time, in chunks, in
order. It says what each package is, which few files carry the weight, what to
read closely versus skim, where its tests live, and the invariants to hold in
mind while reading it.

It complements [`AGENTS.md`](../AGENTS.md), which states the rules a change
must obey. This file states the order in which the code makes sense. The
authoritative specification is
[`rcm-investigation-platform-design-v2.md`](../rcm-investigation-platform-design-v2.md);
[`docs/architecture.md`](architecture.md) has the layer diagram.

## Before you start: the one idea

An LLM interprets language but never computes numbers. A deterministic kernel
computes numbers but never interprets language. Everything else in this
codebase is a consequence of holding that line, plus the machinery that makes
the system say "I can't answer that" instead of approximating.

If you read only three files, read
`packages/kernel/src/revi_kernel/probes.py`,
`packages/investigation/src/revi_investigation/application/submit_turn/service.py`,
and `apps/api/src/revi_api/assembly.py` — a question's shape, the turn that
answers it, and the projection onto the wire.

## Suggested order

1. Kernel — the vocabulary everything else speaks
2. Contracts packages — the nouns that cross boundaries
3. Packs and catalog — the governed content, which is where semantics live
4. The investigation engine — interpretation → planning → validation → execution → findings
5. Presentation — turning findings into prose and charts
6. The API app — the wire, and Monitors
7. Connectors and stores — DuckDB and Postgres
8. The testing package — the independent rederivation harness

---

## 1. `packages/kernel` — the calculation kernel

**What it is.** Strict-typed, deterministic, dependency-free. It imports no
other Revi package and no vendor SDK (enforced by an import-linter contract).
Every value that reaches a user is built out of these types.

**Read closely**

- `probes.py` — the probe: the unit of measurement a plan is made of.
- `scope.py` — windows, comparison kinds, and how a period is expressed.
- `grades.py` — evidence grades, and `min_grade`: quality is the *worst* input,
  never an average.
- `money.py` — integer cents. There is no float money anywhere in this repo.
- `maturity.py` — how "this window has not finished settling" is represented.

**Skim** `filters.py`, `frame.py`, `refs.py`, `cohort.py`, `watermark.py`,
`capabilities.py`, `errors.py` — small, and their names are honest.

**Tests** `packages/kernel/tests/`: `test_money.py`, `test_scope.py`,
`test_maturity.py`, `test_grades_filters_probes.py`.

**Invariants to check while reading**

- No float arithmetic on money. Cents are `int`.
- Nothing here knows about SQL, HTTP, pydantic, or a language model.
- A degraded input degrades the output grade; it never silently upgrades.

## 2. `packages/*-contracts` — the nouns that cross boundaries

**What it is.** Pydantic models and protocol definitions, no implementation
dependencies. `investigation-contracts` is the big one and is the *de facto*
API documentation: `contracts/openapi.json` is generated from these class
docstrings.

**Read closely**

- `investigation-contracts/.../api.py` — the answer as it reaches a client.
- `investigation-contracts/.../monitors.py` — pins, tiles, the brief, leads.
- `investigation-contracts/.../header.py` and `narrative.py` — the context
  header and the shape of published prose.

**Skim** `evidence.py`, `refinements.py`, `provenance.py`, `settings.py`,
`debug.py`. `catalog-contracts/model.py` is worth a read before the catalog.

**Tests** these are exercised through `packages/testing/tests/test_api_contract.py`
and the API suites rather than in isolation.

**Invariants to check**

- No implementation package is imported here (import-linter enforces it).
- A field's docstring ships to the public OpenAPI spec — it must read as a
  statement about the field, not as a note to the team.

## 3. `packs/base-rcm` + `packages/pack` + `packages/catalog`

**What it is.** All governed semantics — metric contracts, dimensions,
playbooks, display names, materiality thresholds — live in YAML under `packs/`,
never in code. `packages/pack` loads, merges, validates and snapshots them;
`packages/catalog` is the semantic layer over the warehouse.

**Read closely**

- `packs/base-rcm/metrics/` — start with `denial_rate` and
  `net_collection_rate`; they show the contract shape, including the parts that
  make a metric refuse.
- `pack/src/revi_pack/domain.py` — what a pack *is*.
- `pack/src/revi_pack/loader.py` — how it is loaded and validated (long; skim
  the validation branches, read the top).
- `pack/src/revi_pack/snapshot.py` — pinning a pack version to an answer.
- `catalog/src/revi_catalog/loader.py` + `warehouse/catalog/` — the semantic
  contract with the warehouse.

**Skim** `pack/merge.py`, `pack/conformance.py`, `catalog/masking.py`.

**Tests** `packages/pack/tests/` (`test_base_pack_content.py` is the content
audit; `test_pack_conformance.py` the rules) and `packages/catalog/tests/`.

**Invariants to check**

- No metric id, threshold, or question phrasing is hardcoded in Python. If you
  find one, that is a defect.
- A metric contract states its own population, basis, and comparability rules;
  the engine asks the contract rather than deciding for it.

## 4. `packages/investigation` — the engine

The largest package and the heart of the product. Read it in pipeline order.

### 4a. Interpretation — `application/interpretation.py`

Language in, typed `AnalysisSpec` out. The LLM's output is schema-validated
against closed sets; anything outside them is a refusal, not a guess. Read the
window-resolution code closely — "right now" and "last month" are where a
question quietly becomes a different question.

Prompts live in `application/llm/templates/` and are program input, not
documentation. `application/llm/schemas.py` is the closed set.

**Tests** `test_interpretation_decisions.py`, `test_relative_now_windows.py`,
`test_predicate_values.py`, `test_definitional.py`.

### 4b. Planning and validation — `application/planning.py`, `application/validation.py`

The spec compiles to an `InvestigationPlan` of probes; §6.6 validation then
decides whether the plan may run at all. Read `validation.py` for the refusal
taxonomy — it is the honesty machinery's first gate.

**Tests** `test_planning.py`, `test_validation.py`, `test_chart_sort.py`.

### 4c. Execution and calculation — `application/execution.py`, `application/calculation_glue.py`

Cache-first execution against the warehouse (unchanged probes never re-run),
then the deterministic kernel computes. Read `execution.py`'s suppression and
bounded-cell handling closely; that is where "≤" values are born.

**Tests** `test_execution.py`, `test_calculation_glue.py`,
`test_denial_rate_population.py`.

### 4d. Findings — `application/findings/`

Recently split out of one 3,400-line module; read the modules in dependency
order, each depends only on the ones before it:

| module | what it decides |
| --- | --- |
| `windows.py` | what window a probe actually measured, and how the answer says so |
| `shapes.py` | the shape of a findings set — compare, movement, concentration, scalar, trend |
| `bounds.py` | bounded values, the selection census, and the warnings a selection earns |
| `premise.py` | whether the premise the question asserted is true |
| `builders.py` | one builder per shape a finding can take |
| `service.py` | the stage itself: which shapes a turn has, and what is worth publishing |

`premise.py` is the most interesting file in the package: it is what lets Revi
answer "it did not double — it rose 8%".

**Tests** `test_findings_rendering.py`, `test_premise_verification.py`,
`test_premise_and_selection.py`, `test_bounds_and_maturity.py`,
`test_direction_and_shapes.py`, `test_probe_window_disclosure.py`,
`test_census_and_trend_bounds.py`, `test_unit_integrity.py`.

### 4e. The turn — `application/submit_turn/`

Recently split out of one 6,300-line module. `service.py` holds
`SubmitTurnService`, whose `submit()` dispatches a turn to exactly one path.
The paths are base classes in a single-inheritance chain, in dependency order —
**each one calls only into the ones below it, so any of them can be read
without the ones above**:

| module | class | what it holds |
| --- | --- | --- |
| `recording.py` | `_TurnRecording` | frames, the trace record, turn events, reading a stored investigation back |
| `containment.py` | `_Reconciliation` | reconciling a child answer against its parent's totals (§7.8) |
| `guards.py` | `_AnalysisGuards` | maturity, comparability and subject guards |
| `clarifying.py` | `_ClarificationPolicy` | whether to ask a clarification, and what may go in it |
| `core.py` | `_TurnCore` | the analysis runner and the outcome shapes |
| `refinement.py` | `_RefinementTurns` | the refinement path and the turns that run no probes |
| `service.py` | `SubmitTurnService` | the constructor and `submit()` |

`core.py` is deliberately one class: its six methods call one another in a
cycle (an analysis may end in a clarification, and an answered clarification
re-enters the analysis), so they cannot be separated without inventing a seam.

Alongside the chain are modules of plain functions with no engine state — read
these *first* if you want the decisions without the orchestration:
`types.py`, `header.py`, `census.py`, `clarification.py`, `presentation.py`,
`open_session.py`.

**Tests** `test_refinement_turns.py`, `test_clarification_funnel.py`,
`test_clarification_resume.py`, `test_clarification_convergence.py`,
`test_clarification_options_validated.py`, `test_presentation_and_convergence.py`,
`test_presentation_resume.py`, `test_breakdown_reconciliation.py`,
`test_comparability.py`, `test_comparison_integrity.py`,
`test_window_maturity_governance.py`, `test_answer_hygiene.py`,
`test_playbook_integrity.py`, `test_reuse_and_disclosure_notes.py`,
`test_llm_failure_honesty.py`. End-to-end: `test_reference_conversation.py`,
`test_first_turn_reference.py`, `test_cob_reference.py`.

**Invariants to check across the whole package**

- Every published number traces to a governed metric contract, a pinned
  watermark, and recorded probes. No provenance ⇒ no answer.
- The LLM never emits SQL and never emits a number.
- Clarifications are *successful* outcomes carried as data on `TurnOutcome`,
  never exceptions.
- Refinements reuse the parent plan; unchanged probes must not touch the
  warehouse.
- A bounded value is rendered as a bound, and a bounded field above the
  threshold refuses to be ranked.

## 5. `packages/presentation` — prose and charts

**What it is.** Findings in, sentences and chart specs out. Nothing here
computes; grounding validation asserts that every number in the prose came from
a finding.

**Read closely** `narrative.py` (long — read the composition entry point and
the substitution/redaction rules; skim the phrase tables), then `charts.py`.

**Tests** `test_narrative.py`, `test_narrative_integrity.py`,
`test_prose_and_marks.py`, `test_charts.py`, `test_comparison_charts.py`,
`test_period_axis.py`.

**Invariants to check**

- A sentence may not state a number the findings do not contain.
- A redaction removes a claim; it never leaves a grammatical hole, and it never
  substitutes a clause into a noun slot.
- The chart and the sentence above it agree about which cell is first.

## 6. `apps/api` — the wire, and Monitors

**Read closely**

- `app.py` — the routes and SSE. Short; read it first for the shape of the API.
- `assembly.py` — projects engine output onto the wire. This is where the
  "calm surface, full fidelity in Evidence and exports" rule is implemented.
- `service.py` — the application service that composes everything.
- `wiring.py` — the composition root; the only place that knows which
  connector, store and model adapter are in use.
- `warning_codes.py` — the coded warning families, and the register rules
  (verdict-class content vs. quiet notes).

**Monitors** — `monitors/`, recently split out of one 4,100-line module.
`service.py` assembles `MonitorsService` from one class per capability:

| module | class | capability |
| --- | --- | --- |
| `pins.py` | `_PinApi` | creating, listing, repairing pins, and what each measures |
| `tiles.py` | `_LoadEvaluation` | evaluating a pin at a load into a tile |
| `brief.py` | `_BriefComposition` | the per-load brief and the census that ties out |
| `leads.py` | `_LeadLifecycle` | claiming a fix, and the platform confirming it |
| `cards.py` | `_CardDecoration` | lead state on worklist cards, cash-timing lanes |
| `spec.py` | — | a stored investigation as a re-runnable typed spec |
| `common.py` | `_MonitorsBase` | shared state and small helpers |

Sibling modules `monitors_policy.py` (governed thresholds) and
`monitors_sweep.py` (the scheduled tick) stay outside the package because they
are separately addressable: the policy is content, the sweep is a scheduler.

**Tests** `apps/api/tests/`: `test_monitors_composition.py`,
`test_monitors_lead_verification.py`, `test_monitors_policy.py`,
`test_monitors_sweep.py`, `test_monitor_intent.py`, `test_warning_codes.py`,
`test_evidence.py`, `test_rederive.py`, `test_portfolio_ranking.py`,
`test_worklist.py`. Cross-package API suites live in `packages/testing/tests/`:
`test_api_contract.py`, `test_api_reference.py`, `test_api_auth.py`,
`test_api_settings.py`, `test_monitors_api.py`, `test_monitors_loads.py`,
`test_monitors_surface_integrity.py`, `test_portfolio.py`.

**Invariants to check**

- Every tile is a real `Investigation` with a real trace and permalink — a tile
  runs the same answer path a question does, so the honesty rules cannot be
  re-implemented (and lost) in a second place.
- A monitor threshold never appears in Python; it comes from
  `packs/base-rcm/monitors.yaml`.
- Only the platform may confirm a lead resolved, and only with evidence from a
  load *after* the fix was claimed.
- Everything withheld from a brief is counted on the response.

## 7. Connectors and stores

- `packages/connector-duckdb` — `compile.py` (probe → SQL; long, skim the
  dialect details and read the predicate/cohort handling), `repository.py`,
  `anomalies.py`. DuckDB is the mock; Snowflake is the intended production
  backend and the seam is here. **Tests** `test_duckdb_contract.py`.
- `packages/store-postgres` — application state and alembic migrations.
  `stores.py`, `serde.py`, `tables.py`, `monitors_stores.py`. **Tests**
  `test_serde.py`, `test_postgres_stores.py` (needs `make db-up migrate`),
  `test_session_list_sql.py`.
- `packages/adapter-claude` — the only place `claude_agent_sdk` may be
  imported. `adapter.py`, `envelope.py`, `config.py`. **Tests**
  `test_adapter.py`, `test_config.py`.

## 8. `packages/testing` — the independent check

**What it is.** The warehouse-diff harness recomputes every published finding
by an independent naive-SQL path that reads only metric contract YAML, the
catalog, and each answer's own published context. It exists so correctness is
checked by machinery rather than by trust. Run it with `make warehouse-diff`.

**Read closely** `revi_warehouse_diff/deriver.py` (the independent path),
`goldens.py` + `goldens.json` (reference values derived outside the product's
calculation path), `replay.py`.

**Skim** `revi_testing/` — fakes, fixtures, store contracts, and the scripted
LLM used to make turns deterministic.

**Tests** `packages/testing/tests/test_warehouse_diff.py`,
`test_analytical_fixtures.py`, `test_demo_narrative.py`.

**Invariants to check**

- The deriver must not import the product's calculation path. If it did, the
  harness would be checking the product against itself.
- A golden the deriver cannot yet reproduce is recorded as a refusal with the
  expected reason, so the hole stays visible instead of silently becoming
  coverage.

---

## Where the "why" lives

`docs/reviews/` holds frozen review evidence — the rounds of adversarial
persona review behind many non-obvious behaviors. It is a historical record and
is deliberately not rewritten. Code comments state the constraint; the reviews
record how it was found.

## Reading the tests

Test files are named for their subject, not for the review that motivated them.
A test's docstring states the rule it pins and, where it matters, the defect
that made the rule necessary. The suite is the specification of the honesty
machinery — if a behavior in
[`AGENTS.md`](../AGENTS.md#things-that-look-like-bugs-but-are-features) looks
wrong to you, there is a test asserting it on purpose.
