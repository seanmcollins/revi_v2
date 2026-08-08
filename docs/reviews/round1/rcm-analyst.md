# Revi — Senior RCM Analyst review (round 1)

**Reviewer persona:** Senior RCM Analyst. Fifteen years in Epic workqueues and Excel. I read 835s
for a living, I know what CO-45 costs me versus what CO-50 costs me, and I am the person who gets
paged when the denial number in the board deck doesn't match the number in the WQ.

**Commit:** `4a9afe6` · **Warehouse:** `data/revi_warehouse.duckdb` @ `wm_003` ·
**Mode:** `REVI_LLM_MOCK=1`, API on `:8102`, driven over real HTTP with httpx.
Everything below is a command I ran and the output I got back.

---

## Verdict

The *semantic layer* of this thing is the best I have seen from a vendor. Somebody who actually
knows RCM wrote `packs/base-rcm/codes.yaml`, `benchmarks.yaml` and the metric contract
descriptions — the PR3 answer is correct, the benchmark figures are real and cited, and the
contracts say out loud where they are approximations. I would sign off on most of that content in
a governance meeting.

The *product on top of it* is not something I could use on a Monday morning. Its headline denial
rate is 9.6x the real one and ranks my payers in exactly the wrong order. Its prioritized worklist
is sixteen dead links deep before the first card that opens. It compares one week to a quarter and
labels the result "vs prior week." It prints `Decimal('0.499407')` in a finding title. And every
one of those 19 sourced benchmark figures is passed to the narrative as an empty tuple, so I never
see a single one.

The gap between the pack and the product is the whole story here. The knowledge is there. The
plumbing that would put it in front of me is not.

---

## What genuinely holds up

**1. "What is PR3" is answered correctly, and that is not a low bar.**
```
POST /v1/sessions/{sid}/turns  {"utterance":"what is pr3"}
→ PR  "Patient Responsibility" — "...PR-group amounts are billing instructions, not denials:
       PR-3 means the copay belongs to the patient, which is why the group code + CARC pair is
       the unit of analysis."
→ 3   "Copay" — "...Rides with group PR — 'PR3' is a billing instruction to the patient, not a
       payer denial."
```
It decomposes the token into group + reason, gets both right, and volunteers the one thing most
tools get wrong — that PR is not a denial. `packs/base-rcm/codes.yaml` also correctly separates
CO (provider liability, never billable to the patient), PI (payer-initiated, disputable), OA, and
CR. That is textbook-correct CAGC semantics.

**2. The metric contract descriptions are written by somebody fluent.**
`net_collection_rate` explains that its denominator is contract-expected rather than
charges-minus-contractuals *and says so rather than pretending to an NPSR it does not have*.
`clean_claim_rate` explicitly flags that MAP CL-1 measures at the scrubber while this flag is
outcome-derived. `initial_denial_rate` warns you never to conflate initial and final. That is the
kind of caveat I normally have to extract from a vendor over three calls.

**3. The benchmark content is real research, not marketing filler.**
`packs/base-rcm/benchmarks.yaml`: 19 figures, every one with `cohort_label`, `cautions`,
`period`, `authority`, `last_verified_at`, and a real source — Kodiak 11.81–11.99%, Optum Denials
Index 11–12%, Premier 13.9–15.7% split by commercial / managed Medicaid / MA, Health Affairs MA
17% with the 57%-overturned companion. Those match what I know. Ranges not point targets, which is
the correct call.

**4. Group code + CARC as a pair works — when you ask for it typed.**
```
spec: metric_ids=[denied_dollars], dimensions=[group_code, carc], basis=remit
→ F4  OA / 22: $412,971 denied dollars   [direct]
  F5  CO / 50: $395,351 denied dollars   [direct]
  F6  CO / 16: $363,465 denied dollars   [direct]
```
`OA / 22` is the right way to render a COB adjustment. The capability exists.

**5. Evidence grading is honest where it matters.**
On "Do I have a COB problem?" the direct binding (`cob_mismatch_flag`) grades `direct` and the
CARC-22 proxy chart grades `proxy`. It does not let the payer's reason code certify a conclusion
about coverage. And the share wording says *"100.0% of visible total"* rather than "of total" —
which is correct, because suppression hid eight payers with 1–6 mismatches each (182 true total,
verified in SQL).

