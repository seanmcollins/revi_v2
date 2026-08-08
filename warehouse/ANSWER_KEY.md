# Mock warehouse answer key — planted scenarios and how to verify them

The generator (`revi_warehouse`, seed `20260807`) builds one deterministic world
and projects it into three snapshot schemas — `snap_001`, `snap_002`, `snap_003`
— simulating consecutive nightly loads (`main.watermarks`: loaded 2026-08-01
04:05, 2026-08-02 04:12, 2026-08-03 04:10; newest data dates 2026-07-31,
2026-08-01, 2026-08-02). Activity after a snapshot's newest data date is absent
from it and all derived claim fields reflect only what that snapshot can see.

The exact expected numbers live in `data/answer_key.json`, computed **from the
generated data** per scenario per watermark — never hand-entered. Regenerate with
`make warehouse`; self-check with
`python -m revi_warehouse.generate --out ... --verify` (nonzero exit on failure).
All SQL below runs against any snapshot schema; substitute `snap_003` for the
newest watermark.

## Scenario 1 — Denial spike: Meridian Health x Imaging, CARC 197 (CO)

**Mechanism.** For claims of payer `Meridian Health` on service line `Imaging`,
the probability of a claim-level CO/197 (prior authorization missing) denial on
the first remit is 2% when the first remit falls before 2026-06-15 and 14% from
2026-06-15 onward. The generic denial mix never emits 197 in this cell, so the
planted rates are clean.

**Verify.**

```sql
WITH cell AS (
  SELECT claim_id FROM snap_003.v_claim
  WHERE payer_name = 'Meridian Health' AND service_line_name = 'Imaging'
),
first_remit AS (
  SELECT claim_id, MIN(remit_date) AS fr FROM snap_003.fact_remit GROUP BY claim_id
),
d197 AS (
  SELECT DISTINCT claim_id FROM snap_003.fact_denial
  WHERE carc_code = 197 AND group_code = 'CO'
)
SELECT fr.fr >= DATE '2026-06-15' AS post_break,
       count(*) AS adjudicated_claims,
       count(d197.claim_id) AS carc197_denied,
       count(d197.claim_id)::DOUBLE / count(*) AS rate
FROM cell JOIN first_remit fr USING (claim_id) LEFT JOIN d197 USING (claim_id)
GROUP BY 1 ORDER BY 1;
```

Expected at `snap_003` full scale: pre-break rate ~2.2%, post-break rate ~10%,
ratio > 4x (answer key: `scenarios.1_denial_spike_meridian_imaging`).

## Scenario 2 — COB: Silverline Medicare Advantage, CARC 22 (OA) + delayed rebill

**Mechanism.** ~8% of `Silverline Medicare Advantage` claims with service dates
2026-04-01..2026-07-31 are stamped `other_insurance_flag = TRUE`,
`payer_sequence = 'P'`, `cob_mismatch_flag = TRUE`. Each such claim draws a
claim-level OA/22 denial on its first remit and a second remit 30–45 days later
that pays in full (the rebill after coordination is corrected). Rebills beyond a
snapshot's cutoff are invisible at that watermark, so the cohort's paid/denied
split shifts across watermarks.

**Verify.**

```sql
SELECT count(*) AS cob_claims
FROM snap_003.v_claim
WHERE payer_name = 'Silverline Medicare Advantage'
  AND cob_mismatch_flag AND other_insurance_flag AND payer_sequence = 'P'
  AND service_date BETWEEN DATE '2026-04-01' AND DATE '2026-07-31';

SELECT count(*), SUM(denied_amount_cents)
FROM snap_003.v_denial
WHERE payer_name = 'Silverline Medicare Advantage'
  AND carc_code = 22 AND group_code = 'OA'
  AND service_date BETWEEN DATE '2026-04-01' AND DATE '2026-07-31';

-- rebill gap for cohort claims with both remits visible
WITH cohort AS (
  SELECT claim_id FROM snap_003.v_claim
  WHERE payer_name = 'Silverline Medicare Advantage' AND cob_mismatch_flag
    AND service_date BETWEEN DATE '2026-04-01' AND DATE '2026-07-31'
)
SELECT AVG(gap) FROM (
  SELECT MAX(remit_date) - MIN(remit_date) AS gap, count(*) AS n
  FROM snap_003.fact_remit JOIN cohort USING (claim_id) GROUP BY claim_id
) WHERE n >= 2;
```

