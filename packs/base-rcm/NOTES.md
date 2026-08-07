# base-rcm pack — authoring notes

Companion to the pack content. Documents the field-reference conventions the
contracts rely on, the probe-time derived measures, and metrics considered
but deferred with reasons. The wire-up test
(`packages/pack/tests/test_base_pack_content.py`) enforces the closed sets
declared here.

## Field-reference conventions in metric contracts

- **`sum:` fields are catalog measure ids** from `warehouse/catalog/
  measures.yaml` (each resolves to its declared entity, column, and row
  filter — e.g. `payment_cents` is PAYMENT-type transaction amounts), or a
  **derived measure id** from the registry below.
- **`count_distinct:` fields are entity primary-key columns** exactly as
  declared by the catalog's count measures (`claim_id`, `claim_line_id`,
  `denial_id`, `remit_id`).
- **Filter predicates inside `filtered:`/`exclusions:`** reference certified
  catalog dimensions (`clean_claim`, `appeal_status`, `denial_category`,
  `ar_age_bucket`, ...) or base-view columns of the measure's entity
  (`status`, `submission_date`, `discharge_date`, `txn_type`,
  `first_pass_paid`). Contract-internal filters are part of governed
  meaning; analyst scope filters are validated separately against
  `scope_dimensions`.
- **Cross-entity ratios** (`net_collection_rate`, `gross_collection_rate`)
  pair components from different entities (transaction cash over claim
  expected/billed); the kernel computes ratio-of-sums per cell and both
  entities carry the shared scope dimensions and date bases.

## Derived measure registry (probe-time computations)

These are not stored columns. Each is computed deterministically by the
snapshot/aggregation compiler from base-view columns (same spirit as the
catalog's derived `ar_age_bucket` dimension):

| id | entity | formula |
| --- | --- | --- |
| `ar_age_days_billed_cents` | claim | for unresolved claims (status OPEN or DENIED): `billed_amount_cents x (newest_data_date - aging_basis_date)` in days; else 0 |
| `underpayment_cents` | claim | for adjudicated claims: `max(0, expected_amount_cents - SUM(visible line allowed_amount_cents))`; never netted across claims |
| `credit_balance_cents` | claim | `max(0, posted PAYMENT + PATIENT_PAYMENT cents - expected_amount_cents) - REFUND cents already posted`, floored at 0 |
| `payment_lag_days` | transaction | for PAYMENT transactions: `post_date - submission_date` (days); NULL otherwise |
| `charge_entry_lag_days` | claim_line | `charge_entry_date - service_date` (days) |
| `submission_lag_days` | claim | for submitted claims: `submission_date - service_date` (days); NULL otherwise |
| `late_charge_cents` | claim_line | `billed_amount_cents` when `charge_entry_date > service_date + 3 days`, else 0 |

## Honesty notes baked into contract descriptions

- `denial_rate` / `initial_denial_rate` / `clean_claim_rate` /
  `first_pass_yield` derive denial standing from the outcome-derived
  `clean_claim` / `first_pass_paid` flags; the warehouse has no scrubber
  events, and every recorded denial lands on a claim's first remit (second
  remits are rebill/appeal outcomes only), so first-denial dedup and rebill
  exclusion are structurally satisfied rather than implemented.
- `days_in_ar` is the billed-weighted **aging** form (ages to the snapshot's
  newest data date), not MAP FM-1's NPSR form — no net-revenue series
  exists.
- `ar_balance` values open A/R at gross billed charges (no per-claim net
  open balance is stored).
- `underpayment_variance` floors per claim at zero; underpayments are never
  netted against overpayments.
- `denial_write_off_dollars` measures the OTHER_ADJ family; transactions
  carry no stored denial linkage, so denial attribution is by investigation
  scope.
- `appeal_overturn_rate` divides overturned by **decided** appeals.
- Group code **CR** is defined in codes.yaml but never emitted by the mock
  generator (noted in its definition).

## Deferred metrics (and why)

- **`pos_collection_rate`** (MAP PA-7): requires distinguishing
  point-of-service patient payments from later collections. In the
  generator, patient payments post 10–45 days *after* the payer payment
  posting (world.py stage 15), i.e. always post-adjudication — a POS split
  would be structurally zero and misleading. Deferred until the generator
  plants POS activity. The `pos_collections` concept and an `unavailable`-
  strength binding document this in-band.
- **`days_denial_to_appeal`** (Task Force cycle time): `v_denial` exposes
  `appeal_decision_date` but not the appeal *filing* date, so
  denial-to-appeal time is not computable. Denial-to-decision time would be
  computable and can be added when the catalog certifies it.
- **`days_denial_to_resolution`**: needs `resolved_date` at denial grain;
  `v_denial` does not pre-join it. Candidate for a future catalog change.
- **`primary_denial_rate`** (zero-pay primary remits over primary remits):
  `v_remit` carries no paid/zero-pay amounts. The practical equivalent is
  `denial_rate` scoped to `payer_sequence = P` — scope, not a new contract.
- **`denial_write_off_pct_of_revenue`** (MAP AR-6 ratio) and
  **`cash_collections_pct_of_expected`** (FM-2 shape): both need an NPSR
  series; expected-basis proxies exist (`net_collection_rate` covers the
  FM-2 intent against expected) and any NPSR proxy must be labeled.
- **Zero-pay vs partial-pay as a scope dimension**: `denial_level`
  (CLAIM/LINE) exists on `v_denial` but is not a certified catalog
  dimension; exposing it is a catalog change, not pack content.
- **Bad debt / charity / uncompensated care (AR-7/AR-8/FM-3/FM-4), cost to
  collect (FM-6/7), CMI (FM-5), payer rejection rate, DNC/FBNS days,
  unapplied cash, takeback rate**: no supporting events/fields in the mock
  schema — covered as governed concepts/vocabulary only.

## Playbook parameter convention

`dimension_scorecard` declares `params: [dimension]` and references the
parameter as `"$dimension"` inside probe `dimensions`; the planner binds it
to a certified catalog dimension at plan time. `$`-prefixed tokens in probe
dimensions always refer to declared playbook params.