**6. The catalog synonyms are real analyst vocabulary.**
`fin class`, `fc`, `fin cls`, `rmt payer`, `epm plan`, `ub vs hcfa`, `1500 vs ub04`,
`cob order`, `primary secondary tertiary`, `days out bucket`. Somebody has sat in front of Clarity.
I threw 134 shop-floor terms at `PackSnapshot.resolve_term` and **84 resolved**, including
`takeback`, `offset`, `DNFB`, `TFL`, `DSO`, `unapplied cash`, `zero pay`, `MSP`, `835`, `837`,
`270`, `271`, `276`. That is a better vocabulary than most commercial denial tools ship with.

---

## Findings

### F1 — CRITICAL — `denial_rate` counts un-adjudicated claims as denials, and it inverts my payer ranking

This is the one that ends the pilot.

```
POST turn, spec: metric_ids=[denial_rate], dimensions=[payer],
                 window 2026-05-01..2026-07-31, basis=service
→ F1  State Medicaid: Decimal('0.499407') denial rate   [direct] [high]
  F2  Veritas Comp Fund: Decimal('0.393229')            [direct] [high]
  F3  Silverline Medicare Advantage: Decimal('0.390066')[direct] [high]
```

A 49.9% denial rate. Against the same warehouse, same window:

| payer | Revi `denial_rate` | claims with ≥1 denial | denial rate over *adjudicated* claims | OPEN claims in Revi's numerator |
|---|---|---|---|---|
| State Medicaid | **0.4994** | **0.0519** | 0.0939 | 1,510 of 1,685 |
| Veritas Comp Fund | 0.3932 | 0.0651 | 0.0969 | 126 of 151 |
| Silverline MA | 0.3901 | **0.1053** | 0.1472 | 430 of 589 |
| Lakewood Medicaid MCO | 0.3898 | 0.0827 | 0.1193 | 234 of 297 |
| Summit Peak MA | 0.3853 | 0.0875 | 0.1246 | 252 of 326 |
| State Medicaid MCO | 0.3791 | 0.0697 | 0.1009 | 302 of 370 |

The numerator is `clean_claim = false`, and in this warehouse `clean_claim` is false for **all
11,319 OPEN claims that have zero denial records**:

```sql
select clean_claim, status, count(*) from snap_003.v_claim group by 1,2;
(False,'OPEN',11319)  (False,'CLOSED',3996)  (False,'DENIED',861)  (False,'PAID',3141)  (True,'PAID',101194)
```

So the published rate is 9.6x the true remittance denial rate for State Medicaid. Bad enough. But
look at the ordering: **Revi's #1 worst payer (State Medicaid, 49.9%) is actually the *best* of
those six at 5.2%.** Silverline, genuinely my worst at 10.5%, ranks third. The defect does not just
inflate a number — it reverses the answer to the question I asked.

And the answer arrives graded `direct`, `confidence: high`, with **zero warning**. The contract
description in `packs/base-rcm/metrics/denial_rate.yaml` honestly says *"a claim still awaiting its
first remittance carries a false clean flag and therefore counts as denied, so trailing windows at
a fresh watermark read high"* — that caveat never reaches the answer payload. The only warning
emitted was `alternate_basis_used`. Disclosure that lives in a YAML file I will never open is not
disclosure.

**Fix:** restrict the denominator to adjudicated claims (or add `status neq OPEN` to the numerator
the way `initial_denial_rate` already does), and make the contract's watermark caveat a *published
warning on every answer*, not description prose. Until then this metric should not be reachable.

---

### F2 — CRITICAL — the prioritized worklist is sixteen dead links deep before the first card opens

I drilled every one of the 33 cards from `GET /v1/portfolio/latest` using its own `drill_spec` as
`TurnRequest.spec`, each from a fresh session.

