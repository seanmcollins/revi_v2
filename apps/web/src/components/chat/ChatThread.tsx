"use client";

import { useEffect, useRef } from "react";

import { AnswerCard } from "@/components/answer/AnswerCard";
import { WatermarkBanner } from "@/components/banners/WatermarkBanner";
import { ReconciliationBanner } from "@/components/banners/ReconciliationBanner";
import { useSessionStore } from "@/lib/store";

/** The investigation thread: user turns right-aligned, answers full-width. */
export function ChatThread() {
  const turns = useSessionStore((s) => s.turns);
  const showFailurePreview = useSessionStore((s) => s.showFailurePreview);
  const endRef = useRef<HTMLDivElement>(null);
  const lastEventCount = useRef(0);

  // Follow the stream: scroll as content arrives.
  const streamSignature = turns
    .map((t) => t.answer.narrative.length + t.answer.findings.length * 100)
    .join(",");
  useEffect(() => {
    if (streamSignature.length !== lastEventCount.current) {
      lastEventCount.current = streamSignature.length;
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
    }
  }, [streamSignature]);

  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-6 py-6">
      <WatermarkBanner />
      {showFailurePreview && (
        <div className="space-y-1">
          <p className="text-[0.62rem] uppercase tracking-wide text-muted-foreground">
            Failure-state preview (demo toggle)
          </p>
          <ReconciliationBanner
            result={{
              status: "failed",
              detail:
                "Payer rows sum to −$191,412.06 against a parent decline of −$193,525.79 — a $2,113.73 gap. One frame is stale or a payer mapping changed mid-window.",
              parentCents: -19_352_579,
              childSumCents: -19_141_206,
            }}
          />
        </div>
      )}

      {turns.length === 0 && <EmptyState />}

      {turns.map((turn) => (
        <article key={turn.id} id={`lineage-turn-${turn.id}`} className="space-y-3">
          {turn.submission.utterance && (
            <div className="flex justify-end">
              <p className="max-w-[85%] rounded-2xl rounded-br-md bg-secondary px-4 py-2 text-[0.82rem] leading-snug">
                {turn.submission.utterance}
              </p>
            </div>
          )}
          <AnswerCard turn={turn} />
        </article>
      ))}
      <div ref={endRef} />
    </div>
  );
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center gap-3 py-16 text-center">
      <div className="flex size-12 items-center justify-center rounded-xl border border-verified/40 bg-verified/10">
        <span className="font-mono text-lg font-semibold text-verified">R</span>
      </div>
      <div>
        <h2 className="text-[0.95rem] font-semibold">Start an investigation</h2>
        <p className="mt-1 max-w-sm text-[0.75rem] leading-relaxed text-muted-foreground">
          Ask a question, or replay the golden five-turn drill-down from the rail.
          Every answer pins its window, scope, cohort, and watermark — and every
          number traces to a probe you can audit.
        </p>
      </div>
    </div>
  );
}
