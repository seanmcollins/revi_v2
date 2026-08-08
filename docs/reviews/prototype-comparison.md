# Revi v2 vs. the original prototype — an honest comparison

**Subjects.** *v2* is this repository (`/Users/dev/revi_v2`) at the state reviewed in
`docs/reviews/round1/SYNTHESIS.md`. *v1* is the original prototype — a separate, older checkout on
this machine, read-only for this exercise and not modified. Its path and its former project name are
deliberately omitted: a lint guard in `make lint` fails the build if the legacy name appears anywhere
in this tree, and that guard is correct.

**Method.** Both trees were read directly. v2 counts come from the working tree, `make`-equivalent
commands, and the round-1 adversarial synthesis (10 candidate defects, 9 CONFIRMED / 1
PARTIALLY_CONFIRMED / 0 REFUTED). v1 counts come from its own tree, its committed eval corpus files,
and its handoff/eval/semantic-definitions documents. Where v1's documents state a count, it was
re-derived from the files rather than quoted.

**Measured baselines.**

| | v2 | v1 |
|---|---|---|
| Python source | ~25,300 LOC across 16 packages + 2 apps + generator | ~50,200 LOC in one backend package |
| Frontend | 13,162 LOC (Next.js / Tailwind / shadcn) | 14,984 LOC (Vite dashboard, Workers build target) |
| Tests collected | **633** (649 collected, 16 deselected: `live_llm`, `postgres`) | **2,425** backend tests |
| Reference/regression suite | 57 tests marked `reference`, pinned to a generated answer key | 348 scored eval cases (325 public / 23 held out) + 8 mapping cases |
| Warehouse / data | 5 entities, 3 snapshots, 120,370 claims · 298,969 lines · 284,531 transactions · 110,566 remits · 7,934 denials · 33 planted anomalies; 146 MB DuckDB | uploaded Parquet at one claim grain; 24 concepts / 84 aliases; 5 committed SQLite planes |
| Governed content | 27 metric contracts, 8 playbooks, 90 concepts, 55 knowledge cards, 19 benchmarks | 9 metric calculators, 189 knowledge records (live edition), 3 versioned artifact kinds |
| Model boundary | 1 adapter (Claude Agent SDK), 322 LOC, 4 prompt templates | 6 providers behind one Protocol, 3,508 LOC |

---

## 1. Scored comparison

Scores are 1–5 per side, judged on **what the code does today**, not on what either design intends.
Neither column is a grade for effort.