```
ANSWERED: 6   REFUSED: 27
refusal codes:    {UNSUPPORTED_CONCEPT: 18, DATE_BASIS_INVALID: 9}
refusing metrics: denial_rate 9, dnfb_dollars 3, underpayment_variance 3,
                  timely_filing_at_risk_dollars 3, credit_balance_dollars 2,
                  avg_days_to_pay 2, bill_lag_days 2, gross_collection_rate 1,
                  charge_lag_days 1, late_charge_pct 1
refused impact:   $1,569,558  (89.7% of portfolio dollars)
answered impact:  $180,055    (10.3%)
```

The walkthrough states "6 of the 33 cards answer today." True, and honestly stated. What it does
not state is **which** six. The cards are returned in priority order and the **top sixteen all
refuse**. The first card that opens is rank 17, priority `0.0692`, against a top score of `0.6000`.

Worse, the two cards sitting at the very top — `ANM-023` and `ANM-024`, both
`credit_balance_dollars` — are there *because* the priority formula floors compliance-mandatory
work at 0.60 so refunds can never sink below discretionary work. That is the right governance call.
The system deliberately promotes the two items I am legally obliged to act on inside a 60-day
overpayment clock, and then hands me:

```
{"code":"UNSUPPORTED_CONCEPT","message":"no probe in the plan is answerable at the source",
 "correlation_id":"corr_7372239cd558"}
```

No `details`. No "this metric is unavailable in your catalog." No suggested alternative. A
correlation id, which is for your on-call engineer, not for me.

A worklist where the top sixteen items are broken is not a worklist with a gap. It is a worklist
I stop opening on day two.

---

### F3 — CRITICAL — an explicit custom comparison is labelled "vs prior week", and 7 days is compared to 90 days unnormalized

Reference conversation, T4, `"Compare that to Q1"`:

```
header:  2026-07-27..2026-08-02 (post) · vs 2026-01-01..2026-03-31 · watermark wm_003
         comparison_kind: custom
F10  "16 denied dollars down $169,026 vs prior week"
     "16: denied dollars moved from 17051059 to 148451 cents (down $169,026, -99.1% vs prior week)."
F11  "18 denied dollars down $128,324 vs prior week"     [direct] [high]
F12  "50 denied dollars down $108,435 vs prior week"     [direct] [high]
```

Two separate problems in one finding.

**The label is a lie.** The header correctly says `vs 2026-01-01..2026-03-31`. The finding title,
the statement and the percentage all say **"vs prior week."** Root cause is
`packages/investigation/src/revi_investigation/application/findings.py:300–309`:

```python
if comparison.kind is ComparisonKind.PRIOR_YEAR:
    return "vs prior year"
requested = spec.context.window.requested
if requested is not None and requested.quantity == 1:
    return f"vs prior {requested.unit.value}"
```

`ComparisonKind.CUSTOM` is never handled, so it falls through to the *primary* window's unit — one
week — and prints "vs prior week" for a comparison against a quarter.

**The magnitude is meaningless.** One week of denials against thirteen weeks of denials, no
normalization, no warning. `-99.1%` is arithmetic, not analysis. It is published as `grade: direct`,
`confidence: high`, `impact_cents: -16902608`. If that -$169,026 "impact" lands in a leadership deck
I am the one explaining it.

The platform's entire pitch is that every number carries its effective context. Here the context
header and the finding text disagree, and the finding text is what gets copied.

---

### F4 — HIGH — CARC findings render as bare integers, with no group code and no descriptor

Reference conversation T3, "For the top three payers, what's the CARC mix?":

```
F7  "204 denied dollars down $12,545 vs prior week"
F8  "2 denied dollars down $8,564 vs prior week"
F9  "22 denied dollars down $7,557 vs prior week"
chart_main: x="carc", rows x = "2","3","4","11","16","18","23","27","45","50","96","97",...
```

Two things wrong, both domain-fatal.

**The group code is dropped.** `denied_dollars.yaml` v2 says in its own description: *"Denials are
keyed on the group code + CARC pair throughout."* The playbook's CARC-mix path cuts by `carc` alone.
That is not a style quibble — in this warehouse **14 of 20 CARC codes appear under more than one
group code**:

```sql
select carc_code, count(distinct group_code) g from snap_003.v_denial group by 1 having g>1;
→ 16, 29, 4, 97, 204, 197, 27, 151, 50, 11, 253, 96, 18, 45   (all CO and PI)
```

