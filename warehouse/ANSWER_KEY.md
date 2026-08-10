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

Service dates span **2024-01-01 .. 2026-08-02**, in two eras:

- **2024** is a closed comparison year (see "The 2024 comparison backfill"
  below): every claim in it was billed, adjudicated and paid or written off by
  2025-06-30. It exists so that period-over-period questions have real rows.
- **2025-01-01 onward** is the *organic era* — the world the five scenarios and
  the anomaly population live in. Scenario cohorts that would otherwise be
  open-ended ("everything before the break") are bounded below by
  `service_date >= DATE '2025-01-01'`, because a prior-year cohort must not
  retroactively redefine the baseline a break is measured against. Every
  organic and injected claim is serviced on or after that day, so the bound
  changes no published number.

## Scenario 1 — Denial spike: Halvern Health x Imaging, CARC 197 (CO)

**Mechanism.** For claims of payer `Halvern Health` on service line `Imaging`,
the probability of a claim-level CO/197 (prior authorization missing) denial on
the first remit is 2% when the first remit falls before 2026-06-15 and 14% from
2026-06-15 onward. The generic denial mix never emits 197 in this cell, so the
planted rates are clean.

**Verify.**

```sql
WITH cell AS (
  SELECT claim_id FROM snap_003.v_claim
  WHERE payer_name = 'Halvern Health' AND service_line_name = 'Imaging'
    AND service_date >= DATE '2025-01-01'   -- organic era; excludes the 2024 backfill
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
  AND l.claim_service_date >= DATE '2025-01-01'   -- organic era
  AND l.allowed_amount_cents IS NOT NULL
GROUP BY 1 ORDER BY 1;
```

The post/pre allowed-to-billed ratio is ~0.917 (target 0.92; contract-rate
jitter explains the remainder). Monthly variance dollars by first-remit month
are in `scenarios.4_underpayment_northbridge_ortho`.

## Scenario 5 — Timely filing: State Medicaid HMO (90 days from service) at Eastmere

**Mechanism.** Exactly `timely_cluster_size` (400 at full scale) claims with
July-2026 service dates at `Eastmere Medical Center` are forced onto plan
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
WHERE plan_name = 'State Medicaid HMO' AND facility_name = 'Eastmere Medical Center'
  AND service_date BETWEEN DATE '2026-07-01' AND DATE '2026-07-31'
  AND submission_date IS NULL;

