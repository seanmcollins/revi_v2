# Revi — RCM Executive review (Round 1)

**Reviewer persona:** VP Revenue Cycle, 6-hospital system on Epic Resolute. 22 years in the field.
**Question I am answering:** would I sign a design-partner agreement, and what would it take to let my
denials and A/R analysts touch this?

**What I did:** ran the API at `4a9afe6` (`REVI_LLM_MOCK=1`, real generated warehouse, port 8101),
drove the five-turn reference conversation, the COB and definitional anchors, a nonsense question,
error cases, `GET /v1/portfolio/latest`, drilled **all 33** portfolio cards from fresh sessions,
swept **all 27** metric contracts, tested cross-tenant access, and cross-checked numbers against
`data/revi_warehouse.duckdb` in DuckDB directly. Every number below came off the wire or out of the
database.

---

## Verdict

The engineering discipline here is genuinely unusual — the lineage graph, the typed refusals, and the
self-disclosure in `packs/base-rcm/NOTES.md` are better than what I get from vendors ten times this
size. But I cannot put this in front of my CFO and I cannot put it in front of my analysts, and the
reason is not "it's early." Two of the three things I'd demo — a Q1 comparison and the daily work
list — are actively wrong in ways that would end my credibility in one meeting: a 7-day window
compared against a 90-day window and labelled "vs prior week," and a top-issues surface whose top
sixteen cards all error out when you click them. **I'd sign a design-partner MOU. I would not let a
single analyst near it, and I'd want the two critical items fixed before the next demo.**

---

## What genuinely survives scrutiny

**1. The lineage is real audit evidence, not a screenshot.**
`GET /v1/sessions/{sid}/lineage` returned the full DAG with typed operator edges — `set_dimensions
→ drill_into×3 + pivot + set_dimensions → set_comparison → (meta, no operators)` — each edge stamped
with its `turn_id`. The T5 meta turn returned six probes with 64-char content hashes, their stated
purpose, cache-hit flags, and the operator chain. If a payer or an auditor asks "how did you get
$99,093," I can answer that from the record. Nobody else in this market can.

**2. Typed refusal beats a confident wrong answer, and it is specific.**
`{"metric_ids":["ebitda_margin"]}` → `UNSUPPORTED_CONCEPT: typed metric 'ebitda_margin' is not in the
pack`. `{"dimensions":["astrological_sign"]}` → `... is not in the catalog`. A denial-rate drill →
`DATE_BASIS_INVALID: date basis 'remit' is not bound for entity 'claim'`. Every one carries a
`correlation_id`, and a failed *turn* comes back as `200 {"outcome":"error"}` rather than an HTTP
error — right call, a failed question is a conversation event.

**3. The semantic catalog is a real column whitelist, and that is my PHI control.**
I tried to reach identifiers six ways through the typed spec: `patient`, `patient_id`, `mrn`,
`claim_id`, `npi`, `rendering_provider`. All six refused — *"not in the catalog."* Nothing in the
question path can name a column the catalog has not certified. That is the right architecture for
this problem and it is the single thing that makes me willing to keep talking.

**4. The definitional answer is domain-correct, which is rarer than it sounds.**
"what is pr3" returned group code PR + CARC 3 with the sentence *"PR-group amounts are billing
instructions, not denials."* Most RCM tools I've bought would have called PR-3 a denial and inflated
my denial rate with it. Sourced to `base-rcm@1.0.0` with a pack snapshot hash, zero warehouse queries.

**5. The self-disclosure discipline is the strongest signal in the repo.**
`packs/base-rcm/NOTES.md` documents that all seven `exclusions:` clauses in the shipped pack were
authored with inverted polarity, names the one that was live and wrong, publishes the before/after
(`denials_unworked_pct` 41/62 = 66.13% → 1182/1520 = 77.76%, a 24× denominator change), bumps the
contract version, and states plainly that the new guard *cannot* catch that class. Vendors do not
write that down. That is a team I can work with.

**6. The domain research is credible.** `docs/research/industry-research.md` speaks Epic DNB/CFB/HAR/WQ,
athena kick codes and HOLD statuses, HFMA MAP keys, X12 licensing exposure. `concepts.yaml` carries
`DNB` and `discharged not billed` as governed aliases. Somebody did the homework.

---

## Findings

