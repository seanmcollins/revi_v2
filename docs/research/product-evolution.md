# Revi — product evolution

**Written** 2026-08-08. **Inputs:** four market/GTM research sweeps (competitive pricing & design-partner
motion; EHR/warehouse integration reality; agentic follow-through and the appeal artifact; wedge, moat and
platform strategy), plus this repo's own `docs/research/industry-research.md`,
`docs/reviews/round1/SYNTHESIS.md` (10 candidates: 9 CONFIRMED, 1 PARTIALLY_CONFIRMED, 0 REFUTED), and
`docs/reviews/prototype-comparison.md`.

**Status of this document.** It is a set of decisions, not a survey. Where the sweeps disagreed, a position
is taken and the losing option is named. Where a recommendation depends on a defect in the round-1 review,
the finding id is cited so the roadmap and the ticket queue stay the same list.

---

## 1. Thesis

> **Revi is the defensible-number layer for revenue-cycle money: it sits between somebody else's detector
> and a health system's audit obligation, and it sells the reconciled explanation and the record behind it.**

Three things follow from that sentence, and each is a refusal as much as a claim.

**It is not a detector.** Detection has been bought, not invented. Waystar acquired Iodine Software for
$1.25B (announced 2025-07-23, closed 2025-10-01, framed as "+15% TAM"); prediction now ships inside the
clearinghouse (AltitudePredict), inside the EHR, and inside four funded startups (Anomaly, Adonis,
Experian, Sift — see `industry-research.md` §4). Shipping a new standalone detector in 2026 is a commodity
with a distribution problem. Revi's prebuilt anomaly system is worth far more as a demo-data engine, an
eval corpus generator, and the reference implementation of a `DetectorPort` than as the differentiator.

**It is not a dashboard, and "governed semantics" is not the moat.** Every semantic-layer-as-product bet of
the last four years became a platform primitive: dbt's metrics layer was deprecated and rebuilt as
MetricFlow after the Transform acquisition (dbt Labs / Fivetran merged June 2026); Google *opened* the
Looker semantic layer in Aug 2024 with an Open SQL Interface, a JDBC driver and native connectors to Power
BI, Tableau, ThoughtSpot and Sheets; Snowflake made the semantic model a schema-level database object
(semantic views → Cortex Analyst); Databricks did the same as Unity Catalog metric views → Genie. The
*measured* value of modelling is real — BIRD-SQL goes 57% → 78% with a semantic model against a 93% human
baseline; dbt's 2026 benchmark is 64.5% raw vs 72.7% modelled — but that value accrues to whoever owns the
**content**, not whoever owns the layer. Revi's durable assets are the RCM contract corpus (five date bases
bound per entity, group-code+CARC pair keying, initial-vs-final denominators with rebill exclusions, payer
filing rules with authority tiers, 19 cohort-labelled benchmarks with cautions) and the **audit record**
(per-probe lineage hashes, pack snapshot pinning, contract fingerprints under the no-silent-redefinition
rule, two-axis evidence grades, typed refusal). Those took domain fluency to author and do not commoditize
on a two-year clock.

**It is not an autonomous agent, and it is never the signatory.** California SB 1120 has already set the
regulatory grammar on the payer side — AI may inform, a licensed human decides, AI "does not supplant health
care provider decisionmaking," and a determination may not be based "solely on a group dataset." Every
compliance reviewer will apply the mirror to provider-side automation whether or not a statute yet compels
it. The defensible posture is: Revi produces an exhibit and a chain of custody; a named human at the
provider attaches it to their own certification and files it.

**Why this is the right shape of company right now.** Bain's 2025 data (via HFMA) puts AI adoption in
denials management at ~20%, against 64% for ambient documentation, 43% CDI, 30% coding — while
FinThrive/Hanover (n=100 hospital finance and RC leaders, Jul–Aug 2025) puts denials and underpayments at
67% as a stated AI deployment target, second only to prior auth at 73%. Demand 67%, adoption 20%. The
stated barrier is trust and skepticism that AI understands payer-specific rules (Experian State of Claims
2025). **That is not a detection gap. It is an explanation gap, and it is Revi-shaped.** The round-1 review
independently confirmed Revi already has the rare half: the RCM executive's line — *"If a payer or an
auditor asks 'how did you get $99,093,' I can answer that from the record"* — and *"Most RCM tools I've
bought would have called PR-3 a denial and inflated my denial rate with it."*

**The consolidation headwind resolves in this thesis's favour, but only in this framing.** FinThrive/Hanover:
70% expect to *reduce* reliance on third-party RCM vendors; 60% plan to consolidate RCM vendors within three
years. KLAS 2026 RCM Suites: the top consolidation drivers are vendor consolidation (57%) and stronger
partnership (57%), and deep adopters got efficiency but "rarely achieved actual cost reductions." A product
that *replaces* a vendor is what a consolidating buyer cuts. A product that makes the vendors they are
keeping drillable, reconciled and auditable is additive to the consolidation decision rather than a casualty
of it. This is the single most important sentence in the GTM narrative and it should appear on the first
slide.

**Honest counterweight, stated up front.** The round-1 review is unanimous that the kernel is real and the
product is not: 6 of 33 worklist cards drill, the first working card is rank 17, ~90% of ranked dollars are
un-investigable (D5); 15 of 27 metric contracts can never answer and the dead set is the HFMA MAP Key core
(D4); there is no authentication and cross-tenant read *and write* were reproduced live (D3); the demo turn
publishes an order-of-magnitude-wrong number at `direct`/`high` (D1). None of the strategy below is
purchasable until §3's *now* row is done. A thesis is not a mitigation.

---

## 2. The wedge decision

Five candidates were on the table across the sweeps and the review. Ranked, with the evidence that ranks
them.

| Rank | Candidate wedge | Verdict |
|---|---|---|
| **1** | **"Defend the number" — reconciled explanation + evidence packet over denials & underpayments, on alerts the customer already receives** | **Chosen** |
| 2 | Governed conversational analyst (free-form multi-turn investigation) | Expansion, not the land |
| 3 | Definitional + benchmark knowledge base | Free top-of-funnel, never the ACV wedge |
| 4 | Detection / anomaly scoring | Do not build — expose a `DetectorPort` |
| 5 | Appeal-letter generation on medical necessity and DRG downgrades | Concede explicitly |

