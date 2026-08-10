# Porting the Revi frontend into a Vite host — migration scope

Target host: React 18 + TypeScript, Vite build, react-router-dom v7, Tailwind CSS v4,
Radix/shadcn patterns, Recharts (host is adding it). Revi mounts inside the host's shell
as a route subtree for now, with a clean extraction path since that arrangement may not
last. **Parity mandate: the port must not change how Revi looks or behaves.**

Grounding: a full coupling inventory of `apps/web` (108 source files, 43 test files).
Only **8 files import from `next/*`** — the Next surface is small. The real work is the
React 19→18 down-port and the router-observed URL rewriting. Everything data-shaped
(SSE transport, API driver, Zustand store, chart layer) is framework-agnostic already.

## Verdicts up front

| Axis | Verdict |
|---|---|
| Charts | **Keep Recharts.** ApexCharts cannot reproduce the honesty marks (per-cell dashed ceilings, split solid/dashed unmeasured segments, ≤/†/‡ tick composition) or the measured axis geometry without a lossy rewrite. Recharts 3.10 runs unmodified under Vite + React 18. |
| Tailwind | **Already v4, CSS-first, zero `@apply`, no JS config.** The 654-line `globals.css` ports verbatim. |
| Tests | **Already Vitest** (+ `@vitejs/plugin-react`, `vite-tsconfig-paths`, jsdom). The suite ports with targeted edits, not a migration. |
| Dark mode | **Being removed upstream** (owner decision: light only). The port inherits a light-only app — no `next-themes`, no `.dark` block, no toggles. |
| SSR | Nothing to lose. All data fetching is client-side; the 5 server components are trivial shells. |

## What ports untouched

`sse.ts` (hand-rolled POST-SSE reader — deliberately not `EventSource`), `apiDriver.ts`
(1,103 lines), `mockDriver.ts`, `store.ts` (single Zustand store, no middleware),
`queries.ts` (TanStack Query), `links.ts`, `export.ts`, `announce.ts`, the entire
`components/` tree except the touch-points below, `InvestigationChart.tsx` wholesale,
and `globals.css` minus the `.dark` block. This is most of the app by volume.

## Workstreams

### W1 — Scaffold and build (small)
- Vite entry: `index.html` + `main.tsx` mounting `<App/>`; favicon moves from
  `src/app/favicon.ico` (Next file convention) to an explicit `<link rel="icon">`.
- Tailwind via `@tailwindcss/vite` (or keep the PostCSS plugin — verify identical output
  for `@custom-variant` and the paired `--text-*--line-height` tokens either way).
- No `@source` directives exist today; if Revi's sources sit outside the host's root,
  add one so v4's content detection sees them.
- Verify `tw-animate-css` resolves through Vite's CSS `@import` — all Radix enter/exit
  choreography depends on it and its absence degrades **silently** to no animation.
- Delete: `next.config.ts` (empty anyway), `next-env.d.ts`, tsconfig's Next plugin +
  `.next/types` includes, the create-next-app `public/*.svg` scaffold (all unreferenced).
- ESLint: `eslint-config-next` is the *only* lint config — replace with the host's
  flat config + `typescript-eslint` + react-hooks.

### W2 — Routing (small surface, one hard part)
Route table is tiny — 4 routes, 1 layout, no loading/error boundaries, no middleware,
no API routes:

| Next | react-router v7 |
|---|---|
| `app/page.tsx` → `<Workspace/>` | `<Route path="/" element>` |
| `app/monitors/page.tsx` | `<Route path="/monitors">` |
| `app/s/[sessionId]/page.tsx` | `<Route path="/s/:sessionId">` + `useParams` |
| `app/i/[investigationId]/page.tsx` | `<Route path="/i/:investigationId">` + `useParams` |
| `app/layout.tsx` (providers, fonts, metadata) | Layout route: providers + `<Outlet/>`; `<title>` via the host's head mechanism |

Mechanical swaps: 5 `useRouter().push` → `useNavigate()`, 1 `usePathname()` →
`useLocation().pathname`, 6 `next/link` → react-router `<Link>`. Mount under a host
subtree (e.g. `/revi/*`) with relative links or a basename so extraction later is a
one-line change.

**The hard part — raw `history.replaceState` (top parity risk):**
- `Workspace.tsx` rewrites the address bar to `/s/{id}` when a session goes live and
  back to `/` on New chat via **raw `window.history.replaceState`**, relying on Next's
  router observing it. react-router does **not** observe raw `replaceState` —
  `useLocation` consumers desync. Convert to `navigate(path, { replace: true })`
  while preserving the exact lifecycle: never override a permalink, never interrupt
  work, redirect-to-Monitors at most once per unseen load (sessionStorage latch),
  focus + screen-reader announcement on arrival.
