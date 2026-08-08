# M13 acceptance walkthrough — design §18.1, items 1–16

Every claim below was **run**, not recalled. Where a criterion is met by an
adaptation rather than literally, the adaptation is named and the residue is
stated. Where something does not hold, it says so.

- Repository: `/Users/dev/revi_v2`, base commit `652d8ee` plus the
  uncommitted M13 working tree.
- Warehouse: `data/revi_warehouse.duckdb`, newest watermark `wm_003`
  (`2026-08-03 04:10`, newest data date `2026-08-02`).
- Pack: `base-rcm@1.0.0` (+ the `demo-tenant` overlay where the API wires it).

## Gate summary

| Gate | Command | Result |
|---|---|---|
| Python suite | `uv run pytest -q` | **586 passed**, 16 deselected (`live_llm`, `postgres`) |
| Reference regressions | `uv run pytest -m reference -q` | **43 passed**, 559 deselected |
| Lint + boundaries | `make lint` | ruff clean · import-linter **6 kept, 0 broken** · name guard clean |
| Types | `uv run mypy packages/*/src warehouse/generator/src apps/api/src apps/scheduler/src` | **no issues in 114 source files** (strict) |
| Frontend | `cd apps/web && pnpm lint && pnpm test && pnpm build` | eslint clean · **208 tests, 6 files** · build ✓ |

Baseline at the start of M13 was 545 tests; the milestone added 41.

---

## 1. Snowflake can be replaced by the DuckDB repository without changing application or domain code

**ADAPTED — the swap is proven structurally; the Snowflake adapter itself is future work.**

There is no Snowflake account on this project, so the literal swap cannot be
performed. What is demonstrated instead is everything that would make it a
configuration change rather than a rewrite:

- **The port.** `AnalyticalRepository` (`packages/kernel/src/revi_kernel/capabilities.py`)
  is the only way application code reaches data. No domain or application
  module imports a driver — enforced, not asserted: the import-linter contract
  *"duckdb only in the DuckDB connector and warehouse generator"* lists every
  other package as a source module and fails CI on any edge. Only two
  composition-root edges are whitelisted
  (`revi_api.wiring -> revi_connector_duckdb`, `revi_scheduler.sweep -> revi_connector_duckdb`).
- **Capability negotiation.** `RepositoryCapabilities` (as-of reads, cohort
  semi-join + max size, HAVING pushdown, server-side top-N) is queried by
  `PlanValidationService._check_capabilities`, which raises
  `SOURCE_CAPABILITY_UNSUPPORTED` rather than silently degrading. A backend
  that cannot semi-join cohorts is refused loudly:
  `packages/investigation/tests/test_validation.py::TestCapabilities::test_cohort_scope_needs_cohort_semijoin`.
- **A reusable contract suite.** `packages/testing/src/revi_testing/analytical_contract.py`
  defines `AnalyticalRepositoryContract` — 9 behaviours (watermark ordering
  and as-of divergence, watermark stamping, cohort semi-join ≡ re-derived
  predicate, server-side top-N truncation, HAVING pushdown, deterministic
  masked row sampling, ratio components, snapshot aging buckets, cross-grain
  cohorts). DuckDB runs it today via
  `packages/connector-duckdb/tests/test_duckdb_contract.py::TestDuckDbAnalyticalContract`;
  a Snowflake adapter subclasses it and runs the same file unchanged.

**Residue:** the suite has exactly one implementation. Until a second one
runs it, "no application change required" is a well-supported design claim,
not an observation.

## 2. An investigation runs through both an in-process client and an HTTP client using the same contract tests

**PASS.**

`packages/testing/tests/test_api_contract.py` is parameterised over both
transports — each of these runs twice, `[in_process]` and `[http]` (ASGI):
`test_open_session_pins_pack_and_watermark`, `test_nl_turn_answers_with_full_payload`,
`test_typed_refinement_turn`, `test_idempotency_key_returns_the_stored_turn`,
`test_portfolio_serves_ranked_anomaly_population`, plus clarification and
error-envelope cases. HTTP-only additions cover what only a wire has:
`test_sse_event_ordering`, `test_http_404_serves_the_error_envelope`,
`test_malformed_body_is_422_and_not_the_envelope`.

`packages/testing/tests/test_api_reference.py::TestReferenceOverHttp::test_five_turns_match_the_in_process_run`
runs the whole five-turn conversation through both clients and asserts
**identical plan hashes, identical referents, identical impact cents** turn
by turn.