Expected: share ~7.7% of the window's Silverline claims, avg rebill gap ~36 days
(answer key: `scenarios.2_cob_silverline`).

## Scenario 3 — Cash decline (the reference-conversation scenario)

**Mechanism.** Posted payer cash (`fact_transaction.txn_type = 'PAYMENT'`, post
date basis) in week 2026-07-27..2026-08-02 is ~12–13% below week
2026-07-20..2026-07-26 at `snap_003`, from two planted mechanisms:

- **(a) Atlas Commercial volume:** 20% of Atlas submissions dated on/after
  2026-07-13 are deferred by 21 days, pushing them (and their downstream remits
  and payments) past every snapshot cutoff — a 20% submission-volume drop
  starting two weeks before the decline week.
- **(b) State Medicaid posting lag:** payments and contractual adjustments for
  State Medicaid remits dated on/after 2026-07-24 post 4 days later than the
  payer's usual ~4-day lag, opening a posting gap that lands squarely in the
  decline week (the cash "catches up" after the horizon).

**Verify.**

```sql
SELECT SUM(amount_cents) FILTER (WHERE post_date BETWEEN DATE '2026-07-20' AND DATE '2026-07-26') AS wk_prior,
       SUM(amount_cents) FILTER (WHERE post_date BETWEEN DATE '2026-07-27' AND DATE '2026-08-02') AS wk_decline
FROM snap_003.fact_transaction WHERE txn_type = 'PAYMENT';

-- per-payer attribution (top decliners must be Atlas Commercial + State Medicaid)
SELECT payer_name,
       SUM(amount_cents) FILTER (WHERE post_date BETWEEN DATE '2026-07-20' AND DATE '2026-07-26') AS wk_prior,
       SUM(amount_cents) FILTER (WHERE post_date BETWEEN DATE '2026-07-27' AND DATE '2026-08-02') AS wk_decline
FROM snap_003.v_transaction WHERE txn_type = 'PAYMENT'
GROUP BY 1 ORDER BY wk_decline - wk_prior;

-- mechanism (a): Atlas weekly submission counts
SELECT DATE_TRUNC('week', submission_date) AS wk, count(*)
FROM snap_003.v_claim
WHERE payer_name = 'Atlas Commercial' AND submission_date >= DATE '2026-06-15'
GROUP BY 1 ORDER BY 1;

-- mechanism (b): State Medicaid observed remit->post lag
SELECT remit_date >= DATE '2026-07-24' AS stretched, AVG(post_date - remit_date)
FROM snap_003.v_transaction
WHERE txn_type = 'PAYMENT' AND payer_name = 'State Medicaid' AND remit_date IS NOT NULL
  AND remit_date >= DATE '2026-07-06'
GROUP BY 1;
```

The answer key (`scenarios.3_cash_decline.snap_003`) records the exact weekly
totals, the delta (full scale: **-12.7%**), all twelve per-payer week-over-week
deltas, Atlas submission counts by week, and the observed State Medicaid lag
(~4.1 -> ~7.5 days on visible postings).

## Scenario 4 — Underpayment: Northbridge Commercial ORTHO-SURG at 92% of expected

**Mechanism.** For `Northbridge Commercial` claim lines in proc group
`ORTHO-SURG`, when the claim's first remit falls on/after 2026-05-01 the line's
allowed amount is set to `round(0.92 x expected)` instead of the contract-exact
expected amount. Everywhere else allowed equals expected exactly, so
claim-level underpayment variance (`expected_amount_cents - SUM(line allowed)`,
floored at zero — underpayments never net against overpayments) is exactly zero
before May and strictly positive after.

**Verify.**

```sql
WITH first_remit AS (
  SELECT claim_id, MIN(remit_date) AS fr FROM snap_003.fact_remit GROUP BY claim_id
)
SELECT fr >= DATE '2026-05-01' AS post,
       SUM(l.billed_amount_cents) AS billed,
       SUM(l.allowed_amount_cents) AS allowed,
       SUM(l.allowed_amount_cents)::DOUBLE / SUM(l.billed_amount_cents) AS ratio
FROM snap_003.v_claim_line l JOIN first_remit USING (claim_id)
WHERE l.payer_name = 'Northbridge Commercial' AND l.proc_group = 'ORTHO-SURG'
  AND l.allowed_amount_cents IS NOT NULL
GROUP BY 1 ORDER BY 1;
```

