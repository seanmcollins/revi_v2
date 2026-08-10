"use client";

import { Eye, EyeOff } from "lucide-react";
import Link from "next/link";

import type { WatchDeclaration, WatchRefusal } from "@/lib/rounds";

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

/**
 * "NOTHING IS BEING WATCHED" — the other outcome of the same gesture, in
 * the same place.
 *
 * This is the note the reader could not see. The server refuses a watch it
 * cannot honour — a threshold in cents over a metric measured as a ratio —
 * and says so in a sentence that names the units this contract WOULD take;
 * that sentence was appended to `warnings` after the classified list had
 * already been built, and the client renders the classified list. Result:
 * an ordinary answer, no confirmation, no warning, and an analyst who
 * walks away believing they are being watched. The server's own code
 * comment names that outcome as the worst one available.
 *
 * So it renders exactly where the confirmation would have gone, at the
 * same weight, in the refusal's tone rather than the confirmation's — and
 * `role="alert"`, because a state the reader was expecting did not happen.
 * Every sentence is the server's; nothing here is composed.
 */
export function WatchRefusedNote({ refusal }: { refusal: WatchRefusal }) {
  return (
    <aside
      role="alert"
      data-watch-refused
      className="fade-up flex items-start gap-2 rounded-xl border border-negative/40 bg-negative/[0.06] px-3 py-2.5"
    >
      <EyeOff aria-hidden className="mt-0.5 size-3 shrink-0 text-negative" />
      <div className="min-w-0 space-y-1">
        <p className="text-meta font-medium leading-snug text-foreground">
          {refusal.subject
            ? `This read as a watch on ${refusal.subject}, and no watch was created.`
            : "This read as a watch declaration, and no watch was created."}
        </p>
        {/* The platform's own sentence: why it was refused, and what this
            contract does take. Paraphrasing it would drop the only part
            that tells the analyst how to ask again. */}
        <p className="text-meta leading-snug text-muted-foreground">{refusal.reason}</p>
        {/* What WOULD work, in the analyst's own idiom. A refusal with no
            way forward is a wall, and this is the part that turns it into
            a sentence somebody can act on. */}
        {refusal.legalAlternatives.length > 0 && (
          <p className="text-micro leading-snug text-muted-foreground">
            Phrasings this measure takes: {refusal.legalAlternatives.join(" · ")}
          </p>
        )}
        <p className="num flex flex-wrap items-baseline gap-x-2 text-micro text-muted-foreground">
          <span>The answer below stands on its own. Nothing is being watched.</span>
          <Link
            href="/rounds"
            className="focus-ring rounded underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
          >
            See what IS being watched
          </Link>
        </p>
        {/* The sensitivity words the grammar could not read, shown back
            verbatim. It is the difference between "we refused you" and
            "this is the clause we could not read" — and the second is the
            one that teaches which words work. */}
        {refusal.thresholdPhrase && (
          <p className="text-micro leading-snug text-muted-foreground">
            What could not be read: “{refusal.thresholdPhrase}”.
          </p>
        )}
      </div>
    </aside>
  );
}
