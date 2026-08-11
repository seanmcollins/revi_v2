# Handoff — how to finish the open work

For the next agent (or human) picking this up. Read `AGENTS.md` first (the
invariants), then `docs/STATUS.md` (where things stand), then this file (what
to do, in order). The owner's standing laws that govern all of it:

- **The experience bar:** "a fleet of FAST and accurate analysts with the
  brains of RCM consultants at your fingertips" — and just as flexible.
- **The completeness bar:** if the semantic layer and data support a path to
  an answer, find it. Only two honest non-answers: no certified path exists
  (refuse naming the data gap), or materially different paths exist (offer
  them, runnable).
- **Honesty outranks everything:** never delete a bound, premise verdict,
  suppression note, or refusal to make an answer nicer. A confidently wrong
  reading is worse than any clarification.
- **No symptom patches:** owner-reported pain points are symptoms; fix the
  class at the design level.
- **Language:** `docs/client-language.md` is binding; both guards (Python +
  web) fail the build on violations. Defaults show their rule, not a name.
- **Verification:** the full bar, serial (DuckDB lock): backend pytest,
  `-m reference`, `-m postgres`, ruff, `lint-imports`, the CI mypy command,
  `make warehouse-diff` (must PASS), web tsc/lint/vitest/build. Commit only
  on green; every commit message states its bar.

## 1. Verify and merge `wip/deep-research-methods-fixes` (FIRST — P0s live here)

The branch carries complete fixes for every finding in
`docs/reviews/deep-research-methods-review.md` (read it in full — it has
file:line attributions, fix shapes, a preserved VERIFIED-CORRECT list, and
three anchor moments whose character must survive). The wave was interrupted
during final live verification: **probably green, provably unproven.**

Protocol: check out the branch → run the full serial bar → run the review's
live repros against :8000/:3000: (a) the F1 repro — the recovery review's
`total_expected_cents / total_open_dollars_cents` must be the defensible
severity- and deadline-aware ratio (~0.10, not 0.42), with the pricing
construction stated in the report; (b) the A/R study no longer publishes the
July censoring artifact as evidence — the reading carries the settling
caveat; (c) "research our patient satisfaction" refuses at PREVIEW, free;
(d) a preview-confirmed run executes the previewed plan (id crosses the
wire); (e) cancel works mid-run; (f) the three anchor moments (Halvern
premise, quantified half-refusal, service-line confounder) retain their
character. Reference values for F1 must be derived from the answer key's
recovery chains — if a reference test was fitted to output instead, fix the
test. Then merge to main and push.

## 2. Polish wave (owner's 95→100 directive; restraint clause binding)

"Don't go overboard — directionally VERY good, just find the tweaks." All
design decisions below are DECIDED (owner-approved); implement, don't
relitigate. Territory: mostly `apps/web`, plus two named backend
composition sites.

- **Thread spine:** every answer leads with a headline figure in the
  pronounced numeral treatment; prior turns compact to question + figure +
  chart thumbnail (tap to expand); repeated context lines consolidate across
  same-scope turns; the current answer is the loud one.
- **Message borders:** a crisp hairline at full border-token strength on
  answer cards and user bubbles (translucent borders dissolve — boundary
  from structure); one radius family; a 5-turn thread should be countable
  at a glance.
- **Contrast/fatigue tokens** (`globals.css`): page background a step warm
  off pure white; primary ink near-black (~12–15:1), muted tier RAISED
  toward readability (quiet comes from size/position, not grayness);
  saturation only on small elements; all pairs above AA.
- **Axis scaling:** bars zero-based always; lines/areas get data-driven
  domains (~10% padding) with a quiet "axis starts at X" note when non-zero;
  percent axes never forced 0–100; verify corpus-wide that data occupies a
  healthy fraction of each line chart's vertical range.
- **Dead-affordance sweep:** click every interactive-styled element on the
  settled build; wire it or strip the costume; add tests for found cases.
- **Entity-color collision reseat:** per-figure deterministic reseat when
  two entities hash to the same slot (Medicaid/Medicare collide today).
- **Study reading titles** through `metric_display.yaml` names (preview card
  + report) instead of humanized ids.
- **Backend composition leaks:** the urgency-position finding statement
  publishes a raw dimension id ("in the catalog's declared order for
  filing_runway_bucket"); warnings_v2 messages carry raw metric ids
  (probe_families_empty). Run metric display over both sites.
- **M45 stranded-opening fallback** prints an ISO range — humanize at the
  shared machinery.
- Also: random-input walk; fix obvious defects; log what was deliberately
  left alone.

## 3. Digestibility pass (behavior-frozen), then push

The owner's words: "a human has to review this shit — clean out all the
overengineered / overly complex shit." Second look at the seven >1,200-line
modules deferred at M30 (interpretation, validation, narrative,
revi_api/service, compile, investigation-contracts/api, planning); first
digestibility review of everything since (deep_research/*, statistics,
monitors, wave shims). Hunt: single-caller abstractions, unused
configurability, branches the types forbid, narrating comments, opaque
tests. Refresh `docs/code-tour.md` to the final shape. Invariants: pure
motion/deletion, collected-test counts equal before and after, full bar
green. Then push.

## 4. The terminal gate

A Fable-model (or strongest-available) fresh-eyes judgment of the whole
product against the experience bar, as a first-time career RCM analyst:
cold Home walk, the four hero questions live, six unscripted questions,
multi-turn threads (window inheritance, anaphora, corrections), a deep
research study from the composer end to end, monitors, exports, permalinks,
console cleanliness. The demo-curation command (`make demo-curate
ARGS="--clean-rail"`) runs last. Verdict format: ship-ready yes/no with a
blocking list; anything found feeds one more fix wave.

## 5. Afterward (not blocking)

- Chat-turn agentic mode A/B per `docs/agentic-resolution.md` §promotion —
  the eval corpus design is described in the flexibility-wave task notes;
  zero wrong-reading regressions tolerated.
- The work-app mount: `docs/vite-host-migration.md` (host glue only).
- Design questions parked: monitorable research determinations; worklist
  cards carrying a real investigation id (lifecycle design, not a field).
