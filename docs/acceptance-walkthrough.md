# Acceptance walkthrough — design §18.1, items 1–16

Written for M13 and **updated by the post-M13 fix pass**, which closed five
of the defects this document had pinned. Where a claim changed, the original
finding is kept alongside the fix rather than deleted: what was wrong, and
what it took to see it, is the part that does not repeat itself.

Every claim below was **run**, not recalled. Where a criterion is met by an
adaptation rather than literally, the adaptation is named and the residue is
stated. Where something does not hold, it says so.

- Repository: `/Users/dev/revi_v2`, base commit `327ec97` plus the
  uncommitted fix-pass working tree.
- Warehouse: `data/revi_warehouse.duckdb`, newest watermark `wm_003`
  (`2026-08-03 04:10`, newest data date `2026-08-02`).
- Pack: `base-rcm@1.0.0` (+ the `demo-tenant` overlay where the API wires it).

## Gate summary

| Gate | Command | Result |
|---|---|---|
| Python suite | `uv run pytest -q` | **681 passed**, 16 deselected (`live_llm`, `postgres`) |
| Reference regressions | `uv run pytest -m reference -q` | **67 passed**, 630 deselected |
| Lint + boundaries | `make lint` | ruff clean · import-linter **6 kept, 0 broken** · name guard clean |
| Types | `uv run mypy packages/*/src warehouse/generator/src apps/api/src apps/scheduler/src` | **no issues in 119 source files** (strict) |
| Frontend | `cd apps/web && pnpm lint && pnpm test && pnpm build` | eslint clean · **208 passed, 3 skipped** (the live-API suite) · build ✓ |
| Live API driver | server up + `REVI_LIVE_API=… npx vitest run src/lib/liveApi.test.ts` | **3 passed** — T1, a cold-start portfolio drill, a clarification, all with zero contract drift |

Baseline at the start of M13 was 545 tests; the milestone added 41. The
post-M13 fix pass took 586 → 633 (the three inverted §18.1-10 tests were
un-inverted in place, not counted as new). The **round-1 review fix pass**
took 633 → 681 and reference 57 → 67: comparison rendering and window-length
guards, unit-aware published values, the adjudicated `denial_rate`
population, authentication and tenant isolation, portfolio drillability, the
third reconciliation state, and governed benchmarks on the wire.

---

## Security posture

This section exists because its absence was itself a finding. The round-1
review's sharpest criticism of this document was not that authentication
was missing — it was that a gaps list itemizing `ruff format` counts said
nothing at all about there being no authentication. What follows is what
is built, and then what is not.

### Built (round-1 fix pass)

- **Authentication.** Every `/v1` route except `/v1/health` requires
  `Authorization: Bearer <token>`. The token is an HMAC-SHA256-signed
  envelope carrying `tenant`, `sub` and `exp`
  (`apps/api/src/revi_api/auth.py`); the scheme is published in
  `contracts/openapi.json` (`securitySchemes.HTTPBearer`, `security` on
  all six tenant-scoped routes).
- **Tenant isolation.** `tenant` is no longer a client-asserted body
  field. It comes from the signature, and every `{session_id}` /
  `{investigation_id}` lookup resolves the owning session and compares
  tenants before returning anything. The check lives in `ApiService`, not
  in middleware, so the in-process client is bound by it too. A
  cross-tenant read or write is `403`, and the exploit the reviewers ran
  end to end is pinned as a test
  (`packages/testing/tests/test_api_auth.py::TestTenantIsolation::test_the_review_exploit_end_to_end`).
- **Turn attribution.** A turn executes under the principal's tenant. It
  previously executed under the hardcoded literal `"api"`, so every
  session opened over HTTP belonged to the same tenant regardless of who
  asked.
- **Unconfigured means closed.** With neither `REVI_AUTH_SECRET` nor
  `REVI_AUTH_DEV_TENANT` set, every `/v1` request is refused with `401`.
  The development bypass is explicit, logged at startup, and reported by
  `GET /v1/health` as `auth_mode`, so no environment can be running open
  without saying so.
