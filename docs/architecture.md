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

## Design extensions (approved in plan, 2026-08-07)

1. `DEFINITIONAL` turn class — "what is PR3" answered from governed pack content, zero probes.
2. `project_lagged_realization` operator — deterministic cash outlook (see `operator-algebra-v0.md`),
   DERIVED-grade, conclusion policy labels estimates as estimates.
3. **Concentration findings** — findings from a *ranked* frame when a playbook has no comparison.
   Questions like "do I have a COB problem?", "score my facilities" and "what is aging out of
   timely filing?" rank a population rather than comparing two windows; before this path existed
   they executed correctly and then answered nothing. `impact_cents` is set only when the ranked
   measure is money — a claim count is not dollars.
