# Revi — the ambition/scale review

**Reviewer persona:** ambition & scale operator lens
**Commit:** `4a9afe6` · **Date:** 2026-08-08
**Method:** API driven live over HTTP on port 8105 (`REVI_LLM_MOCK=1`, `data/revi_warehouse.duckdb`),
five-turn reference conversation, all 33 portfolio drills, all 27 pack metrics probed by typed spec,
cross-tenant probes, full Python suite re-run, targeted source reads.

---

## Verdict

The engineering here is better than the product, by a lot. Somebody built a real semantic
kernel with real refusal discipline and real determinism, proved it with 633 tests that
actually pass in 31 seconds, and wrote documentation honest enough that I independently
reproduced most of its self-reported defects. That is rare and it is worth something.

But I drove this thing like a customer and the customer experience is: the daily worklist —
the actual product — cannot open 27 of its 33 items, and it cannot open a single one of its
top sixteen. Two thirds of the metric library is unanswerable, and the unanswerable two
thirds happens to be days-in-A/R, net collection rate, denial rate, and every other number a
CFO would name first. The one turn in the flagship demo that makes a real analytical move
prints a false comparison label on a certified number. And the flywheel — the thing that
would make this compound instead of just exist — is one line of Python containing the word
"RESERVED."

So: the bet is defensible, the execution of the bet is not yet a company. Right now this is a
very well-governed read-only chart tool that refuses honestly. That is a feature Epic ships in
a release, not a business. The gap between what it is and what it could be is closed by
*fewer* architecture decisions and *far* more surface: make the metrics answer, make the
worklist actionable, and instrument the loop that turns every analyst correction into pack
content. Do that in 90 days and you have something. Keep polishing the kernel and you have a
beautiful thing nobody buys.

---

## What genuinely survives scrutiny

**1. The gates are not theater.** `uv run pytest -q` → `633 passed, 16 deselected in 31.09s`,
matching `docs/acceptance-walkthrough.md`'s claim exactly. Import-linter vendor isolation and
strict mypy over 115 files are real constraints, not aspirations. Most teams at this stage are
lying about this. This one isn't.

**2. Refusal discipline is real, and it is the hardest part.** Live:
`{"metric_ids":["unicorn_rate"]}` → `200 {"outcome":"error","code":"UNSUPPORTED_CONCEPT",
"message":"typed metric 'unicorn_rate' is not in the pack","correlation_id":"corr_2df5241e412b"}`.
`"banana helicopter tuesday"` → `clarification_required`, never a guess. T1 surfaces
`probe 'lag_distribution_compare' omitted: its measures are not answerable at the source`
rather than quietly dropping it. Text-to-SQL products die on silent wrong answers; this one
structurally cannot produce them. That is the moat if anything is.

**3. Determinism is observable, not asserted.** Two independent sessions running
"Why did cash decline last week?" produced identical `plan_hash 5b18277f1caa42d1…` and
identical impacts. A portfolio drill via `TurnRequest.spec` costs `llm_calls: 0` — typed in,
typed out, no model in the loop.

**4. The documentation is unusually honest.** `packs/base-rcm/NOTES.md` and the walkthrough's
gaps section enumerate the exclusion-polarity defect, the process-local sweep, and "6 of the
33 cards answer today" — all of which I reproduced independently. Teams that write this down
fix things faster than teams that don't.

**5. The DEFINITIONAL path is a genuine, shippable differentiator.** `"what is pr3"` returns
governed `PR` (group code) + CARC `3` definitions with `pack_snapshot_id
e9fab59dd289…`, zero probes. Their own research file says no competitor does governed
definitional answers with provenance. Believe it — this is a wedge hiding in plain sight.

**6. The typed-first-turn insight is architecturally correct.** `AnomalyCard.drill_spec` *is*
a `TurnRequest.spec`, so any surface — a card, a chart, an email, eventually an agent — can
anchor an investigation with no model call and no hidden parent. That generalizes.

---

## Findings

### F1 · CRITICAL · The flagship conversation publishes a false comparison label on a certified number

Turn 4 of the reference conversation ("Compare that to Q1"). Live over HTTP:

