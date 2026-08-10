/**
 * The real TurnDriver — POST-SSE against the Revi API, behind the same
 * seam the mock driver uses. Nothing outside this file (and the thin
 * wiring in page.tsx) knows which driver is live.
 *
 *   POST /v1/sessions                  → session bootstrap (lazy, on the first
 *                                        turn — never on "new chat"); with
 *                                        session_id it RE-JOINS that session
 *   GET  /v1/sessions                  → this tenant's sessions (the rail's list)
 *   POST /v1/sessions/{sid}/turns      → SSE TurnEvent stream (Accept: text/event-stream)
 *                                        or blocking TurnResponse (Accept: application/json)
 *   GET  /v1/investigations/{iid}      → completed turn (reconnect recovery)
 *   GET  /v1/sessions/{sid}/lineage    → session DAG
 *   GET  /v1/portfolio/latest          → pre-materialized portfolio
 *                                        (always 200; an empty feed is a
 *                                        snapshot with status "empty")
 *   DELETE /v1/sessions/{sid}          → soft-archive a session (204)
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
  monitorToWire,
  type ErrorEnvelopeData,
  type LeadStatus,
  type PortfolioSnapshotData,
  type ReceivedTurnState,
  type SessionBootstrap,
  type TurnResponseParse,
  type MonitorModel,
  type WirePin,
} from "@/lib/contract";
import {
  mapLeadState,
  mapMonitorsPin,
  parseBrief,
  parseMonitors,
  type BriefData,
  type LeadState,
  type MonitorsData,
  type MonitorsPin,
} from "@/lib/monitors";
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

/**
 * Send the request and raise the server's own envelope on a non-2xx.
 *
 * Split from `requestJson` because a 204 has no body to decode: calling
 * `.json()` on one throws, and a route whose whole success signal IS the
 * empty response would fail on the happy path.
 */
async function requestOk(
  url: string,
  init: RequestInit,
  options: RequestOptions,
): Promise<Response> {
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
  return response;
}

async function requestJson(
  url: string,
  init: RequestInit,
  options: RequestOptions,
): Promise<unknown> {
  const response = await requestOk(url, init, options);
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

/**
 * `GET /v1/portfolio/latest`.
 *
 * There is no "this deployment serves no worklist" branch here any more.
 * The route is unconditional and always answers 200; a load with nothing
 * detected comes back as a NORMAL snapshot carrying `status: "empty"`,
 * zero items and the feed's own `PORTFOLIO_FEED_EMPTY` warning. The 501
 * this used to catch was never reachable — it was a client hedging
 * against a deployment mode the server does not have, and it turned the
 * one state that IS real (a quiet data load) into a claim about the
 * deployment. Emptiness is now the snapshot's own word for itself and
 * the panel renders it as such.
 */
export async function fetchPortfolioLatest(
  options: RequestOptions = {},
): Promise<PortfolioSnapshotData> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(`${base}/v1/portfolio/latest`, { method: "GET" }, options);
  const { value, drift } = parsePortfolioSnapshot(raw);
  if (drift.length > 0) reportDriftToConsole(drift, "GET /v1/portfolio/latest", options.onDrift);
  if (!value) throw new Error("portfolio response failed contract validation");
  return value;
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

/**
 * `pin` is the SESSION's watermark and pack: `InvestigationResponse`
 * publishes a watermark id at most, never its load time or the pack
 * version, so the stored context header is only renderable when the caller
 * has the session those facts belong to. Without one the parse is the same
 * minus the header — never a header pinned to invented dates.
 */
export async function fetchInvestigation(
  investigationId: string,
  options: RequestOptions = {},
  pin?: WirePin,
): Promise<TurnResponseParse> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(
    `${base}/v1/investigations/${encodeURIComponent(investigationId)}`,
    { method: "GET" },
    options,
  );
  const parse = parseInvestigationResponse(raw, pin);
  if (parse.drift.length > 0) {
    reportDriftToConsole(parse.drift, `GET /v1/investigations/${investigationId}`, options.onDrift);
  }
  return parse;
}

/* ------------------------------------------------------------------ */
/* Monitors — the proactive surface's reads and writes                    */
/* ------------------------------------------------------------------ */

/**
 * `GET /v1/monitors` — every active monitor, evaluated at the newest load.
 *
 * The route EVALUATES on request: a deployment whose sweep interval is 0,
 * or one nobody has opened since the load landed, walks the Monitors here.
 * That is why this read is slower than the other GETs and why the surface
 * says it is walking rather than showing a spinner with no account of
 * itself.
 */
export async function fetchMonitors(options: RequestOptions = {}): Promise<MonitorsData> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(`${base}/v1/monitors`, { method: "GET" }, options);
  const { value, drift } = parseMonitors(raw);
  if (drift.length > 0) reportDriftToConsole(drift, "GET /v1/monitors", options.onDrift);
  if (!value) throw new Error("monitors response failed contract validation");
  return value;
}

