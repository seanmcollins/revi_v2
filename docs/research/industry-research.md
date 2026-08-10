# RCM Industry Research Synthesis — for Revi

Synthesized 2026-08-07 from five research sweeps: (1) HFMA MAP Keys / KPI canon, (2) competitor
landscape, (3) EHR/PM vocabulary (Epic, athenahealth, Cerner/Oracle Health, MEDITECH, eCW, IDX),
(4) denial-management workflows and regulation, (5) conversational-analytics UX and trust patterns.
Audience: the Revi build (plan: `~/.claude/plans/your-mission-look-at-twinkly-frog.md`, esp. §4
mock warehouse + base pack and §8 frontend).

---

## 1. Executive summary

**What the industry expects from an RCM analytics product in 2026.**

1. **Standardized metric definitions are the price of admission.** HFMA's 29 MAP Keys (definitions
   updated Aug 2025) plus the Claim Integrity Task Force's standardized denial metrics (©2021) are
   the de facto semantic standard. Every credible product publishes each KPI as an explicit
   named-numerator / named-denominator fraction with points of clarification (date basis, gross vs
   net, exclusions, smoothing windows). This is *exactly* Revi's metric-contract design — the
   industry has independently converged on "metric contracts with grain/date-basis/denominator
   rules" as its trust architecture.
2. **The market arc is descriptive → predictive → agentic**, with conversational interfaces as the
   differentiating UX layer. VisiQuate's Ana (conversational NL → live visualization, since ~2018,
   KLAS 92.0), ENTER's CTRL ENTER ("Why was this denied?"), and the BI incumbents (Databricks
   Genie, Snowflake Cortex Analyst, Power BI Copilot, ThoughtSpot Spotter) all converged on the
   same trust stack: **governed semantic layer between LLM and warehouse + human-verified answers
   with visible badges + show-the-work provenance**. Raw text-to-SQL is the discredited baseline
   (80–90% benchmark accuracy collapses to 10–20% on real enterprise schemas; "silent failures" —
   wrong SQL that runs cleanly — kill adoption in a handful of answers). Keyword NLQ is dead
   (Tableau Ask Data retired 2024; Power BI Q&A retiring Dec 2026) because users had to know field
   names.
3. **Denial economics dominate the value narrative.** Initial denial rates are 11.4–11.8% and
   rising (Kodiak/Optum), ~84% of denials are potentially avoidable, ~65% are never reworked, and
   MA appeal overturn runs 80.7% while only 11.5% of denials are appealed. Products win on:
   initial-vs-final pairing, CARC/group-code line-level analytics, payer scorecards, overturn
   probability × dollars-at-stake worklist ordering, and payer-behavior detection (downcoding,
   records-request stalling, takebacks — the "Anomaly Manage" emerging category).
4. **Governance is the buying gate, not a feature.** Only 18% of health systems have mature AI
   governance (HFMA/Eliciting Insights Q2 2025, n=233); buyers demand audit trails, HITL
   boundaries, model change control, and HIPAA posture. Answers must reconcile to the system of
   record's close (ATB, period end), declare their date basis, and drill from KPI to claim.
5. **Answers must ship with actions.** VisiQuate pairs answers with playbook recommendations;
   Adonis ships alerts with resolution steps, scored by probability of cash recovery (explicitly
   filtering "administrative noise"). A number without a "so what / do what" is considered
   incomplete.

**Where Revi's design is strong** (validated by this research):

- Typed investigations over a governed catalog — the exact architecture the BI leaders shipped
  toward (semantic layer + closed operators beats text-to-SQL). Revi is architecturally *ahead* of
  the healthcare incumbents, whose "conversational" layers are mostly NL→dashboard shortcuts.
- Metric contracts with explicit date basis, denominator rules, and grain — mirrors HFMA MAP Key
  format (Purpose/Value/Calculation + points of clarification), which is the recognized trust idiom.
- Context header on every answer (window + basis + filters + watermark) matches the #1 documented
  trust pattern: date-basis transparency and declared smoothing conventions.
- DEFINITIONAL path ("what is PR3") maps directly to how analysts actually work — CARC/RARC/group
  code lookups are constant, and no competitor does governed definitional answers with provenance.
- Group-code + CARC pair keying of denials, overturned ÷ *decided*, underpayments-never-net,
  days-in-AR-ages-to-newest — all confirmed as the industry-correct semantics.
- Evidence grades + reconciliation invariant answer the "silent failure" problem by construction.

**Where Revi needs additions** (detailed in §2 and §6):

- **Initial vs final denial distinction.** The single most-cited credibility rule in denial
  analytics: initial denial rate (11.6%) and final/write-off rate (2.7%) must never be conflated.
  Revi's base pack has one `denial_rate`; it needs the initial variant (first-chronological denial,
  rebill exclusions, 3-month-average denominator) and the write-off-as-%-of-revenue pairing.
- **Paired volume-and-dollar denial views** — each alone misleads; the Task Force defines both.
- **Aged-A/R percentage forms** (AR >90 days as % of billed A/R, by payer group) — Revi has aging
  buckets but the % form is the number analysts quote against the <15–20% benchmark.
- **Denial cycle-time metrics** (time to appeal, time to resolution) and the "never worked" share —
  the abandoned-denials story (~65%) is the biggest opportunity metric in the domain.
- **Benchmark context in answers** — cited, sourced ranges (HFMA 95% CCR; Kodiak 11.6% initial) are
  a cheap, high-trust addition as pack knowledge entries.
- **EHR alias breadth** — the concept dictionary should absorb the vendor-status vocabularies
  (Epic DNB/CFB/WQ/HAR, athena HOLD/kick codes/unpostables, Cerner Edit Failure, MEDITECH
  unbilled), because answering in the user's native vocabulary is a documented trust signal.
- **Frontend trust affordances** — verified-contract badges, ambiguity → suggestions (not
  guessing), feedback triage, and drill-through lineage; see §6.

---

## 2. KPI canon

Deduplicated master table. **Status** column: **PLANNED** = in Revi's ~20 base-pack metrics
(plan §4); **ADD** = should add (feasible on the mock warehouse); **OOS** = out of scope for mock
data, with reason. Where sweeps conflicted on definitions/benchmarks, noted inline.

### 2.1 Denials & appeals

