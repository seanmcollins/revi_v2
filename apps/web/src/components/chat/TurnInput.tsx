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
 *
 * TWO OPTIONAL PROPS, BOTH FOR HOME, NEITHER A FORK.
 *
 * `onAsk` replaces what happens with the typed text, not how it is typed:
 * Home submits through the same store action and then navigates into the
 * session the turn mints, because Home renders no thread for the answer to
 * arrive in. Defaulting to the store keeps the workspace's behaviour
 * byte-identical.
 *
 * `autoFocus` because Home IS the composer's page — "New chat" lands here
 * and the analyst should be typing, not tabbing. The workspace does not
 * pass it: a session being re-opened must not steal focus from the thread
 * being read.
 */
export function TurnInput({
  suggestions,
  onAsk,
  autoFocus = false,
}: {
  suggestions: string[];
  onAsk?: (utterance: string) => void;
  autoFocus?: boolean;
}) {
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

  // Own effect, own dependency list: this fires once on mount, where the
  // one above fires on every busy→idle edge.
  useEffect(() => {
    if (autoFocus) composerRef.current?.focus();
  }, [autoFocus]);

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setValue("");
    if (onAsk) onAsk(trimmed);
    else void submit({ utterance: trimmed });
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
              className="rounded-full border bg-surface-sunken px-2.5 py-1 text-meta text-muted-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground disabled:opacity-50"
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
          className="max-h-40 resize-none pr-12 text-body"
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
      {/* The composer says something only when it has something to say.
          The standing disclaimer ("every number is computed from your
          data…") belongs on the empty state, where a first-time reader
          meets the product, and the integrity line under every answer is
          where the same promise is kept per answer. This line is only for
          what is happening right now: queued clicks waiting on the
          pipeline. */}
      {pending > 0 && (
        <p className="num text-micro text-muted-foreground">
          {mode === "api"
            ? `${pending} refinement${pending === 1 ? "" : "s"} queued — submitting when this answer lands`
            : `${pending} refinement${pending === 1 ? "" : "s"} queued — the mock fixture has nowhere to send them`}
        </p>
      )}
    </div>
  );
}
