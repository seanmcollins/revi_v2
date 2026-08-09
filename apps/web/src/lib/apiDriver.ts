/**
 * The real TurnDriver — POST-SSE against the Revi API, behind the same
 * seam the mock driver uses. Nothing outside this file (and the thin
 * wiring in page.tsx) knows which driver is live.
 *
 *   POST /v1/sessions                  → session bootstrap (lazy, on first turn);
 *                                        with session_id it RE-JOINS that session
 *   GET  /v1/sessions                  → this tenant's sessions (the rail's list)
 *   POST /v1/sessions/{sid}/turns      → SSE TurnEvent stream (Accept: text/event-stream)
 *                                        or blocking TurnResponse (Accept: application/json)
 *   GET  /v1/investigations/{iid}      → completed turn (reconnect recovery)
 *   GET  /v1/sessions/{sid}/lineage    → session DAG
 *   GET  /v1/portfolio/latest          → pre-materialized portfolio (may 501)
 *   GET  /v1/health                    → connection heartbeat
 *
 * Failure discipline: `submit` never rejects. Server-sent errors arrive as
 * typed `error` events; transport failures recover via the idempotency key
 * (same-key JSON replay) or `GET /v1/investigations/{iid}`; unrecoverable
 * drops emit a visible error event and flip the connection pill offline.
 * Contract drift (missing required fields) is console.errored with the
 * exact field path and surfaced through `onContractDrift` — never a
 * silent blank UI.
 */

import {
  mapDebugTrace,
  newReceivedState,
  parseErrorEnvelope,
  parseInvestigationResponse,
  parsePortfolioSnapshot,
  parseSessionLineage,
  parseSessionList,
  parseSessionResponse,
  parseTurnFrame,
  parseTurnResponse,
  refinementsToWire,
  trackReceived,
  turnResponseToEvents,
  type ErrorEnvelopeData,
  type PortfolioSnapshotData,
  type ReceivedTurnState,
  type SessionBootstrap,
  type TurnResponseParse,
  type WirePin,
} from "@/lib/contract";
import type {
  ConnectionState,
  DriverKind,
  ResumedSession,
  ResumedTurn,
  TurnDriver,
  TurnSubmission,
} from "@/lib/driver";
import { parseCapabilities, settingsToWire, type DeploymentCapabilities } from "@/lib/settings";
import { SseHttpError, streamTurnEvents } from "@/lib/sse";
import type {
  DebugTrace,
  SessionLineageData,
  SessionListData,
  TurnEvent,
} from "@/lib/types";

export type { SessionBootstrap } from "@/lib/contract";

/* ------------------------------------------------------------------ */
/* Environment                                                         */
/* ------------------------------------------------------------------ */

/**
 * `NEXT_PUBLIC_REVI_DRIVER=mock|api` — default api (the live product). A
 * localStorage override ("revi-driver", set by the ⌘K palette) wins on the
 * client so the driver can be switched without a rebuild.
 */
export function resolveDriverKind(): DriverKind {
  if (typeof window !== "undefined") {
    try {
      const stored = window.localStorage.getItem("revi-driver");
      if (stored === "api" || stored === "mock") return stored;
    } catch {
      // Storage unavailable (privacy mode) — fall through to the env default.
    }
  }
  return process.env.NEXT_PUBLIC_REVI_DRIVER === "mock" ? "mock" : "api";
}

/** `NEXT_PUBLIC_REVI_API_URL` — default the API dev origin. */
export function apiBaseUrl(): string {
  return process.env.NEXT_PUBLIC_REVI_API_URL ?? "http://localhost:8000";
}

/** `NEXT_PUBLIC_REVI_TENANT` — explicit tenant context on session open (§12). */
export function resolveTenant(): string {
  return process.env.NEXT_PUBLIC_REVI_TENANT ?? "demo";
}

/* ------------------------------------------------------------------ */
/* Shared request plumbing                                             */
/* ------------------------------------------------------------------ */

interface RequestOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  onDrift?: (paths: string[], context: string) => void;
  signal?: AbortSignal;
}

