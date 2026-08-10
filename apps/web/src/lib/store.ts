/**
 * Zustand session store. Turn answers are assembled exclusively by the pure
 * reducer `applyEventToAnswer` — the mock driver and the future real SSE
 * driver both feed the same TurnEvent pipeline, so wiring the API later is
 * swapping the driver only.
 */

import { create } from "zustand";

import type { CreatePinRequest } from "@/lib/apiDriver";
import type { LeadStatus, MonitorDeclaration, MonitorRefusal, WorklistData } from "@/lib/contract";
import type { LeadState, MonitorsPin } from "@/lib/monitors";
import { envDriverKind } from "@/lib/driver";
import type {
  ConnectionState,
  DriverKind,
  TurnDriver,
  TurnSubmission,
} from "@/lib/driver";
import { REFERENCE_QUESTIONS } from "@/lib/mock/reference";
import {
  DEFAULT_SETTINGS,
  isDefaultSettings,
  loadSettings,
  saveSettings,
  type DeploymentCapabilities,
  type SessionSettings,
} from "@/lib/settings";
import {
  STAGE_ORDER,
  type AnomalyReconciliation,
  type MetricDisplay,
  type SessionSummary,
  type TurnUsage,
  type PackVersionRef,
  type ChartSpec,
  type ClarificationData,
  type ContextHeaderData,
  type DataWatermark,
  type DebugTrace,
  type DefinitionCardData,
  type EvidenceBundle,
  type EvidenceGrade,
  type Finding,
  type InterpretationData,
  type MetricProvenance,
  type Refinement,
  type ReferentId,
  type StageEvent,
  type StageId,
  type TurnClass,
  type TurnEvent,
  type WarningEvent,
} from "@/lib/types";

/* ------------------------------------------------------------------ */
/* Answer state (per turn)                                             */
/* ------------------------------------------------------------------ */

export interface StageStatus {
  stage: StageId;
  state: "pending" | "active" | "done" | "skipped";
  detail?: string;
  probesDone?: number;
  probesTotal?: number;
  cacheHits?: number;
}

export type AnswerStatus = "streaming" | "complete" | "clarification" | "error";

/** How this turn's decision trace was obtained (debug mode only). */
export type TraceFetchState = "idle" | "loading" | "error";

export interface AnswerState {
  turnClass?: TurnClass;
  stages: StageStatus[];
  /** Server id for the turn — the handle `GET .../trace` is read by. */
  investigationId?: string;
  /** The turn's decision trace, when debug was on (or fetched afterwards). */
  debug?: DebugTrace;
  traceFetch?: TraceFetchState;
  /** The server's own words when a trace read was refused. */
  traceError?: string;
  header?: ContextHeaderData;
  interpretation?: InterpretationData;
  findings: Finding[];
  charts: ChartSpec[];
  narrative: string;
  clarification?: ClarificationData;
  warnings: WarningEvent[];
  evidence?: EvidenceBundle;
  definition?: DefinitionCardData;
  answerGrade?: EvidenceGrade;
  /** Whose definition these numbers are — see `MetricProvenance`. */
  metric?: MetricProvenance;
  /**
   * Set when this turn drilled a portfolio card AND the request carried
   * its `anomaly_ref`: the card's published figure beside this platform's
   * re-derivation of it, with the verdict on whether they agree.
   */
  anomalyReconciliation?: AnomalyReconciliation;
  /**
   * The ranked worklist this turn carried (`TurnAnswer.worklist`), when
   * its interpretation resolved the pack's governed worklist routing — or
   * when the request asked for it outright. Rendered inline on the answer;
   * see `AnswerWorklist`. Present on clarifications as well as answers.
   */
  worklist?: WorklistData;
  /**
   * `TurnAnswer.monitor` — this turn declared a monitor in words and the
   * platform registered it, so the answer below is also the baseline the
   * monitor starts from. Present on that one turn class and no other; the
   * card renders the server's confirmation sentence rather than composing
   * one, because every figure in it is a fact from the answer beside it.
   */
  monitor?: MonitorDeclaration;
  /**
   * `TurnAnswer.monitor_refused` — this turn read as a monitor declaration and
   * the monitor was NOT created.
   *
   * The one warning on this answer that changes what the analyst does
   * tomorrow, and until now it reached the screen as nothing: the server
   * appends the refusal to `warnings` after `warnings_v2` is built, and
   * the client prefers the structured list. It is carried here as its own
   * field so the card can render it where the confirmation would have been
   * rather than hoping it survives a warning list.
   */
  monitorRefused?: MonitorRefusal;
  /**
   * The governed display-name corrections this turn's measures carry.
   * Already applied to finding titles and chart titles at the seam; kept
   * here so the answer can also show the caveat that travels with a name.
   */
  metricDisplay?: MetricDisplay[];
  cacheHits: number;
  status: AnswerStatus;
  /**
   * `subcode` refines the copy where one code covers two failures;
   * `usage` is what the failed turn still spent, which is a real number a
   * card that shows only the refusal quietly leaves out.
   */
  error?: { code: string; message: string; subcode?: string; usage?: TurnUsage };
  /**
   * Rebuilt from stored server state when this session was re-opened,
   * rather than monitored as it streamed. What the server kept comes back —
   * findings, warnings, the evidence bundle projected from the turn's
   * recorded trace, and charts rebuilt from the frames it persisted. The
   * stage timeline and the composed narrative were never stored, so this
   * turn says where it came from instead of replaying a pipeline nobody
   * observed or inventing prose nobody wrote.
   */
  rehydrated?: boolean;
}

export function emptyAnswer(): AnswerState {
  return {
    stages: STAGE_ORDER.map((stage) => ({ stage, state: "pending" })),
    findings: [],
    charts: [],
    narrative: "",
    warnings: [],
    cacheHits: 0,
    status: "streaming",
  };
}