| Metric | Definition (numerator / denominator, date basis) | Benchmark | Status |
|---|---|---|---|
| **Remittance Denial Rate** (MAP AR-5) | Claims denied ÷ claims remitted; remittance-date basis. HFMA frames as "actionable denials." | target <5%; 5–10% typical | **PLANNED** — `denial_rate` (CLAIM grain, REMIT basis). Alias: "denial rate." |
| **Initial Denial Rate — % of claim volume** (HFMA Task Force) | First-chronological denial per claim ÷ total claims submitted, denominator = 3-month average prior to reporting month. Excludes rebills (UB-04 bill type XX7; CMS-1500 Box 22 "R"). | Kodiak avg 11.4% (2024) → 11.6% (2025); 41% of providers ≥10% | **ADD** — `initial_denial_rate`. Warehouse has submission dates + denial dates; add first-denial dedup + a rebill exclusion flag to `fact_claim`. The 3-mo-avg denominator convention is a contract "point of clarification" worth modeling. |
| **Initial Denial Rate — % of claim dollars** | Same, gross-charges-weighted. | — | **ADD** — dollar variant of the above (billed cents exist). Pairing volume+dollar views is a named trust pattern. |
| **Initial Denial Rate — line level** (Task Force Appendix 2, best practice) | Denied lines by CARC ÷ lines submitted; line-item charge, not full claim repeated. | — | **PLANNED** (partial) — `denied_dollars` is LINE grain; the line-level *rate* falls out of existing facts. Low-cost ADD if desired. |
| **Primary Denial Rate** (Task Force) | Zero-pay remits ÷ all primary-payer remits, trailing 4 weeks, excl. duplicates. | — | **ADD** — `primary_denial_rate`. `payer_sequence P|S|T` + `fact_remit` support it; exercises a trailing-window contract. |
| **Zero-pay vs partial-pay denial split** (legacy MAP 12/13) | Zero-paid claims denied ÷ claims remitted; partially paid claims denied ÷ claims remitted. | — | **ADD as dimension**, not new contracts — `denial_level` (claim vs line) on `fact_denial` already distinguishes; expose as certified scope dimension on denial metrics. |
| **Final Denial Rate** (Kodiak convention) | Denied dollars never collected ÷ claims (proprietary; not a MAP Key). | median 2.5%→2.7%; by payer: Medicaid 6%, MA 5%, commercial 3% | **PLANNED** (via `denial_write_off_dollars`); the *rate* form is the AR-6 row below. |
| **Denial Write-Offs as % of NPSR** (MAP AR-6) | Net dollars written off as denials (net of recoveries) ÷ average monthly NPSR; write-off-month basis. | target 2–5% of net revenue | **ADD** — ratio form of planned `denial_write_off_dollars` over net revenue (proxy: expected cents). "Net of recoveries" is a contract exclusion rule. |
| **% of Initial Denials Overturned** (Task Force; 3 variants) | Overturned+paid ÷ paid+adjusted, in gross charges, claim volume, and inpatient→observation conversion; numerator and denominator same period. | commercial appeal overturn ~54% (Premier); MA PA appeals 80.7% (KFF) | **PLANNED** — `appeal_overturn_rate` (overturned ÷ *decided*, matching Task Force intent). Obs-conversion variant OOS (no level-of-care status in mock schema). |
| **Time from Initial Denial to Appeal** (Task Force) | Days from initial denial remittance to appeal submission. | — | **ADD** — `days_denial_to_appeal`; `fact_denial` appeal fields support it. |
| **Time from Initial Denial to Claim Resolution** (Task Force) | Days from denial remittance to zero balance (± payment). | — | **ADD** — `days_denial_to_resolution` using `resolved_date`. |
| **Denials never worked %** | Share of denials with no appeal/rework activity. | ~65% industry (35–65% range cited) | **ADD** — `denials_unworked_pct`; derivable from appeal fields + resolution status. The headline opportunity metric for worklist products. |
| **Denial reason mix by CARC / group code** | Distribution of denied claims/dollars by group code + CARC. Canonical taxonomy: medical necessity/level of care, eligibility (incl. COB), authorization, notifications; Optum stage split front-end ~41–46%, mid ~17%, back ~32–34%. | registration/eligibility ~24% largest category | **PLANNED** — group_code+CARC pair keying; taxonomy rollup belongs in pack concepts, CARC scope on `denied_dollars` (LINE) with the designed GRAIN_INCOMPATIBLE demo on claim-grain `denial_rate`. |
| Denial risk score (pre-submission), overturn-probability worklist scoring | Predictive model outputs (Experian, Waystar, Sift, Etyon). | — | **OOS** — Revi is deliberately deterministic; no ML scoring. The *deterministic* analogs (rank by dollars at stake × deadline proximity) are in scope via ranking policies. |

### 2.2 A/R, aging & cash

| Metric | Definition | Benchmark | Status |
|---|---|---|---|
| **Net Days in A/R** (MAP FM-1) | Net A/R ÷ average daily NPSR. | <40 target; MGMA median 36 (better performers) / 47 | **PLANNED** — `days_in_ar` (SNAPSHOT, ages to newest data date — correct per industry). Note: practice guides also quote a gross-charge-basis "Days in A/R" (<35); the catalog must not conflate the two bases. |
| **Aged A/R as % of Billed A/R** (MAP AR-1; AR-2 by payer group; AR-3 incl. unbilled; AR-4 payer share of whole book) | Buckets 0-30/31-60/61-90/91-120/>120 ÷ total billed A/R (variants differ by denominator — a classic semantic trap). | >90 days <15–20% hospital (<10% per one sweep — sweeps conflict; use 15–20% w/ range note); >120 <5% ideal | **ADD** (% forms) — `ar_over_90_pct` etc.; Revi has `ar_balance` + aging buckets, the ratio + by-payer-group slices are `share_of_total` transforms + one contract note on billed vs total denominator. |
| **Cash Collections as % of NPSR** (MAP FM-2) | Patient service cash collected ÷ average monthly NPSR (trailing 3-mo avg convention). | ~100% | **ADD** — `cash_collections_pct_of_expected` using expected cents as net-revenue proxy; honest label on the proxy. |
| **Net Collection Rate** | Payments ÷ (charges − contractual adjustments). | 95–99%; MGMA ≥95%; <92% = leakage | **PLANNED** — `net_collection_rate`. |
| **Gross Collection Rate** | Payments ÷ gross charges. | context-only | **PLANNED** — `gross_collection_rate`. |
| **POS Cash Collections** (MAP PA-7) | POS payments (at/before service through ~7d post) ÷ total self-pay cash. | POS cash >2% of NPSR leading practice | **ADD** — `pos_collection_rate`: patient payments with post_date within N days of service ÷ total patient cash. `patient_cash_posted` + date bases support it. |
| **Net Days in Credit Balance** (MAP AR-9) | Credit balance dollars ÷ average daily NPSR. | minimal | **PLANNED** — `credit_balance_dollars` (SNAPSHOT); days form is a trivial transform. |
| **Average Days to Pay** (payer scorecard staple) | Submission → payment days, by payer. | — | **PLANNED** — `avg_days_to_pay`. |
| Bad Debt (AR-7), Charity Care (AR-8), Uninsured Discounts (FM-3), Uncompensated Care (FM-4), Charity % of Uncompensated | Write-off category ÷ gross revenue; reconciliation family (FM-4 = FM-3+AR-7+AR-8). | bad debt ~1.3–2% | **OOS** — mock transaction types (PAYMENT/CONTRACTUAL_ADJ/OTHER_ADJ/PATIENT_PAYMENT/REFUND) don't distinguish charity vs bad-debt vs uninsured-discount write-offs. Add concepts/definitions to pack only. |
| Unapplied cash %, takeback/recoupment rate | Suspense-posted payments; payer clawbacks (<1% of net rev high performers). | — | **OOS** — no suspense or payer-recoupment events in mock schema (REFUND models credit-balance refunds). Vocabulary in pack. |

### 2.3 Pre-billing, charge capture & claims

| Metric | Definition | Benchmark | Status |
|---|---|---|---|
| **Days in DNFB** (MAP PB-1) | Gross dollars discharged-not-final-billed ÷ average daily gross revenue; discharge basis. | 5–7 days acceptable (one HFMA source says >3–5 sustained is disruptive — cite as range) | **PLANNED** — `dnfb_dollars` (SNAPSHOT); days form = transform over `charges`. |
| Days in FBNS (PB-2) / DNSP (PB-3) | Final-billed-not-submitted; DNSP = DNFB + FBNS. | DNSP ~2 days (one source) | **OOS** — mock schema has no final-bill-vs-submission intermediate state (only discharge/submission dates). Concept + alias in pack; DNSP ≈ DNFB structurally in mock. |
| **Total Charge Lag Days** (MAP PB-4) | Σ(post date − service date) per CPT ÷ charge count. | best practice <24–48h | **PLANNED** — `charge_lag_days` (from `charge_entry_date`). |
| **Late Charges as % of Total Charges** (MAP CL-2) | Charges with post date >3 days after last service date ÷ total gross charges. | — | **ADD** — `late_charge_pct`; trivially computable from `charge_entry_date`, completes the charge-capture pair with charge lag. |
| **Clean Claim Rate** (MAP CL-1) | Claims passing edits with no manual intervention ÷ claims entering scrubber. | 95%; top 98% | **PLANNED** — `clean_claim_rate` (from `clean_claim` flag). |
| **First-Pass Yield / Resolution Rate** | Claims paid on first submission, no rework ÷ claims submitted. | ≥90–95% | **PLANNED** — `first_pass_yield` (from `first_pass_paid`). Distinct from CCR (pre-submission edits) — keep both concepts. |
| Bill lag | Service → claim submission days. | — | **PLANNED** — `bill_lag_days`. |
| Payer Rejection Rate | Front-end (pre-adjudication) rejections ÷ claims; <2%. | <2% | **OOS** — no clearinghouse-rejection events in mock. Critical *vocabulary* though: rejection ≠ denial disambiguation goes in concepts.yaml. |
| Charges, claim volume | Gross charges; claim counts. | — | **PLANNED** — `charges`, `claim_volume`. |
| Discharged Not Coded (DNC), unbilled claims % | Coding-queue backlog variants. | <2 days / <2% | **OOS** — no coding-status field; alias DNC → DNFB-adjacent concept. |

