# Revi architecture — boundary map

Companion to the authoritative design doc (`../rcm-investigation-platform-design-v2.md`). This file records
how the design's boundaries map onto this repository and which rules are mechanically enforced.

## Dependency rule (design §11.1)

```
entrypoints (apps/api, apps/scheduler)  →  application  →  domain
infrastructure (connector-*, store-*, adapter-*)  →  application ports + domain
```

Capability implementations (`revi_investigation`, `revi_catalog`, `revi_calculation`, `revi_pack`,
`revi_presentation`) never import each other — only `revi_kernel` and each other's `*-contracts` packages.
Enforced by import-linter (root `pyproject.toml`, run via `make lint`), including vendor isolation:

| Vendor | Only allowed in |
|---|---|
| `duckdb` | `revi_connector_duckdb`, `revi_warehouse` |
| `claude_agent_sdk` | `revi_adapter_claude` |
| `sqlalchemy` / `psycopg` / `alembic` | `revi_store_postgres` |
| `fastapi` / `starlette` / `uvicorn` | `revi_api` |
| `pydantic` | contracts packages + LLM schemas only — never domain modules |

## Persistence split

- **Analytical data** → `AnalyticalRepository` port. DuckDB adapter now (as-of reads = snapshot-schema
  selection); Snowflake + Semantic Views later. The analytical contract suite in `revi_testing` is the
  swap-safety net (design §18.1-1).
- **Application state** → Postgres, one schema per capability (`revi_session`, `revi_trace`, `revi_cohort`,
  `revi_pack`, `revi_cache`) honoring §15 schema-per-capability structurally. Role separation is deferred
  until multi-tenant deployment; noted here deliberately.
- Every app-state port has an in-memory fake in `revi_testing`; both implementations pass the same contract
  suite.

## Evidence grades have two axes (design §2.3, §5.5)

A frame's grade is the weakest of two independent judgements, and they are
computed in different places because they are known in different places:

| Axis | Question it answers | Where |
|---|---|---|
| **Certification** | Is this field trustworthy at all? | the connector, from the semantic catalog — an adapter can see this without a pack |
| **Binding strength** | How well does this field stand in for *the concept being asked about*? | `PlanValidationService` (§6.6 step 1), from the pack's `bindings.yaml` |

Certification alone is not enough, because the same certified field is
different evidence for different questions. A CARC is the direct
representation of a **denial** and only a *proxy* for **coordination of
benefits** — a reason code is the payer's assertion about coverage, not the
coverage. So the investigation's pack concept ids ride on `AnalysisSpec`
(a closed set, validated at interpretation, carried across refinements) and
grading is asked per concept. A field the pack declares no binding for
contributes nothing: silence is not a downgrade.

The planner's grade is applied in `ExecuteInvestigationService` **after**
the evidence cache, weakest-wins. Cache entries therefore stay
concept-independent — the same bytes serve a denial question and a COB
question — while each answer still carries the grade its own question
earned.

## Governed content is checked against the catalog before it is served (design §5.2, §12)

A pack and the semantic catalog are two independently versioned bodies of
governed content. Nothing forced them to agree until composition time, and the
checks that existed were all demand-driven: `PlanValidationService` rejects
unresolvable concepts, and the DuckDB compiler raises `UNSUPPORTED_CONCEPT` on
an unknown filter dimension — but both only ever see the metrics a question
actually reaches. A contract no playbook names, or one whose probes are pruned
as unanswerable before they compile, is never examined at all. It sits in the
pack looking authoritative for as long as nobody asks.

That is not hypothetical. All seven `exclusions:` clauses in `base-rcm` were
authored as inclusion predicates — `exclusions` compiles to
`FILTER (WHERE NOT …)`, so each kept exactly the population its description
said it removed. Six referenced base-view columns that are not catalog
dimensions, so every probe touching them was pruned and the inversion never
surfaced as a wrong number. The seventh, `denials_unworked_pct`, named a
certified dimension, executed, and had been reporting over
patient-responsibility notices only — the exact records it meant to drop.

