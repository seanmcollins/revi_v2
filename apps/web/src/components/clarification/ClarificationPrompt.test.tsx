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

  /**
   * `CLARIFICATION_NO_OPTIONS` — the engine's marker for a clarification
   * the analyst cannot tap their way out of (`submit_turn.NO_OPTIONS_
   * REASON`). It is APPENDED to the reason, never prefixed, because the
   * reason's own leading §12 code is what every other reader keys off.
   *
   * Two live sessions reached the page with `options: []` and a question
   * mark above an empty row of buttons, one of them after a tenth of a
   * dollar was spent establishing the platform could not do it.
   */
  describe("CLARIFICATION_NO_OPTIONS — a statement, not a question with no answers", () => {
    const NO_OPTIONS: ClarificationData = {
      question: "What would you like me to measure?",
      options: [],
      reason:
        "CAPABILITY_UNSUPPORTED: this pack publishes no metric for provider productivity; " +
        "CLARIFICATION_NO_OPTIONS",
    };

    it("renders as an informational card and offers no empty button row", () => {
      const submit = vi.fn().mockResolvedValue(undefined);
      useSessionStore.setState({ submit, streamingTurnId: null });

      render(<ClarificationPrompt clarification={NO_OPTIONS} />);

      expect(screen.getByText("There is no answerable option to offer here.")).toBeInTheDocument();
      // Free text is the only recovery, so the only button is its Send.
      expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Send"]);
      expect(
        screen.getByRole("group", {
          name: "No answerable options: What would you like me to measure?",
        }),
      ).toBeInTheDocument();
    });

    it("keeps the engine's sentence and drops the marker, which is not for a reader", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(<ClarificationPrompt clarification={NO_OPTIONS} />);

      expect(
        screen.getByText(
          "this pack publishes no metric for provider productivity",
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/CLARIFICATION_NO_OPTIONS/)).not.toBeInTheDocument();
      // The question still appears — it is the most specific thing on the
      // card — just not as a prompt above nothing.
      expect(screen.getByText(NO_OPTIONS.question)).toBeInTheDocument();
    });

    it("takes the same shape for an empty option list carrying no marker at all", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(
        <ClarificationPrompt
          clarification={{ question: "Which did you mean?", options: [], reason: "Low match." }}
        />,
      );
      expect(screen.getByText("There is no answerable option to offer here.")).toBeInTheDocument();
      expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Send"]);
    });

    it("free text on a no-options card still travels on the clarification channel", () => {
      const submit = vi.fn().mockResolvedValue(undefined);
      useSessionStore.setState({ submit, streamingTurnId: null });

      render(<ClarificationPrompt clarification={NO_OPTIONS} />);

      fireEvent.change(screen.getByPlaceholderText("Ask it a different way…"), {
        target: { value: "denied dollars by payer last month" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Send" }));

      expect(submit).toHaveBeenCalledWith({
        clarificationResponse: "denied dollars by payer last month",
      });
    });
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