function applyStage(stages: StageStatus[], event: StageEvent): StageStatus[] {
  const idx = STAGE_ORDER.indexOf(event.stage);
  return stages.map((s, i) => {
    if (i < idx && (s.state === "pending" || s.state === "active")) {
      return { ...s, state: "done" };
    }
    if (i !== idx) return s;
    const state: StageStatus["state"] =
      event.status === "completed" ? "done" : event.status === "skipped" ? "skipped" : "active";
    return {
      ...s,
      state,
      detail: event.detail ?? s.detail,
      probesDone: event.probesDone ?? s.probesDone,
      probesTotal: event.probesTotal ?? s.probesTotal,
      cacheHits: event.cacheHits ?? s.cacheHits,
    };
  });
}

/** Pure event reducer — unit tested without React. */
export function applyEventToAnswer(answer: AnswerState, event: TurnEvent): AnswerState {
  switch (event.type) {
    case "stage": {
      const stages = applyStage(answer.stages, event);
      const cacheHits =
        event.cacheHits !== undefined && event.cacheHits > answer.cacheHits
          ? event.cacheHits
          : answer.cacheHits;
      return { ...answer, stages, cacheHits };
    }
    case "context_header":
      return { ...answer, header: event.header, turnClass: event.turnClass };
    case "interpretation":
      return { ...answer, interpretation: event.interpretation };
    // Upsert, not append. A referent identifies a finding, so a second
    // event carrying one the answer already has is the SAME finding said
    // again — and the terminal frame does say some of them again, because
    // the governed display names that correct their titles are only known
    // once the turn is finished (a streamed `finding` frame reads "…
    // dnfb dollars" while the completed answer's narrative reads
    // "discharged not final billed"). Appending would have rendered the
    // card twice; keying by referent replaces it with the corrected copy.
    case "finding": {
      const index = answer.findings.findIndex(
        (f) => f.referent.value === event.finding.referent.value,
      );
      if (index === -1) return { ...answer, findings: [...answer.findings, event.finding] };
      const findings = [...answer.findings];
      findings[index] = event.finding;
      return { ...answer, findings };
    }
    case "chart_spec": {
      const index = answer.charts.findIndex((c) => c.id === event.spec.id);
      if (index === -1) return { ...answer, charts: [...answer.charts, event.spec] };
      const charts = [...answer.charts];
      charts[index] = event.spec;
      return { ...answer, charts };
    }
    case "narrative_delta":
      return { ...answer, narrative: answer.narrative + event.text };
    case "clarification":
      return { ...answer, clarification: event.clarification, status: "clarification" };
    case "warning":
      return { ...answer, warnings: [...answer.warnings, event] };
    case "evidence":
      return { ...answer, evidence: event.evidence };
    case "definition_card":
      return { ...answer, definition: event.definition };
    case "turn_complete": {
      const stages = answer.stages.map((s) =>
        s.state === "active" ? { ...s, state: "done" as const } : s,
      );
      return {
        ...answer,
        stages,
        investigationId: event.investigationId || answer.investigationId,
        debug: event.debug ?? answer.debug,
        answerGrade: event.answerGrade ?? answer.answerGrade,
        metric: event.metric ?? answer.metric,
        anomalyReconciliation: event.anomalyReconciliation ?? answer.anomalyReconciliation,
        worklist: event.worklist ?? answer.worklist,
        monitor: event.monitor ?? answer.monitor,
        monitorRefused: event.monitorRefused ?? answer.monitorRefused,
        metricDisplay: event.metricDisplay ?? answer.metricDisplay,
        status:
          event.status === "clarification_required"
            ? "clarification"
            : event.status === "failed"
              ? "error"
              : "complete",
      };
    }
    case "error":
      return {
        ...answer,
        status: "error",
        error: {
          code: event.code,
          message: event.message,
          ...(event.subcode ? { subcode: event.subcode } : {}),
          ...(event.usage ? { usage: event.usage } : {}),
        },
      };
    default:
      return answer;
  }
}

/* ------------------------------------------------------------------ */
/* Session store                                                       */
/* ------------------------------------------------------------------ */

export interface TurnRecord {
  id: string;
  index: number;
  submission: TurnSubmission;
  answer: AnswerState;
}

export interface ReferentEntry {
  referent: ReferentId;
  turnId: string;
  label: string;
  impactCents?: number;
  statement?: string;
}

export interface PendingRefinement {
  refinement: Refinement;
  sourceTurnId?: string;
  sourceReferent?: string;
  at: number;
}

export type FeedbackChoice = "yes" | "fix" | "review";

export interface WatermarkBannerState {
  visible: boolean;
  newWatermark?: DataWatermark;
  decision?: "stay_pinned" | "re_anchor";
}

export interface ConnectionStatus {
  mode: DriverKind;
  state: ConnectionState;
  detail?: string;
  /** From `GET /v1/health`'s `llm_mode` — "scripted-demo" | "claude-agent-sdk". */
  llmMode?: string;
  /** `store_mode` — "memory" | "postgres". */
  storeMode?: string;
  /** `auth_mode` — a dev-tenant bypass is visible, not inferred. */
  authMode?: string;
  /** `watermark` — the newest load the deployment can see. */
  newestWatermarkId?: string;
  /**
   * True once `GET /v1/health` has actually answered (either way). Until
   * it has, this client knows nothing about the deployment's LLM mode,
   * store or auth — and a "degraded mode" badge rendered from that
   * ignorance is a claim about the server made before the server spoke.
   */
  healthChecked?: boolean;
}

/** Lifecycle of the `GET /v1/capabilities` read the settings panel needs. */
export type CapabilitiesState = "idle" | "loading" | "ready" | "unavailable";

/** Lifecycle of the `GET /v1/sessions` read the session rail renders. */
export type SessionListState = "idle" | "loading" | "ready" | "unavailable";

