"use client";

import { Eye } from "lucide-react";
import Link from "next/link";

import type { WatchDeclaration } from "@/lib/rounds";

/**
 * "Watching · baseline 12.4%" — the answer that also started a watch.
 *
 * Saying "watch Silverline's denial rate" in the composer is an ordinary
 * turn: same interpretation, same planning, same §6.6 validation, same
 * findings. What is different is that the answer DOUBLES AS THE BASELINE,
 * and this note is where that is said — so the analyst can see they are
 * watching the right cell before they walk away from it.
 *
 * It renders distinctly from an ordinary answer because a state changed:
 * something now exists on the server that will interrupt this person
 * tomorrow morning. But it renders QUIETLY, above the answer rather than
 * over it, because the answer is still the thing they asked for.
 *
 * Every sentence here is the server's. `statement` is composed from the
 * payload — never by a model, and never containing a figure the answer
 * does not carry — and the threshold is the compiled gate in words. A
 * confirmation written client-side would be this surface asserting a
 * number nobody measured, on the one card whose whole job is to prove the
 * right thing is being watched.
 */
export function WatchDeclarationNote({ watch }: { watch: WatchDeclaration }) {
  return (
    <aside
      data-watch-declaration={watch.pinId}
      className="fade-up flex items-start gap-2 rounded-xl border border-verified/35 bg-verified/[0.06] px-3 py-2.5"
    >
      <Eye aria-hidden className="mt-0.5 size-3 shrink-0 text-verified" />
      <div className="min-w-0 space-y-1">
        <p className="text-meta leading-snug text-foreground">{watch.statement}</p>
        <p className="num flex flex-wrap items-baseline gap-x-2 text-micro text-muted-foreground">
          {watch.baselineValueText !== "" && (
            <span>
              Baseline {watch.baselineValueText}
              {watch.baselineWatermarkId !== "" && ` at ${watch.baselineWatermarkId}`}
            </span>
          )}
          {watch.thresholdStatement !== "" && <span>{watch.thresholdStatement}</span>}
          <Link
            href="/rounds"
            className="focus-ring rounded underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
          >
            See it in Rounds
          </Link>
        </p>
        {/* What the platform READ in the utterance, rather than an
            assertion that it read intent. "keep an eye on" is a phrase
            from a closed vocabulary, and showing it back is how an analyst
            learns which words start a watch. */}
        {watch.matchedPhrase !== "" && (
          <p className="text-micro leading-snug text-muted-foreground">
            Started because you said “{watch.matchedPhrase}”.
          </p>
        )}
      </div>
    </aside>
  );
}
