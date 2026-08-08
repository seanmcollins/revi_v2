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
- **Filter predicates inside `filtered:`/`exclusions:`** reference catalog
  dimensions (`clean_claim`, `appeal_status`, `denial_category`,
  `cob_mismatch_flag`, ...) and **nothing else**. Contract-internal filters
  are part of governed meaning; analyst scope filters are validated
  separately against `scope_dimensions`.

  An earlier revision of this note also allowed "base-view columns of the
  measure's entity (`status`, `submission_date`, `discharge_date`,
  `txn_type`, `first_pass_paid`)". That was never true. A `sum:`/
  `count_distinct:` **field** falls back to the entity's declared columns
  (`_ValueBinding` in the DuckDB compiler), but a filter **dimension**
  resolves only through `CatalogSnapshot.dimension()`, so a predicate on a
  base-view column raises `UNSUPPORTED_CONCEPT` at compile time and the whole
  probe is pruned as unanswerable. The mistaken convention is the root cause
  of the exclusion-polarity defect below, and it still leaves these
  contract-internal `filtered:` predicates unanswerable until the catalog
  certifies the dimension:

  | contract | unresolved filter dimension(s) |
  | --- | --- |
  | `ar_balance` | `status` |
  | `ar_over_90_pct` | `status` |
  | `days_in_ar` | `status` |
  | `dnfb_dollars` | `discharge_date`, `submission_date` |
  | `first_pass_yield` | `first_pass_paid` |
  | `initial_denial_rate` | `status` |
  | `timely_filing_at_risk_dollars` | `status`, `submission_date` |

  `validate_pack_catalog_conformance` deliberately does **not** cover these
  yet — it guards `exclusions:` only. Widening it to `filtered:` predicates
  is the obvious next step and would fail composition on all seven rows above
  until the catalog grows `status`, `submission_date`, `discharge_date` and
  `first_pass_paid`.
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

## KB wave 1 (machine-researched knowledge, 2026-08-07)

`knowledge.yaml` and `benchmarks.yaml` carry the first wave of governed
narrative knowledge: **55 knowledge cards** and **19 benchmark figures**,
every one `review_status: machine_researched` and
`authored_by: machine-researched (KB wave 1, 2026-08-07)`. Nothing in this
wave is certified. Sean reviews and approves before any card or figure may
be treated as governed truth; the promotion path (machine_researched →
human_approved, with the proposal/delta machinery in
`revi_pack.domain.PackDelta`) is Phase 4 work. Until then, consumers must
surface the provenance tier alongside the content, exactly as they surface
`cohort_label` and `cautions` on a benchmark.

### Card id namespaces

| Namespace | Cards | Scope |
| --- | --- | --- |
| `benchmark.*` | 12 | published external figures and survey context (denial rates, overturn rates, A/R aging, cost/admin burden, patient collections) |
| `payer.*` | 12 | payer behavior by segment (MA, commercial, Medicaid, marketplace) — denial patterns, downcoding, clawbacks, prompt-pay stalling |
| `reg.*` | 15 | regulation and standards (CMS-4201-F, CMS-0057-F, No Surprises Act IDR, price transparency, 501(r), ERISA, HIPAA transactions/attachments, X12 licensing) |
| `ops.*` | 16 | denial and appeal operations (taxonomy, CARC root-cause mapping, appeal ladders by payer, appeal economics, prevention, write-off governance, measurement) |

### Cards elaborate concepts; they never contradict them

`concepts.yaml` owns the one-paragraph governed definition; a card adds key
points, cautions, and dated provenance. Card aliases may deliberately
overlap concept aliases (`resolve_term` returns the concept first, then the
elaborating card) — alias uniqueness is enforced **among cards only**. Five
such overlaps are authored on purpose and pinned by test, so a definitional
turn returns definition *plus* elaboration: `soft denial` /
`hard denial` → `ops.denial_taxonomy`, `coordination of benefits` →
`ops.cob_ordering_msp`, `map keys` → `ops.map_keys_measurement`,
`denial write-off` → `ops.write_off_governance`, `unworked denials` →
`benchmark.denials_never_worked`. Where
a card touches a concept's territory (e.g. `ops.group_code_liability` vs the
group-code definitions in `codes.yaml`), the card elaborates the operational
consequence and defers to the concept/code definition for meaning.

### No licensed code text

CARC/RARC/group-code descriptions in cards are paraphrased in our own words,
the same discipline `codes.yaml` follows. X12 publishes the lists openly but
asserts copyright and licenses redistribution
(`reg.carc_rarc_licensing` documents the exposure); verbatim code-list text
is never reproduced in pack content.

### Benchmark metric mapping

Every `BenchmarkFigure.metric_id` resolves to a real pack metric contract
(snapshot integrity enforces it). Mapped figures:

- `initial_denial_rate` (7): Kodiak, Optum, Premier private-payer, Health
  Affairs MA, Crowe commercial, traditional Medicare, Medicaid.
- `appeal_overturn_rate` (4): KFF MA prior-auth appeals, Premier private
  payer, Health Affairs MA claims, OIG SNF admission denials.
