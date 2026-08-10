# `apps/web` — the Revi frontend

The analyst-facing surface of Revi: a conversational workspace for RCM
investigations. An analyst asks a question, the answer arrives as a streamed
turn carrying its own context (window, date basis, filters, cohort, data
load) and its full evidence, and every follow-up refines the same
investigation rather than starting a new one.

Three routes and a rail:

| Route | What it is |
|---|---|
| `/` | The workspace — question composer, answer thread, evidence and lineage panel |
| `/s/{session_id}` | Permalink to a session; the thread is rebuilt server-side from its lineage |
| `/i/{investigation_id}` | Permalink to a single answer |
| `/monitors` | What changed on this data load — the brief, the monitor tiles, and their leads |

Vite + React 18 + react-router-dom v7 + Tailwind v4 + shadcn/ui + Zustand
for turn state + TanStack Query for the GET side. Light theme only — there
is no dark palette and no theme toggle.

The dev server is pinned to `:3000` (`vite.config.ts`), not Vite's default
`5173`: the API's CORS allowlist (`REVI_CORS_ORIGINS`) names that origin.

## Commands

pnpm only. Run them from this directory.

```bash
pnpm dev        # vite dev server on :3000
pnpm test       # vitest run
pnpm test:watch # vitest, watching
pnpm lint       # eslint
pnpm build      # tsc --noEmit && vite build -> dist/
pnpm gen:types  # regenerate src/lib/types.gen.ts from ../../contracts/openapi.json
```

`src/lib/types.gen.ts` is generated — never hand-edit it. Regenerate it with
`pnpm gen:types` after the API contract moves.

From the repo root, `make dev` brings up the API on `:8000` and this app on
`:3000` together.

## Driver modes

The app talks to the platform through a `Driver` interface (`src/lib/driver.ts`)
with two implementations, so the UI can be developed and tested without the
backend running.

| Mode | Implementation | What it does |
|---|---|---|
| `api` (default) | `src/lib/apiDriver.ts` | The real product: `POST /v1/sessions`, SSE turn streaming, lineage, monitors, portfolio |
| `mock` | `src/lib/mockDriver.ts` | A dev/test fixture — it answers the reference-demo questions and returns a scripted clarification for anything else |

Configured by environment, with a per-browser override stored in
`localStorage` under `revi-driver` (the command palette can flip it):

| Variable | Default | Meaning |
|---|---|---|
| `VITE_REVI_DRIVER` | `api` | `api` or `mock` |
| `VITE_REVI_API_URL` | `http://localhost:8000` | Where the API lives |
| `VITE_REVI_TENANT` | `demo` | Tenant sent on session open |

Read through `import.meta.env`, and inlined at build time — a change to any
of them needs a rebuild, not a restart.

Mock mode is a fixture, not a demo of the product — every number it shows is
canned. The connection pill says which mode is live at all times.

## Fixtures

`src/lib/mock/` holds the mock driver's content: the reference conversation,
the definitional answers, and the portfolio. `src/lib/__fixtures__/` holds
recorded LIVE payloads — real responses captured from the API — which the
contract tests replay so a server-side contract change fails here rather
than in a demo.

Contract tests (`src/lib/contract-*.test.ts`) reconcile the client's parsers
against `../../contracts/openapi.json` directly; they are the reason a
renamed wire field is caught at `pnpm test` time.

## Also read

- [`../../README.md`](../../README.md) — the platform: stack, quickstart, repository map
- [`./src/globals.css`](./src/globals.css) — the design tokens, the type scale, and the typeface