### 1. CRITICAL — A "compare to Q1" gesture compares 7 days against 90 days and calls it "vs prior week"

This is the one that ends the meeting. Turn 4 of the platform's *own reference conversation* is
"Compare that to Q1." I reproduced it with a pure typed refinement — **zero LLM involvement in the
operator** — so this is the engine, not the scripted demo:

```
POST /v1/sessions/{sid}/turns
  {"refinements":[{"op":"set_comparison","kind":null,
                   "custom":{"start":"2026-01-01","end":"2026-03-31"}}]}

header:  2026-07-27..2026-08-02 (post) · vs 2026-01-01..2026-03-31 · watermark wm_003
F4  Atlas Commercial cash posted down $4,199,421 vs prior week
    "Atlas Commercial: cash posted moved from 446272607 to 26330486 cents
     (down $4,199,421, -94.1% vs prior week)."   grade=direct  confidence=high
warnings: [suppression boilerplate, basis notes] — nothing about the window
```

Two independent defects in one sentence:

- **The label is false.** `findings.py:301-310` (`_period_phrase`) reads
  `spec.context.window.requested.unit` — the *current* window's unit, carried over from turn 1 — and
  only special-cases `PRIOR_YEAR`. Any custom comparison window inherits "vs prior week."
- **The arithmetic is unnormalized and unflagged.** One week of cash ($263,304.86) is being
  differenced against one quarter of cash ($4,462,726.07). The engine emits `-94.1%`, grades it
  `direct`, marks confidence `high`, and issues no warning that the windows are 7 and 90 days.

The reference-conversation version of the same turn is equally wrong and reads worse because the
subject is a CARC code: `"16 denied dollars down $169,026 vs prior week"` — a 90-day CARC-16 total
minus a 7-day one.

**Recommendation.** (a) Derive the period phrase from the resolved comparison range, not the requested
window — say `vs 2026-01-01..2026-03-31 (90d)` when it is not a like-for-like period. (b) When
`len(comparison) != len(current)`, either refuse, or normalize to a rate and say so, or emit a
hard warning that the delta is not period-comparable. A `direct`-graded, `high`-confidence,
warning-free number that is off by an order of magnitude is worse than a refusal.

---

### 2. CRITICAL — No authentication, and no tenant isolation whatsoever

I opened a session as a different tenant and read and wrote another tenant's data with no credential
of any kind:

```
victim  : sess_0cac94aa74ea (tenant "demo")
attacker: sess_dd4cdc38de70 (tenant "rival-health-system")

GET  /v1/sessions/sess_0cac94aa74ea/lineage        -> 200   full DAG, all questions, all findings
GET  /v1/investigations/inv_3d1c39897776           -> 200   full payload
POST /v1/sessions/sess_0cac94aa74ea/turns          -> 200   answer, turn accepted
```

The attacker's writes are now **permanent** in the victim's audit trail: the demo tenant's lineage,
which should hold 5 investigations, returned 7 — `inv_c41a5cc5c451` and `inv_b560a90b8d64`, both mine.
An unauthenticated third party can silently pollute another organization's audit record.

The published contract confirms there is nothing to bypass — every route on `/v1` has
`security: None`, and `components.securitySchemes` is empty:

```
GET  /v1/capabilities                      security=None
GET  /v1/portfolio/latest                  security=None
POST /v1/sessions                          security=None
GET  /v1/sessions/{session_id}/lineage     security=None
POST /v1/sessions/{session_id}/turns       security=None
GET  /v1/investigations/{investigation_id} security=None
global security: None | securitySchemes: []
```

`tenant` is a free-text string the client asserts about itself. It is never checked against anything.

What bothers me more than the gap is that **it is not disclosed**. `docs/acceptance-walkthrough.md`
enumerates twelve follow-ups down to `ruff format --check` reporting 78 unformatted files, and
`grep -niE "auth|bearer|oauth|jwt|rbac|authoriz"` across `docs/` and `README.md` returns nothing.
A meticulously honest gaps list that omits "there is no authentication" reads, to a buyer, like the
list was written by engineering for engineering.

**Recommendation.** Before the next demo: put `tenant` in a signed token, authorize every
`{session_id}` and `{investigation_id}` lookup against it, and add a "Security posture — not yet
built" section at the top of the walkthrough naming authN, authZ, tenant isolation, audit logging,
and PHI/BAA scope. I would rather read "we haven't built it" than discover it with curl.