The post/pre allowed-to-billed ratio is ~0.917 (target 0.92; contract-rate
jitter explains the remainder). Monthly variance dollars by first-remit month
are in `scenarios.4_underpayment_northbridge_ortho`.

## Scenario 5 — Timely filing: State Medicaid HMO (90 days from service) at Eastside

**Mechanism.** Exactly `timely_cluster_size` (400 at full scale) claims with
July-2026 service dates at `Eastside Medical Center` are forced onto plan
`State Medicaid HMO` (timely filing: 90 days, SERVICE basis) and left
unsubmitted at every snapshot — at `snap_003` they are 2–32 days old, aging
toward the 90-day deadline (58–88 days remaining). Additionally,
`carc29_count` (15) claims on the same plan/facility with service dates
2026-02-01..2026-03-15 were filed 95–110 days after service — past the limit —
and carry claim-level CO/29 denials dated June–July 2026. ("June claims" is
implemented as claims *filed/denied* around June: a claim must be >90 days past
service for a CARC 29 denial to be mechanically correct, and June-of-service
claims cannot be past a 90-day limit by 2026-08-02.)

**Verify.**

```sql
SELECT count(*), SUM(billed_amount_cents),
       MIN(DATE '2026-08-02' - service_date) AS min_age,
       MAX(DATE '2026-08-02' - service_date) AS max_age
FROM snap_003.v_claim
WHERE plan_name = 'State Medicaid HMO' AND facility_name = 'Eastside Medical Center'
  AND service_date BETWEEN DATE '2026-07-01' AND DATE '2026-07-31'
  AND submission_date IS NULL;

SELECT count(*), SUM(denied_amount_cents)
FROM snap_003.v_denial
WHERE plan_name = 'State Medicaid HMO'
  AND facility_name = 'Eastside Medical Center' AND carc_code = 29;
```

Expected at full scale: 414 unsubmitted July claims (400 planted + a handful of
organically never-submitted ones), ~$1.17M billed at risk, 15 CARC 29 denials
(answer key: `scenarios.5_timely_filing_state_medicaid_hmo`).

## The detected-anomaly population (`<snap>.detected_anomalies`)

Beyond the five headline scenarios, the world carries a **background population
of 36 planted, independently detectable anomalies** so that ranking, triage and
"what else is going on?" behaviour has something real to chew on. The population
is declared once, as a spec table (`config.ANOMALY_SPECS`), and realised by a
single vectorized injection pass (`anomalies.inject_anomalies`).

### How it is built

`anomalies.py` has two halves, both driven by the same spec table:

1. **Injection.** One pass appends spec-generated claims — plus their lines,
   remits, transactions and denials — to the already-built base `World`. Base
   arrays are never mutated and injected claims occupy indices
   `n_base_claims..n_claims-1`, i.e. claim ids strictly above
   `anomalies_meta.first_injected_claim_id`. Ten mechanisms are available:
   denial-probability tweaks, allowed-amount shifts (underpayment), contract-rate
   resets, posting-lag stretches, submission gaps, duplicate claims (CARC 18),
   eligibility clusters (CARC 27), charge-entry-lag bursts, DNFB accumulation,
   credit-balance buildups and unworked-denial aging.
2. **Detection.** `write_detected_anomalies` fills `<snap>.detected_anomalies`
   per snapshot as if an external detector ran at load time. **Every published
   number is recomputed with SQL from that snapshot's visible data** — nothing is
   copied from the spec. The spec supplies only *where* and *when* to look
   (dimension scope + observation window); the dollars, counts and day statistics
   come back out of the warehouse.

Determinism: each spec draws from its own PCG64 stream seeded by
`(REVI_SEED, ANOMALY_STREAM, crc32(spec_id))`. The base world's draw sequence is
never consumed or shifted, so adding, removing or re-tuning any spec cannot
perturb the organic world or the five scenarios.

### Table shape

| column | notes |
| --- | --- |
| `anomaly_id` | stable spec id (`ANM-001`…`ANM-036`) |
| `detected_at` | equals that snapshot's `loaded_at` watermark |
| `category` | mechanism family (see table below) |
| `title`, `description` | human-readable; description quotes the recomputed figures |
| `metric_id` | a **real** `packs/base-rcm/metrics/*.yaml` id (the pack is read-only) |
| `dimensions` | JSON cell scope: payer / plan / facility / service_line / proc_group |
| `window_start`, `window_end` | the observation window the detector filtered on |
| `impact_cents` | recomputed from that snapshot by SQL, never from the spec |
| `severity` | derived from `impact_cents` (rules below) |
| `confidence` | derived from the qualifying-event count (rules below) |
| `status` | always `open` — these are detections, not workflow records |
| `evidence` | JSON facts: counts, dollars, day statistics (never judgments) |