/** A non-2xx JSON response; carries the decoded ErrorEnvelope when present. */
export class ApiRequestError extends Error {
  constructor(
    readonly status: number,
    readonly envelope: ErrorEnvelopeData | null,
    message: string,
  ) {
    super(message);
    this.name = "ApiRequestError";
  }
}

function envelopeFromText(text: string): { envelope: ErrorEnvelopeData | null; drift: string[] } {
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    return { envelope: null, drift: [] }; // proxy/error page — not our envelope
  }
  if (typeof payload !== "object" || payload === null) return { envelope: null, drift: [] };
  const result = parseErrorEnvelope(payload);
  if (result.ok) return { envelope: result.value, drift: [] };
  return { envelope: null, drift: result.missing };
}

async function requestJson(
  url: string,
  init: RequestInit,
  options: RequestOptions,
): Promise<unknown> {
  const doFetch = options.fetchImpl ?? fetch;
  const response = await doFetch(url, {
    ...init,
    headers: { Accept: "application/json", ...init.headers },
    signal: options.signal,
  });
  if (!response.ok) {
    const text = await response.text().catch(() => "");
    const { envelope, drift } = envelopeFromText(text);
    if (drift.length > 0) options.onDrift?.(drift, `error envelope (HTTP ${response.status})`);
    throw new ApiRequestError(
      response.status,
      envelope,
      envelope?.message ?? `request failed: HTTP ${response.status}`,
    );
  }
  return (await response.json()) as unknown;
}

/* ------------------------------------------------------------------ */
/* GET endpoints (TanStack Query fetchers)                             */
/* ------------------------------------------------------------------ */

/** `GET /v1/health` body, loosely read — see app.py's health() for the field names. */
export interface HealthDetail {
  ok: boolean;
  /** "scripted-demo" | "claude-agent-sdk" (or absent — untyped on the wire). */
  llmMode?: string;
  /** "memory" | "postgres" — which stores this deployment is actually running. */
  storeMode?: string;
  /** "dev-tenant" bypass vs a signed-token policy. */
  authMode?: string;
  /** Newest watermark the deployment can see (not necessarily this session's pin). */
  watermarkId?: string;
}

/**
 * `GET /v1/health` — liveness plus the wiring actually in effect. The
 * payload is untyped on the wire (no published schema), so every field is
 * read best-effort rather than treating a missing one as drift.
 */
export async function fetchHealthDetail(options: RequestOptions = {}): Promise<HealthDetail> {
  const doFetch = options.fetchImpl ?? fetch;
  try {
    const response = await doFetch(`${options.baseUrl ?? apiBaseUrl()}/v1/health`, {
      headers: { Accept: "application/json" },
      signal: options.signal,
    });
    if (!response.ok) return { ok: false };
    const body = (await response.json().catch(() => null)) as Record<string, unknown> | null;
    const readString = (key: string): string | undefined =>
      typeof body?.[key] === "string" ? (body[key] as string) : undefined;
    return {
      ok: true,
      ...(readString("llm_mode") ? { llmMode: readString("llm_mode") } : {}),
      ...(readString("store_mode") ? { storeMode: readString("store_mode") } : {}),
      ...(readString("auth_mode") ? { authMode: readString("auth_mode") } : {}),
      ...(readString("watermark") ? { watermarkId: readString("watermark") } : {}),
    };
  } catch {
    return { ok: false };
  }
}

/**
 * `GET /v1/capabilities` — the deployment's pinned pack, newest watermark,
 * LLM mode AND the admin bounds for session settings. The settings panel
 * renders controls from this and ONLY from this: a deployment that
 * publishes no model tiers gets no model tier control, rather than a
 * control whose every value would be refused.
 */
export async function fetchCapabilities(
  options: RequestOptions = {},
): Promise<DeploymentCapabilities> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(`${base}/v1/capabilities`, { method: "GET" }, options);
  return parseCapabilities(raw);
}

/**
 * `GET /v1/investigations/{iid}/trace` — a recorded turn's decision
 * breakdown. `null` means the server answered but the payload is not a
 * usable trace; a refused (`REVI_DEBUG_TRACE=0` → 400 POLICY_DENIED) or
 * unknown investigation throws `ApiRequestError`, so the caller can show
 * the deployment's own refusal instead of an empty panel.
 */