`revi_pack.conformance.validate_pack_catalog_conformance` closes the structural
half. It walks every metric contract in the composed snapshot, resolves each
`exclusions` predicate against the catalog, and raises
`PackCatalogConformanceError` (`UNSUPPORTED_CONCEPT`, the same code the
compiler raises for the same condition at probe time) naming **every**
offending `(metric_id, dimension_id)` pair — systematic authoring errors arrive
in batches, and stopping at the first turns one review into seven.
`revi_api.wiring.build_components` calls it unconditionally, so a deployment
refuses to start rather than serving content whose meaning the catalog cannot
carry.

Two boundaries are deliberate. **"Resolves nowhere" means absent, not
uncertified**: an uncertified dimension binds a real column and downgrades the
answer to DISCOVERY grade, which is how the platform says "this evidence is
weak" — failing composition on it would make honest weak-evidence content
unauthorable. And the check covers `exclusions`, not contract-internal
`Filtered` predicates; widening it is the obvious next step and
`packs/base-rcm/NOTES.md` enumerates the seven contracts that would trip it,
all blocked on the same catalog work.

**What it does not catch.** The guard would have caught six of the seven. It
cannot catch the seventh, and no structural check can: an exclusion whose
dimension resolves is indistinguishable from a correct one, because the only
thing that ever distinguished them was the description. Polarity is pinned
instead in executed numbers — `test_duckdb_contract.py::TestExclusionPolarity`
proves that `exclusions` subtracts, including that the excluded value
contributes zero on its own cut of the very dimension it names — and read
against each contract's prose at review time. A tripwire and a reading habit,
not a guarantee.

## A typed first turn is the drill-down anchor (design §18.1-10)

A refinement refines a *parent* investigation. That is right, and it is why a
portfolio card had nowhere to land: a card is not an investigation, so posting
its `set_window` + `add_filter` operators to a fresh session returned the
designed `CLARIFICATION_REQUIRED` — honest, and useless. The build plan's
answer was a portfolio-anchored session whose hidden investigation the card's
operators would refine. We did not build that, because every surface with an
already-typed intent would need its own hidden parent: a chart click on a
fresh page, a saved view, a scheduled brief.

The generic answer is the **typed first turn**. `TurnRequest.spec` carries a
`TypedInvestigationSpec` — metric ids, dimensions, filters, window/basis,
optional comparison — and a turn carrying one is a `NEW_INVESTIGATION` *by
construction*: no parent, no classification, no interpretation, **zero model
calls**. It is the typed twin of interpretation, not a bypass of it.
`InterpretQuestionService.from_typed_spec` runs the same disposal the LLM path
runs — every metric id against the pinned pack, every dimension (breakdown and
scope alike) against the catalog, the basis against the governing contract's
`allowed_date_bases`, the window resolved exactly once into stored concrete
dates — and everything downstream is the ordinary pipeline, §6.6 validation
included. A typed spec skips the guessing, never the governance. `spec` and
`refinements` are mutually exclusive on the request (422, not a resolution
order): a turn either starts an investigation or edits one.

`AnomalyCard.drill_spec` is therefore a complete, executable investigation
rather than a bag of operators, and it is **required** — a card whose handle
could be absent is a card the UI would have to invent a question for. The
card's dimensions appear twice on purpose: as `dimensions` they are the
breakdown, and at their detected values as `filters` they are the scope that
isolates the cell. So the drill re-derives the detector's assertion from
certified semantics and a versioned metric contract, the header shows the cell
as visible chips (§7.2), and a later `remove_filter` widens back into the
population without guessing what it was. No comparison is set: a card claims a
level, not a movement, and `set_comparison` is one ordinary refinement away —
which now has a parent to land on.

Two supporting changes fall out, both generic:

- **A grouped query with nothing to compare gets a `rank` step.** A direct
  metric query cut by dimensions and carrying no comparison window is a ranked
  *population* question, which is exactly the shape the concentration finding
  path reads. Without the step such a plan executed perfectly and answered
  nothing — the same silent emptiness M13 fixed for playbooks, reached this
  time through direct queries. The step reuses that path verbatim rather than
  inventing a second finding shape.