### Emission rule

A spec is emitted into a snapshot **iff** all three hold:

1. its `onset` is on or before the snapshot's `newest_data_date`;
2. the snapshot shows at least `min_events` qualifying events; and
3. the recomputed `impact_cents` clears `min_impact_cents`.

Because the test is re-evaluated per snapshot against only that snapshot's
visible rows, visibility falls out of the data rather than being scripted: a
window that has not opened yet produces nothing, and a signal whose claims were
subsequently submitted/posted simply stops qualifying and **disappears**.

### Severity and confidence

Severity is a pure function of the recomputed impact (`anomalies.severity_for`):

| severity | recomputed `impact_cents` |
| --- | --- |
| `critical` | >= 10,000,000 ( >= $100k ) |
| `high` | >= 2,500,000 ( >= $25k ) |
| `medium` | >= 500,000 ( >= $5k ) |
| `low` | below $5k |

Confidence is a pure function of the qualifying-event count
(`anomalies.confidence_for`) — more corroborating events, more confidence:

| events | confidence |
| --- | --- |
| >= 40 | 0.95 |
| >= 20 | 0.90 |
| >= 10 | 0.80 |
| >= 5 | 0.70 |
| < 5 | 0.60 |

### Evidence carries facts, not verdicts

Evidence JSON never says whether something is worth working — it supplies the
facts a reader needs to decide. Denial-family anomalies carry
`appealable_claims` vs `appeal_window_expired_claims`,
`days_to_appeal_deadline{min,max}`, `days_since_denial{min,median,max}`,
`appeal_status_counts` and `claim_status_counts`. Timely-filing anomalies carry
`open_claims`/`expired_claims` with their dollars and
`days_to_deadline{min,median,max}`. The population deliberately mixes the
outcomes: `ANM-004` is 14/14 **expired** (nothing to appeal), `ANM-027` is
**mixed** (17 open, 12 expired), `ANM-002` is borderline (29 open, 1 just
expired), `ANM-019` is 14/14 timely-filing deadlines **already passed**, while
`ANM-018` is fully open (29–51 days of runway) and `ANM-020` is open but
**imminent** (5–19 days).

### Per-snapshot visibility

Full-scale counts: **snap_001 = 33, snap_002 = 34, snap_003 = 33**.

- Most of the population is visible from `snap_001`.
- `ANM-034` first becomes visible at `snap_002` (onset 2026-08-01).
- `ANM-007` and `ANM-029` first become visible at `snap_003` (onset 2026-08-02).
- `ANM-031`, `ANM-032`, `ANM-033` are the **documented self-resolvers**: present
  at `snap_001` and `snap_002`, then gone at `snap_003` because their claims were
  submitted / their charges posted on 2026-08-02. They are the deliberate
  exception to visibility monotonicity, and `--verify` asserts both that they
  vanish and that nothing *else* does.

### The population

`vis` = visible at snap_001/002/003. Impact is the recomputed `snap_003` value at
full scale (self-resolvers show their last observed severity instead).