```
header:    2026-07-27..2026-08-02 (post) · vs 2026-01-01..2026-03-31 · cohort ... · watermark wm_003
           comparison_kind: "custom"
finding:   F10 [direct/high] "16 denied dollars down $169,026 vs prior week"
statement: "16: denied dollars moved from 17051059 to 148451 cents (down $169,026, -99.1% vs prior week)."
impact_cents: -16902608
```

Two failures in one sentence, both in the deterministic plane, neither the model's fault:

1. **The label is wrong.** The header correctly says `comparison_kind: custom` over
   `2026-01-01..2026-03-31`; the finding says "vs prior week." Root cause is
   `packages/investigation/src/revi_investigation/application/findings.py:300-310` —
   `_period_phrase` branches on `PRIOR_YEAR` and otherwise falls through to
   `f"vs prior {requested.unit.value}"`. There is no `ComparisonKind.CUSTOM` branch, even
   though `derive_comparison` sets exactly that kind (`packages/kernel/src/revi_kernel/scope.py:281`).
2. **The number is meaningless.** 7 days of denied dollars are differenced against 90 days of
   denied dollars with no normalization and no warning. `-99.1%` and `-$169,026` are artifacts
   of window length, published at `grade: direct, confidence: high`.

This is the exact class of error — "wrong answer that runs cleanly" — that the entire
architecture exists to prevent, sitting in the demo you show buyers. Every trust claim in the
README is downstream of this not happening.

**Fix:** add the CUSTOM branch and render the actual range; refuse or explicitly flag a
comparison whose length differs from the primary window by more than a tolerance. Add a
reference test that asserts finding *text*, not just impacts.

---

### F2 · CRITICAL · The prioritized worklist is 82% dead, and 100% dead at the top

I drilled every card returned by `GET /v1/portfolio/latest` using its own `drill_spec`:

```
ANSWERED 6 of 33
failures: {'UNSUPPORTED_CONCEPT': 18, 'DATE_BASIS_INVALID': 9}

ANM-023  0.600  UNSUPPORTED_CONCEPT   (compliance-mandatory, top of list)
ANM-024  0.600  UNSUPPORTED_CONCEPT   (compliance-mandatory, top of list)
ANM-001  0.273  DATE_BASIS_INVALID    date basis 'remit' is not bound for entity 'claim'
... 13 more failures ...
ANM-004  0.069  ANSWER   <- the first card in the ranking that opens, at rank 17 of 33
```

The two cards the pack floors to 0.60 *because they are compliance-mandatory* both refuse. The
first item a user can actually investigate is rank 17 with a priority of 0.069 — i.e. the
system's own ranking says it is nearly worthless.

"Show me the top five things today" is the killer app in this category, and their own research
file names it (`docs/research/industry-research.md` §10.2 / §1.3). Shipping it in a state where
every high-priority item is a dead link isn't a gap — it's an anti-demo. A prospect clicks the
top card and the product says `UNSUPPORTED_CONCEPT`.

The walkthrough discloses "6 of the 33 cards answer today" and classifies all of it as catalog
work. Correct diagnosis, wrong prioritization: this *is* the product.

---

### F3 · CRITICAL · Two thirds of the metric library cannot be computed — including every KPI a buyer names first

I probed all 27 contracts in `packs/base-rcm/metrics/` with an identical typed spec
(`dimensions:["payer"]`, `2026-05-01..2026-07-31`):

```
TOTAL METRICS 27  →  {'answer': 11, 'UNSUPPORTED_CONCEPT': 15, 'DATE_BASIS_INVALID': 1}

days_in_ar             UNSUPPORTED_CONCEPT   unknown dimension 'status'
net_collection_rate    UNSUPPORTED_CONCEPT   no probe in the plan is answerable at the source
gross_collection_rate  UNSUPPORTED_CONCEPT   no probe in the plan is answerable at the source
denial_rate            DATE_BASIS_INVALID    date basis 'remit' is not bound for entity 'claim'
ar_over_90_pct         UNSUPPORTED_CONCEPT   unknown dimension 'status'
initial_denial_rate    UNSUPPORTED_CONCEPT   unknown dimension 'status'
first_pass_yield       UNSUPPORTED_CONCEPT   unknown dimension 'first_pass_paid'
dnfb_dollars           UNSUPPORTED_CONCEPT   unknown dimension 'discharge_date'
avg_days_to_pay        UNSUPPORTED_CONCEPT   no probe in the plan is answerable at the source
```

