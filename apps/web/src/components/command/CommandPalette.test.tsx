import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { CommandPalette } from "@/components/command/CommandPalette";
import { useSessionStore } from "@/lib/store";

// jsdom does not implement scrollIntoView; the palette calls it to keep the
// selected row in view while arrowing.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

const ENV_KEY = "NEXT_PUBLIC_REVI_DRIVER";

function setEnvDriver(value: string | undefined) {
  if (value === undefined) delete process.env[ENV_KEY];
  else process.env[ENV_KEY] = value;
}

describe("CommandPalette — driver-switch affordance is a dev/test-only action", () => {
  const originalEnv = process.env[ENV_KEY];

  beforeEach(() => {
    useSessionStore.getState().reset();
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    setEnvDriver(originalEnv);
  });

  it("hides the driver-switch action with the default (live API) env", () => {
    setEnvDriver(undefined);
    render(<CommandPalette open onOpenChange={() => {}} />);

    expect(screen.queryByText(/Switch to (mock|live API) driver/)).not.toBeInTheDocument();
  });

  it("hides the driver-switch action when the env is explicitly api", () => {
    setEnvDriver("api");
    render(<CommandPalette open onOpenChange={() => {}} />);

    expect(screen.queryByText(/Switch to (mock|live API) driver/)).not.toBeInTheDocument();
  });

  it("keeps the driver-switch action only when the env itself forces mock", () => {
    setEnvDriver("mock");
    render(<CommandPalette open onOpenChange={() => {}} />);

    expect(screen.getByText(/Switch to (mock|live API) driver/)).toBeInTheDocument();
  });

  it("always offers Replay reference demo, regardless of env", () => {
    setEnvDriver(undefined);
    render(<CommandPalette open onOpenChange={() => {}} />);

    expect(screen.getByText("Replay reference demo")).toBeInTheDocument();
  });
});

describe("CommandPalette — the internal settings panel is ⌘K-reachable", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({ settingsOpen: false, capabilitiesState: "idle", driver: null });
  });

  afterEach(() => {
    cleanup();
    useSessionStore.setState({ settingsOpen: false, capabilitiesState: "idle" });
  });

  it("lists Settings in the Workspace group and opens the panel", () => {
    render(<CommandPalette open onOpenChange={() => {}} />);

    fireEvent.click(screen.getByText("Settings"));

    expect(useSessionStore.getState().settingsOpen).toBe(true);
  });

  it("labels the action when debug mode is on, so it is visible from the palette", () => {
    useSessionStore.setState({ settings: { ...useSessionStore.getState().settings, debug: true } });

    render(<CommandPalette open onOpenChange={() => {}} />);

    expect(screen.getByText("internal · debug on")).toBeInTheDocument();
  });
});

describe("CommandPalette — replay action reflects live progress", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
  });

  afterEach(() => cleanup());

  it("runs replayReference() from the store when invoked", () => {
    const replayReference = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ replayReference, replaying: false, replayProgress: null });

    render(<CommandPalette open onOpenChange={() => {}} />);
    fireEvent.click(screen.getByText("Replay reference demo"));

    expect(replayReference).toHaveBeenCalledTimes(1);
  });

  it("labels the action with live progress while a replay is running", () => {
    useSessionStore.setState({ replaying: true, replayProgress: { index: 2, total: 5 } });

    render(<CommandPalette open onOpenChange={() => {}} />);

    expect(screen.getByText("Replaying reference demo (2/5)")).toBeInTheDocument();
  });
});

/**
 * The row Enter will fire has to be visible AND announced.
 *
 * The only selection signal used to be `bg-accent`, which measures 1.05:1
 * against the overlay in dark and 1.19:1 in light — and `hover:bg-accent/50`
 * measures 1.06:1, so hover and selected were the same pixel on a menu
 * containing "Reset session" and "New chat", both of which discard an open
 * investigation with no undo. SC 1.4.11 wants 3:1 for a state indicator;
 * the 2px `--ring` rail measures 9.34:1 dark / 3.74:1 light there.
 */
describe("CommandPalette — the selected row is drawn and announced", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    // `reset()` deliberately leaves `replaying` alone (a replay owns that
    // flag across the reset it performs itself), and the suite above sets
    // it — an idle palette is the state under test here.
    useSessionStore.setState({
      replaying: false,
      replayProgress: null,
      streamingTurnId: null,
      newChatPending: false,
      switchingSessionId: null,
    });
  });

  afterEach(() => cleanup());

  it("marks exactly one option selected, with a 2px ring rail and no tint alone", () => {
    render(<CommandPalette open onOpenChange={() => {}} />);

    const options = screen.getAllByRole("option");
    const selected = options.filter((o) => o.getAttribute("aria-selected") === "true");
    expect(selected).toHaveLength(1);
    expect(selected[0].className).toContain("border-l-ring");
    // Every row reserves the 2px, so selecting one never shifts the labels.
    expect(options[1].className).toContain("border-l-transparent");
    expect(options[1].className).not.toContain("border-l-ring");
  });

  it("points the combobox at the row Enter will run", () => {
    render(<CommandPalette open onOpenChange={() => {}} />);

    const input = screen.getByRole("combobox");
    const active = input.getAttribute("aria-activedescendant");
    expect(active).toBeTruthy();
    expect(document.getElementById(active as string)?.getAttribute("aria-selected")).toBe("true");
  });

  it("moves the announced row with the arrow keys", () => {
    render(<CommandPalette open onOpenChange={() => {}} />);
    const input = screen.getByRole("combobox");

    expect(input.getAttribute("aria-activedescendant")).toBe("palette-option-0");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe("palette-option-1");
  });
});