So "16" silently merges CO-16 (my billing error, I fix and rebill) with PI-16 (payer-discretion
reduction, I dispute). Those go to different workqueues and different people. Merging them is the
analysis equivalent of adding apples to the accounts payable ledger.

**The descriptor is dropped.** "2 denied dollars down $8,564" is unreadable. The pack *has* the
answer — `codes.yaml` defines CARC 2 as "Coinsurance", and the warehouse carries
`carc_description` on `v_denial` — but the finding builder prints the raw code. And CARC 2 is a
**PR** code: member coinsurance. It is the second-largest "denial driver" in that answer and it is
not a denial at all.

The typed path (`dimensions: [group_code, carc]`) renders `OA / 22`, `CO / 50` correctly, so the
capability is right there. The playbook just doesn't use it, and neither path resolves the code to
its title.

---

### F5 — HIGH — 19 sourced benchmark figures are never shown to anyone

`apps/api/src/revi_api/assembly.py:127`:

```python
prompt = build_narrative_prompt(
    findings=findings,
    header=header,
    reconciliation=outcome.reconciliation,
    benchmarks=(),            # ← hardcoded empty
)
```

`grep -rn "benchmarks_for_metric" --include='*.py' packages apps | grep -v /tests/` returns **only
the definition** in `packages/pack/src/revi_pack/domain.py:706`. Every call site is a test. There is
no `benchmarks` field on the turn response either — I checked the published OpenAPI.

So: 401 lines of researched, cited, caution-annotated benchmark content in
`packs/base-rcm/benchmarks.yaml`, and the analyst-facing surface passes an empty tuple.

This is the difference between a number and an answer. Revi told me
`Lakewood Medicaid MCO: Decimal('0.833333') denials unworked pct`. The pack knows the industry
figure is around two-thirds and carries three sourced benchmarks for that exact metric. I was not
shown one of them. "83%" versus "83% against an industry ~67%" is the difference between a data
point and a reason to staff a follow-up team.

Same gap in reverse: `net_collection_rate.yaml` and `first_pass_yield.yaml` cite benchmark ranges
("95-99 percent", "90-95 percent") *inside their prose descriptions*, where they have no source, no
cohort label and no `last_verified_at` — while the governed figures that do have all three go
nowhere.

---

### F6 — HIGH — `denied_dollars` counts patient responsibility and contractual write-downs as denials

```sql
select group_code, denial_category, count(*), sum(denied_amount_cents)/100.0
from snap_003.v_denial group by 1,2;
→ PR / PATIENT_RESP     408 records   $872,600
  CO / CONTRACTUAL      577 records   $1,285,492
  PI / CONTRACTUAL       48 records   $120,313
  total denied          7,998         $17,500,429
```

`denied_dollars` has no exclusions. So **13.0% of my "denied dollars" is not a denial**:

- **PR-1 / PR-2 / PR-3** ($872,600) — deductible, coinsurance, copay. The pack's own PR definition
  says these are *"billing instructions, not denials."* They go to patient statements, not to a
  denial WQ.
- **CO-45** ($1,153,390 alone) — billed charge exceeds the contracted amount. That is the
  contractual allowance. It is on essentially every paid line of a real 835. It is the number I am
  *supposed* to write off.
- **CO-253** ($132,103) — federal sequestration. There is literally nothing to work.

The pack is internally inconsistent about this: `denials_unworked_pct` v2 correctly carries
`exclusions: {dimension: denial_category, op: eq, value: PATIENT_RESP}` with the reasoning spelled
out — but `denied_dollars` and `denial_rate` do not. Two metrics in the same pack disagree about
what a denial is. Any analyst who pulls both will find the numbers don't tie and will stop trusting
both.

---

### F7 — HIGH — `denials_unworked_pct` measures "not appealed", then calls it "never worked"

The contract numerator is `appeal_status eq NONE`. That equates *not appealed* with *not worked*,
and in denial ops those are very different things.

```sql
-- what is actually sitting in the numerator (PATIENT_RESP already excluded):
OTHER/CO  969 | MED_NEC/CO 746 | ELIG/CO 677 | CODING/CO 660 | COB/OA 607
DUPLICATE/CO 419 | CONTRACTUAL/CO 386 | AUTH/CO 277 | ... = 5,131 of 7,590 → 67.6%

select carc_code, count(*) from snap_003.v_denial
where denial_category<>'PATIENT_RESP' and appeal_status='NONE' and carc_code in (45,253)
group by 1;   →  45: 375,  253: 46
```

