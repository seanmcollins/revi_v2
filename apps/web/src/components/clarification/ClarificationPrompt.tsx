"use client";

import { MessageCircleQuestion, SlidersHorizontal } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useSessionStore } from "@/lib/store";
import type { ClarificationData } from "@/lib/types";

/** A leading stable code ("TURN_BUDGET_EXHAUSTED: spent …") on a reason. */
const REASON_CODE = /^([A-Z][A-Z0-9_]{3,}): ([\s\S]+)$/;

/**
 * Clarification is a first-class successful turn, never an error: the
 * interpreter asks instead of guessing. Options are answerable
 * interpretations rendered as buttons, plus free text.
 *
 * The `reason` the server sends is often prefixed with a stable §12 code.
 * The sentence after it is the honest explanation and is always shown; the
 * code itself is engine vocabulary and appears in debug mode only — same
 * meaning, one fewer thing to decode mid-investigation.
 */
export function ClarificationPrompt({ clarification }: { clarification: ClarificationData }) {
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const debug = useSessionStore((s) => s.settings.debug);
  const openSettings = useSessionStore((s) => s.openSettings);
  const [freeText, setFreeText] = useState("");

  const match = clarification.reason ? REASON_CODE.exec(clarification.reason) : null;
  const reasonCode = match?.[1];
  const reasonText = debug ? clarification.reason : (match?.[2] ?? clarification.reason);
  // The one clarification whose recovery lives in a control rather than in
  // a reply: the per-turn cost ceiling is set in the settings panel.
  const budgetExhausted = reasonCode === "TURN_BUDGET_EXHAUSTED";

  const choose = (text: string) => {
    // Replies travel on the dedicated clarification channel — the API
    // receives `clarification_response`, not a fresh utterance.
    if (!streaming) void submit({ clarificationResponse: text });
  };

  return (
    <div className="rounded-lg border border-grade-derived/40 bg-grade-derived/5 p-3.5">
      <div className="flex items-start gap-2.5">
        <MessageCircleQuestion className="mt-0.5 size-4 shrink-0 text-grade-derived" />
        <div className="min-w-0 flex-1 space-y-2.5">
          <p className="text-[0.8rem] font-medium leading-snug">{clarification.question}</p>
          {reasonText && (
            <p className="text-[0.7rem] leading-snug text-muted-foreground">{reasonText}</p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {clarification.options.map((option) => (
              <Button
                key={option}
                variant="outline"
                size="sm"
                disabled={streaming}
                className="h-7 rounded-full text-xs font-normal"
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
              placeholder="Or say it differently…"
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