SELECT count(*), SUM(denied_amount_cents)
FROM snap_003.v_denial
WHERE plan_name = 'State Medicaid HMO'
  AND facility_name = 'Eastmere Medical Center' AND carc_code = 29
  AND service_date >= DATE '2025-01-01';   -- organic era
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
| `ANM-006` | eligibility_cluster | Ashvale HMO / Primary Care | denial_rate | 2026-07-18 | $10,018 | medium | 111 |
| `ANM-007` | eligibility_cluster | State MCO Standard / Emergency | denial_rate | 2026-08-02 | $15,182 | medium | --1 |
| `ANM-008` | eligibility_cluster | Bluestone Select HMO / Primary Care | denial_rate | 2026-07-08 | $507 | low | 111 |
| `ANM-009` | duplicate | Federal Medicare / Imaging | denied_dollars | 2026-06-25 | $25,494 | high | 111 |
| `ANM-010` | duplicate | Ashvale PPO / Laboratory | denied_dollars | 2026-07-05 | $415 | low | 111 |
| `ANM-011` | underpayment | Bluestone Mutual / General Surgery / SURG-GEN | underpayment_variance | 2026-05-05 | $134,031 | critical | 111 |
| `ANM-012` | underpayment | Summit Peak MA / Cardiology / CARD-PROC | underpayment_variance | 2026-05-20 | $7,741 | medium | 111 |
| `ANM-013` | contractual | Veritas Comp Fund / Orthopedic Surgery / ORTHO-SURG | gross_collection_rate | 2026-05-01 | $493,266 | critical | 111 |
| `ANM-014` | posting_lag | Bluestone Mutual / Cardiology | avg_days_to_pay | 2026-07-08 | $73,956 | high | 111 |
| `ANM-015` | posting_lag | Ashvale Health Plan / Oncology | avg_days_to_pay | 2026-07-22 | $63,552 | high | 111 |
| `ANM-016` | submission_gap | Halvern Health / Primary Care | bill_lag_days | 2026-06-18 | $23,607 | medium | 111 |
| `ANM-017` | submission_gap | Veritas Comp Fund / Primary Care | bill_lag_days | 2026-07-02 | $474 | low | 111 |
| `ANM-018` | timely_filing | Halvern Exchange PPO / Southfield / Primary Care | timely_filing_at_risk_dollars | 2026-06-02 | $30,376 | high | 111 |
| `ANM-019` | timely_filing | State Medicaid HMO / Northgate / Emergency | timely_filing_at_risk_dollars | 2026-03-18 | $13,237 | medium | 111 |
| `ANM-020` | timely_filing | Lakewood MCO Core / Riverbend / Primary Care | timely_filing_at_risk_dollars | 2026-05-04 | $18,499 | medium | 111 |
| `ANM-021` | dnfb | Federal Medicare / Northgate / General Surgery | dnfb_dollars | 2026-07-03 | $178,217 | critical | 111 |
| `ANM-022` | dnfb | Northbridge Commercial / Southfield / Obstetrics | dnfb_dollars | 2026-06-26 | $61,627 | high | 111 |
| `ANM-023` | credit_balance | Atlas PPO Select / Imaging | credit_balance_dollars | 2026-06-05 | $48,939 | high | 111 |
| `ANM-024` | credit_balance | Federal Medicare / Primary Care | credit_balance_dollars | 2026-06-14 | $824 | low | 111 |
| `ANM-025` | charge_entry_lag | Federal Medicare / Riverbend / Oncology | charge_lag_days | 2026-07-04 | $83,805 | high | 111 |
| `ANM-026` | charge_entry_lag | Bluestone Mutual / Eastmere / Cardiology | late_charge_pct | 2026-07-18 | $37,504 | high | 111 |
| `ANM-027` | unworked_denials | State Medicaid FFS / Emergency | denials_unworked_pct | 2026-05-01 | $53,919 | high | 111 |
| `ANM-028` | unworked_denials | Northbridge Commercial / Emergency | denials_unworked_pct | 2026-06-22 | $34,669 | high | 111 |
| `ANM-029` | denial_spike | Bluestone Preferred PPO / Imaging | denial_rate | 2026-08-02 | $17,677 | medium | --1 |
| `ANM-030` | underpayment | Lakewood MCO Plus / Laboratory / LAB | underpayment_variance | 2026-06-08 | $257 | low | 111 |
| `ANM-031` | dnfb | Ashvale Health Plan / Westpark / General Surgery | dnfb_dollars | 2026-07-22 | resolved | critical | 11- |
| `ANM-032` | submission_gap | Summit Peak MA / Central Plaza / Behavioral Health | bill_lag_days | 2026-07-14 | resolved | medium | 11- |
| `ANM-033` | charge_hold | Federal Medicare / Riverbend / Laboratory | charge_lag_days | 2026-07-09 | resolved | low | 11- |
| `ANM-034` | eligibility_cluster | Halvern HMO Care / Emergency | denial_rate | 2026-08-01 | $6,838 | medium | -11 |
| `ANM-035` | duplicate | State MCO Expansion / Laboratory | denied_dollars | 2026-07-06 | $3,524 | low | 111 |
| `ANM-036` | dnfb | Halvern Health / Central Plaza / Primary Care | dnfb_dollars | 2026-07-12 | $626 | low | 111 |

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
   intersecting them — `ANM-016`/`ANM-036` use Halvern Health but Primary Care,
   never Imaging (scenario 1); `ANM-019` uses State Medicaid HMO but at Northgate,
   never Eastmere (scenario 5); `ANM-022`/`ANM-028` use Northbridge Commercial but
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
   first_injected_claim_id`, which also excludes the 2024 backfill) and requires
   exact equality with the recorded all-rows values, per snapshot. If injection
   or the backfill had leaked into any scenario, the base-only recomputation
   would diverge. The same pass runs the backfill's closure guards (below) over
   every snapshot.

The load-bearing anchors are unchanged: the reference-week payer cash totals at
`snap_003` remain exactly **152,196,731** and **132,844,152** cents (-12.7%).

## The 2024 comparison backfill

"How did 2025 compare with 2024?" is an ordinary question for anyone probing
this warehouse, and it deserves rows rather than a shrug. `backfill.py` appends
a full calendar year of 2024 claims — with lines, remits, transactions, denials
and appeals — after every other stage has run. Full scale, at every watermark:

| | 2024 (backfill) | 2025 (organic) |
| --- | --- | --- |
| claims | 65,451 | 75,898 |
| claims per day | 178.8 | 207.9 (2024 is 86.0% of the rate) |
| billed | $187.1M | $219.1M |
| posted payer cash | $72.8M | $80.8M |
| denial rate (claims with >= 1 denial) | 6.03% | 6.69% |

Every row cohorts claims by **service year** — including the cash row, which
sums the payer payments posted against each year's claims whenever they posted.
Grouping the same cash by *posting* year instead gives $67.8M / $79.9M, because
$5.1M of 2024's cash posts in early 2025 (the year closes on 2025-06-30, not on
2024-12-31); on that basis the 2025 column is not purely organic. Pick one basis
and say which — the two differ by more than the year-over-year gap they measure.

Rows added: 65,451 claims, 163,197 lines, 66,075 remits, 175,006 transactions,
3,949 denials — identical in all three snapshots, since 2024 predates every
cutoff. Claim ids start at `backfill_meta.first_backfill_claim_id`
(`CLM-0120512` at full scale), above every pre-existing id.

**Shape.** Volume follows `BACKFILL.volume_ratio` (0.86) of the observed 2025
claims-per-day rate, with monthly seasonality. The payer mix is tilted mildly
toward Medicaid — the story is that commercial and Medicare Advantage volume
grew into 2025 (Atlas 17.9% -> 20.0%, State Medicaid 18.4% -> 16.1%, Summit
Peak MA 3.2% -> 4.0% of claims). Denial propensity is each payer's 2025 rate
times `BACKFILL.denial_factor` (0.88). Nothing else changes: contract rates,
line mixes, lag distributions and payment mechanics are the organic world's, so
a 2024-vs-2025 cycle-time comparison is a real comparison (18.14 vs 18.05 mean
days submission -> first payment posting), not an artifact.

**Why it cannot move a 2026 number.** Every backfill claim is *closed*: billed,
adjudicated, and paid or written off, with every appeal decided and every
duplicate payment refunded, by `BACKFILL.resolved_by` (2025-06-30 — the last
observed resolution is 2025-04-27). One consequence covers everything:

- no claim sits in `OPEN` or `DENIED` status, so A/R, aging and days-in-A/R are
  untouched at every watermark;
- no claim is unsubmitted, so DNFB and timely-filing-at-risk dollars are
  untouched;
- no credit balance stands open;
- no denial is left mid-appeal (`appeal_status = 'APPEALED'` count is zero), so
  the unworked-denial share of any 2026 window is untouched;
- every scenario window, every anomaly observation window and every trailing
  window anchored at a 2026-08 watermark starts after the last backfill date.

Denials that were never appealed keep `appeal_status = 'NONE'` and carry a
posted write-off — terminal at the claim level, and the honest way to keep the
2024 appeal rate (34.5% of denials) comparable with 2025's.

**Determinism.** Each sub-stream is seeded by
`(REVI_SEED, BACKFILL_STREAM, crc32(sub_stream))`, the same pattern the anomaly
engine uses, and the rows are appended after everything else. No draw belonging
to dims/world/anomalies is consumed or shifted, so every pre-existing row is
byte-identical with the backfill on or off. `GeneratorConfig.include_backfill`
switches it off, and `tests/test_backfill.py` builds both ways and diffs the
pre-existing id space, the `detected_anomalies` tables and the whole answer key.

**Verify.**

```sql
-- volume, cash and denial rate by year
WITH c AS (SELECT claim_id, year(service_date) AS yr, billed_amount_cents
           FROM snap_003.fact_claim WHERE service_date < DATE '2026-01-01'),