export async function fetchTrace(
  investigationId: string,
  options: RequestOptions = {},
): Promise<DebugTrace | null> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(
    `${base}/v1/investigations/${encodeURIComponent(investigationId)}/trace`,
    { method: "GET" },
    options,
  );
  return mapDebugTrace(raw);
}

export async function fetchHealth(options: RequestOptions = {}): Promise<boolean> {
  return (await fetchHealthDetail(options)).ok;
}

export type PortfolioFetchResult =
  | { kind: "ok"; snapshot: PortfolioSnapshotData }
  | { kind: "unavailable" };

/** `GET /v1/portfolio/latest`; HTTP 501 is a graceful "not built yet". */
export async function fetchPortfolioLatest(
  options: RequestOptions = {},
): Promise<PortfolioFetchResult> {
  const base = options.baseUrl ?? apiBaseUrl();
  let raw: unknown;
  try {
    raw = await requestJson(`${base}/v1/portfolio/latest`, { method: "GET" }, options);
  } catch (error) {
    if (error instanceof ApiRequestError && error.status === 501) return { kind: "unavailable" };
    throw error;
  }
  const { value, drift } = parsePortfolioSnapshot(raw);
  if (drift.length > 0) reportDriftToConsole(drift, "GET /v1/portfolio/latest", options.onDrift);
  if (!value) throw new Error("portfolio response failed contract validation");
  return { kind: "ok", snapshot: value };
}

/**
 * `GET /v1/sessions` — the caller tenant's sessions. The route takes no
 * tenant parameter: the token decides whose list this is, so there is
 * nothing here to scope and nothing a client could get wrong.
 */
export async function fetchSessionList(
  limit: number,
  options: RequestOptions = {},
): Promise<SessionListData> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(
    `${base}/v1/sessions?limit=${encodeURIComponent(String(limit))}`,
    { method: "GET" },
    options,
  );
  const { value, drift } = parseSessionList(raw);
  if (drift.length > 0) reportDriftToConsole(drift, "GET /v1/sessions", options.onDrift);
  if (!value) throw new Error("session list response failed contract validation");
  return value;
}

export async function fetchSessionLineage(
  sessionId: string,
  options: RequestOptions = {},
): Promise<SessionLineageData> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(
    `${base}/v1/sessions/${encodeURIComponent(sessionId)}/lineage`,
    { method: "GET" },
    options,
  );
  const { value, drift } = parseSessionLineage(raw);
  if (drift.length > 0) {
    reportDriftToConsole(drift, `GET /v1/sessions/${sessionId}/lineage`, options.onDrift);
  }
  if (!value) throw new Error("lineage response failed contract validation");
  return value;
}

export async function fetchInvestigation(
  investigationId: string,
  options: RequestOptions = {},
): Promise<TurnResponseParse> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(
    `${base}/v1/investigations/${encodeURIComponent(investigationId)}`,
    { method: "GET" },
    options,
  );
  const parse = parseInvestigationResponse(raw);
  if (parse.drift.length > 0) {
    reportDriftToConsole(parse.drift, `GET /v1/investigations/${investigationId}`, options.onDrift);
  }
  return parse;
}

function reportDriftToConsole(
  paths: string[],
  context: string,
  onDrift?: (paths: string[], context: string) => void,
): void {
  for (const path of paths) {
    console.error(`[revi] contract drift — missing required field "${path}" (${context})`);
  }
  onDrift?.(paths, context);
}

/* ------------------------------------------------------------------ */
/* The driver                                                          */
/* ------------------------------------------------------------------ */

export interface ApiDriverOptions {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
  /** Recovery attempts after a dropped stream (default 3). */
  recoveryAttempts?: number;
  /** Base backoff between recovery attempts, in ms (default 750). */
  recoveryDelayMs?: number;
  onSession?: (session: SessionBootstrap) => void;
  onConnectionState?: (state: ConnectionState, detail?: string) => void;
  onContractDrift?: (paths: string[], context: string) => void;
}

