/**
 * Session settings: persistence, wire translation, and application.
 *
 * The properties worth pinning are the honesty ones — defaults produce NO
 * wire key at all (the pre-settings body, unchanged), an out-of-bounds
 * value goes out exactly as chosen instead of being quietly corrected, and
 * the panel's state is what the next turn actually runs under.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TurnDriver, TurnSubmission } from "@/lib/driver";
import {
  DEFAULT_SETTINGS,
  exceedsCeiling,
  isDefaultSettings,
  loadSettings,
  numberToDecimal,
  parseCapabilities,
  parseSettingsBounds,
  readSettings,
  saveSettings,
  settingsToWire,
  SETTINGS_STORAGE_KEY,
  type SessionSettings,
} from "@/lib/settings";
import { useSessionStore } from "@/lib/store";

const CUSTOM: SessionSettings = {
  modelTier: "claude-sonnet-5",
  maxTurnCostUsd: "0.25",
  narrativeDepth: "analyst",
  evidenceDepth: "deep",
  debug: true,
};

beforeEach(() => {
  window.localStorage.clear();
  useSessionStore.setState({ settings: DEFAULT_SETTINGS, lastPolicyDenial: null });
});

/* ------------------------------------------------------------------ */
/* Wire translation                                                    */
/* ------------------------------------------------------------------ */

describe("settingsToWire", () => {
  it("emits nothing at all when every control is at its default", () => {
    expect(isDefaultSettings(DEFAULT_SETTINGS)).toBe(true);
    expect(settingsToWire(DEFAULT_SETTINGS)).toBeNull();
  });

  it("emits the published SessionSettingsModel spelling once anything changes", () => {
    expect(settingsToWire(CUSTOM)).toEqual({
      model_tier: "claude-sonnet-5",
      max_turn_cost_usd: "0.25",
      narrative_depth: "analyst",
      evidence_depth: "deep",
      debug: true,
    });
  });

  it("keeps the budget a decimal STRING — never a float", () => {
    const wire = settingsToWire({ ...DEFAULT_SETTINGS, maxTurnCostUsd: "0.10" });
    expect(wire?.max_turn_cost_usd).toBe("0.10");
    expect(typeof wire?.max_turn_cost_usd).toBe("string");
  });

  it("sends an out-of-bounds budget as chosen rather than clamping it", () => {
    // Clamping here would produce a panel that says one thing and answers
    // computed under another; the API's refusal is the honest outcome.
    const wire = settingsToWire({ ...DEFAULT_SETTINGS, maxTurnCostUsd: "5.00" });
    expect(wire?.max_turn_cost_usd).toBe("5.00");
    expect(exceedsCeiling("5.00", "0.50")).toBe(true);
  });

  it("distinguishes an unset ceiling (no ledger) from a zero one", () => {
    expect(settingsToWire({ ...DEFAULT_SETTINGS, maxTurnCostUsd: null })).toBeNull();
    expect(settingsToWire({ ...DEFAULT_SETTINGS, maxTurnCostUsd: "0.00" })).toEqual(
      expect.objectContaining({ max_turn_cost_usd: "0.00" }),
    );
  });

  it("formats slider positions as two-decimal strings", () => {
    expect(numberToDecimal(0.1 + 0.2)).toBe("0.30");
  });
});

/* ------------------------------------------------------------------ */
/* Persistence                                                         */
/* ------------------------------------------------------------------ */

describe("settings persistence", () => {
  it("round-trips through localStorage", () => {
    saveSettings(CUSTOM);
    expect(loadSettings()).toEqual(CUSTOM);
  });

  it("removes the key entirely when settings return to their defaults", () => {
    saveSettings(CUSTOM);
    saveSettings(DEFAULT_SETTINGS);
    expect(window.localStorage.getItem(SETTINGS_STORAGE_KEY)).toBeNull();
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS);
  });

  it("falls back to defaults on corrupt storage instead of throwing", () => {
    window.localStorage.setItem(SETTINGS_STORAGE_KEY, "{not json");
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS);
  });

  it("keeps an unknown persisted model tier — dropping it would hide the refusal", () => {
    const parsed = readSettings({ modelTier: "claude-retired-3" });
    expect(parsed.modelTier).toBe("claude-retired-3");
  });

  it("ignores unreadable enum values rather than inventing a third mode", () => {
    const parsed = readSettings({ narrativeDepth: "novel", evidenceDepth: 7, debug: "yes" });
    expect(parsed.narrativeDepth).toBe("summary");
    expect(parsed.evidenceDepth).toBe("standard");
    expect(parsed.debug).toBe(false);
  });
});

