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
  of the exclusion-polarity defect below. Seven contracts carried unresolved
  `filtered:` predicates because of it; **all seven are resolved as of
  2026-08-08**, five by certifying the dimension and two by renaming the
  predicate onto a certified flag:

  | contract | unresolved filter dimension(s) | resolution |
  | --- | --- | --- |
  | `ar_balance` | ~~`status`~~ | certified |
  | `ar_over_90_pct` | ~~`status`~~ | certified |
  | `days_in_ar` | ~~`status`~~ | certified (numerator also needed the derived `ar_age_days_billed_cents`, now delivered) |
  | `dnfb_dollars` | ~~`discharge_date`, `submission_date`~~ | renamed to `discharged_flag` / `billed_flag` |
  | `first_pass_yield` | ~~`first_pass_paid`~~ | certified |
  | `initial_denial_rate` | ~~`status`~~ | certified |
  | `timely_filing_at_risk_dollars` | ~~`submission_date`~~ | renamed to `billed_flag` (`status` certified) |

  `validate_pack_catalog_conformance` still guards `exclusions:` only;
  widening it to `filtered:` predicates remains the right platform fix. Until
  then the pack side covers it:
  `test_numerator_filter_dimensions_are_certified_catalog_dimensions`
  (`packages/pack/tests/test_base_pack_content.py`) walks every predicate
  inside every shipped `filtered:` numerator and requires each dimension to
  be a **certified** catalog dimension, so this table can never silently grow
  a row again.
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
| `days_to_filing_deadline` | claim | snapshot only, unsubmitted claims only: `(service_date + the plan's timely_filing_days) - as_of` in days; negative means the deadline has passed. The claim → plan → filing rule join; `filing_runway_bucket` buckets the same quantity |

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

## Playbook `triggers:` have no runtime consumer

Worth knowing before anyone tunes them. `triggers:` is loaded
(`revi_pack.loader`), carried on `PlaybookSpec` (`revi_pack.domain`) and
read by nothing else — no planner, no interpreter, no selection code.
Playbook selection runs off `playbook_summaries()`, which returns
`(id, description)` pairs, and `InterpretationService._vocabulary` clips
each description to **160 characters**. So the trigger phrases are
authoring documentation — a record of the questions a playbook was
designed for, useful at review time and to whatever selection mechanism
eventually consumes them — and the first ~160 characters of the
description are the entire selection surface in service today. Front-load
descriptions accordingly; `payer_scorecard`'s was rewritten in the fix
pass for exactly this reason.

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

## Adjudicated population restored (2026-08-08, round-1 review D2)

The polarity correction above removed `denial_rate`'s and `clean_claim_rate`'s
`status` exclusions rather than repairing them, because `status` was not a
catalog dimension. Removal left both contracts computing over *every* claim
in the window. That is not a cosmetic widening:

- the numerator reads the outcome-derived `clean_claim` flag, which is
  `paid AND NOT denied`, so a claim awaiting its first remittance reads
  false — **for want of evidence, not because a payer decided anything**;
- all **11,319** OPEN claims in this warehouse carry **zero** denial
  records, so every one of them entered `denial_rate`'s numerator as a
  denial that does not exist;
- live, State Medicaid published **49.94%** where the adjudicated-only rate
  is **9.39% (175/1,864** over 2026-05-01..2026-08-02 on the service basis,
  matching direct SQL over `snap_003.v_claim`);
- worse than the magnitude, the *ranking inverted at the top*: the payer
  Revi flagged as the single worst was, on the corrected population, better
  than the median, because the error tracks each payer's share of OPEN
  claims rather than anything about denials.

`status` is now a certified catalog dimension (`warehouse/catalog/
dimensions.yaml`: claim → `status`, domain `PAID | OPEN | CLOSED | DENIED`,
`phi: none`), so both exclusions resolve and both were restored **with
`exclusions:` polarity** — `status eq OPEN` names the population to remove.
Both contracts take a version (v1 → v2): each had a reading in service, and
a recorded number must be traceable to the meaning that produced it.

They move together on purpose. Both read the same flag with opposite
polarity, and both descriptions claim that over an identical population the
two sum to one; restoring one population and not the other would have
broken that invariant silently. `TestReferencePackContracts` pins the pair
executed against the real warehouse.

Certifying `status` also resolves the `filtered:` predicates in
`ar_balance`, `ar_over_90_pct`, `days_in_ar` and `initial_denial_rate`
(table above). That is a side effect, not the goal — several of those
contracts were still unanswerable on their *measure fields*, which is a
separate failure mode and the other half of the coverage work. (That half
closed in the adapter on 2026-08-08 — all seven derived measures shipped —
but not yet in §6.6 validation. See Appendix B.)

### Population caveats are now published, structurally

`denial_rate` v1's description already said the population was not
restricted to adjudicated claims. It was correct, it was governed, and it
never reached a reader: the live response's `warnings` array carried basis
and suppression notes only. So the rule is mechanical now rather than
per-metric — a description may carry one `Population caveat:` sentence
group, and `PlanValidationService` publishes it as a warning on **every**
answer that reads the metric (`revi_investigation.application.validation`,
step 5). Prose stays out of the semantic fingerprint, so authoring or
editing a caveat never forces a version bump; publishing it is not
optional.

## Contract revisions

- **`denial_rate` v2 / `clean_claim_rate` v2 (adjudicated population
  restored, 2026-08-08).** See "Adjudicated population restored" above.
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

## Coverage wave 2 (2026-08-08) — widening the governed surface

Purely **additive**. No existing contract's id, formula, exclusions,
grain, basis or semantics changed; no version was bumped; the reference
conversation and the eight guide questions run over untouched content.
Everything below is new artifacts plus new closed-set entries.

| artifact | before | after |
| --- | --- | --- |
| metric contracts | 27 | 49 |
| playbooks | 8 | 18 |
| concepts | 90 | 108 |
| bindings | 17 | 32 |
| conclusion policies | 9 | 19 |
| presentation recipes | 10 | 20 |
| benchmark figures | 19 | 20 |
| anomaly-actionability category rules | 8 | 15 |
| knowledge cards | 55 | 55 (see "No KB wave 2" below) |

### The authoring rule this wave followed

Author only inside the **computable intersection**: a contract ships if
its measure fields resolve to catalog measures at its own entity, its
filter dimensions are catalog dimensions bound at that entity, and its
primary basis is bound there. Everything outside that goes in the
deferred list below with the catalog or platform change it needs — never
into a contract that would look authoritative and raise
`UNSUPPORTED_CONCEPT` the first time somebody asked.

That rule is not theoretical. Before this wave, 13 of the pack's 27
contracts could not execute at all (see the computability appendix), and
none of them announces that in its own text. Every one of the 22 new
contracts was compiled and executed against `data/revi_warehouse.duckdb`
snap_003 before it was kept, and each value was reproduced by a
hand-written SQL query against the same views.

### Metric contracts added (22)

Dollars and volume rulers the catalog could always compute but nothing
addressed: `allowed_dollars`, `expected_reimbursement`, `line_charges`,
`line_volume`, `patient_responsibility_dollars`,
`contractual_adjustment_dollars`, `refund_dollars`, `denied_ar_dollars`,
`denied_claims`, `denial_volume`, `appeal_volume`,
`overturned_denied_dollars`, `remit_volume`.

Ratios: `net_to_gross_rate`, `patient_responsibility_rate`,
`write_off_rate`, `refund_rate`, `claim_resolution_rate`,
`ar_over_120_pct`, `appeal_overturn_dollar_rate`, `appeals_pending_pct`,
`denials_unworked_dollar_pct`.

Three shapes recur and are worth naming:

- **Count-and-dollar twins.** `appeal_overturn_rate` /
  `appeal_overturn_dollar_rate` and `denials_unworked_pct` /
  `denials_unworked_dollar_pct` are separate contracts, not one contract
  with a unit switch. They diverge exactly when the big-dollar denials
  behave differently from the small ones, and that divergence is the
  finding. Executed at wm_003 the unworked pair reads 77.76% of records
  against 80.30% of dollars — the untouched denials skew slightly larger.
