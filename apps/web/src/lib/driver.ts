/**
 * The driver seam. The UI talks to a TurnDriver; swapping the mock for the
 * real API is exchanging this implementation only — the store, the event
 * union, and every component are identical for both.
 *
 * The real driver (lib/apiDriver.ts) wraps `streamTurnEvents` from lib/sse.ts:
 *   POST /v1/sessions/{sid}/turns  with Accept: text/event-stream.
 */

import type { DeploymentCapabilities, SessionSettings } from "@/lib/settings";

/**
 * The build-time driver default — `process.env` only, no `localStorage`.
 *
 * It lives on the seam rather than in the workspace component because the
 * store's INITIAL connection state needs it too: opening on a hardcoded
 * `mock` and correcting it in an effect one paint later is what made an
 * api-mode deployment flash a "Mock driver" pill and an amber degraded
 * badge before it had spoken to the server at all. Reading only
 * `process.env` keeps the server render and the first client render in
 * agreement; the `localStorage` override (`resolveDriverKind`, in
 * apiDriver.ts) is applied afterwards, on the client, where it is safe.
 */
export function envDriverKind(): DriverKind {
  return process.env.NEXT_PUBLIC_REVI_DRIVER === "mock" ? "mock" : "api";
}

import type {
  DataWatermark,
  DebugTrace,
  PackVersionRef,
  Refinement,
  SessionListData,
  TurnEvent,
} from "@/lib/types";

/** Which implementation sits behind the seam. */
export type DriverKind = "mock" | "api";

/** Connection state machine for the live API: connecting → online ⇄ offline. */
export type ConnectionState = "connecting" | "online" | "offline";

export interface TurnSubmission {
  utterance?: string;
  /**
   * A typed FIRST turn — an explicit `TypedInvestigationSpec` (metric ids,
   * dimensions, filters, window/basis) that opens a NEW investigation with
   * no parent and no model call. This is what a portfolio card's
   * `drill_spec` is, and what a chart click posts when the session has no
   * prior answer to refine. Passed to the wire verbatim: it is already the
   * published shape, so translating it would only be a chance to get it
   * wrong.
   */
  spec?: Record<string, unknown>;
  refinements?: Refinement[];
  /** Reply to a pending clarification — sent as `clarification_response`. */
  clarificationResponse?: string;
  /** Ask the server to re-anchor this session to the freshest watermark. */
  reAnchor?: boolean;
  /**
   * Internal session settings to run THIS turn under (`TurnRequest.settings`).
   * Omitted entirely when every control sits at its default, so the default
   * path stays byte-identical to the one that existed before the settings
   * panel. Out-of-bounds values are sent as chosen — the server refuses with
   * `POLICY_DENIED` and the refusal is shown verbatim, never pre-empted by
   * clamping here.
   */
  settings?: SessionSettings;
}

/**
 * One turn rebuilt from what the server kept, ready to reduce through the
 * same `applyEventToAnswer` pipeline a live turn goes through. `events`
 * carries only what the wire actually holds after the fact — findings,
 * warnings, the terminal status — so a resumed turn renders its real
 * answer and nothing it cannot prove.
 */
export interface ResumedTurn {
  investigationId: string;
  /** The question as asked, from the lineage node. */
  question: string;
  events: TurnEvent[];
}

/** A re-joined session plus its rebuilt thread. */
export interface ResumedSession {
  sessionId: string;
  watermark: DataWatermark;
  pack: PackVersionRef;
  turns: ResumedTurn[];
}

export interface TurnDriver {
  submit(
    submission: TurnSubmission,
    emit: (event: TurnEvent) => void,
    signal?: AbortSignal,
  ): Promise<void>;
  /**
   * "New chat": abandon whatever session is cached and start clean. The api
   * driver discards its cached session and eagerly opens a fresh one
   * server-side; the mock driver just rewinds its local reference-turn
   * progress. Never rejects — an unreachable API is tolerated by falling
   * back to lazy session creation on the next turn.
   */
  newSession(): Promise<void>;
  /**
   * `GET /v1/investigations/{iid}/trace` — a turn's decision breakdown,
   * read AFTER the fact. The server records a trace on every turn
   * regardless of the debug setting (the setting only decides whether it
   * rides along with the answer), so this is how debug mode explains a turn
   * that was answered before the toggle was flipped. Optional on the seam:
   * the mock driver has no server-side traces and says so by not
   * implementing it, rather than by fabricating one.
   */
  getTrace?(investigationId: string): Promise<DebugTrace | null>;
  /**
   * `GET /v1/capabilities` — what this deployment will accept. The settings
   * panel renders controls from this and nothing else; a driver that cannot
   * answer (the mock fixture) leaves the panel showing why the controls are
   * unavailable instead of offering knobs with no deployment behind them.
   */
  capabilities?(): Promise<DeploymentCapabilities>;
  /**
   * `GET /v1/sessions` — the caller tenant's sessions, newest activity
   * first. Optional on the seam for the same reason `capabilities` is: the
   * mock fixture has no deployment and therefore no sessions to list, and
   * saying so is honest where inventing rows would not be. The rail then
   * shows why the list is empty instead of three plausible-looking titles.
   */
  listSessions?(limit?: number): Promise<SessionListData>;
  /**
   * Switch to an existing session: re-join it (`POST /v1/sessions` with
   * `session_id` — the server's existing re-join semantics) and rebuild
   * its thread from the wire (`GET .../lineage` for the questions and
   * order, `GET /v1/investigations/{iid}` for each turn's findings, its
   * evidence bundle and its charts).
   *
   * Only what the server kept comes back. Stream-only ephemera — the
   * stage timings and the composed narrative, neither of which is
   * stored — are gone, and the resumed turn says so rather than replaying
   * an invented pipeline or inventing prose nobody wrote.
   */
  resumeSession?(sessionId: string): Promise<ResumedSession>;
}
