"use client";

import { CornerDownLeft, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useSessionStore } from "@/lib/store";

/**
 * Multiline composer. Enter sends, Shift+Enter breaks; disabled while a
 * turn streams (the pipeline is single-flight per session).
 */
export function TurnInput({ suggestions }: { suggestions: string[] }) {
  const [value, setValue] = useState("");
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const pending = useSessionStore((s) => s.pendingRefinements.length);

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    setValue("");
    void submit({ utterance: trimmed });
  };

  return (
    <div className="space-y-2">
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {suggestions.map((s) => (
            <button
              key={s}
              type="button"
              disabled={streaming}
              onClick={() => send(s)}
              className="rounded-full border bg-surface-sunken px-2.5 py-1 text-[0.68rem] text-muted-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>
      )}
      <form
        className="relative"
        onSubmit={(e) => {
          e.preventDefault();
          send(value);
        }}
      >
        <Textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(value);
            }
          }}
          placeholder={
            streaming ? "Investigating…" : "Ask about cash, denials, AR… (Enter to send)"
          }
          disabled={streaming}
          rows={2}
          className="max-h-40 resize-none pr-12 text-[0.8rem]"
        />
        <Button
          type="submit"
          size="icon-sm"
          disabled={streaming || !value.trim()}
          aria-label="Send"
          className="absolute bottom-2 right-2"
        >
          {streaming ? (
            <LoaderCircle className="animate-spin" />
          ) : (
            <CornerDownLeft />
          )}
        </Button>
      </form>
      <p className="num text-[0.62rem] text-muted-foreground">
        {pending > 0
          ? `${pending} typed refinement${pending === 1 ? "" : "s"} queued (logged to console — API lands in M8)`
          : "Answers are computed by the deterministic kernel; the model never invents numbers."}
      </p>
    </div>
  );
}