### 2.4 Contract performance & timely filing

| Metric | Definition | Benchmark | Status |
|---|---|---|---|
| **Underpayment / Payment Variance** | Expected reimbursement (contract-modeled) − actual payment; underpayments never net against overpayments. | payers underpay 7–11% (MGMA-attributed); 1–3% of net revenue lost; FinThrive found 32% of claims underpaid | **PLANNED** — `underpayment_variance` (never-nets rule confirmed industry-correct). |
| **Timely Filing At-Risk Dollars** | Open unsubmitted/deniable inventory vs payer filing limits. | — | **PLANNED** — `timely_filing_at_risk_dollars` (SNAPSHOT + pack filing_rules). Differentiator: no competitor surfaces *at-risk* (pre-denial) timely filing conversationally. |
| Expected reimbursement | Contract-modeled allowed amount (the baseline for variance). | — | **PLANNED** — `expected_cents` in schema; concept entry needed. |
| Zero-balance review, QPA/IDR analytics | Auditing "resolved" accounts; No Surprises Act arbitration tracking. | IDR provider win 85–88% | **OOS** — OON/NSA machinery absent from mock; concepts only. |

### 2.5 Front-end / patient access & financial management

| Metric | Definition | Benchmark | Status |
|---|---|---|---|
| Pre-Registration Rate (PA-2), Insurance Verification Rate (PA-3), Service Auth Rate IP/OP (PA-4/5), Schedule Occupied (PA-1), Uninsured Conversion (PA-6) | Front-end process rates. | verification ≥98% | **OOS** — no scheduling/eligibility/auth event data in mock warehouse. All five belong in concepts.yaml + definitional knowledge entries (users *will* ask "what is PA-4"). |
| Cost to Collect (FM-6/FM-7 by functional area) | RCM cost ÷ cash collected; functional areas must sum to total. | 2–4% (~3%) | **OOS** — no cost/labor data. Definitional entry only. |
| Case Mix Index (FM-5) | Σ DRG relative weights ÷ discharges (excl. normal newborns, Medicare-exempt). | — | **OOS** — no DRG weights in mock. Definitional entry only. |
| Practice MAP Keys (practice net days in A/R, practice cash collection %, professional services denial % = CPT units denied ÷ billed, operating margin, net income/FTE physician) | Physician-practice variants with distinct denominators. | — | **OOS** as separate contracts (mock is one enterprise); professional-services denial % is nearly identical to line-level denial rate — note as alias. Definitional entries for the rest. |

**Summary of ADDs (10):** `initial_denial_rate` (volume + dollar variants), `primary_denial_rate`,
`denial_write_off_pct_of_revenue`, `days_denial_to_appeal`, `days_denial_to_resolution`,
`denials_unworked_pct`, `ar_over_90_pct` (aged-A/R % family), `late_charge_pct`,
`pos_collection_rate`, `cash_collections_pct_of_expected` — plus zero-pay/partial-pay as a denial
scope dimension. All are computable from the already-planned warehouse schema with at most two
small additions (rebill-exclusion flag; first-denial dedup logic in the generator/answer key).

---

## 3. Concept & alias vocabulary

Seed material for `concepts.yaml`, `aliases.yaml`, and knowledge entries. Deduplicated across
sweeps; grouped by domain. **Bold** = concept id candidate; parenthetical = aliases/synonyms
including EHR-native terms. The design target of ~60+ concepts is easily met — prioritize the
starred (★) entries, which appeared in 3+ sweeps.

### 3.1 Remittance & denial codes (highest DEFINITIONAL traffic)

- ★ **carc** (claim adjustment reason code, reason code, adjustment reason) — paraphrase ~20, never redistribute X12 list
- ★ **rarc** (remittance advice remark code, remark code)
- ★ **group_code** / **cagc** (claim adjustment group code; CO = contractual obligation, PR = patient responsibility, OA = other adjustment, PI = payer initiated, CR = correction/reversal) — "what is PR3" resolves to group PR + CARC 3
- ★ **remit** (835, ERA, electronic remittance advice, remittance)
- **cas_segment** (claim/line adjustment detail on 835), **plb_segment** (provider-level adjustment; takebacks/offsets)
- **claim_transaction** (837, 837I institutional / UB-04, 837P professional / CMS-1500)
- **eligibility_transaction** (270/271), **auth_transaction** (278), **claim_status_transaction** (276/277, 277CA), **ack** (999)
- **eob** (explanation of benefits), **zero_pay_remit**
- Canonical CARC examples for knowledge entries: CO-29 timely filing, CO-97 bundled/included, CO-96 non-covered/medical necessity, CO-197 no prior auth, CO-22/OA-22 COB, CO-18 duplicate, CO-50 medical necessity, CO-4 modifier inconsistent

### 3.2 Denial lifecycle

- ★ **denial** vs **rejection** (rejection = front-end/clearinghouse refusal pre-adjudication; denial = adjudicated negative determination) — the canonical disambiguation
- ★ **initial_denial** vs **final_denial** (first-chronological vs post-appeal standing); **primary_denial**
- **hard_denial** vs **soft_denial**; **technical_denial** vs **clinical_denial**
- **avoidable/recoverable quadrant** (Optum: avoidable × recoverable × situationally-recoverable)
- **denial_category taxonomy** (Task Force: medical necessity & level of care; eligibility incl. COB; authorization; notifications) + **rfi_denial** (request for information / ADR / medical-records request — payer stall tactic)
- ★ **appeal** (appeal window, filing limit — payer-specific 65–180 days; levels: redetermination, reconsideration, IRE, ALJ, external review; peer-to-peer)
- **overturn** / **uphold** / **partial overturn**; **observation_conversion** (inpatient→obs downgrade)
- **rebill** (corrected claim, resubmission, frequency code 7, bill type XX7, Box 22 "R") — excluded from initial-denial denominators
- **write_off** (denial write-off net of recoveries; contractual vs administrative/avoidable write-off; small-balance write-off)
- **downcoding** (payer E/M level reduction — Cigna R49 et al.), **drg_downgrade**, **clinical_validation_denial**, **takeback** (recoupment, clawback, offset)
- **timely_filing** (filing limit, TFL), **cob** (coordination of benefits, primacy, MSP, crossover claim), **prior_auth** (precert, authorization, gold carding), **two_midnight_rule**, **condition_code_44**

### 3.3 A/R & billing pipeline (heavy EHR alias load)