- **Band contracts are never scope filters.** `ar_over_120_pct` is its
  own contract rather than `ar_over_90_pct` with a bucket filter, because
  the published guidance differs by band and the two must never be read
  against each other's benchmarks. Their descriptions say so.
- **Same-entity ratios only.** `write_off_rate` and `refund_rate` divide
  two transaction-entity measures; `patient_responsibility_rate` and
  `net_to_gross_rate` divide two claim-entity measures. A ratio pairing
  measures from different entities does not compile today — see
  "Cross-entity ratios" in the deferred list.

### Playbooks added (10)

`ar_aging_review`, `appeals_effectiveness`, `credit_balance_review`,
`clean_claim_review`, `patient_responsibility_review`,
`charge_capture_review`, `payer_scorecard`, `denial_category_drilldown`,
`volume_mix_shift`, `write_off_review`.

Each ships a conclusion policy whose `required_evidence` names probe
templates that playbook actually emits (pinned by
`test_wave_2_conclusion_policies_require_evidence_their_playbook_produces`),
so no policy describes a conclusion the shape can never reach.

Two policies were narrowed during authoring rather than shipped
aspirationally:

- **`charge_capture_claim`** requires only `procedure_volume` and
  `claim_volume_context`, and claims *procedure-level capture moved*, not
  *we are missing charges*. The unbilled-inventory and charge-timing
  probes are in the playbook, but `dnfb_dollars`, `charge_lag_days` and
  `late_charge_pct` were all unanswerable when this was written, and a
  missing-charge attribution needs them.
  **Updated 2026-08-08:** all three now execute at the source, but only
  `dnfb_dollars` also clears §6.6 validation; `charge_lag_days` and
  `late_charge_pct` are still pruned before execution (Appendix B). The policy
  is unchanged, and one third of the reason for the narrowing is gone.
- **`credit_balance_claim`** deliberately keeps `credit_standing` (which
  read the then-unanswerable `credit_balance_dollars`) as required evidence.
  The playbook therefore could not conclude until `credit_balance_cents` was
  delivered. That was the intended behavior: no credit-balance number, no
  credit-balance conclusion. Refund evidence alone would let it assert a
  liability it never measured.
  **Updated 2026-08-08:** `credit_balance_cents` shipped and
  `credit_balance_dollars` executes, so the *contract* is no longer the
  blocker — but §6.6 validation still prunes `credit_standing`, so the playbook
  still cannot conclude. Same behavior, different cause; see Appendix B.

`payer_scorecard` complements `dimension_scorecard` rather than
duplicating it. The generic one parameterizes ANY certified dimension
with one governed metric set; the payer one fixes the dimension so it can
carry payer-specific evidence the generic shape has no room for (denial
code profile, appeal outcomes, write-off pressure) and a required
service-line mix control, because cross-payer levels are only meaningful
on comparable mix.

### Named cohort concepts, and the schema limit they hit

`government_payers`, `commercial_payers`, `managed_care`,
`medicare_advantage`, `traditional_medicare`, `medicaid_coverage`,
`surgical_services` and `high_dollar_claims` give analyst phrases a
governed home. Each names its predicate **in prose inside the concept
definition**, using the catalog's own declared value domains, and carries
a `bindings.yaml` row to the certified dimension that expresses it.

The prose is not a stylistic choice. The pack schema has no artifact type
that carries a *predicate*: `Concept` has id/name/description/definition/
aliases/related/sources, and `BindingCandidate` carries a concept id, one
dimension-or-measure id, a state and a strength. Neither can hold
`payer_type IN (MEDICARE, MEDICARE_ADVANTAGE, MEDICAID, MEDICAID_MCO)` as
structure.

