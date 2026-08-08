# Round 1 — adversarial review

Five hostile reviewer personas drove Revi independently at commit `4a9afe6`, each against the
running API over real HTTP (`REVI_LLM_MOCK=1`) with the generated DuckDB warehouse, plus targeted
source reads. Their findings were then put through an independent refutation pass and synthesized.

**Read `SYNTHESIS.md` first.** The five persona reports are the primary evidence behind it.

## Index

| File | Lens | Read it for |
|---|---|---|
| **[SYNTHESIS.md](SYNTHESIS.md)** | — | **Start here.** Executive summary, ranked confirmed-defect table with verified evidence and fixes, the sub-claims that died under refutation, consensus strengths, judgment findings, and an 8-item next-actions shortlist. |
| [rcm-exec.md](rcm-exec.md) | VP Revenue Cycle, 6-hospital system on Epic Resolute, 22 years | Would a buyer sign, and what blocks analysts from touching it. Strongest on the metric-coverage gap read as a board slide, portfolio ranking versus appeal runway, suppression fabricating a 100% concentration claim, and the missing ingestion / month-end tie-out. |
| [rcm-analyst.md](rcm-analyst.md) | Senior RCM analyst, 15 years in workqueues and 835s | Domain correctness at the record level. Strongest on the `denial_rate` population defect with payer-by-payer ground truth, CARC/group-code semantics, what actually counts as a denial, and the argument that 9 refusing cards are a YAML repoint rather than catalog work. |
| [vc-investor.md](vc-investor.md) | Healthcare / vertical-AI seed-A partner | Investability. Strongest on the coverage-versus-moat argument, the analytics-to-outcomes gap, competitive-research quality, unit economics, and what the demo proves versus what production requires. |
| [tech-founder.md](tech-founder.md) | 2× infrastructure founder / CTO | Architecture and operational readiness. Strongest on the cohort write-leak, the DuckDB single-writer bottleneck, idempotency, LLM-path guardrails, evaluation absence, observability, rate limiting, and what import-linter does and does not enforce. |
| [ambition-scale.md](ambition-scale.md) | Ambition and scale | Whether this compounds. Strongest on the absent flywheel, the "AI-decorated not AI-native" argument, reconciliation non-coverage, the actionability file sitting outside governance, and a 90-day plan. |

## Method note

Cross-persona candidate defects were verified by a refutation pass whose brief was to kill each
claim using code reading, live reproduction against the real warehouse, and direct SQL — not to
confirm it. Ten candidates entered: nine confirmed, one partially confirmed, none refuted. Seven
sub-claims inside surviving findings were corrected; those corrections are recorded in
`SYNTHESIS.md` §2b and should be carried forward instead of the original phrasing.

Findings raised by a single persona were not put through refutation and are listed separately in
`SYNTHESIS.md` §4b as leads. Strategy and judgment claims are labeled as such in §4a — they are
argued positions, not verified facts.
