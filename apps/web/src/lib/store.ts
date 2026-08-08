/**
 * Zustand session store. Turn answers are assembled exclusively by the pure
 * reducer `applyEventToAnswer` — the mock driver and the future real SSE
 * driver both feed the same TurnEvent pipeline, so wiring the API later is
 * swapping the driver only.
 */

import { create } from "zustand";

import type { TurnDriver, TurnSubmission } from "@/lib/driver";
import {
  STAGE_ORDER,
  type ChartSpec,
  type ClarificationData,
  type ContextHeaderData,
  type DataWatermark,
  type DefinitionCardData,
  type EvidenceBundle,
  type EvidenceGrade,
  type Finding,
  type InterpretationData,
  type MetricContractSummary,
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

export interface AnswerState {
  turnClass?: TurnClass;
  stages: StageStatus[];
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
  metric?: MetricContractSummary;
  cacheHits: number;
  status: AnswerStatus;
  error?: { code: string; message: string };
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
    case "finding":
      return { ...answer, findings: [...answer.findings, event.finding] };
    case "chart_spec":
      return { ...answer, charts: [...answer.charts, event.spec] };
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
        answerGrade: event.answerGrade ?? answer.answerGrade,
        metric: event.metric ?? answer.metric,
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
        error: { code: event.code, message: event.message },
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

interface SessionState {
  sessionId: string;
  watermark: DataWatermark;
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
  replaying: boolean;
  driver: TurnDriver | null;

  setDriver: (driver: TurnDriver) => void;
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

export const useSessionStore = create<SessionState>((set, get) => ({
  sessionId: "sess_demo_001",
  watermark: INITIAL_WATERMARK,
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
  driver: null,

  setDriver: (driver) => set({ driver }),

  submit: async (submission) => {
    const { driver, streamingTurnId } = get();
    if (!driver || streamingTurnId) return;
    const id = nextTurnId();
    const record: TurnRecord = {
      id,
      index: get().turns.length,
      submission,
      answer: emptyAnswer(),
    };
    set((state) => ({ turns: [...state.turns, record], streamingTurnId: id }));
    try {
      await driver.submit(submission, (event) => {
        set((state) => ({
          turns: state.turns.map((t) =>
            t.id === id ? { ...t, answer: applyEventToAnswer(t.answer, event) } : t,
          ),
          referents: registerReferents(state.referents, id, event),
        }));
      });
    } finally {
      set({ streamingTurnId: null });
    }
  },

  emitRefinement: (refinement, source) => {
    // Typed refinement objects are the gesture channel — no NL in the loop.
    // Until the API lands (M8) they are logged and queued, visibly.
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
    })),

  toggleFailurePreview: () =>
    set((state) => ({ showFailurePreview: !state.showFailurePreview })),

  reset: () => {
    turnCounter = 0;
    set({
      turns: [],
      referents: {},
      pendingRefinements: [],
      feedback: {},
      watermarkBanner: { visible: false },
      showFailurePreview: false,
      drawerTurnId: null,
      focusedReferent: null,
      streamingTurnId: null,
      watermark: INITIAL_WATERMARK,
      replaying: false,
    });
  },
}));
