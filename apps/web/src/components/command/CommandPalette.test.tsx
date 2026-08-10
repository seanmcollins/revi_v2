import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryRouter } from "react-router-dom";

import { CommandPalette } from "@/components/command/CommandPalette";
import { useSessionStore } from "@/lib/store";

/**
 * The palette navigates now — "Open Monitors" is the ⌘K route to the surface
 * an analyst starts their day on, and it was the one primary destination
 * in the product with no keyboard verb. `useNavigate` throws outside a
 * router, which no unit test mounts, so every render here gets a
 * `MemoryRouter` and the navigation is performed into memory rather than
 * into `window.location`.
 */
function draw() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <CommandPalette open onOpenChange={() => {}} />
    </MemoryRouter>,
  );
}

// jsdom does not implement scrollIntoView; the palette calls it to keep the
// selected row in view while arrowing.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

const ENV_KEY = "VITE_REVI_DRIVER";

/**
 * `vi.stubEnv` rather than a `process.env` assignment: the read site is
 * `import.meta.env.VITE_REVI_DRIVER` now, and Vitest keeps that object
 * live for exactly this (a production build statically replaces it, which
 * is what `NEXT_PUBLIC_*` did too).
 */
function setEnvDriver(value: string | undefined) {
  if (value === undefined) vi.stubEnv(ENV_KEY, undefined);
  else vi.stubEnv(ENV_KEY, value);
}

describe("CommandPalette — driver-switch affordance is a dev/test-only action", () => {

  beforeEach(() => {
    useSessionStore.getState().reset();
    window.localStorage.clear();
  });

  afterEach(() => {
    cleanup();
    vi.unstubAllEnvs();
  });

  it("hides the driver-switch action with the default (live API) env", () => {
    setEnvDriver(undefined);
    draw();

    expect(screen.queryByText(/Switch to (mock|live API) driver/)).not.toBeInTheDocument();
  });

  it("hides the driver-switch action when the env is explicitly api", () => {
    setEnvDriver("api");
    draw();

    expect(screen.queryByText(/Switch to (mock|live API) driver/)).not.toBeInTheDocument();
  });

  it("keeps the driver-switch action only when the env itself forces mock", () => {
    setEnvDriver("mock");
    draw();

    expect(screen.getByText(/Use the (mock fixture|live API)/)).toBeInTheDocument();
  });

  it("always offers Replay reference demo, regardless of env", () => {
    setEnvDriver(undefined);
    draw();

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
    draw();

    fireEvent.click(screen.getByText("Settings"));

    expect(useSessionStore.getState().settingsOpen).toBe(true);
  });

  it("labels the action when debug mode is on, so it is visible from the palette", () => {
    useSessionStore.setState({ settings: { ...useSessionStore.getState().settings, debug: true } });

    draw();

    expect(screen.getByText("Internal · debug on")).toBeInTheDocument();
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

    draw();
    fireEvent.click(screen.getByText("Replay reference demo"));

    expect(replayReference).toHaveBeenCalledTimes(1);
  });

  it("labels the action with live progress while a replay is running", () => {
    useSessionStore.setState({ replaying: true, replayProgress: { index: 2, total: 5 } });

    draw();

    expect(screen.getByText("Replaying reference demo (2/5)")).toBeInTheDocument();
  });
});

/**
 * The row Enter will fire has to be visible AND announced.
 *
 * The only selection signal used to be `bg-accent`, which measures 1.19:1
 * against the overlay — and `hover:bg-accent/50` measures 1.06:1, so hover
 * and selected were the same pixel on a menu containing "Reset session"
 * and "New chat", both of which discard an open investigation with no
 * undo. SC 1.4.11 wants 3:1 for a state indicator; the 2px `--ring` rail
 * measures 3.74:1 there.
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
    draw();

    const options = screen.getAllByRole("option");
    const selected = options.filter((o) => o.getAttribute("aria-selected") === "true");
    expect(selected).toHaveLength(1);
    expect(selected[0].className).toContain("border-l-ring");
    // Every row reserves the 2px, so selecting one never shifts the labels.
    expect(options[1].className).toContain("border-l-transparent");
    expect(options[1].className).not.toContain("border-l-ring");
  });

  it("points the combobox at the row Enter will run", () => {
    draw();

    const input = screen.getByRole("combobox");
    const active = input.getAttribute("aria-activedescendant");
    expect(active).toBeTruthy();
    expect(document.getElementById(active as string)?.getAttribute("aria-selected")).toBe("true");
  });

  it("moves the announced row with the arrow keys", () => {
    draw();
    const input = screen.getByRole("combobox");

    expect(input.getAttribute("aria-activedescendant")).toBe("palette-option-0");
    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(input.getAttribute("aria-activedescendant")).toBe("palette-option-1");
  });
});
