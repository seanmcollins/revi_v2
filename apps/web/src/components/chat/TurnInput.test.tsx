import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TurnInput } from "@/components/chat/TurnInput";
import { useSessionStore } from "@/lib/store";

/**
 * A sequential replay drives `submit` directly through the store — the
 * composer must not let a user race it with a manual submission while it
 * runs (the store's `replaying` flag is true for the whole run, not just
 * while an individual turn streams).
 */
describe("TurnInput — disabled during a reference-demo replay", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({ submit: vi.fn().mockResolvedValue(undefined) });
  });

  afterEach(() => cleanup());

  it("is enabled when idle", () => {
    useSessionStore.setState({ streamingTurnId: null, replaying: false });
    render(<TurnInput suggestions={[]} />);

    expect(screen.getByRole("textbox")).toBeEnabled();
    expect(screen.getByLabelText("Send")).toBeDisabled(); // empty value, not "busy"
  });

  it("disables the composer while streamingTurnId is set", () => {
    useSessionStore.setState({ streamingTurnId: "turn_1", replaying: false });
    render(<TurnInput suggestions={[]} />);

    expect(screen.getByRole("textbox")).toBeDisabled();
  });

  it("disables the composer for the whole replay run, not just per-turn streaming", () => {
    // The gap between two sequential replay turns: the individual turn
    // isn't streaming right now, but the overall replay still is.
    useSessionStore.setState({ streamingTurnId: null, replaying: true });
    render(<TurnInput suggestions={["Break that down by payer"]} />);

    expect(screen.getByRole("textbox")).toBeDisabled();
    expect(screen.getByText("Break that down by payer")).toBeDisabled();
  });
});