`docs/research/industry-research.md` §1.1 says the HFMA MAP Keys are "the price of admission."
Days in A/R, net collection rate, and denial rate are the three most-quoted numbers in the
category. None of them work. A CFO's first question kills the demo.

Note the scale: the whole semantic catalog is 22 dimensions, 20 measures, 5 entities, 5 date
bases. At **sixty governed objects the hand-authored layer is already 59% internally
inconsistent**. That is the scaling argument against the current authoring model, and it is
not hypothetical — it is measured.

---

### F4 · CRITICAL · The compounding loop does not exist, in any form

- `packages/pack-learning/src/revi_pack_learning/__init__.py` is **one line**:
  `"""RESERVED SEAT (design-doc Phase 4). Intentionally empty; named in import-linter contracts now."""`
- `PackDelta` (`packages/pack/src/revi_pack/domain.py:899`) and `AnalystCorrection`
  (`:934`) are fully typed and have **zero consumers** —
  `grep -rn "PackDelta\|AnalystCorrection" packages --include='*.py' | grep -v domain.py`
  returns nothing. No producer, no store, no endpoint, no test.
- Recoverability — the input to the largest weight in the priority formula (actionability
  0.60) — is hand-authored constants: `UNDERPAYMENT: 0.85`, `DUPLICATE: 0.90`, `COB: 0.70`,
  `CASH_DECLINE: 0.30`, `default: 0.50` (`packs/base-rcm/anomaly_actionability.yaml`). Nothing
  observes actual recovery and updates them.
- No outcome capture anywhere: no appeal-filed, no appeal-won, no dollars-recovered, no
  analyst-accepted/rejected. `grep` across `packages`, `apps`, `packs` finds no feedback
  persistence of any kind.

So the honest answer to "is the data flywheel real" is: **no, and nothing in the repo is
pointed at it.** Every unit of value this product creates evaporates. Session N+1 is exactly as
smart as session 1. That is the difference between a tool and a company, and it is the single
most important thing to change.

---

### F5 · CRITICAL · No authentication and no tenant isolation — any client reads any tenant's data

Live, against the running server, with no credentials of any kind:

```
tenant "hospital-A" opens a session and runs an investigation → inv_119506377cce
GET  /v1/investigations/inv_119506377cce            → 200, full findings + dollar figures
GET  /v1/sessions/sess_c15e24487c0e/lineage         → 200, 1 investigation
POST /v1/sessions/sess_c15e24487c0e/turns           → 200, answer  (writing into A's session)
GET  /v1/portfolio/latest                           → 200, no tenant parameter exists
```

`grep -riE "auth|bearer|jwt|Depends\(" apps/api/src/revi_api/*.py` returns nothing but
docstring prose. `docs/architecture.md` says "Role separation is deferred until multi-tenant
deployment; noted here deliberately" — but there is no auth layer at all to defer roles
*within*.

Their own research file §1.4 says "Governance is the buying gate, not a feature," and cites
that only 18% of health systems have mature AI governance. You cannot walk into that room with
an unauthenticated API over revenue data. This is a hard blocker on pilot #1, not a hardening
task.

---

### F6 · HIGH · Python `repr` leaks into published finding titles

Live drill of portfolio card ANM-004:

```json
"title":     "Federal Medicare / General Surgery: Decimal('0.888889') denials unworked pct",
"statement": "Federal Medicare / General Surgery ranks #1 by denials unworked pct over
              2026-03-01..2026-04-15: Decimal('0.888889').",
"grade": "direct", "confidence": "high"
```

Root cause `packages/investigation/src/revi_investigation/application/findings.py:501-504`:

```python
magnitude = (
    f"${abs(amount) // 100:,}" if shape.is_money and amount is not None else f"{value!r}"
)
```

