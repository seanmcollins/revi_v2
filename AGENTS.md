# AGENTS.md — working in this repo

A guide for agents (and humans) modifying, extending, or integrating Revi. The
authoritative specification is [`rcm-investigation-platform-design-v2.md`](rcm-investigation-platform-design-v2.md);
when this file and the design doc disagree, the design doc wins. [`README.md`](README.md)
covers quickstart; [`docs/architecture.md`](docs/architecture.md) covers layer boundaries.

## What Revi is

Revi is a conversational investigation platform for healthcare revenue-cycle
analytics. Users ask questions in plain language; questions compile into **typed
investigations** executed over governed semantics. An LLM interprets language but
never computes numbers; a deterministic calculation kernel computes numbers but
never interprets language. Alongside the conversational surface, **Monitors** is
the proactive surface: pinned investigation specs re-run on every data load,
material changes surface in a per-load brief, and detected anomalies are ranked
by recoverable dollars.

## Core principles

These are the physics of the system. Every one of them was tested by adversarial
review; weakening any of them breaks the product's central claim (numbers you can
trust without checking them yourself).

1. **Two-plane split.** The probabilistic control plane (LLM behind
   `LanguageModelPort`) emits operators from closed, schema-validated sets. The
   deterministic data plane (calculation kernel) produces every published number.
   No LLM-generated SQL, no LLM-generated numbers, ever.
2. **Provenance-complete or refuse.** Every published value traces to governed
   metric contracts, a pinned data watermark, and recorded probes. If a question
   can't be answered with provenance, Revi says so honestly (design doc §2.8)
   rather than approximating.
3. **Governed content lives in packs, not code.** Metric definitions, dimensions,
   playbooks, display names, materiality thresholds — all YAML under `packs/`.
   Question handling is never hardcoded; demo scenarios live in warehouse *data*,
   never in canned responses.
4. **Watermark pinning.** Every answer and every monitor tile is pinned to a
   specific data load (`wm_*`) and is reproducible against it. Comparisons across
   loads are explicit, never silent.