Both are also exercised over real sockets — see the live wire check below.

## 3. No application service creates raw SQL

**PASS.**

- `grep -rniE "select |from .* where|insert into" --include='*.py' packages/investigation/src packages/calculation/src packages/pack/src packages/presentation/src packages/kernel/src` → **no matches**.
- Mechanically enforced by the same import-linter contract as item 1: SQL
  generation lives in `packages/connector-duckdb/src/revi_connector_duckdb/compile.py`,
  driven only by the semantic catalog. Its module docstring states the rule
  the compiler obeys — per-grain curated base views pre-join certified paths,
  *the compiler never invents a join*
  (`test_cross_grain_without_certified_path_unsupported`).
- Application code speaks `EvidenceProbe`; nothing DuckDB-typed crosses the
  port.

## 4. All calculated values are reproducible from recorded evidence frames and versioned metric contracts and operators

**PASS.**

- Every published number is produced by a versioned kernel operator, never by
  a model: `packages/calculation/tests/test_operators.py` (23 tests) covers
  ratio-of-sums, compare antisymmetry and cross-watermark rejection,
  share-of-total, top-k truncation flagging, deterministic rank ties, pivot,
  reconcile tolerance, decompose additivity in exact cents, and projection.
- The trace records what is needed to recompute:
  `packages/investigation/tests/test_first_turn_reference.py::TestReferenceFirstTurn::test_trace_record_persisted_per_design_14`
  asserts 6 probes with 64-char hashes, per-probe cache-hit flags, per-probe
  grades, the operator list, findings, warnings, LLM template ids with schema
  retry counts, and per-stage timings.
- Reproduction is asserted, not assumed:
  `test_second_identical_run_hits_the_evidence_cache` re-runs the same
  question and gets **zero new repository calls** and identical impacts;
  `test_reference_conversation.py::TestReplayDeterminism::test_replay_reproduces_plan_hashes_and_findings`
  replays the recorded operators and reproduces every plan hash, impact and
  title.

## 5. Every investigation is pinned to a pack version **and** a data watermark

**PASS.**

`InvestigationContext` carries both; `OpenSessionService` pins them at session
open and every answer's header shows them.
`test_reference_conversation.py::TestWholeSession::test_every_answer_carries_the_context_header_at_wm003`
asserts the pin holds across all five turns.
`test_refinement_turns.py::TestWatermarkEpochs` covers the two honest
responses to a mid-session refresh: `test_stale_watermark_is_surfaced_and_continuation_stays_pinned`
(banner, stay pinned) and `test_re_anchor_starts_a_new_epoch`.

Observed live: `POST /v1/sessions` →
`watermark=wm_003 @ 2026-08-03T04:10:00 newest_data_date=2026-08-02 epoch=0`,
`pack=base-rcm@1.0.0`.

## 6. Unknown questions produce composed exploration or an explicit evidence limitation — not hallucinated certainty

**PASS.**

Four independent guards, each with tests:

1. **The model is never trusted to have answered.** An unmatched or low-margin
   structured call becomes a clarification, never a guess:
   `test_definitional.py::TestClarificationOutcomes::test_structured_output_none_never_guesses`,
   `test_low_confidence_classification_clarifies`.
2. **Every id the model returns is post-validated against the pack/catalog**:
   `TestInterpretationValidation::test_unknown_metric_id_is_unsupported_concept`,
   `test_unknown_playbook_id_is_unsupported_concept`,
   `test_illegal_basis_is_date_basis_invalid`.
3. **Unanswerable probes are pruned with a surfaced warning rather than
   fabricated.** Seen live on T1: `probe 'lag_distribution_compare' omitted:
   its measures are not answerable at the source for this catalog`.
4. **The narrative validator redacts ungrounded sentences.** Observed live and
   unedited: the scripted demo narrative cites `F1/F2/F3`, but referent
   handles are session-monotonic, so on T2 the same sentence cites handles
   that do not exist in that turn — and the platform redacts it rather than
   printing it: `narrative sentence redacted: cites unknown referent(s) ['F1','F2','F3']`.
   That is the validator doing its job on a stale script. (The scripted demo
   narrative is a fixture limitation, not an engine defect; noted in
   "Findings and follow-ups".)

## 7. The LLM cannot mutate the active pack

**PASS.**