| id | category | cell | metric_id | onset | snap_003 impact | severity | vis |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `ANM-001` | denial_spike | Summit Peak MA / Cardiology | denial_rate | 2026-06-20 | $170,643 | critical | 111 |
| `ANM-002` | denial_spike | Bluestone Mutual / Laboratory | denial_rate | 2026-06-01 | $21,180 | medium | 111 |
| `ANM-003` | denial_spike | Lakewood Medicaid MCO / Emergency | denial_rate | 2026-07-05 | $56,005 | high | 111 |
| `ANM-004` | unworked_denials | Federal Medicare / General Surgery | denials_unworked_pct | 2026-03-01 | $62,035 | high | 111 |
| `ANM-005` | denial_spike | Veritas Comp Fund / Behavioral Health | denial_rate | 2026-07-10 | $972 | low | 111 |
| `ANM-006` | eligibility_cluster | Pinnacle HMO / Primary Care | denial_rate | 2026-07-18 | $10,018 | medium | 111 |
| `ANM-007` | eligibility_cluster | State MCO Standard / Emergency | denial_rate | 2026-08-02 | $15,182 | medium | --1 |
| `ANM-008` | eligibility_cluster | Bluestone HMO Blue / Primary Care | denial_rate | 2026-07-08 | $507 | low | 111 |
| `ANM-009` | duplicate | Federal Medicare / Imaging | denied_dollars | 2026-06-25 | $25,494 | high | 111 |
| `ANM-010` | duplicate | Pinnacle PPO / Laboratory | denied_dollars | 2026-07-05 | $415 | low | 111 |
| `ANM-011` | underpayment | Bluestone Mutual / General Surgery / SURG-GEN | underpayment_variance | 2026-05-05 | $134,031 | critical | 111 |
| `ANM-012` | underpayment | Summit Peak MA / Cardiology / CARD-PROC | underpayment_variance | 2026-05-20 | $7,741 | medium | 111 |
| `ANM-013` | contractual | Veritas Comp Fund / Orthopedic Surgery / ORTHO-SURG | gross_collection_rate | 2026-05-01 | $493,266 | critical | 111 |
| `ANM-014` | posting_lag | Bluestone Mutual / Cardiology | avg_days_to_pay | 2026-07-08 | $73,956 | high | 111 |
| `ANM-015` | posting_lag | Pinnacle Health Plan / Oncology | avg_days_to_pay | 2026-07-22 | $63,552 | high | 111 |
| `ANM-016` | submission_gap | Meridian Health / Primary Care | bill_lag_days | 2026-06-18 | $23,607 | medium | 111 |
| `ANM-017` | submission_gap | Veritas Comp Fund / Primary Care | bill_lag_days | 2026-07-02 | $474 | low | 111 |
| `ANM-018` | timely_filing | Meridian Exchange PPO / Southfield / Primary Care | timely_filing_at_risk_dollars | 2026-06-02 | $30,376 | high | 111 |
| `ANM-019` | timely_filing | State Medicaid HMO / Northgate / Emergency | timely_filing_at_risk_dollars | 2026-03-18 | $13,237 | medium | 111 |
| `ANM-020` | timely_filing | Lakewood MCO Core / Riverbend / Primary Care | timely_filing_at_risk_dollars | 2026-05-04 | $18,499 | medium | 111 |
| `ANM-021` | dnfb | Federal Medicare / Northgate / General Surgery | dnfb_dollars | 2026-07-03 | $178,217 | critical | 111 |
| `ANM-022` | dnfb | Northbridge Commercial / Southfield / Obstetrics | dnfb_dollars | 2026-06-26 | $61,627 | high | 111 |
| `ANM-023` | credit_balance | Atlas PPO Select / Imaging | credit_balance_dollars | 2026-06-05 | $48,939 | high | 111 |
| `ANM-024` | credit_balance | Federal Medicare / Primary Care | credit_balance_dollars | 2026-06-14 | $824 | low | 111 |
| `ANM-025` | charge_entry_lag | Federal Medicare / Riverbend / Oncology | charge_lag_days | 2026-07-04 | $83,805 | high | 111 |
| `ANM-026` | charge_entry_lag | Bluestone Mutual / Eastside / Cardiology | late_charge_pct | 2026-07-18 | $37,504 | high | 111 |
| `ANM-027` | unworked_denials | State Medicaid FFS / Emergency | denials_unworked_pct | 2026-05-01 | $53,919 | high | 111 |
| `ANM-028` | unworked_denials | Northbridge Commercial / Emergency | denials_unworked_pct | 2026-06-22 | $34,669 | high | 111 |
| `ANM-029` | denial_spike | Bluestone PPO Blue / Imaging | denial_rate | 2026-08-02 | $17,677 | medium | --1 |
| `ANM-030` | underpayment | Lakewood MCO Plus / Laboratory / LAB | underpayment_variance | 2026-06-08 | $257 | low | 111 |
| `ANM-031` | dnfb | Pinnacle Health Plan / Westpark / General Surgery | dnfb_dollars | 2026-07-22 | resolved | critical | 11- |
| `ANM-032` | submission_gap | Summit Peak MA / Central Plaza / Behavioral Health | bill_lag_days | 2026-07-14 | resolved | medium | 11- |
| `ANM-033` | charge_hold | Federal Medicare / Riverbend / Laboratory | charge_lag_days | 2026-07-09 | resolved | low | 11- |
| `ANM-034` | eligibility_cluster | Meridian HMO Care / Emergency | denial_rate | 2026-08-01 | $6,838 | medium | -11 |
| `ANM-035` | duplicate | State MCO Expansion / Laboratory | denied_dollars | 2026-07-06 | $3,524 | low | 111 |
| `ANM-036` | dnfb | Meridian Health / Central Plaza / Primary Care | dnfb_dollars | 2026-07-12 | $626 | low | 111 |