- **CORS** origins are `REVI_CORS_ORIGINS` (comma-separated), defaulting
  to `http://localhost:3000`.

### Not built — name it before a security review does

- **No identity provider.** There is no user store, no login, no
  federation. Whoever holds `REVI_AUTH_SECRET` can sign for any tenant.
  Tokens are bearer credentials: not revocable before expiry, replayable
  until they lapse, and symmetric (verification implies issuance).
- **No authorization beyond tenant.** No roles, no per-metric or per-PHI
  scopes, no service-vs-human distinction.
- **No audit log.** Traces record what a turn did, not who asked for it.
  Cross-tenant refusals log a warning line and nothing more; there is no
  tamper-evident access record and no retention policy.
- **No rate limiting and no input caps.** A 2 MB utterance is accepted, as
  is a 50,000-value `in` predicate; the cardinality budget guards group-by
  cells, not predicate width.
- **PHI / BAA scope is undefined.** The catalog's `phi` labels and
  small-cell suppression are real controls on what *analysis* can reach,
  but there is no encryption-at-rest story, no BAA, no de-identification
  attestation, and the evidence cache is keyed without a tenant and has
  no TTL or purge path — PHI-derived frames accumulate with no deletion
  route.
- **The warehouse connection is read-write.** Cohort materialization
  issues `CREATE TABLE` against the analytical warehouse. That is a
  procurement fact, not an implementation detail, and it is not yet
  written into `architecture.md`.
- **Secrets handling is environment variables only.** No KMS, no
  rotation, no per-tenant keys.

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
4. **The narrative validator redacts ungrounded sentences.** M13 observed this
   live and unedited: the scripted demo narrative cited `F1/F2/F3`, referent
   handles are session-monotonic, so on T2 the same sentence cited handles
   that did not exist in that turn — and the platform redacted it rather than
   printing it: `narrative sentence redacted: cites unknown referent(s) ['F1','F2','F3']`.
   The validator was doing its job on a stale script, and the script is fixed
   now (follow-up 6): the scripted model reads the certified findings out of
   its own prompt and cites that turn's handles. The guard is unchanged and
   still covered by `packages/presentation/tests/test_narrative.py`;
   `packages/testing/tests/test_demo_narrative.py` asserts that no demo turn
   is redacted any more, which is a stronger statement than the old one — the
   validator stays armed and now has nothing to fire at.

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

**Drill-down continuity — CLOSED in the post-M13 fix pass.** M13's gap was
that a card's operators had nowhere to land: a refinement refines a parent
investigation and a portfolio card is not one, so a cold-start drill
returned `CLARIFICATION_REQUIRED`. The fix is not the portfolio-anchored
session the build plan named — that needs a hidden parent per surface — but
the **typed first turn**: `TurnRequest.spec` carries a
`TypedInvestigationSpec`, a turn carrying one is `NEW_INVESTIGATION` by
construction with no parent and zero model calls, and `AnomalyCard.drill_spec`
is exactly that spec (design in `docs/architecture.md`). Chart drills from a
fresh session get the same machinery; nothing about it is portfolio-specific.

The three pinning tests are un-inverted and now assert the working flow
(`test_api_reference.py::TestPortfolioDrillDown`):
`test_cards_carry_a_complete_typed_drill_handle` (over **every** card),
`test_drilling_a_card_from_a_fresh_session_answers`,
`test_a_card_drill_is_an_anchor_later_refinements_can_land_on`. Observed live
over real HTTP against a fresh session — `llm_calls=0`, header
`2026-06-25..2026-07-25 (remit) · filters: payer eq [Federal Medicare];
service_line eq [Imaging] · watermark wm_003`, finding
`F1 Federal Medicare / Imaging: $35,515 denied dollars [direct]`, and a
following `remove_filter` landing on that investigation as an ordinary
refinement.