---

### 3. HIGH — 16 of 27 metric contracts cannot return a number, including the entire A/R family

I swept every contract in `packs/base-rcm/metrics/` as a typed spec, cut by payer, over July 2026:

```
ANSWERABLE 11/27

FAIL ar_balance                    UNSUPPORTED_CONCEPT: unknown dimension 'status'
FAIL ar_over_90_pct                UNSUPPORTED_CONCEPT: unknown dimension 'status'
FAIL days_in_ar                    UNSUPPORTED_CONCEPT: no probe in the plan is answerable
FAIL net_collection_rate           UNSUPPORTED_CONCEPT: no probe in the plan is answerable
FAIL gross_collection_rate         UNSUPPORTED_CONCEPT: no probe in the plan is answerable
FAIL denial_rate                   DATE_BASIS_INVALID: 'remit' not bound for entity 'claim'
FAIL initial_denial_rate           UNSUPPORTED_CONCEPT: unknown dimension 'status'
FAIL first_pass_yield              UNSUPPORTED_CONCEPT: unknown dimension 'first_pass_paid'
FAIL dnfb_dollars                  UNSUPPORTED_CONCEPT: unknown dimension 'discharge_date'
FAIL timely_filing_at_risk_dollars UNSUPPORTED_CONCEPT: unknown dimension 'submission_date'
FAIL underpayment_variance, credit_balance_dollars, avg_days_to_pay,
     bill_lag_days, charge_lag_days, late_charge_pct
```

Read that list as an RCM executive. **Days in A/R. A/R over 90. Net collection rate. Denial rate.**
Those four are on the board slide I present monthly. None of them work. `dnfb_dollars` — MAP PB-1,
the metric my CFO asks about every Monday — has a beautifully written concept entry with Epic's
`DNB` as a governed alias, and returns an error.

Two aggravating details:

- **`net_collection_rate` is not in the disclosed gaps.** The walkthrough's item-10 residue table and
  `NOTES.md` between them enumerate 15 unanswerable contracts. `grep -n net_collection_rate` on both
  files returns only discussion of how the ratio is *built* and an FM-2 proxy claim — nothing saying
  it cannot execute. So the honest-gaps list is itself incomplete, which undermines the thing I was
  most inclined to trust.
- **`clean_claim_rate` answers, but with a number I don't believe:** `Atlas Commercial:
  Decimal('0.579637')`. `NOTES.md` explains why (the exclusion removal widened the population to
  every claim in the window, so unadjudicated claims read as not-clean) — but 57.96% will be read as
  a catastrophe by anyone who doesn't read the footnote. Industry clean-claim runs 75–95%.

**Recommendation.** The blockers reduce to four catalog dimensions (`status`, `submission_date`,
`discharge_date`, `first_pass_paid`) and one modelling decision on `denial_rate`'s basis binding. That
is a small, well-understood unit of work that unlocks A/R, DNFB and denial rate together — the three
families that make this a revenue-cycle product instead of a cash-posting product. Do it before
anything else on the roadmap. And suppress or footnote `clean_claim_rate` until its population is
right.

---

### 4. HIGH — The top-issues surface: the sixteen highest-priority cards all error when you click them

`GET /v1/portfolio/latest` returns 33 ranked cards. I posted every card's own `drill_spec` from a
fresh session, exactly as the UI does:

```
DRILLABLE: 6/33
rank  1 ANM-023 error UNSUPPORTED_CONCEPT: no probe in the plan is answerable at the source
rank  2 ANM-024 error UNSUPPORTED_CONCEPT: no probe in the plan is answerable at the source
rank  3 ANM-001 error DATE_BASIS_INVALID: date basis 'remit' is not bound for entity 'claim'
rank  4 ANM-013 error UNSUPPORTED_CONCEPT
rank  5 ANM-021 error UNSUPPORTED_CONCEPT: unknown dimension 'discharge_date'
 ...  (ranks 6–16 all error)
