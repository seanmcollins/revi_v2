# Operator algebra — v0 companion (design §20.1, initial scope)

**Status:** current. This is the initial-scope companion document the design doc gates Phase 2 on.
It specifies the two operators whose methodology is not obvious from their signatures: `decompose`
(volume/rate/mix attribution behind `Explain`) and `project_lagged_realization` (the deterministic
cash outlook). Everything here is versioned kernel code — packs cannot define arithmetic, and the
learning loop cannot propose operators.

**Explicitly deferred to the full companion** (per design §17/§19): timing attribution inside
`decompose` (the cash-decline scenario's remit-lag component is surfaced honestly by the
`cash_outlook` playbook's lag-distribution `compare` probe instead), seasonality/baseline modeling
for detectors, and materiality thresholds relative to tenant scale.

---

## 1. `decompose` v0 — midpoint (Bennet) volume/rate/mix attribution

**Question it answers:** "why did metric M change between period A and period B?" for any metric
expressible per cell as `value = volume × rate` (cash = claims × avg payment; denied dollars =
denials × avg denied amount; denial rate handled as rate-only with volume weighting).

**Inputs:** two frames at the same watermark, same dimension columns (the *cells*), each carrying
per-cell additive columns `volume` and `value` (rate ≡ value / volume, ratio-of-sums per cell).

**Method (Bennet / midpoint two-factor):** for each cell *i*, with `Δv = v₁ - v₀`, `Δr = r₁ - r₀`,
midpoint weights `v̄ = (v₀+v₁)/2`, `r̄ = (r₀+r₁)/2`:

```
volume_contribution_i = Δv_i · r̄_i
rate_contribution_i   = v̄_i · Δr_i
Δvalue_i              = volume_contribution_i + rate_contribution_i        (exact, no residual)
```

The midpoint form is chosen because it is **exactly additive** (contributions sum to the cell's
delta with zero residual term), **symmetric** (swapping periods negates every contribution), and
**order-free** (no arbitrary "which factor moves first" convention). These three properties are
property-tested.

**Mix split:** the volume contribution is further split into overall-volume and share-shift
components. With total volumes `V₀ = Σv₀`, `V₁ = Σv₁` and cell shares `s = v/V`:

```
scale_contribution_i = ΔV · s̄_i · r̄_i          (the book grew/shrank)
mix_contribution_i   = V̄ · Δs_i · r̄_i          (the book shifted between cells)
```

where `V̄` and `s̄` are midpoints. `scale + mix = volume_contribution` per cell up to the standard
second-order cross term, which is folded into `mix` (documented, deterministic, tested to sum
exactly to the volume contribution).

**Output frame:** one row per (cell, component ∈ {volume_scale, volume_mix, rate}) with
`contribution` (same unit as the metric), plus per-cell `delta_total`. Rows ordered by
|contribution| descending. Evidence grade: `min_grade` over inputs (grade law). All money in
integer cents; contributions rounded HALF_UP at the final step only, with a largest-remainder
adjustment so components still sum exactly to each cell's delta in cents.

**Reconciliation invariant:** `Σ_cells Σ_components contribution = Δ(total value)` exactly (in
cents). Violation is a kernel bug, not a data condition — asserted, never surfaced as data.

## 2. `project_lagged_realization` v0 — deterministic cash outlook

**Question it answers:** "will my cash increase next month?" — answered as a *qualified,
DERIVED-grade estimate with explicit drivers*, never a certified forecast (approved design
extension; conclusion policies must label it an estimate).

**Inputs (all deterministic frames at one watermark):**
1. *In-flight inventory:* open submitted claims (SnapshotProbe) with expected amounts, grouped by
   payer and submission-age bucket.
2. *Realization curves:* historical fraction of expected amount that posts within k days of
   submission, per payer, computed from a trailing training window (default 26 weeks) — a plain
   aggregation, not a fitted model.
3. *Pipeline inflow assumption:* trailing submission volume per payer (default 8-week trailing
   mean of weekly expected-amount submitted), extended flat across the projection horizon.

**Method:** expected posted cash in the horizon window `H` =

```
Σ_payers [ Σ_open  E[remaining realization in H | age, payer]
         + Σ_assumed_new_submissions E[realization within H | payer] ]
```

using only the empirical realization curves (step functions over age buckets). A **range** is
produced by recomputing under the trailing window's observed per-payer curve variability
(min/max weekly curve over the training window) — not a confidence interval, and labeled as such.

**Output frame:** per payer (and total): `projected_cash_cents`, `projected_low_cents`,
`projected_high_cents`, `driver_inflight_cents`, `driver_assumed_inflow_cents`, plus the
comparison baseline (current-period posted cash) so "increase vs decrease" is a deterministic
comparison. Grade: DERIVED at best; PROXY if any input is proxy-grade (grade law). The narrative
must present drivers and assumptions ("assumes submission volume holds at trailing mean") — the
conclusion policy enforces the estimate label.

**Honest non-answer:** if the training window lacks coverage (e.g. a payer with < 8 weeks of
remit history covering < 80% of inventory dollars), the operator returns
`INSUFFICIENT_EVIDENCE` details naming the missing coverage instead of extrapolating.

## 3. Versioning

Operators carry `OperatorVersion` (semver). Any change to the math above is a **new version**;
frames record `operator@version` in provenance, so every historical number remains reproducible
under the version that produced it.