d AS (SELECT DISTINCT claim_id FROM snap_003.fact_denial)
SELECT yr, count(*) AS claims, SUM(billed_amount_cents) AS billed,
       count(d.claim_id)::DOUBLE / count(*) AS denial_rate
FROM c LEFT JOIN d USING (claim_id) GROUP BY 1 ORDER BY 1;

-- the closure proof: all three must return zero
SELECT count(*) FROM snap_003.fact_claim
WHERE service_date < DATE '2025-01-01'
  AND (status NOT IN ('PAID', 'CLOSED') OR submission_date IS NULL
       OR resolved_date IS NULL OR resolved_date > DATE '2025-06-30');
SELECT count(*) FROM snap_003.fact_transaction t JOIN snap_003.fact_claim c USING (claim_id)
WHERE c.service_date < DATE '2025-01-01' AND t.post_date > DATE '2025-06-30';
SELECT count(*) FROM snap_003.fact_denial d JOIN snap_003.fact_claim c USING (claim_id)
WHERE c.service_date < DATE '2025-01-01' AND d.appeal_status = 'APPEALED';
```

**Known consequence.** A cohort or probe with no lower date bound now spans
2024 as well. For example, "all claims for Atlas Commercial, State Medicaid and
Halvern Health" materialises 86,415 claims at `snap_003` rather than the 55,722
it did before:

```sql
SELECT count(*) FILTER (WHERE service_date >= DATE '2025-01-01') AS organic_era,
       count(*)                                                 AS with_backfill