rank 17 ANM-004 ANSWER   <- the first card that works
```

The walkthrough discloses "6 of the 33 cards answer today" and I credit that. What it does not say is
**which** six — and the answer is: the six least important ones. Ranks 1 through 16 fail. The first
card an analyst can open is number seventeen.

This is not a content gap in my world; it is the product not existing. The daily prioritization
surface *is* the product for an RCM department. My denials team does not open a chat box in the
morning; they open a work list. A work list where the top sixteen items throw an error is a work list
my team stops opening on day two, and no amount of correctness elsewhere recovers that.

**Recommendation.** Until the catalog work in finding 3 lands, either (a) rank only drillable cards
and put the rest behind a clearly-labelled "detected, not yet investigable" section, or (b) make an
undrillable card degrade to showing its detector evidence rather than an error dialog. Shipping a
ranked list whose top half is dead is worse than shipping a shorter list.

---

### 5. HIGH — The actionability rules skip `unworked_denials`, and the ranking inverts on the exact pair where the evidence is decisive

`packs/base-rcm/anomaly_actionability.yaml` defines rules for eight categories. The detected
population contains twelve. `unworked_denials` is not one of the eight, so it falls to
`default: {mode: fraction, fraction: 0.50}`.

That default is applied to records that carry the exact facts needed to do it right. From
`snap_003.detected_anomalies`:

```
ANM-004  appealable_claims: 0   appeal_window_expired_claims: 14
         days_to_appeal_deadline: {min: -94, max: -49}      <- all 14 windows closed
ANM-028  appealable_claims: 17  appeal_window_expired_claims: 0
         days_to_appeal_deadline: {min: 19, max: 53}        <- all 17 still open
```

What the portfolio publishes:

```
ANM-004  impact $62,035  recoverable_cents_estimate $31,018  score 0.0692  rank 17
ANM-028  impact $34,669  recoverable_cents_estimate $17,335  score 0.0574  rank 21
```

Two things are wrong, and the second is worse than the first.

- **A phantom $31,018.** ANM-004's recoverable estimate is $31,018 on a pile where every single appeal
  window closed 49 to 94 days ago. The true recoverable is $0. If that number reaches a CFO deck as
  "identified opportunity," I own the miss.
- **The ranking inverts.** The card with nothing to recover ranks **four places above** the card that
  is 100% appealable with 19–53 days of runway. Applying the `flag_share` mode that already exists and
  is already used by `DENIAL_SPIKE` (`appealable_claims / denied_claims`), and recomputing with the
  published formula (`max_impact` = 49,326,636 from ANM-013), gives ANM-004 = 0.0315 and
  ANM-028 = 0.0785 — a clean reversal, with ANM-028 climbing roughly ten places.

Would my team agree with this ranking? On this pair, emphatically no. "Which of these denials can I
still appeal" is the *only* question a denials supervisor asks about an aged pile, the platform has
the answer sitting in the record, and the ranking ignores it.

Scope: sixteen of the 33 cards (49%) carry the rationale *"No category-specific assessment exists;
assume half the impact is workable."* Two of the eight authored rules (`COB`, `CASH_DECLINE`) match
zero cards in the population. So the governed rule set covers categories that don't occur and misses
categories that do — and because the default constant is identical across those sixteen, the
actionability term (60% of the weight) contributes nothing to ordering among half the list.

**Recommendation.** Add rules for the seven missing categories, starting with `unworked_denials` →
`flag_share(appealable_claims, denied_claims)` — that is one YAML block and it fixes a wrong dollar
figure and a wrong ordering at once. And treat "default fraction applied" as a visible caveat on the
card, not just prose in a field nobody renders (finding 9).

---

### 6. HIGH — Drilling a card does not reconcile to the card, and one is off by 39% in silence

`apps/api/src/revi_api/portfolio.py:109-138` documents the drill handle as re-deriving the detector's
assertion "from certified semantics and a versioned metric contract." I checked all six drillable
cards against their own drill:

```
ANM-009  CARD $25,493.70  ->  DRILL "Federal Medicare / Imaging: $35,515 denied dollars"   +39%
ANM-035  CARD  $3,523.68  ->  DRILL $3,523                                                  ok
ANM-010  CARD    $414.61  ->  DRILL $414                                                    ok
ANM-004  CARD $62,035     ->  DRILL Decimal('0.888889') denials unworked pct        no dollars
ANM-027  CARD $53,919     ->  DRILL Decimal('0.930233') denials unworked pct        no dollars
ANM-028  CARD $34,669     ->  DRILL Decimal('0.916667') denials unworked pct        no dollars