**421 of the 5,131 "never worked" denials are CO-45 and CO-253** — contractual allowance and
sequestration. There is no appeal to file. Filing one would be malpractice. Another 451 are
duplicates (CO-18/PI-18) and 720 are coding (CO-4, CO-11, CO-97), which in every shop I have worked
are resolved by **correct-and-rebill, not appeal** — and correctly show `appeal_status = NONE`
forever afterwards.

So the headline "money left on the table" metric counts as abandoned a large block of denials whose
correct disposition is exactly what the data shows. The contract description says the exclusion
removes "billing instructions, not workable denials" — it should apply the same reasoning to
CONTRACTUAL, and it needs a "worked" signal that isn't the appeal flag (rebill, corrected claim,
write-off approval) before this number means what its name says.

---

### F8 — HIGH — analyst-facing text contains Python object reprs and raw cents

Straight off the wire, no post-processing:

```
title:     "State Medicaid: Decimal('0.499407') denial rate"
title:     "Lakewood Medicaid MCO: Decimal('0.833333') denials unworked pct"
statement: "State Medicaid: cash posted moved from 18722151 to 8812843 cents (down $99,093, ...)"
```

`packages/investigation/src/revi_investigation/application/findings.py:511`:
```python
magnitude = (f"${abs(amount) // 100:,}" if shape.is_money and amount is not None
             else f"{value!r}")     # ← Decimal repr for every non-money metric
```
and line 372:
```python
f"{label}: {metric_label} moved from {prior!r} to {current!r} cents ..."
```

Three consequences: (a) `Decimal('0.499407')` is a stack-trace artifact in a finding title;
(b) every ratio metric — denial rate, clean claim rate, collection rate, unworked pct, the entire
KPI set I live in — is unformatted, when it should read `49.9%`; (c) cash figures are rendered
twice, once as raw cents and once as `$99,093` floor-divided so the cents are silently dropped.

The frontend has a perfectly good `formatCents` and a `0.239 → "23.9%"` helper in
`apps/web/src/lib/format.ts`, but the *title* and *statement* strings are assembled server-side and
rendered verbatim, so they bypass it. Anything that ships a Python repr to a user tells that user
nobody has read the output.

---

### F9 — HIGH — six of the product's eight advertised entry points dead-end, with an unusable clarification

`apps/web/src/lib/guideQuestions.ts` calls these "the product's canonical entry points" and its
docstring claims *"Each one maps to a governed playbook in the base-rcm pack ... so the live API
answers them for real."* I drove all eight against the live API:

| guide question | live API outcome |
|---|---|
| What is PR3? | ✅ definitional |
| Do I have a COB problem? | ✅ answer |
| Give me my denial rate by month for the last 6 months | ❌ clarification_required |
| What are my top 5 issues? | ❌ clarification_required |
| Will my cash increase next month? | ❌ clarification_required |
| Assess the performance of each facility financially | ❌ clarification_required |
| Give me payer payments by payer category weekly over the last 3.25 months | ❌ clarification_required |
| Drill into Medicaid | ❌ clarification_required |

Yes, that is the scripted mock classifier. It is also the only mode that runs without an API key —
the mode the README quickstart, the demo and this review all use — and the source comment asserts
the opposite of what the server does.

The clarification itself is the real problem. Here is the response to the single most standard
question in my job, next to actual gibberish:

```
"Give me my denial rate by month for the last 6 months"
  {"question":"I couldn't confidently read that request — could you rephrase it?",
   "options":[], "reason":"turn classification returned no structured output"}

"purple monkey dishwasher"
  {"question":"I couldn't confidently read that request — could you rephrase it?",
   "options":[], "reason":"turn classification returned no structured output"}
```

Byte-identical. `options: []`. The pack resolves "denial rate" to `denial_rate` — I verified that
directly against `resolve_term`. The system had every piece it needed to say "did you mean
denial_rate by month?" and instead gave me the same brush-off it gave three random words. A
clarification with no options is a refusal wearing a question mark.

