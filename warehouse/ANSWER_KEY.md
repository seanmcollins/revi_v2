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

## Scenario 3 — Cash decline (the golden-conversation scenario)

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