The `LanguageModelPort` surface is `structured()` and `stream_text()` — there
is no write path to pack storage anywhere in the adapter or the application
layer. The model's outputs are ids and closed-set operators that are
validated *after* the fact (item 6). Additionally:

- `packages/investigation/tests/test_llm_layer.py::TestGuard` — the payload
  guard rejects connection strings, file paths, credentials, SSNs, and raw
  tabular payloads before a prompt is sent — 7 parametrised sensitive-payload
  cases plus `test_raw_tabular_payload_rejected` and
  `test_serialized_row_array_rejected`.
- `TestSchemas::test_refinement_union_parses_all_twelve_ops` and
  `test_unknown_operator_and_extra_keys_are_rejected` — the emission schema is
  the closed 12-operator set; anything else is a typed failure.
- `test_prompts_carry_vocabulary_not_data` — prompts carry ids and
  descriptions, never rows.

## 8. A candidate pack change can be replayed against historical investigations before promotion

**ADAPTED — the mechanism is demonstrated end to end; the promotion harness is not built.**

New this milestone: `packages/investigation/tests/test_pack_replay.py`.
It takes a realistic proposal — narrow `cash_posted` to exclude traditional
Medicaid — publishes it as base version `1.1.0`, and replays the recorded
cash-decline investigation against both packs at the same watermark:

```
snapshot ids:        280aff05e2f6  ->  ec2980e129d7
plan hash identical: True
base@1.0.0: weekly cash 132,844,152 vs prior 152,196,731   delta -19,352,579
            F1 State Medicaid · F2 Atlas Commercial · F3 Meridian Health
cand@1.1.0: weekly cash 124,031,309 vs prior 133,474,580   delta  -9,443,271
            F1 Atlas Commercial · F2 Meridian Health · F3 Summit Peak MA
```

The probes are identical (same plan hash), so this is a fair A/B: only the
meaning moved. A reviewer sees "this proposal halves the reported weekly
decline and changes who the top driver is" instead of reading YAML.
`test_candidate_replay_quantifies_the_delta` pins it.

**Residue:** this is one investigation replayed by hand. The offline harness
that would run a *corpus* of historical investigations, summarise the blast
radius, and gate promotion is design §20 / Phase 4 and does not exist.

## 9. Meaning-changing pack revisions create new versions and can be rolled back

**PASS.**

- **A meaning change cannot be made quietly.** Overlays may add aliases and
  tune bounded parameters; redefining a contract is refused at compose time
  with a typed §12 error:
  `test_pack_replay.py::test_overlay_cannot_redefine_meaning` asserts
  `PolicyDeniedError`, `code is ErrorCode.POLICY_DENIED`,
  `details["metric"] == "cash_posted"`. The same rule covers concepts,
  playbooks, knowledge cards, benchmarks and out-of-range detector tunes —
  `packages/pack/tests/test_pack_merge.py` (19 tests).
- **Versions, not edits.** Snapshot ids are content hashes:
  `test_pack_snapshot.py::test_snapshot_id_stable_across_reloads`,
  `test_snapshot_id_ignores_nonsemantic_order`,
  `test_snapshot_id_changes_on_any_content_change`.
  `test_pack_replay.py::test_meaning_change_mints_a_new_snapshot_id` shows the
  candidate producing a different id with `cash_posted` at contract version 2
  while the original still composes from its own layer at version 1.
- **Rollback is a pin, not a restore.** Because every investigation records
  its pack version and the old layer still composes to the old id, reverting
  means pointing at the previous snapshot. Verified in the same test
  (`load_base_pack().id == base.id` after the candidate exists).

## 10. COB and daily-prioritization examples work through generic investigation machinery rather than hard-coded intent branches

**COB: PASS (fixed this milestone). Daily prioritization: PASS for production and ranking; GAP on drill-down continuity.**

### COB — "Do I have a COB problem?"

At the start of M13 this playbook **executed and answered nothing**. Its
probes ran, its transforms applied, and it produced zero findings, because
the findings evaluator only understood `compare` outputs and this playbook
has no comparison. Its proxy branch was also graded `direct`, which would
have let a payer's reason code certify a conclusion about coverage. Both are
fixed; nothing question-specific was added to the engine.

What changed (details in `docs/architecture.md`):

- **Concentration findings** — a generic second finding shape: when no
  `compare` output exists, the first `rank` output with a dimension column and
  a measure supplies findings in rank order. This also unblocks
  `dimension_scorecard` and `timely_filing_watch`, which had the same silent
  emptiness. `impact_cents` is set only when the ranked measure is money.
