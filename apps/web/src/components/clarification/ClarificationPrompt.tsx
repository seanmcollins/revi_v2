"use client";

import { CircleAlert, History, MessageCircleQuestion, SlidersHorizontal } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useSessionStore } from "@/lib/store";
import type { ClarificationData } from "@/lib/types";

/** A leading stable code ("TURN_BUDGET_EXHAUSTED: spent …") on a reason. */
const REASON_CODE = /^([A-Z][A-Z0-9_]{3,}): ([\s\S]+)$/;

/**
 * The engine's marker for a clarification that offers NOTHING to choose
 * from (`submit_turn.NO_OPTIONS_REASON`).
 *
 * Appended to the reason, never prefixed — the reason's own leading code is
 * what every other reader keys off, and the server states that moving it
 * would break them to label this. So it is matched at the tail, not by
 * `REASON_CODE` above, and it is stripped from what the analyst reads: it
 * is a shape instruction for this component, not a sentence for a human.
 */
const NO_OPTIONS_MARKER = "CLARIFICATION_NO_OPTIONS";
const NO_OPTIONS_TAIL = /\s*;?\s*CLARIFICATION_NO_OPTIONS\s*$/;

/**
 * Clarification is a first-class successful turn, never an error: the
 * interpreter asks instead of guessing. Options are answerable
 * interpretations rendered as buttons, plus free text.
 *
 * The `reason` the server sends is often prefixed with a stable §12 code.
 * The sentence after it is the honest explanation and is always shown; the
 * code itself is engine vocabulary and appears in debug mode only — same
 * meaning, one fewer thing to decode mid-investigation.
 *
 * ONE clarification is not a question at all. When the interpreter can
 * offer no answerable interpretation it marks the reason
 * `CLARIFICATION_NO_OPTIONS`, and what shipped for that was a question mark
 * above an empty row of buttons — a prompt asking the analyst to pick from
 * nothing, twice, live, once after a tenth of a dollar was spent deciding
 * the platform could not do it. That state renders as a statement of what
 * the platform needs instead, with the free-text composer as its only
 * recovery, because that is the only recovery there is.
 *
 * AND ONE IS NOT A PROMPT AT ALL. A clarification rebuilt from the stored
 * investigation is a record of a question that was asked and answered
 * turns ago, and the store keeps neither its wording nor the options it
 * offered. Read through the no-options branch — which keys on
 * `options.length === 0` — every restored clarification on the permalink
 * announced "There is no answerable option to offer here." over turns that
 * had each offered four real interpretations live. `restored` separates
 * "stored without its options" from "the engine offered none": the card
 * states what it is, shows whatever the store DID keep, and offers no
 * controls, because the composer at the foot of the thread is where this
 * conversation actually continues.
 */