interface SessionState {
  sessionId: string;
  watermark: DataWatermark;
  pack: PackVersionRef;
  /** True once a real API session has been created and adopted. */
  sessionLive: boolean;
  connection: ConnectionStatus;
  /** Field paths missing from server responses — drives the drift banner. */
  contractDrift: string[];
  /** Send `re_anchor: true` with the next turn (analyst chose re-anchor). */
  pendingReAnchor: boolean;
  turns: TurnRecord[];
  referents: Record<string, ReferentEntry>;
  pendingRefinements: PendingRefinement[];
  feedback: Record<string, FeedbackChoice>;
  watermarkBanner: WatermarkBannerState;
  /** Preview toggle for failure-state components (demo only). */
  showFailurePreview: boolean;
  drawerTurnId: string | null;
  focusedReferent: string | null;
  streamingTurnId: string | null;
  /** True for the whole "Replay reference demo" run — newChat() through the last turn. */
  replaying: boolean;
  /** 1-indexed position in the reference conversation while replaying (e.g. "2/5"). */
  replayProgress: { index: number; total: number } | null;
  /** True while `newChat()`'s `driver.newSession()` call is in flight. */
  newChatPending: boolean;
  driver: TurnDriver | null;

  /* -- the session list (GET /v1/sessions) ------------------------- */
  /** The tenant's sessions as the server lists them — never local guesses. */
  sessions: SessionSummary[];
  /** Every session the tenant owns, so a capped list can say it is capped. */
  sessionsTotal: number;
  sessionsState: SessionListState;
  /** Why the list could not be read — shown instead of inventing rows. */
  sessionsError: string | null;
  /** The session being switched to, while its thread is rebuilding. */
  switchingSessionId: string | null;
  /** The server's own words when a switch failed. */
  switchError: string | null;

  /* -- Monitors (the proactive surface) ------------------------------ */
  /**
   * Monitors registered from THIS browser session, keyed by the artifact
   * they were started from (`turnId:referent`, `turnId:chart:id`,
   * `turnId:worklist`).
   *
   * Local, and deliberately so: the affordance has to switch to "Monitoring ·
   * baseline 12.4%" the instant the server confirms, and re-reading the
   * whole pin list to discover a pin this client just created would show a
   * button that still says "Monitor this" over a monitor that exists. It is
   * keyed by artifact rather than by pin id because the affordance is on
   * the artifact and that is the question it asks: is THIS thing monitored.
   *
   * It is not a cache of the tenant's monitors. Monitors itself reads
   * `GET /v1/monitors` and this map has nothing to say about it.
   */
  monitors: Record<string, MonitorsPin>;
  /**
   * Every monitor this TENANT has, as the server lists them.
   *
   * The store above remembers what this browser registered; this is what
   * already existed. Both are needed and neither replaces the other: the
   * first is what makes a click feel immediate, and the second is what
   * stops an analyst who opens a permalink the next morning from being
   * offered "Monitor this" over a monitor that has been running all week —
   * which would put two tiles over one measure.
   *
   * On the STORE rather than behind a query hook because the component
   * that needs it is a leaf on the answer path (`MonitorThis`, in a chart's
   * action row and on every finding), and a `useQuery` there would drag a
   * `QueryClientProvider` into every surface and every test that mounts an
   * answer.
   */
  knownMonitors: MonitorsPin[];
  monitorsLoaded: boolean;
  /** A `loadMonitors` read is in flight — the single-flight latch. */
  monitorsLoading: boolean;
  /**
   * Why this tenant's monitors could not be read — the server's own
   * sentence, or this client's when the failure was not the server's.
   *
   * It exists because the alternative was measured live and is worse than
   * any error copy: `GET /v1/monitors/pins` 500'd off one malformed monitor,
   * this store swallowed it in an empty `catch {}`, and every tile's
   * settings menu rendered "Change what it takes to brief you" as a
   * DISABLED button with nothing on screen to say why — a control that
   * cannot be operated and cannot be diagnosed, on all thirty tiles at
   * once. A failed read is a fact about the deployment and it is stated;
   * it is never presented as an affordance that simply does not work.
   */
  monitorsError: string | null;
  /** The artifact key a monitor is being created for, while the POST is in flight. */
  monitorPendingKey: string | null;
  /**
   * The server's refusal, verbatim, and the artifact it was about.
   *
   * Kept together because the sentence names a threshold unit that is
   * illegal for a particular contract, and a refusal shown next to the
   * wrong artifact would name a unit the reader's metric does have.
   */
  monitorError: { key: string; message: string } | null;
  /**
   * Lead lifecycle states this browser has changed, keyed by anomaly id.
   *
   * The portfolio snapshot carries `lead_status` as of the load it was
   * built at; a status changed thirty seconds ago is newer than that, so a
   * card prefers this. It holds the SERVER's answer — never the status
   * that was asked for — because `resolved_claimed` may come back with a
   * verification note that says the claim could not be verified, and the
   * card must show what happened rather than what was clicked.
   */
  leadStates: Record<string, LeadState>;
  leadPendingId: string | null;
  leadError: { anomalyId: string; message: string } | null;

  /* -- internal settings (see lib/settings.ts) --------------------- */
  /** What the NEXT turn will be submitted under. Persisted in localStorage. */
  settings: SessionSettings;
  settingsOpen: boolean;
  /** The deployment's published bounds; null until read (or if unreadable). */
  capabilities: DeploymentCapabilities | null;
  capabilitiesState: CapabilitiesState;
  /** Why the bounds could not be read — shown instead of inventing controls. */
  capabilitiesError: string | null;
  /**
   * The most recent `POLICY_DENIED` refusal, verbatim. Settings are refused
   * loudly, never clamped, so the panel repeats the server's own sentence
   * next to the control that caused it.
   */
  lastPolicyDenial: string | null;