**These concepts are non-executable vocabulary today, and an earlier
revision of this note overstated them.** It claimed the interpreter
"renders concept definitions into its prompt" and therefore "has
everything it needs to build a legal filter". It does not. `Interpretation
Service._vocabulary` builds the concept block as
`f"- {cid}: {name}"` over `self._pack.concept_summaries()`, and
`concept_summaries()` returns `(id, name)` pairs — metric and playbook
summaries carry a 160-character description clip, concepts carry no
description at all. So the model sees `government_payers: Government
Payers` and nothing more, and a legal filter still has to be constructed
from certified dimensions by the model unaided. What the pack content
actually buys today is narrower and still worth having: the term resolves
(alias lookup), the DEFINITIONAL path returns the values to a human, the
binding proves the dimension is certified, and a reviewer has one place
to check what a named scope is supposed to mean.

Two ways out, and they belong to different lanes:

- **Phase 2, pack schema (the real fix).** A structured cohort artifact —
  `{id, entity, predicate}`, validated against the catalog at composition
  time — turns these from prose into predicates the planner compiles. See
  deferred.
- **Cheap interim, interpretation lane (not this pack's to make).**
  Extending `concept_summaries()` to carry a clipped definition, exactly
  as metric and playbook summaries already do, would at least put the
  values in front of the model. That is a change to
  `revi_api.adapters.concept_summaries`, the `CapabilityPack` protocol and
  the `interpret_question` template — investigation/API work, deliberately
  out of scope for a pack pass, and recorded here so it is not mistaken
  for something the pack can do to itself.

`high_dollar_claims` is the honest counterexample and is authored as one:
claim value lives in measures, never in a dimension, so **no** dollar-
threshold predicate is legal here. Its binding is
`state: observed, strength: unavailable` — the same shape
`pos_collections` uses — and the concept says outright that ranking and
share-of-total answer the adjacent question instead. Pinned by
`test_high_dollar_cohort_is_declared_unavailable_rather_than_approximated`.

### Anomaly actionability: seven categories were running on the default

The warehouse's detection feed emits 13 categories.
`anomaly_actionability.yaml` had rules for 6 of them plus two (`COB`,
`CASH_DECLINE`) that nothing emits. The other seven — `DNFB`,
`UNWORKED_DENIALS`, `SUBMISSION_GAP`, `ELIGIBILITY_CLUSTER`,
`POSTING_LAG`, `CHARGE_ENTRY_LAG`, `CHARGE_HOLD` — fell to the blanket
`fraction: 0.50` default. They are roughly half of every snapshot's
portfolio, so "assume half is workable" was setting the priority order
for half the worklist by accident.

All seven now carry argued rules, and each fact name was verified against
`data/answer_key.json` rather than guessed. The spread is deliberate:
`DNFB` 0.95 (the money is unbilled, not lost) down to `CHARGE_ENTRY_LAG`
0.10 (the charges are already captured; only runway is at risk) and
`POSTING_LAG` 0.15 (remitted cash arrives by itself — that anomaly earns
its place by what it predicts, not by dollars to chase).

`UNWORKED_DENIALS` shipped this wave with a flat `fraction: 0.45` and an
argument for it. The argument was wrong at the end that matters, and the
rule was changed in the fix pass below — see "Audit response".

Covered by `test_anomaly_actionability_covers_every_emitted_category`,
which also asserts every rule carries a rationale, a fraction in [0, 1],
and the facts its mode requires. That file is read by the API composition
root rather than the pack loader, so it had no test in this package
before.

### No KB wave 2

`knowledge.yaml` gained nothing. Every card in it is machine-researched
with a cited, dated, URL-bearing source, and the loader requires exactly
that. Authoring new cards without doing the research would mean inventing
citations into a governed artifact whose whole value is that its
provenance is real. A KB wave 2 is a research task, not a content-authoring
task, and it is left as one.

`benchmarks.yaml` gained exactly one figure, and by promotion rather than
research: **`benchmark.ar_over_120_pct.mgma_better_performers`** (8.1%).
That figure was already carried as narrative inside
`ops.map_keys_measurement` with its MGMA source, and "KB wave 1" above
recorded it as dropped *for want of a Revi metric* — only `ar_over_90_pct`
existed. `ar_over_120_pct` now gives it an honest home, so it was promoted
with its original source intact and five cautions (physician-group cohort,
2020 data, better-performer not median, our gross-billed denominator, and
never read against the 90-day band).

Two other wave-1 dropouts stayed dropped:

- **Total write-offs under 5% of collections** (vendor operating KPI,
  `ops.write_off_governance`). `write_off_rate` is now a
  collections-denominated write-off ratio, so the *shape* fits. It was
  still not promoted: the published KPI's numerator is ambiguous about
  whether contractual allowances are included, while `write_off_rate`
  counts the non-contractual family only. If contractuals are in the
  published figure, our number reads structurally low against it and the
  benchmark becomes a machine for concluding "we are doing great".
  Cautions cannot fix a denominator-and-numerator mismatch that a reader
  would have to resolve to use the number at all.
- **Patient/self-pay collection rate 34.5–37.6%** (Kodiak). Still no
  metric to hang it on: a patient collection rate is a cross-entity ratio
  and does not compile (see deferred).

### Closed sets extended

- `packages/pack/tests/test_base_pack_content.py`: `WAVE_2_METRICS` (22),
  `WAVE_2_PLAYBOOKS` (10), `WAVE_2_COHORT_CONCEPTS` (7 concept →
  certified-dimension pairs); breadth floors raised to concepts ≥ 100,
  metrics ≥ 45, benchmarks ≥ 20; plus the anomaly-actionability category
  coverage test.
- `packages/pack/tests/test_pack_conformance.py`: the contracts-carrying-
  exclusions set grew from 3 to 5 (`patient_responsibility_rate`,
  `denials_unworked_dollar_pct`), each spelled out with its dimension,
  `eq` polarity and certification check — because a resolving exclusion is
  structurally indistinguishable from a correct one, so only the pinned
  polarity distinguishes them.
- `DERIVED_SUM_MEASURES` is **unchanged**. No new derived measures were
  introduced; every new contract sums a real catalog measure or counts a
  real primary key. That was a design constraint, not a coincidence — the
  registry's seven entries are all undelivered (below).

### Deferred: catalog, generator and platform work this wave could not do

Recorded rather than implemented, per the pack-only boundary.

**Blocked on new certified dimensions**

- **`allowed_to_charge_rate`** (line allowed ÷ line billed). Needs
  `status` (or an adjudicated flag) certified at `claim_line`: without it
  the denominator counts lines whose allowed amount is still null and the
  ratio reads low by construction. `allowed_dollars`' description already
  warns readers not to compute it by hand for exactly this reason.
- **Zero-pay vs partial-pay denial split.** Still needs `denial_level`
  certified (carried forward from wave 1).
- **`rarc_synthetic` / `revenue_code` promotion.** Both resolve today and
  downgrade answers to discovery grade, which is correct and deliberate.
  Certifying them needs real remark codes and real UB revenue codes from
  the generator, not a catalog edit.
- **Unbilled-A/R and DNFB populations.** ~~`submission_date` and
  `discharge_date` are base-view columns, not dimensions, so
  `dnfb_dollars` and `timely_filing_at_risk_dollars` cannot compile their
  own `filtered:` predicates. Certifying a `billed_flag` /
  `discharged_flag` pair (or the raw date columns) unblocks both contracts
  and two probes in `charge_capture_review`.~~
  **DELIVERED 2026-08-08, on both paths.** Both flags are certified; both
  contracts were renamed onto them, and both compile, execute and clear §6.6
  (Appendix B, "the two renames"). Only one of the two
  `charge_capture_review` probes it unblocks — `unbilled_standing` — is
  reachable through a question; `charge_timing` is held by the §6.6 gap below.
- **`first_pass_paid`.** ~~Same class; blocks `first_pass_yield` and one
  probe in `clean_claim_review`.~~ **DELIVERED 2026-08-08, on both paths** —
  certified, and `first_pass_yield` plus `clean_claim_review/first_pass_detail`
  both execute and clear §6.6.
- **`txn_type`.** Blocks restoring `avg_days_to_pay`'s payer-payment
  population (carried forward from the exclusion-polarity correction).
- **`remit_seq`.** Would let `remit_volume` separate first-pass
  adjudication from rebill and appeal traffic, which is most of the value
  of counting remits at all.
- **Patient geography / age band.** No patient-level dimension is
  certified, so nothing in the patient-collections family can be cut by
  who the patient is.

**Blocked on new measures or generator columns**

- **`patient_collection_rate`** (patient cash ÷ patient responsibility).
  The obvious next patient metric, and the one that would let the Kodiak
  34.5–37.6% benchmark ship. Blocked twice over: the numerator is a
  transaction measure and the denominator a claim measure, so it is a
  cross-entity ratio (below), and the 10–45 day posting lag means short
  windows pair unrelated cohorts.
- **Patient cash as a share of TOTAL cash.** Needs either an additive
  `total_cash_cents` measure (payments plus patient payments) or
  expression-level addition in `MeasureExpr`; today a numerator can be one
  `Sum`, one `Count`, one `CountDistinct` or a `Filtered` wrapper around
  one of those, and nothing else.
- **Allowed-vs-paid variance at line grain.** No line-level paid amount
  exists; `underpayment_cents` compares allowed against expected at claim
  grain, which is a different question.
- **`underpayment_rate`** (variance ÷ expected). Shape is fine and the
  `underpayment_detector` policy already expresses its threshold in those
  terms — but it would sum the undelivered `underpayment_cents`, adding a
  second unanswerable contract to the one that already exists.
- **POS collections**, **denial-to-appeal cycle time**,
  **denial-to-resolution time**, **bad debt / charity split**, **cost to
  collect**, **CMI**, **payer rejection rate**, **unapplied cash**,
  **takeback rate**: all carried forward from wave 1, all still blocked on
  absent events or fields. Wave 2 added governed *concepts* for bad debt,
  charity care, uncompensated care and patient statements so the terms
  resolve and an answer can say plainly that the quantity is not
  measurable here — which is the honest half of the coverage.

**Blocked on platform work (not catalog, not pack)**

- **Cross-entity ratio-of-sums.** ~~`net_collection_rate` and
  `gross_collection_rate` have shipped since M4 and have never executed:
  `ProbeCompiler._value_binding` raises `GRAIN_INCOMPATIBLE` when a
  measure's entity differs from the probe entity, so a claim-grain probe
  cannot sum `payment_cents`. The kernel's stated design (fetch both
  additive components, divide per cell) is not implemented. This is the
  single highest-value platform fix for pack coverage: it unblocks two
  shipped contracts, `patient_collection_rate`, and any future
  cash-against-expected work.~~
  **DELIVERED IN THE ADAPTER 2026-08-08.** Both contracts compile and execute
  and reconcile cut by payer (Appendix B). ~~**Not delivered on the product
  path:** §6.6 validation still requires every measure to be a catalog measure
  at the probe's own entity, so both are pruned before execution when reached
  through a question.~~ **Delivered on the product path the same day** by the
  capability negotiation below; both now answer through a typed turn at the
  Appendix B figures.
  `patient_collection_rate` is still unwritten — it is blocked on the
  posting-lag argument above, not on the compiler.
- **The seven derived measures.** ~~`ar_age_days_billed_cents`,
  `underpayment_cents`, `credit_balance_cents`, `payment_lag_days`,
  `charge_entry_lag_days`, `submission_lag_days` and `late_charge_cents`
  are documented in the registry above and **none is delivered**. The
  DuckDB compiler implements exactly two probe-time derivations —
  `open_balance_cents` and the `ar_age_bucket` CASE expression, both on
  the snapshot path — and every other derived id falls through to
  `declared_columns` and raises `UNSUPPORTED_CONCEPT`. Seven shipped
  contracts depend on them.~~
  **DELIVERED IN THE ADAPTER 2026-08-08.** All seven are implemented in the
  DuckDB compiler and all seven dependent contracts execute (Appendix B).
  ~~**Not delivered on the product path:** §6.6 prunes all seven, so the
  contracts answer only when a probe is handed to the adapter directly.~~
  **Delivered on the product path the same day** by the capability
  negotiation below: all seven contracts answer through a typed turn.
  One follow-on landed with the audit pass:
  `charge_entry_lag_days` and `late_charge_cents` read `charge_entry_date`,
  which no dimension, measure, date basis or join path named, so the adapter
  reached it through a private constant. It is now declared under
  `claim_line.declared_columns` in `warehouse/catalog/entities.yaml` and the
  derivation checks the declaration before emitting SQL — the column
  dependency is catalog-visible instead of adapter-private.
- **§6.6 does not know what the adapter can do.** ~~*(New 2026-08-08; the
  highest-value platform fix remaining for pack coverage.)*
  `PlanValidationService._field_resolves`
  (`packages/investigation/src/revi_investigation/application/validation.py`)
  decides answerability from the catalog alone: a measure must be a catalog
  measure **at the probe's own entity**, or one of the fields in
  `_SNAPSHOT_DERIVED_FIELDS`, which contains exactly one entry
  (`open_balance_cents`). That predicate was written when it was true. It has
  been false since the adapter grew seven probe-time derivations and
  cross-entity ratio-of-sums, and the consequence is not a warning but a
  refusal: the plan is pruned to empty and the turn raises
  `UNSUPPORTED_CONCEPT: no probe in the plan is answerable at the source` —
  a sentence about the source that the source disproves. Measured at wm_003:
  **9 of 49 contracts**, **3 of 55 wave-2 probe groups** (`charge_timing`,
  `bill_timing`, `credit_standing`) and **12 of 33 portfolio cards** are
  refused here and nowhere else. The fix is a capability declaration the
  adapter publishes and the validator consults, rather than a second hardcoded
  list; a hardcoded list is what produced this. Pack-side this is unreachable —
  no contract edit can route around a guard that is checking the wrong thing.~~
  **DELIVERED 2026-08-08 (capability negotiation, design §6.3).**
  `RepositoryCapabilities` now carries `derived_measures` — each with its
  catalog entity and the probe shapes that can compute it — and
  `cross_entity_ratio_of_sums`; the DuckDB adapter builds that advertisement
  from `_DERIVED_MEASURES` itself, so there is one list rather than two that
  drift. `_field_resolves` is gone, replaced by a negotiation that consults
  the catalog *and* the advertisement, and `_SNAPSHOT_DERIVED_FIELDS` is gone
  with it — `open_balance_cents` is now advertised like every other
  derivation. Both defaults are empty, so an adapter that computes nothing
  extra keeps the old refusal, word for word. Re-measured at wm_003 the same
  day, each contract posed on its own primary basis:

  | layer | before | after |
  | --- | --- | --- |
  | contracts, product path | 39 of 49 | **48 of 49** |
  | wave-2 probe groups named above | pruned | **all three survive** |
  | probe groups across all 18 playbooks | 75 of 92 | **92 of 92** |
  | portfolio cards drillable | 21 of 33 | **29 of 33** |

  The one contract still refused is `denial_rate` on its primary `remit`
  basis, which is the catalog gap below and not a capability gap — it is
  pinned as still-refused by
  `packages/investigation/tests/test_capability_negotiation_reference.py`.
  The four cards still blocked are `gross_collection_rate` and
  `underpayment_variance` cut by `proc_group`, which binds at `claim_line`
  only: the pre-existing drill-scope problem recorded under "Drill scope
  reduction" below, now visible as itself instead of hidden behind
  `UNSUPPORTED_CONCEPT`. Two probe-shape laws came with the negotiation:
  a snapshot-only derivation asked for inside a flow aggregation is refused
  at *plan* time with the reason the adapter would have given, and a
  cross-entity metric's group keys and window basis must bind at both
  entities — so the plan-time and execute-time verdicts cannot disagree in
  either direction.
- **A composing actionability mode.** `UNWORKED_DENIALS` wants
  "in-window share × overturn probability"; the rule language offers
  `fraction`, `open_share` and `flag_share` with no way to multiply two.
  A `product` mode over a list of sub-rules would express it.
- **Drill scope reduction.** The `CONTRACTUAL` anomaly's `impact_cents`
  equals its `contractual_adj_cents` fact **to the cent** (49,326,636), so
  repointing `gross_collection_rate` → `contractual_adjustment_dollars`
  would reproduce the card exactly — the same move the `denial_rate` →
  `denied_dollars` repoint makes. It was **not** added: the card's
  dimensions include `proc_group`, `_drill_spec` passes every detected
  dimension as both breakdown and filter, and `proc_group` binds at
  `claim_line` only, so the repointed drill would still be blocked while
  wearing a "repointed" label that implies it works. Fixing it needs
  either a claim_line-grain contractual measure or a drill that drops
  unsupported scope dimensions and says so.
- **A structured cohort artifact.** See "Named cohort concepts" above: a
  `cohort_definitions` artifact type carrying `{id, entity, predicate}`
  validated against the catalog would turn the wave-2 cohort concepts from
  prose the interpreter reads into predicates the planner compiles.

### Appendix A — executed values for every new contract

Window `2026-05-01 .. 2026-08-02`, watermark `wm_003`, schema `snap_003`;
snapshot contracts as of `2026-08-02`. Each was produced twice: once by
compiling the contract through `ProbeCompiler` and once by a hand-written
query over the same base views. The two agreed exactly in all 22 cases.

| contract | grain / basis | numerator | denominator | reading |
| --- | --- | --- | --- | --- |
| `allowed_dollars` | line / service | 1,920,143,044 | — | $19.20M |
| `line_charges` | line / service | 5,697,582,337 | — | $56.98M |
| `line_volume` | line / service | 48,294 | — | 48,294 lines |
| `expected_reimbursement` | claim / service | 2,623,183,106 | — | $26.23M |
| `patient_responsibility_dollars` | claim / service | 221,982,196 | — | $2.22M |
| `contractual_adjustment_dollars` | transaction / post | 2,813,889,276 | — | $28.14M |
| `refund_dollars` | transaction / post | 806,374 | — | $8,064 |
| `denied_ar_dollars` | claim / service (snapshot) | 209,606,158 | — | $2.10M |
| `denied_claims` | denial / remit | 1,582 | — | 1,582 claims |
| `denial_volume` | denial / remit | 1,582 | — | 1,582 records |
| `appeal_volume` | denial / remit | 359 | — | 359 appeals |
| `overturned_denied_dollars` | denial / remit | 14,866,645 | — | $148,666 |
| `remit_volume` | remit / remit | 18,923 | — | 18,923 remits |
| `net_to_gross_rate` | claim / service | 2,623,183,106 | 5,642,309,382 | 46.49% |
| `patient_responsibility_rate` | claim / service | 215,865,809 | 1,828,254,169 | 11.81% |
| `write_off_rate` | transaction / post | 207,718,760 | 2,130,678,924 | 9.75% |
| `refund_rate` | transaction / post | 806,374 | 2,130,678,924 | 0.038% |
| `claim_resolution_rate` | claim / service | 12,615 | 19,672 | 64.13% |
| `ar_over_120_pct` | claim / service (snapshot) | 1,410,505,150 | 3,438,036,345 | 41.03% |
| `appeal_overturn_dollar_rate` | denial / remit | 14,866,645 | 29,475,192 | 50.44% |
| `appeals_pending_pct` | denial / remit | 187 | 359 | 52.09% |
| `denials_unworked_dollar_pct` | denial / remit | 258,249,313 | 321,594,630 | 80.30% |

Cross-checks that fell out of the run and are worth keeping:

- `ar_over_90_pct` reads 1,513,436,476 / 3,438,036,345 = **44.02%** over
  the same open population, against `ar_over_120_pct`'s 41.03% — the
  91–120 band is only three points of A/R here, which is exactly why the
  bands are separate contracts and not one with a filter.
- `denials_unworked_pct` (records) 77.76% against
  `denials_unworked_dollar_pct` 80.30%: the untouched denials skew larger,
  so the record count understates the money.
- `appeal_overturn_rate` (records) 49.42% against
  `appeal_overturn_dollar_rate` 50.44%: near parity, so in this data
  appeal wins are not size-biased. The contracts still ship as a pair;
  parity is a finding, not a reason to drop one.
- **The generator emits at most one denial record per claim** (max 1,
  zero claims with more, over 1,582 denied claims in the window). So
  `denial_volume` and `denied_claims` return identical numbers here and
  `denied_claims`' non-additivity caveat never bites on the reference
  data. Both descriptions now say so: the distinction is governed meaning
  for production remittances, not an observed effect.

### Appendix B — computability of the whole contract set

Measured by compiling every contract and executing it against snap_003.
**48 of 49 execute; 1 does not** (re-measured 2026-08-08 after the two
unbilled-inventory renames below; the count read 46 of 49 earlier the same
day). Twelve of the thirteen that were dark are now lit. Ten of them were lit
by adapter and catalog work only — **no metric contract changed**, which was
the point: every one of those ten was already correctly specified and had
simply never been delivered. The remaining two, `dnfb_dollars` and
`timely_filing_at_risk_dollars`, needed a genuine pack-side edit, recorded
under "the two renames" below.

**Read that number with the qualifier it needs.** "Executes" here means the
**source path**: `ProbeCompiler` compiles the contract and
`DuckDbAnalyticalRepository` returns a frame. That is not the same as "a user
can ask for it". A question traverses the **product path** — planner, then
`PlanValidationService` (§6.6), then execution. For one day the two disagreed:
§6.6 decided answerability from the *catalog alone* plus one hardcoded field
name, so a contract whose numerator sums a probe-time derived measure, or
pairs measures across entities, was **pruned before it was ever executed** —
by a component that had not been told the adapter grew the ability. That is
closed: the repository now advertises what it computes and §6.6 reads the
advertisement (design §6.3; see the deferred-work entry above). The two
layers, measured the same day at the same watermark, before and after:

| layer | what it measures | before | after |
| --- | --- | --- | --- |
| source | compile + execute against snap_003 | **48 of 49** | **48 of 49** |
| product | planner + §6.6 validation + execution | **40 of 49** | **48 of 49** |

The "before" product figure is the one measured when the gap was found (each
contract posed on a basis its contract allows, so `denial_rate` answered);
posing every contract on its own **primary** basis instead reads 39 before and
48 after, because `denial_rate`'s primary `remit` basis is unbound at the
claim entity either way. Both readings agree on what changed: the nine
capability-gap refusals, and nothing else.

The nine that were refused on the product path were the seven derived-measure
contracts (`avg_days_to_pay`, `bill_lag_days`, `charge_lag_days`,
`late_charge_pct`, `underpayment_variance`, `days_in_ar`,
`credit_balance_dollars`) plus the two cross-entity ratios
(`net_collection_rate`, `gross_collection_rate`), each with
`UNSUPPORTED_CONCEPT: no probe in the plan is answerable at the source` — a
message that was, precisely, wrong: the source could answer all nine. All nine
answer through a question now, at the figures in the table below (the product
path reproduces the source path to the cent — pinned by
`packages/investigation/tests/test_capability_negotiation_reference.py`).
**Both renames in this pass clear both paths** — `dnfb_dollars` and
`timely_filing_at_risk_dollars` were blocked on a filter dimension the catalog
genuinely did not define, so certifying the flags fixed the real thing rather
than routing around a stale guard.

Window `2026-05-01 .. 2026-08-02` on each contract's primary basis (snapshot
contracts as of `2026-08-02`), watermark `wm_003`. Each figure was produced
twice, once through `ProbeCompiler` and once by a hand-written query over the
same base views; the pairs are pinned in
`TestPreviouslyDeadContracts` (`packages/connector-duckdb/tests/`).

| newly executing | numerator | denominator | reading | what delivered it |
| --- | --- | --- | --- | --- |
| `net_collection_rate` | 1,494,532,901 | 2,623,183,106 | 56.97% | cross-entity ratio-of-sums |
| `gross_collection_rate` | 1,494,532,901 | 5,642,309,382 | 26.49% | cross-entity ratio-of-sums |
| `avg_days_to_pay` | 328,459 | 48,984 | 6.71 days | derived `payment_lag_days` |
| `bill_lag_days` | 149,973 | 18,410 | 8.15 days | derived `submission_lag_days` |
| `charge_lag_days` | 100,670 | 48,294 | 2.08 days | derived `charge_entry_lag_days` |
| `late_charge_pct` | 953,661,749 | 5,697,582,337 | 16.74% | derived `late_charge_cents` |
| `underpayment_variance` | 14,306,720 | — | $143,067 | derived `underpayment_cents` |
| `days_in_ar` | 548,063,722,723 | 3,438,036,345 | 159.41 days | derived `ar_age_days_billed_cents` |
| `credit_balance_dollars` | 5,221,798 | — | $52,218 | derived `credit_balance_cents` |
| `first_pass_yield` | 14,318 | 18,410 | 77.77% | certified dimension `first_pass_paid` |
| `dnfb_dollars` | 963,165,147 | — | $9.63M | filter rename to `discharged_flag` / `billed_flag` |
| `timely_filing_at_risk_dollars` | 2,242,600,028 | — | $22.43M | filter rename to `billed_flag`; unchanged by the v2 runway cut below |

Three readings deserve a sentence each:

- `days_in_ar` reads **159 days**, not the tens a hospital would report. It
  is the aging form over this warehouse's open inventory, whose dollars are
  bimodal: 41% sit past 120 days (the planted unbilled/charge-hold clusters,
  tail 578 days) against a fresh 0–30 band. Its denominator is
  3,438,036,345 — byte-identical to `ar_over_120_pct`'s in Appendix A, which
  is the reconciliation that matters: two snapshot contracts value the same
  A/R identically.
- `avg_days_to_pay` reads **6.71 days** and is diluted downward exactly as
  the contract's own description warns: the numerator is payment lag only,
  the denominator counts every transaction in the window.
- `first_pass_yield` and `clean_claim_rate` count **different numerators over
  the same claims**, and the difference is exact. Over the window's 18,410
  claims: 14,318 are `first_pass_paid` and 13,725 are `clean_claim`; no claim
  is clean without being first-pass-paid, and the 593 that are first-pass-paid
  without being clean are *precisely* the OPEN-but-adjudicated population —
  the payer decided them clean, the cash has not posted. `first_pass_paid`
  reads the remit; `clean_claim` reads posted cash. (The published
  `clean_claim_rate` of 91.09% is over its own adjudicated-only denominator of
  15,068, so it is not comparable to 77.77% directly; 13,725/18,410 = 74.55%
  is.) See the rewritten `status` note in
  `warehouse/catalog/dimensions.yaml`.

**The two renames (2026-08-08).** `dnfb_dollars` and
`timely_filing_at_risk_dollars` were the only dark contracts that needed pack
content to change, and the change is a rename, not a redefinition. Both named
raw date columns as filter **dimensions** — `discharge_date` /
`submission_date` — which are date BASES in this catalog (a window rides on
them) and therefore resolve nowhere as predicates. The certified boolean
dimensions carrying the same facts are `discharged_flag` and `billed_flag`,
materialized straight from those two columns in
`warehouse/generator/src/revi_warehouse/writer.py` and guarded by `verify.py`.
So `NOT discharge_date IS NULL` became `discharged_flag eq true`, and
`submission_date IS NULL` became `billed_flag eq false`, in both contracts. No
id, version, grain, basis, unit, sign or scope-dimension moved and neither
description needed a word changed, because neither described the population in
terms of the filter syntax. That the population is genuinely identical is
asserted rather than assumed: `test_dnfb_dollars_reads_the_certified_flags_not_the_raw_dates`
and `test_timely_filing_at_risk_dollars_values_open_unbilled_inventory` each
execute the probe and then compute the same figure twice in SQL — once from
the flags, once from the raw dates — and require all three to agree.
`test_numerator_filter_dimensions_are_certified_catalog_dimensions`
(`packages/pack/tests/`) closes the hole that let the defect live: nothing had
ever walked the predicates inside a `filtered:` numerator, so a date basis
posing as a dimension passed every content test and failed only at probe time.

Two `dnfb_dollars` caveats, both about the flag rather than the contract:

- ~~`discharged_flag` is **not as-of aware**. It reads the claim's CURRENT
  discharge date, so a back-dated snapshot query counts claims discharged
  after the as-of. Measured at `as_of 2026-06-01` on snap_003: 5 claims worth
  3,028,611 cents, which reads `dnfb_dollars` there as 654,723,734 where the
  date form gives 651,695,123.~~
  **CLOSED 2026-08-09.** The snapshot compiler projects the flag at the
  probe's as-of — `SELECT * REPLACE (discharge_date <= as_of AS
  discharged_flag)`, the same treatment `resolved_date` already had — so the
  back-dated reading is now 651,695,123, the date form, and
  `test_discharged_flag_is_projected_at_the_probes_as_of` asserts all three
  numbers. At the watermark nothing moved: no claim in any snapshot carries a
  discharge date past that snapshot's cutoff, so `dnfb_dollars` is still
  963,165,147. `status` remains watermark-derived and **cannot** be restated
  this way (it summarises remits and cash, not a claim column); the size of
  that gap is now measured in the catalog's own `status` note — at
  `as_of 2026-06-01` the open-inventory population of 9,907 claims contains
  3,827 that read PAID, 472 CLOSED and 214 DENIED because that is what they
  became later.
- DNFB is the discharged slice of the timely-filing inventory, so
  963,165,147 sits inside 2,242,600,028 by construction; the tests assert the
  inequality so a future edit cannot silently invert them.

| still unanswerable | blocked on |
| --- | --- |
| `denial_rate` | REMIT basis unbound at the claim entity (primary basis only; alternates answer) |

`denial_rate` is the mild case and the one worth reading carefully: it
fails only at its **primary** basis. The planner uses the question's basis
whenever the contract allows it, so `denial_rate` answers normally on
`service` and `submission` and only falls back to the unbound `remit`
when a question is genuinely posted on a cash or remittance basis. The
`daily_portfolio` and `denial_spike` playbooks reach it through the
allowed bases. It is the one contract in the pack that cannot be probed as
authored, and it stays on this list until the modelling decision behind it is
made (rebind to denial grain, or bind REMIT on claim) — not patched away.

Playbook-level view, same two layers (each probe template's metric ids grouped
by kind, grain and effective basis exactly as `PlanningService` groups them,
then either compiled-and-executed or passed through §6.6). Across the ten new
playbooks:

| layer | result |
| --- | --- |
| source — compiled and executed | **55 of 55** probe groups, every one at `direct` grade |
| product — planner + §6.6 validation | ~~**52 of 55**~~ → **all of them**, once §6.6 began negotiating capabilities with the repository (§6.3) |

The source count read 49 of 54 when wave 2 shipped, then 50 of 55 after the fix
pass added `ar_aging_review/denied_inventory_aging`. The five that were dark
were `unbilled_standing` and `charge_timing` in `charge_capture_review`,
`first_pass_detail` and `bill_timing` in `clean_claim_review`, and
`credit_standing` in `credit_balance_review`. All five now execute at the
source: `first_pass_detail` on the certified `first_pass_paid` dimension,
`bill_timing` on the derived `submission_lag_days`, `charge_timing` on the
derived `charge_entry_lag_days` / `late_charge_cents`, `credit_standing` on the
derived `credit_balance_cents`, and `unbilled_standing` on the `dnfb_dollars`
rename above.

~~**Only two of the five clear the product path**~~ — that was true for one
day, and which three lagged was the whole point: `charge_timing`,
`bill_timing` and `credit_standing` each sum a derived measure the validator
had never been told about, executed perfectly if you handed the probe to the
adapter, and never reached the adapter through a question. **All five clear
both paths now**, and so does every other probe group in the pack: measured
across all 18 playbooks (a payer breakdown on the service basis at wm_003),
92 of 92 groups survive planning where 75 of 92 did before — the extra
seventeen include `underpayment_review`, whose four groups were pruned to
nothing so the whole playbook refused. Pinned by
`packages/investigation/tests/test_capability_negotiation_reference.py`.
Playbook by playbook, with the state each was in when the gap was found:

- **`charge_capture_review`: strengthened, still not complete.**
  `unbilled_standing` now returns DNFB by facility on both paths (6 facilities,
  131,898,265 cents at the largest), taking the playbook from 3 of 5 to 4 of 5
  probe groups on the product path. `charge_timing` executes at the source
  (charge lag and late-charge share by facility) and reaches it through a
  question too since the capability negotiation landed — 5 of 5.
  The `charge_capture_claim` conclusion policy was narrowed during wave 2 to
  claim only *procedure-level capture moved* because `dnfb_dollars`,
  `charge_lag_days` and `late_charge_pct` were all dark; one of those three is
  now genuinely available end to end. The policy is **not** widened here —
  widening it is a content decision with its own review, and two of its three
  missing pieces are still missing where it counts.
- **`clean_claim_review`: strengthened, still not complete.**
  `first_pass_detail` (first-pass yield per payer) clears both paths, so the
  playbook reads 4 of 5 on the product path. `bill_timing`
  (service-to-submission lag by facility) executed at the source and was
  pruned by §6.6; with the negotiation it clears both paths, so the
  slow-billing-versus-dirty-billing distinction its own scope note asks the
  reader to make is reachable from a question — 5 of 5.
- **`credit_balance_review`** was deliberately unable to conclude, and the
  reason moved twice under it. `credit_balance_dollars` stopped being
  unanswerable at the source — it executes and returns 5,221,798 cents, 12
  payer rows at `direct` grade — but `credit_standing` was one of the three
  probe groups §6.6 pruned, so `credit_balance_claim`'s required evidence was
  not produced on the path a user travels. With the capability negotiation
  that block is gone as well: `credit_standing` survives planning and executes
  on the product path, so the evidence is now retrieved where a question can
  reach it. Whether `credit_balance_claim` then fires is a content question
  about its own thresholds and was not re-measured here. The design intent
  never changed and is still correct — no credit-balance number, no
  credit-balance conclusion.

## Audit response (2026-08-08) — corrections to coverage wave 2

An adversarial audit of the wave-2 commit returned must_fix. Everything
below is a correction to wave-2 content only; no pre-existing artifact id,
formula, exclusion, grain, basis or version changed, and no contract took
a version bump (only prose, scope notes, one recipe binding, one
actionability mode and one added probe moved).

Numbers quoted below were re-executed against `data/revi_warehouse.duckdb`
snap_003 over `2026-05-01 .. 2026-08-02` at `wm_003`, the same window
Appendix A uses.

### 1. `clean_claim_rate` + `initial_denial_rate` do NOT sum to one

`clean_claim_review`'s description and its `clean_by_payer` scope note both
asserted the two rates read "an identical population" and therefore sum to
one, making a payer that moved on one and not the other "a data question,
not a finding". False, and in a way that would have suppressed a real
finding. `clean_claim_rate` v2 carries `exclusions: {status eq OPEN}`;
`initial_denial_rate` carries none and divides by every claim in the
window per the Task Force denominator convention.

Executed: `13,725/15,068 + 1,343/18,410 = 91.09% + 7.29% = **98.38%**`, and
per payer between **97.47%** (Silverline MA) and **99.18%** (Federal
Medicare). The shortfall is exactly the un-adjudicated tail — 3,342 of the
window's 18,410 submitted claims, 18.2% — so the gap *is* the finding: it
measures how much of a payer's recent business the payer has not decided
yet. Both strings now say that.

The playbook keeps `initial_denial_rate` rather than swapping to
`denial_rate`. It is the right contract for a first-pass-quality
investigation (it is the Task Force shape, and seven of the pack's
benchmark figures hang on it), and the corrected reading is more useful
than the invariant it replaced. The scope note points at the pair that
*does* partition one population: `clean_claim_rate` against `denial_rate`,
both carrying the OPEN exclusion, executed at `13,725 + 1,343 = 15,068`
exactly — the invariant `TestReferencePackContracts` already pins.

### 2. Named cohort concepts are non-executable vocabulary

See the rewritten "Named cohort concepts" section above. The claim that
the interpreter renders concept definitions into its prompt was wrong;
concepts reach the prompt as `id: name` only. Each cohort concept now says
"vocabulary, not an executable filter" in its own definition, and the
concepts.yaml section comment carries the mechanism.

### 3. Charges − contractuals = expected holds per claim, not per window

`expected_reimbursement` called charges, contractuals and itself "the three
sides of the same identity"; `contractual_adjustment_dollars` said
"charges minus contractuals is the expected quantity". Both invited a
hand-reconciliation that cannot close. Contractuals post only after
adjudication, and the contracts differ in grain and basis
(claim/service against transaction/post). Executed, charges minus
contractuals overshoots expected by:

| basis pairing | residual | expected | gap |
| --- | --- | --- | --- |
| charges service, contractuals post (the primary bases) | 2,828,420,106 | 2,623,183,106 | **+7.82%** |
| charges submission, contractuals post | 2,637,044,946 | 2,545,315,466 | +3.60% |
| both submission | 3,194,326,604 | 2,545,315,466 | +25.50% |
| both service | 3,677,231,653 | 2,623,183,106 | **+40.18%** |

Both descriptions now state the per-claim scope, the reason, and two of
those figures.

### 4. Patient responsibility is near-zero before adjudication, not zero

`patient_responsibility_dollars` asserted it is "zero until the claim's
first remittance lands". Measured: **555** of the window's **6,272** OPEN
claims carry a patient-responsibility amount, **6,116,387 cents** in total.
Small — 2.8% of the window's 221,982,196 — but not zero, and the contract
should not assert what it has not checked.

The `patient_responsibility_rate` exclusion is kept, with the real
rationale substituted: the population is *asymmetric*, not empty. Those
OPEN claims carry **30.3%** of the window's expected dollars
(794,928,937 of 2,623,183,106) against **2.8%** of its patient
responsibility, which is why including them moves the published share from
**11.81%** (215,865,809 / 1,828,254,169) to **8.46%** (221,982,196 /
2,623,183,106) — a third of the reading, produced entirely by claims
nobody has decided.

### 5. `payer_denial_profile` is not "adjudicated claims only"

Only `clean_claim_rate` restricts to adjudicated claims. The scope note
claimed all three metrics did. Executed denominators over the window:
**15,068** (`clean_claim_rate`), **18,410** (`initial_denial_rate`) and
**19,672** (`claim_resolution_rate` on its service basis) — the last two
divide by every claim, so both read low on recent cohorts for reasons that
have nothing to do with the payer. The note now names which metric is
adjudicated-only, says the other two read low on recent cohorts, repeats
that clean and initial do not sum to one, and requires equal-cohort-age
comparison.

### 6. `volume_mix_share` was hijacking every `charges` frame

The recipe bound `applies_to: charges`. `_pick_recipe` matches on measure
name with first match winning, so the recipe captured **every** frame
carrying charges anywhere in the platform — a bare weekly charge trend
included, which then rendered as a single-series stacked bar. It is now
bound to the **playbook id** `volume_mix_shift`, the way
`cash_decline_decomposition` and `scorecard_table` bind. After the change
no recipe in the pack binds to `charges`, so a bare charges frame falls
through to the line/bar heuristic. Pinned by
`test_no_recipe_binds_a_shared_measure_that_would_hijack_bare_frames` and
`test_bare_charges_trend_falls_back_to_the_line_heuristic` in
`packages/pack/tests/test_base_pack_content.py`.

The trade-off is stated in the recipe's own notes: bound to a playbook id,
it now applies to every frame in that playbook, so `volume_trend` (no
dimension) still degenerates to one series. That is smaller and more
contained than capturing the whole platform's charges frames, and the
renderer gap behind it is recorded below.

### 7. `payer_scorecard`'s tiebreak was past the 160-character clip

Playbook selection sees the first 160 characters of the description and
nothing else (see "Playbook `triggers:`" above). The disambiguation
against the pre-existing `dimension_scorecard` sat in sentence three, well
past the clip, so the only surface the interpreter reads carried no reason
to prefer either playbook. The description now leads with it:
"Payer scorecard — the payer-specific alternative to the generic
dimension_scorecard: pick this one when the question names payers, that
one for any other dimension." — 157 characters, inside the clip.
`dimension_scorecard`'s description is untouched.

### 8. `UNWORKED_DENIALS` now assesses on the appealable share

The flat `fraction: 0.45` could not see zero. **ANM-004** (14 denials,
every one past its 60-day appeal window, `appealable_claims: 0`) was
priced at 45% of its $62,035 impact — **$27,916** of "partially
recoverable" money that no appeal can reach, published on a card that also
reports the expiry. The rule now uses `mode: flag_share` over
`appealable_claims / denied_claims`, the same mode and fact pair
`DENIAL_SPIKE` uses on the same evidence, with 0.45 retained only as the
fallback for a record publishing neither fact. Re-run against
`data/answer_key.json` (identical in all three snapshots):

| record | appealable / denied | fraction | recoverable | label |
| --- | --- | --- | --- | --- |
| ANM-004 | 0 / 14 | 0.0000 | **$0** | not recoverable |
| ANM-027 | 17 / 29 | 0.5862 | $31,608 | partially recoverable |
| ANM-028 | 17 / 17 | 1.0000 | $34,669 | highly recoverable |

The wave-2 rationale for the flat fraction was not baseless — `flag_share`
prices ANM-028's all-in-window cohort at 100%, and an appealable share
gates recovery without predicting it, while this pack's own
`appeal_overturn_rate` puts a win near half. The judgment is that a gate
which is exactly right at the zero end beats a flat fraction that is
indefensibly wrong there, and the rule's rationale says the number is an
upper bound. Composing gate × win probability still needs the `product`
mode listed under deferred; that remains the real fix.

### 9. Hygiene

- **Aliases.** The wave-2 plural variants on `takeback` (`takebacks`,
  `recoups`, `recoupments`, `clawbacks`) were dropped; the singulars
  `recoupment` and `clawback` were already there and the concept id and
  name resolve on their own. Worth recording precisely, because the
  audit's stated reason was not right: `normalize_term` lowercases and
  collapses non-alphanumerics, it does **not** stem, so a plural alias is
  a genuinely distinct lookup key rather than a duplicate. They were
  dropped on house style — 5 singular/plural pairs exist across 443
  pre-existing aliases, so plurals are the exception, and adding a set of
  them to one concept is inconsistency, not coverage. `recoup` and `payer
  takeback` were kept: distinct lexeme, distinct phrase.
- **`payer of last resort`** moved from `cob` to `medicaid_coverage`. It
  is a Medicaid-primacy term — it names *which* payer pays last — not a
  generic coordination-of-benefits phrase.
- **Definition length.** `high_dollar_claims` (101 words) and `prompt_pay`
  (97) were the two longest new definitions against a ~48-word median;
  both are trimmed to the mid-60s with nothing dropped that a reader
  needs. The cohort concepts absorbed their new honesty clause by giving
  up wordier phrasing elsewhere, so the file's mean definition length went
  *down* across this pass.
- **Recipes the renderer cannot draw.** `revi_presentation.charts` charts
  ONE primary measure per frame (money-unit column first, else the first
  measure), taking the series from a second **dimension**. Five wave-2
  recipes described companion measures — `deep_aging_profile`,
  `appeal_outcome_pair`, `adjustment_families`, `write_off_pressure` and
  `patient_book_split` (the audit named the first four; the fifth is the
  same defect). All five are reworded as intent-for-a-future-renderer with
  what renders today stated plainly, and the wave-2 block carries a header
  note saying so, so nobody spends an afternoon diagnosing a rendering bug
  that is an unimplemented feature.
- **`denied_inventory_aging`.** `denied_inventory_aging` was the one
  multi-series recipe that could be made real instead of reworded, so it
  was: `ar_aging_review` gains a `denied_inventory_aging` probe cutting
  `denied_ar_dollars` by `[payer, ar_age_bucket]` (both certified, both in
  the contract's `scope_dimensions`). Executed: 60 cells, `direct` grade,
  reconciling to 209,606,158 — Appendix A's `denied_ar_dollars` total to
  the cent. `build_chart_spec` renders it as a genuine stacked bar
  (x=`payer`, series=`ar_age_bucket`), pinned by
  `test_denied_inventory_aging_probe_materializes_the_stacked_recipe`. It
  is the one probe in that playbook with no `top_n`, on purpose:
  `top_n` becomes a probe `limit` over ordered rows, and on a two-dimension
  cut the rows are *cells*, so truncation drops stack segments rather than
  whole bars — a payer missing its 120+ segment would read as having no
  aged denials. The grid is bounded anyway at payers x five buckets.
- **`credit_balance_review`.** A comment at the conclusion-policy
  reference names the deliberate dead end (it cannot conclude until
  `credit_balance_dollars` is answerable) and points here, so the behavior
  reads as designed rather than broken.
  **Updated 2026-08-08:** the dead end holds, the cause moved.
  `credit_balance_dollars` executes at the source now (5,221,798 cents, 12
  payer rows, `direct`); §6.6 validation is what prunes `credit_standing`. The
  comment names the current cause so nobody re-fixes the contract.

## Delivered: the probe-time `filing_rules` join (2026-08-09)

**Status: BUILT.** The milestone this section used to describe said it would
be "replaced by the figures" when it landed. Here they are.

**What shipped.** Two pieces of catalog surface, the same architectural shape
as the `ar_age_days_billed_cents` / `ar_age_bucket` pair they were modelled on:

| artifact | where | what it is |
| --- | --- | --- |
| `days_to_filing_deadline` | derived measure, DuckDB compiler, snapshot shape, claim grain | `(service_date + the plan's timely_filing_days) - as_of`, in days, over unsubmitted claims only |
| `filing_runway_bucket` | certified `derived_bucket` dimension, `warehouse/catalog/dimensions.yaml` | the same quantity as `expired` / `0-30` / `31-60` / `61-90` / `90+`, plus `filed` for a claim whose clock is already closed |
| `timely_filing_days` | `claim.declared_columns`, `warehouse/catalog/entities.yaml` | the limit half of the join, made catalog-visible instead of an adapter constant |

**Where the deadline comes from, and why.** A claim's filing limit is read
from its PLAN — `dim_plan.timely_filing_days`, pre-joined into `v_claim`, so
the compiler reads a column and the base view carries the join, exactly as
§6.1-6.3 require. That is deliberate and not a shortcut: a source adapter
cannot see pack content, so a deadline computed from `filing_rules.yaml`
inside the connector would be a layering inversion, and the plan record is in
any case the *most specific* rule available — the pack's own precedence rule
is "most specific first, first match wins", and its `plan_configuration` tier
is documented as mirroring `dim_plan`. The shipped `TIMELY_FILING` detector
already ages claims the same way (`service_date + timely_filing_days`), so the
metric and the anomaly cards now agree on what a deadline is instead of
holding two.

The anchor is the **service date**. All seven governed rules in
`filing_rules.yaml` declare `date_basis: service`; `dim_plan` additionally
carries a per-plan `timely_filing_basis` and 10 of the 30 plans set it to
SUBMISSION, which cannot start a filing clock because the submission is the
event the clock bounds. It is moot either way — the runway is only defined on
unsubmitted claims, which have no submission date to anchor on.

**Filing-rule coverage, measured.** All 30 plans carry a limit and a basis in
`dim_plan`, so plan-level coverage is complete and the generator needed no
extension. The pack's rule ladder is coarser: it resolves 13 of the 30 plans
to the same limit the plan record carries and the other 17 to a payer-pattern
default that differs (six payers — Atlas, Halvern, Northbridge, Bluestone,
Ashvale, Veritas — have no rule of their own and fall through to
`commercial_default_90` or `unmatched_default_90`). Only **7 of 30** plans
match a rule with `requires_confirmation: false` (Federal Medicare Part A/B,
the four Medicaid MCO plans, State Medicaid HMO); the other 23 are planning
defaults. That is why the contract's caveat says the deadline is a planning
default to confirm for most plans, and
`test_every_plan_resolves_against_the_packs_filing_rules` pins both the
coverage and the agreement of the `plan_configuration` tier with `dim_plan`.
Closing the 17-plan gap is a `filing_rules.yaml` edit (add plan-level rules
for those six payers), not a code change.

**The figures.** At `wm_003`, `as_of 2026-08-02`, `timely_filing_at_risk_dollars`
cut by `filing_runway_bucket` — every cent of Appendix A's 2,242,600,028:

| runway bucket | claims | billed cents | share |
| --- | --- | --- | --- |
| `expired` (deadline gone) | 3,605 | 1,053,056,288 | 47.0% |
| `0-30` | 344 | 103,821,804 | 4.6% |
| `31-60` | 381 | 112,830,845 | 5.0% |
| `61-90` | 880 | 242,754,866 | 10.8% |
| `90+` | 2,767 | 730,136,225 | 32.6% |
| **total** | **7,977** | **2,242,600,028** | 100% |

The `filed` bucket is a real cell of the dimension over open inventory (4,500
claims, 1,292,943,448 cents) and is empty inside this metric by construction,
so the probe returns it with a NULL numerator rather than a zero.

Read the top row first. Round-1 F9 read the metric as "an upper bound on
filing exposure"; the decomposition says something worse and more useful —
**47% of it is already past its deadline**, and only 4.6% is inside 30 days.
Summed runway over the population is 31,980 claim-days
(`days_to_filing_deadline`, cross-checked against `dim_plan` in SQL).

**What changed in the pack, and what did not.** `timely_filing_at_risk_dollars`
is now **v2**: `filing_runway_bucket` joins `scope_dimensions`, which changes
the semantic fingerprint and therefore the version. The numerator is
untouched, so the published total is still 2,242,600,028 and every playbook,
card, fixture and answer-key reference to it still cites the same number.
Narrowing the population to "inside a bounded runway" was considered and
rejected: it would drop the expired dollars, which are the 47%, and a metric
that hides its worst half is not more honest than one that explains itself.
The `Population caveat:` therefore stays — it is now a population statement
rather than an admission of a missing capability — and `metric_display.yaml`
drops the word *proxy* from the display name, because the number is no longer
standing in for a measurement nobody could make.

Still open, and owned elsewhere: the `filing_runway` presentation recipe's
runway annotation, the `timely_filing_watch` playbook prose, the
`portfolio_filing_risk` probe and the `timely_filing_risk_claim` conclusion
policy can all now say what they were written to say. Those are pack-content
edits in the playbook/presentation files, not catalog work.

## Delivered: claim-grain procedure attribution (2026-08-09)

`primary_proc_group` is a certified claim-grain dimension: among a claim's own
lines, the `proc_group` carrying the largest share of billed charges, ties
broken by group name ascending. Materialized in `v_claim` **and**
`v_transaction` (a cross-entity ratio needs its group keys bound at both
entities), guarded by three `--verify` checks per snapshot.

It closes the "Drill scope reduction" gap above: `gross_collection_rate` and
`underpayment_variance` are claim-grain, `proc_group` binds at `claim_line`
only, and the four portfolio cards that wanted a procedure cut were blocked on
exactly that. The rule is stated rather than implied, because it has to be:
this is an **attribution, not a decomposition**. Each claim lands in exactly
one bucket, so a claim-grain measure cut by it reconciles to its ungrouped
total to the cent — at `2026-05-01 .. 2026-08-02` on the service basis,
`gross_collection_rate` (1,494,532,901 / 5,642,309,382) and
`underpayment_variance` (14,306,720) both do — but the non-dominant lines of a
multi-procedure claim are attributed to the dominant group along with the
rest. ORTHO-SURG reads 1,512,940,795 cents at claim grain against 1,492,972,588
at line grain in that window; the difference is the attribution, not an error.
Ask "which procedures cost us" of a line-grain metric cut by `proc_group`; ask
"which claims" of a claim-grain metric cut by this one.

201 claims carry no lines and read NULL. All 201 are zero-billed and carry no
transactions, so no money metric moves; `--verify` pins that NULL happens
exactly when a claim has no lines.