| # | Dimension | v2 | v1 | Evidence |
|---|---|:--:|:--:|---|
| 1 | **Architecture** | **4** | **3** | **v2:** a typed investigation algebra over a versioned kernel — `revi_kernel` owns evidence frames, two-axis grades, probes, cohorts, money and watermarks; capability packages never import each other, only `revi_kernel` and each other's `*-contracts`; import-linter runs 6 contracts with `include_external_packages = true` and vendor isolation per package; strict mypy over 115 files. Docked one point because the headline `entrypoints → application → domain` rule in `docs/architecture.md` has **zero** mechanical enforcement (no `type = "layers"` contract exists), and `packages/investigation/src` is 6,504 LOC — a monolith wearing a package boundary. **v1:** genuinely clean `domain/services/adapters/api` layering with exactly one frozen composition root, and `make arch-check` enforces import direction *plus* a domain-vocabulary denylist (core packages may not learn RCM words) *plus* a vendor-token rule — three mechanical claims v2 does not make. But the thing being layered is a metric registry and a retrieval pipeline: 9 hand-written calculators, no algebra, no operator kernel, no plan/probe separation, and a 3,508-line provider module. |
| 2 | **Conversational model** | **4** | **2** | **v2:** 12 closed refinement operators, parent-child investigation lineage, cohort pinning, watermark epochs with an explicit re-anchor path, session-monotonic referent handles, replay determinism proved by byte-identical plan hashes across cold processes (`5b18277f1caa`, `d7ed10110c81`, `de8a6cb741e4`), and `TurnRequest.spec` — a typed first turn with `llm_calls: 0` that collapses card drills, chart clicks and saved views into one pipeline. Docked because the reconciliation invariant is **not** actually held: `reconciliation` returns `None` from four silent paths, `null` means both "never checked" and "checked and passed", and `apiDriver.ts` never reads the field at all (D8). **v1:** the client resends the whole transcript; the server persists nothing. A context-dependent turn is rewritten by a model-backed `FollowUpResolver` and the rewrite is shown to the user as "Interpreted as: …". That is honest and it is genuinely a PHI posture (§6 below) — but there is no investigation object, no lineage across turns, no replay, and no way to edit a prior answer rather than restate it. Its one advantage over v2: the continuation behaviour is *scored*, by 10 gated conversation cases in the eval suite. |
| 3 | **Data model depth** | **5** | **2** | **v2:** five entities (`claim`, `claim_line`, `transaction`, `remit`, `denial`) with curated pre-joined base views per snapshot, five date bases (SERVICE / POST / SUBMISSION / REMIT / DISCHARGE) bound column-by-column *per entity* so an unbound basis is a typed refusal rather than a wrong number, certified vs. uncertified dimensions driving the DISCOVERY-grade downgrade, certified join paths the compiler may not exceed, three watermarked snapshots, and 33–34 planted anomalies with a committed answer key. **v1:** every one of its 9 metrics is at grain `claim`; there is no second grain, no date-basis concept, and no watermark. The honest counterweight is large and belongs here: v1 ingests an **arbitrary customer export**, profiles it, maps columns to concepts, and derives `supported_calculators(mapped_concepts)` — so it can answer over a file it has never seen. v2 has no ingestion of any kind; its depth is depth over data it generated itself. |
| 4 | **Knowledge base** | **2** | **4** | **v1 is ahead, clearly.** v1 serves 189 records as `knowledge_pack` v6 through a two-plane split (mutable authoring store, immutable serving store) with exactly one legal crossing: `submit → evaluate → activate`. Approval binds to `candidate_hash` — change one served word and the approval goes stale and promotion refuses; the sole writer of an approval row is an admin endpoint that requires a human-entered name; the ingest pipeline can only write `pending` and structurally cannot publish; a `robots.txt` disallow has no override flag; a card with no labelled or human-declared effective date is refused. Activation records a named approver and hot-swaps without restart; rollback is one step and deliberately ungated. **v2** has 90 concepts, 55 cards and 19 benchmarks — real content, cited, cohort-labelled, caution-annotated — and every card carries `review_status: machine_researched` with the promotion machinery deferred to Phase 4, i.e. nothing exists. Worse, the content is not reachable: `assembly.py:127` passes `benchmarks=()` and `TurnOutcome` has no benchmark field at all, so 19 sourced figures reach no user (D9); and the DEFINITIONAL path is an O(1) normalized-alias dictionary lookup over `concept_for_alias` / card alias index — exact match or nothing. There is no retrieval, no ranking, no abstention floor, and no synthesis over the 55 cards. |
| 5 | **Evaluation rigor** | **3** | **4** | **Different kinds of rigor; v1 ahead on the one that matters more right now.** v1 runs a scored gate (`make eval`) over 348 cases: membership in the held-out partition is *derived* (`sha256(case_id) % 100 < 30`) and the loader raises if the file disagrees; machine-authored cases are forced public; the stored report drops held-out question text so artifact history cannot be mined for it; hard failures (citation infidelity, uncited claims, forbidden citations, effective-date violations, unsafe calculator dispatch) fail on one occurrence and can never be baselined away; scored metrics gate against a committed baseline; the gate fails on `PACK CHANGED`; and `test_every_committed_card_is_exercised` fails if any served record lacks a reference question. Known failures are declared with class and owner (28, eight classes) and still count in every denominator. v1's ceiling: real-provider runs are explicitly **not** gated, so the gate measures the deterministic router and retriever, not the model. **v2** is ahead on a different axis — property tests via Hypothesis across 6 files covering kernel laws (money, scope, grades/filters/probes), operator algebra (compare antisymmetry, decompose additivity in exact cents, top-k truncation flagging, deterministic rank ties), refinement laws, and store round-trips; a 9-behaviour `AnalyticalRepositoryContract` that a second backend would run unchanged; contract tests parametrised over in-process *and* HTTP transports asserting identical plan hashes; and 57 reference regressions against a committed answer key. But there is **no eval corpus, no scored gate, and zero measurement of the probabilistic layer** — and the scripted model is first-match-wins substring matching, so every refusal test in the review was structurally guaranteed to pass (J4). |
| 6 | **Trust machinery** | **4** | **4** | **Roughly even, on opposite halves.** v2's strength is *grounding of computed evidence*: two-axis grades (certification from the catalog, binding strength from the pack, weakest-wins), a lineage DAG with per-probe 64-char hashes, cache-hit flags, stated purpose and per-probe grade; a narrative validator that redacts sentences citing referents the turn does not own; a payload guard rejecting connection strings, file paths, credentials, SSNs and raw tabular payloads before any prompt; a catalog whitelist so strict that six independent attempts to reach `patient` / `mrn` / `claim_id` / `npi` through the typed spec all refused; small-cell suppression and deterministic masked row sampling. v1's strength is *grounding of prose and of PHI*: `_validated_answer` enforces server-side that every `fact`/`policy` claim cites an ID the retriever approved for **this** question, and a violation is not repaired — the answer is replaced by a deterministic fallback and the provider status says so; `_reject_sensitive_payload` runs on every provider call before any spawn; telemetry scrubs then fingerprints (typed placeholders, fail-closed over-matching, salted hash, off entirely without a salt) and the raw question is never persisted, not even transiently; transcripts are ephemeral by construction. Both lose points for the same thing: **no authentication or tenant isolation anywhere**. In v2 that is an unqualified defect (D3: cross-tenant read *and write* reproduced live); in v1 it is a stated local-first posture with `allow_origins=[]`, which is defensible for a workstation artifact and not for a deployment. v2 additionally carries an evidence cache with no tenant key, no TTL and no purge path — PHI-derived frames accumulating forever, with the omission baked into the port signature. |
| 7 | **LLM boundary design** | **3** | **5** | **v1 is ahead by a wide margin on everything except schema discipline.** Six providers (mock, two CLIs, Anthropic API, Bedrock, OpenAI-compatible) behind one Protocol, with `make arch-check` failing on a vendor token anywhere outside the boundary module; a `RetryPolicy` that retries only genuinely transient transport conditions (a schema-validation failure is a model problem and is never retried), honours server `Retry-After` under a cap, and is bounded by the overall timeout so a retrying provider can never take longer than a non-retrying one; a deterministic transport unwrap at the adapter boundary instead of an envelope-repair prompt; and provider-efficiency telemetry that is genuinely unusual — `billed_input_tokens` separated from first-turn prefill after discovering the CLI reports the *uncached remainder* (a 5.2× understatement), prompt-cache read/creation counters, `calls_reporting_cost`, and `calls_with_retry_turns` after finding the CLI silently regenerated whole documents and burned 49–91% of billed output tokens invisibly. Model pinning, `--effort` passthrough, strict tool schemas, and schema-on-the-wire deduplication all came out of measured spikes. **v2's** discipline at the *validation* seam is equal or better — closed 12-operator union, extra keys rejected, every returned id post-validated against the live pack and catalog, prompts carry vocabulary not rows, no LLM arithmetic ever. But the operational envelope is empty: grep across the adapter and the LLM application layer for `timeout|circuit.?breaker|retry|backoff` returns **zero hits**; there is no per-call timeout, no retry, no circuit breaker, no per-tenant spend ceiling, no prompt caching (`cache_control` appears nowhere, so the full vocabulary prompt is resent at list price every call), and CI never runs the live model (D10). |
| 8 | **UI** | **3** | **4** | **v2's app is the more elevated artifact and the less connected one.** v2 publishes `contracts/openapi.json` as the frontend's pinned contract — including `ErrorEnvelope` on every `/v1` route and a `TurnStreamEvent` schema for the SSE frames a naive export would leave untyped — generates `lib/types.gen.ts` from it, and holds the wire→UI vocabulary reduction in one named place (`lib/contract.ts`), guarded by parser tests against payloads *captured from a running server* plus a live-API suite checked in and skipped unless `REVI_LIVE_API` names one. That is a better contract story than v1's. What undermines it: the default driver is `mock` (1,026 lines of fixtures), and `grep -n "metric\|interpretation" apiDriver.ts` returns nothing — the governed-metric badge and the show-the-interpretation panel never render in API mode; feedback tells the user "recorded against this trace" and mutates an in-memory map; `recoverable_cents_estimate`, `actionability_rationale`, `age_days`, `priority_score` and `compliance_floor_applied` are all on the wire and none reach a component; the reconciliation banner is wired only to demo data. **And the review's own gap: nobody drove the app in a browser.** All five personas drove the API over HTTP and read source. Every UI claim in this document — v2's and the review's — is a claim about code, not about a rendered screen. **v1** went through four hostile review rounds with a scripted board demo (the VC persona broke 4 of 8 beats), keeps its trust surface — citations, effective dates, accuracy boundaries, "Deterministic values" badges, the benchmark panel beside the computed number — rendered per answer and never demoted by policy, and drift-checks its generated client in both directions inside `make test`. Its designer persona regraded it B− on a new accessibility axis, which v2 has not been measured on at all. |
| 9 | **Operational readiness** | **3** | **4** | **Both are local-first; v1 is closer to *runnable*, v2 is closer to *the right shape*.** v1: one-command setup, fully offline (`make test` and `make eval` open no sockets; the mock provider and committed seed edition make a fresh clone functional with no keys), a backup command covering all five SQLite planes plus the Parquet directory, a documented restore/corruption procedure, one-step rollback, retry counters on `/api/health`, and CI that runs `make test && make eval && make arch-check`. Its ceilings are real: SQLite, single tenant, no auth, no hosted deployment, and the working data planes committed to git — so a test run dirties tracked databases and a data-plane diff is not evidence anyone changed anything deliberately. v2: Postgres 16 with alembic migrations, docker-compose, a CI matrix that provisions Postgres, generates the warehouse, and runs lint + strict mypy + the default and `postgres` suites. Against that: no auth, no tenant isolation, no rate limiting and no input caps (a 50,000-value `in` predicate bought ~8.8s of engine work per unauthenticated request); zero hits for prometheus/otel/statsd/datadog/sentry anywhere; `/v1/health` returns ok unconditionally with no liveness/readiness split; idempotency is check-then-act and produced 6 distinct investigations from 6 concurrent identical requests; and the cohort write path leaks — 214 orphan tables / 11.9M rows / 145 MB in a development warehouse, with a sweep that is architecturally unable to reach any of it because the registry it reads is a process-local dict (D6). That last one also surfaces an undisclosed procurement fact: **v2 requires `CREATE TABLE` on the customer's analytical warehouse**, and `architecture.md` documents only the read path. |
| 10 | **Domain-pack governance** | **4** | **4** | **Even, and complementary — this is the strongest argument for the port-back list.** v2 governs *composition and integrity*: overlay merge with a closed legality policy (an overlay may add new ids and patch aliases or tune detector thresholds within declared bounds; redefining a concept, metric, card, benchmark, playbook, conclusion policy or filing rule raises `PolicyDeniedError`), contract fingerprints under a §5.2 rule (same id at the same version must have the same fingerprint; a different version must differ — meaning is never silently overwritten), a pack snapshot content hash pinned on every investigation and stamped on every definitional answer, and `validate_pack_catalog_conformance` called unconditionally at startup so a pack whose exclusions the catalog cannot carry **refuses to boot**. That guard exists because all seven `exclusions:` clauses shipped inverted, six inertly, and `NOTES.md` says so out loud including the class the guard cannot catch. v1 governs *lifecycle*: content-addressed artifact versions, hash-bound eval reports, named approvers on activation, activation-observer hot-swap, one-step ungated rollback, revocation that reports whether served content is affected, and history that is never rewritten so past answers stay attributable. v2 is docked for having no lifecycle at all and for `anomaly_actionability.yaml` — the 0.60-weighted judgement that orders the worklist — loading straight off disk at `wiring.py:257`, outside `pack_snapshot_id`, un-overlayable, un-replayable, with a `content_hash` that has no consumer anywhere. v1 is docked for having no overlay/tenant composition model of v2's kind and no semantic catalog to conform against. |
| | **Total** | **35** | **36** | Near parity, achieved on opposite halves of the problem. |