---

### F10 — MEDIUM/HIGH — a card's own number does not match what drilling it produces, and nothing checks that seam

`ANM-001` card text: *"Summit Peak Medicare Advantage / Cardiology: 27 denials totaling **$170,643**
in window 2026-06-20..2026-07-28."* `impact_cents: 17064300`. `provenance: external_detection`,
no grade.

Its `drill_spec` refuses (`DATE_BASIS_INVALID`). But I repointed the same spec at `denied_dollars`
on the `remit` basis and it answered instantly:

```
header: 2026-06-20..2026-07-28 (remit) · filters: payer eq [Summit Peak MA]; service_line eq [Cardiology]
F1  Summit Peak Medicare Advantage / Cardiology: $177,202 denied dollars   [direct]
```

**$170,643 on the card, $177,202 in the drill.** A 3.8% divergence, because the card counts
claim-level denied dollars over 27 DENIED-status claims and the metric sums 29 denial records. Both
are defensible; neither is labelled; nothing reconciles them. Reconciliation only runs parent→child
*within* a session, so the card→investigation seam — the single most-travelled path in a daily
worklist — is unchecked. I click a $170,643 card and land on $177,202 with no explanation. That is
how an analyst learns to distrust a tool.

Two things fall out of the same experiment worth saying plainly:

1. **The nine `denial_rate` refusals are a content bug, not catalog work.** The walkthrough files
   them under "All three rows are catalog work, deliberately out of scope for this pass." But
   `ANM-001`'s evidence is `denied_claims: 27` and `denied_cents: 17064300` — counts and dollars.
   The detector tagged it `metric_id: denial_rate` when what it measured was denied dollars.
   Repointing the metric id makes it answer today. That is nine of the twenty-seven refusals
   recoverable in a YAML edit, including four of the top eleven cards.
2. **`external_detection` cards carry no grade and no verification path.** The card asserts a number
   the investigation layer cannot reproduce. For a platform whose thesis is "every published number
   is computed by a versioned kernel," the surface I open first every morning is the one place that
   isn't.

---

### F11 — MEDIUM — the synonym index is dead code, and the LLM sees a 160-character blurb

The catalog carries the best analyst vocabulary in the repo. It is never used.

```
grep -rn "dimension_for_synonym\|dimensions_for_synonym" --include='*.py' packages apps | grep -v /tests/
→ packages/catalog-contracts/src/revi_catalog_contracts/model.py:402  (the definition)
→ packages/catalog-contracts/src/revi_catalog_contracts/model.py:413  (the definition)
```

Defined, unit-tested, **never called from a production path**. And the vocabulary handed to the
model omits them entirely —
`packages/investigation/src/revi_investigation/application/interpretation.py`:

```python
dimensions = "\n".join(f"- {dim.id}: {dim.label}" for dim in self._catalog.dimensions if dim.certified)
```

So when I say *"break that out by fin class"* or *"cut it by LOB"*, the model is shown
`financial_class: Financial class` and `product_type: Product type` and has to guess. The mapping
exists in YAML and is thrown away before the model reads it.

Same file, `_DESCRIPTION_CLIP = 160`. Metric descriptions are clipped to 160 characters for the
prompt. `denial_rate`'s first 160 characters end at *"...divided by every claim in the window on the
chosen date b"* — one character before the caveat that would have stopped the model choosing it for
a trailing-window question. The most careful writing in this repo is truncated before the model ever
sees the careful part.

Two alias collisions also resolve to nothing, which is safe but silently unhelpful:
`product` → {`plan`, `product_type`} and `service area` → {`facility`, `service_line`}.

---

### F12 — MEDIUM — a wrong alias, and no governed RARC codes at all

**`place of service` is a synonym for `facility`** (`warehouse/catalog/dimensions.yaml`, `facility`
synonyms: `[location, site, hospital, campus, service area, loc, place of service]`). POS is a
two-digit CMS code on the claim line — 11 office, 21 inpatient, 22 on-campus outpatient, 23 ER. It
is not a facility, and POS-driven denials and POS/modifier mismatches are a real denial category.
Answering a POS question with a facility cut is a confidently wrong answer, which is worse than a
refusal. (This one currently cannot fire, because the synonym index is dead — see F11 — but it will
the moment somebody wires it up.)