- **Binding-strength grading** — `PlanValidationService` now min-propagates
  the pack's declared binding strength for the concepts under investigation,
  alongside catalog certification. Applied after the evidence cache so cache
  entries stay concept-independent.
- **Pack content only, no code branches** — two new metric contracts
  (`cob_mismatch_claims`, `cob_mismatch_rate`) carry the governed population
  inside the contract; `cob_investigation.yaml` names them; a new certified
  catalog dimension `cob_mismatch_flag` backs the direct binding.

Verified against `data/answer_key.json` scenario `2_cob_silverline`:

```
header:  2026-04-01..2026-07-31 (service) · watermark wm_003
F1 [direct/high]  Silverline Medicare Advantage: 153 cob mismatch claims
                  (100.0% of visible total)
frames:  cob_mismatch_by_payer         grade=direct
         cob_code_proxy                grade=proxy
         cob_code_proxy__share__rank   grade=proxy   (grade law propagates)
trace grades: {'cob_mismatch_by_payer': 'direct', 'cob_code_proxy': 'proxy'}
```

Answer key: `cob_mismatch_claims: 153`, `silverline_claims_in_window: 1981`,
`cob_mismatch_share: 0.07723372…`, `service_window: 2026-04-01..2026-07-31`.
All three asserted in
`packages/investigation/tests/test_cob_reference.py` (6 tests), plus
`test_validation.py::TestResolution::test_binding_strength_downgrades_per_concept`
(the *same* probe grades DIRECT for `denial` and PROXY for `cob`),
`test_unbound_fields_do_not_downgrade`, and
`test_binding_strength_reaches_the_contracts_own_fields`.

Also driven over real HTTP — see the live wire check.

### Daily prioritization

`GET /v1/portfolio/latest` returned **33 ranked cards** at `wm_003` under
`anomaly_priority@1` with the pack-governed weights
`{impact 0.25, recency 0.15, actionability 0.60, half_life 14d, compliance_floor 0.60}`.
Every card carries its decomposed score components (impact, age, recoverable
estimate, actionability rationale) — the ordering is never a black box — and
the two compliance-mandatory credit-balance items sit at exactly the floor
(0.6000), above a $493k contract-rate reset at 0.4120, which is the governed
behaviour: an un-fixable pile of dollars must not outrank fixable ones.
Covered by `packages/testing/tests/test_portfolio.py` (12 tests, 2 of them
reference-marked against the generated warehouse).