### Reading the total

The tie is the finding. v2 wins architecture, conversation, data model and (marginally) contract
discipline; v1 wins knowledge, evaluation, the model boundary and lifecycle governance. Neither
system is a superset of the other, and the rewrite did not so much replace v1 as trade one set of
solved problems for another. v2 built the machine that computes trustworthy numbers. v1 built the
machinery that decides whether content is allowed to be published and measures whether the system is
getting better or worse. v2 currently has no answer to either of those last two questions.

---

## 2. The "worth porting back" shortlist

Ranked by **value ÷ effort**. Effort is to port the *pattern* — the design and its invariants — not
the code; the two codebases share no runtime, no storage layer and no provider abstraction, so
nothing here is a copy-paste.

### 1. Provider retry/timeout policy + provider-efficiency telemetry — **S/M effort, highest ratio**

**What it is.** In v1, `providers.py` carries a `RetryPolicy` (bounded attempts, exponential backoff
with a cap, server `Retry-After` honoured under a ceiling) whose classifier retries *only* genuinely
transient transport conditions — a schema-validation failure is a model problem and is never retried
— and whose total budget is bounded by the overall call timeout, so a retrying provider can never
take longer than a non-retrying one. Alongside it, a measured telemetry vocabulary:
`billed_input_tokens` distinguished from first-turn prefill (added after discovering a CLI reported
the *uncached remainder* as `input_tokens`, a 5.2× understatement), prompt-cache read/creation
counters, `calls_reporting_cost` (so a zero total is distinguishable from "nobody reported"), and
`calls_with_retry_turns` (added after discovering the CLI silently regenerated whole documents 2–4
times per synthesis, burning 49–91% of billed output tokens invisibly).

