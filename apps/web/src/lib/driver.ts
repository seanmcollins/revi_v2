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
 * The build-time driver default — `import.meta.env` only, no `localStorage`.
 *
 * It lives on the seam rather than in the workspace component because the
 * store's INITIAL connection state needs it too: opening on a hardcoded
 * `mock` and correcting it in an effect one paint later is what made an
 * api-mode deployment flash a "Mock driver" pill and an amber degraded
 * badge before it had spoken to the server at all. Reading only
 * `import.meta.env` keeps the module-scope default and the first render in
 * agreement; the `localStorage` override (`resolveDriverKind`, in
 * apiDriver.ts) is applied afterwards, on the client, where it is safe.
 */
export function envDriverKind(): DriverKind {
  return import.meta.env.VITE_REVI_DRIVER === "mock" ? "mock" : "api";
}

import type { CreatePinRequest } from "@/lib/apiDriver";
import type { LeadStatus } from "@/lib/contract";
import type { LeadState, MonitorsPin } from "@/lib/monitors";
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
  /**
   * `TurnRequest.anomaly_ref` — the id of the portfolio card this turn is
   * drilling. Sent alongside `spec`, and load-bearing: it is what makes
   * the server reconcile the card's published figure against the answer's
   * own and publish `anomaly_reconciliation`. Without it the analyst reads
   * $178,217 on one screen and $195,873.92 on the next with nothing
   * anywhere connecting them.
   */
  anomalyRef?: string;
  refinements?: Refinement[];
  /** Reply to a pending clarification — sent as `clarification_response`. */
  clarificationResponse?: string;
  /**
   * `TurnRequest.worklist` — ask this turn to carry the ranked worklist.
   *
   * The typed twin of the governed routing. A turn whose interpretation
   * resolves the pack's worklist playbook or concept gets the list
   * attached on its own; this is the explicit handle for a surface that
   * ALREADY knows — the lane chips on an inline worklist re-asking for one
   * lane, a "what should I work first" chip, a scheduled brief.
   *
   * Purely additive, and the wording of that matters for what the UI may
   * claim: it never changes what the turn investigates. The turn runs
   * exactly as it would have and the worklist rides alongside its
   * findings, so a lane chip is a re-query of the LIST, not a refinement
   * of the answer above it.
   */
  worklist?: { limit?: number; lane?: string };
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
   * "New chat": abandon whatever session is cached and start clean.
   *
   * Purely local, for both implementations: the api driver drops its
   * cached session and makes NO request, and the mock driver rewinds its
   * local reference-turn progress. The server mints a session when a turn
   * arrives and never on its own, so a session is created by the first
   * question — not by the button. Between the two there is no session, and
   * the UI is expected to say so rather than keep showing the pin of the
   * one just abandoned. Never rejects; there is nothing here that can fail.
   */
  newSession(): Promise<void>;
  /**
   * "You are already in this session" — told to a driver that does not know
   * it yet, so the next turn CONTINUES that session instead of opening a
   * new one.
   *
   * A driver is owned by a React component, and a session outlives the
   * component that opened it: a hop to Monitors and back mounts a fresh
   * driver over a thread that is still on screen, still named in the
   * `/s/{id}` address bar, and still the session the analyst is in. The
   * store is what remembers across that gap (`sessionId` + `sessionLive`),
   * so the store tells the driver — see its `setDriver`.
   *
   * Synchronous and free: no request. The api driver posts `session_id` on
   * the next bootstrap and the server re-joins; the mock fixture has one
   * session and nothing to re-join, and says so by not implementing this.
   */
  continueSession?(sessionId: string): void;
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
  /**
   * `DELETE /v1/sessions/{sid}` — dismiss a session from the rail.
   *
   * A SOFT archive: nothing is deleted. The session keeps its
   * investigations, traces, frames and cohorts and stays fetchable by id,
   * so a link in a ticket does not 404 because somebody tidied a list; it
   * stops appearing in `GET /v1/sessions` and nothing more. Idempotent
   * server-side, so a double-click is not an error.
   *
   * Optional on the seam for the same reason `listSessions` is: the mock
   * fixture has no deployment and therefore no sessions to archive, and a
   * driver that cannot do this says so by not implementing it rather than
   * by pretending a row went away.
   */
  archiveSession?(sessionId: string): Promise<void>;
  /**
   * Which session an investigation belongs to
   * (`InvestigationResponse.session_id`, a required field).
   *
   * The `/i/{investigation_id}` permalink is a link to ONE answered turn,
   * and a turn is only honest inside the conversation that produced it —
   * its filters, its cohort and its referents were established by the
   * turns above it. So the link resolves to a session and opens that,
   * rather than rendering a findings card with no lineage over it.
   *
   * Optional on the seam like the reads beside it: the mock fixture has no
   * deployment, and a driver that cannot resolve an id says so by not
   * implementing this.
   */
  sessionForInvestigation?(investigationId: string): Promise<string>;

  /* -- Monitors: the writes behind the proactive surface --------------- */

  /**
   * `POST /v1/monitors/pins` — start monitoring an artifact, or a typed spec.
   *
   * On the seam rather than called directly for the reason `archiveSession`
   * is: a driver with no deployment behind it has nowhere to register a
   * monitor, and the honest way to say that is to not implement the method.
   * The affordance then explains that Monitors is the live API's, instead of
   * offering a control whose click would do nothing.
   *
   * Rejections propagate. The server refuses an illegal threshold unit
   * with a sentence naming the legal alternatives, and that sentence is
   * the point of the refusal.
   */
  createMonitorsPin?(request: CreatePinRequest): Promise<MonitorsPin>;
  /**
   * `GET /v1/monitors/pins` — the stored monitors, with their typed specs,
   * their window modes, their thresholds and their provenance.
   *
   * A different question from `GET /v1/monitors`: that says what each monitor
   * READ at this load, this says what each monitor IS. Two surfaces need the
   * second — the sensitivity editor, which re-registers a monitor over its
   * own stored spec, and the "Monitor this" affordance, which has to know
   * whether the artifact in front of the analyst is already monitored.
   */
  listMonitorsPins?(): Promise<MonitorsPin[]>;
  /** `DELETE /v1/monitors/pins/{pin_id}` — stop monitoring. A soft archive. */
  deleteMonitorsPin?(pinId: string): Promise<void>;
  /**
   * `PATCH /v1/monitors/leads/{anomaly_id}` — move a lead along its
   * lifecycle. Only the four a person may set; the platform's two verdicts
   * are refused with the reason, which the caller shows.
   */
  setLeadStatus?(anomalyId: string, status: LeadStatus, note: string): Promise<LeadState>;
}