/** `GET /v1/monitors/brief` — what changed at this load, gated and counted. */
export async function fetchMonitorsBrief(options: RequestOptions = {}): Promise<BriefData> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(`${base}/v1/monitors/brief`, { method: "GET" }, options);
  const { value, drift } = parseBrief(raw);
  if (drift.length > 0) reportDriftToConsole(drift, "GET /v1/monitors/brief", options.onDrift);
  if (!value) throw new Error("brief response failed contract validation");
  return value;
}

/** `GET /v1/monitors/pins` — the stored monitors, with their specs and thresholds. */
export async function fetchMonitorsPins(options: RequestOptions = {}): Promise<MonitorsPin[]> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(`${base}/v1/monitors/pins`, { method: "GET" }, options);
  const pins: MonitorsPin[] = [];
  const list = typeof raw === "object" && raw !== null ? (raw as { pins?: unknown }).pins : undefined;
  for (const entry of Array.isArray(list) ? list : []) {
    const pin = mapMonitorsPin(entry);
    if (pin !== null) pins.push(pin);
  }
  return pins;
}

/**
 * What a "Monitor this" click sends. Exactly one of `investigationId` or
 * `spec` — a body carrying both is refused server-side rather than
 * resolved, because either resolution order would be a guess about which
 * the caller meant.
 */
export interface CreatePinRequest {
  investigationId?: string;
  /** The artifact within that investigation — a finding referent or chart id. */
  referent?: string;
  spec?: Record<string, unknown>;
  presentation?: "chart" | "finding" | "worklist_slice" | "scalar";
  label?: string;
  monitor?: MonitorModel;
}

/**
 * `POST /v1/monitors/pins` — add a monitor.
 *
 * Errors propagate untouched. The server refuses an illegal threshold unit
 * with a sentence naming the legal alternatives, and that sentence is the
 * whole value of the refusal — a client that caught it and said "could not
 * create monitor" would replace the one thing that teaches with the one
 * thing that does not.
 */
export async function createMonitorsPin(
  request: CreatePinRequest,
  options: RequestOptions = {},
): Promise<MonitorsPin> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(
    `${base}/v1/monitors/pins`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        ...(request.investigationId !== undefined
          ? { investigation_id: request.investigationId }
          : {}),
        ...(request.referent !== undefined ? { referent: request.referent } : {}),
        ...(request.spec !== undefined ? { spec: request.spec } : {}),
        ...(request.presentation !== undefined ? { presentation: request.presentation } : {}),
        ...(request.label !== undefined ? { label: request.label } : {}),
        ...(request.monitor !== undefined ? { monitor: monitorToWire(request.monitor) } : {}),
      }),
    },
    options,
  );
  const pin = mapMonitorsPin(raw);
  if (pin === null) {
    reportDriftToConsole(["pin_id"], "POST /v1/monitors/pins", options.onDrift);
    throw new Error("the monitor was created but the response does not name it");
  }
  return pin;
}

/**
 * `DELETE /v1/monitors/pins/{pin_id}` — un-monitor.
 *
 * A SOFT archive server-side, like every other dismissal on this platform:
 * the evaluated history a brief already published stays readable and a
 * permalink into a tile's investigation does not 404 because somebody
 * tidied their Monitors.
 */
export async function deleteMonitorsPin(
  pinId: string,
  options: RequestOptions = {},
): Promise<void> {
  const base = options.baseUrl ?? apiBaseUrl();
  await requestOk(
    `${base}/v1/monitors/pins/${encodeURIComponent(pinId)}`,
    { method: "DELETE" },
    options,
  );
}

/**
 * `PATCH /v1/monitors/leads/{anomaly_id}` — move one lead along its
 * lifecycle.
 *
 * Only the four human-settable statuses are accepted. Asking for
 * `resolved_confirmed` is refused with the reason, and the refusal is
 * shown: confirmation is a measurement across two loads, and a lead that
 * could be confirmed by assertion would make the whole verification path
 * decorative.
 */
