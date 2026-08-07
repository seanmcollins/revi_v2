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

## Design extensions (approved in plan, 2026-08-07)

1. `DEFINITIONAL` turn class — "what is PR3" answered from governed pack content, zero probes.
2. `project_lagged_realization` operator — deterministic cash outlook (see `operator-algebra-v0.md`),
   DERIVED-grade, conclusion policy labels estimates as estimates.