**Residue, stated plainly: 6 of the 33 cards answer today.** The other 27
return typed §12 errors — and every one of them is a pre-existing pack↔catalog
content gap the anchor made visible rather than a defect in the anchor:

| Cards | Metric(s) | Refusal |
|---|---|---|
| 9 | `denial_rate` | `DATE_BASIS_INVALID` — its primary basis `remit` is not bound at its own `claim` grain (`warehouse/catalog/date_bases.yaml`), so it can never be probed on it. Pinned by `test_denial_rate_cannot_be_probed_on_its_own_primary_basis`. |
| 12 | `avg_days_to_pay`, `bill_lag_days`, `charge_lag_days`, `credit_balance_dollars`, `gross_collection_rate`, `late_charge_pct`, `underpayment_variance` | `UNSUPPORTED_CONCEPT` — measure fields (`payment_lag_days`, `submission_lag_days`, `charge_entry_lag_days`, `credit_balance_cents`, `payment_cents`, `late_charge_cents`, `underpayment_cents`) that the catalog does not define at those entities. |
| 6 | `dnfb_dollars`, `timely_filing_at_risk_dollars` | `UNSUPPORTED_CONCEPT` — contract-internal `filtered:` predicates over `discharge_date` / `submission_date`, which are base-view columns, not catalog dimensions (the same class as the exclusions defect; see follow-up 3). |

All three rows are catalog work, deliberately out of scope for this pass.
A refusal with a stable code is the designed behaviour; an empty answer would
not be.

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

`pnpm build` and `pnpm test` pass (208 tests). **Post-M13 addition:** the real
`ApiDriver` is now driven against this same running server by
`apps/web/src/lib/liveApi.test.ts` — T1 over SSE (13 stage frames, header,
F1/F2/F3 at the answer-key impacts, 4 charts, a narrative citing F1 with no
redaction), a portfolio card drilled from a **fresh** session (zero model
calls, header at `wm_003` with `payer`/`service_line` chips, a certified
finding), and a clarification rendered as a clarification — all with **zero
contract drift**. A browser-driven verification of the rendered UI was still
not performed; the driver, the parser and the store are covered, the pixels
are not.

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

## Findings and follow-ups

Items 1, 2, 3, 5 and 6 below were **fixed in the post-M13 fix pass**; the
notes are kept and rewritten rather than deleted, because what the fix was
matters as much as that there was one.

1. **Portfolio drill-down anchor — FIXED.** Item 10's gap, closed by the typed
   first turn rather than by a portfolio-anchored session (design in
   `docs/architecture.md`; residue table under item 10).
2. **The blocking-JSON turn body did not map to the UI's parser — FIXED, and
   it was worse than the note said.** Publishing the 200 body made the
   mismatch visible, but the mismatch was not confined to it: the UI's
   required-field tables named the UI's own vocabulary throughout
   (`finding.referent.value` vs the wire's `"F1"`, `charts` vs `chart_specs`,
   `header.grain.entity`, which is neither published nor rendered), and its
   fixtures were hand-written in that same vocabulary — so the suite was green
   while *no* live frame could be read. `lib/contract.ts` is now a real
   wire → UI seam with every reduction named; `contract-expectations.test.ts`
   runs against payloads captured from a running server; and
   `lib/liveApi.test.ts` drives the real `ApiDriver` against a real API
   (skipped unless `REVI_LIVE_API` names one). Two live-only bugs fell out on
   the way: typed refinements were being posted in the UI's PascalCase
   spelling (a 422 against a conforming server), and the engine published no
   `execute` stage frame at all, so a streaming client watched the rail stall
   on "validate" for the whole warehouse round trip.
