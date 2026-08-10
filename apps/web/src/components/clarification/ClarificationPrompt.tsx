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
 * TWO REGISTERS, AND THEY ARE NOT THE SAME SPEECH ACT. This card was one
 * component wearing one costume, and the costume was a warning's.
 *
 *   A QUESTION WITH ANSWERS IS A NEUTRAL AFFORDANCE. "Which A/R view do
 *     you want — days in A/R, aging distribution, or balance trend?" is
 *     the product asking the reader something, with three real answers
 *     attached. It shipped under an amber rule, a CircleAlert, the
 *     sentence "There is no answerable option to offer here." and the
 *     fine print "turn classification confidence 0.78" — a refusal's
 *     costume over an ordinary question, above the three buttons that
 *     answer it. The acceptance gate named it the single scariest-feeling
 *     element in the product. It is now a quiet card: the question leads,
 *     the options are buttons, nothing is amber, and the engine's own
 *     bookkeeping is not on it.
 *   A REFUSAL KEEPS THE LOUD ONE. When the interpreter can offer no
 *     answerable interpretation it marks the reason
 *     `CLARIFICATION_NO_OPTIONS`, and what shipped for THAT was a question
 *     mark above an empty row of buttons — a prompt asking the analyst to
 *     pick from nothing, twice, live, once after a tenth of a dollar was
 *     spent deciding the platform could not do it. That state is a verdict
 *     about what the platform cannot do, it renders as a statement of what
 *     the platform needs instead, and it is not softened here.
 *
 * THE MARKER IS THE ENGINE'S CLAIM, AND ONLY THE ENGINE MAY MAKE IT. This
 * card used to take the loud register off `options.length === 0` as well,
 * reasoning that an empty list is the same defect arriving without its
 * label. Measured live, that reasoning was wrong, and expensively:
 *
 *     Q  "what is the denial rate for UnitedHealthcare?"
 *     A  reason (verbatim, debug): "PREDICATE_VALUE_UNMATCHED: payer
 *        ['UnitedHealthcare'] not in the 12 values this watermark holds"
 *        question: "There is no payer named 'UnitedHealthcare' in this
 *        data … Here are all 12 payer values this watermark holds:
 *        'Ashvale Health Plan', … 'Veritas Comp Fund'. Which did you
 *        mean?"   options: []   — and NO `CLARIFICATION_NO_OPTIONS`.
 *
 * The value guard is the best behaviour in the product: it refuses a
 * hallucinated payer, enumerates every real one, and asks which was meant.
 * The card printed "There is no answerable option to offer here." in amber
 * one line above twelve answerable options — a sentence the engine never
 * said, contradicting the sentence underneath it. An empty `options` array
 * means the buttons are missing, which is a fact about a ROW; it is not a
 * fact about whether the platform has anything to offer, and the client
 * may not promote the first into the second. So the loud register is taken
 * when the engine marks it, and never inferred.
 *
 * WHICH LEAVES A THIRD STATE, and it is honest rather than loud: a
 * question with no buttons, whose recovery is the composer under it. It
 * gets the neutral card, its own placeholder ("Answer in your own words…",
 * because "Or say it differently…" promises chips that are not there), and
 * no claim about what the platform can do.
 *
 * WHAT THE REASON IS, AND WHERE IT GOES. On a refusal the reason is the
 * honest explanation ("CAPABILITY_UNSUPPORTED: this pack publishes no
 * metric for provider productivity") and it is the most useful sentence on
 * the card, so it is shown. On a clarification that OFFERS options the
 * reason is engine bookkeeping without exception — "turn classification
 * confidence 0.78" (`interpretation.py`), "options_dropped=1",
 * "CLARIFICATION_REPEATED: ask 2 narrowed 4 option(s) to 2 from the reply"
 * — while every word written for a reader is already in the QUESTION,
 * which the engine composes for exactly that purpose. So it is not on the
 * default surface at all: it is in debug, verbatim, beside the trace that
 * explains it. A model's confidence score is never a thing a reader is
 * asked to weigh mid-investigation.
 *
 * WHICH REGISTER, AND HOW IT IS DECIDED. The backend lane is adding a
 * wire-level distinction between "offers options" and "refuses". Until it
 * lands this keys on the one thing the payload already carries that is the
 * ENGINE's own statement about it: the `CLARIFICATION_NO_OPTIONS` marker
 * on the reason. ADOPT THE WIRE MARKER WHEN IT SHIPS — watch
 * `contracts/openapi.json`'s `TurnClarification` for a kind/class field
 * and key `refusal` off that instead, on the same principle: the register
 * follows what the server declared, never what the client inferred from a
 * field's being empty.
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

  // There is nothing to choose from — a REFUSAL, and the loud register.
  //
  // The ENGINE'S marker, and nothing else. An empty `options` array is not
  // this claim: live, the value guard's own clarification enumerates all
  // twelve real payers in its question, carries no marker, and ships
  // `options: []` — and inferring the refusal from that emptiness printed
  // "There is no answerable option to offer here." over twelve answerable
  // options. (When the wire distinction lands, this is the line that reads
  // it.)
  //
  // Not on a RESTORED turn. There nothing about the live shape survived at
  // all: the server stores the turn's status and not the question it
  // asked, so any reading of this field invents a fact the record does not
  // contain.
  const refusal = !restored && (clarification.reason ?? "").includes(NO_OPTIONS_MARKER);
  /** Whether there is a row of buttons at all — a fact about the ROW. */
  const offersButtons = clarification.options.length > 0;

  const declared = (clarification.reason ?? "").replace(NO_OPTIONS_TAIL, "").trim();
  const match = declared ? REASON_CODE.exec(declared) : null;
  const reasonCode = match?.[1];
  const explanation = debug ? clarification.reason : (match?.[2] ?? (declared || undefined));
  // WHAT THE READER IS SHOWN, per register. A refusal's reason explains the
  // refusal and is the card's most useful sentence. A question's reason is
  // the engine's own bookkeeping — a classification confidence, a dropped
  // option count, an ask counter — and every reader-facing word is already
  // in the question above it, so it appears only when somebody has asked
  // for the engine's vocabulary by turning debug on.
  const reasonText = refusal || debug ? explanation : undefined;
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
      // The register, on the DOM, because it is a claim about which speech
      // act this card is making and every test of it should read the claim
      // rather than a colour token that may be re-tuned.
      data-clarification-register={refusal ? "refusal" : "neutral"}
      aria-label={
        refusal
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
        refusal
          ? "rounded-lg border border-warning/40 bg-warning/5 p-3.5"
          : // QUIET. A question the product asks its reader is an ordinary
            // affordance and gets the card surface every other calm block
            // on this thread gets — no accent rule, no tinted fill, no
            // hue that means something elsewhere in this product
            // (`grade-derived` is an EVIDENCE grade, and a question is not
            // graded evidence).
            "rounded-lg border bg-card/60 p-3.5"
      }
    >
      <div className="flex items-start gap-2.5">
        {refusal ? (
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
        ) : (
          // A speech mark, in muted ink. It says "this is a question"
          // without saying "something is wrong": the icon slot is kept so
          // the card is identifiable at a glance in a scrolled thread, and
          // the alert glyph and the warning colour are both gone from it.
          <MessageCircleQuestion className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
        )}
        <div className="min-w-0 flex-1 space-y-2.5">
          {/* A statement, not a prompt. The engine's own question text is
              kept verbatim underneath — it says what was asked and is the
              most specific thing on the card — but the heading above it
              stops the card reading as "pick one" when there is no one to
              pick. Only ever on a refusal: printed over three real
              interpretations it was simply false. */}
          {refusal && (
            <p className="text-body font-medium leading-snug text-warning">
              There is no answerable option to offer here.
            </p>
          )}
          {/* THE LEAD on the neutral card — the question is what this card
              is, and there is nothing above it. */}
          <p
            className={
              refusal
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
          {(offersButtons || budgetExhausted) && (
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
              placeholder={
                refusal
                  ? "Ask it a different way…"
                  : offersButtons
                    ? "Or say it differently…"
                    : // No chips to be an alternative TO. The composer is
                      // the whole recovery here, and the placeholder says
                      // what it is for rather than implying a road not
                      // taken.
                      "Answer in your own words…"
              }
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
