"use client";

import { AlertTriangle, CornerDownLeft, LoaderCircle, Telescope } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { ResearchLaunchCard } from "@/components/research/ResearchLaunchCard";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Textarea } from "@/components/ui/textarea";
import { allOpenSelector, offerFromPreview } from "@/lib/deepResearch";
import { useSessionStore } from "@/lib/store";
import { useResearchPreview } from "@/lib/useDeepResearch";

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
 *
 * TWO WAYS TO SPEND THE SAME SENTENCE, AND THE BOX DOES NOT CHANGE.
 *
 * Enter asks the question and gets an answer in seconds. "Deep research"
 * takes the SAME text and spends about a minute on it instead. Which one a
 * question deserves is the analyst's call and nobody else's, so the choice
 * is a second control beside Send rather than a mode, a prefix, a toggle or
 * a guess made from the wording. Nothing about the ordinary path moves:
 * Enter still sends, Shift+Enter still breaks the line, and the deep
 * research control is disabled under exactly the conditions Send is.
 *
 * IT PREVIEWS; IT DOES NOT LAUNCH. The press resolves what a run WOULD do
 * — the readings, the reasons, whether the data can answer this at all —
 * and opens the confirmation card over it. That is a real request and it
 * says so while it runs. The composer is NOT cleared: nothing has been
 * spent, the sentence is still the analyst's, and a box emptied by a
 * preview would have thrown away the question if they closed the card.
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

  /**
   * A QUESTION THE PAGE OFFERED, taken into the box the analyst types in.
   *
   * "Ask about this" on an expanded monitor puts a plain-English question
   * in `composerDraft`; this is where it lands. Three properties, and all
   * three are the point:
   *
   *   IT IS EDITABLE, because it is just text in the textarea. Nothing
   *     hidden travels with it — no scope, no spec, no referent — so what
   *     the analyst reads is the whole of what they will send.
   *   IT DOES NOT SEND ITSELF. The caret goes to the end and the analyst
   *     presses Enter, or rewrites it, or deletes it.
   *   IT IS ONE-SHOT. The slot is cleared as it is taken, so nothing
   *     re-fills the box behind somebody who cleared it, and offering the
   *     same question twice prefills twice.
   */
  const draft = useSessionStore((s) => s.composerDraft);
  const clearComposerDraft = useSessionStore((s) => s.clearComposerDraft);
  const [taken, setTaken] = useState("");
  /**
   * Taken DURING RENDER, not in an effect. React's own "adjust state when
   * an input changes" pattern: an effect here would paint the empty box
   * first and the offer a frame later, and it is a cascading render the
   * lint rule is right to refuse. `taken` is the latch that makes it run
   * once per offer — and it resets when the store slot clears below, so
   * offering the same question twice prefills twice.
   */
  if (draft !== taken) {
    setTaken(draft);
    if (draft !== "") setValue(draft);
  }
  useEffect(() => {
    if (taken === "") return;
    // One-shot: nothing re-fills the box behind somebody who cleared it.
    clearComposerDraft();
    const node = composerRef.current;
    if (!node) return;
    node.focus();
    // The caret at the END, not selecting the offer: a prefill that arrives
    // selected is one keystroke from being wiped by somebody who meant to
    // add a word to it.
    node.setSelectionRange(taken.length, taken.length);
    node.scrollIntoView({ block: "nearest" });
  }, [taken, clearComposerDraft]);

  const send = (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || busy) return;
    setValue("");
    if (onAsk) onAsk(trimmed);
    else void submit({ utterance: trimmed });
  };

  /**
   * THE DEEP-RESEARCH PRESS, in three states and no fourth.
   *
   * The card opens only once the server has answered, because what the
   * card is FOR is the answer: an empty confirmation shown instantly and
   * filled in a second later would ask a reader to decide before there is
   * anything to decide about.
   */
  const {
    preview,
    pending: previewing,
    error: previewError,
    request: previewRun,
  } = useResearchPreview();
  const [cardOpen, setCardOpen] = useState(false);
  const [asked, setAsked] = useState("");

  const research = () => {
    const trimmed = value.trim();
    if (!trimmed || busy) return;
    setAsked(trimmed);
    void (async () => {
      // The card opens on the ANSWER, not on the press: only a resolved
      // preview has anything in it to decide about.
      const resolved = await previewRun(allOpenSelector(), trimmed);
      if (resolved) setCardOpen(true);
    })();
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
          className="max-h-40 resize-none pr-40 text-body"
        />
        <div className="absolute bottom-2 right-2 flex items-center gap-1.5">
          {/* A REAL BUTTON, IN THE TAB ORDER, ALWAYS THERE.
              Not a hover affordance and not a menu item: this is one of
              the two things that can be done with what is in the box, and
              a control that does not exist on a touch screen or in a
              screenshot is not a quiet control, it is an absent one. The
              accessible name says what the press DOES — it opens the
              confirmation — because a name promising a launch on a
              control that opens a card would tell a screen-reader user a
              minute of work had started when nothing had. */}
          <Popover
            open={cardOpen}
            onOpenChange={(next) => {
              // Only CLOSING is the popover's to decide. It opens when
              // the server has answered and not when the trigger is
              // pressed, so an empty card can never appear.
              if (!next) setCardOpen(false);
            }}
          >
            <PopoverTrigger asChild>
              <Button
                type="button"
                variant="ghost"
                size="xs"
                data-research-composer
                disabled={busy || !value.trim()}
                aria-label="Deep research on this question — see what it will look at"
                onClick={research}
                className="h-8 gap-1 rounded-md px-2 text-meta font-normal text-muted-foreground transition-colors duration-150 hover:text-foreground"
              >
                {previewing ? (
                  <LoaderCircle aria-hidden className="size-3 animate-spin" />
                ) : (
                  <Telescope aria-hidden className="size-3" />
                )}
                Deep research
              </Button>
            </PopoverTrigger>
            <PopoverContent align="end" className="w-[26rem] max-w-[calc(100vw-2rem)] p-3">
              {preview && (
                <ResearchLaunchCard
                  offer={offerFromPreview(preview, asked)}
                  variant="compact"
                  onLaunched={() => setCardOpen(false)}
                />
              )}
            </PopoverContent>
          </Popover>
          <Button
            type="submit"
            size="icon-sm"
            disabled={busy || !value.trim()}
            aria-label="Send"
          >
            {busy ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <CornerDownLeft />
            )}
          </Button>
        </div>
      </form>

      {/* WHILE IT RESOLVES, AND IF IT DOES NOT. The pending line is a
          status because it is not news; the failure is an alert because
          the analyst pressed something and it did not happen. The server's
          own sentence is printed verbatim — it is the one that knows why —
          and the box is left exactly as it was. */}
      {previewing && (
        <p role="status" className="text-micro leading-snug text-muted-foreground">
          Working out what deep research would look at…
        </p>
      )}
      {previewError && (
        <p
          role="alert"
          className="flex items-start gap-1.5 text-micro leading-snug text-negative"
        >
          <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
          {previewError}
        </p>
      )}
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
