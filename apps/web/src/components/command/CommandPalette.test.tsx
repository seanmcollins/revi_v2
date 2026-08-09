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