  setDriver: (driver: TurnDriver) => void;
  /** Hydrate persisted settings on the client (never during SSR). */
  hydrateSettings: () => void;
  patchSettings: (patch: Partial<SessionSettings>) => void;
  resetSettings: () => void;
  openSettings: () => void;
  closeSettings: () => void;
  /** Read `GET /v1/capabilities` through the driver seam; never rejects. */
  loadCapabilities: () => Promise<void>;
  /** Read `GET /v1/sessions` through the driver seam; never rejects. */
  loadSessions: () => Promise<void>;
  /**
   * Switch to another session: re-join it server-side and rebuild its
   * thread from the lineage plus each turn's stored investigation. A no-op
   * while a turn is streaming, a replay is running, or a switch is already
   * in flight — and for the session already open. Never rejects: a failed
   * switch leaves the thread cleared and names the failure.
   */
  switchSession: (sessionId: string) => Promise<void>;
  /**
   * Open the session an investigation belongs to — what
   * `/i/{investigation_id}` resolves to. Never rejects: a link to a turn
   * this tenant cannot read names the failure on the rail's error line
   * rather than leaving a blank workspace.
   */
  openInvestigation: (investigationId: string) => Promise<void>;
  /**
   * Dismiss a session from the rail (`DELETE /v1/sessions/{sid}`) — a SOFT
   * archive server-side: nothing is deleted and the session stays
   * fetchable by id. The row leaves optimistically and is PUT BACK if the
   * server refuses, because a row that vanished from the screen while
   * surviving on the server is the one failure a list of somebody's work
   * cannot have. Never rejects: the server's own sentence is surfaced.
   */
  archiveSession: (sessionId: string) => Promise<void>;
  /**
   * Start monitoring an artifact (`POST /v1/monitors/pins`).
   *
   * `key` identifies the artifact on screen, so the affordance beside it
   * can switch to its monitoring state and no other affordance does. Never
   * rejects: a driver with no deployment says so, and the server's own
   * refusal — "a `points` threshold cannot be applied to a money
   * contract; the legal units here are …" — is kept verbatim and shown
   * next to the control that caused it.
   */
  createMonitor: (key: string, request: CreatePinRequest) => Promise<void>;
  /**
   * Read this tenant's monitors (`GET /v1/monitors/pins`) so an affordance
   * can tell "not monitored" from "monitored, by somebody, last Tuesday".
   *
   * Single-flight and idempotent: every `MonitorThis` on a page asks for it
   * on mount, and one read answers all of them. Never rejects — a failed
   * read leaves `monitorsLoaded` false and the affordance offers the monitor,
   * which is the harmless direction to fail in (the server refuses a
   * duplicate spec on its own terms; a hidden control cannot be recovered
   * from at all) — and it RECORDS the failure in `monitorsError` so every
   * surface that gates on a pin can say why it has none.
   *
   * `force` re-reads a list that already loaded (or already failed): it is
   * what the retry beside the error sentence does, and without it the
   * single-flight latch would make "try again" a no-op.
   */
  loadMonitors: (options?: { force?: boolean }) => Promise<void>;
  /**
   * Stop monitoring (`DELETE /v1/monitors/pins/{pin_id}`) — a soft archive
   * server-side. `key` is the artifact whose affordance goes back to
   * offering the monitor.
   */
  removeMonitor: (key: string, pinId: string) => Promise<void>;
  /**
   * Move a lead along its lifecycle (`PATCH /v1/monitors/leads/{id}`).
   *
   * NOT optimistic, unlike `archiveSession`. What comes back is not the
   * status that was sent: claiming a resolution makes the platform
   * re-derive the lead's exposure and answer with a verification note that
   * may say it could not verify. Showing the requested status first and
   * correcting it a moment later would flash a confirmation the platform
   * never gave.
   */
  setLeadStatus: (anomalyId: string, status: LeadStatus, note: string) => Promise<void>;
  /**
   * Fetch a turn's decision trace after the fact (`GET .../trace`) — how
   * debug mode explains a turn answered before the toggle was flipped.
   */
  loadTrace: (turnId: string) => Promise<void>;
  setConnection: (patch: Partial<ConnectionStatus>) => void;
  adoptSession: (session: {
    sessionId: string;
    watermark: DataWatermark;
    pack: PackVersionRef;
  }) => void;
  reportContractDrift: (paths: string[]) => void;
  dismissContractDrift: () => void;
  submit: (submission: TurnSubmission) => Promise<void>;
  emitRefinement: (
    refinement: Refinement,
    source?: { turnId?: string; referent?: string },
  ) => void;
  setFeedback: (turnId: string, choice: FeedbackChoice) => void;
  openDrawer: (turnId: string) => void;
  closeDrawer: () => void;
  focusReferent: (referent: string | null) => void;
  simulateWatermarkRefresh: () => void;
  resolveWatermarkBanner: (decision: "stay_pinned" | "re_anchor") => void;
  toggleFailurePreview: () => void;
  reset: () => void;
  /**
   * "New chat": clear the thread (§`reset`) AND abandon the driver's
   * cached session so the next turn opens a genuinely new one — a bare
   * `reset()` clears the thread but NOT the api driver's cached session,
   * which would silently continue the OLD backend session (inherited
   * referents/lineage). Single-flight: a no-op while a turn is streaming
   * or another `newChat()` is already in flight.
   */
  newChat: () => Promise<void>;
  /**
   * "Replay reference demo": starts a genuinely fresh session (`newChat()`)
   * and submits the reference conversation's five utterances one at a time
   * through the normal `submit` path, awaiting each turn's full completion
   * before sending the next — they are follow-ups, so order matters and
   * nothing is pre-computed. If a turn lands on a clarification or an
   * error, the replay stops there; it never fakes a continuation. Single-
   * flight, like `newChat()`.
   */
  replayReference: () => Promise<void>;
}

export const INITIAL_WATERMARK: DataWatermark = {
  id: "wm_003",
  loadedAt: "2026-08-03 04:10",
  newestDataDate: "2026-08-02",
};

const REFRESHED_WATERMARK: DataWatermark = {
  id: "wm_004",
  loadedAt: "2026-08-04 04:07",
  newestDataDate: "2026-08-03",
};

let turnCounter = 0;
function nextTurnId(): string {
  turnCounter += 1;
  return `turn_${turnCounter}`;
}

