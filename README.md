# Revi

**Revi** is a conversational investigation platform for healthcare revenue-cycle-management (RCM)
analytics. Analysts ask broad or ambiguous questions — *"Do I have a COB issue?"*, *"Why did cash decline
last week?"* — and refine them across turns. Questions compile into **typed investigations** over governed
semantics; a **probabilistic control plane** (LLM) interprets language and emits operators from closed sets,
while a **deterministic data plane** (versioned calculation kernel) computes every published number. Every
answer carries its effective context — window, date basis, filters, cohort, data watermark — and full
evidence provenance.

- **Authoritative design:** [`rcm-investigation-platform-design-v2.md`](rcm-investigation-platform-design-v2.md)
- **Architecture boundaries:** [`docs/architecture.md`](docs/architecture.md)
- **Operator algebra (decompose/projection v0):** [`docs/operator-algebra-v0.md`](docs/operator-algebra-v0.md)

## Stack

| Piece | Tech |
|---|---|
| Platform packages | Python 3.12, uv workspace, frozen dataclasses, Protocol ports |
| Analytical warehouse | DuckDB (mock, generated; Snowflake + Semantic Views is the future production backend) |
| Application state | Postgres 16 (docker-compose) |
| LLM | Claude Agent SDK behind a `LanguageModelPort` — stateless, schema-constrained calls only |
| API | FastAPI + SSE |
| Frontend | Next.js + Tailwind + shadcn/ui |

## Quickstart

```bash
cp .env.example .env          # fill in ANTHROPIC_API_KEY
make bootstrap                # uv sync + pnpm install
make warehouse                # generate the deterministic mock warehouse + answer key
make db-up migrate            # Postgres + schema
make dev                      # API :8000 + web :3000
```

`make help` lists everything else (`test`, `reference`, `lint`, `portfolio`, …).

## Repository map

```
packages/     platform capabilities (kernel, investigation, catalog, calculation, pack,
              presentation) + their public contracts, connectors/adapters, test harness
packs/        governed domain-pack content (base RCM pack + tenant overlays), YAML
warehouse/    mock-data generator + semantic catalog YAML + scenario answer key
apps/         FastAPI app, portfolio scheduler, Next.js frontend
docs/         architecture notes, operator algebra, industry research
```
