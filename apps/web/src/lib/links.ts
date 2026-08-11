/**
 * The product's permalinks.
 *
 * Until these routes existed there was no per-session or per-investigation
 * URL anywhere in the app — `useRouter`/`usePathname`/`pushState` returned
 * a single hit across the whole codebase, and it was a page reload — while
 * the archive dialog promised in writing that a session "stays reachable by
 * link". That promise is kept here.
 *
 * Both builders are pure string functions with the origin passed in, so
 * they are testable without a browser and safe to call during a server
 * render (where `window` does not exist and the caller passes "").
 */

/** `/s/{session_id}` — re-joins the session and rebuilds its thread. */
export function sessionLinkFor(sessionId: string, origin: string): string {
  return `${origin.replace(/\/+$/, "")}/s/${encodeURIComponent(sessionId)}`;
}

/**
 * `/i/{investigation_id}` — one answered turn, resolved to the session it
 * belongs to (`InvestigationResponse.session_id`) and opened there, so the
 * turn arrives with the conversation that produced it rather than as a
 * findings card with no lineage above it.
 */
export function investigationLinkFor(investigationId: string, origin: string): string {
  return `${origin.replace(/\/+$/, "")}/i/${encodeURIComponent(investigationId)}`;
}

/**
 * `/r/{run_id}` — one deep-research run: the waiting room while it works,
 * and the report once it has.
 *
 * ONE ADDRESS FOR BOTH STATES, deliberately. A run takes about a minute
 * and outlives the click that started it, so the link handed to somebody
 * mid-run has to still be the link to the report — the surface changes
 * under the address rather than the address changing under the reader.
 *
 * A sibling of `/s/{id}` rather than a child of it. A run DOES persist as
 * an investigation in a session of its own, so `/s/{session}` resolves to
 * it too — as the restored answer the server stored. This route is the
 * composed artifact: the same run, read as a report rather than as a
 * conversation.
 */
export function researchPath(runId: string): string {
  return `/r/${encodeURIComponent(runId)}`;
}

export function researchLinkFor(runId: string, origin: string): string {
  return `${origin.replace(/\/+$/, "")}${researchPath(runId)}`;
}