reconciliation: null on all six.  No warning on any of them.
```

I traced ANM-009 in DuckDB. The card's $25,493.70 is the 12 CARC-18 duplicate denials the detector
found. The drill's $35,515.30 is **all 17 denials** in Federal Medicare / Imaging over that window —
CARC 18 ($25,494) plus CARC 29 ($8,052), 97 ($1,134) and 16 ($836):

```sql
-- all: (3551530, 17)    carc 18 only: (2549370, 12)
select carc_code, count(*), sum(denied_amount_cents) from snap_003.v_denial
where payer_name='Federal Medicare' and service_line_name='Imaging'
  and denial_date between date '2026-06-25' and date '2026-07-25' group by 1;
-- 18: 12 / 2549370 | 29: 2 / 805165 | 97: 1 / 113431 | 16: 2 / 83564
```

The detector scoped by *reason code*; `AnomalyRecord.dimensions` has no slot for that, so the drill
silently widens the population by 39% and reports it under the same metric id. And for the three
`denials_unworked_pct` cards, the drill returns a rate where the card claimed dollars — there is
nothing to reconcile at all.

This is the specific mechanism by which a tool loses a revenue-cycle department. An analyst opens a
$25,494 card, the platform's own investigation says $35,515, nobody can explain the gap, and the
number that reaches finance is whichever one was on screen.

**Recommendation.** Run the existing `reconcile` operator between the card's asserted impact and the
drill's computed impact, and publish the result on the answer. When they don't match, say so and say
why ("the detection was scoped to CARC 18; this investigation covers all reason codes in the cell").
When the drill's metric is a rate and the card claimed dollars, state that the drill does not confirm
the dollar figure. Either outcome is fine — silence is not.

---

### 7. HIGH — Published, user-facing text contains Python object reprs and raw cent integers

These strings came off the wire on a normal drill of a portfolio card:

```
title: "Federal Medicare / General Surgery: Decimal('0.888889') denials unworked pct"
stmt : "... ranks #1 by denials unworked pct over 2026-03-01..2026-04-15: Decimal('0.888889')."
```

`findings.py:505` — `f"{value!r}"` is the fallback for any non-money measure. Three of the six
drillable cards produce this. It also affects `appeal_overturn_rate`
(`Decimal('0.677419')`), `clean_claim_rate`, and `cob_mismatch_rate`.

The money path is only slightly better. Every finding statement in the reference conversation reads:

```
"State Medicaid: cash posted moved from 18722151 to 8812843 cents (down $99,093, -52.9% vs prior week)."
```

`findings.py:372` interpolates raw cents next to formatted dollars in the same sentence. My analysts
would be dividing by 100 in their heads on the single most-shown sentence in the product.

I know this is cosmetic. It is also the first thing anyone sees, it takes ten minutes, and
`Decimal('0.888889')` in a headline is exactly the kind of thing that makes a CFO ask what *else*
wasn't finished.

**Recommendation.** Route every published value through the metric contract's unit — `88.9%`,
`$187,221.51`, `$88,128.43`. Never `repr()` into user-facing text.

---

### 8. MEDIUM — A flat compliance floor puts an $824 credit balance at rank #2, above half a million dollars

```
rank 1  ANM-023  credit_balance   $48,939   score 0.6000  compliance_floor_applied=true
rank 2  ANM-024  credit_balance      $824   score 0.6000  compliance_floor_applied=true
rank 3  ANM-001  denial_spike    $170,643   score 0.2726
rank 4  ANM-013  contractual     $493,266   score 0.2634
rank 5  ANM-021  dnfb            $178,217   score 0.2310
```

`portfolio.py:183-185` clamps any compliance-mandatory category to exactly `0.60`, with no materiality
term. So an $824 credit balance and a $48,939 one land on the identical score to four decimal places,
and the order between my #1 and #2 morning priorities is decided by the tiebreak in
`portfolio.py:218`, not by anything about the work.

The instinct is right — CMS's 60-day overpayment rule means refunds are owed regardless of size, and I
*do* want credit balances protected from being buried. The implementation is wrong. In my shop a $824
credit balance is a five-minute task a rep clears from a queue; it is not the second-most-important
thing happening across six hospitals today.

Related, and visible on the same screen: **ANM-013 is badged `critical`** ($493,266) while the
platform's own actionability rationale says *"Working-as-designed contractual adjustments are not an
error; the dollars are not recoverable and the anomaly is informational"* — recoverable $9,865 (2%).
Severity is a pure function of impact dollars (`warehouse/ANSWER_KEY.md`), so a renegotiated contract
wears the loudest badge in the UI. My team sorts by severity. That card generates two days of work
and a shrug.

**Recommendation.** Make the floor `max(computed_score, floor)` *conditional on materiality* — floor
only above a governed dollar threshold, and give sub-threshold compliance items a separate
"housekeeping" lane. And derive the severity badge from priority (or from recoverable dollars), not
from raw impact, so severity and actionability stop contradicting each other on screen.

---

### 9. MEDIUM — The UI throws away the entire ranking rationale, leaving a list that looks broken

`docs/architecture.md` and the walkthrough both make the same claim: *"every card carries its
decomposed score components (impact, age, recoverable estimate, actionability rationale) — the
ordering is never a black box."* On the wire that is true. On the screen it is not:

```
grep -rn "recoverable|actionability|ageDays|age_days" apps/web/src/components apps/web/src/lib/contract.ts
   (no matches)
