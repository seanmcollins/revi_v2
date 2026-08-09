/**
 * The settings panel renders from `GET /v1/capabilities` and nothing else.
 *
 * The regression these tests exist to prevent is a cosmetic knob: a control
 * offered for a mechanism this deployment does not have, whose every value
 * the server would refuse. A deployment that publishes no model tiers must
 * produce no model tier control — not one that "defaults sensibly".
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { DEFAULT_SETTINGS, type DeploymentCapabilities } from "@/lib/settings";
import { useSessionStore } from "@/lib/store";

const FULL_CAPABILITIES: DeploymentCapabilities = {
  llm: "claude-agent-sdk",
  packId: "base-rcm",
  packVersion: "1.0.0",
  packSnapshotId: "snap_1",
  newestWatermarkId: "wm_003",
  settings: {
    modelTiers: ["claude-opus-5", "claude-sonnet-5"],
    defaultModelTier: "claude-opus-5",
    modelTierEffective: true,
    maxTurnCostUsd: "0.50",
    narrativeDepths: ["summary", "analyst"],
    evidenceDepths: ["standard", "deep"],
    evidenceDepthDeepMultiplier: 4,
    debugAvailable: true,
  },
};

/** What a scripted-demo deployment actually publishes (verified live). */
const SCRIPTED_CAPABILITIES: DeploymentCapabilities = {
  ...FULL_CAPABILITIES,
  llm: "scripted-demo",
  settings: {
    ...FULL_CAPABILITIES.settings,
    modelTiers: [],
    modelTierEffective: false,
    debugAvailable: false,
  },
};

function openWith(capabilities: DeploymentCapabilities | null) {
  useSessionStore.setState({
    settingsOpen: true,
    settings: DEFAULT_SETTINGS,
    capabilities,
    capabilitiesState: capabilities ? "ready" : "unavailable",
    capabilitiesError: capabilities ? null : "Could not reach the API.",
    lastPolicyDenial: null,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  cleanup();
  useSessionStore.setState({
    settingsOpen: false,
    settings: DEFAULT_SETTINGS,
    capabilities: null,
    capabilitiesState: "idle",
    capabilitiesError: null,
    lastPolicyDenial: null,
  });
});

describe("SettingsPanel — capabilities decide what exists", () => {
  it("marks itself Internal", () => {
    openWith(FULL_CAPABILITIES);
    render(<SettingsPanel />);

    expect(screen.getByText("Internal")).toBeInTheDocument();
  });

  it("renders every control a deployment publishes", () => {
    openWith(FULL_CAPABILITIES);
    render(<SettingsPanel />);

    expect(screen.getByText("Interpretation model")).toBeInTheDocument();
    expect(screen.getByText("Per-turn cost ceiling")).toBeInTheDocument();
    expect(screen.getByText("Answer detail")).toBeInTheDocument();
    expect(screen.getByText("Evidence depth")).toBeInTheDocument();
    expect(screen.getByText("Debug mode")).toBeInTheDocument();
  });

  it("omits the model tier control when the model applies no per-call override", () => {
    openWith(SCRIPTED_CAPABILITIES);
    render(<SettingsPanel />);

    expect(screen.queryByText("Interpretation model")).not.toBeInTheDocument();
    // …and says why, rather than leaving a silent hole.
    expect(screen.getByText(/applies no per-call override/i)).toBeInTheDocument();
  });

  it("omits the debug toggle when the deployment disabled the trace surface", () => {
    openWith(SCRIPTED_CAPABILITIES);
    render(<SettingsPanel />);

    expect(screen.queryByText("Debug mode")).not.toBeInTheDocument();
  });

  it("offers no controls at all when the bounds cannot be read", () => {
    openWith(null);
    render(<SettingsPanel />);

    expect(screen.getByText(/bounds could not be read/i)).toBeInTheDocument();
    expect(screen.getByText("Could not reach the API.")).toBeInTheDocument();
    expect(screen.queryByText("Evidence depth")).not.toBeInTheDocument();
    expect(screen.queryByText("Debug mode")).not.toBeInTheDocument();
  });

  it("publishes the deep multiplier honestly instead of promising more evidence", () => {
    openWith(FULL_CAPABILITIES);
    render(<SettingsPanel />);

    expect(screen.getByText(/4× wider/)).toBeInTheDocument();
  });

  it("shows the effective configuration read-only", () => {
    openWith(FULL_CAPABILITIES);
    useSessionStore.setState({
      connection: { mode: "api", state: "online", llmMode: "claude-agent-sdk", storeMode: "memory" },
    });
    render(<SettingsPanel />);

    expect(screen.getByText("Effective configuration")).toBeInTheDocument();
    expect(screen.getByText("api · online")).toBeInTheDocument();
    expect(screen.getByText("memory")).toBeInTheDocument();
    expect(screen.getByText("base-rcm@1.0.0")).toBeInTheDocument();
  });
});

describe("SettingsPanel — choices apply and persist", () => {
  it("writes a chosen control straight into the store and localStorage", () => {
    openWith(FULL_CAPABILITIES);
    render(<SettingsPanel />);

    fireEvent.click(screen.getByRole("switch"));

    expect(useSessionStore.getState().settings.debug).toBe(true);
    expect(window.localStorage.getItem("revi-settings")).toContain('"debug":true');
  });

  it("switches evidence depth through the published option, not a guess", () => {
    openWith(FULL_CAPABILITIES);
    render(<SettingsPanel />);

    fireEvent.click(screen.getByRole("radio", { name: /Deep/ }));

    expect(useSessionStore.getState().settings.evidenceDepth).toBe("deep");
  });

  it("keeps a persisted tier the deployment no longer allows, and says so", () => {
    openWith(FULL_CAPABILITIES);
    useSessionStore.setState({
      settings: { ...DEFAULT_SETTINGS, modelTier: "claude-retired-3" },
    });
    render(<SettingsPanel />);

    const stale = screen.getByRole("radio", { name: /claude-retired-3/ });
    expect(stale).toHaveAttribute("aria-checked", "true");
    expect(stale).toHaveAttribute("title", expect.stringContaining("allowlist"));
  });

  it("warns without clamping when a ceiling is above the published bound", () => {
    openWith(FULL_CAPABILITIES);
    useSessionStore.setState({ settings: { ...DEFAULT_SETTINGS, maxTurnCostUsd: "5.00" } });
    render(<SettingsPanel />);

    expect(screen.getByText(/Above this deployment’s published ceiling of \$0.50/)).toBeInTheDocument();
    // The value is untouched — the API gets to refuse it by name.
    expect(useSessionStore.getState().settings.maxTurnCostUsd).toBe("5.00");
  });

  it("repeats the API's refusal verbatim", () => {
    openWith(FULL_CAPABILITIES);
    const message =
      "max_turn_cost_usd 5.00 exceeds this deployment's ceiling of 0.50 (REVI_LLM_MAX_BUDGET_USD)";
    useSessionStore.setState({ lastPolicyDenial: message });
    render(<SettingsPanel />);

    expect(screen.getByText(message)).toBeInTheDocument();
  });
});