export function ClarificationPrompt({ clarification }: { clarification: ClarificationData }) {
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const debug = useSessionStore((s) => s.settings.debug);
  const openSettings = useSessionStore((s) => s.openSettings);
  const [freeText, setFreeText] = useState("");

  // A clarification is a question addressed to the analyst, and it arrives
  // after focus was taken away by the disabled composer. Nothing moved
  // focus to it, so a keyboard-only or screen-reader user was left on
  // <body> in front of an unannounced prompt. Focus the first answerable
  // interpretation — the prompt text is read out with it because the
  // options are inside the labelled group.
  const firstOptionRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    firstOptionRef.current?.focus();
    // Re-runs when a NEW clarification lands, not on every render.
  }, [clarification.question]);

  // Rebuilt from history. Checked FIRST, because everything below it is a
  // statement about what a LIVE interpreter offered, and a restored turn
  // has not told this card anything about that.
  const restored = clarification.restored === true;

  // There is nothing to choose from. Keyed off the engine's marker, and
  // also off an options list that is simply empty — that is the same defect
  // arriving without its label, and a blank button row is not made honest
  // by the absence of a marker explaining it.
  //
  // Not on a RESTORED turn. There the empty list is an unrestored field,
  // not a claim: the server stores the turn's status and not the question
  // it asked, so reading emptiness as "the engine had nothing to offer"
  // invents the one fact the record does not contain.
  const marked = (clarification.reason ?? "").includes(NO_OPTIONS_MARKER);
  const noOptions = !restored && (marked || clarification.options.length === 0);

  const declared = (clarification.reason ?? "").replace(NO_OPTIONS_TAIL, "").trim();
  const match = declared ? REASON_CODE.exec(declared) : null;
  const reasonCode = match?.[1];
  const reasonText = debug ? clarification.reason : (match?.[2] ?? (declared || undefined));
  // The one clarification whose recovery lives in a control rather than in
  // a reply: the per-turn cost ceiling is set in the settings panel.
  const budgetExhausted = reasonCode === "TURN_BUDGET_EXHAUSTED";

  const choose = (text: string) => {
    // Replies travel on the dedicated clarification channel — the API
    // receives `clarification_response`, not a fresh utterance.
    if (!streaming) void submit({ clarificationResponse: text });
  };

  // A HISTORICAL question, rendered as one. No amber, because nothing here
  // changes how a number should be read; no chips and no composer, because
  // the reply this turn received is already the turn below it, and a Send
  // button on a question whose options were never stored is an invitation
  // to answer something the reader cannot see.
  if (restored) {
    return (
      <div
        role="group"
        aria-label={
          clarification.question
            ? `Restored clarification: ${clarification.question}`
            : "Restored clarification: its wording was not stored"
        }
        className="rounded-lg border border-dashed bg-card/60 p-3.5"
      >
        <div className="flex items-start gap-2.5">
          <History className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
          <div className="min-w-0 flex-1 space-y-2">
            <p className="text-body font-medium leading-snug">
              This turn ended with a question
            </p>
            {clarification.question !== "" && (
              <p className="text-body leading-snug text-foreground">
                {clarification.question}
              </p>
            )}
            {reasonText && (
              <p className="text-meta leading-snug text-muted-foreground">{reasonText}</p>
            )}
            {/* Whatever the store DID keep. Listed, not tapped: these are
                the interpretations that were offered at the time, and the
                one that was taken is the turn below this card. */}
            {clarification.options.length > 0 && (
              <ul className="space-y-0.5 text-meta leading-snug text-muted-foreground">
                {clarification.options.map((option) => (
                  <li key={option} className="flex gap-1.5">
                    <span aria-hidden>·</span>
                    {option}
                  </li>
                ))}
              </ul>
            )}
            <p className="text-meta leading-snug text-muted-foreground">
              {clarification.options.length > 0
                ? "Restored from this session's history — the options above are a record of what was offered, not live choices. Ask again below to continue."
                : "Restored from this session's history. The wording of the question and the interpretations it offered were not stored with the investigation — ask it again below to see them."}
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div
      role="group"
      aria-label={
        noOptions
          ? `No answerable options: ${clarification.question}`
          : `Clarification: ${clarification.question}`
      }
      // Announced whether or not the focus move above wins.
      //
      // Two effects fire in the same commit when a clarification lands:
      // the one below, which focuses the first option, and the composer's
      // own refocus when `busy` falls. Effect order follows tree position
      // and the composer is later in the tree, so it takes the focus back
      // microseconds later — leaving a question on screen that nothing
      // announced. A polite live region does not compete for focus and
      // reads the prompt either way.
      aria-live="polite"
      className={
        noOptions
          ? "rounded-lg border border-warning/40 bg-warning/5 p-3.5"
          : "rounded-lg border border-grade-derived/40 bg-grade-derived/5 p-3.5"
      }
    >
      <div className="flex items-start gap-2.5">
        {noOptions ? (
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
        ) : (
          <MessageCircleQuestion className="mt-0.5 size-4 shrink-0 text-grade-derived" />
        )}
        <div className="min-w-0 flex-1 space-y-2.5">
          {/* A statement, not a prompt. The engine's own question text is
              kept verbatim underneath — it says what was asked and is the
              most specific thing on the card — but the heading above it
              stops the card reading as "pick one" when there is no one to
              pick. */}
          {noOptions && (
            <p className="text-body font-medium leading-snug text-warning">
              There is no answerable option to offer here.
            </p>
          )}
          <p
            className={
              noOptions
                ? "text-body leading-snug text-foreground"
                : "text-body font-medium leading-snug"
            }
          >
            {clarification.question}
          </p>
          {reasonText && (
            <p className="text-meta leading-snug text-muted-foreground">{reasonText}</p>
          )}
          {/* Rendered only when there is something in it. An empty flex row
              still occupies a gap and still reads to a screen reader as the
              group's set of choices, which is precisely the state the
              engine marked this card to avoid. */}
          {(clarification.options.length > 0 || budgetExhausted) && (
            <div className="flex flex-wrap gap-1.5">
              {clarification.options.map((option, index) => (
                <Button
                  key={option}
                  ref={index === 0 ? firstOptionRef : undefined}
                  variant="outline"
                  size="sm"
                  disabled={streaming}
                  // Options are model-proposed sentences, not fixed short
                  // labels — h-auto/whitespace-normal let a long one wrap
                  // to a second line inside the pill instead of overflowing
                  // it (the fixed-height, no-wrap default is for icon-sized
                  // button text, not this).
                  className="h-auto min-h-7 whitespace-normal rounded-full py-1.5 text-left text-xs font-normal leading-snug"
                  onClick={() => choose(option)}
                >
                  {option}
                </Button>
              ))}
              {budgetExhausted && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 gap-1 rounded-full text-xs font-normal text-muted-foreground"
                  onClick={openSettings}
                >
                  <SlidersHorizontal className="size-3" />
                  Open settings
                </Button>
              )}
            </div>
          )}
          <form
            className="flex items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (freeText.trim()) choose(freeText.trim());
            }}
          >
            <Textarea
              value={freeText}
              onChange={(e) => setFreeText(e.target.value)}
              // "Or say it differently" reads as the alternative to picking
              // a chip. With no chips it is the only way forward, and the
              // placeholder says so rather than implying a road not taken.
              placeholder={noOptions ? "Ask it a different way…" : "Or say it differently…"}
              rows={1}
              className="min-h-8 flex-1 resize-none text-xs"
            />
            <Button type="submit" size="sm" variant="secondary" disabled={streaming || !freeText.trim()}>
              Send
            </Button>
          </form>
        </div>
      </div>
    </div>
  );
}
