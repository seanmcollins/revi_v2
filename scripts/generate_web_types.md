# Regenerating frontend API types

`make openapi` exports the FastAPI schema to `contracts/openapi.json`
(no warehouse/DB/LLM needed — route and DTO shapes only).

The frontend wires its own generation step (owned by the frontend
workstream; `apps/web` is not touched by the API workstream). It is already
set up under `apps/web`:

```jsonc
// apps/web/package.json (devDependencies)
"openapi-typescript": "^7.13.0"

// apps/web/package.json (scripts)
"gen:types": "openapi-typescript ../../contracts/openapi.json -o src/lib/types.gen.ts"
```

Flow: `make openapi && (cd apps/web && pnpm gen:types)` — writes
`apps/web/src/lib/types.gen.ts`.

Notes for the frontend agent:

- `TurnResponse` is a discriminated union on `outcome`
  (`answer | clarification_required | error`).
- The SSE stream on `POST /v1/sessions/{sid}/turns` (with
  `Accept: text/event-stream`) emits `stage*, warning?, clarification?,
  context_header, finding*, chart_spec*, narrative_delta*, turn_complete`;
  the `turn_complete` frame's data is the full `TurnResponse` and is the
  authoritative payload (the streamed narrative is provisional).
- `ChartSpec.rows[].referent_id` is the click handle: POST a typed
  `drill_into` refinement with it — no natural language in the loop.
- `PortfolioResponse.items[].drill_filters` + `drill_window` start an
  ordinary investigation turn from an anomaly card.
