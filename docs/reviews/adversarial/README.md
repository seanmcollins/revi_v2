# Adversarial review — the record

Ten rounds of persona-based adversarial review ran against this product between
2026-08-08 and 2026-08-10, using simulated reviewer personas (RCM executive,
RCM analyst, VC investor, a competitor product exec, a UI/UX principal, and a
principal product designer — a fresh-eyes reviewer joined late). Each round
enumerated surfaces, probed live, filed findings with repro steps, and issued
buy/champion verdicts; each round's confirmed findings drove a fix wave before
the next round ran. The raw round transcripts were working artifacts (large
machine-serialized agent output) and are not retained in the repository; the
round-1 human-readable reviews are kept in full under
[`../round1/`](../round1/), and this file summarizes the arc.

**These reviewers were AI personas, not customers.** The verdicts below are a
rehearsal discipline — a way to force defects into the open before a human
ever saw the product — not market evidence.

## The arc

| Round | Focus | Outcome |
|---|---|---|
| 1 | Full-surface enumeration by six personas | 40+ findings; the honesty machinery held, the presentation did not — warning walls, internal ids on default surfaces, chart defects |
| 2 | Fix verification + deeper analytics probing | Comparison honesty and denial-rate composition rebuilt; auth/tenant isolation hardened |
| 3 | Off-script probing; the competitor lens at its sharpest | Bounds-as-data machinery landed (≤ rendering, ranking refusals); answer-surface voice rebuilt |
| 4 | Premise verification | The four-arm premise verdict machinery (confirmed / partial / false / unverifiable) proved out live |
| 5 | Narrative integrity | Grounding validation, sentence dedup, clarification-resume context |
| 6 | Window maturity + adjudication completeness | Immature-window guards; probe-window disclosure |
| 7 | The proactive surface (now Monitors) | Watch-cell identity defects found and fixed; materiality gating proved |
| 8 | Buyer pressure | VC signed; monitor lifecycle with data-verified resolution held under probing |
| 9 | Contract-language pressure | Exec signed an annual ("I am not re-trading a condition I stated in writing"); analyst signed 3-year at list |
| 10 | Demo-readiness | UI/UX verdict: customer-demo-ready ("I would champion it"); two final P0s filed and fixed in wave H |

A separate Fable-model acceptance gate then re-verified both round-10 P0s dead,
ran the four hero questions and an off-script battery live, and filed a final
blocking list (drill-context inheritance, a prose-corruption edge, clarification
register) that drove the last fix wave before publication.

## What survived into the product

The durable output of the loop is machinery, not verdicts: bounds render as
bounds everywhere a number renders; premises are verified before being
answered; rankings refuse when the field is mostly ceilings; windows disclose
their maturity; the warning taxonomy is coded and translated for humans; tiles
and briefs are provably consistent; and the warehouse-diff harness rederives
every published value by an independent path in CI. Where a specific finding
mattered, the regression test that pins it cites the defect in its own words —
the tests are the durable citation, not these transcripts.