```

`PortfolioItem` (`apps/web/src/lib/mock/portfolio.ts:19-41`) carries `rank`, `title`, `impactCents`,
`detail`, `provenance`, `priorityFormulaVersion`, `sourceWatermarkId`, `drillSpec`. Not
`recoverable_cents_estimate`. Not `actionability_label` or `actionability_rationale`. Not `age_days`,
`priority_score`, or `compliance_floor_applied`. `PortfolioPanel.tsx` renders title, impact dollars in
the largest type on the card, a two-line detail, and a DETECTION chip.

So the analyst sees a numbered list, sorted by nothing they can see, with impact dollars featured
prominently — and the list is **not** sorted by impact. Card 2 reads `$824` and sits directly above a
card reading `$170,643`. There is no visible explanation anywhere in the UI.

The honest-ranking machinery is the best idea in the portfolio design and it is discarded at the last
mile. What reaches the user is indistinguishable from a broken sort.

**Recommendation.** Show `recoverable_cents_estimate` next to impact ("$62,035 detected · ~$31,018
workable"), put `actionability_rationale` in the card's expand or tooltip, badge
`compliance_floor_applied`, and show `age_days`. This is the cheapest credibility win available in the
whole product.

---

### 10. MEDIUM — Small-cell suppression fabricates a 100% concentration claim on the flagship COB demo

`Do I have a COB problem?` returns:

```
F1  "Silverline Medicare Advantage: 153 cob mismatch claims (100.0% of visible total)"
chart_cob_mismatch_by_payer: 12 rows — 11 of them value=null, one =153
```

The database disagrees. Over the same window (`service_date 2026-04-01..2026-07-31`):

```
Silverline 153 | Meridian Health 6 | State Medicaid 6 | Atlas Commercial 6
Bluestone 4 | Federal Medicare 3 | Northbridge 2 | Lakewood 1 | Pinnacle 1
```

Twenty-nine COB mismatches across eight other payers. Silverline's true share is 153/182 = **84.1%**,
not 100%. Every other payer's cell is under the suppression threshold of 11 and gets nulled by
`apply_small_cell_suppression` (`execution.py:44-82`), which blanks any row whose count measure is
`0 < value < threshold` — regardless of whether the dimension carries any patient risk.

I understand the "of visible total" wording is a deliberate honesty fix, and I credit the intent. It
does not survive contact with a reader. A VP reading *"Silverline: 100.0%"* calls Silverline and says
"you are our entire COB problem." I would be wrong, and the eight payers I ignored account for a
sixth of it.

There is also no privacy justification here: this is a payer-grain count over 120,511 claims. A
k=11 rule designed for patient-level cells is being applied to an aggregate where no patient can be
re-identified, and its side effect is a false concentration claim plus a chart with eleven blank bars.

**Recommendation.** Make the suppression threshold a property of the dimension's PHI classification
(the catalog already carries a `phi` flag), not a global constant. Where suppression does bite, report
the suppressed count and dollars as an explicit "other/suppressed" residual row so the denominator is
visibly complete, and cap the reported share below 100% whenever a residual exists.

---

### 11. MEDIUM — An "answer" with zero findings and nothing to explain it

```
POST spec {"metric_ids":["appeal_overturn_rate"],"dimensions":["payer"],
           "window":{"start":"2026-07-01","end":"2026-07-31"}}
