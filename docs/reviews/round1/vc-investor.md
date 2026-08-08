# Revi — Seed/A diligence memo

**Reviewer persona:** healthcare / vertical-AI partner. ~40 RCM decks seen this cycle.
**Artifact reviewed:** `/Users/dev/revi_v2` @ `4a9afe6`.
**Method:** API driven live over HTTP on port 8103 (`REVI_LLM_MOCK=1`, DuckDB warehouse), 200+ turns
issued; every number below is from a command I ran or a `file:line` I read. Frontend built
(`pnpm build` → exit 0). Python suite run (`633 passed, 16 deselected in 31.10s`).

---

## Verdict

The engineering here is in the top decile of anything I diligence — typed refusals instead of
plausible lies, content-hashed pack snapshots, a mechanically enforced port boundary, and a
postmortem in `packs/base-rcm/NOTES.md` that most Series B companies could not write. But the
*product* is a governance chassis with almost no governed content wired through it. **15 of 27
metric contracts never answer under any dimension/basis combination I tried; the first drillable
card in the prioritized worklist is rank 17 of 33; the worklist can answer 10.3% of the dollars it
ranks; and the one marquee metric that does answer publishes a 49.9% denial rate where true denial
incidence in the same population is 5.2%, graded `direct` / `high` with no warning.** That last one
is the exact failure mode the entire architecture exists to prevent, and it is live today.

I would not lead this round on the current artifact. I would take a second meeting on the strength
of the velocity and the refusal discipline, and I would gate a term sheet on three things: a design
partner with a real Snowflake schema, an authentication layer, and a demonstrated path from
"analytics" to a collections number somebody will pay for.

---

## What genuinely survives scrutiny

**1. Determinism is real, not asserted.** Three independent five-turn runs against a live server
produced byte-identical plan hashes (`5b18277f1caa` on T1/T2, `d7ed10110c81` on T3, `de8a6cb741e4`
on T4) and identical impacts. `633 passed, 16 deselected in 31.10s`. Strict mypy over 115 files,
import-linter with 6 contracts. This is not a demo held together with string.