FROM snap_003.v_claim
WHERE payer_name IN ('Atlas Commercial', 'State Medicaid', 'Halvern Health');
-- 55722, 86415
```

The *windowed* numbers such a probe reports are unchanged — only the population
it names is larger, which is what an unbounded cohort means. Anything measured
over a window that starts on or after 2025-05-28 is byte-for-byte what it was:
that is the latest date carried by any backfill row of any kind, 430 days before
the earliest watermark, so no trailing window the platform anchors at a 2026-08
watermark can reach the backfill.

## Denial recovery: the resubmission feed (`<snap>.fact_recovery_event`)

"How often does resubmitting this kind of denial actually work, for this payer?"
deserves the tenant's own history rather than an industry rule of thumb.
`recovery.py` gives denials a follow-up story: a resubmission event, the payer's
answer, and occasionally a second attempt — authored entirely from the config
tables in `config.py` (`RECOVERY_CLASS_SPECS`, `RECOVERY_PAYER_OVERTURN_FACTOR`,
`RECOVERY`), never per claim. The full parameter set and the realized aggregates
are both in `data/answer_key.json` under `recovery`.

**Population, stated first because every rate below is a rate over it.** Chains
exist for **organic-era denials with no formal appeal on the remit** — the
stories that used to end at the denial. At `snap_003`, full scale: 7,998
organic-era denials, of which 2,600 went to the appeal path (their outcome is in
`fact_denial.appeal_status`, unchanged) and **5,398** are the recovery feed's
population. The 2024 backfill is a closed year and carries no follow-up work at
all. Do not sum the two paths without saying so — they are separate feeds.

**Dollars, stated second for the same reason.** `recovered_amount_cents` is the
payer-**allowed** value of the denied unit, capped at the denied amount. It is
what the rework was worth, gross of patient responsibility, and it is **not
posted cash**: no transaction, remit, claim status or scenario figure moves
because this feed exists. Every published number in this document is exactly
what it was before it — proven by regenerating and diffing every pre-existing
table.

### Table shape

| table | grain |
| --- | --- |
| `fact_recovery_event` | one row per event: `RESUBMISSION` or `OUTCOME` |
| `v_recovery_event` | the same rows pre-joined to the denial, claim, payer, plan (incl. `timely_filing_days`) and recovery class |
| `dim_recovery_class` | CARC -> recoverability class, with the reason |
| `v_denial` (new columns) | the chain rolled up to **one row per denial**: `recovery_status`, `denial_recovery_class`, `resubmission_type`, `resubmission_date`, `days_to_resubmission`, `recovery_outcome_date`, `recovery_cycles`, `recovered_amount_cents` |

Every event names its claim, its denial and the remit the denial arrived on; an
`OUTCOME` row also names the `RESUBMISSION` it answers (`parent_event_id`), and
a second denial carries a CARC, group code and synthetic `RZnn` remark from the
same code system as the original. `snap_003` holds 5,888 events over 2,745
chains: 2,745 first resubmissions, 2,546 answers, and 305 second attempts of
which 292 have been answered. Outcomes so far: 900 `PAID_FULL`, 187
`PAID_PARTIAL`, 1,751 `DENIED_AGAIN`.

The catalog binds the chain at the **denial** grain, where a denial is still one
row. `v_recovery_event` is deliberately not an entity: a probe addressing it
would count one denial once per event.

### The authored effects, and what the data realized

All figures below are `snap_003`, full scale, from `answer_key.json`. The
authored parameter is the model input; the realized rate is what the generated
world actually shows, which differs because of censoring, class and payer mix,
and the filing-deadline interaction.

**By denial class** (`base_overturn` is the authored win rate at reference
conditions; `recovery rate` is realized over *decided* chains):

| class | resubmit prob | median days | base overturn | denials | resubmitted | decided | recovered | resub rate | recovery rate |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CODING | 0.78 | 9 | 0.68 | 720 | 541 | 508 | 316 | 75.1% | **62.2%** |
| REGISTRATION | 0.72 | 12 | 0.60 | 1,113 | 775 | 713 | 374 | 69.6% | 52.5% |
| ROUTING | 0.66 | 18 | 0.50 | 1,340 | 780 | 718 | 321 | 58.2% | 44.7% |
| CLINICAL | 0.44 | 28 | 0.21 | 1,480 | 576 | 528 | 74 | 38.9% | 14.0% |
| FINAL | 0.12 | 34 | 0.07 | 745 | 73 | 66 | 2 | 9.8% | **3.0%** |

Realized median days to resubmission: 9 / 13 / 20 / 31 / 49 — the fixable
classes go back out first, as authored.

**By payer.** `RECOVERY_PAYER_OVERTURN_FACTOR` multiplies the class base; payer
identity enters the **outcome only**, never the decision to work the denial, so
the payer contrast among resubmitted denials is not confounded by selection.

| payer | factor | denials | resubmitted | decided | recovered | recovery rate |
| --- | --- | --- | --- | --- | --- | --- |
| Northbridge Commercial | **1.30 (strong)** | 329 | 160 | 148 | 84 | **56.8%** |
| Federal Medicare | 1.10 | 437 | 246 | 230 | 113 | 49.1% |
| Atlas Commercial | 1.15 | 1,009 | 556 | 518 | 233 | 45.0% |
| Bluestone Mutual | 1.00 | 313 | 166 | 142 | 60 | 42.3% |
| State Medicaid | 0.80 | 972 | 451 | 421 | 145 | 34.4% |
| Summit Peak MA | 0.70 | 235 | 121 | 112 | 38 | 33.9% |
| Lakewood Medicaid MCO | **0.55 (weak)** | 243 | 125 | 117 | 34 | **29.1%** |

(The other five payers fall between; `Silverline Medicare Advantage` reads
higher than its 0.95 factor suggests because the planted COB cohort concentrates
guaranteed ROUTING recoveries on it — a **class-mix confound**, and exactly the
reason a recoverability estimate has to stratify rather than rank payers raw.)

**Detectability.** The strong/weak contrast is the check the effect sizes were
set to pass: 84/148 vs 34/117, a pooled two-proportion **z = 4.50**
(p ≈ 7e-6), recorded in `answer_key.json` at
`recovery.snap_003.detectability`. Within a single class it is wider still —
CODING is 34/37 (91.9%) for Northbridge against 8/25 (32.0%) for Lakewood.

**Timeliness.** Recovery probability decays with days-to-resubmission
(`timeliness_floor + (1 - floor) * exp(-days / tau)`, tau = 60 days, floor
= 0.35) — and the decay is compounded by class mix, because slow classes are
also weak ones:

| days to resubmission | denials | decided | recovered | recovery rate |
| --- | --- | --- | --- | --- |
| 0-14 | 1,212 | 1,137 | 618 | 54.4% |
| 15-30 | 862 | 779 | 341 | 43.8% |
| 31-60 | 322 | 294 | 82 | 27.9% |
| 61+ | 349 | 323 | 46 | **14.2%** |

**The filing deadline, and the governed-rules interaction.** The deadline is the
claim's service date plus its plan's configured limit (`dim_plan.
timely_filing_days`, the same arithmetic the certified `filing_runway_bucket`
uses). Crossing it collapses recovery — but *how far* it collapses depends on
whether the limit is governed without caveat. Seven of the thirty plans resolve
to a `requires_confirmation: false` rule in `packs/base-rcm/filing_rules.yaml`
(State Medicaid HMO, the four Medicaid MCO plans, both Federal Medicare plans);
for the other 23 the limit is a planning default that may not be the real
contract term, so the observed cliff is partial:

| position | filing rule | denials | decided | recovered | recovery rate |
| --- | --- | --- | --- | --- | --- |
| within deadline | confirmed | 643 | 595 | 255 | 42.9% |
| within deadline | requires confirmation | 2,013 | 1,859 | 829 | 44.6% |
| past deadline | confirmed | 43 | 39 | 0 | **0.0%** |
| past deadline | requires confirmation | 46 | 40 | 3 | **7.5%** |

An analysis that treats every deadline as governed will over-predict the cliff
on 23 of 30 plans. What puts a real population past the deadline is **aged
inventory**: `backlog_share` sends 6% of coding denials but 32% of the FINAL
class to the back of a work queue for a median of 115 days first — past the
90/95/120-day windows, inside the 180/365-day ones.

**Dollar size** is deliberately mild: large denials are pursued more (+10% per
decade of denied dollars, reference $1,000) and win slightly more (+5%).
Realized resubmission rate: 48.7% under $500, 50.7% $500-$5k, **57.6%** over $5k.

**Second cycles.** 305 chains were worked twice; the second attempt goes out
faster (×0.75 on the median delay) and wins less (×0.55 on the probability).

**`resubmission_type` carries no independent effect.** The action is drawn from
the class (coding fixes go out as `CORRECTED_CLAIM`, clinical work as
`RECONSIDERATION`), and the outcome does not read it. Its raw rates therefore
look like an effect of the action — `CORRECTED_CLAIM` 50.2%, `RECONSIDERATION`
11.8% — when they are entirely the class showing through. The dimension is
uncertified for that reason, among others.

### Right-censoring at the data edge

Not a special case: event days are generated forward from the denial and each
snapshot keeps only what had happened by its cutoff. At `snap_003`:

- **212 chains are out the door with no answer yet** (`recovery_status =
  'RESUBMITTED_PENDING'`);
- **2,653 denials read `NOT_RESUBMITTED`** — and 383 of them *will* be
  resubmitted after 2026-08-02 (`recovery.world_truth.
  resubmission_after_newest_watermark`, which only the generator can see).
  Nothing in the warehouse distinguishes those from the ones nobody will ever
  work.

The feed grows with the loads exactly as everything else does: 5,847 events at
`wm_001`, 5,864 at `wm_002`, 5,888 at `wm_003`. A `wm_004` would resolve some of
the open stories with no change to the scheme — the world already holds their
dates.

Consequences a correct recoverability estimate has to carry: a rate with
`NOT_RESUBMITTED` in the denominator is a **lower bound** on fresh cohorts, and
recency-weighted cohorts are the most censored ones. The honest denominator for
"does resubmitting work" is the decided population.

### Verify

```sql
-- realized recovery by payer and class, over the feed's own population
SELECT payer_name, denial_recovery_class,
       count(*) AS denials,
       count(*) FILTER (WHERE recovery_status <> 'NOT_RESUBMITTED') AS resubmitted,
       count(*) FILTER (WHERE recovery_status IN
              ('RECOVERED_FULL', 'RECOVERED_PARTIAL', 'DENIED_AGAIN')) AS decided,
       count(*) FILTER (WHERE recovery_status IN
              ('RECOVERED_FULL', 'RECOVERED_PARTIAL')) AS recovered,
       SUM(recovered_amount_cents) AS recovered_cents