**Why 2 is not the land.** Free-form conversational analytics is the hardest thing to prove in a pilot and
the most directly contested by a bundled incumbent. `industry-research.md` §4 asserts Epic offers
"SlicerDicer/Reporting Workbench self-service; **no NL**." That is now false: Epic's Healthcare Intelligence
page ships **SideKick**, "the embedded AI assistant, [that] creates reports for them based on their
plain-language questions," alongside **Pulse** peer benchmarking, and Epic's own customer SIG (Sept 24–25
2025) ran sessions on NL-to-SQL agents for Clarity/Caboodle and SlicerDicer SideKick self-service reporting.
Epic also announced **Penny** at UGM 2025 (coding suggestions plus denial appeal-letter generation; reported
GA Nov 2026 covering autonomous ED/Radiology coding, outpatient denial appeals and automated claims
follow-up). The VC persona flagged Epic bundling as the largest extinction risk and noted it was sourced to
a university support page (J1). It is now a shipped, vendor-documented feature. Correcting the doc is
recommendation N1 in §3 — but the strategic consequence is here: **do not land on the surface Epic is
bundling at zero marginal price.** Land on what Epic structurally will not do — reconcile across a non-Epic
source, defend a number to a payer or an auditor with lineage, and serve the mixed estates that M&A creates.
Note the honest nuance: Epic's Pulse benchmarking is documented over cost reduction, provider efficiency and
patient throughput — *not* the HFMA MAP Key / Kodiak denial canon. RCM benchmark depth is genuinely open
ground.

**Why 3 is not the wedge.** The definitional path is the most-praised surface in the entire adversarial
review (four of five personas, independently) and it requires no warehouse, no ingestion, no PHI and no auth
exposure. It is also something nobody signs a purchase order for. Ship it free (§6).

**Why 4 is a refusal.** Building detection means competing on precision claims buyers cannot verify, against
a $1.25B acquisition and an EHR vendor — and Revi is philosophically committed *not* to make ML scoring
claims (`industry-research.md` §5 lists predictive denial-risk scoring as deliberately out of scope). The
`DetectorPort` inverts this from a weakness into the integration story: *Revi does not add a vendor; it makes
the ones you kept auditable.* That is the only viable posture against a bundled incumbent, and it turns the
70%-reduce-vendors statistic into the pitch.

**Why 5 is a concession, said out loud.** Every credible appeal-generation product ships from a *clinical*
evidence base. SmarterDx reads "the complete patient record, including provider notes, labs, meds, orders,
vitals" (36% average overturn on medical-necessity and DRG-downgrade denials, $4.6K per appeal, 57K+ denials
processed, 85+ health systems). Ensemble EIQ sells "clinically calibrated generative AI and clinical
oversight" with a "100% clinical review success rate." Revi's warehouse has five entities — claim,
claim_line, transaction, remit, denial — and no chart. It cannot compete there and should say so. Refusing
an entire appeal class it cannot evidence is the same move as typed refusal, one level up, and it is on
brand with the product's best-reviewed behaviour.

### The recommendation

> **Land on one denial/underpayment family at one facility: take the alerts the customer's existing detector
> already produces, explain them with a reconciled, drillable investigation, and deliver the result as an
> Evidence Packet whose every figure carries a probe hash back to a source remit.**

Four properties make this the right wedge, and each is checkable:

1. **It is the appeal class where the evidence is arithmetic.** Underpayment/contract variance, COB,
   timely-filing rebuttal, duplicate and downcoding denials are won on reconciled numbers, not on chart
   narrative — and the pack already ships contracts for all five (`underpayment_variance`,
   `cob_mismatch_claims`/`cob_mismatch_rate`, `timely_filing_at_risk_dollars`, `denied_dollars` at CARC-pair
   grain).