- ★ **dnfb** — Epic: *DNB, Discharged Not Billed, Candidate for Billing/CFB*; Cerner: *Discharged Not Ready To Bill*; MEDITECH: *unbilled accounts, unprinted bills*; also DNSP, FBNS, DNC (discharged not coded)
- ★ **ar_aging** (aged trial balance/ATB, aging buckets 0-30/31-60/61-90/91-120/>120, "AR over 90", billed vs unbilled vs total A/R denominators)
- ★ **days_in_ar** (net days in A/R, DAR, DSO, true A/R days)
- **workqueue** — Epic: *WQ, charge review WQ, claim edit WQ, follow-up WQ, credit WQ, denial WQ, BDC record*; athena: *worklist, hold bucket, Workflow Dashboard*; Cerner: *work queue states — Edit Failure, Pending Manual Review, Past Due, Technical Denial, EOB Variance, Credit Balance*; IDX: *TES workfile, ETM task*
- **account** — Epic: *HAR, hospital account, HSP account, guarantor account*; IDX: *BAR invoice*; athena: *claim*; **encounter/visit** hierarchy
- **claim_hold** — Epic: *stop bill, bill hold, DNB check*; athena: *HOLD, MGRHOLD, CBOHOLD, ATHENAHOLD, DROP (ready to submit), kick code/kick reason (DRPBILLING, PTRESP, RVCLOSE…)*; Cerner: *billing hold*; MEDITECH: *proration hold, INSBAL*
- **charge_capture** (charge entry, charge lag, late charges, charge reconciliation, missing charges/missing slips (athena), Charge Router / Revenue Guardian (Epic), charge session)
- **clean_claim**, **claim_scrubbing** (scrubber, edits, global vs local rules (athena), front end scrubs, clearinghouse), **claim_run**, **claim_status** (enriched claim status)
- **unpostables** (athena: unmatched remittance/mail), **unapplied_cash** (undistributed, suspense)
- **credit_balance** (refund, overpayment), **bad_debt_transfer** (agency placement), **dunning** (statement escalation), **self_pay** (self-pay after insurance, patient responsibility: copay, coinsurance, deductible), **early_out**
- **financial_class** (payer group, payer mix, FSC (IDX), fin class), **payer_sequence** (primary/secondary/tertiary, benefit order (Cerner), visit filing order (Epic))
- **close** (day close, month-end close, period end (MEDITECH), ATB reconciliation)

### 3.4 Revenue & contract

- ★ **npsr** (net patient service revenue, net revenue) vs **gross_charges** (chargemaster/CDM amounts) — the gross-vs-net basis discipline
- ★ **contractual_adjustment** (allowance, contractual write-off) vs **allowed_amount** vs **expected_reimbursement** (contract modeling, adjudication simulation)
- ★ **underpayment** (payment variance, contract variance (Cerner queue name)) — never nets
- **revenue_leakage**, **yield**, **cost_to_collect** (functional areas: patient access, PFS/patient accounting, HIM), **case_mix_index** (CMI, DRG relative weight), **cdi**, **pos_collections** (point-of-service, upfront collections), **propensity_to_pay**, **charity_care** (FAP, financial assistance), **uncompensated_care**, **uninsured_discount**
- **date_bases** — service date (DOS), post date, submission date, remit/remittance date, discharge date, write-off month; per-metric basis is a contract property

### 3.5 Standards bodies & reference frames (knowledge entries)

- **map_keys** (HFMA MAP Keys, 29 KPIs, five domains PA/PB/CL/AR/FM; MAP App peer benchmarking; MAP Award), **claim_integrity_task_force**, **mgma**, **aapc**, **kodiak** (formerly Crowe RCA), **optum_denials_index**, **experian_state_of_claims**, **klas**
- Regulatory: **cms_0057_f** (72h/7d PA decisions, specific denial reasons, FHIR PA API 2027), **no_surprises_act** (IDR, QPA, open negotiation, batching), **cms_4201_f** (MA two-midnight)

---

## 4. Competitor landscape

| Product | Category | Key capabilities | Conversational / AI | Gaps Revi can exploit |
|---|---|---|---|---|
| **VisiQuate (Ana, Flo, Policy Pulse)** | RCM analytics (KLAS leader, 92.0) | Denial analytics w/ AI root-cause, payer action center, CFO forecasting, Etyon ML scoring, anomaly alerting ($74M auth spike caught "in days") | **Closest competitor.** Ana: NL → real-time visualization ("denial rate by payer"); role-specific Ana Agents; playbook recommendations | Ana is NL→dashboard, not typed multi-turn investigation; no visible metric contracts, no reconciliation guarantees, no governed definitional answers; KLAS users "want more AI," complain of generic training |
| **Adonis Intelligence** | AI RCM ops (Series C $40M) | Calibrated alerting (filters "administrative noise," scores by cash-recovery probability), denial clustering, smart worklists by dollar impact, "true underpayments not false flags," exec→claim drill-down | AI agents autonomously work claims; alerting + prioritization, not Q&A | No conversational analytics at all; scoring is surfaced as a prioritization signal, not as a published derivation the analyst can audit — Revi's provenance is deterministic and inspectable; alert-first not question-first |
| **Waystar (AltitudeAI)** | Clearinghouse + AI suite (Best in KLAS claims 91.8) | AltitudePredict (denial prediction), AltitudeAssist (pre-submission auto-fix), AltitudeCreate (GenAI appeals, 3x faster), $15.5B denials prevented; peer benchmarking | Agentic workflow automation (Jan 2026), Google/Gemini partnership; no analyst Q&A interface | Rejection detail is returned in clearinghouse form rather than as analyst-readable explanation; analytics is dashboards + benchmarks, not investigation; clearinghouse lock-in |
| **FinThrive (Fusion)** | RCM data platform | FHIR Data Hub, Insights Hub ML viz, 30/60/90 cash forecast, Denials & Underpayments Analyzer (32% of claims underpaid finding), 50+ AI use cases | Agentic next-best actions; no NL analytics | UI "not intuitive," breadth "overwhelming" (SelectHub 3.4/5) — an opening for a focused conversational surface |
| **Experian Health (AI Advantage)** | Claims mgmt + denial AI | Predictive Denials (per-claim risk w/ contributing elements), Denial Triage (value segmentation); users ~4% denial rate vs 10%+ | Predictive, in-workflow; no Q&A | No investigation/RCA surface; scoring explanations limited to flagged claim elements |
| **ENTER (CTRL ENTER)** | Full-stack AI RCM | Desktop NL assistant over EMR/PM/portals — "Why was this denied?"; contract-aware pre-flighting; 98.5% contract value collected claim | Clearest "talk to your RCM" entrant | Overlay assistant, not a governed analytics platform; no semantic catalog, no auditability story; unproven at enterprise |
| **Anomaly (Smart Response, Manage)** | Payer-behavior analytics | Claim-line payment/denial prediction (">99% precision," 3B claims); payer behavior patterns: downcoding, records-request stalling, clawbacks ($110M found at one system) | Predictive engine; defines the emerging payer-behavior category | Prediction-first; Revi can answer payer-behavior *questions* deterministically (variance, lag shifts, denial-mix shifts by payer) with provenance |
| **Sift Healthcare** | Payments intelligence | Overturn-probability scoring, payer response-timing models, Quality Score cash forecasting, 329 MS-DRG playbooks; embeds in Epic/Cerner WQs | ML scores in existing workqueues | No conversational layer; scores not explanations |
| **MD Clarity (RevFind) / Rivet** | Underpayment & contract | Adjudication-simulating expected pay at CPT/modifier grain; what-if contract modeling; Rivet groups underpaid claims into valued "projects" (~$6K/yr entry) | None | Point solutions; no cross-domain investigation ("is this denial spike or underpayment?") |
| **R1 (R37/Phare OS), Ensemble (EIQ), Optum (Integrity One)** | Enterprise managed services + AI | Massive data assets (180M payer transactions, 124M-remit Denials Index), forward-deployed engineers, ~$1B recoveries | Agentic ops at scale; EIQ writes insights back into EHR | Services-embedded, not self-serve; buy = outsourcing decision. Revi serves the analyst who stays in-house |
| **Epic Resolute / athenaCollector / Cerner RevElate / MEDITECH BCA** | EHR-native RCM | Workqueues, Radar/Financial Pulse benchmarking, BCA dashboards, state-based queues; the system-of-record vocabulary | SlicerDicer/Reporting Workbench self-service; no NL | Reporting requires knowing the tool; cross-module questions ("COB problem?") need an analyst. Revi must *speak their vocabulary* and reconcile to their close |
| **Databricks Genie / Snowflake Cortex Analyst / Power BI Copilot / ThoughtSpot Spotter / Tableau Pulse / Zenlytic** | Horizontal conversational BI | Semantic layers, verified/trusted answers, VQR + regression evals, SSE streaming status, feedback triage, deterministic middle layers | The trust-pattern reference set (see §6) | Zero RCM domain content; a health system would spend 12+ months building the semantic model Revi ships. Hakkoda-style consultancies confirm demand for exactly this build |