**Why v2 lacks it.** v2 has exactly one adapter and treats the SDK as reliable. Grep across
`packages/adapter-claude` and the LLM application layer for `timeout|circuit.?breaker|retry|backoff`
returns zero hits; the installed SDK contributes only a 60s subprocess-handshake timeout and no
wall-clock query bound. The cost cap in `config.py` concedes in its own comment that it is enforced
by the CLI between turns and should be treated as a soft cap. This is D10 verbatim.

**Effort.** S for the timeout + bounded retry + transient classification (the port is one dataclass
and one wrapper around `LanguageModelPort.structured`). M to add per-tenant/day spend accounting,
prompt caching, and a `live_llm` CI job publishing p50/p95 and cost per turn. The design decisions
are already made and already justified by measurement in v1 — that is what makes this cheap.

### 2. Scored eval gate + reference-question corpus + derived held-out partition — **M harness / L corpus, highest absolute value**

**What it is.** Four separable pieces, each portable on its own:

- **Cases as data, scoring as code.** `cases/*.json` hold the questions; `evaluation/scoring.py` is
  where "correct" is defined. Adding a case is a review of product judgment, not a code change.
- **A derived held-out partition.** Membership is `sha256(case_id) % 100 < 30` — the derivation
  decides where a case must live, the file records where it does, and the loader raises if they
  disagree, so a badly-scoring case cannot be quietly relocated into the set nobody reads. Three
  further layers: separate files, a loader that refuses the held-out path rather than taking a flag,
  and stored reports that drop held-out question text so artifact history cannot be mined for it.
  `authored_by` is required with no default, and a machine-authored case can never enter the held-out
  partition regardless of its hash.
