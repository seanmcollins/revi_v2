"use client";

import { ArrowUpRight, Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";

import { AnswerCard } from "@/components/answer/AnswerCard";
import { ContractDriftBanner } from "@/components/banners/ContractDriftBanner";
import { WatermarkBanner } from "@/components/banners/WatermarkBanner";
import { ReconciliationBanner } from "@/components/banners/ReconciliationBanner";
import type { TurnSubmission } from "@/lib/driver";
import { describeRefinement } from "@/lib/format";
import { GUIDE_QUESTIONS } from "@/lib/guideQuestions";
import { useSessionStore } from "@/lib/store";
import { scrollIntoViewRespectingMotion } from "@/lib/useReducedMotion";

/** The investigation thread: user turns right-aligned, answers full-width. */
export function ChatThread() {
  const turns = useSessionStore((s) => s.turns);
  const showFailurePreview = useSessionStore((s) => s.showFailurePreview);
  const switchingSessionId = useSessionStore((s) => s.switchingSessionId);
  const endRef = useRef<HTMLDivElement>(null);
  const lastEventCount = useRef(0);

  // Follow the stream: scroll as content arrives.
  const streamSignature = turns
    .map((t) => t.answer.narrative.length + t.answer.findings.length * 100)
    .join(",");
  useEffect(() => {
    if (streamSignature.length !== lastEventCount.current) {
      lastEventCount.current = streamSignature.length;
      scrollIntoViewRespectingMotion(endRef.current, { block: "end" });
    }
  }, [streamSignature]);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-6">
      <ContractDriftBanner />
      <WatermarkBanner />
      {showFailurePreview && (
        <div className="space-y-1">
          <p className="text-[0.62rem] uppercase tracking-wide text-muted-foreground">
            Failure-state preview (demo toggle)
          </p>
          <ReconciliationBanner
            result={{
              status: "failed",
              detail: "failed measures: cash_posted",
              summary: "status=failed; failed measures: cash_posted",
            }}
          />
        </div>
      )}

      {turns.length === 0 &&
        (switchingSessionId ? <SwitchingState /> : <EmptyState />)}

      {turns.map((turn, i) => (
        <article key={turn.id} id={`lineage-turn-${turn.id}`} className="fade-up space-y-3">
          <UserBubble submission={turn.submission} />
          <AnswerCard turn={turn} active={i === turns.length - 1} />
        </article>
      ))}
      <div ref={endRef} />
    </div>
  );
}

/**
 * The user's side of a turn: an utterance or clarification reply as text,
 * a pure gesture turn as its typed operator chips — no NL in the loop.
 */
function UserBubble({ submission }: { submission: TurnSubmission }) {
  const text = submission.utterance ?? submission.clarificationResponse;
  if (text) {
    return (
      <div className="flex justify-end">
        <p className="max-w-[85%] rounded-2xl rounded-br-md bg-secondary px-4 py-2 text-[0.82rem] leading-snug">
          {text}
        </p>
      </div>
    );
  }
  if (submission.refinements && submission.refinements.length > 0) {
    return (
      <div className="flex justify-end">
        <div className="flex max-w-[85%] flex-wrap justify-end gap-1 rounded-2xl rounded-br-md bg-secondary px-4 py-2">
          {submission.refinements.map((refinement, i) => (
            <code
              key={`${refinement.op}-${i}`}
              className="rounded border bg-surface-sunken px-1.5 py-0.5 font-mono text-[0.62rem]"
            >
              {describeRefinement(refinement)}
            </code>
          ))}
        </div>
      </div>
    );
  }
  return null;
}

/**
 * The landing hero: wordmark, one soft radial glow, and the eight guide
 * questions as elegant chips. First impression is a product, not a dev
 * tool — the composer below stays put (zero layout jank on first turn).
 *
 * The chip grid is sized by CONTAINER width, not viewport: the thread sits
 * in a middle column whose width is set by the rails, so a viewport
 * breakpoint would fire at the wrong moment.
 */
function EmptyState() {
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const replaying = useSessionStore((s) => s.replaying);
  // A brand-new session bootstrapping (newChat()) shows this same hero —
  // there is nothing to switch to yet — but its guide questions stay
  // disabled until the fresh session exists server-side to submit into.
  const newChatPending = useSessionStore((s) => s.newChatPending);
  const busy = streaming || replaying || newChatPending;

  return (
    <div className="@container relative flex flex-col items-center gap-7 pb-8 pt-10 text-center">
      <div aria-hidden className="hero-glow pointer-events-none absolute inset-x-0 -top-16 h-[30rem]" />

      <div className="fade-up relative space-y-3">
        <p className="text-[0.6rem] font-medium uppercase tracking-[0.3em] text-muted-foreground">
          RCM Investigations
        </p>
        <h2 className="text-[3.25rem] font-semibold leading-none tracking-[-0.045em]">Revi</h2>
        <p className="mx-auto max-w-md text-pretty text-[0.83rem] leading-relaxed text-muted-foreground">
          Ask about cash, denials, AR. Every answer states the window, scope,
          cohort and data date it used — and every number traces back to the
          query behind it.
        </p>
      </div>

      {/* Editorial section rule: hairline, then a squared eyebrow at the
          left margin — the label names the block, the rule anchors it. */}
      <div className="relative w-full space-y-2.5">
        <div className="flex items-center gap-2 border-t pt-2.5">
          <span aria-hidden className="accent-gradient size-1.5 shrink-0 rounded-[1px]" />
          <p className="text-[0.58rem] font-medium uppercase tracking-[0.1em] text-muted-foreground">
            Start with
          </p>
        </div>
        <div className="grid w-full items-stretch gap-1.5 text-left @lg:grid-cols-2">
          {GUIDE_QUESTIONS.map((question, i) => (
            <button
              key={question}
              type="button"
              disabled={busy}
              onClick={() => void submit({ utterance: question })}
              className="fade-up group flex items-center justify-between gap-2 rounded-lg border bg-card/55 px-3 py-2 text-left text-[0.72rem] leading-snug backdrop-blur-sm transition-all duration-150 hover:-translate-y-px hover:border-ring/40 hover:bg-card hover:shadow-sm disabled:opacity-50"
              style={{ animationDelay: `${80 + i * 40}ms` }}
            >
              <span className="text-pretty">{question}</span>
              <ArrowUpRight className="size-3 shrink-0 text-muted-foreground opacity-0 transition-opacity duration-150 group-hover:opacity-100" />
            </button>
          ))}
        </div>
      </div>

      <p
        className="fade-up relative text-[0.64rem] text-muted-foreground"
        style={{ animationDelay: "400ms" }}
      >
        Same question, same answer · governed metrics · auditable evidence
        <span aria-hidden className="mx-2 text-border">·</span>
        <kbd className="rounded border bg-surface-sunken px-1 py-0.5 font-mono text-[0.58rem]">⌘K</kbd>{" "}
        to command
      </p>
    </div>
  );
}

/**
 * Between `switchSession()` clearing the thread and the re-opened session's
 * turns landing, `turns.length` is briefly 0 — the same signal the empty
 * hero renders on. Showing the hero there would read as "no turns yet, ask
 * something", which is false: the turns exist, they are just still
 * loading. This says the true thing instead, and never invites a click the
 * store would have to no-op.
 */
function SwitchingState() {
  return (
    <div
      role="status"
      className="flex flex-col items-center gap-3 pb-8 pt-24 text-center text-muted-foreground"
    >
      <Loader2 className="size-5 animate-spin" />
      <p className="text-[0.78rem]">Opening that session…</p>
    </div>
  );
}