5. **Honesty machinery is load-bearing.** Bounded values render as bounds (≤),
   premises are verified before being answered ("It did not double — it rose
   8%"), rankings refuse when too much of the field is bounded, immature windows
   are flagged, small cohorts are suppressed with stated thresholds. These are
   features, not caveats to trim.
6. **Truth relocates, never deletes.** The default surface is calm — marks on
   the data, quiet notes below it, warning register reserved for verdict-class
   content (premise corrections, refusals, regressions). Full fidelity always
   lives in the Evidence rail and every export. Moving detail is fine; dropping
   it is not.
7. **Determinism everywhere testable.** The warehouse generator, the answer key,
   scripted LLM fixtures, plan-hash caching, and the warehouse-diff harness
   (an independent naive-SQL rederivation of every published value) exist so
   that correctness is checked by machinery, not by trust.

## Inflexible vs. flexible

Default to **inflexible**. The flexible list is short and exhaustive.

**Inflexible** (do not change without explicit owner sign-off):
- The two-plane split and the `LanguageModelPort` boundary.
- Refusal over approximation; all §2.8 honest-non-answer behavior.
- Watermark pinning and reproducibility of published answers.
- The honesty machinery: bounds-as-data, premise verification, coded warnings
  (`warning_codes.py` families), suppression, maturity guards, reconciliation.
- Pack-governed semantics — no hardcoded metrics, thresholds, or question paths.
- Layer boundaries enforced by import-linter (6 contracts) — contracts packages
  have no implementation dependencies; the kernel imports nothing above it.
- Export/Evidence full fidelity, regardless of how calm the default surface gets.
- The verification bar (below) passing before any change lands.

**Flexible** (safe to adapt during integration):
- **Warehouse backend.** DuckDB is the mock; Snowflake is the intended
  production backend. Swap behind `connector-duckdb`'s seams; the semantic
  catalog (`warehouse/catalog/`) is the contract.
- **Anomaly detection.** Detected anomalies enter through an `AnomalySource`
  port; an external detection system can replace the baked warehouse population.
- **Auth.** Dev-open today (`REVI_AUTH_DEV_TENANT`), bearer tokens available
  (`make token`); replace with the host application's auth at the FastAPI
  dependency seam (`apps/api/src/revi_api/auth.py`). Everything is
  tenant-scoped already.
- **Pack content.** Add metrics, playbooks, concepts, display names — the
  mechanism validates content against contract schemas.
- **Presentation.** Chart styling, layout, palette, and the web shell may
  evolve freely *within* the calm register and honesty marks. The web app is
  replaceable entirely: the API + SSE stream is the real product boundary.
- **Model pin and settings profiles** (`.env`, `settings_policy.py`).

## Repository map (read in this order)

```
rcm-investigation-platform-design-v2.md   the spec — §2 principles, §6 algebra, §11 architecture
docs/architecture.md                      layer boundaries and allowed imports
packages/kernel/                          calculation kernel (strict-typed, deterministic)
packages/investigation/                   the engine: interpretation → planning → validation
                                          → execution → findings; application/ is the core
packages/catalog/ + calculation/          semantic catalog and metric computation
packages/pack/ + pack-learning/           pack loading/validation; learning workflow
packages/presentation/                    narrative composition, grounding validation
packages/adapter-claude/                  LanguageModelPort implementation (Claude Agent SDK)
packages/connector-duckdb/                warehouse access
packages/store-postgres/                  app state + alembic migrations
packages/testing/                         warehouse-diff harness (independent rederivation)
packages/*-contracts/                     public contracts only — no implementation deps
packs/base-rcm/                           governed content: 49 metric contracts, playbooks,
                                          concepts, display names, monitor materiality
warehouse/                                deterministic generator + catalog + answer key
apps/api/                                 FastAPI + SSE; assembly.py projects engine output
                                          to the wire; monitors.py is the Monitors backend
apps/web/                                 Next.js UI; lib/ maps wire → view models
contracts/openapi.json                    exported API surface (make openapi)
docs/reviews/adversarial/                 ten rounds of persona review — the "why" behind
                                          many non-obvious behaviors
```

## Integration boundary

Integrate at the **API**, not by importing Python packages into the host app.
`contracts/openapi.json` (regenerate with `make openapi`) is the surface;
answers stream over SSE. The web app consumes only this surface — it is the
reference client and proof the boundary is sufficient. Embedding the UI: the
host provides auth and tenancy; Revi provides sessions, turns, evidence,
exports, and Monitors.

## Verification bar

Every change must pass, from repo root:

```bash
uv run pytest -q -p no:randomly        # full backend suite
uv run pytest -m reference             # reference conversations vs answer key
uv run pytest -m postgres              # store parity (needs make db-up migrate)
uv run ruff check . && uv run lint-imports
make warehouse-diff                    # independent rederivation, zero divergence
cd apps/web && pnpm test && pnpm lint && pnpm build
```

CI also runs mypy across all package `src/` dirs (see `.github/workflows/`) —
broader than `make typecheck`.

## Vocabulary

User-facing language and internal language are deliberately different. In
prose shown to users: "data load" (never watermark or `wm_003`), "monitor" /
"Monitor this", "check" (never probe), metric display names from
`packs/base-rcm/metric_display.yaml` (never snake_case ids). Internal
identifiers never appear on default surfaces — Evidence and exports carry them.

## Things that look like bugs but are features

- Refusing to rank when >50% of a field is bounded. Answering "I can't verify
  that premise." Declining a comparison across incompatible date bases.
- ≤ values, dashed/hollow chart marks, suppressed small cells.
- "Nothing material changed since the last load" as a proud, complete brief.
- The same question answered from cache within one watermark (plan-hash reuse).

If a change makes one of these disappear, the change is wrong.