The `!r` fallback fires for **every non-money metric**. I reproduced it on
`appeal_overturn_rate`, `clean_claim_rate`, `cob_mismatch_rate`, `denials_unworked_pct` — i.e.
every ratio in the pack. `apps/web/src/components/findings/FindingCard.tsx:51` renders
`{finding.title}` verbatim, so this is what the user sees. It should read "88.9%."

Same function, same class of problem: `metric_label = shape.measure.replace("_", " ")` uses the
raw column name, and the CARC findings on T3/T4 read "204 denied dollars", "16 denied dollars"
— bare code, no label. The pack's own governed guidance forbids exactly this:
`packs/base-rcm/presentation.yaml`, recipe `denial_code_mix`: *"always label the pair, never the
CARC alone."* The engine does not read its own pack's presentation rules.

---

### F7 · HIGH · The UI tells users their feedback was logged. It was discarded.

`apps/web/src/components/feedback/FeedbackTriage.tsx:15-19`:

```ts
const CLOSURE: Record<FeedbackChoice, string> = {
  yes:    "Logged. Thanks — recorded against this trace.",
  fix:    "Logged to the trace for pack review. ...",
  review: "Flagged for analyst review with the full evidence trail attached.",
};
```

The handler is `setFeedback(turnId, c.id)` → `apps/web/src/lib/store.ts:404-405`, which mutates
an in-memory zustand map. No `persist` middleware, no `localStorage`, no `fetch`. The published
OpenAPI has exactly seven paths and none of them accept feedback. Nothing is logged, nothing is
recorded against any trace, nothing is flagged for anyone, and it is gone on refresh.

In a product whose entire pitch is "we never claim more than the evidence supports," the one
place you talk to the user about trust is a lie. Also: this is the *only* feedback surface in
the system, so F4's missing flywheel has a fake front door.

---

### F8 · HIGH · "Not checked" is indistinguishable from "reconciled"

Across the reference conversation:

```
T1  plan_hash 5b18277f1caa42d1  reconciliation: null
T2  plan_hash 5b18277f1caa42d1  reconciliation: "status=passed"   <- the no-op turn
T3  plan_hash d7ed10110c81d111  reconciliation: null              <- 3 drills + pivot + set_dimensions
T4  plan_hash de8a6cb741e46363  reconciliation: null
```

The reconciliation invariant fired on the one turn that changed nothing and stayed silent on
the turn that actually drilled into a 55,722-claim cohort.
`packages/investigation/src/revi_investigation/application/submit_turn.py:802-830` has four
silent `return None` paths (no split/drill op, no compare shape, no parent totals frame,
watermark mismatch). T3 passes the first gate and dies on `parent_totals is None` because the
pivot changed the measure from `cash_posted` to `denied_dollars`.

That behavior is defensible. Publishing it as `null` is not: the UI's `ReconciliationBanner`
only renders on failure, so the analyst cannot distinguish "these children sum to their parent"
from "nobody checked." §18.1-13 claims violations are "flagged, never silently shown" — true —
but *non-coverage* is silently shown, which is the same trust hole one level up.

**Fix:** emit `status=not_applicable` with the reason. It is a two-line change and it converts
a hidden gap into a visible, honest one.

---

### F9 · HIGH · This is AI-decorated, not AI-native — and there is no agentic follow-through

The model's entire surface area:

- 4 prompt templates, 132 lines total (`classify_turn`, `interpret_question`,
  `resolve_referents`, `emit_refinements`) + a narrative stream.
- Output vocabulary: one of 5 turn classes, a fixed id-extraction schema, referent
  resolutions, and 12 closed operators.
- Adapter: 322 LOC, against 6,504 in `investigation` + 2,590 in `pack` + 1,377 in `kernel`.
- Observed cost: `llm_calls: 2` on a new investigation, `3` on a refinement, `1` on meta.

The LLM is a parser. That is a *fine* place to start and the refusal discipline it buys is
real. But two things make it look like a ceiling rather than a floor:

1. **The only "next step" the product ever proposes is hardcoded.**
   `findings.py:391` and `:536`: `suggested_refinements=(f"drill into {referent_value}",)`.
   Every finding, every turn, one canned suggestion. No hypothesis generation, no "here are the
   three competing explanations and here is the probe that separates them" — which is the thing
   a 2026 model is *actually good at* and the thing an investigation platform should be selling.
2. **The demo narrative is deliberately content-free.** Live T1:
   *"F1 leads the certified findings on this turn, followed by F2 and F3. Every finding here is
   graded direct against certified semantics."* That is the scripted mock, granted — but
   `apps/api/src/revi_api/scripted_llm.py:225-230` states the design intent: the narrator
   "states no free numbers at all and no proper names." The grounding validator's price is a
   narrator that says nothing.

Meanwhile the OpenAPI has **zero write endpoints**. Cards carry `status: "open"` and nothing can
change it. No queue, no assignment, no appeal draft, no payer packet, no export. Their own
research file §1.5 is unambiguous: *"Answers must ship with actions. ... A number without a 'so
what / do what' is considered incomplete."* You wrote the requirement and did not build it.

The strategic question is whether the deterministic kernel is a moat or a governor. Today's
evidence says: it is a moat for *correctness* and a governor for *coverage* — F3 shows the
capability ceiling is human YAML throughput, and F4 shows nothing makes that throughput cheaper
over time. An end-to-end agent with schema access would have answered `days_in_ar`. Keep the
kernel — but the LLM has to start *authoring the pack*, under the same governance, or the
architecture loses on coverage while winning on trust.

---

### F10 · HIGH · The warehouse leaks physical tables and the garbage collector is a no-op

After a handful of demo sessions on my machine:

```
cohort tables in schema 'cohort_store': 199   (11,088,678 rows)
data/revi_warehouse.duckdb: 141 MB → 142 MB during this review

$ uv run python -m revi_scheduler.sweep
INFO  dropped 0 expired cohort table(s) in the warehouse
      (the connector's cohort registry is process-local: a fresh sweep only
       ever drops what it materialized itself)
```

Every drill `CREATE TABLE`s a 55K-row cohort into the analytical store and nothing reclaims it.
The walkthrough lists this as open follow-up 4, and the CLI is admirably honest in its own log
— but consider the production target. Snowflake. An analytics application that writes physical
tables into the customer's warehouse on every user click, with no working reclamation, is a
cost conversation and a security-review conversation on day one. Cohorts should be a predicate
or a transient/temp object, not a permanent table, and the registry has to be durable.

---

### F11 · MEDIUM · The single largest weight in the ranking sits outside the governance system

`packs/base-rcm/anomaly_actionability.yaml` carries the recoverable-fraction rules that feed
`actionability`, weighted **0.60** — larger than impact (0.25) and recency (0.15) combined. Its
own header says why it is out of band:

> "the strict pack loader is mid-flight in a concurrent workflow, so these rules ride in their
> own file (read and content-hashed by the API composition root)"

`apps/api/src/revi_api/wiring.py:257` loads it straight off disk. Consequences: it is **not** in
`pack_snapshot_id` (`e9fab59dd289…` from `/v1/capabilities`), so a change to the numbers that
order the entire worklist does not mint a new snapshot; it cannot be overlaid by a tenant; it
cannot be A/B-replayed by the candidate-pack mechanism §18.1-8 demonstrates; and the §12
meaning-change refusal does not apply to it.

Worse, the provenance claim is dead code. `actionability.py:3` says "content-hashed for the
trace"; `content_hash` is set at `actionability.py:87` and
`grep -rn "content_hash" apps packages --include='*.py'` shows **no consumer anywhere**. It is
computed and thrown away. The most consequential judgement in the product — "can this actually
be fixed?" — is the one piece of content with no version, no provenance, and no rollback.

---

### F12 · MEDIUM · Turn 2 of the flagship conversation is a literal no-op

```
T1  "Why did cash decline last week?"  plan_hash 5b18277f1caa42d1…
      F1 State Medicaid −$99,093 · F2 Atlas Commercial −$48,940 · F3 Meridian Health −$38,064
T2  "Break that down by payer"          plan_hash 5b18277f1caa42d1…   <- identical
      F4 State Medicaid −$99,093 · F5 Atlas Commercial −$48,940 · F6 Meridian Health −$38,064
```