**Positioning takeaway:** nobody combines (a) governed RCM domain semantics, (b) typed multi-turn
investigation with reconciliation guarantees, and (c) BI-grade trust affordances. VisiQuate is the
only overlap on (a)+(c)-lite; the BI platforms have (c) but not (a); the AI RCM startups have (a)
data but neither (b) nor (c).

---

## 5. Denial-management functionality checklist

What a credible denial analytics offering must answer/do. ✓ = Revi's plan covers; ◐ = partial /
needs the §2 ADDs; ✗ = out of scope (say so honestly in-product).

**Rates & framing**
- [◐] Report initial AND final denial rates as a pair, never conflated (11.6% vs 2.7% is the canonical example) — needs `initial_denial_rate` + AR-6 ratio ADDs
- [◐] Paired volume-and-dollar views for every denial rate — needs dollar variant
- [✓] Line-level (CARC/CPT) drill-down beneath claim-level headline rates; denials keyed on group code + CARC pair
- [✓] Zero-pay vs partial-pay distinction (via `denial_level` dimension ADD)
- [✓] Correct exclusions: rebills out of initial-denial denominators; write-offs net of recoveries; first-chronological-denial dedup (generator must plant these)
- [✓] Date-basis discipline: denials on remit date, write-offs on write-off month — declared in every answer

**Slicing & root cause**
- [✓] By payer / payer category / plan / facility / service line / provider / month-week; payer × CARC mix shifts over time
- [✓] Denial-category taxonomy rollup (medical necessity, eligibility/COB, auth, notifications) as pack concepts over CARC pairs
- [✓] COB-specific investigation (planted scenario 2: other_insurance & primary mismatch + CARC 22/OA)
- [✓] Attribution toward upstream stage (front-end/mid/back) via category taxonomy — knowledge entry for the Optum 41-46% front-end stat
- [✗] Predictive denial-risk scoring pre-submission — deliberately not (deterministic platform); say so

**Appeals & recovery**
- [✓] Overturn rate = overturned ÷ decided (industry-correct)
- [◐] Time-to-appeal and time-to-resolution cycle metrics — ADDs
- [◐] Never-worked share (~65% industry) — ADD; the biggest "money on the table" narrative
- [✓] Timely-filing at-risk dollars against payer filing rules (differentiating: pre-denial early warning)
- [✗] Appeal-letter generation / deadline-countdown workflow — not an analytics concern for v1; vocabulary in pack

**Payer behavior**
- [✓] Underpayment variance by payer/service line, never netted (planted scenario 4)
- [✓] Payment-lag shifts by payer (planted scenario 3: remit→post lag +4d) — this *is* payer-behavior analytics done deterministically
- [✓] Payer scorecard shape via generic `dimension_scorecard` playbook (denial rate, days to pay, first-pass yield, underpayment by payer)
- [✗] Downcoding / records-request-stalling / takeback detection — no supporting events in mock; name them in knowledge entries

**Trust plumbing (from §6, denial-specific)**
- [✓] Drill-through lineage: KPI → payer → CARC → claim-level evidence rows (EvidenceDrawer + RowEvidenceProbe)
- [✓] Benchmarks cited with source + year as governed pack knowledge, shown as ranges not single targets
- [✓] Definitional answers for any code/term ("what is PR3", "what is a soft denial") with pack provenance, zero probes

---

## 6. Frontend feature implications (ranked)

Ranked by (trust impact × differentiation × build cost). Items 1–6 are load-bearing for
credibility; 7–12 are high-value differentiators; 13–16 are polish.

1. **Context header on every answer** (already planned §8) — window + date basis + filters +
   cohort + watermark chips. This is the single most-documented trust pattern across all five
   sweeps (HFMA points-of-clarification; eCW's claim-date-vs-service-date toggle; Genie's applied
   filters). *Extend:* the date-basis chip should be explicit ("REMIT basis") and clickable to a
   definitional card.
2. **Governed-metric badge with contract provenance** — every metric answer carries a "governed"
   badge (analogous to Power BI verified ✓ / Genie "Trusted" / Tableau certified). Click →
   numerator, denominator, date basis, exclusions, contract version, pack version. The badge
   means "a human governed this definition," never "the AI is confident." Revi's metric contracts
   make this nearly free and no healthcare competitor has it.
3. **Evidence drawer / drill-through lineage** (planned) — KPI → probes → contract versions →
   operators → cache hits → reconciliation status → (masked) evidence rows. "Every aggregate
   auditable to a source remit" is the enterprise procurement gate. Surface truncation and
   suppression explicitly.
4. **Clarification as first-class UI, never guessing** (planned; harden it) — on ambiguity, render
   button options of *answerable* interpretations (Cortex Analyst's `suggestions` pattern: return
   choices instead of SQL). Low-confidence classification must visibly ask, not silently pick.
