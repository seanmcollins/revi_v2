import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ClarificationPrompt } from "@/components/clarification/ClarificationPrompt";
import { DEFAULT_SETTINGS } from "@/lib/settings";
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

/**
 * The live reason on a clarification that OFFERS options, verbatim off the
 * wire: `interpretation.py` composes it as
 * `f"turn classification confidence {confidence:.2f}"`. It is engine
 * bookkeeping — there is no reader anywhere for whom a model's
 * self-reported score is an instruction — and it was rendering as fine
 * print under a question that had three real answers attached.
 */
const CONFIDENCE_REASON = "turn classification confidence 0.78";

describe("ClarificationPrompt — options rendered as tappable recovery chips", () => {
  afterEach(() => {
    cleanup();
    useSessionStore.setState({ streamingTurnId: null, settings: DEFAULT_SETTINGS });
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
   * THE REGISTER. A question with answers and a refusal are two different
   * speech acts, and this card used to make both in a warning's voice.
   *
   * The acceptance gate's named "single scariest-feeling element": a
   * clarification offering three real A/R interpretations, rendered under
   * an amber rule, an alert glyph, the sentence "There is no answerable
   * option to offer here." and the fine print "turn classification
   * confidence 0.78" — directly above the three buttons that answered it.
   *
   * These pin the two registers apart. Both halves matter: softening the
   * refusal would be the same defect pointed the other way, because a
   * refusal IS a verdict about what this platform cannot do.
   */
  describe("a question with answers is neutral; a refusal is not", () => {
    const OFFERS_OPTIONS: ClarificationData = {
      question:
        "Which A/R view do you want — days in A/R, aging distribution, or balance trend?",
      options: ["Days in A/R by payer", "A/R aging distribution", "A/R balance trend"],
      reason: CONFIDENCE_REASON,
    };

    const REFUSES: ClarificationData = {
      question: "What would you like me to measure?",
      options: [],
      reason:
        "CAPABILITY_UNSUPPORTED: this pack publishes no metric for provider productivity; " +
        "CLARIFICATION_NO_OPTIONS",
    };

    /** The card's own element, whichever register it took. */
    const card = (): HTMLElement => screen.getByRole("group");

    it("renders an options-bearing clarification in the neutral register", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(<ClarificationPrompt clarification={OFFERS_OPTIONS} />);

      expect(card()).toHaveAttribute("data-clarification-register", "neutral");
      // No amber: not on the rule, not on the fill, not on a word of it.
      expect(card().className).not.toMatch(/warning/);
      expect(card().innerHTML).not.toMatch(/text-warning/);
      // …and no accent hue borrowed from the evidence-grade palette either.
      expect(card().className).not.toMatch(/grade-derived/);
      // No warning header over a question that has three real answers.
      expect(
        screen.queryByText("There is no answerable option to offer here."),
      ).not.toBeInTheDocument();
      // The question leads, and the options are the buttons under it.
      expect(screen.getByText(OFFERS_OPTIONS.question)).toBeInTheDocument();
      for (const option of OFFERS_OPTIONS.options) {
        expect(screen.getByRole("button", { name: option })).toBeInTheDocument();
      }
    });

    it("keeps the loud register on a refusal — a verdict is not softened", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(<ClarificationPrompt clarification={REFUSES} />);

      expect(card()).toHaveAttribute("data-clarification-register", "refusal");
      expect(card().className).toMatch(/border-warning/);
      expect(card().className).toMatch(/bg-warning/);
      expect(
        screen.getByText("There is no answerable option to offer here."),
      ).toBeInTheDocument();
      // And the refusal's own explanation is still the useful sentence on
      // it — this register is where the reason belongs.
      expect(
        screen.getByText("this pack publishes no metric for provider productivity"),
      ).toBeInTheDocument();
    });

    it("never prints a model's confidence on the default surface", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(<ClarificationPrompt clarification={OFFERS_OPTIONS} />);

      expect(screen.queryByText(/confidence/i)).not.toBeInTheDocument();
      expect(screen.queryByText(/0\.78/)).not.toBeInTheDocument();
      expect(card().textContent).not.toContain(CONFIDENCE_REASON);
    });

    it("keeps every other engine counter off the default surface too", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(
        <ClarificationPrompt
          clarification={{
            ...OFFERS_OPTIONS,
            reason:
              "turn classification confidence 0.62; options_dropped=1; " +
              "CLARIFICATION_REPEATED: ask 2 narrowed 4 option(s) to 2 from the reply",
          }}
        />,
      );

      for (const fragment of ["options_dropped", "CLARIFICATION_REPEATED", "ask 2", "0.62"]) {
        expect(card().textContent).not.toContain(fragment);
      }
    });

    it("hands the whole reason back, verbatim, in debug", () => {
      useSessionStore.setState({
        submit: vi.fn(),
        streamingTurnId: null,
        settings: { ...DEFAULT_SETTINGS, debug: true },
      });
      render(<ClarificationPrompt clarification={OFFERS_OPTIONS} />);

      // Hidden from the reader, not dropped from the client: debug is
      // where the engine's vocabulary lives, and it is the untouched
      // string rather than a summary of it.
      expect(screen.getByText(CONFIDENCE_REASON)).toBeInTheDocument();
      // Still neutral — debug adds detail, it does not change the register.
      expect(card()).toHaveAttribute("data-clarification-register", "neutral");
    });
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

    /**
     * THE MARKER IS THE ENGINE'S CLAIM, AND ONLY THE ENGINE MAY MAKE IT.
     *
     * This case used to assert the opposite — that an empty option list
     * takes the loud shape whether or not the marker explains it, because
     * "a blank button row is not made honest by the absence of a marker".
     * Measured live against the running stack, that reasoning was wrong:
     *
     *   Q  "what is the denial rate for UnitedHealthcare?"
     *   A  reason (verbatim, in debug): "PREDICATE_VALUE_UNMATCHED: payer
     *      ['UnitedHealthcare'] not in the 12 values this watermark holds"
     *      question: "There is no payer named 'UnitedHealthcare' in this
     *      data … Here are all 12 payer values this watermark holds: … .
     *      Which did you mean?"   options: []   and NO marker.
     *
     * The value guard is the best behaviour in the product. The card
     * printed "There is no answerable option to offer here." in amber, one
     * line above twelve enumerated answers — a sentence the engine never
     * said, contradicting the sentence beneath it. An empty array is a
     * fact about a button ROW; it is not a claim about what the platform
     * can offer, and the client may not promote one into the other.
     */
    it("does not claim a refusal the engine did not mark — the live value guard", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(
        <ClarificationPrompt
          clarification={{
            question:
              "There is no payer named 'UnitedHealthcare' in this data — so I stopped rather " +
              "than answer over an empty population. Here are all 12 payer values this " +
              "watermark holds: 'Ashvale Health Plan', 'Atlas Commercial'. Which did you mean?",
            options: [],
            reason:
              "PREDICATE_VALUE_UNMATCHED: payer ['UnitedHealthcare'] not in the 12 values " +
              "this watermark holds",
          }}
        />,
      );

      expect(screen.getByRole("group")).toHaveAttribute(
        "data-clarification-register",
        "neutral",
      );
      expect(
        screen.queryByText("There is no answerable option to offer here."),
      ).not.toBeInTheDocument();
      // The engine's question — the twelve real payers — is the lead.
      expect(screen.getByText(/Which did you mean\?/)).toBeInTheDocument();
    });

    /**
     * THE REGISTER SURVIVES THE COPY DISCIPLINE.
     *
     * `reason` is customer copy, and the API boundary takes internal enums
     * out of it — the marker included. So the register also travels as the
     * server's own classified warning, and this card reads that: without
     * it, a genuine dead end would arrive with the marker stripped and be
     * rendered as an ordinary question.
     */
    it("takes the loud register from the server's coded declaration", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(
        <ClarificationPrompt
          clarification={{
            question: "What would you like me to measure?",
            options: [],
            // The marker is gone: this is the reason as the boundary
            // publishes it on the terminal payload.
            reason: "This pack publishes no metric for provider productivity",
          }}
          noOptions
        />,
      );

      expect(screen.getByRole("group")).toHaveAttribute(
        "data-clarification-register",
        "refusal",
      );
      expect(
        screen.getByText("There is no answerable option to offer here."),
      ).toBeInTheDocument();
    });

    it("stays neutral when the server declared options were offered", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(
        <ClarificationPrompt
          clarification={{
            question: "By 'that', do you mean F2 — Cash posted, July 2026?",
            options: ["F2 — Cash posted, July 2026"],
          }}
          noOptions={false}
        />,
      );

      expect(screen.getByRole("group")).toHaveAttribute(
        "data-clarification-register",
        "neutral",
      );
      expect(
        screen.getByRole("button", { name: "F2 — Cash posted, July 2026" }),
      ).toBeInTheDocument();
    });

    it("names the composer as the answer when there are no chips to be an alternative to", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(
        <ClarificationPrompt
          clarification={{ question: "Which did you mean?", options: [], reason: "Low match." }}
        />,
      );
      // Not "Or say it differently…" — there is no "it" to say differently.
      expect(screen.getByPlaceholderText("Answer in your own words…")).toBeInTheDocument();
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

  /**
   * B-01. A clarification rebuilt from the stored investigation is a
   * RECORD of a question, and the store keeps neither its wording nor the
   * options it offered. Read through the no-options branch — which keys on
   * `options.length === 0` — every restored clarification on the permalink
   * announced "There is no answerable option to offer here." over turns
   * that had each offered four real interpretations live.
   *
   * The permalink is the demo path: the code's own comments call it "the
   * one question every buyer asks in the first demo".
   */
  describe("a restored clarification is history, not a refusal", () => {
    const RESTORED: ClarificationData = { question: "", options: [], restored: true };

    it("never claims the platform had nothing to offer", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(<ClarificationPrompt clarification={RESTORED} />);

      expect(
        screen.queryByText("There is no answerable option to offer here."),
      ).not.toBeInTheDocument();
      expect(screen.getByText("This answer ended with a question")).toBeInTheDocument();
      expect(
        screen.getByText(/The wording of the question and the interpretations it offered/),
      ).toBeInTheDocument();
    });

    it("offers no dead controls — the composer below is where this continues", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(<ClarificationPrompt clarification={RESTORED} />);

      expect(screen.queryAllByRole("button")).toEqual([]);
      expect(screen.queryByPlaceholderText(/Ask it a different way/)).not.toBeInTheDocument();
    });

    it("shows the question and options when the store DID keep them, as a record", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(
        <ClarificationPrompt
          clarification={{
            question: "Which did you mean?",
            options: ["Investigate the denial-rate increase this month"],
            restored: true,
          }}
        />,
      );

      expect(screen.getByText("Which did you mean?")).toBeInTheDocument();
      expect(
        screen.getByText("Investigate the denial-rate increase this month"),
      ).toBeInTheDocument();
      // Listed, not tapped: the option that was taken is the turn below.
      expect(screen.queryAllByRole("button")).toEqual([]);
      expect(screen.getByText(/a record of what was offered, not live choices/)).toBeInTheDocument();
    });

    it("leaves a LIVE marked refusal exactly as it was", () => {
      useSessionStore.setState({ submit: vi.fn(), streamingTurnId: null });
      render(
        <ClarificationPrompt
          clarification={{
            question: "Which did you mean?",
            options: [],
            reason: "Low match.; CLARIFICATION_NO_OPTIONS",
          }}
        />,
      );
      expect(screen.getByText("There is no answerable option to offer here.")).toBeInTheDocument();
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
