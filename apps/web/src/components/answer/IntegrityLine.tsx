"use client";

import { useState, type ReactNode } from "react";

import {
  ThingsToKnowSheet,
  thingsToKnowLabel,
  thingsToKnowSeverity,
} from "@/components/answer/ThingsToKnow";
import type { IntegrityTone, VerificationClause } from "@/components/answer/useAnswerModel";
import { useSessionStore } from "@/lib/store";
import type { WarningEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * THE INTEGRITY LINE — the signature element of the calm answer.
 *
 * One quiet sentence under every answer:
 *
 *     ● Verified against your data · 3 things to know, 2 change how a
 *       number here should be read · 12 checks
 *
 * It is the product's whole claim, said once and small: honesty without
 * noise. Everything it states is a count of something on the payload —
 * the caveats it opens are exactly the caveats it counts, and the checks
 * are the probes the evidence bundle recorded — and each count is the tap
 * target onto the thing it counts. Nothing here is a score, a badge or a
 * confidence percentage: a number this line cannot back would undo the
 * only thing it exists to do.
 *
 * Four things the review round required of it, each of which had the line
 * claiming something the payload did not say:
 *
 *   the DOT is driven by the clause. It was `bg-verified` with the
 *     verified halo unconditionally, so "Answered without reading your
 *     data" shipped under a green verified mark.
 *   the GRADE rides here. `answerGrade` rendered in exactly one component
 *     that this layout never mounts, so a Proxy-graded answer presented
 *     as a Direct one — the honesty invariant not relocated but deleted.
 *   the SEVERITY is stated with the count. "12 things to know" tells a
 *     reader nothing about whether opening them matters; "10 change how a
 *     number here should be read" is the reason to open them.
 *   the CONTROLS look like controls. `decoration-border` computes to
 *     1.16:1 on the dark background — an underline you cannot see under
 *     text the same colour as the sentence around it. A disclosure
 *     control nobody can identify is a disclosure that did not happen.
 *
 * Its ornament budget is one dot with a soft halo (`.integrity-dot`, and
 * only on a verified turn), and a hairline above that fades out before
 * the column edge — a signature under the writing rather than a divider
 * between two blocks.
 */
export function IntegrityLine({
  verification,
  thingsToKnow,
  checks,
  turnId,
  hasEvidence,
  trailing,
  className,
}: {
  /** The honest clause for this turn, with its tone and its grade. */
  verification: VerificationClause;
  /** The caveats behind the line. Its count is theirs, exactly. */
  thingsToKnow: readonly WarningEvent[];
  /** Data checks this turn ran (`evidence.probes.length`). */
  checks: number;
  turnId: string;
  /**
   * Is there anything in the rail for this turn — an evidence bundle, the
   * facts, the supporting figures?
   *
   * Not `evidence !== undefined`. A zero-probe turn used to have NO path
   * into Evidence from the calm answer at all: the checks link gated on
   * `checks > 0` and the facts button on `findings.length > 0`, so a turn
   * with neither offered the reader nothing, and the rail — which is
   * always mounted — silently showed some other turn's bundle instead.
   */
  hasEvidence: boolean;
  /** The answer's take-away action, seated on the same baseline. */
  trailing?: ReactNode;
  className?: string;
}) {
  const openDrawer = useSessionStore((s) => s.openDrawer);
  const [sheetOpen, setSheetOpen] = useState(false);

  return (
    <>
      <div className={cn("pt-2.5", className)}>
        <div aria-hidden className="integrity-rule mb-2 h-px w-full" />
        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
          {/* text-meta, not text-micro, and foreground/70, not muted:
              this is the product's whole claim and it was the quietest
              thing on the page. */}
          <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-meta text-foreground/70">
            <IntegrityDot tone={verification.tone} />
            <span className="text-foreground/80">{verification.text}</span>

            {/* The answer-level grade, in words, on the surface that
                otherwise never says it. */}
            {verification.gradeNote && (
              <>
                <Separator />
                <span
                  data-answer-grade={verification.gradeNote.grade}
                  className="text-warning"
                >
                  {verification.gradeNote.text}
                </span>
              </>
            )}

            {thingsToKnow.length > 0 && (
              <>
                <Separator />
                <button
                  type="button"
                  onClick={() => setSheetOpen(true)}
                  className="focus-ring rounded underline decoration-foreground/40 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
                >
                  {thingsToKnowLabel(thingsToKnow.length)}
                </button>
                {/* The severity, beside the count and not behind it. */}
                <span className="text-micro text-muted-foreground">
                  {thingsToKnowSeverity(thingsToKnow)}
                </span>
              </>
            )}

            {checks > 0 ? (
              <>
                <Separator />
                {hasEvidence ? (
                  <button
                    type="button"
                    onClick={() => openDrawer(turnId)}
                    className="focus-ring num rounded underline decoration-foreground/40 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
                  >
                    {checks} check{checks === 1 ? "" : "s"}
                  </button>
                ) : (
                  <span className="num">
                    {checks} check{checks === 1 ? "" : "s"}
                  </span>
                )}
              </>
            ) : (
              hasEvidence && (
                <>
                  <Separator />
                  {/* No probe ran, and there is still a case file: the
                      question, the cache note, the reconciliation, and
                      whatever facts this turn published. The rail says
                      what it does not have rather than being unreachable. */}
                  <button
                    type="button"
                    onClick={() => openDrawer(turnId)}
                    className="focus-ring rounded underline decoration-foreground/40 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
                  >
                    Evidence
                  </button>
                </>
              )
            )}
          </p>
          {trailing}
        </div>
      </div>

      <ThingsToKnowSheet
        warnings={thingsToKnow}
        open={sheetOpen}
        onOpenChange={setSheetOpen}
      />
    </>
  );
}

/** What the mark means, in colour and in its accessible name. */
const TONE: Readonly<Record<IntegrityTone, { className: string; label: string }>> = {
  verified: {
    className: "integrity-dot bg-verified",
    label: "Verified",
  },
  measured: {
    className: "bg-foreground/50",
    label: "Measured, not governed",
  },
  qualified: {
    className: "bg-warning",
    label: "Qualified evidence",
  },
  unread: {
    className: "bg-muted-foreground/70",
    label: "No data was read",
  },
};

function IntegrityDot({ tone }: { tone: IntegrityTone }) {
  const meta = TONE[tone];
  return (
    <span
      data-integrity-tone={tone}
      className={cn("mr-0.5 inline-block size-1.5 shrink-0 rounded-full", meta.className)}
    >
      <span className="sr-only">{meta.label}: </span>
    </span>
  );
}

function Separator() {
  return (
    <span aria-hidden className="text-muted-foreground/60">
      ·
    </span>
  );
}
