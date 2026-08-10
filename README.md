# Revi

**Revi** is a conversational investigation platform for healthcare revenue-cycle-management (RCM)
analytics. Analysts ask broad or ambiguous questions — *"Do I have a COB issue?"*, *"Why did cash decline
last week?"* — and refine them across turns. Questions compile into **typed investigations** over governed
semantics; a **probabilistic control plane** (LLM) interprets language and emits operators from closed sets,
while a **deterministic data plane** (versioned calculation kernel) computes every published number. Every
answer carries its effective context — window, date basis, filters, cohort, data watermark — and full
evidence provenance.

- **Authoritative design:** [`rcm-investigation-platform-design-v2.md`](rcm-investigation-platform-design-v2.md)
- **Working principles + verification bar:** [`AGENTS.md`](AGENTS.md) — start here to understand what this codebase will and will not trade away
- **Guided read of the layers:** [`docs/code-tour.md`](docs/code-tour.md)
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
| Frontend | React + Vite + Tailwind + shadcn/ui |

## Quickstart

```bash
cp .env.example .env          # fill in ANTHROPIC_API_KEY
make bootstrap                # uv sync + pnpm install
make warehouse                # generate the deterministic mock warehouse + answer key + anomalies
make db-up migrate            # Postgres + schema
make dev                      # API :8000 + web :3000
```

`make help` lists everything else (`test`, `reference`, `lint`, `typecheck`, `sweep`, …).

Auth is dev-open by design at this stage and nothing here is deployed. Before drawing any
conclusion about the security posture, read the **"Not built"** section of
[`docs/acceptance-walkthrough.md`](docs/acceptance-walkthrough.md) — it names the gaps
itself, and [`SECURITY.md`](SECURITY.md) summarizes them.

## Repository map

```
packages/     platform capabilities (kernel, investigation, catalog, calculation, pack,
              presentation) + their public contracts, connectors/adapters, test harness
packs/        governed domain-pack content (base RCM pack + tenant overlays), YAML
warehouse/    mock-data generator + semantic catalog YAML + scenario answer key
apps/         FastAPI app, cohort-TTL sweep CLI, React/Vite frontend
docs/         architecture notes, operator algebra, code tour, acceptance walkthrough,
              industry research
docs/reviews/ persona-review evidence: five hostile reviewer reports from round 1, plus
              ten rounds of adversarial review/fix cycles as raw JSON. These are the
              unedited record of what reviewers found, including what is still broken
```

## License

Revi is licensed under [PolyForm Noncommercial 1.0.0](LICENSE.md): free to use, modify,
and share for any **noncommercial** purpose — personal study, research, teaching, and
use by charitable, educational, public-research, health, and government organizations.
Commercial use is not granted by this license.

Third-party components carry their own terms, which are unaffected by the above; see
[THIRD-PARTY.md](THIRD-PARTY.md). Official X12 code descriptions are licensed content
and are not reproduced in this repository — the code definitions in `packs/base-rcm/`
are independently written.

Contributions are welcome under the same license: see [CONTRIBUTING.md](CONTRIBUTING.md).
