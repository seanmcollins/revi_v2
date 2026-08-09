import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ClarificationPrompt } from "@/components/clarification/ClarificationPrompt";
import { useSessionStore } from "@/lib/store";
import type { ClarificationData } from "@/lib/types";

/**
 * ClarificationPrompt is the ONE code path that renders
 * `TurnClarification.options` as recovery chips, for both the mock driver
 * and the live API — the wire shape (`clarification.options`, populated by
 * `parseTurnFrame`/`parseTurnResponse` in lib/contract.ts) is identical
 * either way, so this component-level test stands in for both.
 */
const CLARIFICATION: ClarificationData = {
  question: "Which did you mean?",
  options: ["Why did cash decline last week?", "What is PR3?"],
  reason: "Low confidence match.",
};

describe("ClarificationPrompt — options rendered as tappable recovery chips", () => {
  afterEach(() => {
    cleanup();
    useSessionStore.setState({ streamingTurnId: null });
  });

  it("renders every option as a chip and submits its text as clarificationResponse on click", () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ submit, streamingTurnId: null });

    render(<ClarificationPrompt clarification={CLARIFICATION} />);

    expect(screen.getByText(CLARIFICATION.question)).toBeInTheDocument();
    for (const option of CLARIFICATION.options) {
      expect(screen.getByRole("button", { name: option })).toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole("button", { name: "What is PR3?" }));

    // The reply travels on the clarification channel, not as a fresh
    // utterance — the API distinguishes the two.
    expect(submit).toHaveBeenCalledTimes(1);
    expect(submit).toHaveBeenCalledWith({ clarificationResponse: "What is PR3?" });
  });

  it("disables option chips (and blocks clicks) while a turn is streaming", () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ submit, streamingTurnId: "turn_1" });

    render(<ClarificationPrompt clarification={CLARIFICATION} />);

    for (const option of CLARIFICATION.options) {
      expect(screen.getByRole("button", { name: option })).toBeDisabled();
    }
    fireEvent.click(screen.getByRole("button", { name: CLARIFICATION.options[0] }));
    expect(submit).not.toHaveBeenCalled();
  });

  it("renders up to the server's four options as chips, not truncated or dropdown-hidden", () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ submit, streamingTurnId: null });
    const fourOptions: ClarificationData = { ...CLARIFICATION, options: ["A", "B", "C", "D"] };

    render(<ClarificationPrompt clarification={fourOptions} />);

    for (const option of fourOptions.options) {
      expect(screen.getByRole("button", { name: option })).toBeInTheDocument();
    }
  });

  it("free text still works alongside the option chips", () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ submit, streamingTurnId: null });

    render(<ClarificationPrompt clarification={CLARIFICATION} />);

    fireEvent.change(screen.getByPlaceholderText("Or say it differently…"), {
      target: { value: "cash decline last month" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(submit).toHaveBeenCalledWith({ clarificationResponse: "cash decline last month" });
  });
});