function registerReferents(
  referents: Record<string, ReferentEntry>,
  turnId: string,
  event: TurnEvent,
): Record<string, ReferentEntry> {
  if (event.type === "finding") {
    const f = event.finding;
    return {
      ...referents,
      [f.referent.value]: {
        referent: f.referent,
        turnId,
        label: f.title,
        impactCents: f.impactCents,
        statement: f.statement,
      },
    };
  }
  if (event.type === "chart_spec") {
    const next = { ...referents };
    for (const row of event.spec.rows) {
      if (row.referent && !next[row.referent]) {
        next[row.referent] = {
          referent: { value: row.referent, kind: "table_row" },
          turnId,
          label: row.label,
        };
      }
    }
    return next;
  }
  return referents;
}

export const INITIAL_PACK: PackVersionRef = { packId: "base-rcm", version: "1.0.0" };

export const useSessionStore = create<SessionState>((set, get) => ({
  sessionId: "sess_demo_001",
  watermark: INITIAL_WATERMARK,
  pack: INITIAL_PACK,
  sessionLive: false,
  // The env default, not "mock": the store used to open on
  // `{mode: "mock", state: "online"}` and the workspace corrected it in an
  // effect one paint later, so an api-mode deployment flashed BOTH the
  // "Mock driver" pill and the amber "Demo script mode" badge before its
  // first health poll. `envDriverKind()` reads only `process.env`, so the
  // server render and the first client render agree.
  connection: { mode: envDriverKind(), state: "connecting" },
  contractDrift: [],
  pendingReAnchor: false,
  turns: [],
  referents: {},
  pendingRefinements: [],
  feedback: {},
  watermarkBanner: { visible: false },
  showFailurePreview: false,
  drawerTurnId: null,
  focusedReferent: null,
  streamingTurnId: null,
  replaying: false,
  replayProgress: null,
  newChatPending: false,
  driver: null,

  sessions: [],
  sessionsTotal: 0,
  sessionsState: "idle",
  sessionsError: null,
  switchingSessionId: null,
  switchError: null,

  monitors: {},
  knownMonitors: [],
  monitorsLoaded: false,
  monitorsLoading: false,
  monitorsError: null,
  monitorPendingKey: null,
  monitorError: null,
  leadStates: {},
  leadPendingId: null,
  leadError: null,

  settings: DEFAULT_SETTINGS,
  settingsOpen: false,
  capabilities: null,
  capabilitiesState: "idle",
  capabilitiesError: null,
  lastPolicyDenial: null,

  setDriver: (driver) => set({ driver }),

  hydrateSettings: () => set({ settings: loadSettings() }),

  patchSettings: (patch) => {
    const next = { ...get().settings, ...patch };
    saveSettings(next);
    // A control just changed; the last refusal was about the OLD value and
    // repeating it would misattribute it to the new one.
    set({ settings: next, lastPolicyDenial: null });
  },

  resetSettings: () => {
    saveSettings(DEFAULT_SETTINGS);
    set({ settings: DEFAULT_SETTINGS, lastPolicyDenial: null });
  },

  openSettings: () => {
    set({ settingsOpen: true });
    void get().loadCapabilities();
  },

  closeSettings: () => set({ settingsOpen: false }),

  loadCapabilities: async () => {
    const { driver, capabilitiesState } = get();
    if (capabilitiesState === "loading") return;
    if (!driver?.capabilities) {
      // The mock fixture has no deployment to describe. Saying so is the
      // honest answer; rendering controls anyway would offer knobs with
      // nothing behind them.
      set({
        capabilities: null,
        capabilitiesState: "unavailable",
        capabilitiesError:
          "This driver has no deployment to read bounds from — settings apply to the live API only.",
      });
      return;
    }
    set({ capabilitiesState: "loading", capabilitiesError: null });
    try {
      const capabilities = await driver.capabilities();
      set({ capabilities, capabilitiesState: "ready", capabilitiesError: null });
    } catch (error) {
      set({
        capabilities: null,
        capabilitiesState: "unavailable",
        capabilitiesError:
          error instanceof Error
            ? error.message
            : "Could not read this deployment's settings bounds.",
      });
    }
  },

  loadSessions: async () => {
    const { driver } = get();
    // No driver yet (the first paint, before page.tsx wires one): that is
    // "not asked", not "unavailable" — claiming the latter would blame the
    // deployment for the app's own startup order.
    if (!driver) {
      set({ sessionsState: "idle" });
      return;
    }
    if (!driver.listSessions) {
      // The mock fixture has no deployment, so it has no sessions. Saying
      // that is the honest empty state; three plausible titles would not be.
      set({
        sessions: [],
        sessionsTotal: 0,
        sessionsState: "unavailable",
        sessionsError:
          "This driver has no deployment to list sessions from — the session list is the live API's.",
      });
      return;
    }
    if (get().sessionsState !== "ready") set({ sessionsState: "loading" });
    try {
      const page = await driver.listSessions();
      set({
        sessions: page.sessions,
        sessionsTotal: page.total,
        sessionsState: "ready",
        sessionsError: null,
      });
    } catch (error) {
      set({
        sessions: [],
        sessionsTotal: 0,
        sessionsState: "unavailable",
        sessionsError:
          error instanceof Error ? error.message : "Could not read this tenant's sessions.",
      });
    }
  },

  switchSession: async (sessionId) => {
    const { driver, streamingTurnId, replaying, newChatPending, switchingSessionId } = get();
    if (!driver || streamingTurnId || replaying || newChatPending || switchingSessionId) return;
    // "Already open" means the thread is ON SCREEN, not merely that the
    // store holds the id. Those came apart on the first click after a page
    // load: a workspace can hold a session id with none of its turns
    // rebuilt (a reload, a portfolio drill-through, a turn submitted
    // before the rail was read), and the click that would have hydrated it
    // returned here in silence — leaving the cold-start hero, with its
    // eight live guide chips, over a session the analyst believes they
    // just opened. The next chip click then starts an unrelated question
    // inside it. Gating on the thread instead of on the id makes the click
    // do the thing it looks like it does; a session already rebuilt on
    // screen still costs nothing.
    if (sessionId === get().sessionId && get().turns.length > 0) return;
    if (!driver.resumeSession) {
      set({
        switchError: "This driver cannot re-open a session — the live API can.",
      });
      return;
    }
    set({ switchingSessionId: sessionId, switchError: null });
    // Clear the thread BEFORE the fetch: the rail already shows the target
    // as selected, and leaving the old session's answers on screen under a
    // new title would attribute them to a session that never asked them.
    get().reset();
    if (get().connection.mode === "api") get().setConnection({ state: "connecting" });
    try {
      const resumed = await driver.resumeSession(sessionId);
      get().adoptSession({
        sessionId: resumed.sessionId,
        watermark: resumed.watermark,
        pack: resumed.pack,
      });
      set((state) => {
        let referents = state.referents;
        const turns: TurnRecord[] = resumed.turns.map((turn, index) => {
          const id = nextTurnId();
          let answer = emptyAnswer();
          for (const event of turn.events) {
            answer = applyEventToAnswer(answer, event);
            referents = registerReferents(referents, id, event);
          }
          return {
            id,
            index,
            submission: { utterance: turn.question },
            // Everything below the answer card keys off this: a rebuilt
            // turn shows its real findings and skips the stage timeline it
            // cannot honestly draw.
            answer: { ...answer, rehydrated: true },
          };
        });
        return { turns, referents };
      });
    } catch (error) {
      set({
        switchError:
          error instanceof Error
            ? error.message
            : "Could not re-open that session. Its turns stay on the server.",
      });
    } finally {
      set({ switchingSessionId: null });
      void get().loadSessions();
    }
  },

  openInvestigation: async (investigationId) => {
    const { driver } = get();
    if (!driver) return;
    if (!driver.sessionForInvestigation) {
      set({
        switchError: "This driver cannot open an investigation link — the live API can.",
      });
      return;
    }
    set({ switchError: null });
    let sessionId: string;
    try {
      sessionId = await driver.sessionForInvestigation(investigationId);
    } catch (error) {
      set({
        switchError:
          error instanceof Error
            ? error.message
            : `Could not open investigation ${investigationId}.`,
      });
      return;
    }
    // From here it is an ordinary session open: the turn is only honest
    // inside the conversation that produced it.
    await get().switchSession(sessionId);
  },

  archiveSession: async (sessionId) => {
    const { driver, streamingTurnId, switchingSessionId, sessions, sessionsTotal } = get();
    // Archiving the session a turn is streaming into, or one mid-switch,
    // would pull the row out from under a request already in flight.
    if (!driver || streamingTurnId || switchingSessionId) return;
    if (!driver.archiveSession) {
      set({ switchError: "This driver cannot archive a session — the live API can." });
      return;
    }
    const row = sessions.find((s) => s.sessionId === sessionId);
    if (!row) return;
    // Optimistic: the row goes now, because the click should feel like the
    // thing it is. The exact list and total are kept so a refusal can
    // restore the rail to precisely what it was — including the ORDER,
    // which a re-fetch would not guarantee mid-activity.
    set({
      sessions: sessions.filter((s) => s.sessionId !== sessionId),
      sessionsTotal: Math.max(0, sessionsTotal - 1),
      switchError: null,
    });
    try {
      await driver.archiveSession(sessionId);
    } catch (error) {
      // Put it back, exactly where it was. A row that left the screen while
      // surviving on the server is a list quietly lying about what exists.
      set({
        sessions,
        sessionsTotal,
        switchError:
          error instanceof Error
            ? error.message
            : "Could not archive that session — it is still in this tenant's list.",
      });
      return;
    }
    // The archived session was the one on screen. The thread stays exactly
    // where it is: those answers were really computed and are still
    // fetchable by id — archiving hides a row, it does not retract an
    // investigation. What is no longer true is that the rail has a
    // selectable row for it, so nothing here re-selects one.
  },

  loadMonitors: async (options) => {
    const { driver, monitorsLoaded, monitorsLoading } = get();
    if (monitorsLoading) return;
    if (monitorsLoaded && options?.force !== true) return;
    if (!driver?.listMonitorsPins) {
      // Not a failure and not silence either: this driver has no
      // deployment to list monitors from (the mock fixture, or a store that
      // has not been given a driver yet), and saying so is what stops a
      // surface from blaming the server for it.
      set({
        monitorsError:
          driver === null
            ? "This browser has not connected to a deployment yet, so no monitor settings could be read."
            : "This browser is running the mock fixture, which stores no monitors — their settings live on the deployment.",
      });
      return;
    }
    // Single-flight across every affordance on the page. The latch is set
    // before the await, so twelve fact rows mounting at once make one GET.
    set({ monitorsLoading: true, monitorsError: null });
    try {
      const pins = await driver.listMonitorsPins();
      set({ knownMonitors: pins, monitorsLoaded: true, monitorsError: null });
    } catch (error) {
      // Left unloaded on purpose. The affordance then offers the monitor,
      // and the server is the authority on whether that is a duplicate —
      // a control hidden because a list could not be read is a control
      // nobody can recover from.
      //
      // But NOT left unsaid. The server's own sentence is kept so the
      // menu that needs a pin can explain itself instead of drawing a
      // grey button: measured live, one malformed monitor 500'd this read
      // tenant-wide and every tile's settings control went dead with no
      // sentence anywhere on the page.
      set({
        monitorsError:
          error instanceof Error && error.message !== ""
            ? error.message
            : "The request for this tenant's monitors did not complete.",
      });
    } finally {
      set({ monitorsLoading: false });
    }
  },

  createMonitor: async (key, request) => {
    const { driver, monitorPendingKey } = get();
    if (monitorPendingKey !== null) return;
    if (!driver?.createMonitorsPin) {
      set({
        monitorError: {
          key,
          message: "This driver has no deployment to register a monitor with — Monitors is the live API's.",
        },
      });
      return;
    }
    set({ monitorPendingKey: key, monitorError: null });
    try {
      const pin = await driver.createMonitorsPin(request);
      set((state) => ({
        monitors: { ...state.monitors, [key]: pin },
        // Into the tenant's list too, so a second surface showing the same
        // artifact (the Evidence rail's copy of a finding) does not offer
        // to monitor what was just monitored.
        knownMonitors: [...state.knownMonitors, pin],
        monitorError: null,
      }));
    } catch (error) {
      // The server's own sentence. An illegal threshold unit is refused
      // with the legal alternatives named, and that naming is the entire
      // value of the refusal — "could not create monitor" would throw away
      // the one thing that tells the analyst what to type instead.
      set({
        monitorError: {
          key,
          message:
            error instanceof Error
              ? error.message
              : "Could not start this monitor. Nothing was registered.",
        },
      });
    } finally {
      set({ monitorPendingKey: null });
    }
  },

  removeMonitor: async (key, pinId) => {
    const { driver, monitorPendingKey } = get();
    if (monitorPendingKey !== null) return;
    if (!driver?.deleteMonitorsPin) {
      set({
        monitorError: { key, message: "This driver cannot remove a monitor — the live API can." },
      });
      return;
    }
    set({ monitorPendingKey: key, monitorError: null });
    try {
      await driver.deleteMonitorsPin(pinId);
      set((state) => {
        const monitors = { ...state.monitors };
        delete monitors[key];
        return {
          monitors,
          knownMonitors: state.knownMonitors.filter((pin) => pin.pinId !== pinId),
          monitorError: null,
        };
      });
    } catch (error) {
      set({
        monitorError: {
          key,
          message:
            error instanceof Error
              ? error.message
              : "Could not stop this monitor — it is still in your Monitors.",
        },
      });
    } finally {
      set({ monitorPendingKey: null });
    }
  },

  setLeadStatus: async (anomalyId, status, note) => {
    const { driver, leadPendingId } = get();
    if (leadPendingId !== null) return;
    if (!driver?.setLeadStatus) {
      set({
        leadError: {
          anomalyId,
          message: "This driver has no deployment to record a status on — Monitors is the live API's.",
        },
      });
      return;
    }
    set({ leadPendingId: anomalyId, leadError: null });
    try {
      const lead = await driver.setLeadStatus(anomalyId, status, note);
      // The SERVER's answer, not the requested status. Claiming a
      // resolution makes the platform re-derive this lead's exposure and
      // answer with what it measured, including "could not verify".
      set((state) => ({ leadStates: { ...state.leadStates, [anomalyId]: lead }, leadError: null }));
    } catch (error) {
      set({
        leadError: {
          anomalyId,
          message:
            error instanceof Error
              ? error.message
              : `Could not change ${anomalyId}'s status. It is unchanged on the server.`,
        },
      });
    } finally {
      set({ leadPendingId: null });
    }
  },

  loadTrace: async (turnId) => {
    const { driver, turns } = get();
    const turn = turns.find((t) => t.id === turnId);
    const investigationId = turn?.answer.investigationId;
    if (!investigationId) return;
    const patch = (answer: Partial<AnswerState>): void =>
      set((state) => ({
        turns: state.turns.map((t) =>
          t.id === turnId ? { ...t, answer: { ...t.answer, ...answer } } : t,
        ),
      }));
    if (!driver?.getTrace) {
      patch({
        traceFetch: "error",
        traceError: "This driver cannot read recorded traces — the live API can.",
      });
      return;
    }
    patch({ traceFetch: "loading", traceError: undefined });
    try {
      const trace = await driver.getTrace(investigationId);
      if (trace === null) {
        patch({ traceFetch: "error", traceError: "The server has no recorded trace for this turn." });
        return;
      }
      patch({ debug: trace, traceFetch: "idle", traceError: undefined });
    } catch (error) {
      // The deployment's own refusal (REVI_DEBUG_TRACE=0 → POLICY_DENIED)
      // is the useful message here — repeated verbatim.
      patch({
        traceFetch: "error",
        traceError: error instanceof Error ? error.message : "Could not read this turn's trace.",
      });
    }
  },

  setConnection: (patch) =>
    set((state) => ({ connection: { ...state.connection, ...patch } })),

  adoptSession: (session) =>
    set({
      sessionId: session.sessionId,
      watermark: session.watermark,
      pack: session.pack,
      sessionLive: true,
    }),

  reportContractDrift: (paths) =>
    set((state) => ({
      contractDrift: Array.from(new Set([...state.contractDrift, ...paths])),
    })),

  dismissContractDrift: () => set({ contractDrift: [] }),

  submit: async (submission) => {
    const { driver, streamingTurnId, pendingReAnchor, settings, switchingSessionId } = get();
    // A turn submitted mid-switch would land in whichever session won the
    // race — the one being left or the one being joined.
    if (!driver || streamingTurnId || switchingSessionId) return;
    // Settings ride on the turn, not the session record: the refusal for an
    // out-of-bounds value lands on the turn that used it. At their defaults
    // the key is dropped entirely by `settingsToWire`, so the default path
    // is unchanged.
    const effective: TurnSubmission = {
      ...submission,
      ...(pendingReAnchor ? { reAnchor: true } : {}),
      ...(isDefaultSettings(settings) ? {} : { settings }),
    };
    if (pendingReAnchor) set({ pendingReAnchor: false });
    const id = nextTurnId();
    const record: TurnRecord = {
      id,
      index: get().turns.length,
      submission: effective,
      answer: emptyAnswer(),
    };
    set((state) => ({ turns: [...state.turns, record], streamingTurnId: id }));
    try {
      await driver.submit(effective, (event) => {
        set((state) => ({
          turns: state.turns.map((t) =>
            t.id === id ? { ...t, answer: applyEventToAnswer(t.answer, event) } : t,
          ),
          referents: registerReferents(state.referents, id, event),
          // A settings bound was broken. The server's sentence names the
          // bound and what would satisfy it, so it is kept verbatim and
          // shown next to the controls as well as on the answer.
          lastPolicyDenial:
            event.type === "error" && event.code === "POLICY_DENIED"
              ? event.message
              : state.lastPolicyDenial,
        }));
      });
    } finally {
      set({ streamingTurnId: null });
      // Gestures made while streaming were queued — flush them as one
      // refinement turn now that the pipeline is free (api mode only).
      const after = get();
      if (after.connection.mode === "api" && after.pendingRefinements.length > 0) {
        const refinements = after.pendingRefinements.map((p) => p.refinement);
        set({ pendingRefinements: [] });
        void after.submit({ refinements });
      }
      // The turn just changed this session's title (if it was the first),
      // its turn count and its last activity — every column the rail
      // renders. One small GET per turn keeps the list from claiming
      // yesterday's state about the session in front of the analyst.
      if (after.connection.mode === "api") void after.loadSessions();
    }
  },

  emitRefinement: (refinement, source) => {
    // Typed refinement objects are the gesture channel — no NL in the loop.
    const { connection, streamingTurnId } = get();
    if (connection.mode === "api" && !streamingTurnId) {
      // Live API: a gesture IS a turn — submit the typed operator directly.
      void get().submit({ refinements: [refinement] });
      return;
    }
    // Mock mode (logged + queued, visibly) or mid-stream (queued, flushed
    // as one refinement turn when the current stream finishes).
    console.info("[revi] typed refinement emitted", refinement, source ?? {});
    set((state) => ({
      pendingRefinements: [
        ...state.pendingRefinements,
        {
          refinement,
          sourceTurnId: source?.turnId,
          sourceReferent: source?.referent,
          at: Date.now(),
        },
      ],
    }));
  },

  setFeedback: (turnId, choice) =>
    set((state) => ({ feedback: { ...state.feedback, [turnId]: choice } })),

  openDrawer: (turnId) => set({ drawerTurnId: turnId }),
  closeDrawer: () => set({ drawerTurnId: null }),

  focusReferent: (referent) => set({ focusedReferent: referent }),

  simulateWatermarkRefresh: () =>
    set({
      watermarkBanner: { visible: true, newWatermark: REFRESHED_WATERMARK },
    }),

  resolveWatermarkBanner: (decision) =>
    set((state) => ({
      watermarkBanner: { ...state.watermarkBanner, visible: false, decision },
      watermark:
        decision === "re_anchor" && state.watermarkBanner.newWatermark
          ? state.watermarkBanner.newWatermark
          : state.watermark,
      // The next turn carries re_anchor: true so the server re-pins too.
      pendingReAnchor: state.pendingReAnchor || decision === "re_anchor",
    })),

  toggleFailurePreview: () =>
    set((state) => ({ showFailurePreview: !state.showFailurePreview })),

  reset: () => {
    turnCounter = 0;
    set((state) => ({
      turns: [],
      referents: {},
      pendingRefinements: [],
      feedback: {},
      watermarkBanner: { visible: false },
      showFailurePreview: false,
      drawerTurnId: null,
      focusedReferent: null,
      streamingTurnId: null,
      // A live API session stays pinned to its real watermark across a
      // thread reset; only the mock demo rewinds to the seed watermark.
      watermark: state.sessionLive ? state.watermark : INITIAL_WATERMARK,
      // NOT `replaying`/`replayProgress` — `replayReference()` calls this
      // via `newChat()` mid-run and owns that flag's lifecycle itself; a
      // plain reset() outside a replay never has it set in the first place.
      contractDrift: [],
      pendingReAnchor: false,
      // Tied to the turns being cleared, not to the controls — the chosen
      // settings themselves survive a reset, as any preference should.
      lastPolicyDenial: null,
      // A failed switch's message is about the thread that is being
      // cleared; carrying it into the next one would misattribute it.
      switchError: null,
    }));
  },

  newChat: async () => {
    const { driver, streamingTurnId, newChatPending, connection, switchingSessionId } = get();
    if (!driver || streamingTurnId || newChatPending || switchingSessionId) return;
    set({ newChatPending: true });
    get().reset();
    // No session exists after this point, and nothing may pretend one does.
    //
    // The driver mints nothing here — the server creates a session when the
    // first turn arrives — so between this click and that first question
    // there is a real gap with no session in it. The store used to keep the
    // ABANDONED session's watermark and pack sitting in state across that
    // gap, which put a specific data-load date and load time in the header
    // over a session that did not exist: a pin belonging to the thread that
    // was just discarded, read as a pin on the empty one in front of you.
    //
    // `sessionLive: false` is the honest state, and every surface that
    // renders the pin already gates on it. The watermark itself is left
    // untouched rather than reset to the module's seed constant, because
    // that constant is the MOCK fixture's load — swapping one wrong pin for
    // another wrong pin is not an improvement. It is simply not shown until
    // the first turn brings back a real one.
    //
    // The connection is NOT flipped to "connecting": there is no request in
    // flight to be connecting to, and the pill claiming otherwise over a
    // button press is the same fabrication one layer down.
    set({ sessionLive: false });
    try {
      await driver.newSession();
    } finally {
      set({ newChatPending: false });
      // The list is refreshed, but nothing was added to it: the rail shows
      // the tenant's existing sessions with none of them selected, which is
      // exactly what is true until the first question is asked.
      if (connection.mode === "api") void get().loadSessions();
    }
  },

  replayReference: async () => {
    const { driver, streamingTurnId, replaying, newChatPending, switchingSessionId } = get();
    if (!driver || streamingTurnId || replaying || newChatPending || switchingSessionId) return;
    set({ replaying: true, replayProgress: null });
    try {
      await get().newChat();
      const total = REFERENCE_QUESTIONS.length;
      for (let i = 0; i < total; i += 1) {
        set({ replayProgress: { index: i + 1, total } });
        await get().submit({ utterance: REFERENCE_QUESTIONS[i] });
        const turns = get().turns;
        const last = turns[turns.length - 1];
        // A turn that didn't land on "complete" — clarification, error, or
        // (defensively) anything else — stops the replay right there. The
        // turns already answered stay on screen; nothing fakes onward.
        if (last?.answer.status !== "complete") break;
      }
    } finally {
      set({ replaying: false, replayProgress: null });
    }
  },
}));