FROM snap_003.v_denial
WHERE service_date >= DATE '2025-01-01' AND appeal_status = 'NONE'
GROUP BY 1, 2 ORDER BY 1, 2;

-- the timeliness decay, and the deadline cliff underneath it
SELECT CASE WHEN resubmission_date > service_date + timely_filing_days
            THEN 'past_deadline' ELSE 'within_deadline' END AS filing_position,
       CASE WHEN days_to_resubmission <= 14 THEN '0-14'
            WHEN days_to_resubmission <= 30 THEN '15-30'
            WHEN days_to_resubmission <= 60 THEN '31-60' ELSE '61+' END AS bucket,
       count(*) AS decided,
       count(*) FILTER (WHERE recovery_status LIKE 'RECOVERED%') AS recovered
FROM snap_003.v_denial
WHERE service_date >= DATE '2025-01-01' AND appeal_status = 'NONE'
  AND recovery_status <> 'NOT_RESUBMITTED' AND recovery_status <> 'RESUBMITTED_PENDING'
GROUP BY 1, 2 ORDER BY 1, 2;

-- the chain itself, ordered and linked (returns zero rows)
SELECT e.recovery_event_id
FROM snap_003.fact_recovery_event e
JOIN snap_003.fact_denial d USING (denial_id)
LEFT JOIN snap_003.fact_recovery_event p ON p.recovery_event_id = e.parent_event_id
WHERE e.event_date <= d.denial_date
   OR (e.event_type = 'OUTCOME' AND (p.recovery_event_id IS NULL OR e.event_date <= p.event_date))
   OR e.recovered_amount_cents > e.denied_amount_cents;
```

**Determinism.** Each sub-stream is seeded by
`(REVI_SEED, RECOVERY_STREAM, crc32(sub_stream))` and the stage runs last, after
the backfill. No draw belonging to dims/world/anomalies/backfill is consumed or
shifted: regenerating with the feed added leaves all 43 pre-existing tables and
all five base views byte-identical, and the answer key gains entries without
changing one.

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
- Recovery chains are causally ordered (denial < resubmission < outcome),
  conserving (`recovered_amount_cents` between 0 and the denied amount, non-zero
  only on a paid outcome), linked (every outcome answers a resubmission that is
  in the table), and confined to their stated population (organic era, no
  appealed denial, no backfill claim). `v_denial` restates each chain without
  fanning the denial out.