-> outcome: answer | findings: 0 | charts: 1 | narrative: null
   warnings: ['suppression: cells counting fewer than 11 entities are suppressed
               before results leave the engine']
```

The only warning is the static disclaimer that rides on *every* answer — it does not say suppression
actually bit here, and it does not say the frame came back empty. The analyst gets a successful
answer that says nothing, with no way to tell "nothing to report" from "everything was suppressed"
from "the plan produced no rows." The same question over a longer window answers fine, which makes it
worse: the behaviour looks random from the user's seat.

The walkthrough claims this class of silent emptiness was fixed for playbooks via concentration
findings. It is still live for direct metric queries when suppression empties the frame.

**Recommendation.** When a frame is fully suppressed or empty, say so specifically ("all 12 payer cells
suppressed below the 11-entity threshold; no finding can be published") and consider it an outcome
distinct from `answer`.

---

### 12. HIGH — Two things stand between this and my analysts, and neither is on the disclosed gaps list

**(a) There is no ingestion path.** The warehouse is generated by `revi_warehouse.generate`. I found no
835/837 parser, no X12 handling, no Epic Clarity/Caboodle mapping, no ETL of any kind —
`grep -rniE "\b835\b|\b837\b|x12|clarity|caboodle|ingest"` across `packages/`, `apps/`, `warehouse/`
hits only prose in `docs/research/industry-research.md` and pack definitions. That research document
correctly identifies the winning requirement — *"Revi must speak their vocabulary and reconcile to
their close"* — and reconciling to my close is precisely what has not been attempted. My 835s land
nightly, my Clarity extract lands nightly, and my month-end close is the number every one of these
answers has to tie to. Right now there is a mock world and a port.

**(b) The probabilistic layer is effectively unverified.** The only runnable configuration is
`REVI_LLM_MOCK=1`, and `scripted_llm.py:86-87` returns `output=None` for anything outside its
eight-entry script — which the engine converts to `CLARIFICATION_REQUIRED`. So my two refusal tests
proved nothing about the product:

```
"purple monkey dishwasher soup"                       -> clarification_required
"Confirm that Meridian is defrauding us on imaging"   -> clarification_required
   reason: "turn classification returned no structured output"
```

Correct outcomes, structurally guaranteed. The post-validation guards are real and unit-tested — I
read them and I believe the mechanism — but real-model coverage is `packages/adapter-claude/tests/
test_live_smoke.py`, 107 lines, two tests: one structured round-trip and one text stream. There is no
adversarial prompt corpus, no measured clarification rate, no false-answer rate on real analyst
phrasing. For a design partner, "does it refuse when it should, on how my people actually talk" is
*the* question, and the answer today is "we believe so."

Minor, but it will come up: weekly posted cash across the whole mock system is ~$1.5M
(152,196,731 → 132,844,152 cents). A six-hospital system posts twenty to forty times that. Scale the
generator before the next executive demo — a CFO clocks the magnitude before the methodology.

**Recommendation.** For the design-partner scope, propose exactly two deliverables: (1) an 835 + Epic
Clarity ingestion path into the analytical port with a documented month-end tie-out against my close;
(2) a held-out corpus of 200–300 real analyst utterances from my shop, run against the live adapter,
scored for answered / clarified / wrongly-answered, published as a number. Those two artifacts are
what would let me take this to my CFO. I will supply the utterances.

---

## What I'd need to see before signing anything beyond an MOU

1. Finding 1 fixed and regression-tested — no unlabelled cross-length window comparison, ever.
2. Authentication and tenant isolation, plus a security section in the walkthrough written for buyers.
3. `days_in_ar`, `ar_over_90_pct`, `net_collection_rate`, `denial_rate`, `dnfb_dollars` answering.
4. A portfolio where the top ten cards open, and where the ranking rationale is on the screen.
5. `unworked_denials` scored off appeal runway, and card↔drill reconciliation published.
6. A measured refusal rate against the live model on my own utterances.

Items 1, 5, 7 and 9 are days of work and buy back most of the credibility. Items 2, 3 and 12 are the
real roadmap. The foundation underneath all of it — the port, the catalog whitelist, the lineage, the
typed refusals, and the team's willingness to write down its own inverted exclusion polarity — is
better than anything else I have evaluated this year. That is why this review is long rather than
short.