5. **Stage rail with streaming progress** (planned) — typed stage events during tens-of-seconds
   latency (interpreting → planning → executing → reconciling → narrating). Genie's thinking
   steps double as progress indicator and audit artifact; show plan-diff summaries ("kept window,
   added filter payer=Medicaid — from turn 3").
6. **Show-the-interpretation panel** — before/with results, display how the question was read:
   resolved metric, window (with fractional-window resolution to concrete dates), filters,
   synonym mappings ("payer payments → payer-source cash excluding patient payments"). Power BI
   shows the matched trigger phrase; Revi's typed interpretation is strictly richer — render it.
7. **Initial-vs-final and volume-vs-dollar paired presentation for denial answers** — when a
   denial-rate question is asked, the FindingCard shows the pair (or offers the counterpart as a
   suggested refinement). Conflating them is the domain's #1 credibility failure.
8. **Benchmark context chips** — findings display cited benchmark ranges from pack knowledge
   ("11.6% Kodiak 2025 avg; <5% target") with source + year. Ranges/percentiles, not single
   targets. Cheap, high-trust, and expected (Epic Financial Pulse, MAP App set the habit).
9. **Chart-click → typed refinement** (planned — keep) — bar/point click posts
   `{op: DrillInto, target}` with no NL in the loop. ThoughtSpot's "drill anywhere, no dead ends"
   is the reference; every answer is a live object with suggested follow-up refinements rendered
   as chips.
10. **Reconciliation & watermark banners** (planned) — "parts sum to whole ✓" indicator on
    drill-downs; explicit RECONCILIATION_FAILED flag never hidden; watermark-stale banner with
    stay-pinned vs re-anchor choice. Exhaustive-and-mutually-exclusive totals are a documented
    user anxiety (Cerner's one-queue-per-balance design; ATB reconciliation habit).
11. **Feedback triage with visible closure** — per-answer Yes / Fix it / Request review (Genie's
    three-way), persisted to traces for pack-learning's reserved seat. Deliberately no
    auto-learning — human-gated improvement is itself the trust story; say so in the UI.
12. **Definitional cards** — "what is PR3" renders a governed definition card (term, group code +
    CARC semantics, pack version, related concepts) distinct from analytical answers; also
    reachable by clicking any code/term chip anywhere in the UI. Zero competitors do this.
13. **Grade badges with plain-language legend** (planned) — direct/derived/proxy on every finding;
    the cash-outlook DERIVED estimate gets a visibly qualified presentation (range + drivers +
    "estimate, not forecast" label).
14. **Portfolio panel with drill-in continuity** (planned) — top-issues cards ranked by governed
    ranking policy (dollar impact), each with a one-click drill that opens an ordinary session
    turn. Mirrors the industry's dollar-impact-first worklist pattern without black-box scoring.
15. **Answer + recommended action pairing** — when a playbook concludes (denial spike, COB), the
    narrative ends with playbook-sourced next steps (governed pack content, not LLM invention).
    VisiQuate/Adonis set the expectation that numbers ship with actions.
16. **Honest-limitation styling** — the §2.8 non-answer path gets deliberate, well-designed
    presentation ("can't support this because X is missing") rather than error styling.
    Constraint disclosure is a trust affordance (Cortex Analyst documents its own multi-turn
    limits in-product).

**Anti-patterns to avoid (documented failures):** keyword-NLQ requiring field-name knowledge
(retired by both Tableau and Microsoft); silent failures (plausible wrong numbers — Revi's
reconciliation + grades exist to prevent this; never bypass); uncalibrated alert floods
(Adonis markets noise-filtering as the antidote); one generic assistant over everything instead
of scoped domain competence; dashboards without drill-to-evidence.

---

## 7. Sources

Consolidated, deduplicated across sweeps.

### HFMA / standards / benchmarks
- https://www.hfma.org/data-and-insights/map-initiative/map-keys/
- https://www.hfma.org/data-and-insights/map-initiative/map-award/
- https://www.hfma.org/guidance/standardizing-denial-metrics-revenue-cycle-benchmarking-process-improvement/
- https://trubridge.com/wp-content/uploads/2024/02/HFMA-Standardizing-Denial-Metrics-Revenue-Cycle-Benchmarking-Process-Improvement.pdf
- http://static1.1.sqspcdn.com/static/f/1102518/24673391/1396563330183/ct_chfp_webinar_2014_map_keys.pdf
- https://www.hfma.org/revenue-cycle/the-kpis-that-define-revenue-cycle-excellence/
- https://www.hfma.org/finance-and-business-strategy/hospital-financial-and-revenue-cycle-benchmarks-paint-a-complicated-picture-heading-into-the-new-year/
- https://www.hfma.org/revenue-cycle/kpis/7-kpis-providers-should-be-tracking/
- https://www.hfma.org/press-releases/health-system-adoption-of-ai-outpaces-internal-governance-and-strategy/
- https://www.hfma.org/fast-finance/aca-marketplace-plans-payment-denial/
- https://www.hfma.org/payment-reimbursement-and-managed-care/no-surprises-act-idr-data-provider-wins/
- https://carecloud.com/continuum/hfma-map-keys-guide/
- https://valerionhealth.com/blog/hfma-revenue-cycle-kpis/
- https://www.mgma.com/articles/data-mine-measuring-success-finding-the-right-metrics-to-optimize-the-revenue-cycle
- https://whitespacehealth.com/blogs/mgma-ar-benchmark/
- https://www.enjoincdi.com/blog/hospital-denial-rates-benchmarks-trends-what-the-data-shows-2026/
- https://www.businesswire.com/news/home/20250227049715/en/Healthcare-Providers-Facing-Stiff-Headwinds-on-Revenue-Cycle-Performance-Kodiak-Solutions-Data-Show
- https://www.businesswire.com/news/home/20240808692914/en/Health-insurers-increasingly-use-information-requests-to-delay-paying-billions-of-dollars-to-medical-providers-Kodiak-Solutions-data-show
- https://www.beckerspayer.com/payer/claims-denial-rates-up-prior-auth-denials-down-in-2024-report/
- https://marketplace.optum.com/content/dam/change-healthcare/marketplace-assets/outcomes-and-insights/2024-denials-index.pdf
- https://business.optum.com/en/insights/denials-index.html
- https://www.experian.com/blogs/healthcare/state-of-claims-2025/
- https://premierinc.com/newsroom/policy/claims-adjudication-costs-providers-257-billion-18-billion-is-potentially-unnecessary-expense
- https://premierinc.com/newsroom/blog/trend-alert-private-payers-retain-profits-by-refusing-or-delaying-legitimate-medical-claims
- https://www.kff.org/medicare/medicare-advantage-insurers-made-nearly-53-million-prior-authorization-determinations-in-2024/
- https://oig.hhs.gov/reports/all/2026/medicare-advantage-organizations-overturned-nearly-all-appealed-prior-authorization-denials-for-skilled-nursing-facility-admission-raising-concerns-about-initial-denials/
- https://oig.hhs.gov/oei/reports/OEI-09-19-00350.pdf
- https://www.kff.org/medicaid/new-oig-report-examines-prior-authorization-denials-in-medicaid-mcos/
- https://www.mdclarity.com/blog/revenue-cycle-metrics-rcm-kpis
- https://www.mdclarity.com/blog/payment-variance-healthcare
- https://www.mdclarity.com/blog/dnfb-in-healthcare
- https://www.plutushealthinc.com/post/revenue-cycle-management-kpi
- https://www.medicalbillersandcoders.com/blog/net-collection-ratio-benchmarks-multi-specialty-groups/
- https://www.caplinehealthcaremanagement.com/gross-collection-ratio-vs-net-collection-ratio-as-kpi-in-medical-billing/
- https://ams-solutions.com/kpis-for-an-effective-healthcare-revenue-cycle-management/
- https://trubridge.com/resources/accounts-receivable-aging-the-importance-of-30-60-and-90-day-benchmarks/
- https://www.rtacpa.com/benchmarking-accounts-receivable-90-days-old-or-older/
- https://www.revecore.com/insights/resources/payment-variance-vs-underpayment
- https://www.aspirion.com/understanding-payment-variance-in-healthcare-revenue-cycle-management/
- https://www.ensemblehp.com/blog/underpayments-are-undermining-your-revenue-learn-how-to-identify-recover-resolve-them/
- https://www.mrsa1.net/ifitcanbemeasured
- https://evidence.care/denials-management-in-healthcare/
- https://revcosolutions.com/revenue-cycle-kpis-13-crucial-metrics-to-boost-performance/
- https://www.bristolhcs.com/blog/blog-detail/benchmarking-collection-rates-how-top-practices-maximize-every-dollar-earned
- https://www.techtarget.com/revcyclemanagement/news/366601124/Difference-Between-Clean-Claims-Initial-Claim-Denials-Key-Hospital-KPI
- https://www.techtarget.com/revcyclemanagement/feature/Breaking-down-claim-denial-rates-by-healthcare-payer
- https://www.healthcarefinancenews.com/news/8-keys-help-physician-practices-track-financial-performance
- https://www.medprecisionbilling.com/resources/days-in-ar-formula-and-benchmark/
- https://tsico.com/denial-management-crisis/
- https://www.counterforcehealth.org/post/insurance-denial-statistics-why-80-of-appeals-succeed-but-only-1-try/
- https://www.fiercehealthcare.com/payers/commonwealth-fund-21-adults-experienced-coverage-denial-past-year
- https://aegishealth.us/blog/7-metrics-to-track-to-reduce-claim-denials
- https://dexur.com/a/initial-denials/1796/
- https://dexur.com/a/denial-issue-types-routing-tracking-analytics/1772/

### Regulation & appeals
- https://www.cms.gov/newsroom/fact-sheets/cms-interoperability-prior-authorization-final-rule-cms-0057-f
- https://www.cms.gov/medicare/appeals-and-grievances/mmcag/downloads/managed-care-appeals-flow-chart-.pdf
- https://www.medicare.gov/providers-services/claims-appeals-complaints/appeals/medicare-health-plans
- https://myersandstauffer.com/insights/blog-prior-authorization-provisions-implementation-timelines-update/
- https://muni.health/blog/insurance-appeal-deadlines-2026
- https://muni.health/blog/medicare-advantage-appeal-letter-template-2026
- https://muni.health/blog/medicare-redetermination-appeal-guide-2026
- https://muni.health/blog/prior-authorization-denial-complete-guide-2025
- https://insights.wchsb.com/2025/09/03/cignas-e-m-downcoding-policy-a-critical-revenue-cycle-analysis/
- https://providernewsroom.com/cigna-healthcare/new-reimbursement-policy-for-professional-evaluation-and-management-services-claims-effective-october-1-2025/
- https://aasm.org/growing-trend-of-downcoding-among-commercial-payers/
- https://evidence.care/new-insights-on-medicare-advantage-plans-and-the-two-midnight-rule/
- https://www.ensemblehp.com/blog/two-midnight-rule-qa/
- https://www.beckershospitalreview.com/finance/providers-won-88-of-no-surprises-idr-determinations-in-early-2025-5-notes/
- https://www.healthcaredive.com/news/no-surprises-disputes-idr-2025-cms/810525/
- https://www.federalregister.gov/documents/2026/06/04/2026-11140/federal-independent-dispute-resolution-operations
- https://www.cms.gov/newsroom/fact-sheets/federal-independent-dispute-resolution-operations-final-rule
- https://www.hklaw.com/en/insights/publications/2026/06/federal-idr-process-overhaul-finalized
- https://www.nsatracker.com/idr-entities
- https://www.multistate.us/insider/2025/8/14/prior-authorization-reform-gains-momentum-in-states
- https://ediacademy.com/blog/835-remittance-accuracy-why-carc-rarc-and-cagc-code-combinations-matter/
- https://www.rivethealth.com/blog/carcs-rarcs-claim-adjustment-remittance-advice-codes
- https://signaledi.com/blog/edi-835-remittance
- https://behavehealth.com/glossary/835-transaction
- https://www.rivethealth.com/blog/front-end-issues-cause-about-half-of-denials

### Competitors
- https://www.waystar.com/blog-powerful-ai-examining-results-of-waystar-altitudeai-in-rcm/
- https://investors.waystar.com/news-releases/news-release-details/waystar-introduces-agentic-ai-advance-toward-autonomous-revenue
- https://www.waystar.com/news/waystar-advances-ai-innovation-with-google-cloud-to-accelerate-the-autonomous-revenue-cycle/
- https://www.waystar.com/our-platform/analytics-reporting/analytics/
- https://www.waystar.com/our-platform/denial-prevention-recovery/denial-appeal-management/
- https://www.cdomagazine.tech/aiml/waystar-launches-altitudeai-to-revolutionize-denied-claims-process-with-genai
- https://atlasbillers.com/vendor-guides/clearinghouses/waystar/
- https://finthrive.com/news/finthrive-debuts-denials-and-underpayments-analyzer-a-unified-solution-for-denials-and-underpayments-at-hfma-2025
- https://finthrive.com/news/finthrive-introduces-finthrive-fusion
- https://finthrive.com/news/finthrive-introduces-agentic-ai-at-hfma-2025-to-help-customers-transform-healthcare-revenue-cycle-management-performance
- https://finthrive.com/news/finthrive-highlights-agentic-ai-powered-rcm-platform-at-himss-showcasing-50-ai-and-automation-use-cases-across-unified-fusion-architecture
- https://finthrive.com/blog/why-ai-predictive-denials-are-the-future-of-claims-management
- https://www.selecthub.com/p/revenue-cycle-management-software/finthrive/
- https://www.experian.com/blogs/healthcare/ai-advantage-transforming-claim-denials-management/
- https://www.experian.com/content/dam/marketing/na/healthcare/brochures/ai-advantage-predictive-denials-product-sheet.pdf
- https://www.experian.com/content/dam/marketing/na/healthcare/brochures/ai-advantage-denial-triage-product-sheet.pdf
- https://www.experianplc.com/newsroom/press-releases/2024/experian-health-ranked-best-in-klas-in-claims-management--cleari
- https://www.optum.com/en/newsroom/health-tech/optum-leads-revenue-cycle-management-innovation-new-ai-powered-solution.html
- https://business.optum.com/en/operations-technology/revenue-cycle-management.html
- https://www.r1rcm.com/news-and-press/r1-launches-r37-ai-lab-in-partnership-with-palantir/
- https://www.r1rcm.com/newsroom/r1-announces-phare-os-expansion-to-help-providers-navigate-growing-complexity-and-margin-pressure
- https://hospitalogy.com/articles/2026-07-13/the-revenue-cycle-has-an-infrastructure-problem-r1-built-the-os-to-solve-it/
- https://www.techtarget.com/revcyclemanagement/news/366625065/R1-secures-investment-for-agentic-AI-in-revenue-cycle-management
- https://akasa.com/
- https://akasa.com/solutions/claim-status/
- https://www.adonis.io/platform/intelligence
- https://www.adonis.io/resources/introducing-adonis-intelligence
- https://www.prnewswire.com/news-releases/adonis-raises-40m-series-c-to-equip-healthcare-providers-with-aidriven-revenue-cycle-operations-302722199.html
- https://www.alleywatch.com/2026/03/adonis-ai-healthcare-payments-revenue-cycle-orchestration-platform-akash-magoon/
- https://hitconsultant.net/2026/06/22/adonis-launches-epic-connection-hub-rcm-ai/
- https://elion.health/products/adonis-intelligence
- https://www.findanomaly.com/
- https://www.businesswire.com/news/home/20260604461491/en/Anomaly-Launches-Manage-Bringing-Power-Back-to-Providers-in-their-Interactions-with-Payers
- https://www.businesswire.com/news/home/20260513966755/en/Anomaly-Secures-an-Additional-$17M-to-Fundamentally-Change-How-Health-Systems-Engage-With-Payers
- https://www.fiercehealthcare.com/health-tech/startup-anomaly-rolls-out-ai-tech-help-predict-potential-insurance-claims-denials
- https://www.mdclarity.com/revfind
- https://www.mdclarity.com/platform-overview
- https://www.mdclarity.com/clarity-flow
- https://www.mdclarity.com/comparison/best-denial-management-software
- https://www.rivethealth.com/payer-performance
- https://www.rivethealth.com/payment-variance-detection
- https://www.capterra.com/p/205843/Rivet/
- https://www.sifthealthcare.com/
- https://www.sifthealthcare.com/recovery/
- https://www.sifthealthcare.com/denials-management
- https://www.prnewswire.com/news-releases/sift-healthcare-releases-fourth-annual-denials-insights-report-identifying-nine-critical-payer-trends-shaping-reimbursement-risk-in-2026-302697889.html
- https://www.cedar.com/solutions/kora-ai
- https://www.cedar.com/all-press/cedar-unveils-agentic-ai-purpose-built-for-healthcare-billing-aiming-to-help-providers-cut-costs-with-a-30-reduction-in-patient-billing-calls
- https://research.contrary.com/company/cedar
- https://www.infinx.com/infinx-healthcare-ai-rcm-platforms/
- https://www.janus-ai.com/janusiq/
- https://www.prnewswire.com/news-releases/janus-health-launches-janusiq-ai-powered-platform-for-health-systems-navigating-financial-and-operational-headwinds-302487498.html
- https://etyon.com/
- https://www.prnewswire.com/news-releases/visiquate-acquires-etyon-to-supercharge-insight-automation-and-denials-management-for-healthcare-providers-302496054.html
- https://www.visiquate.com/analytics
- https://www.visiquate.com/analytics/denials-management-analytics
- https://www.visiquate.com/news/visiquate-adds-conversational-ui-to-its-healthcare-analytics-creates-virtual-data-assistant-named-ana
- https://www.visiquate.com/news/visiquate-will-share-expanded-ana-analytics-chatbot-robotic-process-automation-and-chronic-care-management-capabilities-at-himss20
- https://www.visiquate.com/news/visiquate-acquires-rotera-to-advance-ai-driven-revenue-cycle-automation
- https://www.citybiz.co/article/842828/visiquate-debuts-policy-pulse-to-help-providers-act-on-payer-policy-changes-before-denials-hit/
- https://klasresearch.com/comments/visiquate-analytics/225261
- https://www.techtarget.com/revcyclemanagement/news/366600196/VisiQuate-MedeAnalytics-top-revenue-cycle-analytics-rankings
- https://klasresearch.com/best-in-klas-ranking/denials-management-services/2026/460
- https://hitconsultant.net/2026/02/04/2026-best-in-klas-winners-full-list-software-services/
- https://hakkoda.io/healthcare/
- https://hakkoda.io/resources/snowflake-cortex-analyst/
- https://www.ibm.com/consulting/hakkoda
- https://www.codametrix.com/
- https://www.arintra.com/
- https://www.enter.health/
- https://www.ensemblehp.com/rcm-intelligence/
- https://finance.yahoo.com/news/microsoft-partners-ensemble-health-partners-130000532.html
- https://www.thoughtful.ai/platform/revenue-cycle-automation
- https://www.smarterdx.com/smarterdenials
- https://hospitalogy.com/articles/2025-02-24/smarter-dx-ai-powered-revenue-boost-hospitals/
- https://www.inovalon.com/blog/enhance-rcm-analytics-and-boost-reimbursements-with-cutting-edge-scorecard-dashboards/
- https://www.rapidclaims.ai/blogs/top-ai-denial-analytics-vendors-hospitals
- https://www.stedi.com/customers/adonis-uses-stedi-to-expand-revenue-cycle-management-(rcm)-capabilities

### EHR / PM vocabulary
- https://epicsupport.sites.uiowa.edu/epic-resources/resolute
- https://epicsupport.sites.uiowa.edu/epic-resources/hospital-billing-hb-follow
- http://staff.washington.edu/kens3/Revenue_Cycle_Timeline_v05.pdf
- https://www.ensemblehp.com/wp-content/uploads/2024/06/EBOOK_Top-26-Epic-Workflows-to-Optimize.pdf
- https://www.revigate.com/blog/metrics-reports-to-track-charge-capture-performance-on-epic
- https://www.epicshare.org/perspectives/revenue-cycle-optimization-and-success
- https://thewilshiregroup.net/threading-the-needle-a-smarter-approach-to-revenue-guardians-and-charge-capture-in-epic/
- https://healthtechresourcesinc.com/epic-consultants/epic-analytics-business-intelligence-modules
- https://pinnaclehca.com/2019/03/top-5-missed-epic-optimization-opportunities/
- https://www.aapc.com/discuss/threads/epic-wrvu.189392/
- https://www.epic1.org/Portals/0/Academic/Roadshow%20Slides/Revenue_Cycle_Overview_Roadshow_Breakout_Session_Slide_10.27.2021_1-2_pm%5B1%5D.pdf
- https://www.mindbowser.com/epic-resolute-denials-management/
- https://ignitehs.com/blog/athenahealth-medical-billing-a-navigational-guide/
- https://ignitehs.com/blog/kickcodes-youre-probably-sleeping-on-and-why-you-shouldnt-2/
- https://www.physicianinterlink.com/kick-codes-in-athena-collector/
- https://rcmexperts.us/blog/athena/understanding-status-holds-in-athenahealth/
- https://rcmexperts.us/blog/athena/athenahealth-claim-tracking/
- https://staffingly.com/insights/pain-points-solutions/why-athenahealth-hold-claims-sit-untouched/
- https://www.athenahealth.com/sites/default/files/media_docs/athenaOne-Service-Description.pdf
- https://tdx.msu.edu/TDClient/32/Portal/KB/ArticleDet?ID=1278
- https://emitrr.com/blog/athena-medical-billing/
- https://blog.ttuhsc.edu/spirit5/_resources/images/2012/10/centricity_upgrade.pdf
- https://www.healthcareitnews.com/news/athenahealths-centricity-business-now-athenaidx
- https://choc.org/wp-content/uploads/2020/07/Cerner-Patient-Accounting-Overview_v2.pdf
- https://www.oracle.com/a/ocom/docs/industries/healthcare/oracle-health-patient-accounting-solution-brief.pdf
- https://www.e4.health/transitioning-to-revelate-meaningful-differences-between-the-foundational-applications/
- https://www.healthcareitleaders.com/blog/oracle-cerner-revelate-implementation-tips/
- https://wikicrt.cerner.com/display/public/rcaHP/Overview+of+the+Soarian+Financials+Perspective+in+Revenue+Cycle
- https://home.meditech.com/en/d/mktcontent/otherfiles/professionalservicesbcadashboards.pdf
- https://ehr.meditech.com/ehr-solutions/meditechs-revenue-cycle
- https://ehr.meditech.com/ehr-solutions/meditech-business-clinical-analytics
- https://groups.google.com/g/meditech-l/c/fKGHNAZlkrw
- https://lumexity.com/blog/eclinicalworks-rcm-guide
- https://www.billingparadise.com/eClinicalWorks-emr/featured-reports.html
- https://www.eclinicalworks.com/products-services/ebo/
- https://www.os-healthcare.com/news-and-blog/using-atb-reports-and-resolution-rates-to-run-an-effective-business-office
- https://jtshealthpartners.com/articles/dnfb-in-healthcare/
- https://www.mbwrcm.com/the-revenue-cycle-blog/dnfb-report-healthcare-revenue-cycle

### Conversational analytics & trust patterns
- https://www.thoughtspot.com/blog/introducing-spotter-ai-analyst
- https://www.thoughtspot.com/press-releases/thoughtspot-introduces-spotter-semantics-to-bring-trust-and-context-to-enterprise-ai
- https://docs.thoughtspot.com/cloud/26.6.0.cl/semantic-layer
- https://docs.databricks.com/en/genie/trusted-assets.html
- https://docs.databricks.com/gcp/en/genie/talk-to-genie
- https://docs.databricks.com/aws/en/genie/monitor
- https://www.databricks.com/blog/aibi-genie-now-generally-available
- https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst
- https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/rest-api
- https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/verified-query-repository
- https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst-evaluations
- https://docs.snowflake.com/en/user-guide/snowflake-cortex/cortex-analyst/analyst-optimization
- https://github.com/Snowflake-Labs/sfguide-getting-started-with-cortex-analyst/blob/main/cortex_analyst_streaming_demo.py
- https://help.tableau.com/current/tableau/en-us/tableau_gai_einstein_trust.htm
- https://help.tableau.com/current/online/en-us/pulse_insights_platform_insight_types.htm
- https://help.tableau.com/current/online/en-us/pulse_create_metrics.htm
- https://www.tableau.com/blog/tableau-metrics-and-natural-language-query-evolve-tableau-pulse
- https://www.theinformationlab.co.uk/community/blog/a-problem-with-tableaus-ask-data-and-how-tableau-should-fix-it/
- https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai-verified-answers
- https://learn.microsoft.com/en-us/power-bi/create-reports/copilot-prepare-data-ai
- https://www.epcgroup.net/blog/power-bi-copilot-prep-data-for-ai-regulated-industries-2026
- https://powerbi.microsoft.com/en-us/blog/deprecating-power-bi-qa/
- https://www.magnetismsolutions.com/news/power-bi-qampa-to-retire-by-december-2026-what-you-need-to-know
- https://hex.tech/
- https://learn.hex.tech/docs/getting-started/ai-overview
- https://hex.tech/blog/ai-analytics-adoption-risks/
- https://zenlytic.com/
- https://www.zenlytic.com/blog/introducing-the-cognitive-layer
- https://www.prweb.com/releases/zenlytic-launches-zoe-self-learning-the-ai-data-analyst-that-onboards-itself-302773223.html
- https://promethium.ai/guides/text-to-sql-comparison-2026-enterprise-solutions/
- https://towardsdatascience.com/why-90-accuracy-in-text-to-sql-is-100-useless/
- https://atlan.com/know/ai-agent/data-for-ai/text-to-sql-for-enterprise/
- https://atlan.com/know/ai-agent/what-is-an-enterprise-copilot/
- https://www.cio.com/article/4122237/why-copilots-fail-bi-and-why-multi-agent-ai-is-the-real-future-for-bi.html
- https://zylos.ai/research/2026-07-02-buyer-side-governance-enterprise-ai-agent-deployments/
- https://agentmarketcap.ai/blog/2026/04/07/mckinsey-ai-trust-2026-agentic-governance-framework
- https://www.healthcarefinancenews.com/news/health-system-adoption-ai-outpacing-internal-governance
- https://www.alignmt.ai/post/healthcare-ai-governance-gap-2026
- https://www.globenewswire.com/news-release/2026/03/12/3254770/0/en/ThoughtSpot-Introduces-Spotter-Semantics-to-Bring-Trust-and-Context-to-Enterprise-AI.html