- **Hard failures vs. gated metrics.** Invariant violations fail the run on one occurrence and can
  never be baselined away; scored metrics gate against a committed baseline that only a mock-provider
  run may update, with the score movement explained in the commit.
- **Coverage as a rule.** `test_every_committed_card_is_exercised` fails if any served record lacks a
  reference question that needs it.

**Why v2 lacks it.** v2 has 633 tests and 57 answer-key regressions, which measure the *deterministic*
plane thoroughly and the *probabilistic* plane not at all. Its scripted model is first-match-wins
substring matching, so paraphrases of the flagship question fall off a cliff to
`clarification_required` and every refusal test passes by construction (J4). v2's own LLM spike
already found the failure mode this gate would catch: before a steering sentence was added, **all
four trials compiled "the top two payers" to `AddFilter` instead of `DrillInto`** — schema fidelity
perfect, operator choice wrong. That makes **operator choice** a graded axis v2 needs and v1 never
had to measure.

**Effort.** M for the harness and partition machinery. L for the corpus — but it is incrementally
useful from the first 50 cases, and the review records an offer from the RCM-executive persona to
supply real utterances. Note the trap v1 documented: a *ratio*-based held-out floor couples public
growth to human-authored growth and freezes the suite; use an absolute floor.

**Corollary worth taking with it:** the coverage rule is the mechanism that would have caught
`benchmarks=()`. Nineteen authored benchmarks unreachable by any code path is exactly the defect
"every governed card must have a case that needs it" is designed to make impossible.

### 3. Telemetry scrub-then-fingerprint — **S effort**