/** The response parsed as JSON but is missing required fields. */
class ContractDriftError extends Error {
  constructor() {
    super("response failed contract validation");
    this.name = "ContractDriftError";
  }
}

const sleep = (ms: number): Promise<void> =>
  ms <= 0 ? Promise.resolve() : new Promise((resolve) => setTimeout(resolve, ms));

function isAbort(error: unknown, signal?: AbortSignal): boolean {
  if (signal?.aborted) return true;
  return error instanceof DOMException && error.name === "AbortError";
}

export class ApiDriver implements TurnDriver {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;
  private readonly recoveryAttempts: number;
  private readonly recoveryDelayMs: number;
  private session: SessionBootstrap | null = null;
  private sessionPromise: Promise<SessionBootstrap> | null = null;

  constructor(private readonly options: ApiDriverOptions = {}) {
    this.baseUrl = options.baseUrl ?? apiBaseUrl();
    this.fetchImpl = options.fetchImpl ?? fetch;
    this.recoveryAttempts = options.recoveryAttempts ?? 3;
    this.recoveryDelayMs = options.recoveryDelayMs ?? 750;
  }

  /** `GET /v1/health` — used by the startup check and the heartbeat loop. */
  async checkHealth(): Promise<boolean> {
    return fetchHealth({ baseUrl: this.baseUrl, fetchImpl: this.fetchImpl });
  }

  /**
   * "New chat": discard whatever session is cached and eagerly mint a
   * fresh one (`POST /v1/sessions`, no session_id) — the SAME
   * onSession/onConnectionState path a lazy bootstrap uses, so the
   * watermark pill updates the moment this resolves. Never rejects:
   * unreachable/malformed is tolerated by leaving the cache cleared, so
   * the NEXT turn's `ensureSession()` retries from scratch instead of
   * silently continuing the old backend session.
   */
  async newSession(): Promise<void> {
    this.session = null;
    this.sessionPromise = null;
    try {
      await this.createSession();
    } catch (error) {
      this.session = null;
      this.sessionPromise = null;
      if (!(error instanceof ApiRequestError) && !(error instanceof ContractDriftError)) {
        // The server never answered — genuinely unreachable. A rejected or
        // drift-y response already reported itself inside createSession()
        // without implying the connection is down.
        this.options.onConnectionState?.("offline", "new session failed");
      }
    }
  }

  /**
   * A recorded turn's decision breakdown. Errors propagate: a deployment
   * with `REVI_DEBUG_TRACE=0` refuses with POLICY_DENIED and that refusal
   * is what debug mode should show, not a silent empty panel.
   */
  async getTrace(investigationId: string): Promise<DebugTrace | null> {
    return fetchTrace(investigationId, this.requestOptions());
  }

  /** `GET /v1/capabilities` — the deployment's bounds for the settings panel. */
  async capabilities(): Promise<DeploymentCapabilities> {
    return fetchCapabilities({ baseUrl: this.baseUrl, fetchImpl: this.fetchImpl });
  }

  /** `GET /v1/sessions` — this tenant's sessions, newest activity first. */
  async listSessions(limit = 50): Promise<SessionListData> {
    return fetchSessionList(limit, this.requestOptions());
  }

  /**
   * Switch to an existing session and rebuild its thread.
   *
   * Three published reads, in order: re-join (`POST /v1/sessions` carrying
   * `session_id`, which returns the session with its ORIGINAL pin rather
   * than re-pinning it to today's watermark), the lineage DAG for the
   * questions and their order, then each node's stored investigation for
   * its findings. Errors propagate: a thread missing one of its turns
   * misrepresents the session, so a failed read fails the switch visibly
   * instead of quietly serving a shorter history.
   */
  async resumeSession(sessionId: string): Promise<ResumedSession> {
    // Drop the cached session first: if any step below fails, the next
    // turn must bootstrap cleanly rather than continue a half-switched one.
    this.session = null;
    this.sessionPromise = null;
    const session = await this.openSession({
      tenant: resolveTenant(),
      session_id: sessionId,
    });
    const options = this.requestOptions();
    const lineage = await fetchSessionLineage(sessionId, options);
    const parses = await Promise.all(
      lineage.nodes.map((node) => fetchInvestigation(node.investigationId, options)),
    );
    const turns: ResumedTurn[] = [];
    lineage.nodes.forEach((node, index) => {
      const parse = parses[index];
      if (!parse.value) return; // drift already reported against its path
      turns.push({
        investigationId: node.investigationId,
        question: node.question || node.label,
        events: turnResponseToEvents(parse.value),
      });
    });
    return { ...session, turns };
  }