/* ------------------------------------------------------------------ */
/* Bounds                                                              */
/* ------------------------------------------------------------------ */

describe("parseSettingsBounds", () => {
  it("reads the published SettingsBoundsPayload", () => {
    const bounds = parseSettingsBounds({
      model_tiers: ["claude-opus-5", "claude-sonnet-5"],
      default_model_tier: "claude-opus-5",
      model_tier_effective: true,
      max_turn_cost_usd: "0.50",
      narrative_depths: ["summary", "analyst"],
      evidence_depths: ["standard", "deep"],
      evidence_depth_deep_multiplier: 4,
      debug_available: true,
    });
    expect(bounds.modelTiers).toEqual(["claude-opus-5", "claude-sonnet-5"]);
    expect(bounds.evidenceDepthDeepMultiplier).toBe(4);
    expect(bounds.debugAvailable).toBe(true);
  });

  it("offers nothing a deployment did not publish", () => {
    const bounds = parseSettingsBounds({});
    expect(bounds.modelTiers).toEqual([]);
    expect(bounds.narrativeDepths).toEqual([]);
    expect(bounds.evidenceDepths).toEqual([]);
    expect(bounds.debugAvailable).toBe(false);
  });

  it("reads the deployment wiring alongside the bounds", () => {
    const capabilities = parseCapabilities({
      llm: "scripted-demo",
      pack_id: "base-rcm",
      pack_version: "1.0.0",
      newest_watermark_id: "wm_003",
      settings: { debug_available: true },
    });
    expect(capabilities.llm).toBe("scripted-demo");
    expect(capabilities.packId).toBe("base-rcm");
    expect(capabilities.settings.debugAvailable).toBe(true);
  });
});

/* ------------------------------------------------------------------ */
/* Application: the store puts settings on the turn                    */
/* ------------------------------------------------------------------ */

function recordingDriver(): { driver: TurnDriver; submissions: TurnSubmission[] } {
  const submissions: TurnSubmission[] = [];
  const driver: TurnDriver = {
    submit: async (submission, emit) => {
      submissions.push(submission);
      emit({ type: "turn_complete", investigationId: "inv_1", status: "complete" });
    },
    newSession: async () => {},
  };
  return { driver, submissions };
}