**No RARC codes are governed.** `codes.yaml` defines `RARC` as a concept and cites its licensing
exposure, but contains **zero RARC definitions** — `N290`, `MA130`, `M51` all miss `resolve_term`.
The warehouse carries `rarc_synthetic` on `v_denial` and the catalog marks it `certified: false`, so
I cannot cut by remark code either. That matters specifically for CO-16 — my single largest denial
bucket at $2,025,317 — which is *definitionally* uninformative without its RARC. "Missing or invalid
information" is not actionable; "missing rendering provider NPI" is. Revi can tell me I have a CO-16
problem and can never tell me what to fix.

Related, from the same vocabulary sweep (50 of 134 terms missed): `TPL`, `third party liability`,
`medical necessity`, `277CA`, `999`, `EFT`, `bad debt`, `charity`, `NCCI`, `bundling`, `modifier`,
`clearinghouse`, `no surprises act`, `IDR`, `final denial rate`. The 277CA/999 gap is the one I would
raise first — front-end rejections never become denials, and "why did cash decline" is very often
answered there.

---

## Two things I want to flag without a severity

**`carc16` misses; `carc 16` hits.** `pr3` normalizes fine, `carc16` does not. Nobody types
consistently at 4pm on a Friday.

**192 orphaned `cohort_*` tables in the warehouse file.** `select table_name from
information_schema.tables` returns 192 of them alongside 20 real tables. The walkthrough is upfront
that `drop_expired_cohorts` is process-local and reports "dropped 0" honestly — this is what that
looks like after a few days of driving.

---

## What I would fix first, in order

1. **Gate `denial_rate` until the denominator is adjudicated-only** (F1). Right now it publishes a
   confidently-graded number that is 9.6x reality and ranks payers backwards. Nothing else on this
   list matters if the flagship metric can do that.
2. **Handle `ComparisonKind.CUSTOM` in `_period_phrase`, and warn on unequal window lengths** (F3).
   Two small changes; removes an entire class of number that gets copied into decks.
3. **Repoint the nine `denial_rate` anomaly cards at `denied_dollars`** (F10, F2). Verified: the
   drill answers today. Nine of twenty-seven refusals, four of the top eleven cards, for a YAML
   edit — not the catalog project the walkthrough files it as.
4. **Wire `benchmarks_for_metric` into the answer payload** (F5). The research is done and cited.
   Passing it `()` is the single largest value left on the floor in this repo.
5. **Render codes as `GROUP / CARC — Title`, everywhere, and make the CARC-mix playbook cut by the
   pair** (F4). The contract already says the pair is the unit of analysis; make the product agree.
6. **Add `denial_category in (PATIENT_RESP, CONTRACTUAL)` exclusions to `denied_dollars`, or rename
   the metric** (F6). Two metrics in one pack currently disagree about what a denial is.
7. **Format ratios as percentages and kill the `!r`** (F8). Half a day. Removes the single most
   embarrassing thing a demo audience will see.
8. **Give clarifications options** (F9). The pack resolves "denial rate" already; use it.
9. **Feed catalog synonyms into the interpretation prompt and raise `_DESCRIPTION_CLIP`** (F11).
   The vocabulary work is already done and currently unreachable.
10. **Fix `place of service`; add RARC definitions and certify `rarc_synthetic`** (F12).

---

## Bottom line

I would not put this in front of a director today. The denial rate is wrong in a way that reverses
its own conclusion, the worklist's top sixteen items are broken, and one comparison in the reference
conversation is mislabelled in the text while being correct in the header.

But I would not walk away either, and I want to be clear about why. Every one of my top five fixes
is small, and four of them are wiring content that already exists and is already correct to a
surface that is already built. `codes.yaml`, `benchmarks.yaml` and the metric contract descriptions
are the hard part of an RCM product, and they are done to a standard I would defend in a governance
review. The plumbing is the easy part, and it is the part that is broken.

Fix the ten items above and I would run this in parallel with my WQ reports for a month and see
which one I stop opening.

*— Senior RCM Analyst, round 1*