export async function patchLeadStatus(
  anomalyId: string,
  status: LeadStatus,
  note: string,
  options: RequestOptions = {},
): Promise<LeadState> {
  const base = options.baseUrl ?? apiBaseUrl();
  const raw = await requestJson(
    `${base}/v1/monitors/leads/${encodeURIComponent(anomalyId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status, note }),
    },
    options,
  );
  const lead = mapLeadState(raw);
  if (lead === null) {
    reportDriftToConsole(["anomaly_id"], `PATCH /v1/monitors/leads/${anomalyId}`, options.onDrift);
    throw new Error("the status changed but the response does not name the lead");
  }
  return lead;
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
   * "New chat": discard the cached session. That is the whole operation —
   * no request is made.
   *
   * This used to eagerly `POST /v1/sessions`, which minted a real backend
   * session for every click of a button whose most common outcome is that
   * the analyst types nothing. The server has no such compulsion: it mints
   * a session when a turn arrives and never on its own, so the eager POST
   * was the client manufacturing rows in the tenant's session list — empty,
   * titleless, and indistinguishable in the rail from work someone did.
   * `ensureSession()` already creates one on the first turn, which is the
   * only moment a session has anything to be about.
   *
   * It also cannot fail, so there is nothing here to tolerate: no request
   * means no unreachable-API branch and no chance of flipping the
   * connection pill offline over a button press. What the UI must do
   * instead is be honest about the gap — between this call and the first
   * turn there is no session, so nothing may show a watermark pinned by
   * the session that was just abandoned (see the store's `newChat`).
   */
  async newSession(): Promise<void> {
    this.session = null;
    this.sessionPromise = null;
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
   * `POST /v1/monitors/pins` — start monitoring. Errors propagate so the
   * server's own refusal reaches the analyst verbatim.
   */
  async createMonitorsPin(request: CreatePinRequest): Promise<MonitorsPin> {
    return createMonitorsPin(request, this.requestOptions());
  }

  /** `GET /v1/monitors/pins` — the stored monitors and what each one IS. */
  async listMonitorsPins(): Promise<MonitorsPin[]> {
    return fetchMonitorsPins(this.requestOptions());
  }

  /** `DELETE /v1/monitors/pins/{pin_id}` — stop monitoring (a soft archive). */
  async deleteMonitorsPin(pinId: string): Promise<void> {
    return deleteMonitorsPin(pinId, this.requestOptions());
  }

  /** `PATCH /v1/monitors/leads/{anomaly_id}` — move a lead along its lifecycle. */
  async setLeadStatus(anomalyId: string, status: LeadStatus, note: string): Promise<LeadState> {
    return patchLeadStatus(anomalyId, status, note, this.requestOptions());
  }

  /**
   * `DELETE /v1/sessions/{sid}` — dismiss a session from the rail.
   *
   * A SOFT archive, and the wording matters because the control is next to
   * a list of somebody's work: nothing is deleted. The session keeps its
   * investigations, traces, frames and cohorts and stays fetchable by id,
   * so a link someone pasted into a ticket does not 404 because a rail got
   * tidied — it simply stops appearing in `GET /v1/sessions`.
   *
   * Errors propagate. An archive that silently failed would leave the row
   * gone from the screen and present on the server, which is the one
   * outcome a list of someone's work cannot have; the caller puts the row
   * back and shows the server's own sentence.
   */
  async archiveSession(sessionId: string): Promise<void> {
    // `requestOk`, not `requestJson`: a 204 has no body, and the empty
    // response IS the success signal.
    await requestOk(
      `${this.baseUrl}/v1/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
      this.requestOptions(),
    );
  }

  /**
   * The session an investigation belongs to — the `/i/{iid}` permalink's
   * one server read.
   *
   * Deliberately NOT `fetchInvestigation`: that parses a whole turn against
   * the contract and needs the session's pin to do it, and the pin is the
   * very thing this call exists to find. All that is wanted here is the id,
   * which is a required field on the response, so the raw body is read for
   * that one string and the rebuild happens through `resumeSession` like
   * every other way into a session.
   */
  async sessionForInvestigation(investigationId: string): Promise<string> {
    const raw = await requestJson(
      `${this.baseUrl}/v1/investigations/${encodeURIComponent(investigationId)}`,
      { method: "GET" },
      this.requestOptions(),
    );
    const sessionId =
      typeof raw === "object" && raw !== null
        ? (raw as { session_id?: unknown }).session_id
        : undefined;
    if (typeof sessionId !== "string" || sessionId === "") {
      this.reportDrift(["session_id"], `GET /v1/investigations/${investigationId}`);
      throw new Error(
        "That investigation exists but does not say which session it belongs to, so there is nothing to open.",
      );
    }
    return sessionId;
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
    // The session's own pin, so a stored `context_header` can be rendered
    // with the load time and pack version the investigation payload does
    // not carry. Both come from the re-join above, i.e. from the server.
    const pin: WirePin = { watermark: session.watermark, pack: session.pack };
    const parses = await Promise.all(
      lineage.nodes.map((node) => fetchInvestigation(node.investigationId, options, pin)),
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
      // Ask this turn to carry the ranked worklist. Additive by contract:
      // the turn investigates exactly what it would have, and the list
      // rides alongside — so a lane chip re-queries the LIST rather than
      // refining the answer it sits under. Only the keys actually chosen
      // are sent; `additionalProperties: false` on WorklistQuery makes a
      // stray `lane: undefined` a 422 rather than a no-op.
      ...(submission.worklist !== undefined
        ? {
            worklist: {
              ...(submission.worklist.limit !== undefined
                ? { limit: submission.worklist.limit }
                : {}),
              ...(submission.worklist.lane !== undefined
                ? { lane: submission.worklist.lane }
                : {}),
            },
          }
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
          ? await fetchInvestigation(investigationId, requestOptions, args.pin)
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