  private requestOptions(): RequestOptions {
    return {
      baseUrl: this.baseUrl,
      fetchImpl: this.fetchImpl,
      onDrift: (paths, context) => this.reportDrift(paths, context),
    };
  }

  private reportDrift(paths: string[], context: string): void {
    reportDriftToConsole(paths, context, this.options.onContractDrift);
  }

  /** Create the session on first use; concurrent callers share one POST. */
  private async ensureSession(signal?: AbortSignal): Promise<SessionBootstrap> {
    if (this.session) return this.session;
    this.sessionPromise ??= this.createSession(signal);
    try {
      return await this.sessionPromise;
    } catch (error) {
      this.sessionPromise = null; // allow a retry on the next turn
      throw error;
    }
  }

  private async createSession(signal?: AbortSignal): Promise<SessionBootstrap> {
    return this.openSession({ tenant: resolveTenant() }, signal);
  }

  /**
   * `POST /v1/sessions`. With no `session_id` this mints a fresh session;
   * with one it RE-JOINS that session — the server's own semantics, which
   * return the existing record and its original watermark pin rather than
   * opening a second session over the same id.
   */
  private async openSession(
    body: Record<string, unknown>,
    signal?: AbortSignal,
  ): Promise<SessionBootstrap> {
    const raw = await requestJson(
      `${this.baseUrl}/v1/sessions`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      {
        fetchImpl: this.fetchImpl,
        onDrift: (paths, context) => this.reportDrift(paths, context),
        signal,
      },
    );
    const result = parseSessionResponse(raw);
    if (!result.ok) {
      this.reportDrift(result.missing, "POST /v1/sessions");
      throw new ContractDriftError();
    }
    this.session = result.value;
    this.options.onSession?.(result.value);
    this.options.onConnectionState?.("online");
    return result.value;
  }

  private turnBody(
    submission: TurnSubmission,
    idempotencyKey: string,
    correlationId: string,
  ): Record<string, unknown> {
    // `settingsToWire` returns null when every control sits at its default,
    // and the key is then omitted entirely — the pre-settings body, byte
    // for byte. Out-of-bounds values go out verbatim: the server refuses
    // with POLICY_DENIED and the analyst reads the bound they broke.
    const settings = submission.settings ? settingsToWire(submission.settings) : null;
    return {
      idempotency_key: idempotencyKey,
      correlation_id: correlationId,
      ...(settings !== null ? { settings } : {}),
      ...(submission.utterance !== undefined ? { utterance: submission.utterance } : {}),
      // The UI names operators in PascalCase; the wire does not. Posting
      // the UI spelling is a 422, so translate at the boundary.
      ...(submission.refinements && submission.refinements.length > 0
        ? { refinements: refinementsToWire(submission.refinements) }
        : {}),
      // A typed FIRST turn (a portfolio card's drill handle, or a chart
      // click with no prior answer to refine): a new investigation, not an
      // edit to one.
      ...(submission.spec !== undefined ? { spec: submission.spec } : {}),
      // Which card this drill came from. The server reconciles the card's
      // figure against the answer's and publishes both; omitting it is how
      // two screens ended up disagreeing by 9.9% in silence.
      ...(submission.anomalyRef !== undefined ? { anomaly_ref: submission.anomalyRef } : {}),
      ...(submission.clarificationResponse !== undefined
        ? { clarification_response: submission.clarificationResponse }
        : {}),
      ...(submission.reAnchor ? { re_anchor: true } : {}),
    };
  }