**GAP — drill-down continuity.** Each card carries a complete, well-typed
drill handle (`set_window` + `add_filter` operators over certified
dimensions, bounded by the anomaly's own window), and those operators are
sound: applied to a session that already has an investigation they narrow it
exactly as a chart click would
(`test_api_reference.py::TestPortfolioDrillDown::test_a_card_refines_an_existing_investigation`
— header lands at the portfolio's watermark and window with `payer` and
`service_line` chips). But there is nothing for them to land on from a cold
start: a refinement refines a parent investigation, and a portfolio card is
not one. Posting a card's handle to a fresh session returns the designed
`CLARIFICATION_REQUIRED` ("no prior answer in this session to refine yet")
with zero probes and zero LLM calls — honest, but not an answer
(`test_drilling_a_card_from_a_fresh_session_clarifies`).

The missing piece is the one the build plan named and this milestone did not
build: `PortfolioResponse` carrying a portfolio-anchored session id whose
investigation the card's operators refine. All three tests are checked in so
the gap cannot quietly close or quietly widen.

## 11. Any combination of certified dimensions, filter algebra, and time windows executes with no new code

**PASS.**

- **Windows.** `packages/kernel/tests/test_scope.py` (26 tests) covers
  trailing/full-period/to-date modes, WEEK/MONTH/QUARTER units, leap-day and
  month-end clamping, and **fractional quantities** with a documented
  deterministic rule — `test_fractional_months_documented_example` pins the
  3.25-months case, `test_fractional_full_months`, `test_fractional_quarter`,
  `test_fractional_days_round_half_up`. Resolution happens once at plan time
  into stored concrete dates (§6.1), which is what makes replay exact.
- **The composite anchor question** ("payer payments by payer category weekly
  over the last 3.25 months" — fractional window × WEEK bucketing × measure
  and dimension synonyms) runs against the warehouse as
  `packages/connector-duckdb/tests/test_duckdb_contract.py::TestReferenceAnswerKey::test_reference_weekly_cash_by_payer_type_last_13_weeks`.
- **Filter algebra.** `And | Or | Not | Predicate | InCohort` compiles
  generically; cohort semi-joins are proven equivalent to the re-derived
  predicate (`test_cohort_semijoin_equals_inline_predicate`), including
  across grains (`test_claim_cohort_filters_denial_probe`).
- **Scope is bounded, not free.** Uncertified dimensions downgrade the whole
  chain to DISCOVERY (`test_uncertified_dimension_downgrades_to_discovery`);
  a ratio metric cut by a dimension outside its `scope_dimensions` is
  `GRAIN_INCOMPATIBLE` (`test_denial_rate_by_carc_is_grain_incompatible`) —
  meaning is preserved, scope is free.
- Scope is fully recorded: `filter_chips` on every context header, with
  `origin_turn` and `pinned` per clause.

## 12. Refinements change only what they name, property-verified

**PASS.**

`packages/investigation/tests/test_refinements.py` has a per-operator locality
test for all twelve (`TestLocality::test_set_dimensions`, `test_add_filter`,
`test_remove_filter`, `test_set_window`, `test_set_window_rederives_comparison`,
`test_set_comparison`, `test_set_grain`, `test_drill_into`, `test_pivot`,
`test_explain_changes_nothing`, `test_rank_by`, `test_expand`) **plus a
hypothesis property over randomly generated specs**:
`test_locality_property_over_random_specs`.

Carryover laws are covered alongside: `TestCarryoverLaws::test_law4_medicaid_conflict_detected_before_execution`
(the Medicaid-in-Medicaid `CONTEXT_CONFLICT`, raised *before* any query),
`test_law4_disjoint_eq_conflict`, `test_law5_pins_survive_reset`,
`test_effective_scope_conjoins_pins`.

## 13. Session drill-down children reconcile to their parent at the pinned watermark; violations are flagged, never silently shown

**PASS.**

`test_reference_conversation.py::TestTurnByTurn::test_t2_reconciles_children_to_t1_totals`
runs the real reconciliation on real frames. The operator itself is covered by
`test_operators.py::TestReconcile` — `test_exact_pass`, `test_fail_flagged`,
`test_suppression_tolerance` (a suppressed-cell allowance, so suppression is
not mistaken for a mismatch), `test_zero_tolerance_without_suppression`.

Failure is surfaced, never swallowed: `SubmitTurnService` appends
`RECONCILIATION_FAILED: <summary>` to the answer's warnings **and** publishes
a `warning` SSE frame, and the frontend renders a `ReconciliationBanner`.

## 14. Presentation-only, meta, and kernel-only turns issue zero warehouse queries

**PASS — asserted with a spy repository, not inferred.**

`packages/investigation/tests/test_refinement_turns.py::TestZeroProbeTurns`:

- `test_presentation_only_reserves_frames_with_zero_probes`
- `test_meta_turn_cites_recorded_provenance_with_zero_probes`
- `test_kernel_only_rank_turn_executes_zero_probes` (in `TestGestureAndKernelOnly`)
- `test_typed_gesture_skips_the_llm_entirely` — a gesture costs zero model calls too

Plus the DEFINITIONAL extension:
`test_definitional.py::TestDefinitionalPath::test_what_is_pr3_answers_from_pack_with_zero_probes`
asserts `engine.repository.execute_count == 0` while returning group code `PR`
and CARC `3` with pack-version provenance.

Confirmed live: the "what is pr3" turn over HTTP reported `llm_calls=1`
(classification only) and returned a definitional payload sourced to
`base-rcm@1.0.0`; the T5 meta turn reported `llm_calls=1` and cited T1's six
recorded probes.

## 15. A five-turn reference conversation replays deterministically from its recorded operators

**PASS.**

`packages/investigation/tests/test_reference_conversation.py::TestReplayDeterminism`:

- `test_replay_reproduces_plan_hashes_and_findings` — every turn's plan hash,
  impact cents and finding titles reproduce exactly.
- `test_replay_used_no_refinement_llm` — the replay re-executes the *recorded
  operators*; the refinement model is never called, so determinism is not
  luck.
- `test_replay_meta_cites_identical_provenance`.

Determinism rests on `ProbeNode.hash` being content-stable: predicate
`origin_turn` tags are stripped and cohort refs reduce to their definition, so
re-materialised cohorts and different asking turns still hash identically
(`test_planning.py::TestPlanHashAndDiff::test_plan_hash_is_stable_and_window_sensitive`).

(The design document uses an older name for this conversation. The repository
standardised on `reference` — marker, Make target and test names — at commit
`7c563c4`; M13 removed the last transitional marker alias. See "What else
changed".)

## 16. Every displayed number's effective context is shown to the analyst and stored in the trace

**PASS.**

- **Shown.** `ContextHeaderPayload` accompanies every answer with window +
  basis, comparison window, filter chips (with origin turn and pinned flag),
  cohort id and size, and watermark. Asserted verbatim across all five turns
  by `test_every_answer_carries_the_context_header_at_wm003`. Live sample from
  T3, including the pinned cohort:
  `2026-07-27..2026-08-02 (post) · vs 2026-07-20..2026-07-26 · cohort: cohort_6710faef64de (55722 claims) · watermark wm_003`.
- **Stored.** `test_trace_record_persisted_per_design_14` (item 4).
- **Qualifications travel with the number.** Alternate date bases are labelled
  (`alternate_basis_used: probe 'submission_volume_by_payer' reads
  'claim_volume' on the 'submission' basis (primary is 'service')`),
  suppression is declared on every affected answer, truncation sets a
  `truncated` flag, and a share computed over a suppressed frame now says
  *"of visible total"* rather than *"of total"* — a wording fix made this
  milestone, because a 100% share of a partially suppressed population is not
  a 100% share.

---

## Live wire check (real HTTP, not ASGI)

```
REVI_LLM_MOCK=1 uv run uvicorn revi_api.main:app --port 8000
# then an httpx script (ad hoc, not checked in) against 127.0.0.1:8000
```

Server wiring logged at startup: `store_mode=memory`, `llm_mode=scripted-demo`,
warehouse `data/revi_warehouse.duckdb`, pack `base-rcm@1.0.0` + `demo-tenant`
overlay. Everything below is real sockets, real DuckDB, real kernel; only the
probabilistic layer (turn class, interpreted ids, refinement operators) is
scripted.

| Step | Result |
|---|---|
| `GET /v1/health` | `200 {status: ok, watermark: wm_003, store_mode: memory, llm_mode: scripted-demo}` |
| `GET /v1/capabilities` | as_of_reads ✓, cohort_semijoin ✓ (max 100k), having_pushdown ✓, server_side_top_n ✓; pack snapshot `090422f8a386…` |
| `POST /v1/sessions` | pinned `wm_003 @ 2026-08-03T04:10:00`, epoch 0 |
| **T1 over SSE** | 24 frames: `stage`×12, `context_header`, `finding`×3, `chart_spec`×4, `narrative_delta`×3, `turn_complete`. F1 State Medicaid −$99,093 · F2 Atlas Commercial −$48,940 · F3 Meridian Health −$38,064 (= answer key) |
| T2 `Break that down by payer` | refinement; F4–F6, same impacts (session-monotonic handles) |
| T3 `…top three payers — CARC mix?` | cohort pinned (`55,722` claims), denial-grain CARC cut, header shows the cohort |
| T4 `Compare that to Q1` | comparison re-derived to `2026-01-01..2026-03-31`, cohort retained |
| T5 `Why do you say F2?` | META, `llm_calls=1`, cites 6 recorded probes + 5 operators + per-probe grades |
| `GET …/lineage` | 5 investigations, 4 typed edges: `set_dimensions` → `drill_into×3 + pivot + set_dimensions` → `set_comparison` → (meta, no operators) |
| **`what is pr3`** | DEFINITIONAL, `llm_calls=1`, group code `PR` + CARC `3` from `base-rcm@1.0.0` |
| **`Do I have a COB problem?`** | F1 Silverline 153 mismatch claims [direct/high]; `chart_cob_code_proxy` grade=**proxy**, `chart_cob_mismatch_by_payer` grade=**direct** |
| `GET /v1/portfolio/latest` | 33 cards, `anomaly_priority@1`, every card `provenance=external_detection` with formula version + source watermark, none with a `grade` |
| `GET /v1/investigations/does-not-exist` | `404 {code: REFERENT_NOT_FOUND, message: …, correlation_id: …}` |
| Unknown referent in a gesture | `200` with `outcome=error`, `code=REFERENT_NOT_FOUND` — a failed *turn* is a turn outcome, not an HTTP error |

Server stopped afterwards.

`pnpm build` and `pnpm test` pass (208 tests); a browser-driven verification of
the running UI against the live API was **not** performed.

---

## What else changed in M13

- **Deprecated marker alias removed.** The transitional pytest alias for the
  `reference` marker is gone from `pyproject.toml`, and the retired term no
  longer appears anywhere in the repository — a case-insensitive
  `grep -rni` excluding `node_modules`, `.venv`, the tool caches, `uv.lock`
  and Sean's design document returns nothing. `pytest -m reference` and
  `make reference` are the only spellings.
- **OpenAPI gaps closed.** `ErrorEnvelope` is now the declared response model
  on every `/v1` route at 400/404/409/503; domain errors moved off 422 so that
  status keeps exactly one model (FastAPI's `HTTPValidationError`).
  `TurnStreamEvent` publishes the nine SSE frame kinds and their payloads.
  Guarded by `test_api_contract.py::TestPublishedSpec` server-side and
  `apps/web/src/lib/contract-openapi.test.ts` client-side.
- **Portfolio provenance.** `AnomalyCard` gained required `provenance`,
  `priority_formula_version`, `source_watermark_id`; the frontend shows a
  `DetectionBadge` instead of a `GradeBadge`. Rationale in
  `docs/architecture.md`.
- **Cohort TTL sweep.** `python -m revi_scheduler.sweep` (+ `make sweep`),
  with tests. The obsolete `make portfolio` target — which invoked a module
  that never existed — was removed; anomaly population is baked by
  `make warehouse`.
- **`NotFoundError` now carries `REFERENT_NOT_FOUND`** instead of
  `UNSUPPORTED_CONCEPT`: a missing id is a missing handle, not an
  inexpressible concept.
- **`share_of_total`'s `within` argument is no longer silently dropped** by
  the playbook planner. A silently global share is a different number under
  the same column name.
- README and `scripts/generate_web_types.md` corrected to match reality.

## Findings and follow-ups (not fixed here)

1. **Portfolio drill-down anchor** — item 10's gap above. The highest-value
   next piece of work.
2. **The blocking-JSON turn body does not map to the UI's parser.** Publishing
   the 200 body made a pre-existing mismatch visible: the server returns
   `TurnAnswer | TurnClarification | TurnError` discriminated on `outcome`,
   while `parseTurnResponse` expects `status`/`header`/`charts`. The
   `GET /v1/investigations/{iid}` recovery path is fine; the same-idempotency-key
   JSON replay path trips the drift banner. Deliberately not papered over with
   an `outcome → status` alias, since the rest of the payload would still fail
   to map. Pinned in `contract-openapi.test.ts`.
3. **Two pack contracts have inverted `exclusions` polarity.** `exclusions` is
   compiled as `NOT(expr)` — it names what to *remove*. `clean_claim_rate`
   (`{status neq OPEN}`) and `first_pass_yield`
   (`{not: {submission_date is_null}}`) therefore keep exactly the population
   their descriptions say they exclude. Currently inert: both reference
   `status` / `submission_date`, which are not catalog dimensions, so the
   metrics cannot execute at all today. Fixing properly means certifying those
   dimensions as well.
4. **`drop_expired_cohorts` is process-local.** The DuckDB connector tracks
   materialised cohorts in an in-process dict, so a freshly started
   `make sweep` reports "dropped 0" meaning "this process materialised
   nothing", not "no stale tables exist". The CLI says so in its own output
   rather than implying the warehouse was cleaned. A real cron sweep needs the
   connector to persist that registry or expose a list-cohort-tables
   primitive.
5. **All portfolio cards report `age_days: 0`.** Onset falls back to
   `detected_at`, which the generator stamps at the watermark, so the recency
   term contributes uniformly and does not currently separate anything.
6. **The scripted demo narrative cites fixed referent handles** (`F1/F2/F3`)
   while handles are session-monotonic, so refinement turns redact it. The
   validator is correct; the fixture is stale. Cosmetic, but it makes demo
   mode look worse than the engine is.
7. **`ruff format --check` reports 78 pre-existing unformatted files.** `make
   lint` runs `ruff check` (clean) and does not gate on formatting. Left alone
   deliberately: reformatting the repo mid-milestone would bury the diff.