Magnitudes span four orders: `ANM-013` at $493k down to `ANM-030` at $257, with
seven sub-$1k signals that exist precisely so that "rank by impact" has noise to
rank *below*.

**Verify** (any snapshot):

```sql
SELECT severity, count(*), SUM(impact_cents)
FROM snap_003.detected_anomalies GROUP BY 1 ORDER BY 2 DESC;

-- what became visible between watermarks, and what resolved
SELECT anomaly_id FROM snap_003.detected_anomalies
EXCEPT SELECT anomaly_id FROM snap_002.detected_anomalies;   -- ANM-007, ANM-029
SELECT anomaly_id FROM snap_002.detected_anomalies
EXCEPT SELECT anomaly_id FROM snap_003.detected_anomalies;   -- the 3 self-resolvers

-- evidence is fact-shaped: appeal runway, not a verdict
SELECT anomaly_id,
       evidence ->> '$.appealable_claims' AS open_windows,
       evidence ->> '$.appeal_window_expired_claims' AS expired_windows
FROM snap_003.detected_anomalies
WHERE json_contains(json_keys(evidence), '"appealable_claims"');
```

The full population is recorded per snapshot in `data/answer_key.json` under
`anomalies` (plus `anomalies_meta`), and `--verify` re-derives every impact,
severity and confidence from the database and fails on any drift.

### Non-interference with the five scenarios

The population must not move a single answer-key number. Three independent
layers enforce that:

1. **Disjoint cells by construction.** No spec targets a scenario's cell. The
   population deliberately *brushes against* scenario dimensions without
   intersecting them — `ANM-016`/`ANM-036` use Meridian Health but Primary Care,
   never Imaging (scenario 1); `ANM-019` uses State Medicaid HMO but at Northgate,
   never Eastside (scenario 5); `ANM-022`/`ANM-028` use Northbridge Commercial but
   never `ORTHO-SURG` lines (scenario 4); `ANM-023` uses Atlas Commercial but
   submits outside the scenario-3a window. Nothing at all is planted on
   Silverline Medicare Advantage (scenario 2).
2. **Build-time guards.** `anomalies._enforce_guards` inspects the appended rows
   and **raises** if any injected claim/line/transaction could reach a scenario
   aggregate — including a hard rule that no injected payer or patient cash may
   post inside the reference compare weeks 2026-07-20..2026-08-02, and that no
   injected State Medicaid payment may carry a remit on/after 2026-07-06.
3. **Post-hoc proof.** `verify._non_interference_checks` re-runs all five
   scenario computations over **base claims only** (`claim_id <
   first_injected_claim_id`) and requires exact equality with the recorded
   all-rows values, per snapshot. If injection had leaked into any scenario, the
   base-only recomputation would diverge.

The load-bearing anchors are unchanged: the reference-week payer cash totals at
`snap_003` remain exactly **152,196,731** and **132,844,152** cents (-12.7%).

## Cross-cutting invariants (also enforced by `--verify`)

- `main.watermarks` matches design doc section 10.3 verbatim.
- Every fact foreign key resolves within its snapshot; a claim's `plan_id`
  always belongs to its `payer_id`.
- Per snapshot: `fact_claim.billed_amount_cents` equals the sum of that
  snapshot's visible `fact_claim_line.billed_amount_cents` exactly.
- `allowed_amount_cents` is NULL until the claim has a visible remit.
- All transaction and denied amounts are strictly positive (type carries
  direction).
- No fact row carries a date after its snapshot's `newest_data_date`.
- Snapshot monotonicity: every fact row of `snap_n` is present in `snap_{n+1}`.
  `detected_anomalies` is a *detection* table, not a fact table, and is
  deliberately exempt: a signal that stops qualifying disappears (the three
  documented self-resolvers above). `--verify` still asserts that nothing else
  ever disappears.
