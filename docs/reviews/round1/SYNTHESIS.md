# Revi — Round 1 adversarial review, synthesis

**Artifact:** `/Users/dev/revi_v2` @ `4a9afe6`
**Reviewers:** five hostile personas, run independently — RCM executive, senior RCM analyst,
seed/A healthcare VC, infrastructure founder/CTO, ambition-and-scale lens.
**Method:** each persona drove the running API over real HTTP against the generated DuckDB
warehouse (`REVI_LLM_MOCK=1`), plus targeted source reads. Every cross-persona candidate defect
was then put through an independent **refutation pass** — a separate verifier whose job was to
kill the claim, working from code, live reproduction, and direct SQL rather than from the review
text.

**Verification scorecard:** 10 candidates entered refutation. **9 CONFIRMED, 1
PARTIALLY_CONFIRMED, 0 REFUTED.** No candidate defect died. Seven *sub-claims* inside surviving
findings did die, and those corrections are recorded in §2b — that is where the refutation pass
earned its keep.

---

## 1. Executive summary

### What all five reviewers agree on

**The kernel is real and the product is not.** Every persona independently arrived at the same
shape of verdict: the determinism spine, the typed-refusal surface, the catalog whitelist, the
lineage DAG, and the team's own written postmortems are above the bar for this stage — in two
reviewers' words, "top decile of anything I diligence" and "better than what I get from vendors
ten times this size." And every persona then found that the surfaces a buyer actually touches are
either broken, empty, or publishing wrong numbers at the highest confidence grade the system can
assign.

Four agreements are unanimous or near-unanimous and all four survived refutation:

1. **The system publishes a confidently wrong number on the demo turn.** A custom comparison
   ("compare that to Q1") diffs a 7-day window against a 90-day window with no normalization,
   labels it "vs prior week," grades it `direct` / `high`, and emits no warning. All five personas
   reproduced it; two reproduced it through entirely independent execution paths.
2. **The prioritized worklist — the surface that maps to a purchase order — is dead at the top.**
   6 of 33 cards drill; the first card that opens is rank 17; ranks 1–16 all refuse; ~90% of
   ranked dollars are un-investigable. All five reproduced identical tallies.
3. **There is no authentication and no tenant isolation.** Four personas independently opened a
   session under a foreign tenant with zero credentials and read *and wrote* another tenant's
   session. The verifier reproduced the full read-and-write exploit end to end.
4. **Coverage is the ceiling, not correctness.** 15 of 27 shipped metric contracts can never
   answer, and the dead set is the HFMA MAP Key core — days in A/R, net collection rate, A/R over
   90, DNFB, first-pass yield, initial denial rate, timely-filing at risk.

There is also unanimous agreement on the *cause of the gap*: the hard part (RCM domain content,
governance machinery, deterministic execution) is done to a high standard, and the plumbing that
would put it in front of a user is missing, hardcoded to empty, or never called. Benchmarks
`= ()`. Synonym index defined and never invoked. Presentation rules in the pack that the finding
formatter does not read. Feedback UI wired to an in-memory map. Reconciliation returning `null`.

### Where the reviewers conflict

| Question | Position A | Position B |
|---|---|---|
| **Severity of the 15-of-27 metric gap** | RCM exec and VC file it **HIGH** — bad, fixable, a roadmap item | Ambition lens files it **CRITICAL** and calls the hand-authored semantic layer "59% internally inconsistent at 60 governed objects" — i.e. an authoring-model scaling failure, not a backlog item |
| **Is the deterministic kernel a moat or a governor?** | Tech founder and RCM exec: the kernel is the asset; harden it, add auth, ship content | Ambition lens: it is a moat for correctness and a **governor for coverage** — point the model at authoring the pack under the same governance, or lose on coverage while winning on trust |
| **Cost to fix the worklist** | Exec / VC / tech founder / ambition lens: catalog certification project, L effort | Analyst: **9 of 27 refusals are a YAML repoint** — the `denial_rate` cards measured denied dollars; repointing the metric id makes them answer *today*, including 4 of the top 11 cards. Verified plausible, not yet verified end-to-end |
| **What is the wedge?** | VC: *"explainable, drillable layer over the alerts you already get"* — Revi ships no detection, the portfolio reads a planted feed | Exec: **ingestion + month-end tie-out** to the customer's close is the gate. Ambition lens: **actions + the outcome loop** (appeal packets, work endpoints, `PackDelta`) is the only thing that compounds |
| **Is the self-disclosure trustworthy?** | VC, tech founder, ambition lens: the `NOTES.md` postmortem is a real founder signal | Exec: `net_collection_rate` is unanswerable and **absent from the disclosed gaps list**, and "there is no authentication" appears nowhere in a doc that discloses `ruff format` counts — so the honest-gaps list is itself incomplete, which undermines the thing they were most inclined to trust |
| **Does demo mode prove anything?** | All: the engine claims hold under it | Exec, tech founder, ambition lens: it proves *nothing* about interpretation — the scripted classifier returns `None` for anything off-script, so every refusal test is structurally guaranteed to pass |

One conflict is worth calling out as a **finding in itself**: the exec's complaint that the gaps
list omits authentication, and the analyst's complaint that a defect filed as "catalog work" is
actually a YAML edit, are the same complaint — *the disclosure is honest about what it knows and
mis-sizes what it discloses.* Both directions of mis-sizing (understating a security hole,
overstating a content fix) cost credibility with a buyer.

---

## 2. Ranked confirmed findings

Severity is the synthesized severity across personas (noted where they disagreed). Effort is
S (< 1 day) / M (days) / L (weeks). "Personas" counts independent reproductions.

