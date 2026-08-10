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

## Where Revi fits

Revenue-cycle teams already have reporting — EHR-embedded dashboards and BI — and are
increasingly sold automation: workflow vendors that act on scored claims. Both leave the
same gap. When a number moves, someone senior asks *why*, and answering that today means
an analyst, a report backlog, and a week. Revi is built for that gap. It is an
**investigator**, not a dashboard and not a work queue: it works the whole revenue cycle
on every data load, finds and quantifies anomalies, ranks them by recoverable dollars
and deadline runway, and then answers the follow-up questions live, in plain language,
with the evidence attached.

### Differentiators

These are design commitments verifiable in this repository, not market claims:

- **The model never computes a number.** The LLM interprets language into operators from
  closed, schema-validated sets; every published figure comes from a deterministic,
  versioned calculation kernel over governed metric definitions. Same question, same
  answer.
- **Provenance-complete or refuse.** Every number carries its query, window, filters,
  and data load. When the honest value is a ceiling it renders as ≤; when a ranking
  would be mostly ceilings, Revi declines to rank; when a question's premise is false,
  the answer corrects it first ("It did not double — it rose 8%"). In this domain a
  wrong number costs more than a missing one, so refusal is a feature.
- **The product audits itself.** An independent harness (`make warehouse-diff`)
  rederives every published value from the metric contracts alone, by a deliberately
  naive SQL path, and fails CI on any divergence.
- **Proactive by construction.** Monitors re-run pinned investigations on every data
  load; a per-load brief says what changed and what resolved on its own; the anomaly
  portfolio separates what already hit cash from what is *still catchable*, with
  dollars and deadlines attached. "Nothing material changed" is a first-class message,
  not an empty state.
- **Semantics are governed content, not code.** Metrics, dimensions, playbooks,
  benchmarks, and materiality thresholds live in reviewable YAML packs — extensible per
  tenant, never hardcoded per question.

### The pitch

Dashboards tell you *that* something moved. Revi tells you **why**, **what it costs**,
and **what to work first** — every load, every payer, every dollar — with every number
ready to hand to a CFO, because the query behind it travels with it.

What exists here is the working prototype of that pitch: a generated warehouse, a real
engine, and — in [`docs/reviews/`](docs/reviews/) — the unedited record of adversarial
review that forced the honesty machinery into existence. The reviewers were AI personas
standing in for buyers, which is a rehearsal, not a market; the machinery they forced
is real either way.

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
docs/reviews/ persona-review evidence: five hostile reviewer reports from round 1 kept
              in full, plus a summarized record of the ten adversarial review/fix
              cycles — the unedited account of what reviewers found, including what
              was still broken when they found it
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