  async submit(
    submission: TurnSubmission,
    emit: (event: TurnEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    let session: SessionBootstrap;
    try {
      session = await this.ensureSession(signal);
    } catch (error) {
      if (isAbort(error, signal)) return;
      this.emitRequestFailure(emit, error, undefined, "session bootstrap");
      return;
    }

    const idempotencyKey = crypto.randomUUID();
    const correlationId = crypto.randomUUID();
    const body = this.turnBody(submission, idempotencyKey, correlationId);
    const url = `${this.baseUrl}/v1/sessions/${encodeURIComponent(session.sessionId)}/turns`;

    const pin: WirePin = { watermark: session.watermark, pack: session.pack };
    const received = newReceivedState();
    let sawTerminal = false;
    let investigationId: string | undefined;

    const emitMapped = (event: TurnEvent): void => {
      trackReceived(received, event);
      if (event.type === "turn_complete" || event.type === "error") sawTerminal = true;
      emit(event);
    };

    const onFrame = (frame: { kind: string; data: Record<string, unknown> }): void => {
      // Harvest an investigation id from ANY frame that carries one — it
      // unlocks GET-based recovery before the turn completes.
      if (typeof frame.data.investigation_id === "string") {
        investigationId = frame.data.investigation_id;
      }
      if (frame.kind === "turn_complete") {
        // The stream is progress; this frame is the authoritative answer.
        const parse = parseTurnResponse(frame.data, pin);
        if (parse.drift.length > 0) this.reportDrift(parse.drift, 'sse frame "turn_complete"');
        if (!parse.value) {
          sawTerminal = true;
          emit({
            type: "error",
            code: "CONTRACT_DRIFT",
            message:
              'The server sent a malformed "turn_complete" frame — required fields are missing (paths in the console).',
            correlationId,
          });
          return;
        }
        for (const event of turnResponseToEvents(parse.value, received)) emitMapped(event);
        return;
      }
      const result = parseTurnFrame(frame.kind, frame.data, pin);
      if (result === null) return; // unpublished kind — skipped, not drift
      if (!result.ok) {
        this.reportDrift(result.missing, `sse frame "${frame.kind}"`);
        if (frame.kind === "error") {
          // The terminal frame itself is malformed — the turn must not hang.
          sawTerminal = true;
          emit({
            type: "error",
            code: "CONTRACT_DRIFT",
            message:
              'The server sent a malformed "error" frame — required fields are missing (paths in the console).',
            correlationId,
          });
        }
        return; // drop the malformed frame, never render partial garbage
      }
      emitMapped(result.value);
    };

    try {
      await streamTurnEvents(url, body, onFrame, {
        signal,
        fetchImpl: this.fetchImpl,
      });
      if (sawTerminal) {
        this.options.onConnectionState?.("online");
        return;
      }
      // Stream closed cleanly but never completed — recover the turn.
    } catch (error) {
      if (isAbort(error, signal)) return;
      if (error instanceof SseHttpError) {
        // The server answered — connection is fine; surface its error.
        this.emitHttpEnvelope(emit, error, correlationId);
        return;
      }
      // Transport drop mid-stream → fall through to recovery.
    }

    await this.recoverTurn({ emit: emitMapped, url, body, pin, received, correlationId, signal }, () => ({
      sawTerminal,
      investigationId,
    }));
  }

  /**
   * Reconnect tolerance: the stream dropped before `turn_complete`. The
   * turn keeps executing server-side, so poll for its result — via
   * `GET /v1/investigations/{iid}` when we know the id, else by replaying
   * the SAME idempotency key with `Accept: application/json` (the server
   * dedupes; this never runs the turn twice). Deltas already received on
   * the live stream are skipped, so the answer stays duplicate-free.
   */
  private async recoverTurn(
    args: {
      emit: (event: TurnEvent) => void;
      url: string;
      body: Record<string, unknown>;
      pin: WirePin;
      received: ReceivedTurnState;
      correlationId: string;
      signal?: AbortSignal;
    },
    state: () => { sawTerminal: boolean; investigationId: string | undefined },
  ): Promise<void> {
    const requestOptions: RequestOptions = {
      baseUrl: this.baseUrl,
      fetchImpl: this.fetchImpl,
      onDrift: (paths, context) => this.reportDrift(paths, context),
      signal: args.signal,
    };

    for (let attempt = 1; attempt <= this.recoveryAttempts; attempt += 1) {
      if (args.signal?.aborted || state().sawTerminal) return;
      try {
        const { investigationId } = state();
        const parse = investigationId
          ? await fetchInvestigation(investigationId, requestOptions)
          : await this.replayTurnJson(args.url, args.body, args.pin, requestOptions);
        if (!parse.value) {
          // Core fields missing — retrying will not help; fail visibly.
          args.emit({
            type: "error",
            code: "CONTRACT_DRIFT",
            message:
              "Recovered the turn, but the response is missing required fields (paths in the console).",
            correlationId: args.correlationId,
          });
          return;
        }
        for (const event of turnResponseToEvents(parse.value, args.received)) {
          args.emit(event);
        }
        this.options.onConnectionState?.("online");
        return;
      } catch (error) {
        if (isAbort(error, args.signal)) return;
        if (error instanceof ApiRequestError) {
          // The server answered with an error — surface it, stop retrying.
          this.emitRequestFailure(args.emit, error, args.correlationId, "turn recovery");
          return;
        }
        // Still unreachable — back off and try again.
        await sleep(this.recoveryDelayMs * attempt);
      }
    }

    this.options.onConnectionState?.("offline", "lost connection mid-turn");
    args.emit({
      type: "error",
      code: "API_UNREACHABLE",
      message:
        "Lost the connection while streaming this turn and could not recover it. The investigation may still complete server-side — it will be in the lineage once the API is reachable again.",
      correlationId: args.correlationId,
    });
  }

  /** Same idempotency key, blocking JSON — the server returns the one TurnResponse. */
  private async replayTurnJson(
    url: string,
    body: Record<string, unknown>,
    pin: WirePin,
    options: RequestOptions,
  ): Promise<TurnResponseParse> {
    const raw = await requestJson(
      url,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
      options,
    );
    const parse = parseTurnResponse(raw, pin);
    if (parse.drift.length > 0) this.reportDrift(parse.drift, "turn replay (application/json)");
    return parse;
  }

  private emitHttpEnvelope(
    emit: (event: TurnEvent) => void,
    error: SseHttpError,
    correlationId: string,
  ): void {
    const { envelope, drift } = envelopeFromText(error.bodyText);
    if (drift.length > 0) this.reportDrift(drift, `error envelope (HTTP ${error.status})`);
    emit({
      type: "error",
      code: envelope?.code ?? `HTTP_${error.status}`,
      message: envelope?.message ?? `The API rejected this turn (HTTP ${error.status}).`,
      correlationId: envelope?.correlationId ?? correlationId,
      ...(envelope?.subcode ? { subcode: envelope.subcode } : {}),
    });
  }

  private emitRequestFailure(
    emit: (event: TurnEvent) => void,
    error: unknown,
    correlationId: string | undefined,
    context: string,
  ): void {
    if (error instanceof ApiRequestError) {
      emit({
        type: "error",
        code: error.envelope?.code ?? `HTTP_${error.status}`,
        message: error.envelope?.message ?? `The API rejected this request (HTTP ${error.status}).`,
        ...(error.envelope?.correlationId ?? correlationId
          ? { correlationId: error.envelope?.correlationId ?? correlationId }
          : {}),
        ...(error.envelope?.subcode ? { subcode: error.envelope.subcode } : {}),
      });
      return;
    }
    if (error instanceof ContractDriftError) {
      // The server answered — reachable, but the response is unusable.
      emit({
        type: "error",
        code: "CONTRACT_DRIFT",
        message:
          "The API responded, but required fields are missing (paths in the console). The UI cannot render this turn.",
        ...(correlationId ? { correlationId } : {}),
      });
      return;
    }
    this.options.onConnectionState?.("offline", `${context} failed`);
    emit({
      type: "error",
      code: "API_UNREACHABLE",
      message: `Could not reach the Revi API (${context}). Check that the server is running at ${this.baseUrl}.`,
      ...(correlationId ? { correlationId } : {}),
    });
  }
}