- `answerVariant.ts` does the same for `?variant=` (plus a module-level snapshot for
  `useSyncExternalStore` and cross-tab `storage` sync) — convert to router-native
  search params or keep bypassing the router *consistently* (decide once; today it
  bypasses Next's router too, so keeping the bypass is the lower-risk parity choice).

### W3 — React 19 → 18 down-port (the widest workstream)
No React-19-only hooks anywhere (`use()`, `useOptimistic`, `useActionState`: zero
occurrences). The coupling is **ref-as-prop**: `forwardRef` appears zero times; all 11
`components/ui/*` primitives are plain function components typed
`React.ComponentProps<...>`.

- Under React 18 this breaks silently at **42 `asChild` sites** — Radix `Slot` forwards
  a ref into `Button`/`Badge`, gets `null`, and tooltips/popovers/hover-cards lose their
  anchor (positioning + focus return break app-wide, warning-only, invisible to jsdom
  tests).
- Plus one direct hit: `<Textarea ref={composerRef}>` in `TurnInput.tsx` — composer
  autofocus/focus-restore dies.
- Fix: re-add `forwardRef` to every `components/ui` primitive (shadcn's own React-18
  form), sweep `@types/react`/`@types/react-dom` to `^18`, then hand-verify anchored
  overlays (tooltip, popover, dropdown, hover-card) in a real browser — jsdom cannot
  catch this class.
- `useSyncExternalStore` (5 sites) and `useId` (8 sites) are React-18-safe as written.

### W4 — Fonts (small but parity-critical)
`next/font/google` self-hosts Geist + Geist Mono (variable), injects `font-display:
swap`, and generates a **metric-matched fallback face**. The type scale in
`globals.css` was tuned against these metrics, and `body` sets
`font-feature-settings: "cv11","ss01"`. Port with `@fontsource-variable/geist` +
`geist-mono`, keep the `--font-geist-sans`/`--font-geist-mono` variable names, and add
a size-adjusted fallback (`@font-face { size-adjust }` from Geist's metrics) so nothing
reflows. Do not substitute a different sans.

### W5 — Env and config (mechanical)
- `NEXT_PUBLIC_REVI_API_URL / _TENANT / _DRIVER` → `import.meta.env.VITE_*` (6 read
  sites: `apiDriver.ts` ×3, `driver.ts`, `CommandPalette.tsx`, plus test-only vars).
- The API's CORS allowlist (`REVI_CORS_ORIGINS`) is pinned to `localhost:3000` — add
  the host dev server's origin or run on 3000.
- `gen:types` (openapi-typescript) is framework-neutral; keep the script.

### W6 — Tests (targeted edits across 43 files)
- 3 files mock `next/navigation` → mock react-router (or wrap in `<MemoryRouter>`);
  3 more render real `next/link` unmocked → need a router provider.
- 7 files set up URL state via `history.replaceState` — wrap renders in
  `<MemoryRouter initialEntries>` instead.
- **The three-way parsing pins** (`contract-watch-units`, `contract-followups`,
  `contract-openapi`) hard-code `../../../../` to the repo root and one hard-codes
  `apps/web/...` by name — they read the Python engine's source to pin frontend/wire/
  engine agreement. In the host repo the engine isn't there. Do not weaken them:
  vendor a copy of `contracts/openapi.json` into the host, keep the TS-vs-openapi pins
  against it, and leave the Python-reading halves in this repo's CI as the
  cross-repo drift alarm. Refresh the vendored spec on every sync.
- The tree-walking design lints (`contrast.test.ts`, the focus-ring lint) will scan any
  new scaffolding placed under `src/` — keep scaffolding outside or expect them to
  (correctly) flag it.

### W7 — Parity verification (the exit gate, not optional)
1. Screenshot corpus: the 19 captured chart fixtures × 4 container widths + the 8 core
   surfaces (answer calm/detailed, Monitors grid, brief, evidence rail, lineage,
   settings, command palette), Next build vs Vite build, pixel-diffed. Light only.
2. Behavioral battery: composer focus lifecycle, tooltip/popover anchoring at the 42
   `asChild` sites (sampled), permalink lifecycle (live → `/s/{id}` → New chat → `/`),
   cold-start redirect-once, `?variant=` deep link, CSV export, copy-link disclosure,
   driver flip + reload.
3. Full Vitest suite green in the host + `pnpm build` + the openapi pin against the
   vendored spec.

## Open decisions (owner input welcome, defaults chosen)

- **Auth**: unknown host mechanism. Default seam: the driver takes a
  `getAuthHeaders(): Promise<Record<string,string>>` provider injected at mount;
  dev keeps the current open-auth behavior. One file (`apiDriver.ts`) changes.
- **Packaging**: unknown. Recommendation: copy the source in as one self-contained
  `revi/` folder (own path alias, own README) rather than a workspace package —
  simpler for the host repo, and the folder boundary *is* the extraction path. Revisit
  packaging only if a second consumer appears.
- **Mount point**: assumed `/revi/*` subtree with basename-relative routing; deep links
  `/revi/s/:id`, `/revi/i/:id`.

## Sequencing and effort

Port **after** the in-flight cleanup wave lands (Monitors rename + dark-mode removal +
copy audit) so the ported code is born with final vocabulary, light-only styling, and
calm copy — otherwise every one of those sweeps happens twice.

| Workstream | Effort |
|---|---|
| W1 scaffold/build | ~half a day |
| W2 routing incl. `replaceState` conversion | ~1 day (the lifecycle is the risk, not the volume) |
| W3 React 18 down-port + browser verification | ~1–1.5 days |
| W4 fonts | ~2–3 hours |
| W5 env | ~1 hour |
| W6 tests | ~1 day |
| W7 parity gate | ~half a day |

Roughly **4–5 focused days** end-to-end for verified parity, most of it W3/W6/W7
diligence rather than invention.