- **Equality-pinned dimensions cost one cell, not their cross-product.** The
  §6.6 cardinality budget multiplied catalog estimates over the group-by
  regardless of scope, so a drill into one detected cell cut by four
  dimensions — each pinned to a single value by the card's own filters — was
  refused for a 21,600-cell budget it could not possibly spend. Conjunctive
  `eq`/`in` predicates now cap a dimension at what they enumerate; anything
  under `Or`/`Not` is ignored, so the estimate stays an upper bound.

## Anomaly onset comes from the source, not the formula (design §5.3)

The governed `anomaly_priority@1` recency term decays from an *onset* — when
the problem began — which it reads as the evidence fact `onset_date`. A
detection feed stamps `detected_at` when it *ran*: the warehouse generator
writes the same load timestamp on every row of a snapshot, so a portfolio
ranked off that stamp reports one age for the whole population and the recency
term separates nothing. For this feed the onset is
`detected_anomalies.window_start` by construction — the planted spec's `onset`
is the observation window start, and the detector only counts events inside
`window_start..window_end` — so `DuckDbAnomalySource` publishes `onset_date` on
the evidence mapping it hands to the application, with `setdefault` semantics:
a feed that knows its own onset always wins. The wiring belongs in the source
because the source is the only component that knows this feed's row shape; the
priority formula stays expressed in evidence facts and never learns a warehouse
column name. At `wm_003` the 33 cards now span 28 distinct ages from 1 to 155
days (median 38), and flattening the onsets demonstrably reorders the
portfolio.

## Anomaly cards carry provenance, not a grade (design §5.3)

`AnomalyCard` deliberately has no `grade`, and the frontend shows a
**DETECTION** badge rather than a `GradeBadge`.

An evidence grade certifies *how this platform computed a number* from
certified semantics through versioned contracts and operators. A portfolio
card is not that: it is a record read from an external detection system
as-of a watermark. Stamping DIRECT/DERIVED/PROXY on it would invent
provenance the platform cannot vouch for. Leaving the field absent was
worse still — the frontend's contract guard treated a missing grade as
drift, so a live portfolio tripped a visible banner on every load.

The resolution is to say the true thing explicitly. Every card carries:

- `provenance: "external_detection"` — what kind of assertion this is;
- `priority_formula_version` — the versioned platform formula that ranked
  it (`anomaly_priority@1`), the one part of the card the platform *does*
  own;
- `source_watermark_id` — the watermark it was read at.

All three are **required** on the wire: a card that could omit its
provenance is a card a client could mistake for platform-computed
evidence. Drilling a card starts an ordinary investigation turn, and *that*
answer carries a real grade.

## Determinism boundary (design §2.2, §7.10)

Probabilistic: turn classification, question interpretation, referent resolution, refinement-operator
emission, chart-type tie-breaks, narrative composition. All are stateless, JSON-schema-constrained calls
through `LanguageModelPort`, validated against closed sets / live registries after the fact.

Deterministic: everything downstream of validated typed operators — planning, probe compilation, metric
evaluation, transform operators, reconciliation, ranking, chart data. No LLM arithmetic, ever.

## Watermarks locally

The generated warehouse contains several snapshot schemas (`snap_001…`) plus a `main.watermarks` table;
an as-of read selects a schema. This is the design's "snapshot copies" posture for the DuckDB twin — no
time-travel emulation. Mid-session refresh, `WATERMARK_STALE`, and epoch transitions are all exercised
locally against real snapshots.

## The published contract (`contracts/openapi.json`)

The OpenAPI document is the frontend's pinned contract, regenerated by
`make openapi` and consumed by `cd apps/web && pnpm gen:types`. Two things
a naive FastAPI export leaves out are published deliberately:

- **`ErrorEnvelope` on every `/v1` route** at 400/404/409/503. Status 422
  stays FastAPI's own `HTTPValidationError` for malformed request bodies —
  one status, one model, so a generated client never has to guess which
  shape it received. Domain (§12) errors that would otherwise share 422
  return **400** for exactly this reason.