2. **The differentiated artifact is the exhibit, not the prose.** Letter generation is commoditized (Waystar
   ships 1,000+ prepopulated payer-specific forms, autonomous generation, proof of delivery, "90% reduction
   in time to create 100 appeal packages"). Nobody ships a *financial exhibit* where expected vs allowed vs
   paid at line grain, the group-code+CARC pair with its governed title, the contract-variance derivation and
   the filing-clock computation with its authority tier are each individually traceable. Premier's 2025
   survey (280 hospitals, 23 states, 48,000+ beds) prices the pain precisely: appealing a claim cost **$57.23**
   per claim in 2023, up from $43.84 in 2022, and **70% of denials were ultimately overturned**. The cost is
   in assembling the exhibit, not in deciding to appeal. The packet slots under anyone's letter — the health
   system's template, Waystar's payer-specific form, or a human's.
3. **It matches what is certifiable today, not the aspirational catalog.** `industry-research.md` §4 rates
   denial analytics as Revi's most differentiated surface, and D5's repoint (9 `denial_rate` cards →
   `denied_dollars`) plausibly recovers 9 of 27 refusals including 4 of the top 11 cards for a YAML edit.
   Scope the wedge to the denial/payer-scorecard family and you are selling what already answers.
4. **It is the cheapest path to the compounding loop.** An action produces an outcome record as a byproduct
   (§5). Nothing else on the candidate list does.

**What "land" means concretely for the first pilot:** one facility, one denial family (COB or underpayment
variance), a customer-supplied alert feed through the `DetectorPort`, a Clarity/Caboodle read-only extract,
a 90-day proof window, and a packet a named human at the provider signs and files.

---

## 3. Roadmap by horizon

Ranked within each horizon by value ÷ effort. Effort is S (< 1 day) / M (days) / L (weeks). Items merged and
deduplicated across the four sweeps; port-back moves from `prototype-comparison.md` §2 are slotted where they
belong rather than listed separately, and are marked **[PB-n]**.

### NOW — the next 90 days

Nothing in the 6mo or 12mo+ rows is purchasable until this row is done. The order below is the order.

| # | Action | Rationale | Evidence | Effort |
|---|---|---|---|---|
| **N1** | **Credibility floor: fix `_period_phrase` for `ComparisonKind.CUSTOM`; refuse or hard-warn on length-mismatched comparison windows and never set `impact_cents` on one; gate `denial_rate` until its population is right; kill `f"{value!r}"` and floor-divided money; render denial codes as `GROUP / CARC — Title`; emit `reconciliation=not_applicable` with a reason instead of `null`.** | Revi cannot demo today without publishing a wrong number at the highest confidence grade it can assign. Every one of these is sub-day and each removes a meeting-ender. The `denial_rate` gate is the sharpest: the payer it flags worst is truly the best. | SYNTHESIS D1 (5/5 personas; `impact_cents=-419942121`, `grade=direct`, labelled "vs prior week" while differencing 7d against 90d), D2 (49.9% published, 5.2% same-denominator / 9.39% adjudicated-only truth; Spearman ≈ −0.49), D7 (verbatim `"cash posted moved from 18722151 to 8812843 cents"`; 14 of 20 CARCs span group codes; PR-2 published as a "denial driver"), D8. | **S** |
| **N2** | **Authentication and tenant scoping, plus the honest "Security posture — not yet built" section.** Signed token carrying `tenant`; authorize every `{session_id}` and `{investigation_id}` lookup; scope `/v1/portfolio/latest`; env-configurable CORS. | Hard gate on pilot #1. Hospital procurement routes non-clinical software through IT/security review even when it skips the clinical Value Analysis Committee, and health-system sales cycles run 6–18 months (Prospeo: ~14.7-month average, ~22 decision-makers). GTM cannot outrun this: any real design-partner conversation stalls here. The *disclosure* gap is the more damaging half — a gaps list itemizing `ruff format` counts that omits "there is no authentication" reads as written by engineering for engineering. | SYNTHESIS D3 (4/5 personas; cross-tenant read **and write** reproduced live; `securitySchemes = None` on all 7 routes; `ApiService.submit_turn` hardcodes `tenant="api"`). Healthcare sales-cycle sourcing: healthcaresalesmasterclass.com, prospeo.io, healthcare.digital. | **M** |
| **N3** | **Portfolio honesty pass + the `denial_rate` → `denied_dollars` repoint.** Stop ranking cards the platform cannot investigate; badge them "detected, not yet investigable at this catalog version" or degrade to detector evidence; surface `recoverable_cents_estimate`, `actionability_rationale`, `age_days`, `compliance_floor_applied` — all already on the wire. Make expired filing windows **zero** the recoverable estimate rather than discount it. | The worklist is the surface that maps to a purchase order and it is dead at the top. The repoint is a YAML edit. The filing-window fix kills a specific published lie: ANM-004 publishes a $31,018 recoverable on 14 claims whose appeal windows closed 49–94 days ago and ranks four places above a card with 17 claims and 19–53 days of runway. Verify the repoint end-to-end before promising the number. | SYNTHESIS D5 (33 cards, 6 answered, first answer at rank 17, $180,055 of $1,749,613 = 10.29%), §4b (exec: phantom recoverable; 16 of 33 cards carry the same default constant so the 0.60-weighted term contributes nothing across half the list). | **S** |
| **N4** | **Cohort write path, before any Snowflake or security conversation.** Content-addressed cohort ids, durable registry, `expires_at`, an authoritative sweep, reclamation wired into the API process — plus a written answer to *"why does your application need `CREATE TABLE` in my warehouse?"* | 214 orphan tables / 11.9M rows / 145 MB in a *development* warehouse with a garbage collector architecturally unable to reach any of it. Inside a customer's own Snowflake account this stops being an ops annoyance and becomes their storage-credit line item and a same-day audit finding. `architecture.md` documents only the read path — this is an undisclosed procurement fact. | SYNTHESIS D6 (CONFIRMED; `repository.py:91` process-local registry, `:211-249` random id + `CREATE TABLE`; sweep prints `dropped 0`). | **M** |
| **N5** | **Plumb the 19 benchmarks through `TurnOutcome` → `assembly` → `FindingPayload`/`TurnAnswer` → OpenAPI → a benchmark chip — and ship them simultaneously as a free public report/calculator.** | The single largest authored-value-left-on-the-floor item in the repo, and the correctness fix doubles as the cheapest GTM asset available. Publishing cited denial/underpayment benchmarks is the proven top-of-funnel mechanism in this market (Kodiak and Optum denial indices; Waystar's "$15.5B denials prevented"; FinThrive's "32% of claims underpaid"). This is not an additional project. | SYNTHESIS D9 (`assembly.py:127` passes `benchmarks=()`; `benchmarks_for_metric` has two call sites, both tests; generated `openapi.json` contains zero "benchmark" keys; `TurnOutcome` has no benchmark field, so the one-line fix is insufficient). `industry-research.md` §4. | **M** |
| **N6** | **Action API with named approval and an immutable audit record — `POST /v1/actions` (packet \| export \| assignment), `POST /v1/actions/{id}/approve` requiring a named human, `GET /v1/actions/{id}` append-only — carrying investigation id, pack snapshot hash and watermark.** Fix idempotency (reservation, not check-then-act) in the same change. | Every action, packet and outcome recommendation is blocked on this, and the *shape* of the write surface is the compliance conversation. Two known defects become money-losing rather than annoying on a write endpoint: check-then-act idempotency produced **6 distinct investigations from 6 concurrent identical requests** (six appeals), and no auth means a foreign caller can file on your behalf. | SYNTHESIS J3 ("The OpenAPI has zero write endpoints: cards carry `status: \"open\"` and nothing can change it"), §4b (idempotency), D3. Product's own research: *"answers must ship with actions… a number without a 'so what / do what' is considered incomplete"* (`industry-research.md` §1.5). | **M** |
| **N7** | **The Evidence Packet, with "provenance-complete or refuse" as a hard contract.** Expected vs allowed vs paid at line grain; group-code+CARC pair with governed title; contract-variance derivation; filing-clock computation with its authority tier; per-figure probe hash. A sentence whose figure cannot be reconciled is **deleted, not softened**, and the packet declares which sections were withheld and why. | This is the wedge's deliverable. The mechanism already exists — the narrative validator redacts sentences citing referents the turn does not own; extend it to documents. The failure mode is already demonstrated in narration and would be catastrophic in a payer-facing document: a `Decimal('0.888889')` or a merged CO-16/PI-16 row inside an appeal exhibit is a self-inflicted credibility loss and, if inflated, an overstated claim. | SYNTHESIS D1, D7, D8 (ANM-009 card $25,493.70 vs its own drill $35,515.30, +39.31%, `reconciliation=None`), strength #6 (lineage DAG with per-probe hashes, cache-hit flags, stated purpose, per-probe grades). Premier 2025: $57.23/appeal, 70% overturned. | **M** |
| **N8** | **`DetectorPort`: make "bring your own alerts" the integration story, with the prebuilt anomaly system as the reference implementation.** | Turns the 70%-reduce-vendors headwind into the pitch and makes Epic Penny and Waystar Altitude complements rather than competitors. It is close to free — the portfolio already reads a planted feed, so the port formalizes current architecture rather than rewriting it. | Waystar/Iodine $1.25B; Epic Penny (UGM 2025, reported Nov 2026); FinThrive/Hanover 70%/60%. SYNTHESIS: the portfolio "reads a planted feed" and Revi ships no detection today. | **M** |
| **N9** | **Governed export + typed deep link, instead of an EHR write-back promise.** (a) A ranked account export carrying metric id, date basis, filters, recoverable estimate, actionability rationale and pack snapshot, shaped for the customer's own workqueue loader; (b) `TurnRequest.spec` exposed as a deep link so a worklist row opens the reconciled investigation that produced it. | Billing/claims write-back is the most gated capability on every target platform. Epic: financial resources are read-only (Account (Premium Billing), ExplanationOfBenefit, Coverage, Claim for prior auth); create/update operations are clinical; **there is no FHIR resource for a billing workqueue at all**. athenahealth: "most write operations require MDP partnership tier and explicit scope approval," with no billing writeback documented even there. eClinicalWorks: every write scope is contract-gated and none cover billing/claims or task/workqueue. Promising "we push into your Epic WQ" is a claim Revi cannot keep on public rails. One sourced upgrade path exists if a customer wants the packet in the chart: `DocumentReference` is create-capable on Epic's FHIR API. | fhir.epic.com "Summary of Resources"; mirth.support (athenahealth); explorer.usecobalt.com/ehr/eclinicalworks. `prototype-comparison.md` §1.2 (`TurnRequest.spec`, typed first turn, `llm_calls: 0`). | **M** |
| **N10** | **Clarity/Caboodle read-only extract adapter.** | Revi has **no ingestion of any kind**, and the RCM-executive persona named it as the gate on pilot #1. See §4 — this bypasses Epic's vendor gauntlet entirely and is the path RCM/financial-analytics vendors have historically used. v1 had exactly this capability (arbitrary-export profiling + concept mapping) and the rewrite dropped it. | SYNTHESIS §4b ("No 835/837 parser, no X12 handling, no Epic Clarity/Caboodle mapping, no ETL anywhere"); `prototype-comparison.md` §3 ("v2 has no path from a real customer export to an answer"); mindbowser.com ("Clarity and Caboodle are optimal for revenue-cycle analytics… batch extracts suffice"). | **M** |
| **N11** | **[PB-1] Close the LLM operational envelope.** Per-call timeout; bounded retry with transient-only classification and `Retry-After` honoured under a cap; circuit breaker; server-side per-tenant/day spend ceiling; prompt caching; honest token accounting separating billed totals from first-turn prefill; a `live_llm` CI job publishing p50/p95 and cost per turn. | Cheapest item on the port-back list and it closes a confirmed CRITICAL. The design decisions are already made and already justified by measurement in v1 — that is what makes it cheap. Grep for `timeout\|circuit.?breaker\|retry\|backoff` across the adapter and LLM application layer returns **zero hits**; `cache_control` appears nowhere, so the full vocabulary prompt is resent at list price every call. | SYNTHESIS D10; `prototype-comparison.md` §2.1. | **S/M** |
| **N12** | **[PB-2] Scored eval gate with operator choice as a graded axis; seed 150–300 utterances.** Cases as data, scoring as code; hard failures that can never be baselined away; derived held-out membership (`sha256(case_id) % 100 < 30`) with an **absolute** floor (v1 documented that a ratio floor freezes the suite); held-out question text dropped from stored reports; coverage rule — every governed card must have a case that needs it. | Zero measurement of the probabilistic layer exists. The scripted classifier is first-match-wins substring matching, so every refusal test in the round-1 review was structurally guaranteed to pass. Revi's own spike already found the failure this catches: all four trials compiled "the top two payers" to `AddFilter` instead of `DrillInto` — schema fidelity perfect, operator choice wrong. The coverage rule is also the mechanism that would have caught `benchmarks=()`. The RCM-executive persona offered to supply real utterances. | SYNTHESIS J4; `prototype-comparison.md` §2.2 and §3 move 1. | **M** harness / **L** corpus |
| **N13** | **[PB-4] Retrieval + citation-allowlist synthesis over the governed cards.** BM25F over a declared `topic` field with the aboutness rule and multi-word aliases indexed as phrases only; an identifier veto; three abstention floors including a higher bar for "may this be rendered as the answer" than "may this enter the context pack"; a server-side allowlist where every fact/policy claim cites an id the retriever approved *for this question*, with violations replaced by a deterministic fallback rather than repaired. Keep dense scoring off — it is structurally worse at refusing. | Turns the product's best-reviewed surface from a dictionary into an answerer. 84 of 134 shop-floor terms resolve; `dimension_for_synonym` is defined, unit-tested and never called; zero RARC definitions are governed, so CO-16 — the largest denial bucket at $2,025,317 — can be named and never explained. | `prototype-comparison.md` §2.4, §3 move 2; SYNTHESIS §4b (domain-vocabulary gaps). | **M** |
| **N14** | **[PB-3 + PB-6] Telemetry scrub-then-fingerprint, then the demand ledger.** Scrub deterministically and fail closed; typed placeholders so scrubbed text still clusters; salted HMAC; telemetry off entirely without a salt; the raw question never persisted. Then an inert materialized view: cluster on the **salted hash, never the scrubbed text**; require >1 occurrence with the threshold echoed back along with excluded singletons; the denominator travels with every numerator; abstention and fallback counted from different columns. | This is the instrument that decides what to build next, and it is not somebody's opinion. Revi already generates the exact signal it consumes — every `UNSUPPORTED_CONCEPT`, every `DATE_BASIS_INVALID`, every clarification with empty `options`, every un-drillable card is a recorded demand event that today evaporates. With 27 of 33 cards failing to drill and 15 of 27 contracts unable to answer, a demand ledger would have surfaced the coverage ceiling as a ranked list rather than as an adversarial review. Ship the scrubber *with* the first observability, never retrofitted. | `prototype-comparison.md` §2.3, §2.6; SYNTHESIS D4, D5. | **S** each, sequenced |
| **N15** | **Package the governance machinery as a Governance Evidence Pack, mapped to RUAIH.** Per-answer lineage export, pack version history and diff, refusal taxonomy with rates, model-change control, the honest security section from N2, and a mapping table from Revi artifacts to certification domains. | The Joint Commission + CHAI released initial responsible-AI guidance 2025-09-17; the **Responsible Use of AI in Healthcare (RUAIH) certification** was announced May 2026 and became available 2026-06-01 across ~22,000 accredited organizations, covering governance, safeguards, monitoring, education, transparency and risk/bias. Revi's lineage DAG, snapshot hashing, typed refusal and evidence grades are literally the vendor-side evidence a certification-seeking system needs. No RCM competitor is packaging this; incumbent scoring is explicitly black-box. This is documentation effort, not engineering effort, and it converts D3/D6 remediation into a sales narrative instead of an apology. Only credible **after** N2. | jointcommission.org (Sept 2025 guidance; May 2026 RUAIH announcement; certification program page). HFMA/Eliciting Insights Q2 2025 (n=233): 18% of health systems have mature AI governance. | **S** |
| **N16** | **Re-baseline the competitive section of `industry-research.md` against Epic SideKick, Pulse and Penny.** | The doc's Epic row is now factually wrong on the load-bearing clause, and it is the clause that determines where Revi is allowed to compete. Correcting it is hygiene; the strategic output is §2's wedge decision. | epic.com Healthcare Intelligence (SideKick, SlicerDicer, Pulse); advisory.com 2025-08-28 (UGM: Penny, 160–200 AI projects, CoMET on 118M patients); healthcaredataanalytics.org Epic SIG agenda. SYNTHESIS J1. | **S** |
| **N17** | **Name the liability boundary in the walkthrough: Revi is evidence of record, never the signatory.** Plus module packaging by domain — denials, A/R & cash, underpayments/contract performance — as sellable SKUs on one investigation platform. | Compliance and legal will ask two questions of any agentic RCM product: who signs, and what happens when the number is wrong. Writing the answer down converts a gap into a position. Separately, both ends of the market expect a menu: R1's Phare OS markets itself as "modular by design — start where it matters most and expand over time," and MD Clarity prices à la carte ("pay only for the products you need"). Revi's catalog is already organized by metric domain, so packaging is close to free and gives §6's land-and-expand motion concrete SKUs. | California SB 1120; r1rcm.com/enterprise-partnerships/phare-os; mdclarity.com/pricing; `industry-research.md` §2. | **S** |

### 6 MONTHS

| # | Action | Rationale | Evidence | Effort |
|---|---|---|---|---|
| **S1** | **Outcome capture as a byproduct of the action, not a separate project.** Every packet or export emits: what was asserted, which probes backed it, who approved it, what was sent, to whom, when — then a resolution poll on the `appeal_status` transition (NONE → APPEALED → OVERTURNED \| UPHELD) with dollars recovered. | This is the whole loop, and it arrives as the audit trail compliance already demands. The warehouse already models the states; what is missing is the write. It is also what the category leader says it is building toward, which makes it a competitive necessity. | SYNTHESIS J2; `packs/base-rcm/concepts.yaml` (`appeal_status` NONE/APPEALED/OVERTURNED/UPHELD); `appeal_overturn_rate` and `denials_unworked_pct` are shipped contracts. Waystar (2026-03-05): the stated goal is agents that "learn from outcomes." | **M** |
| **S2** | **The autonomy ladder, built from the pack's existing authority tiers — and moved inside `pack_snapshot_id`.** Generalize `filing_rules.yaml`'s five `authority` tiers (`federal_statute`, `federal_regulation`, `contract_default`, `plan_configuration`, `planning_default`) paired with `requires_confirmation` into an action-authority policy: which actions may execute unattended, which require named human approval, which are advisory only, and the fallback when authority is weak. | Gives buyers a declarative, versioned, replayable answer to "what can your AI do without a human?" — materially stronger than the prose assurances competitors offer, and largely already built. Human-in-loop is the shipped market norm (SmarterDx: "your team has full control"; Ensemble: 100% clinical review; Waystar: "oversight and safeguards"). **Critical:** it must live inside the snapshot. `anomaly_actionability.yaml` — the 0.60-weighted judgment that orders the worklist — loads straight off disk at `wiring.py:257`, outside `pack_snapshot_id`, un-overlayable, un-replayable, with a `content_hash` that has no consumer. An autonomy policy with that property would not survive a security review. | `packs/base-rcm/filing_rules.yaml`; SYNTHESIS §4b (actionability file outside governance). | **M** |
| **S3** | **Catalog + measure certification sprint, guided by the demand ledger.** Certify `status`, `submission_date`, `discharge_date`, `first_pass_paid`; bind REMIT at claim grain or rebind `denial_rate`; implement-or-retire the derived measure registry; widen `validate_pack_catalog_conformance` to field resolution so a pack that cannot answer refuses to compose. | The single root cause under D2, D4 and the real half of D5 — the difference between a cash-posting tool and a revenue-cycle product, since days in A/R, net collection rate, A/R over 90 and DNFB are the numbers on a board slide. Sequenced *after* N14 deliberately: coverage without a demand ledger is guesswork, and the review's own §2b correction shows the dimension work alone unlocks only about half (the derived-measure track is separate and equally necessary). | SYNTHESIS D4 (dual root cause), §2b. | **L** |
| **S4** | **Replace hand-authored recoverability constants with measured priors.** Once outcomes land, recoverability becomes a measured rate per payer × denial category × age bucket, with the constants surviving only as a governed cold-start prior. Until then publish a defensible sourced prior rather than a round number. | `anomaly_actionability.yaml` drives the 0.60-weighted term that orders the worklist and its values are constants nobody observes: default 0.50, UNDERPAYMENT 0.85, CREDIT_BALANCE 1.0. A sourced cold-start prior already exists — Premier 2025: 70% of denials ultimately overturned. | `packs/base-rcm/anomaly_actionability.yaml` (read directly); SYNTHESIS §4b. | **M** |
| **S5** | **Validate a Microsoft Fabric / OneLake adapter alongside Snowflake; ship the Snowflake Native App only after N4.** | Snowflake's "runs in your account" is a genuine, vendor-confirmed security-review accelerant — Snowflake's own framework docs cite "happier security teams" and "reduced procurement hurdles," and Datavant Connect, MedeAnalytics Health Fabric and Innovaccer Gravity all shipped Native Apps for exactly this reason. But Epic is simultaneously steering its shops toward "Cogito Cloud" and Fabric-native serving (single-source, directional), and the most commonly reported Epic-anchored enterprise pattern splits Databricks (engine) from Fabric + Power BI (serving), with Snowflake present but not dominant. Revi's 9-behaviour `AnalyticalRepositoryContract` — "the single most valuable structural asset in the repository" — is exactly the right shape to de-risk this by testing a second target rather than betting the wedge on one. | snowflake.com Native App Framework; datavant.com, innovaccer.com, medeanalytics.com press releases; rsmus.com (Epic → Fabric, treat as directional); dadosconsulting.com; `prototype-comparison.md` §3 item 3. | **M** |
| **S6** | **[PB-5] Ingest governance: named-human approval bound to a content hash.** Authoring/serving split; the pipeline may write only `pending` and structurally cannot publish; approval binds to `candidate_hash` so changing one served word makes the approval stale and promotion refuse; one legal crossing (`submit → evaluate → activate`); one-step rollback, deliberately ungated. | 55 cards and 19 benchmarks are stuck at `review_status: machine_researched` with no defined path forward. Revi has every hashing primitive and no lifecycle. **Sequence after N12** — "evaluate scores the candidate before activation" is what makes the lifecycle worth having, and that needs a scored gate to exist first. | `prototype-comparison.md` §2.5. | **M/L** |
| **S7** | **Export the pack to Snowflake semantic views and Databricks metric views.** | Counterintuitive but correct: if the layer commoditizes, make the commoditizing layer a distribution channel. Revi becomes the RCM content that makes somebody else's Cortex Analyst or Genie correct — a second revenue surface and a hedge against the wedge losing. Refusing to interoperate is how Looker's competitors lost. | docs.snowflake.com (semantic views as schema-level objects); docs.databricks.com (Unity Catalog metric views); cloud.google.com blog 2024-08-14 (opening the Looker semantic layer). | **M** |
| **S8** | **Run the two design-partner motions in parallel** (see §6): a mid-size land-and-expand partner and one flagship reference account co-developed on its hardest problem. | The two documented motions are not contradictory — one is the expansion cadence, the other is the credibility motion. Detail and evidence in §6. | akasa.com Montage Health case study; akasa.com Cleveland Clinic lessons; newsroom.clevelandclinic.org 2025-04-29. | **M** |

### 12 MONTHS+

| # | Action | Rationale | Evidence | Effort |
|---|---|---|---|---|
| **L1** | **Verified Investigation Repository — governed utterance → typed investigation spec pairs, mined from behaviour, promoted only by a named human bound to a content hash.** | Do not invent the compounding mechanism; the closest working analogue is public. Snowflake's Verified Query Repository admits entries three ways — hand-authored YAML with verifier metadata, a generator tool, and a "Verified Query Suggestion interface" that proposes candidates *from user behavior* — and is used at inference on similar questions. Revi's version is strictly richer because its unit is a typed spec (metric, dimensions, date basis, filters, operator sequence) rather than raw SQL, so a promoted entry is replayable, diffable and auditable. It also targets exactly the axis Revi's own spike found broken: operator choice. **Hard prerequisite: N12.** | docs.snowflake.com Cortex Analyst VQR; `prototype-comparison.md` §3 move 1 and the recorded spike (`AddFilter` vs `DrillInto`). | **L** |
| **L2** | **Outcome-based pricing kicker — and not before.** Name it as a deliberate pricing-roadmap milestone tied to S1, not an incidental byproduct of building write endpoints. | AKASA's %-of-recovery pricing is defensible because its agents autonomously act on a claim, so a specific automated action ties to a specific recovered dollar (per-transaction on eligibility/auth/claim-status; %-of-net-revenue-recovered only on the denial-appeal automation module). Revi cannot attribute a dollar to a surfaced finding until the outcome loop exists — pricing on outcomes today would be an overclaim that directly contradicts the product's own trust thesis. | revcycleai.com AKASA vendor deep-dive; SYNTHESIS J2; `prototype-comparison.md` §3. | **L** |
| **L3** | **athenahealth and eClinicalWorks integrations — design-partner-driven, after the Epic pilot.** | athenahealth's practice-scoped REST endpoints explicitly cover "Billing and Claims — charges, claims, payments, patient balances" plus FHIR R4 bulk `$export`, which is materially better RCM coverage than Epic's clinical-skewed FHIR surface — but there is no enterprise-wide grant ("each practice must explicitly enable your integration"), and MDP membership/certification runs 4–8 months. eCW has solid FHIR R4 *clinical* read coverage, **no** documented billing/claims data access, and no task/workqueue writeback — an eCW integration would answer fewer RCM questions than an Epic one for the same build. | mirth.support/blog/athenahealth-integration; explorer.usecobalt.com/ehr/eclinicalworks. | **L** |
| **L4** | **Clearinghouse 835/837 data-share as an EHR-agnostic fallback.** | Waystar, Experian Health and FinThrive already sit on the payer-facing side and get remittance and claim-status data with no EHR integration at all; Clarity/Caboodle remit extracts are a downstream re-statement of the same feed, delayed by the health system's refresh cadence. Sourcing 835/837 directly decouples Revi's highest-value signal from its slowest, most gate-kept integration path and works identically across Epic/athena/eCW. This is a BD decision in a consolidated market, not primarily an engineering build. | `industry-research.md` §4 competitor table. | **L** |

---

## 4. Integration path to production

The single most important finding across the integration sweep: **Revi's fastest path to production data in
an Epic shop is not Epic's sanctioned third-party pipeline.**

**Do not wait on the vendor gauntlet.** Showroom → Connection Hub/Toolbox/Vendor Services → FHIR client id
→ per-site security review runs 6–18 months end to end and *cannot start* until a specific health system
sponsors the app by enabling it in their own Epic environment (3–6 month Epic IT backlogs are common for
per-site provisioning). There is no "integrate once, sell everywhere." That pipeline is built for
point-of-care apps, and the FHIR resources Epic actually exposes skew clinical.

**The land sequence, in order:**

1. **Clarity/Caboodle read-only ODBC/SQL, negotiated with the health system's own IT team (N10).** These are
   relational reporting databases the customer already runs; they can grant read-only credentials directly,
   with no Epic certification, client id or Showroom listing involved. This is the track RCM/financial
   analytics vendors have historically used, and RCM workloads tolerate batch latency — no real-time
   requirement, batch extracts suffice.
2. **List on Epic Connection Hub immediately, as a trust signal only (~$500/yr, self-attested, needs one
   live Epic customer).** It is explicitly not an Epic endorsement and it is not a data pipe. It is exactly
   the move the closest direct competitor already made: "Adonis is now available in Epic Connection Hub,
   enabling health systems to extend Epic with AI-powered revenue cycle intelligence." Treat Toolbox
   (Blueprint category match + paid Vendor Services, ~$1,700–1,900/yr) and Workshop (invite-only
   co-development) as 12mo+ escalators, never near-term dependencies.
3. **Ship follow-through as a governed export + typed deep link, not a write API (N9).** The pattern holds
   across all three target EHRs: billing/claims/task writeback is the most gated capability on every
   platform. Epic pushes writes to HL7 v2 / interface engines and has no FHIR billing-workqueue resource at
   all; athenahealth gates writes behind MDP tiers with no billing writeback documented even there; eCW
   contract-gates every write scope and documents none for billing/claims or task/workqueue. Deliver a
   structured export shaped like a Follow-Up/Denial workqueue bulk-load that the customer's own interface
   team imports on schedule. `DocumentReference` is the one create-capable Epic upgrade path if a customer
   wants the packet in the chart.
4. **Fix the cohort write path before any warehouse conversation (N4).** "Why does your application need
   `CREATE TABLE` in my warehouse?" will be asked on day one. Have the written answer, and have the leak
   fixed.
5. **Then the Snowflake Native App — and a Fabric/OneLake adapter validated alongside it (S5).** "Runs in
   your account" is a real procurement accelerant with three shipped precedents in healthcare, but Epic is
   steering its own analytics estate toward Fabric and Power BI, and the common enterprise pattern splits
   Databricks (engine) from Fabric + Power BI (serving). Test the second target rather than betting the
   deployment wedge on one. The 9-behaviour repository contract is what makes this a configuration change
   rather than a rewrite.
6. **In parallel, evaluate a clearinghouse 835/837 partnership (L4)** as the EHR-agnostic fallback that
   decouples the highest-value signal from the slowest path.

**What this sequence buys:** a pilot that can start on a customer's own extract inside one quarter, a
credibility artifact in the Epic ecosystem for the price of a rounding error, and no promise on stage that
the public APIs cannot keep.

---

## 5. The compounding loop

Both codebases hit the same wall from opposite directions and neither resolved it: does the next unit of work
buy **coverage** or buy **the loop**? v1 answered "coverage" for four review rounds and built a content
pipeline. v2 answered "correctness" and built a kernel. Neither built the loop — v2's `PackDelta` and
`AnalystCorrection` are fully typed with zero producers, zero consumers, zero storage and zero endpoints;
v1's demand ledger is explicitly inert by design.

**The framing is a false binary, and the sequencing question has a right answer.**

The loop cannot be built first: you cannot tell whether a `PackDelta` improved anything without a scored
gate, and there is none. But coverage without an instrument is guesswork — 27 of 33 cards that cannot drill
and 15 of 27 dead contracts are *already* a ranked coverage backlog that today evaporates unrecorded. So:

> **Instrument first (S), then coverage guided by the instrument (L), then the correction loop (M), with the
> outcome loop arriving as a byproduct of the action API rather than as its own project.**

Concretely: **N14** (telemetry scrub + demand ledger) → **N12** (scored gate) → **S3** (certification sprint
ranked by the ledger) → **S6** (promotion, gated on the gate) → **L1** (Verified Investigation Repository).
The outcome loop rides along on **N6/N7 → S1 → S4**.

### The non-negotiable design constraint

**Build the flywheel on non-PHI artifacts only.** The loop as usually imagined — learn from customer claims
data — is contractually dead on arrival and would poison procurement. Buyers' counsel is explicitly closing
that door: Morgan Lewis (May 2026) states PHI "should not be used in public AI tools or to train
general-purpose models"; Censinet's vendor-contract guidance says contracts "must restrict vendors from using
patient data to train general-purpose or commercial AI models without explicit consent." A BAA alone does not
grant model- or content-improvement rights, which is precisely why buyers now add explicit prohibitions. A
flywheel that needs claim-level PHI to cross a tenant boundary will be struck in redline and will lose deals.

What crosses the boundary instead: scrubbed-and-salted-hash refusal fingerprints, alias and synonym
corrections, metric-contract deltas, CARC/RARC and payer-policy definitions, benchmark cohorts, operator-choice
corrections from refinement traces, and — per tenant, never pooled raw — measured recoverability rates. Tenant
N+1 gets a better **pack**, not a model trained on tenant N's claims. That distinction is what lets the
flywheel clause survive legal review.

### The three loops, in dependency order

**(a) Demand ledger — what to build next.** Inert by construction: a report for humans, never an agent
trigger, with no method that can promote, activate or change routing. Every `UNSUPPORTED_CONCEPT`, every
`DATE_BASIS_INVALID`, every clarification with empty `options`, every un-drillable card becomes a ranked
coverage backlog. Cluster on the salted hash, never the scrubbed text (scrubbing collapses two different
patients' questions into one string and would silently merge distinct demand). Require more than one
occurrence, echo the threshold and the excluded singletons. The denominator travels with every numerator —
"12 unsupported questions" reads as a crisis; "12 of 40" does not. Count abstention and fallback from
different columns so fixing one can never silently start or stop counting the other. **Effort S once
telemetry exists. This is the cheapest possible answer to "what do we build next" that is not somebody's
opinion.**

**(b) Correction loop — content that improves because it was used.** An analyst's "Fix it" produces an
`AnalystCorrection`; that produces a candidate `PackDelta`; a named human approves it bound to a
`candidate_hash` so changing one served word makes the approval stale; that produces a pack version, with
one-step ungated rollback. The promotion machinery is v1's, ported (S6). The retrieval half (N13) makes 55
cards and 19 benchmarks reachable *this* quarter, which is the prerequisite for anyone ever wanting to correct
them. Note what must be fixed first: the UI currently tells users *"Logged. Thanks — recorded against this
trace"* and mutates an in-memory zustand map — the one place the product talks to the user about trust, and
it is lying.

**(c) Outcome loop — the only thing that turns constants into knowledge.** Every packet or export emits what
was asserted, which probes backed it, who approved it, what was sent, to whom, and when; a resolution poll
follows the `appeal_status` transition with dollars recovered. This is not a separate build — it *is* the
audit trail compliance already demands, and it arrives free with N6/N7. Its payoff is S4: replacing the
hand-authored 0.50 / 0.85 / 1.0 recoverability constants — which carry 0.60 of the ranking weight and are
observed by nothing — with measured rates per payer × denial category × age bucket. Until then, publish a
defensible sourced prior (Premier 2025: 70% of denials ultimately overturned) rather than a round number, and
zero the estimate on expired filing windows rather than discounting it.

**The gate that makes all of it legible:** N12's scored corpus, with operator choice as a graded axis. Without
it, a `PackDelta` is a change with no evidence it is an improvement — which is exactly the thing this product
exists to refuse.

---

## 6. Pricing and GTM posture

**Model: tiered platform subscription, per facility and per network, with domain modules.** Not %-of-NPSR,
not per-seat. Every named competitor hides list pricing behind "contact sales" (VisiQuate, Adonis, MD Clarity,
Rivet, FinThrive all confirmed custom-quote-only), and where fragments leak, none prices a pure
analytics/investigation layer as %-of-recovery — AKASA reserves that specifically for its autonomous
denial-appeal module and prices investigation/status modules per transaction. The closer analogs for an
analytics-not-automation product are MD Clarity's unlimited-users à la carte subscription and Rivet's flat
$6,000/yr single-user entry (confirmed independently across two sources). **Treat the industry's pricing
opacity as a differentiation opportunity** — publishing a defensible price is on-brand for a product whose
entire thesis is defensibility.

**Anchor the number against the RCM-ops spend benchmark, not against seats.** Buyers already carry an
internalized figure: health systems spend approximately 5% of net patient revenue on revenue cycle operations
(SEC DRS filing, Thor Holdco Corp., CIK 0001865597, 2021-06-11, implying a ~$50B addressable market), and
full RCM outsourcing runs 2–8% of NPR. Anchor Revi as a small fraction of that spend and of the leakage it
shrinks — denial write-offs at 2–5% of NPR per HFMA MAP AR-6, underpayments at 1–3% of net revenue — which
are the same benchmarks already authored in the pack and currently unreachable (D9/N5). A per-seat framing
invites headcount-based objections and caps the deal at the size of the analytics team.

**Lead the ROI narrative with governance and augmentation, not cost-cutting.** Bessemer's *State of Health AI
2026* (Jan 2026) argues durable pricing power comes from "ROI so clear that customers will pay 2-3x more than
commodity alternatives," and warns that vendors "competing primarily on price rather than differentiated
outcomes" lack durable revenue. At the anecdotal level, AKASA's flagship partner was sold and case-studied on
augmentation language — "not to replace people, but to level up how we're using them… coders as validators" —
against a named workforce-retirement urgency (55% of coders retirement-eligible), not a labor-reduction pitch.
Health-system HR and labor politics resist the latter. It is also the only narrative Revi can make honestly
today: it has typed refusal and provenance, and it does not yet have the reconciliation and outcome plumbing
that a hard dollar-recovery claim requires.

**Who signs, who champions, who can kill it.**

- **CFO — economic buyer and signer.** The on-record buyer in the best-documented mid-market case study is the
  CFO, quoted as champion; HealthLeaders (Sept 2025) frames CFOs as needing to *own* revenue cycle strategy
  rather than fund it.
- **VP/Director Revenue Cycle — champion and daily user.** Drives adoption; supplies the utterances for N12.
- **CIO / security review — the gate, and today an automatic disqualifier.** Non-clinical software routes
  through IT/security even when it skips the clinical Value Analysis Committee. N2 and N4 exist because of
  this row.

**Design-partner motion: run both documented patterns in parallel.**

- **Mid-size land-and-expand (the cadence).** The clearest documented motion in the sector: RFP selection on
  domain fluency → a single narrow workflow chosen *because* it is simple enough to prove the vendor
  relationship → 4-month build-to-live → results measured at 3 months → expansion decision at 6 months (more
  payers, then a physician group, then a new workflow). At a 256-bed, $787M-net-operating-revenue Epic shop.
  Map this onto Revi's certified surface, not its aspirational catalog: the wedge family from §2.
- **Flagship reference (the credibility motion).** The opposite sequencing: lead with the partner's *hardest*
  problem, tied to a named organizational urgency, tuned on their data, with named title-holders
  co-presenting results publicly. For Revi that is denial root-cause investigation with reconciliation
  guarantees — the thing no competitor can copy quickly.

**First proof metrics: cash-cycle and staff-capacity, never model accuracy.** The metric categories that got
reported back to a buyer and used to justify expansion in the reference case were A/R-days movement (13%
decrease), coverage/completeness (97% of claims checked), throughput (5k+ claims/month) and staff-hours
reclaimed (300+/month). Revi's equivalents exist — `days_in_ar`, `denial_write_off_pct_of_revenue`,
`denials_unworked_pct` — but several are currently unreachable (D4), so **pilot scope must be chosen from
what is certified today**, which is another reason the wedge is the denial/payer-scorecard family. Add two
Revi-specific proof metrics no competitor offers: *percentage of published figures that reconcile to source*
and *refusal rate with reasons* — both are governance evidence and both feed N15.

**Bound the proof window at ~90 days with pre-agreed metrics.** Healthcare enterprise sales cycles run 6–18
months (≈14.7-month average, ≈22 decision-makers; 2+ years for the largest IDNs). The pilot must produce
quotable proof inside a bounded window or momentum dies in procurement.

**Pricing sequencing, stated as a roadmap item:** subscription now; outcome-based kicker only after S1 exists
(L2). Do not promise %-of-recovery pricing before Revi can prove causation.

---

## 7. Risks that kill this

Five, ranked by probability × severity. Each with the move that mitigates it and where it lives in §3.

**1. Epic bundles the wedge at zero marginal price.** SideKick already builds reports from plain-language
questions; Pulse already does peer benchmarking; Penny is reported for Nov 2026 with outpatient denial
appeals and automated claims follow-up. If Revi's pitch is "ask questions of your revenue cycle," it is
competing with a free feature inside the system of record, and the repo's own competitive doc still says Epic
has no NL. **Mitigation:** N16 (re-baseline the doc) plus the §2 wedge decision — compete where Epic
structurally will not go: cross-source reconciliation, payer/auditor-facing defensibility, and mixed estates.
Epic's answer always lives inside Epic, and Epic's benchmarking is documented over cost, provider efficiency
and throughput — not the denial canon.

**2. Security review kills pilot #1 before the product is ever evaluated.** No authentication, cross-tenant
read *and* write reproduced live, an evidence cache with no tenant key/TTL/purge path, and an undisclosed
`CREATE TABLE` requirement on the customer's warehouse. Any one of these ends a CIO conversation; together
they end it in the first meeting. **Mitigation:** N2 and N4 are non-negotiable and precede every customer
conversation; N15 then converts the remediation into a certification-mapped sales asset rather than an
apology.

**3. The wedge cannot be demonstrated because the coverage ceiling holds.** 6 of 33 cards drill, the first
working card is rank 17, 15 of 27 contracts can never answer, and the dead set is the MAP Key core. A design
partner who asks for days in A/R or net collection rate in week one gets a refusal. **Mitigation:** N3 (honesty
pass + repoint, which plausibly recovers 4 of the top 11 cards for a YAML edit), scope the pilot to the
certified denial family (§2), and sequence S3 behind the demand ledger so certification effort goes where
demand actually is. Say the boundary out loud in the pilot scope document — the product's best-reviewed
behaviour is typed refusal, and pre-declaring scope is the same move.

**4. The moat is the layer, and the layer commoditizes.** Semantic layers became platform primitives at
dbt, Looker, Snowflake and Databricks inside four years. If Revi's story is "we have a governed semantic
layer," the story has a two-year clock. **Mitigation:** reposition around the RCM contract corpus and the
audit record (§1), and make the pack *exportable* to Snowflake semantic views and Databricks metric views
(S7) so the commoditizing layer becomes a distribution channel rather than a competitor. Refusing to
interoperate is how Looker's competitors lost.

**5. The flywheel is blocked in redline — or never starts because nothing is instrumented.** J2 is exact:
`PackDelta` and `AnalystCorrection` have zero producers and zero consumers; there is no appeal-filed,
appeal-won, dollars-recovered or analyst-accepted signal anywhere; session N+1 is exactly as smart as session
1. And the obvious fix — learn from customer claims — is precisely what buyers' counsel now prohibits.
**Mitigation:** §5's non-PHI artifact constraint written into the contract template before the first pilot,
plus N14 → N12 → S1 in that order, so the loop's first producer is an action record that compliance already
demands rather than a data-sharing clause that compliance will strike.

**Two more worth watching, below the line.** *Payer-portal RPA as a tempting shortcut* — it is a treadmill,
and the evidence comes from the vendors who sell it (AKASA's own copy frames continuous portal breakage as the
core engineering problem, with 71% of accounts removed from staff queues meaning 29% still route to humans;
VisiQuate acquired Rotera on 2025-03-25 for "self-healing automations"; thoughtful.ai's revenue-cycle
automation page now 301-redirects to smarterdx.com with `utm_campaign=site-sunset`), while Availity is
standing up a sanctioned agent-ready rail ("Availity Extend") alongside Enhanced Claim Status, Claim
Attachments and HIPAA Transaction APIs, and CMS-0057-F puts payer FHIR prior-auth APIs on the clock. A
screen-scraper whose behaviour changes when a payer ships a CSS class cannot be replayed, reconciled or
graded — a category error for this product. *And the financing bar:* Rock Health H1 2026 shows $7.4B across
244 deals with 20 mega-deals taking 45% of capital and investors prioritizing clinical execution, structural
discipline and workflow integration over AI as a differentiator. "Platform" without a demonstrated wedge does
not clear that bar.

---

*Sources are cited inline. Repo grounding: `docs/research/industry-research.md`,
`docs/reviews/round1/SYNTHESIS.md`, `docs/reviews/prototype-comparison.md`, plus direct reads of
`packs/base-rcm/filing_rules.yaml`, `packs/base-rcm/anomaly_actionability.yaml`,
`packs/base-rcm/concepts.yaml` and `apps/api/src/revi_api/app.py`. The only file written was this one.*
