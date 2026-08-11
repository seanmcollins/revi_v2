# Deep research — adversarial methods review (2026-08-11)

Dual-persona review (senior RCM analyst + biostatistician) of deep research at
`daa0a15`, live stack, 11 full research runs. Verdict: **not ship-ready; four
blockers.** A complete fix wave for every finding exists on the branch
`wip/deep-research-methods-fixes` — fixes applied, but the wave was interrupted
during final live verification, so the branch is **probably green, provably
unproven**. Do not merge it without the resume protocol in its commit message.

## Blocking findings

**F1 (P0, METHODS) — the recovery headline is ~4x high and outside its own
interval.** Expected recovery prices the count-based win rate against full
denied dollars; the warehouse's wins return 44¢ on the dollar on average
(dollar-weighted recovery 20.24% vs count rate 42.91%), and 50.8% of the priced
open dollars are past filing deadlines whose own table shows a 3.8% rate.
Published $2,439,841 [CI $2.08M–$2.82M]; defensible ≈ $598K. Fix: price
catchable vs passed dollars at their own decided rates × the observed severity
ratio — all three inputs already computed in `composition.py`.

**F2 (P0, METHODS) — deep research bypasses the platform's own censoring
warnings.** A study published "claim resolution rate fell to 20.4% in July /
0.0% in August" as evidence of a crisis; the same question asked quickly gets
`ADJUDICATION_INCOMPLETE` ("only 26.3% of July has settled… a rate here is
skewed"). Whether a study hedges the artifact is model discretion — four
studies, four different treatments. Also: cohort-maturity censoring applies
only to the PURSUIT basis while the report renders DECIDED, so
`excluded_immature` is always 0 where displayed. Fix: route every reading
through the standard warning evaluation; forbid directional claims grounded on
censoring-caveated points.

**F3 (P1, METHODS) — selection bias, demonstrated and undisclosed.** The rate
conditions on worked-and-decided denials (pursuit↔win Pearson r = 0.968);
unworked inventory skews to the worst classes (41.8% CLINICAL/FINAL vs 23.4%
in the rate-setting set). The planned payer × recovery_class stratification
was silently coarsened to payer-only, so the mitigation never ran and
`unpriced_share: 0` overclaims. Fix: honor planned strata (thin cells refuse
into not_estimable with dollars listed), disclose the conditional transfer.

**F4 (P1, METHODS) — directional claims over fully-overlapping intervals.**
"Getting worse on appeals" rested on a 2-point move between estimates carrying
±15 points, with the series silently truncated at the month that made the
claim possible; a facility "best/weakest" on a 1.1-point spread flips under
another window. Also a deterministic bug: 2-D payer × month grids narrated as
time series. Fix: a deterministic interval guard in the grounding path;
rankings refuse when spread < interval width; grids never narrate as trends.

## Other findings

- **F5 (P1)** Refusal is model discretion, not a gate: unavailable measures
  reach the planner's vocabulary; the patient-satisfaction question got six
  confident readings and refused only after the paid minute. Gate at preview.
- **F6 (P1)** Plan variance ~56% across identical runs, undisclosed; the
  previewed plan is not the executed plan (separate model calls, no id crosses
  the wire); one preview published a false negative ("ar age bucket cannot be
  broken out here") that another run measured fully.
- **F7 (P2)** No cancellation route; abandoning the tab abandons spend.
- **F8 (P2)** Recovery preview is static with tripled copy; `options` empty
  exactly where scoping matters; round budget from keyword bags.
- **F9 (P2)** Benchmark-adjacent plan copy promises a comparison the
  (correctly enforced) wall will never deliver.
- **F10 (P3)** `interval_assumes_independence` is named backwards (summed
  endpoints = perfect correlation, conservative); dollar intervals carry only
  rate variance; the pooled deadline rate is hand-rolled outside statistics;
  the timeliness implication ships unstratified against its own estimator's
  warning; bounded cells leak exact values via population/successes; chase vs
  broaden action published from model prose rather than the gate.

## Verified correct — do not "fix"

Wilson intervals exact (reproduced by hand); the deadline tri-state cannot
double-count (conservation is a raise); filing deadlines use real dates; the
chase gate is deterministic with pack-authored thresholds; the benchmark wall
holds through four independent barriers; recorded walks replay byte-identically;
bounded cells carry `interval: null`; cross-checked figures agree exactly with
quick answers — the certified paths are sound. The defects are in *composition
and disclosure*, not arithmetic.

## The three best moments (protect these)

1. "Research why Halvern is cheating us" → every reading planned *by payer*
   rather than *for Halvern* — the premise refused by construction, without a
   lecture.
2. The payer-behavior study answering the half it could and quantifying why
   the other half is unanswerable ("13 of 144 groups on denial rate by month").
3. The service-line study resolving an apparent clinical effect into two payer
   contracts by noticing the same dollars in both cuts — analysis a set of
   quick questions cannot produce.