- **`TurnStreamEvent`** — the SSE frame kinds and their payloads. A
  streaming media type has no response schema of its own, so without this
  the stream would be an untyped blob in the spec and the UI's parser its
  only contract. `create_app` wraps `app.openapi()` to register the schema
  and point `text/event-stream` at it; `application/json` keeps the
  discriminated `TurnResponse` union.

Both are guarded by tests in `packages/testing/tests/test_api_contract.py`
(`TestPublishedSpec`) on the server side and by
`apps/web/src/lib/contract-openapi.test.ts` on the client side, so neither
can silently regress.

### The wire → UI seam, and why the frontend needed one

Publishing the contract made a pre-existing mismatch visible: the UI's parser
asserted the UI's *own* vocabulary against the server. It required
`status` (no turn outcome carries one — the 200 body discriminates on
`outcome`), `finding.referent.value` (the wire sends the string `"F1"`),
`charts` (`chart_specs`), `header.grain.entity` (not published, and not
rendered). Its fixtures were hand-written in the same vocabulary, so they
agreed with the parser and disagreed with the server: the suite was green
while every live frame was contract drift.

The correction is not to re-type the UI. The two vocabularies are different on
purpose — the wire speaks the domain, the UI speaks rendering — so
`lib/types.gen.ts` owns the wire, `lib/types.ts` owns the screen, and
`lib/contract.ts` holds the mapping between them, once, with every reduction
named: engine stage → rail slot, `chart_type` → the three shapes the chart
component draws (the published value rides along on `wireChartType`), measure
unit → formatter unit, `PredicateOp` → chip text. Three things the UI renders
and the wire does not publish — the watermark's load time, its newest data
date, the pack pin — come from the session bootstrap, whose fields are
spec-required; the header's own `watermark_id` is carried through verbatim so a
header from another epoch stays visibly different from the pin. One thing is
withheld rather than guessed: direction-of-good lives on the metric contract,
not on `FindingPayload`, so deltas are not tone-coloured in API mode.

Two guards keep it honest. `contract-expectations.test.ts` runs the parsers
against payloads **captured from a running server**
(`src/lib/__fixtures__/wire-samples.json`), and `liveApi.test.ts` drives the
real `ApiDriver` against a real API — skipped unless `REVI_LIVE_API` names one,
so `pnpm test` stays hermetic, but checked in so the check is reproducible
rather than a thing someone once did by hand.

## Design extensions (approved in plan, 2026-08-07)

1. `DEFINITIONAL` turn class — "what is PR3" answered from governed pack content, zero probes.
2. `project_lagged_realization` operator — deterministic cash outlook (see `operator-algebra-v0.md`),
   DERIVED-grade, conclusion policy labels estimates as estimates.
3. **Concentration findings** — findings from a *ranked* frame when a playbook has no comparison.
   Questions like "do I have a COB problem?", "score my facilities" and "what is aging out of
   timely filing?" rank a population rather than comparing two windows; before this path existed
   they executed correctly and then answered nothing. `impact_cents` is set only when the ranked
   measure is money — a claim count is not dollars.
4. **Typed first turn** (`TurnRequest.spec`) — an explicit `TypedInvestigationSpec` classified
   `NEW_INVESTIGATION` with no parent and zero model calls. The typed twin of interpretation, and
   the drill-down anchor §18.1-10 was missing (see the section above).

## The scripted demo narrative is composed per turn

Referent handles are session-monotonic — the reference conversation certifies
F1..F3 on turn 1 and F4..F6 on turn 2 — so any *fixed* narrative sentence cites
handles that do not exist on later turns and the §2.2 grounding validator
redacts it, correctly. The scripted demo model therefore reads the certified
findings back out of the rendered `compose_narrative` prompt and templates from
the handles and grades alone, stating no free numbers and no proper names; the
figures stay on the finding cards and the chart, where they carry their own
provenance. What is scripted remains only the probabilistic layer — turn class,
interpreted ids, refinement operators, and the shape of the sentence — while
every number, grade, chart and header is computed by the engine against the
real warehouse. All demo turns (the reference five, the COB anchor, the
definitional anchor) now pass `validate_narrative` with zero redactions.