**What it is.** The raw question enters `record_interaction`, is used to derive exactly two values —
a scrubbed string and a salted HMAC — and is not passed on; the sink has no parameter that could
carry it. The scrubber is deterministic, local, consults no model, and **fails closed**: every
pattern over-matches on purpose, because an over-redacted question makes a dashboard blurrier while
an under-redacted one writes patient identifiers to a file that gets backed up and grepped by people
who were never told it might contain PHI. Placeholders are *typed* (`[CLAIM]`, `[MRN]`, `[DOB]`)
rather than uniform, so scrubbed text still clusters; no placeholder contains a digit, which is what
makes the pass idempotent. Telemetry is off entirely unless a salt is configured. The known gaps are
enumerated in the tests, on the grounds that a suite claiming completeness would be the most
dangerous artifact in the repository.

**Why v2 lacks it.** v2 has no telemetry at all — zero hits for prometheus/otel/statsd/datadog/sentry
across every `pyproject.toml`, three stdlib loggers with no structured formatter and no correlation
id. So this is not a replacement, it is a precondition: the moment v2 adds observability over a PHI-
adjacent question stream it needs this policy in place, and retrofitting a scrubber onto a log that
already exists is strictly worse than shipping them together.

**Effort.** S. It is one module, one salted-hash decision, and a test file that documents its own
gaps.

### 4. Retrieval + citation-allowlist synthesis over the governed cards — **M effort**

**What it is.** Three layers v2 does not have:

- **Deterministic lexical ranking** (BM25F over a `topic` field — title, domains, intents, aliases —
  and a low-weighted `body` field), with the *aboutness* rule: a document scores only when the
  query's terms reach its **declared** topic, never its prose alone. Multi-word aliases index as
  phrases only, because indexing them as loose unigrams let a machine-learning question match a
  price-transparency card through the borrowed token `machine`. An identifier veto abstains rather
  than ground a question about one code in a different code's card.
- **Three abstention floors and a bounded context budget**, including a *second, higher* floor
  separating "may this card enter a context pack the model reads?" from "may this card be rendered,
  verbatim and cited, as the product's answer?"
- **A server-side citation allowlist.** The model receives only the retrieved entries, never the
  corpus, and every `fact`/`policy` claim must cite an ID the retriever approved *for this question*.
  A violation is **not repaired** — the answer is replaced by a deterministic fallback built from the
  retrieved entries and the provider status says so. Prior conversation turns are context, never
  citable authority.

**Why v2 lacks it.** v2's DEFINITIONAL path is an exact normalized-alias dictionary lookup
(`concept_for_alias`, plus a card alias index with uniqueness enforced among cards). It is correct,
fast, zero-probe, and — per the RCM-executive persona — the single most differentiated thing in the
product today. It is also all-or-nothing: a question phrased in words that are not an alias gets
nothing, and 55 authored cards with summaries, key points and cautions can only ever be reached by
name. The analyst persona measured the shape of this: 84 of 134 shop-floor terms resolve, and the
synonym index that would widen it (`dimension_for_synonym`) is defined, unit-tested, and never called
from production.

**Effort.** M. The corpus is small (55 cards + 19 benchmarks), so the retrieval side is a few hundred
lines of pure Python with no index infrastructure. The allowlist validator is the load-bearing half
and it is small. Keep dense/semantic scoring off, as v1 did deliberately: it is structurally worse at
refusing, and refusing well is this product's entire thesis.

### 5. Ingest governance — named-human approval bound to a content hash — **M/L effort**