- `denials_unworked_pct` (3): marketplace consumer appeal share, MA
  prior-auth appealed share, HFMA best-practice appeal share (all recorded
  as appealed-share with the complement noted in `cautions`).
- `denial_rate` (1): ACA marketplace plan-reported in-network denials.
- `days_in_ar` (2): Kodiak YoY direction, trade guidance range.
- `ar_over_90_pct` (1), `clean_claim_rate` (1).

### Benchmarks dropped for want of a Revi metric

Researched figures with no metric contract to hang on were dropped rather
than force-fit. They stay in the cards (as narrative with sources) but are
not benchmark artifacts:

- **Final denial rate as % of net revenue** and **median bad-debt rate**
  (Kodiak): Revi has `denial_write_off_dollars` (dollars, OTHER_ADJ family),
  not an NPSR-denominated ratio — see "Deferred metrics" above
  (`denial_write_off_pct_of_revenue`). Narrative lives in
  `benchmark.final_denial_rate`.
- **Prior-authorization denial rates** (MA 7.7%, Medicaid MCO 12.5%,
  post-acute SNF/IRF/LTCH, traditional Medicare FFS 22.9%) and
  **prior-auth turnaround SLAs** (CMS-0057-F 72h/7d): the denominator is
  PA requests, not claims — no PA entity exists in the warehouse. Narrative
  in `payer.ma.prior_auth_appeal_gap`, `payer.medicaid.denials_oversight`,
  `payer.ma.post_acute_denials`, `reg.cms_0057f`.
- **Avoidable/preventable denial share** (Optum 84%, HFMA 90%): no
  preventability classification exists on the denial entity. Narrative in
  `benchmark.denial_avoidability`.
- **Cost to rework or fight a denial** ($25.20–$43.84) and **cost to
  collect** (2–4% of NPR): cost-to-collect (MAP FM-6/FM-7) is a deferred
  metric with no supporting cost data. Narrative in
  `benchmark.cost_to_collect_admin_burden` and `ops.appeal_economics`.
- **Patient/self-pay collection rate** (Kodiak 34.5–37.6%): `patient_cash_posted`
  is a dollar flow, not a rate against patient responsibility. Narrative in
  `benchmark.patient_collections_bad_debt`.
- **Total write-offs as % of collections** (<5% vendor target): no
  collections-denominated write-off ratio contract.
- **Aged A/R over 120 days** (MGMA better performers 8.1%): only
  `ar_over_90_pct` exists; the buckets are not interchangeable.
- **Federal IDR statistics** (dispute volume, provider win rate ~85–88%,
  ineligibility rate): out-of-network arbitration is not a Revi metric
  family. Narrative in `reg.nsa_idr_outcomes`.
- **Medicaid unwinding disenrollment/eligibility churn**, **inappropriate-denial
  share** (OIG 13–18%), and **credentialing delay cost** ($10,122/physician/day):
  no eligibility, audit-outcome, or enrollment-cost metrics. Narrative in
  `reg.medicaid_unwinding`, `reg.ma_denial_oversight`,
  `ops.credentialing_enrollment`.

### Figures deliberately not captured at all

The research sweeps flagged widely circulated numbers with no retrievable
primary source: "50–65% of denials are never reworked" (attributed to MGMA),
absolute national medians for net days in A/R, DNFB days, charge lag, and
net collection rate. These are recorded as cautions inside the relevant
cards (`benchmark.days_in_ar`, `benchmark.denials_never_worked`) and are
never asserted as benchmark figures.

## Playbook parameter convention

`dimension_scorecard` declares `params: [dimension]` and references the
parameter as `"$dimension"` inside probe `dimensions`; the planner binds it
to a certified catalog dimension at plan time. `$`-prefixed tokens in probe
dimensions always refer to declared playbook params.

## Exclusion polarity (2026-08-08 correction)

`exclusions:` names the population to **remove**. The compiler emits
`FILTER (WHERE NOT <expr>)` for it
(`revi_connector_duckdb.compile._measure_expr_sql`), symmetrically on
numerator and denominator. It is not a `where:` clause.

All seven `exclusions:` in this pack were authored as if it were one — as
inclusion predicates — so each kept exactly the population its description
said it removed. Six were inert: the dimension they name is absent from
`warehouse/catalog/dimensions.yaml`, so every probe touching them raised
`UNSUPPORTED_CONCEPT` before any SQL ran. That accident is what hid the
inversion. The seventh resolved, executed and published numbers.

| contract | as authored | what it actually kept | what it meant to remove | outcome |
| --- | --- | --- | --- | --- |
| `avg_days_to_pay` | `txn_type eq PAYMENT` | non-payment transactions only | non-payment transactions | exclusion removed |
| `bill_lag_days` | `not(submission_date is_null)` | unsubmitted claims only | unsubmitted claims | exclusion removed |
| `clean_claim_rate` | `status neq OPEN` | OPEN claims only | claims with no adjudication outcome | exclusion removed |
| `denial_rate` | `status neq OPEN` | OPEN claims only | claims with no adjudication outcome | exclusion removed |
| `denials_unworked_pct` | `denial_category neq PATIENT_RESP` | patient-responsibility records only | patient-responsibility records | **polarity repaired, v1 → v2** |
| `first_pass_yield` | `not(submission_date is_null)` | unsubmitted claims only | unsubmitted claims | exclusion removed |
| `initial_denial_rate` | `not(submission_date is_null)` | unsubmitted claims only | unsubmitted claims | exclusion removed |

