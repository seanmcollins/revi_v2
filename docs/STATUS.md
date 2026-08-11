# Status — where the build stands (2026-08-11, late morning)

**Update:** the methods-integrity fix wave is verified and MERGED (M57).
The recovery review's headline is now honest — severity- and deadline-aware,
its construction stated on the report — and is safe to demo. Old stored
research reports open again; half-refusals render as notes; planner
failures own themselves. The known-issues warning below is retired.

`main` is green through M57 (was M56: every commit on it passed the full serial bar
(backend 4,485 / reference 441 / postgres 51, ruff, 8 import contracts, CI
mypy, warehouse-diff at 10,719 independently rederived values with zero
divergences, both client-language guards, web 1,527 tests + build).

## What works, end to end, on main

- **Home** is the only landing surface: key-figure band (still-catchable
  dollars leading), the top lead's real chart, monitor tiles that expand into
  their own detail (history mini-chart, sensitivity with the recommended level
  stated as a number, stop-monitoring, "Ask about this"), the brief in place,
  lead lifecycle. `/monitors` and `/rounds` redirect here.
- **Conversation** answers in the question's shape (verdicts on yes/no, totals
  first on how-much), inherits the thread's window and scope with one-clause
  disclosures (79/100 audit conversations right, up from 52), scorecards
  answer "top performing payer" with an honest panel verdict, clarifications
  are quiet option cards, benchmarks answer zero-probe.
- **Charts**: thick banded bars, stable entity colors, horizontal rankings,
  view-as (bar/line/area/donut/table with honesty gates), fullscreen,
  collapsible side panes feeding freed width to figures.
- **Deep research**: composer control → model-planned preview (discovery path
  choices + consulted RCM knowledge + reasoned readings) → threshold-gated
  iterative runs → determination reports with the walk shown. Recovery review
  with Wilson intervals and evidence tiers.

## Known issues — read before demoing

`docs/reviews/deep-research-methods-review.md` documents the review; every
finding in it is now FIXED and verified on main (merge bad2dd3 + M57).
The whole product is safe to demo, including the recovery headline.

## Remaining program (docs/HANDOFF.md holds the detail)

1. Polish wave (accumulated brief: thread spine + message borders + headline
   figures per answer, contrast/fatigue tokens, axis-scaling rules, dead-link
   sweep, color-collision reseat, study reading titles through metric display,
   raw-id composition leaks).
3. Digestibility pass (behavior-frozen de-overengineering; second look at the
   seven >1,200-line modules; docs/code-tour.md refresh), then push.
4. Terminal gate: full customer-morning simulation judged against the
   experience bar — "a fleet of fast, accurate, flexible analysts with the
   brains of RCM consultants at your fingertips."
5. Chat-turn agentic mode A/B (docs/agentic-resolution.md; the corpus at the
   flexibility audit is the eval set); work-machine mount for the host app.