3. **Exclusion polarity — FIXED, and it was seven contracts, not two.**
   All seven of `base-rcm`'s `exclusions:` clauses were authored as inclusion
   predicates (`exclusions` compiles to `FILTER (WHERE NOT …)`). Six named
   non-catalog dimensions and were inert; `denials_unworked_pct` named a
   certified one, executed, and had been reporting over patient-responsibility
   denials only — 41/62 = 66.13% where the contract means 1182/1520 = 77.76%
   at `wm_003`. The six were removed with their populations restated, the
   seventh repaired at v2, and a pack↔catalog conformance guard now fails
   startup on the class (`revi_pack.conformance`). `clean_claim_rate` executes
   for the first time: 13,725/18,410 = 74.55%. The guard catches six of the
   seven and **cannot** catch the live-and-wrong one — an exclusion whose
   dimension resolves is indistinguishable from a correct one; polarity is
   pinned in executed numbers instead. Residues, all enumerated in
   `packs/base-rcm/NOTES.md`: `first_pass_yield` and six other contracts stay
   unanswerable on `filtered:` predicates over uncertified columns, and
   `denial_rate`'s `remit` primary basis is unbound at claim grain.
4. **`drop_expired_cohorts` is process-local.** (Still open.) The DuckDB
   connector tracks materialised cohorts in an in-process dict, so a freshly
   started `make sweep` reports "dropped 0" meaning "this process materialised
   nothing", not "no stale tables exist". The CLI says so in its own output
   rather than implying the warehouse was cleaned. A real cron sweep needs the
   connector to persist that registry or expose a list-cohort-tables
   primitive.
5. **All portfolio cards reported `age_days: 0` — FIXED.** Onset now comes
   from the source: `detected_anomalies.window_start` *is* this feed's onset,
   and `DuckDbAnomalySource` publishes it as the evidence fact the formula
   already read (a feed that states its own `onset_date` still wins). The 33
   cards at `wm_003` span 28 distinct ages from 1 to 155 days (median 38), and
   flattening the onsets demonstrably reorders the portfolio.
6. **The scripted demo narrative cited fixed referent handles — FIXED.** The
   scripted model now reads the certified findings out of the rendered
   `compose_narrative` prompt and templates from that turn's own handles and
   grades, stating no free numbers and no proper names. Every demo turn
   validates with zero redactions.
7. **`ruff format --check` reports 78 pre-existing unformatted files.** (Still
   open.) `make lint` runs `ruff check` (clean) and does not gate on
   formatting. Left alone deliberately: reformatting the repo mid-milestone
   would bury the diff.

### Newly enumerated, not fixed

8. **`denial_rate` cannot be probed on its own primary basis.** It is
   `entity_grain: claim` with `primary_date_basis: remit`, and
   `warehouse/catalog/date_bases.yaml` binds REMIT only on
   `remit`/`transaction`/`denial`. `clean_claim_rate` has the same latent
   problem one level down: it lists `remit` as an *allowed* alternate at claim
   grain. Pinned by
   `test_duckdb_contract.py::test_denial_rate_cannot_be_probed_on_its_own_primary_basis`.
   The obvious next conformance check — every declared date basis must be
   bound at the contract's own entity grain — trips exactly these two.
   Deliberately not implemented here: the fix is a real modelling decision
   (rebind `denial_rate` to denial grain, or bind REMIT on claim) and belongs
   with the catalog work, not with a guard that would fail startup on content
   nobody can fix in this pass.
9. **Six metric contracts reference measure fields the catalog does not
   define** at their entity (`payment_lag_days`, `submission_lag_days`,
   `charge_entry_lag_days`, `credit_balance_cents`, `payment_cents`,
   `late_charge_cents`, `underpayment_cents`, plus `first_pass_paid` as a
   filter dimension). They prune to `UNSUPPORTED_CONCEPT` rather than
   answering. Enumerated in `packs/base-rcm/NOTES.md`; all catalog work.
10. **`FindingPayload` publishes no direction-of-good.** It lives on the
    metric contract (`sign:`), so the UI cannot tone-colour a delta in API
    mode and withholds the colour rather than inferring it from the sign of
    the number. Publishing it per finding is a small server change nobody has
    needed yet.