**2. The refusal discipline is the rarest thing in the repo.** Of 27 metric contracts I probed, 16
returned typed error envelopes (`UNSUPPORTED_CONCEPT`, `DATE_BASIS_INVALID`) instead of a plausible
number. Of 33 portfolio cards, 27 refused. Nonsense input ("how many purple bananas does the moon
owe us") returned `clarification_required` with `reason: "turn classification returned no
structured output"` — never a guess. Every RCM startup I have seen would have shipped a number
there. This is the thing I would underwrite.

**3. The exclusion-polarity postmortem.** `packs/base-rcm/NOTES.md:242-332` documents that all seven
`exclusions:` clauses were authored inverted, that six were inert and therefore *hiding* the bug,
that the seventh (`denials_unworked_pct`) had been publishing 66.13% where it meant 77.76%, and
that the new startup guard **cannot** catch the live-and-wrong class. That is a level of honesty I
almost never see, and it is a real signal about the founder.

**4. The definitional path works and feels differentiated.** `POST /turns {"utterance":"what is
pr3"}` → group code `PR` ("Patient Responsibility"), CARC `3` ("Copay"), paraphrased to dodge X12
licensing, stamped `base-rcm@1.0.0` + snapshot `e9fab59dd289…`, zero warehouse queries. Small, but
it is the only place in the product where the governed-content thesis is visibly true in the answer.

**5. `TurnRequest.spec` (typed first turn) is the right primitive.** A portfolio card, a chart
click, a saved view and a scheduled brief all collapse into one investigation pipeline with zero
model calls. I verified a cold-start drill: `{"spec": card.drill_spec}` → `llm_calls=0`, header
`payer eq [Federal Medicare]; service_line eq [Imaging]`, `F1 … $35,515 denied dollars [direct]`.
That is good architecture, not a hack.

**6. Velocity.** `git log` shows the whole repo — 36,527 lines of Python, 633 tests, 18 packages,
FastAPI + SSE, a Next.js app that builds, a deterministic mock warehouse, a 90-concept pack — landed
between `4cd1adb 08-07 18:12` and `4a9afe6 08-08 02:27`. Eight hours and fifteen minutes of commit
wall-clock. Whatever else is true, this founder can put enormous surface area on the table fast.

---

## Findings

### F1 — CRITICAL — The flagship denial metric publishes a number that is wrong by 9.6×, at the highest evidence grade

`denial_rate` for State Medicaid, 2026-05-01..2026-07-31:

```
F1 direct | State Medicaid ranks #1 by denial rate over 2026-05-01..2026-07-31: Decimal('0.499407').
warnings: ["alternate_basis_used: probe 'main' reads 'denial_rate' on the 'service' basis …",
           'suppression: cells counting fewer than 11 entities are suppressed …']
```

Ground truth in the same population, computed directly against the warehouse:

```
claims=3374  not_clean=1685 (49.9%)  open=1510  truly_denied=175 (5.2%)
```

1,510 claims that have not yet been adjudicated carry `clean_claim = false` and are counted as
denied. The published rate is **49.94%**; the actual denial incidence is **5.2%**. Industry initial
denial rate is ~11.6% (the repo's own `benchmarks.yaml`). The cause is documented — the
`status neq OPEN` exclusion was *removed* rather than repaired (`NOTES.md:261`, `:292-297`) — and
the contract description says so. But the description is prose in a YAML file the API never
publishes. `FindingPayload` properties are `['referent','title','statement','metric_ids','values',
'grade','impact_cents','confidence','suggested_refinements']` — no contract text, no benchmark, no
caveat. The analyst sees a `direct`/`high` number.

This is a silent failure: plausible, runs cleanly, wrong. It is the exact thing
`industry-research.md:26-29` says "kills adoption in a handful of answers."

**Recommendation:** certify `status` as a catalog dimension and restore the exclusion before any
demo. Until then, `denial_rate` should refuse rather than answer — a refusal is the product's own
stated design behaviour and it is the honest one here. Longer term: any metric whose contract
description contains a population caveat must publish that caveat on the finding.

---

### F2 — CRITICAL — Custom comparison windows are mislabeled and produce fabricated impact dollars

Turn 4 of the reference conversation ("Compare that to Q1"). The context header is honest —
`2026-07-27..2026-08-02 (post) · vs 2026-01-01..2026-03-31`, `comparison_kind: custom`. The finding
is not:

```
STATEMENT: Atlas Commercial: cash posted moved from 446272607 to 26330486 cents
           (down $4,199,421, -94.1% vs prior week).
   impact_cents: -419942121   grade: direct   confidence: high
warnings: ['suppression: …']     <- no window-length warning
```

Two defects in one card. **(a)** The label says "vs prior week" when the comparison is a 90-day
custom range. `packages/investigation/src/revi_investigation/application/findings.py:300-310`:
`_period_phrase` reads `spec.context.window.requested` — the *current* window's unit — and never
handles `ComparisonKind.CUSTOM`, so a 1-week current window against any custom comparison always
prints "vs prior week". **(b)** Seven days of cash is compared against ninety days of cash and the
difference is published as a **$4.2M impact, graded `direct`, confidence `high`**, with no warning.
On the CARC branch the same turn produces `16 denied dollars down $169,026` — a number that is
100% window-length artifact.

The narrative then says "F10 leads the certified findings on this turn." Certified.

**Recommendation:** handle `CUSTOM` in `_period_phrase`; refuse or loudly flag comparisons whose
window length differs from the current window; never set `impact_cents` on a length-mismatched
compare.

---

### F3 — CRITICAL — No authentication, no authorization, one hard-coded tenant

```
A session: {'session_id': 'sess_faa3af9c1bc6', 'tenant': 'hospital-a', …}
B session tenant: competitor-b pack: base-rcm 1.0.0
B reads A's investigation: 200 {"investigation_id": …, "question": "Why did cash decline last week?", …}
anyone reads A's lineage: 200 (1 investigation)
anyone writes into A's session: 200 answer
```

`grep -niE "auth|jwt|bearer|api_key|Depends\(" apps/api/src/revi_api/` returns nothing but CORS.
`tenant` is a free-text string on the session with zero enforcement anywhere. The pack overlay is
process-global: `apps/api/src/revi_api/wiring.py:207` hard-codes
`pack_dir.parent / "overlays" / "demo-tenant"`, so every tenant gets the same governed semantics.
`architecture.md:24` says "Role separation is deferred until multi-tenant deployment; noted here
deliberately."

For a product whose thesis is *governance is the buying gate* (`industry-research.md:38-41`), and
whose data is PHI-adjacent claim detail, shipping an unauthenticated API that leaks cross-tenant
findings is not a "later" item. No health-system security review survives this. It is also the
cheapest of the criticals to fix, which is why its absence reads as "never spoke to a buyer."

---

### F4 — HIGH — The worklist, the surface closest to collections outcomes, is 90% broken

I drilled all 33 cards from `GET /v1/portfolio/latest` using each card's own `drill_spec`:

```
ANSWERED: 6/33
first answering card is at rank 17
total portfolio impact $1,749,614; answerable impact $180,055 (10.3%)

rank | score  | impact$   | metric                    | answers?
   1 | 0.6000 |    48,939 | credit_balance_dollars    | NO  UNSUPPORTED_CONCEPT
   2 | 0.6000 |       824 | credit_balance_dollars    | NO  UNSUPPORTED_CONCEPT
   3 | 0.2726 |   170,643 | denial_rate               | NO  DATE_BASIS_INVALID
   4 | 0.2634 |   493,266 | gross_collection_rate     | NO  UNSUPPORTED_CONCEPT
   5 | 0.2310 |   178,217 | dnfb_dollars              | NO  UNSUPPORTED_CONCEPT
  … 12 consecutive failures …
```

Every card in the visible fold errors. `denial_rate` alone accounts for 9 of the 27 failures and
cannot be probed on its own declared primary basis at its own declared grain — the walkthrough pins
this as a known content gap (`acceptance-walkthrough.md:335`, `:608-620`).

The walkthrough is honest ("Residue, stated plainly: 6 of the 33 cards answer today"). But *which*
6 matters enormously and is not stated: they are ranks 17 and below. A buyer clicking the top of
the list gets an error envelope five times before seeing a number. And this is the surface that
maps to a purchase order — an RCM director buys a worklist, not a chat window.

---

### F5 — HIGH — 15 of 27 metric contracts never answer, and the dead set is the HFMA MAP Key core

Probing every contract via typed spec across `{no dims, payer} × {none, service, post, remit,
submission}`:

```
metric contracts: 27; ANSWER with findings: 11
NEVER ANSWERS: ar_balance, ar_over_90_pct, avg_days_to_pay, bill_lag_days, charge_lag_days,
  credit_balance_dollars, days_in_ar, dnfb_dollars, first_pass_yield, gross_collection_rate,
  initial_denial_rate, late_charge_pct, net_collection_rate, timely_filing_at_risk_dollars,
  underpayment_variance
ANSWERS ONLY ON AN ALTERNATE BASIS: denial_rate
```

Net days in A/R. Net collection rate. Initial denial rate. A/R over 90. DNFB. Underpayment variance.
Timely-filing at risk. First-pass yield. Those are not edge cases — `industry-research.md:15-21`
calls standardized MAP Key definitions "the price of admission," and `:143` calls timely-filing
at-risk dollars the *differentiator* ("no competitor surfaces at-risk timely filing
conversationally"). It does not surface it either.

The moat pitch is "governed RCM semantics." The measured content is 11 working metrics. A health
system will not spend twelve months building eleven metrics.

---

### F6 — HIGH — The knowledge base and the action layer are wired to nothing

`assembly.py:127`:

```python
prompt = build_narrative_prompt(findings=…, header=…, reconciliation=…, benchmarks=())
```

Hard-coded empty. And the published contract has no place to put them either — searching the live
OpenAPI document:

```
benchmark 0   Benchmark 0   knowledge 0   Knowledge 0
recommend 0   playbook 0    next_step 0
```

So commit `8a5f3f2` ("55 governed knowledge cards + 19 cited benchmark figures") is dead weight in
the shipped product. The research doc ranks benchmark chips #8 of 16 trust affordances and
recommended-action pairing #15 with the note "VisiQuate/Adonis set the expectation that numbers ship
with actions" (`industry-research.md:348-350`). Neither exists on the wire.

**This is the analytics-vs-outcomes gap, mechanically.** Revi tells you State Medicaid cash fell
$99,093 and stops. It does not tell you it is 4.4× the benchmark, it does not tell you the 8 playbooks
in the pack have a next step, and it does not put a dollar on the table you can go collect. Every RCM
analytics startup that dies, dies here.

---

### F7 — HIGH — The differentiating UI affordances exist only in the hand-written mock

`apps/web/.env.example:2` → `NEXT_PUBLIC_REVI_DRIVER=mock`; `src/app/page.tsx:31` defaults to
`"mock"` unless the env var is exactly `"api"`. In that default mode the UI is driven by
`src/lib/mock/reference.ts` — **1,026 lines of hand-written fixtures**.

Two of the three affordances the research doc calls load-bearing live only there:

- **Governed-metric badge** (`industry-research.md:302-306`: "no healthcare competitor has it").
  `MetricProvenanceBadge` needs `MetricContractSummary`. `grep -rn "MetricContractSummary" src`
  shows it is produced by `src/lib/mock/reference.ts` only. `grep -n "metric" src/lib/apiDriver.ts`
  → **zero matches**. In API mode the shield never renders.
- **Show-the-interpretation panel** (#6). `grep -n "interpretation" src/lib/apiDriver.ts` → zero
  matches; only `store.ts` handles the event nobody emits.

And the clarification path — #4, "render button options of *answerable* interpretations" — returns
`"options": []` live. Every ambiguity is a dead end.

The walkthrough is candid that "the pixels are not [covered]" (`:508`). My concern is narrower and
worse: the pixels a viewer sees by default are not connected to the engine at all.

---

### F8 — HIGH — Production latency and unit economics are unmodeled and bad

Measured LLM calls per turn against the live API:

```
Why did cash decline last week?    llm_calls=2
Break that down by payer           llm_calls=3
…CARC mix on their denials?        llm_calls=3
Compare that to Q1                 llm_calls=3
Why do you say F2?                 llm_calls=1
```

The team's own spike (`packages/adapter-claude/spikes/RESULTS.md:105`) measured **"Latency 5–15 s,
cost $0.008–$0.021 per pinned refinement call."** That is **15–45 seconds and $0.024–$0.063 per
conversational turn**; a five-turn investigation is 1.5–3 minutes of waiting and ~$0.15. The demo
answers in 10–42 ms and hides all of it.

Compounding factors: `grep -rn "cache_control\|prompt_cach\|batch" packages/` returns **nothing** —
the vocabulary prompt (90 concepts, 27 contracts, catalog dimensions) is resent at full price on
every call. And the production adapter is the **Claude Agent SDK** (`import claude_agent_sdk`,
`adapter.py:57`), i.e. a CLI subprocess per call, not the Messages API — which is why `max_turns=2`
and "structured output consumes an extra turn" are load-bearing constraints. That is a spike harness,
not a server-side inference architecture. `live_llm` tests are excluded by default
(`pyproject.toml:36`) and appear in **no** CI step, so the production LLM path has zero automated
verification.

---

### F9 — MEDIUM/HIGH — Revi ships no detection; the portfolio reads someone else's feed

`DuckDbAnomalySource` (`packages/connector-duckdb/src/revi_connector_duckdb/anomalies.py:78-103`)
`SELECT`s from `snap_00N.detected_anomalies`, a table the mock generator plants
(`warehouse/generator/src/revi_warehouse/anomalies.py`). Every card carries
`provenance: "external_detection"` — correctly and honestly.

But in production nothing writes that table. The "daily prioritization" surface therefore requires
the customer to already own a detection system — i.e. to already own Adonis, Anomaly, or Sift. Revi
is a ranking-and-explanation layer on top of a product it does not have. That is a defensible
*wedge* if positioned that way ("we make your alerts explainable and drillable"), and a serious hole
if the deck implies Revi finds the money. Right now the repo implies the latter and delivers the
former, on 6 of 33 cards.

---

### MEDIUM — F10 — The competitive research is secondary-source advocacy, and the repo has no market thesis at all

`docs/research/industry-research.md` is a genuinely useful KPI-canon synthesis. As competitive
diligence it does not hold:

- **"Revi is architecturally *ahead* of the healthcare incumbents"** (`:50-51`) is a self-assessment
  presented as a research finding. VisiQuate's Ana has shipped conversational NL since ~2018, holds
  KLAS 92.0, acquired Etyon for ML scoring and Rotera for automation, and the doc's own row credits
  it with catching a $74M auth spike "in days." Revi answers 11 metrics and 6 of 33 cards.
- **"Zero competitors do this"** (`:341`, definitional cards) and **"no competitor does governed
  definitional answers with provenance"** (`:57`) are unfalsifiable as written. Every clearinghouse
  surfaces CARC/RARC descriptions. And Revi's code coverage is 33 codes against ~400 CARCs / ~1,000
  RARCs, paraphrased for licensing reasons (`NOTES.md:162-168`) — a coverage and freshness liability,
  not a moat.
- **"a health system would spend 12+ months building the semantic model Revi ships"** (`:243`) is
  the load-bearing moat claim and F5 kills it.
- **The Epic row is the biggest risk and the weakest evidence.** `:242` dismisses Epic Resolute with
  "SlicerDicer/Reporting Workbench self-service; no NL," sourced to university Epic-support pages.
  Epic bundling conversational analytics into Resolute at zero marginal price is the single largest
  extinction risk for this company and it gets one clause.
- **Snowflake is treated as a partner and a non-competitor simultaneously.** README says the
  production backend is "Snowflake + Semantic Views"; the doc dismisses Cortex Analyst for having
  "Zero RCM domain content." But if the moat is domain YAML on top of Snowflake's semantic layer,
  the natural owner is a Snowflake Native App or a services firm — and the doc cites Hakkoda as
  *evidence of demand*, which is equally evidence that consultancies capture this value.
- **Zero primary research.** No provider CFO or RCM director is quoted anywhere. No design partner,
  no LOI, no pilot appears in the repo.
- **No market thesis exists.** `grep -niE "pricing|TAM|market size|buyer|go.to.market|ACV"` across
  `docs/`, `README.md` and the 67k-word design document returns nothing relevant. For a seed/A, the
  absence of a stated buyer, price point and wedge is itself a finding.

---

### MEDIUM — F11 — The demo does not survive contact with its own advertised entry points

`apps/web/src/lib/guideQuestions.ts` lists the product's eight canonical entry points and the file
comment claims "the live API answers them for real." Driven against the running API:

```
answer                 findings=0  What is PR3?
clarification_required findings=0  Give me my denial rate by month for the last 6 months
answer                 findings=1  Do I have a COB problem?
clarification_required findings=0  What are my top 5 issues?
clarification_required findings=0  Will my cash increase next month?
clarification_required findings=0  Assess the performance of each facility financially
clarification_required findings=0  Give me payer payments by payer category weekly over the last 3.25 months
clarification_required findings=0  Drill into Medicaid
```

Six of eight dead-end, each with `options: []`. Related demo defects I hit in the reference
conversation:

- **Turn 2 is a no-op.** "Break that down by payer" returns *identical* numbers to T1 under new
  handles (F4/F5/F6 = F1/F2/F3, same `impact_cents`, same plan hash `5b18277f1caa`). In a live demo
  the second turn adds nothing and the audience notices.
- **The narrative is content-free.** Every turn produces a variant of `"F7 leads the certified
  findings on this turn, followed by F8 and F9. Every finding here is graded direct against
  certified semantics."` By construction (`apps/api/src/revi_api/scripted_llm.py:258-295`) it states
  no numbers and no proper names. Defensible as a grounding demo; it is not evidence anyone would
  read a Revi answer.
- **Raw Python reprs ship to users.** `findings.py:509` uses `f"{value!r}"` for non-money values,
  so ratio metrics render as `State Medicaid: Decimal('0.499407') denial rate` and
  `Federal Medicare / General Surgery: Decimal('0.888889') denials unworked pct` — in the title and
  the statement, on portfolio drills and metric answers alike.
- **CARC codes render as bare integers.** T3 findings read `204 denied dollars down $12,545`,
  `2 denied dollars…`, `22 denied dollars…`. The pack knows 204/2/22 by name — the definitional path
  proves it — but the finding layer does not join it. The product's most differentiated content is
  one lookup away from its least readable screen.
- **The "why" probes are the ones that get pruned.** T1 warnings: `probe 'lag_distribution_compare'
  omitted` and `transform 'decompose' skipped`. Answer key scenario 3 says the planted causes are an
  Atlas submission deferral and a **State Medicaid posting-lag shift**. The lag probe is exactly what
  was dropped. So "Why did cash decline?" is answered with a ranked list of who declined — a
  decomposition, not a cause. Same on COB: `probe 'cob_rebill_timing' omitted`, and the answer is one
  finding (`Silverline … 153 cob mismatch claims (100.0% of visible total)`) with no rate, no
  yes/no, and no action.

---

### MEDIUM — F12 — What the demo proves vs. what production requires

Honest ledger, since the walkthrough invites one:

| Claim | What is actually proven |
|---|---|
| Snowflake swap is a config change | `AnalyticalRepositoryContract` has exactly one implementation (walkthrough `:65-67` says so). Nine behaviours × one adapter is a design claim, not a portability result. |
| Scale | 120,511 claims / 300,137 lines / 285,486 transactions / **12 payers** / 19 months, 146 MB DuckDB. A mid-size community hospital does 250–500k claims a year; an IDN does 5–20M. Cohort semi-join is capped at 100k (`/v1/capabilities`) and T3 already pins a 55,722-claim cohort — half the ceiling on a toy dataset. |
| Multi-tenant | Single process, one global pack, in-memory stores by default (`store_mode: memory`), Postgres path excluded from local runs and green only in CI. |
| Operations | `drop_expired_cohorts` is process-local, so a cron sweep reports "dropped 0" meaning "this process made nothing" (walkthrough `:583-589`, still open). No metrics, no tracing, no rate limiting, no audit log in `apps/api`. |
| Data source | Synthetic warehouse with planted scenarios and a planted anomaly table. No 835/837 ingestion, no EHR connector, no clearinghouse feed exists anywhere in the repo. |

---

## What I would need to see before a term sheet

1. **One design partner, one real schema.** Point the DuckDB port at a health system's Snowflake and
   report how many of the 27 contracts survive contact. That single number is the whole investment
   case; today it is 11-of-27 against a warehouse the team built for itself.
2. **Close the content gap before adding surface area.** Certify `status`, `submission_date`,
   `discharge_date`, `txn_type`, `first_pass_paid` in the catalog. That one change plausibly
   converts most of F5 and much of F4. Stop shipping features; ship working metrics.
3. **Fix F1 and F2 today.** A wrong number at `direct`/`high` is worse than no product. Refuse
   `denial_rate` until the population is right; handle `CUSTOM` comparisons.
4. **Authentication, tenant scoping, and per-tenant packs.** Non-negotiable, and cheap.
5. **Pick the wedge and say it out loud.** The strongest one visible here is *"explainable,
   drillable, provenance-carrying layer over the alerts you already get from Adonis/Anomaly/Waystar"*
   — it matches the `external_detection` provenance already in the model, it does not require Revi
   to build detection, and it is a real complaint about those products. The weakest is "conversational
   analytics platform," where VisiQuate has a seven-year head start and Epic has distribution.
6. **Ship the outcome, not the number.** Benchmarks and playbook next-steps are already authored and
   already discarded at `assembly.py:127`. Putting them on the wire is days of work and it is the
   difference between a dashboard and a collections product.
7. **Re-plumb the LLM path.** Messages API with prompt caching, not an Agent SDK subprocess; get a
   measured p50/p95 turn latency and a cost-per-turn on the real adapter into CI.

---

*Server used for this review was stopped afterwards. No repository files outside
`docs/reviews/round1/` were modified.*
