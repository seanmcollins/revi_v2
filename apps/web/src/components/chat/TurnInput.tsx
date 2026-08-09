"use client";

import { CornerDownLeft, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useSessionStore } from "@/lib/store";

/**
 * Multiline composer. Enter sends, Shift+Enter breaks; disabled while a
 * turn streams (the pipeline is single-flight per session) or while a
 * reference-demo replay is submitting its own sequential turns.
 */
export function TurnInput({ suggestions }: { suggestions: string[] }) {
  const [value, setValue] = useState("");
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const replaying = useSessionStore((s) => s.replaying);
  const switching = useSessionStore((s) => s.switchingSessionId !== null);
  const newChatPending = useSessionStore((s) => s.newChatPending);
  // Switching is included so the composer cannot look live while the store
  // would drop the turn: a submit mid-switch has no session to land in.
  // newChatPending too: the bootstrap for a fresh backend session is brief
  // but real, and a turn sent into it would race newSession() for which
  // session it lands in.
  const busy = streaming || replaying || switching || newChatPending;
  const pending = useSessionStore((s) => s.pendingRefinements.length);
  const mode = useSessionStore((s) => s.connection.mode);

  // Disabling the textarea while a turn runs drops the caret on the floor:
  // the browser blurs a control that becomes disabled and puts focus on
  // <body>, so a keyboard-only analyst had to Tab back into the composer
  // after every single answer. Give it back the moment the pipeline frees.
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const wasBusy = useRef(busy);
  useEffect(() => {
    if (wasBusy.current && !busy) composerRef.current?.focus();
    wasBusy.current = busy;
  }, [busy]);

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
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
              disabled={busy}
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
          id="turn-composer"
          ref={composerRef}
          aria-label="Ask a question"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send(value);
            }
          }}
          placeholder={
            replaying
              ? "Replaying the reference demo…"
              : switching
                ? "Opening that session…"
                : newChatPending
                  ? "Starting a new chat…"
                  : streaming
                    ? "Investigating…"
                    : "Ask about cash, denials, AR… (Enter to send)"
          }
          disabled={busy}
          rows={2}
          className="max-h-40 resize-none pr-12 text-[0.8rem]"
        />
        <Button
          type="submit"
          size="icon-sm"
          disabled={busy || !value.trim()}
          aria-label="Send"
          className="absolute bottom-2 right-2"
        >
          {busy ? (
            <LoaderCircle className="animate-spin" />
          ) : (
            <CornerDownLeft />
          )}
        </Button>
      </form>
      <p className="num text-[0.62rem] text-muted-foreground">
        {pending > 0
          ? mode === "api"
            ? `${pending} typed refinement${pending === 1 ? "" : "s"} queued — submitting when this turn completes`
            : `${pending} typed refinement${pending === 1 ? "" : "s"} queued (logged to console — mock driver)`
          : "Every number is computed from your data — the model reads the question and writes the answer, it never makes the numbers up."}
      </p>
    </div>
  );
}