**What it is.** The pipeline produces *candidates* and structurally cannot publish. The machine may
fetch (robots-checked, rate-limited, fail-closed — a disallow has **no override flag**, asserted by
test), extract, and propose. It may not invent an effective date (a page's "Last Modified" stamp is a
fetch date wearing a policy date's clothes), may not invent routing vocabulary, and may not approve
anything: the pipeline can write only `pending`, and the sole writer of an approval row is an admin
endpoint requiring a human-entered name, with the repository itself owning the refusal. Approval
binds to `candidate_hash` — change one served word and the approval goes stale and promotion refuses.
Around it: a two-database split (authoring state is mutable by people; serving state must be
immutable and reproducible), one legal crossing (`submit → evaluate → activate`) where every gate
lives, and a one-step rollback that is *deliberately ungated* because standing a gate between an
incident and its fix is how rollback stops being one click. v1 also writes down the limit of its own
claim: the code enforces *a name*, not *a person*.

**Why v2 lacks it.** v2 has the primitives and none of the lifecycle. Content hashing, contract
fingerprints, and `pack_snapshot_id` are all present and pinned onto every answer; what is missing is
the approval row, the staleness-on-edit rule, the authoring/serving separation, and any path by which
`review_status: machine_researched` becomes anything else. `packages/pack-learning/src` is a one-line
reserved seat. `PackDelta` and `AnalystCorrection` are fully typed with zero producers, zero
consumers, zero storage and zero endpoints (J2).

**Effort.** M for the approval-bound-to-hash rule and the authoring/serving split on top of the
existing snapshot hashing. L if it is done properly, i.e. gated on item 2 — because "evaluate scores
the candidate's own content before activation" is the part that makes the lifecycle worth having, and
that requires a scored gate to exist first. **Sequence 2 before 5.**

### 6. Demand ledger — **S effort, gated on item 3**

**What it is.** An inert materialized view over telemetry that already exists: a report for humans,
never an agent trigger — no method in it can promote, activate or change routing. Its three design
decisions are the transferable part. It clusters on the **salted hash, never the scrubbed text**,
because scrubbing collapses two different patients' questions into one string and clustering on that
would silently merge distinct questions into one inflated demand signal. A cluster requires more than
one occurrence, and the threshold is always a parameter the report echoes back along with how many
singletons were excluded. And **the denominator travels with every numerator** — "12 unsupported
questions" invites reading a small count as a crisis; "12 of 40" does not. Abstention (a corpus gap,
the input to the next content wave) and fallback (a provider or quality failure) are counted from
different columns on purpose so fixing one can never silently start or stop counting the other.

**Why v2 lacks it.** No telemetry to build it on, and no place to record a retrieval gap because
there is no retrieval. But v2 *does* generate the exact signal this consumes: every
`UNSUPPORTED_CONCEPT` and `DATE_BASIS_INVALID` refusal, every clarification with an empty `options`
list, and every portfolio card that cannot drill is a recorded demand event that today evaporates.
With 27 of 33 cards failing to drill and 15 of 27 metric contracts unable to answer, a demand ledger
would have surfaced the coverage ceiling as a ranked list rather than as an adversarial review.

**Effort.** S once item 3 exists. Roughly a day, and it is the cheapest possible answer to "what do we
build next" that is not somebody's opinion.

### 7. Two small mechanical guards — **S each, small value, near-free**

- **A domain-vocabulary denylist in the boundary check.** v1's `arch-check` fails if core packages
  learn RCM words, which is how it holds its domain-neutrality claim mechanically. v2 asserts the
  same neutrality for `revi_kernel` and `revi_calculation` in prose only.
- **A `layers` contract in import-linter.** The review notes that v2's headline
  `entrypoints → application → domain` rule has zero enforcement (`type = "layers"` appears zero
  times) and that adding one is a ten-line change. Currently clean, entirely unguarded.

### Explicitly not recommended for porting

- **The single-claim-grain concept dictionary and calculator-declaration model.** v2's catalog
  supersedes it. What *is* worth taking from v1's data layer is the *ingest* half — profiling an
  arbitrary export and deriving what can be computed from what was mapped — but that is a product
  decision about whether v2 ever accepts a customer file, not a governance pattern.
- **Committing the working data planes.** v1 did this at the owner's direction for demo portability
  and documented the honest side effect: the suite dirties tracked databases, so a data-plane diff is
  not evidence of intent. v2's generated warehouse is reproducible from a seed, which is better.
- **Envelope-repair prompting.** v1 tried it and replaced it with a deterministic transport unwrap at
  the adapter boundary. Skip the intermediate step.

---

## 3. Verdict

### What the rewrite genuinely bought

1. **A typed investigation algebra with replayable state.** Twelve closed refinement operators over a
   parent-child investigation lineage, cohort pinning, watermark epochs, and byte-identical plan
   hashes across cold processes. v1's conversation is a model rewriting a question over a stateless
   single-shot pipeline; it could not have grown into this without becoming this.
2. **Real warehouse semantics.** Five entities, five date bases bound per entity, certified vs.
   uncertified dimensions driving evidence grade, certified join paths a compiler may not exceed, and
   watermarked snapshots. v1's entire analytical world is one claim-grain table.