| # | Sev | Finding | Personas | Verified evidence | Recommended fix | Effort | Area |
|---|---|---|---|---|---|---|---|
| **D1** | CRITICAL | **Custom comparison windows are mislabeled "vs prior week" and published as `direct`/`high` with no window-length warning.** `_period_phrase` (`findings.py:300-310`) special-cases only `ComparisonKind.PRIOR_YEAR`; `CUSTOM` falls through and reads the *current* window's unit. A 7-day window is differenced against a 90-day window unnormalized. | 5/5 | **CONFIRMED.** Two independent live reproductions (HTTP API + internal engine replay) against the real warehouse. `set_comparison` custom Q1 → header correctly `vs 2026-01-01..2026-03-31`, finding text `"Atlas Commercial cash posted down $4,199,421 vs prior week"`, `-94.1%`, `impact_cents=-419942121`, `grade=direct`, `confidence=high`. `warnings` contained only an unrelated suppression note. The one existing test on this turn asserts header fields and never touches `finding.title`/`.statement`. `FindingCard.tsx` renders both verbatim. | (a) Render CUSTOM as the actual resolved range (`vs 2026-01-01..2026-03-31 (90d)`). (b) When comparison length ≠ current length, refuse, normalize to a rate and say so, or emit a hard warning — and never set `impact_cents` on a length-mismatched compare. (c) Add a property test asserting header and finding text agree for **every** `ComparisonKind`. | **S** | investigation/findings |
| **D2** | CRITICAL | **`denial_rate` counts un-adjudicated (OPEN) claims as denials — 49.9% published where true incidence is 5.2%, and the payer ranking inverts at the top.** Numerator is `clean_claim = false`; `clean_claim` is `paid AND NOT denied`, so every OPEN claim reads false. The `status neq OPEN` exclusion was *removed*, not repaired, because `status` is not a catalog dimension. | 2/5 | **PARTIALLY CONFIRMED — core defect fully confirmed, two sub-claims corrected (see §2b).** Live API returned `State Medicaid: Decimal('0.499407')`, `grade=direct`, `confidence=high`; the response's `warnings` array carried only basis and suppression notes — the contract's own population caveat never reaches the payload. DuckDB on the identical population: 3,374 claims, 1,685 not-clean (49.9%), 175 with ≥1 denial (5.2%). All 11,319 OPEN claims warehouse-wide have **zero** denial records. State Medicaid — Revi's #1 worst — is the *best* of the six-payer table under both denominator conventions. | Certify `status` and restore the exclusion (or restrict the denominator to adjudicated claims). **Until then `denial_rate` should refuse rather than answer** — refusal is the product's own stated behavior and the honest one here. Structurally: any contract whose description carries a population caveat must publish that caveat as a warning on every answer. | **M** | pack + catalog |
| **D3** | CRITICAL | **No authentication or authorization on any `/v1` route; `tenant` is an unvalidated client-asserted string.** Session/investigation/lineage lookups key on id alone. Cross-tenant **read and write** both succeed unauthenticated. | 4/5 | **CONFIRMED.** Only middleware registered is CORS; zero `Depends`/auth code in `apps/api/src/revi_api/*.py`. Live OpenAPI: `securitySchemes = None`, all 7 `/v1` routes `security = None`. Both store implementations (Postgres + memory) look up by id with no tenant predicate; `ApiService.submit_turn` hardcodes `tenant="api"`. Live: opened a session as one tenant, then as an unrelated caller with no headers read its full lineage and findings (200) and **POSTed a new turn into it** (200) — investigation count rose 1 → 2. | Put `tenant` in a signed token; authorize every `{session_id}` / `{investigation_id}` lookup against it; scope `/v1/portfolio/latest` (today it takes no tenant at all); make CORS origins env-configurable. Add a "Security posture — not yet built" section at the top of the walkthrough naming authN, authZ, tenant isolation, audit logging, and PHI/BAA scope. | **M** | api/platform |
| **D4** | CRITICAL <br>*(exec/VC: HIGH)* | **15 of 27 metric contracts can never return an answer, and the dead set is the MAP Key core.** `ar_balance`, `ar_over_90_pct`, `days_in_ar`, `net_collection_rate`, `gross_collection_rate`, `dnfb_dollars`, `first_pass_yield`, `initial_denial_rate`, `credit_balance_dollars`, `underpayment_variance`, `avg_days_to_pay`, `bill_lag_days`, `charge_lag_days`, `late_charge_pct`, `timely_filing_at_risk_dollars`. | 3/5 | **CONFIRMED.** Independent sweep of all 27 contracts across `{no dims, payer} × {service, post, submission, remit, discharge, default}` reproduced the failing set **name-for-name**. `denial_rate` additionally fails on its own primary basis (`DATE_BASIS_INVALID: 'remit' not bound for entity 'claim'`), explaining the 11-vs-12 spread across reviewers. Root cause is **two** mechanisms, not one (see §2b): 7-8 failures are uncertified dimensions / unbound REMIT; ~8 are derived measure *fields* (`credit_balance_cents`, `underpayment_cents`, `payment_lag_days`, `charge_entry_lag_days`, `late_charge_cents`, `submission_lag_days`) documented in `NOTES.md` as if implemented but present in only one case in the compiler's allow-list. | Two parallel tracks. **(a) Certify** `status`, `submission_date`, `discharge_date`, `first_pass_paid`; bind REMIT at claim grain or rebind `denial_rate`. **(b) Implement or retire** the derived-measure registry — either land the fields in `measures.yaml` + the compiler, or delete the contracts that depend on them. Then widen `validate_pack_catalog_conformance` from `exclusions:` to `filtered:` and to field resolution, so **a pack that cannot answer refuses to compose**. | **L** | catalog + pack + connector |
| **D5** | CRITICAL | **The worklist's 16 highest-priority cards are all un-drillable; only 6 of 33 answer, first working card is rank 17.** Answerability and priority are near-perfectly anti-correlated. | 5/5 | **CONFIRMED to the dollar and the rank**, two independent ways (in-process ASGI + live uvicorn). 33 cards, ANSWERED 6, ERROR 27, `{UNSUPPORTED_CONCEPT: 18, DATE_BASIS_INVALID: 9}`. Ranks 1-2 = ANM-023/024 `UNSUPPORTED_CONCEPT`; rank 3 = ANM-001 `DATE_BASIS_INVALID`; first answer = rank 17 ANM-004, score 0.06924. Total abs impact $1,749,613.83, answered $180,055.39 = **10.29%**. `PortfolioPanel.tsx` literally does `submit({ spec: item.drillSpec })`, so "as the UI does" is exact. The repo's own test cherry-picks a single drillable card rather than asserting the property across the population. | **Immediate (S):** stop ranking cards the platform cannot investigate — either suppress them or badge them "detected, not yet investigable at this catalog version," and degrade an undrillable card to its detector evidence instead of an error dialog. **Also immediate (S):** repoint the 9 `denial_rate` cards at `denied_dollars` — the analyst verified the repointed drill answers today; that is 9 of 27 refusals and 4 of the top 11 cards for a YAML edit. **Real fix:** D4. | **S** (honest ranking + repoint) / **L** (real coverage) | portfolio/api + pack |
| **D6** | CRITICAL | **Every drill materializes a permanent, randomly-named cohort table in the warehouse, and the TTL sweep is architecturally a no-op.** `cohort_id = f"cohort_{uuid.uuid4().hex[:12]}"` (not content-addressed), `CREATE TABLE` on a read-write connection; the registry the sweep reads is a process-local dict. | 2/5 | **CONFIRMED.** `repository.py:91` registry dict; `repository.py:211-249` random id + `CREATE TABLE`. `drop_expired_cohorts` iterates only `self._cohorts` and is called from exactly one place outside tests — a short-lived `python -m revi_scheduler.sweep` process. The long-lived API app **never calls it at all**. Current warehouse: **214 cohort tables, 11,924,508 rows, 145 MB**; sweep prints `dropped 0 expired cohort table(s)`. Reviewers independently measured 199 → 209 tables and ~141 → ~146 MB across five replays. | Content-address cohort ids (hash definition + watermark) so identical drills reuse one table; persist the registry (or add a `list_cohort_tables` primitive so the sweep can enumerate by naming convention); add `expires_at` and make the sweep authoritative; wire a reclamation task into the API process. **And write down the procurement fact this exposes: Revi requires `CREATE TABLE` on the customer's analytical warehouse** — `architecture.md` documents the read path only. | **M** | connector-duckdb + ops |
| **D7** | HIGH | **Published finding titles/statements contain raw Python `repr`s, floor-divided money, and bare CARC integers with the group code and descriptor stripped.** `findings.py:505` falls back to `f"{value!r}"` for any non-money measure; `findings.py:368-373` interpolates raw cents beside floor-divided dollars in one sentence. | 4/5 | **CONFIRMED**, live against the real warehouse. Reproduced `"Bluestone Mutual / Laboratory: Decimal('1.000000') denials unworked pct"` (structurally identical to the reviewers' `Decimal('0.888889')` example) and, **verbatim**, `"State Medicaid: cash posted moved from 18722151 to 8812843 cents (down $99,093, -52.9% vs prior week)."` The ranked-uncompared path that triggers the repr fallback is the *default* plan for any dimensioned uncompared query, not an edge case. SQL confirms **14 of 20 CARC codes span multiple group codes** — always CO vs PI (CO-16 ≈ 931 denials / $2.03M vs PI-16 ≈ 82 / $132K, merged into one "16" row). CARC 2 is exclusively **PR** (patient responsibility) and appeared as a published top-3 "denial driver." No test asserts on title/statement content anywhere. | Route every published value through the metric contract's unit: `88.9%`, `$187,221.51`. Never `repr()` into user-facing text. Render denial codes as `GROUP / CARC — Title` everywhere (the pack has the titles; the definitional path already returns them) and make the CARC-mix path cut by the **pair**, which `denied_dollars.yaml` and `presentation.yaml` already mandate. Add tests that assert rendered strings, not just cents. | **S** | investigation/findings + presentation |
| **D8** | HIGH | **"Never checked" and "checked and passed" are the same value.** `reconciliation` returns `None` from four silent paths in `submit_turn.py:802-847` plus a fifth at the caller; the wire type is `string \| null` with no third state; the UI banner renders only on failure. Separately, a card's stated impact and its own drill diverge by up to 39% with no reconciliation and no warning. | 3/5 | **CONFIRMED.** Ran the 5-turn reference conversation: T1 `None`, T2 `"status=passed"` (the no-op turn — same plan hash as T1), T3 `None` (the turn that actually drilled 3 payers and pivoted the measure), T4 `None`. Live portfolio drill: **ANM-009 card $25,493.70 vs drill $35,515.30 (+39.31%)**, `reconciliation=None`, only an unrelated suppression warning — DuckDB-traced to the detector scoping by CARC-18 (12 denials) while the drill covers all reason codes in the cell (17 denials). `ReconciliationBanner.tsx:13` is literally `if (result.status !== "failed") return null`. Deeper: `apiDriver.ts`'s `turnResponseToEvents` never reads `response.reconciliation` at all — the banner is wired only to demo data. | Emit `status=not_applicable` with a machine-readable reason instead of `null` (two-line change, converts a hidden gap into a visible one). Run the existing `reconcile` operator across the **card → investigation** seam and publish the result, with the reason for divergence ("detection was scoped to CARC 18; this investigation covers all reason codes in the cell"). When the drill's metric is a rate and the card claimed dollars, say the drill does not confirm the dollar figure. Wire `reconciliation` through `apiDriver.ts`. | **S** (status) / **M** (card seam) | investigation + api + web |
| **D9** | HIGH | **19 sourced, caution-annotated benchmark figures are authored and never reach a user.** `assembly.py:127` passes `benchmarks=()` hardcoded; the only caller of `benchmarks_for_metric` outside tests is nothing. | 2/5 | **CONFIRMED, and structurally worse than claimed.** `assembly.py:127` is a literal empty tuple. `benchmarks_for_metric` has exactly two call sites, both in `packages/pack/tests/`. `FindingPayload` and `TurnAnswer` have no benchmark field; the generated `openapi.json` contains **zero** schema keys matching "benchmark." Going a level deeper than the reviewers: **`TurnOutcome` itself has no `benchmarks` field**, so the engine→presentation handoff never plumbs the data at all — fixing the one hardcoded line would not be enough. `benchmarks.yaml` does contain exactly 19 entries, and the three `denials_unworked_pct` benchmarks cited in the live example genuinely exist. | Plumb benchmarks through `TurnOutcome` → `assembly` → `FindingPayload`/`TurnAnswer` → the OpenAPI contract → a benchmark chip on the finding card. The research is already done, cited, cohort-labelled and caution-annotated; this is the single largest authored-value-left-on-the-floor item in the repo. While there: the prose benchmark ranges embedded in `net_collection_rate.yaml` and `first_pass_yield.yaml` descriptions have no source or `last_verified_at` and should move into the governed file. | **M** | pack → api → web |
| **D10** | HIGH | **Production LLM latency and cost are an order of magnitude worse than demo mode and completely unmanaged:** no per-call timeout, no retry, no circuit breaker, no aggregate per-tenant/day budget, and CI never runs against the live model. | 2/5 | **CONFIRMED.** `spikes/RESULTS.md` verbatim: *"Latency 5–15 s, cost $0.008–$0.021 per pinned refinement call"* (one trial hit 20.5 s / $0.0426). Measured `llm_calls` per turn T1=2, T2-T4=3, T5=1, corroborated by the walkthrough. `config.py:12-14` concedes the cap is *"enforced by the CLI between turns, so treat it as a soft cap"*; grep across `apps/api`, `apps/scheduler`, `packages/investigation` found **no** cumulative spend tracking anywhere. Grep for `timeout\|circuit.?breaker\|retry\|backoff` across the adapter and the LLM application layer: **zero hits**; the installed SDK has only a 60 s subprocess-handshake timeout, no wall-clock query bound. `pyproject.toml:36` deselects `live_llm`; CI runs the default and Postgres suites and never `-m live_llm`. Independently measured demo mode at 9-27 ms warm / 111 ms cold — a ~100-1000× gap, not 10×. | Per-call `asyncio.wait_for` timeout; retry with backoff and a circuit breaker around `SourceUnavailableError`/budget errors; a server-side per-tenant/per-day spend ceiling; prompt caching (grep for `cache_control`/`prompt_cach` returns nothing — the full vocabulary prompt is resent at list price every call); evaluate a Messages-API adapter behind the same port instead of a CLI subprocess per call. Get a `live_llm` job into CI with a published p50/p95 and cost-per-turn. | **M** | adapter-claude + platform |

### 2b. What the refutation pass killed

No candidate was refuted. Seven **sub-claims inside surviving findings** were, and each one is a
place where a persona's write-up over-reached. Carrying the corrected form forward matters:
re-litigating a fixed defect against an overstated number is how a review loses its credibility
with the team it is aimed at.

| Sub-claim as written | Verdict | Corrected form |
|---|---|---|
| D2: *"true denial incidence (adjudicated claims only) of 5.2%"*, overstated **~9.6×** | **Corrected** | 5.2% (175/3,374) is the **same-denominator, corrected-numerator** rate, not adjudicated-only. The literal adjudicated-only rate is **9.39%** (175/1,864). Overstatement is **9.6×** against the same population, **5.3×** against the label as written. Both are severe; use the right one. |
| D2: *"it inverts the payer ranking… and vice versa"* | **Partially refuted** | The inversion is real and severe **at the top** — Revi's worst payer is truly the best — but it is not a mirror swap. Revi's *best*-ranked payer (State Medicaid MCO) ranks 4th-worst truly, and the true worst (Silverline) is already #3 on Revi's list. Correlation is meaningfully negative (Spearman ≈ -0.49), asymmetric, and tracks each payer's share of OPEN claims. Drop "and vice versa." |
| D4: root cause is *"uncertified dimensions or unbound REMIT basis"* | **Corrected** | That explains only **7-8 of 15**. The other ~8 fail on unresolved **derived measure fields** at the contract's entity grain — a mechanically distinct failure mode. `NOTES.md` documents a "derived measure registry" as if implemented; only `open_balance_cents` actually exists in the compiler. Fixing the catalog dimensions alone will not unlock these contracts. |
| D6: *"every drill/refinement"* materializes a table | **Softened** | `PinCohortService.pin` short-circuits when a **single already-pinned referent** is re-drilled at the **same watermark in the same session**. The true leak rate is one table per distinct drill-target-set per session/watermark. Cross-session replay, multi-target drills, and any miss of that narrow cache still leak. Verdict unchanged. |
| D8: ANM-001 card $170,643 vs drill $177,202 (+3.8%) | **Weakened** | ANM-001's own `drill_spec` raises `DATE_BASIS_INVALID` outright; the $177,202 required a manually repointed spec (the reviewer did label it "a repointed drill"). Lead with the clean **ANM-009 +39.31%** case, which reproduces through the card's literal unmodified `drill_spec`. |
| D9: benchmark example *"(industry ~two-thirds)"* | **Refuted as written** | None of the three sourced `denials_unworked_pct` benchmarks yields ~67%; their unworked-share complements are ~88%, >99%, and ~12-15%. "Two-thirds" echoes an unsourced MGMA figure that lives in a `cautions` field, not a governed value. Color commentary, not load-bearing — but do not quote it back. |
| D10: *"15-45 s and $0.024-$0.063 per turn"* | **Scoped** | That is the modal **3-LLM-call refinement turn**, not a floor-to-ceiling bound. T5 (1 call) ≈ 5-15 s / $0.008-0.021; T1 (2 calls) ≈ 10-30 s / $0.016-0.042. Present it as steady-state refinement cost. |
| D3: an attacker who *"knows or guesses"* a session id | **Calibrated** | Guessing is impractical — ids are `uuid4().hex[:12]`, ~48 bits. *Knowing* is trivial (URLs, logs, screenshots, support tickets, a departing employee). The finding's "or" already hedges; keep the emphasis on "knows." |
| D1: *"no warning"* | **Confirmed with nuance** | The `warnings` plumbing **does** exist on `TurnAnswer` and `Investigation` — it was simply never populated with anything about the window mismatch in either reproduction. This is good news for the fix: there is somewhere to put the warning. |

Two things follow from the pattern. First, **the reviews' technical citations were unusually
accurate** — line numbers, error codes, dollar figures and SQL cross-checks reproduced exactly in
almost every case. Second, **the imprecision clusters in the interpretive clauses** — the
multiplier, the "vice versa," the one-sentence root cause, the word "every." That is the seam to
watch when this synthesis is used to write tickets.

---

## 3. Strengths — consensus only

Listed only where **two or more personas independently praised the same thing** after driving it
themselves. Single-persona praise is omitted deliberately.

1. **Determinism is observable, not asserted.** *(VC, tech founder, ambition lens)* Independent
   cold sessions on fresh processes produced byte-identical plan hashes (`5b18277f1caa` T1/T2,
   `d7ed10110c81` T3, `de8a6cb741e4` T4) and identical `impact_cents`, matching
   `data/answer_key.json` to the cent. Cold 97 ms, warm 13 ms.

2. **Typed refusal beats a confident wrong answer, and the refusals are specific.**
   *(RCM exec, VC, tech founder, ambition lens)* `UNSUPPORTED_CONCEPT: typed metric
   'ebitda_margin' is not in the pack`; `DATE_BASIS_INVALID: date basis 'remit' is not bound for
   entity 'claim'`; `QUERY_BUDGET_EXCEEDED: probe 'main' groups an estimated 14400 cells, over the
   5000-cell budget` — refused in 13 ms, before any query. A failed *turn* returns
   `200 {"outcome":"error"}` rather than an HTTP error, which is the right call. Every reviewer
   who tried to make it guess failed to.

3. **The exclusion-polarity postmortem is a genuine founder signal.** *(RCM exec, VC, tech
   founder, ambition lens)* `NOTES.md` documents that all seven `exclusions:` clauses shipped with
   inverted polarity, that six were inert and therefore *hiding* the bug, that the seventh was
   live and publishing 66.13% where the truth was 77.76% (a 24× denominator change), and — the
   part that mattered to reviewers — that the new startup guard **cannot** catch that class.
   Multiple reviewers independently reproduced defects the team had already written down.

4. **The definitional path is correct, differentiated, and shippable today.** *(RCM exec, analyst,
   VC, ambition lens)* `"what is pr3"` → group code `PR` + CARC `3`, with the sentence *"PR-group
   amounts are billing instructions, not denials"* — stamped `base-rcm@1.0.0` with a pack snapshot
   hash and **zero warehouse queries**. The exec's note is the strongest endorsement in the five
   reviews: *"Most RCM tools I've bought would have called PR-3 a denial and inflated my denial
   rate with it."*

5. **The RCM domain content is written by someone fluent, and the catalog is a real PHI control.**
   *(RCM exec, analyst)* `codes.yaml` separates CO / PI / OA / CR correctly; contract descriptions
   volunteer their own approximations rather than pretending to an NPSR they do not have;
   `benchmarks.yaml` carries 19 figures with cohort labels, cautions and real sources (Kodiak,
   Optum, Premier, Health Affairs) as **ranges, not point targets**. 84 of 134 shop-floor terms
   resolved, including `takeback`, `offset`, `DNFB`, `TFL`, `MSP`, `835`, `837`, `270`, `276`.
   Separately, six attempts to reach identifiers through the typed spec (`patient`, `mrn`,
   `claim_id`, `npi`, `rendering_provider`) all refused — nothing in the question path can name an
   uncertified column.

6. **The lineage/trace record is real audit evidence.** *(RCM exec, tech founder, ambition lens)*
   `GET /v1/sessions/{sid}/lineage` returns the full DAG with typed operator edges stamped by
   `turn_id`; the meta turn cites 6 probe hashes with per-probe `cache_hit`, stated purpose,
   operator chain and per-probe grades. The exec's framing: *"If a payer or an auditor asks 'how
   did you get $99,093,' I can answer that from the record."*

7. **`TurnRequest.spec` — the typed first turn — is the right primitive.** *(VC, ambition lens)*
   A portfolio card, a chart click, a saved view and a scheduled brief all collapse into one
   pipeline with `llm_calls: 0`. It is also what makes the whole review reproducible.

8. **The gates are not theater.** *(VC, tech founder, ambition lens)* `633 passed, 16 deselected
   in ~31 s`, reproduced by three reviewers. Strict mypy over 115 files. Import-linter *"6 kept,
   0 broken"* with `include_external_packages = true`, so indirect vendor chains are caught, not
   just direct edges — the tech founder called it "the good version of this pattern."

9. **The engineering-honesty documents outperform the marketing.** *(tech founder, VC, ambition
   lens)* The LLM spike results file measures latency and cost per call, names five traps with
   reproductions, and states that operator *choice* is prompt-steerable but not deterministic —
   which is precisely the risk the reviewers then found unmeasured.

---

## 4. Judgment findings — important, not mechanically verifiable

These were **not** put through refutation. They are argued positions from experienced reviewers,
and they should be read as such: directionally load-bearing, factually unaudited. Each is labeled
with who raised it.

### 4a. Strategy / judgment

| # | Judgment | Raised by | Substance | Counter-consideration |
|---|---|---|---|---|
| J1 | **Competitive research is secondary-source advocacy; the repo contains no market thesis at all** | VC | `industry-research.md` is a useful KPI-canon synthesis but its competitive section states self-assessment as finding (*"Revi is architecturally ahead of the healthcare incumbents"*), makes unfalsifiable claims (*"zero competitors do this"*), and gives Epic bundling conversational analytics into Resolute at zero marginal price — the largest extinction risk — one clause sourced to university support pages. No provider CFO or RCM director is quoted; no design partner, LOI or pilot appears anywhere. `grep` for pricing / TAM / buyer / ACV / go-to-market across `docs/`, `README.md` and the design document returns nothing relevant. | The research doc is genuinely strong on the KPI canon and vocabulary, which is the part the product actually consumes. The absence of a market thesis is a gap in the *repo*, not necessarily in the founder's head — but for a seed/A artifact, an unstated buyer and price point is itself the finding. |
| J2 | **The compounding loop does not exist in any form — there is no data flywheel** | Ambition lens | `revi_pack_learning` is one line: `"""RESERVED SEAT (design-doc Phase 4)"""`. `PackDelta` and `AnalystCorrection` are fully typed with **zero consumers** — no producer, no store, no endpoint, no test. Recoverability, the input to the 0.60-weighted actionability term, is hand-authored constants that nothing observes and updates. There is no outcome capture of any kind: no appeal-filed, appeal-won, dollars-recovered, or analyst-accepted signal anywhere. Session N+1 is exactly as smart as session 1. | Correct as a description of the repo; the disagreement is on sequencing. The exec and VC would both spend the next quarter on ingestion and coverage before the loop, on the grounds that a flywheel with nothing turning it is also a paper. The ambition lens' reply — that nothing else compounds until it exists — is the sharper argument if the goal is a company rather than a tool. |
| J3 | **AI-decorated, not AI-native: hardcoded next-steps, content-free narrative, zero write endpoints** | Ambition lens (with support from VC) | The model's whole surface is 4 prompt templates (132 lines) plus a narrative stream, against 6,504 LOC in `investigation`. The only "next step" ever proposed is `suggested_refinements=(f"drill into {referent_value}",)` — one canned suggestion, every finding, every turn. The scripted narrator is content-free *by design intent* (*"states no free numbers at all and no proper names"*). The OpenAPI has **zero write endpoints**: cards carry `status: "open"` and nothing can change it — no queue, no assignment, no appeal draft, no payer packet, no export. The product's own research file says *"answers must ship with actions… a number without a 'so what / do what' is considered incomplete."* | The strategic claim underneath — that hand-authored semantics caps coverage at YAML throughput, and the model should author the pack under the same governance — is the most interesting bet in the five reviews and the least testable. D4's dual root cause is real evidence for it. The counter is that governed-agent-authored semantics is itself an unsolved research problem, and the near-term ROI of the four catalog dimensions is certain. |
| J4 | **Zero LLM evaluation — demo mode proves the engine and nothing about interpretation** | Tech founder (with support from RCM exec) | `ScriptedLanguageModel.structured` is first-match-wins substring matching; every paraphrase of the flagship question falls off a cliff to `clarification_required` (*"Why did cash collections drop last week?"*, *"cash declined last week, why?"*, *"What drove the drop in cash last week?"*). So both refusal tests in the exec's review — nonsense input and a leading-question fraud allegation — were **structurally guaranteed** to pass and proved nothing. No eval package, no eval directory, no scored corpus; exactly 2 `live_llm` references in the suite, both in one smoke file. The team's own spike already found the failure mode: before a steering sentence was added, *all four* trials compiled "the top two payers" to `AddFilter` instead of `DrillInto` — schema fidelity perfect, operator choice wrong. | Overlaps with confirmed D10 (CI never runs the live model), but the *evaluation* claim goes further and is unverified: nobody has measured what the real model does. Both reviewers converge on the same ask — a 200-500 utterance corpus scored on turn class, metric/dimension ids, **operator choice**, and refusal correctness, with a tracked pass rate and a regression gate. The exec offers to supply the utterances from a real shop, which makes this cheap to start. |

### 4b. Single-persona technical findings, not put through refutation

Raised with code citations but reproduced by only one reviewer. Treat as leads, not as verified
defects. Ordered by the raising reviewer's severity.

| Finding | Persona | One-line substance |
|---|---|---|
| Idempotency is not idempotent, not body-bound, and leaks memory | tech founder | Check-then-act with no reservation: 6 concurrent requests with the same key produced **6 distinct investigations**; a reused key with a different body returns the first response; the dict is process-local and unbounded. |
| A single concurrent reader breaks every cohort turn | tech founder | Cohort materialization opens DuckDB read-write, so any second process holding the file turns drills into `SOURCE_UNAVAILABLE`; the driver cause is deliberately severed with `from None`. No second replica, no sweep while the API is up. |
| `denied_dollars` counts patient responsibility and contractual write-downs as denials | analyst | PR ($872,600) + CO/PI contractual ($1,405,805) = **13.0%** of "denied dollars" is not a denial. `denials_unworked_pct` correctly excludes `PATIENT_RESP`; `denied_dollars` and `denial_rate` do not — two metrics in one pack disagree about what a denial is. |
| `denials_unworked_pct` measures "not appealed" and calls it "never worked" | analyst | 421 of 5,131 "never worked" denials are CO-45 and CO-253 (contractual allowance, sequestration) where filing an appeal would be malpractice; another ~1,171 are duplicates and coding, resolved by correct-and-rebill, which leaves `appeal_status = NONE` forever. |
| Actionability rules skip `unworked_denials`, and the ranking inverts on the decisive pair | exec | ANM-004 (all 14 appeal windows closed 49-94 days ago) publishes a **$31,018 phantom recoverable** and ranks 4 places above ANM-028 (17 claims, 19-53 days of runway). Applying the existing `flag_share` mode reverses them. 16 of 33 cards carry the same default constant, so the 0.60-weighted term contributes nothing to ordering across half the list. |
| The compliance floor is flat, and severity contradicts actionability | exec | An **$824** credit balance and a $48,939 one both clamp to exactly `0.6000`, putting $824 at rank 2 above $170,643. Separately ANM-013 is badged `critical` ($493,266) while the platform's own rationale says the dollars are not recoverable (2%). |
| Small-cell suppression fabricates a 100% concentration claim on the COB demo | exec (analyst concurs on the SQL) | Silverline's true COB share is 153/182 = **84.1%**, not 100% — 29 mismatches across 8 payers are suppressed by a k=11 rule applied to a payer-grain aggregate where no patient can be re-identified. |
| Six of eight advertised entry points dead-end with `options: []` | analyst (VC concurs) | `guideQuestions.ts` claims *"the live API answers them for real"*; 6 of 8 return `clarification_required`, byte-identical to the response for `"purple monkey dishwasher"`. A clarification with no options is a refusal wearing a question mark — and the pack resolves "denial rate" already. |
| The synonym index is dead code and the LLM sees a 160-char blurb | analyst | `dimension_for_synonym` is defined, unit-tested, and never called from production; the prompt shows the model bare labels, so *"break that out by fin class"* has to be guessed. `_DESCRIPTION_CLIP = 160` truncates `denial_rate`'s description one character before the caveat that would have stopped the model choosing it. |
| The UI tells users their feedback was logged; it was discarded | ambition lens | *"Logged. Thanks — recorded against this trace."* → `setFeedback` mutates an in-memory zustand map. No persist, no localStorage, no fetch, no endpoint. Gone on refresh. The one place the product talks to the user about trust. |
| The UI discards the entire ranking rationale | exec | `recoverable_cents_estimate`, `actionability_rationale`, `age_days`, `priority_score`, `compliance_floor_applied` are all on the wire and **none** reach the components. The user sees a numbered list, sorted by nothing visible, with impact dollars in the largest type — and it is not sorted by impact. Indistinguishable from a broken sort. |
| Differentiating UI affordances exist only in the hand-written mock | VC | Default driver is `mock` (1,026 lines of fixtures). `grep -n "metric" apiDriver.ts` → zero; `grep -n "interpretation" apiDriver.ts` → zero. The governed-metric badge and the show-the-interpretation panel never render in API mode. |
| The actionability file sits outside the governance system | ambition lens | The 0.60-weighted rules load straight off disk at `wiring.py:257`, are **not** in `pack_snapshot_id`, cannot be tenant-overlaid or A/B-replayed, and their `content_hash` is computed with **no consumer anywhere**. The most consequential judgement in the product is the one piece of content with no version and no rollback. |
| No rate limiting and no input caps | tech founder | 2 MB utterance accepted; a **50,000-value** `in` predicate bought ~8.8 s of engine work per unauthenticated request; 2,000 chained refinements accepted. The cardinality budget guards group-by cells, not predicate width. |
| Evidence cache has no tenant key, no TTL, no purge path | tech founder | Keyed `(probe_hash, watermark_id, pack_snapshot_id)` in both stores, while the `cohorts` table twelve lines above *does* carry `tenant` and `expires_at`. PHI-derived frames accumulate forever with no deletion path — and the omission is baked into the port signature. |
| No metrics, no traces, no alerts | tech founder | Zero hits for prometheus/otel/statsd/datadog/sentry across every `pyproject.toml`; three stdlib loggers with no structured formatter and no correlation id in the line. `/v1/health` returns ok unconditionally and queries the warehouse on every call, with no liveness/readiness split. |
| 633 tests, zero concurrency tests, zero failure injection | tech founder | One `asyncio.gather`/threading hit across all test files; **zero** `side_effect`/raised-infrastructure-error hits. Empirically: 8 concurrent identical refinements produced a linear chain of 8 nested investigations and 24 referent handles for the same 3 facts. |
| `architecture.md` overstates what import-linter enforces | tech founder | The headline `entrypoints → application → domain` rule has **zero** mechanical enforcement (`type = "layers"` appears 0 times); the pydantic row is enforced for exactly one package. Currently clean, but unguarded. Adding a `layers` contract per capability is a ten-line change. |
| An "answer" with zero findings and nothing to explain it | exec | A fully-suppressed frame returns `outcome: answer`, `findings: 0`, `narrative: null`, with only the static suppression disclaimer — indistinguishable from "nothing to report." The same question over a longer window answers fine, so it looks random. |
| Turn 2 of the reference conversation is a literal no-op | ambition lens (tech founder, VC concur) | Identical plan hash and identical impacts to T1 under new handles, because T1's interpretation already carries `dimension_ids: ["payer"]`. Costs 3 LLM calls, inflates the referent namespace, and is the frame that produced D8's one `status=passed`. |
| Domain-vocabulary gaps | analyst | `place of service` is a synonym for `facility` (POS is a claim-line CMS code, not a facility) — currently inert only because the synonym index is dead. **Zero** RARC definitions are governed, so CO-16 — the largest denial bucket at $2,025,317 — can be named and never explained. 277CA / 999 front-end rejections are absent entirely. `carc16` misses where `carc 16` hits. |
| Scale and ingestion | exec, VC | No 835/837 parser, no X12 handling, no Epic Clarity/Caboodle mapping, no ETL anywhere. Weekly posted cash across the whole mock system is ~$1.5M; a six-hospital system posts 20-40× that. 12 payers, 120,511 claims, 146 MB — a mid-size community hospital does 250-500k claims/year, an IDN 5-20M. The cohort semi-join ceiling is 100k and one reference turn already pins 55,722. |

---

## 5. Recommended next actions

Ranked by value ÷ effort. Eight items, capped deliberately.

| # | Action | Why it ranks here | Effort | Kills |
|---|---|---|---|---|
| **1** | **Fix `_period_phrase` for `ComparisonKind.CUSTOM` and guard unequal window lengths.** Render the resolved range; refuse, normalize, or hard-warn when comparison length ≠ current length; never set `impact_cents` on a length-mismatched compare. Add a property test asserting header and finding text agree for every comparison kind. | Sub-day fix on a CRITICAL that publishes an order-of-magnitude-wrong number at `direct`/`high` on the turn the team demos. Every reviewer independently named it as the meeting-ender. Highest value/effort ratio in the review. | **S** | D1 |
| **2** | **Gate `denial_rate` until its population is right, and publish contract population caveats as warnings.** Refuse rather than answer today; then certify `status` and restore the exclusion. Make "contract description contains a population caveat → the caveat is emitted as a warning on every answer" a structural rule, not a per-metric fix. | Second CRITICAL wrong-number, and the one that *reverses its own conclusion* — the payer flagged worst is truly the best. Refusal is the product's own stated design behavior; using it here costs nothing and is the honest move. The structural rule generalizes to every contract with a caveat. | **S** (gate) / **M** (fix) | D2 |
| **3** | **Unit-aware rendering everywhere: kill `f"{value!r}"`, format ratios as percentages, stop floor-dividing money next to raw cents, and render denial codes as `GROUP / CARC — Title`.** Add tests that assert rendered strings, not just cents. | Half a day. Removes `Decimal('0.888889')` from headline titles, stops merging CO-16 with PI-16 (14 of 20 codes span groups), and stops PR-2 appearing as a top "denial driver." The pack's own `presentation.yaml` already mandates the pair — the engine simply does not read it. Cheapest credibility win available. | **S** | D7 |
| **4** | **Portfolio honesty pass: stop ranking cards the platform cannot investigate, and repoint the 9 `denial_rate` cards at `denied_dollars`.** Badge undrillable cards "detected, not yet investigable at this catalog version" or degrade them to detector evidence instead of an error dialog. Surface `recoverable_cents_estimate`, `actionability_rationale`, `age_days` and `compliance_floor_applied` in the UI — all already on the wire. | Turns a CRITICAL anti-demo into an honest short list without waiting on the catalog project, and the repoint alone plausibly recovers 9 of 27 refusals including 4 of the top 11 cards. Note the analyst/consensus conflict flagged in §1: verify the repoint end-to-end before promising the number. | **S** | D5 (symptom) |
| **5** | **Authentication and tenant scoping.** Signed token carrying `tenant`; authorize every `{session_id}` and `{investigation_id}` lookup; scope `/v1/portfolio/latest`; env-configurable CORS. Then add a "Security posture — not yet built" section to the walkthrough naming authN, authZ, tenant isolation, audit logging and PHI/BAA scope. | Hard blocker on pilot #1 — an unauthenticated API over PHI-adjacent revenue data does not survive any health-system security review. Reviewers were split on effort but unanimous that the *disclosure* gap is the more damaging half: a gaps list that itemizes `ruff format` counts and omits "there is no authentication" reads as written by engineering for engineering. | **M** | D3 |
| **6** | **Reconciliation: emit `not_applicable` with a reason instead of `null`, and reconcile the card → drill seam.** Publish the divergence and its cause; when the drill returns a rate and the card claimed dollars, say the drill does not confirm the figure. Wire `reconciliation` through `apiDriver.ts` — the banner is currently connected only to demo data. | The `not_applicable` half is a two-line change that converts a hidden trust gap into a visible one. The card seam is the most-travelled path in a daily worklist and it is 39% off in silence on a real card. In a product whose thesis is "we never claim more than the evidence supports," silent non-coverage is the same hole one level up. | **S** / **M** | D8 |
| **7** | **Catalog + measure certification sprint.** Certify `status`, `submission_date`, `discharge_date`, `first_pass_paid`; bind REMIT at claim grain or rebind `denial_rate`; **and** implement-or-retire the derived measure registry (`credit_balance_cents`, `underpayment_cents`, `payment_lag_days`, `charge_entry_lag_days`, `late_charge_cents`, `submission_lag_days`). Then widen `validate_pack_catalog_conformance` to field resolution so a pack that cannot answer refuses to compose. | The single root cause under D2, D4 and the real half of D5. It is the difference between a cash-posting tool and a revenue-cycle product — days in A/R, net collection rate, A/R over 90 and DNFB are the numbers on a monthly board slide. Remember the §2b correction: the dimension work alone unlocks only about half; the measure-field work is a separate, equally necessary track. | **L** | D4, D5, D2 |
| **8** | **Fix the cohort write path before any Snowflake conversation.** Content-addressed cohort ids, a durable registry, `expires_at`, a sweep that is authoritative, and reclamation wired into the API process — plus a written answer to *"why does your application need `CREATE TABLE` in my warehouse?"* | Currently 214 orphan tables / 11.9M rows / 145 MB in a development warehouse, with the garbage collector structurally unable to reach any of it. Pointed at a customer's Snowflake this is a storage-credit line item and a security-review conversation on day one, and `architecture.md` documents only the read path. Fix it before the portability claim is tested, not after. | **M** | D6 |

**Just below the line**, in order: wire benchmarks through `TurnOutcome` → API → UI (D9 — the
authored value with the largest gap between "done" and "reachable"); LLM-path guardrails and an
eval corpus (D10 + J4 — timeout, circuit breaker, per-tenant budget, `live_llm` in CI, 200-500
scored utterances with **operator choice** as a graded dimension); and the idempotency reservation
fix, which is small, self-contained, and currently fabricates six investigations from one retry
storm.

**The strategic item nobody should schedule and everybody should decide:** J2/J3 — whether the
next quarter buys coverage (items 4, 5, 7) or buys the loop (outcome capture, `PackDelta`,
write endpoints, model-authored pack content under the same governance). The five reviewers split
on this and none of them is wrong. The refutation pass cannot settle it, because it is the one
question in this review that is not about what the code does.

---

*Synthesized from five independent persona reviews plus a refutation-based verification pass over
10 cross-persona candidates. Verification reproduced findings against the running API, the real
generated warehouse, and direct SQL; no repository files outside `docs/reviews/round1/` were
modified.*
