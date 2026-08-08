"use client";

import { MessageCircleQuestion } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useSessionStore } from "@/lib/store";
import type { ClarificationData } from "@/lib/types";

/**
 * Clarification is a first-class successful turn, never an error: the
 * interpreter asks instead of guessing. Options are answerable
 * interpretations rendered as buttons, plus free text.
 */
export function ClarificationPrompt({ clarification }: { clarification: ClarificationData }) {
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const [freeText, setFreeText] = useState("");

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
          {clarification.reason && (
            <p className="text-[0.7rem] leading-snug text-muted-foreground">
              {clarification.reason}
            </p>
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