Same plan, same probes, same numbers, new handles. T1's interpretation already carries
`dimension_ids: ["payer"]` (`apps/api/src/revi_api/scripted_llm.py:117`), so
`set_dimensions(["payer"])` changes nothing. The five-turn showcase — the thing every doc
points at as proof the conversational model works — spends turn 2 re-rendering turn 1.

It also means the reconciliation "pass" in F8 is a frame reconciling against itself.

Not a code defect. A demo-credibility defect, and an easy one to fix: make T2 *"break that down
by service line"* or *"which plans inside State Medicaid?"* — a refinement that actually moves.

---

## What I'd tell the founder to do in the next 90 days

You are optimizing the wrong variable. You have spent your budget on being *right* and almost
none on being *big*. Both matter; right-without-big is a paper. Here is the order I would run.

**Days 0–15 — stop the bleeding, three items, nothing else.**
1. F1 (custom-comparison label + window-length mismatch) and F6 (`repr` in titles). These are
   sub-hour fixes that currently make the demo unshippable. Add reference tests that assert
   rendered *strings*, not just cents — your 633 tests are green while the product prints
   `Decimal('0.888889')`, and `_drillable_card` in `test_api_reference.py:96-104`
   literally selects the one card shape that works. Test the population, not the happy path.
2. F7 — either persist feedback to the trace or delete the component. Do not tell a user you
   logged something you dropped.
3. Bolt on auth (F5). Bearer token, tenant scoping on every route, one afternoon. You cannot
   take a meeting without it.

**Days 15–45 — make the product answer.**
4. Close the catalog gaps until **every** metric in the pack computes and **every** portfolio
   card opens. `status`, `submission_date`, `discharge_date`, `first_pass_paid` as certified
   dimensions; bind REMIT at claim grain or rebind `denial_rate`. `packs/base-rcm/NOTES.md`
   already names all of it. This is unglamorous content work and it is the highest-leverage
   thing on this list — it takes the worklist from 6/33 to 33/33 and unlocks days-in-A/R.
5. Widen `validate_pack_catalog_conformance` from `exclusions:` to `filtered:` so a pack that
   cannot answer refuses to compose. Make the invariant "every shipped contract executes."
6. F8: publish `not_applicable` with a reason instead of `null`.

**Days 45–90 — build the two things that make it a company.**
7. **Actions.** `POST /v1/anomalies/{id}/work` — assign, snooze, resolve, with an outcome and a
   dollar amount. Then generate the appeal packet: the CARC, the denial evidence frames, the
   payer's own policy citation from `knowledge.yaml`, the timely-filing deadline from
   `filing_rules.yaml`. You already have every input. A number without a "do what" is
   incomplete — your own research file says so.
8. **The loop.** `AnalystCorrection` and `PackDelta` are already typed at
   `packages/pack/src/revi_pack/domain.py:899,934`. Give them a store, an endpoint, and a
   review queue. Then close it: recovered dollars per category → observed recoverable fraction →
   a `PackDelta` proposal against `anomaly_actionability.yaml` → human promotion. That is the
   flywheel, it is maybe three weeks, and it converts every customer-hour into permanent
   product. Nothing else you build compounds until this exists.

**The bet I would make, stated plainly.** Keep the deterministic kernel — as models get
smarter, the scarce thing is not reasoning, it is *the right to publish a number*, and you have
built that. But invert the ratio. Today the model parses and humans author the semantics; that
caps you at YAML throughput and F3 is the proof. Point the model at pack authoring — reading
schemas, proposing bindings and metric contracts, generating the tests, writing the
`PackDelta` — and let the kernel be the referee it already is. Governed-agent-authored
semantics with deterministic execution is a real category. Hand-authored semantics with an LLM
front end is a feature Epic ships in a release.

The wedge is big enough — $843K recoverable across 33 anomalies on 120K synthetic claims scales
to eight figures for a real system, and denial economics are the right place to stand. But you
only get the wedge if you can open the top card.