describe("store application", () => {
  afterEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({ settings: DEFAULT_SETTINGS });
  });

  it("omits settings from the submission while everything is at its default", async () => {
    const { driver, submissions } = recordingDriver();
    useSessionStore.setState({ driver });

    await useSessionStore.getState().submit({ utterance: "Why did cash decline last week?" });

    expect(submissions).toHaveLength(1);
    expect(submissions[0].settings).toBeUndefined();
  });

  it("attaches the chosen settings to every later turn", async () => {
    const { driver, submissions } = recordingDriver();
    useSessionStore.setState({ driver });
    useSessionStore.getState().patchSettings({ evidenceDepth: "deep", debug: true });

    await useSessionStore.getState().submit({ utterance: "Break that down by payer" });

    expect(submissions[0].settings).toEqual({
      ...DEFAULT_SETTINGS,
      evidenceDepth: "deep",
      debug: true,
    });
  });

  it("persists a change immediately, so a reload keeps it", () => {
    useSessionStore.getState().patchSettings({ narrativeDepth: "analyst" });
    expect(loadSettings().narrativeDepth).toBe("analyst");
  });

  it("keeps the server's POLICY_DENIED sentence verbatim", async () => {
    const message =
      "max_turn_cost_usd 5.00 exceeds this deployment's ceiling of 0.50 (REVI_LLM_MAX_BUDGET_USD)";
    const driver: TurnDriver = {
      submit: async (_submission, emit) => {
        emit({ type: "error", code: "POLICY_DENIED", message });
      },
      newSession: async () => {},
    };
    useSessionStore.setState({ driver });

    await useSessionStore.getState().submit({ utterance: "Why did cash decline last week?" });

    expect(useSessionStore.getState().lastPolicyDenial).toBe(message);
  });

  it("clears the stale refusal when a control changes", async () => {
    useSessionStore.setState({ lastPolicyDenial: "some earlier refusal" });
    useSessionStore.getState().patchSettings({ debug: true });
    expect(useSessionStore.getState().lastPolicyDenial).toBeNull();
  });

  it("says the bounds are unavailable rather than inventing controls", async () => {
    const { driver } = recordingDriver(); // no capabilities() on the seam
    useSessionStore.setState({ driver, capabilitiesState: "idle" });

    await useSessionStore.getState().loadCapabilities();

    expect(useSessionStore.getState().capabilitiesState).toBe("unavailable");
    expect(useSessionStore.getState().capabilities).toBeNull();
    expect(useSessionStore.getState().capabilitiesError).toMatch(/no deployment/i);
  });

  it("surfaces a refused capabilities read instead of falling back to defaults", async () => {
    const { driver } = recordingDriver();
    const failing: TurnDriver = {
      ...driver,
      capabilities: () => Promise.reject(new Error("request failed: HTTP 401")),
    };
    useSessionStore.setState({ driver: failing, capabilitiesState: "idle" });

    await useSessionStore.getState().loadCapabilities();

    expect(useSessionStore.getState().capabilitiesState).toBe("unavailable");
    expect(useSessionStore.getState().capabilitiesError).toBe("request failed: HTTP 401");
  });
});

/* ------------------------------------------------------------------ */
/* Trace loading (GET /v1/investigations/{iid}/trace)                  */
/* ------------------------------------------------------------------ */

describe("loadTrace", () => {
  afterEach(() => {
    useSessionStore.getState().reset();
    vi.restoreAllMocks();
  });

  it("reads a turn's recorded trace after the fact", async () => {
    const { driver, submissions } = recordingDriver();
    const trace = {
      traceId: "trace_1",
      sessionId: "sess_1",
      investigationId: "inv_1",
      turnId: "turn_1",
      settings: {
        modelTier: null,
        maxTurnCostUsd: null,
        narrativeDepth: "summary",
        evidenceDepth: "standard",
        debug: false,
      },
      refinementOperators: [],
      referentResolutions: [],
      probes: [],
      grades: {},
      findingGrades: {},
      calculationOperators: [],
      warnings: [],
      llmCalls: [],
      templateHashes: {},
      timingsMs: {},
      watermarkId: "wm_003",
      watermarkStale: false,
      epoch: 1,
      reAnchored: false,
      packId: "base-rcm",
      packVersion: "1.0.0",
      packSnapshotId: "snap",
      redactions: [],
    };
    useSessionStore.setState({ driver: { ...driver, getTrace: async () => trace } });
    await useSessionStore.getState().submit({ utterance: "Why did cash decline last week?" });
    expect(submissions).toHaveLength(1);

    const turnId = useSessionStore.getState().turns[0].id;
    await useSessionStore.getState().loadTrace(turnId);

    expect(useSessionStore.getState().turns[0].answer.debug?.traceId).toBe("trace_1");
  });

  it("repeats the deployment's own refusal when the trace surface is off", async () => {
    const { driver } = recordingDriver();
    useSessionStore.setState({
      driver: {
        ...driver,
        getTrace: () =>
          Promise.reject(new Error("debug traces are disabled on this deployment (REVI_DEBUG_TRACE=0)")),
      },
    });
    await useSessionStore.getState().submit({ utterance: "Why did cash decline last week?" });
    const turnId = useSessionStore.getState().turns[0].id;

    await useSessionStore.getState().loadTrace(turnId);

    expect(useSessionStore.getState().turns[0].answer.traceError).toBe(
      "debug traces are disabled on this deployment (REVI_DEBUG_TRACE=0)",
    );
  });
});