3. **Genuine substitutability.** Ports, a 9-behaviour analytical contract suite a second backend runs
   unchanged, and import-linter vendor isolation with `include_external_packages = true`. This is what
   makes "Snowflake later" a configuration change rather than a rewrite, and it is the single most
   valuable structural asset in the repository.
4. **Typed refusal with governed error codes**, and a published contract that carries them —
   `ErrorEnvelope` on every route, `TurnStreamEvent` for the SSE frames, guarded on both sides.
   Every reviewer who tried to make v2 guess failed.
5. **Institutional honesty as a practice.** The `NOTES.md` exclusion-polarity postmortem documents a
   defect the team found itself, including the class the new guard cannot catch. Multiple hostile
   reviewers independently reproduced defects the team had already written down. That is a durable
   asset and v1 had the same habit; the rewrite kept it.

### What the rewrite genuinely lost

1. **Every mechanism that turns content into *approved* content.** No named approval, no
   content-hash binding, no authoring/serving separation, no eval-gated activation, no rollback. v2
   has all the hashing primitives and no lifecycle, which means 55 cards and 19 benchmarks are stuck
   at `machine_researched` with no defined path forward.
2. **Measurement of the probabilistic layer.** v1 scores 348 cases with invariants that can never be
   baselined away. v2 scores zero, and its scripted stand-in guarantees its own refusal tests pass.
   Every claim about how v2 interprets language is currently unfalsified rather than verified.
3. **The retrieval-grounded answering path.** v2 authored a knowledge base and then built only an
   exact-match dictionary over it. The most-praised surface in the review — the definitional path —
   is also the narrowest.
4. **The LLM operational envelope.** Six providers, bounded retry with transient-only classification,
   honest token accounting derived from measurement, model pinning. v2 has one adapter with no
   timeout, no retry, no budget and no caching, and CI has never run the live model.
5. **Ingestion.** v1 takes a file it has never seen and tells you what it can compute from it. v2 has
   no path from a real customer export to an answer, which the executive persona named as the gate on
   pilot #1.
6. **Coverage as a measured property.** "Every served card has a reference question that needs it" is
   a one-line rule that makes `benchmarks=()` structurally impossible. v2 shipped it anyway.

### The three highest-value convergence moves

1. **Put a scored gate around the probabilistic layer, with operator choice as a graded axis.** Port
   the harness pattern — cases as data, scoring as code, hard failures that can never be baselined,
   derived held-out membership with an absolute floor — and seed 150–300 utterances scored on turn
   class, metric/dimension/basis ids, **operator choice**, and refusal correctness. Everything else on
   this list is easier to justify once this exists, and item 5 (promotion) is not worth building
   without it. v2's own spike already documented the failure this catches.

2. **Give the authored knowledge a retrieval path first and a promotion path second.** Retrieval +
   citation-allowlist synthesis (item 4) makes 55 cards and 19 benchmarks reachable this quarter and
   turns the product's best-reviewed surface from a dictionary into an answerer. Ingest governance
   (item 5) is what lets any of it stop being `machine_researched`. The order matters: reachability is
   M effort and unlocks authored value that is already paid for; promotion is L effort and depends on
   move 1.

3. **Close the LLM operational envelope with the pattern v1 already measured.** Per-call timeout;
   bounded retry with `Retry-After` honoured and transient-only classification; a server-side
   per-tenant/day spend ceiling; prompt caching; honest token accounting that separates billed totals
   from first-turn prefill and reports cache reads. Then put a `live_llm` job in CI publishing p50/p95
   and cost per turn. This is the cheapest item on the list and it closes a confirmed CRITICAL.

**The one thing this comparison cannot settle.** Both systems converged, independently and from
different directions, on the same unresolved question: whether the next unit of work buys *coverage*
(catalog certification, ingestion, the metrics that appear on a board slide) or buys *the loop*
(outcome capture, write endpoints, content that improves because the system was used). v1 answered
"coverage" for four review rounds and built a content pipeline. v2 answered "correctness" and built a
kernel. Neither has built the loop — v2's `PackDelta` is typed with no producer; v1's demand ledger
is explicitly inert by design. The port-back list above makes both answers cheaper. It does not
choose between them.

---

*Written 2026-08-08 against v2 at the round-1 review state and the original prototype at its
round-4 state. Read-only on both trees; the only file written was this one.*