### Why the six were removed rather than repaired

`status`, `submission_date` and `txn_type` are base-view columns, not catalog
dimensions, and filter predicates resolve only through the catalog (see the
field-reference note above). A repaired-but-still-unresolvable exclusion
would stay inert and go on hiding. Restoring any of them is one catalog
change — certify the dimension in `warehouse/catalog/dimensions.yaml` (id,
label, `entities:` binding, synonyms, `phi`), then re-add the exclusion with
`exclusions:` polarity — and `warehouse/` is out of scope for a pack pass.

Residue, per contract:

- **`avg_days_to_pay`** — the one removal that costs declared meaning. The
  population is now every transaction posted in the window, not payer
  payments. `payment_lag_days` is null for non-PAYMENT rows so a `sum:`
  numerator is unaffected, but the `count: {}` denominator counts every
  transaction and dilutes the mean downward. Certifying `txn_type` restores
  it; so would a derived measure carrying its own `filter_sql`. Moot in
  practice today: `payment_lag_days` is an undelivered derived measure, so
  the metric is unanswerable either way.
- **`bill_lag_days`, `first_pass_yield`, `initial_denial_rate`** — the
  exclusion was a no-op on each contract's *primary* `submission` basis: a
  claim with a null `submission_date` cannot fall inside a submission window,
  so it is already absent from the population. It bites only on the alternate
  `service` basis, where never-submitted claims now enter the denominator.
  Every affected description says so.
- **`clean_claim_rate`, `denial_rate`** — materially wider populations: every
  claim in the window, not adjudicated claims only. `clean_claim` is
  `paid AND NOT denied`, so an unadjudicated claim reads false — it lowers
  `clean_claim_rate` and raises `denial_rate` near the watermark. Both
  descriptions state it; certifying `status` restores the adjudicated-only
  population.

### Why `denials_unworked_pct` was repaired instead

`denial_category` is certified, so this contract resolved and executed. It has
been reporting over patient-responsibility notices only — the exact records it
meant to drop. Repairing the polarity (`neq` → `eq`) changes a number in
service, so it takes a version: **v1 → v2**. Over 2026-05-01..2026-08-02 at
`wm_003` the reading moves from 41/62 = 66.13% to 1182/1520 = 77.76%, a 24×
denominator change. Nothing pins the old values — no `data/answer_key.json`
entry, no reference test, no portfolio fixture. (The `unworked_denials`
anomaly specs in the warehouse generator name this metric id but carry
pre-baked detection values; they never evaluate the contract.)

### Versions

Only `denials_unworked_pct` was bumped. A contract version exists so a
recorded number can be traced to the meaning that produced it and rolled back
to it. The other six could never produce a number, so there is no v1 reading
to distinguish from a v2 one, and minting a version would assert a field
history that does not exist. The change is not silent regardless: `exclusions`
is part of the §5.2 semantic fingerprint, so every one of the seven moves the
pack snapshot id.

### What now guards this

`revi_pack.conformance.validate_pack_catalog_conformance` fails pack
composition when an `exclusions:` predicate names a dimension the catalog does
not define, and `revi_api.wiring.build_components` calls it unconditionally at
startup. It would have caught six of these seven. It cannot catch
`denials_unworked_pct` — an exclusion whose dimension resolves is
structurally indistinguishable from a correct one, since only the prose says
which population was intended. The compensating controls are the executed
polarity pin
(`packages/connector-duckdb/tests/test_duckdb_contract.py::TestExclusionPolarity`)
and reading each description against its predicate at review time.

## Contract revisions

- **`denials_unworked_pct` v2 (exclusion polarity repair, 2026-08-08).** v1's
  `exclusions: {denial_category neq PATIENT_RESP}` compiles to
  `NOT(denial_category <> 'PATIENT_RESP')`, i.e. it kept only the
  patient-responsibility records the description says are removed. v2 states
  the predicate as the population to remove (`op: eq`). Details and the
  before/after numbers are in "Exclusion polarity" above.
- **`denied_dollars` v2 (grain rebind, M7).** v1 declared `entity_grain:
  line`, but its measure (`denied_amount_cents`) and code-level scope
  dimensions (`carc`, `group_code`, `denial_category`) all bind on the
  DENIAL entity's base view (`v_denial`) — a line-grain probe could never
  compile. v2 rebinds the contract to `entity_grain: denial` (the denial
  entity's physical grain is line-level; probes address it through the
  DENIAL grain), drops `proc_group` from `scope_dimensions` (it binds at
  `claim_line` only; the denial record's line linkage is null for
  claim-level denials, so procedure-level denial cuts wait for a certified
  path), and bumps the version because the fingerprint changed. Claim
  cohorts filter denied_dollars probes through the certified
  denial → claim join path.
