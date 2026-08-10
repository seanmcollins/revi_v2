"use client";

import { Check, GitCompareArrows, HelpCircle } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatCents, formatSignedPct } from "@/lib/format";
import type { AnomalyReconciliation } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The drilled card's figure beside this platform's re-derivation of it.
 *
 * A card published $178,217; opening it answered $195,873.92; the turn's
 * own §7.8 verdict said "not applicable — this is a first turn", which is
 * true about the investigation lineage and silent about the two numbers
 * the reader had just compared. 9.9% of disagreement on consecutive
 * screens, reconciled nowhere.
 *
 * Both figures are honest and they are different claims:
 *
 *   the CARD is the external detection system's assertion, on its own
 *     window, population and valuation basis, computed when it fired;
 *   the ANSWER is this platform's governed metric contract, re-derived at
 *     the pinned watermark over the population the card names, carrying a
 *     real evidence grade.
 *
 * So the strip states both and names the verdict. It deliberately reuses
 * the shape of the §7.8 reconciliation banner — an analyst who has learned
 * to read one reconciliation should not have to learn a second — while
 * keeping the tones apart: a divergence here is expected and explained,
 * not the failure a `RECONCILIATION_FAILED` banner reports.
 */
const TONE = {
  agreed: {
    border: "border-verified/40 bg-verified/10",
    accent: "text-verified",
    title: "The card's figure and this answer agree",
  },
  diverged: {
    border: "border-warning/40 bg-warning/10",
    accent: "text-warning",
    title: "This answer differs from the card that opened it",
  },
  unavailable: {
    border: "border-border bg-surface-sunken/60",
    accent: "text-muted-foreground",
    title: "The card's figure could not be re-derived here",
  },
} as const;

export function AnomalyReconciliationStrip({
  reconciliation,
}: {
  reconciliation?: AnomalyReconciliation;
}) {
  if (!reconciliation) return null;
  const { status, cardImpactCents, answerImpactCents, deltaFraction } = reconciliation;
  const tone = TONE[status];
  const Icon =
    status === "agreed" ? Check : status === "diverged" ? GitCompareArrows : HelpCircle;

  return (
    <div
      data-anomaly-status={status}
      className={cn("rounded-lg border px-3.5 py-2.5", tone.border)}
    >
      <div className="flex items-start gap-2.5">
        <Icon className={cn("mt-0.5 size-4 shrink-0", tone.accent)} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="text-body font-semibold">{tone.title}</span>
            {reconciliation.anomalyId && (
              <span className="font-mono text-micro text-muted-foreground">
                {reconciliation.anomalyId}
              </span>
            )}
          </p>

          {/* Both numbers, always — including when they agree. A reader
              who has just been told two sources match should be able to
              see what matched. */}
          <dl className="mt-1.5 flex flex-wrap gap-x-6 gap-y-1">
            <Figure
              label="Card said"
              hint="The external detection system's assertion, on its own window, population and valuation basis, computed when it fired."
              value={formatCents(cardImpactCents)}
              metricId={reconciliation.cardMetricId}
            />
            <Figure
              label="This answer"
              hint="Re-derived from this platform's governed metric contract at the pinned data load, over the population the card names."
              value={
                answerImpactCents !== undefined ? formatCents(answerImpactCents) : "not re-derived"
              }
              muted={answerImpactCents === undefined}
              metricId={reconciliation.answerMetricId}
            />
            {deltaFraction !== undefined && status !== "agreed" && (
              <Figure
                label="Difference"
                hint="The answer's figure against the card's, as published by the server."
                value={formatSignedPct(deltaFraction)}
                accent={tone.accent}
              />
            )}
          </dl>

          {/* The platform's own account of WHY, verbatim. Summarizing it
              here would drop the window, the basis or the population —
              which is the whole explanation. */}
          {(reconciliation.detail || reconciliation.summary) && (
            <p className="mt-1.5 text-meta leading-snug text-muted-foreground">
              {reconciliation.detail || reconciliation.summary}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * One figure and what it is a claim ABOUT.
 *
 * The hint used to be a native `title` on a bare `<div>`: no keyboard
 * path, no touch equivalent, and an unpredictable hover delay — on the
 * three numbers whose whole purpose is to be compared, where "the
 * detection system's assertion on its own basis" versus "this platform's
 * governed re-derivation" IS the difference between them. The LABEL is
 * the affordance now (a real button, dotted-underlined, in the existing
 * Radix tooltip), so the explanation is reachable by keyboard and touch
 * and the number itself stays plain selectable text.
 */
function Figure({
  label,
  hint,
  value,
  metricId,
  muted,
  accent,
}: {
  label: string;
  hint: string;
  value: string;
  metricId?: string;
  muted?: boolean;
  accent?: string;
}) {
  return (
    <div>
      <dt className="text-micro font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="focus-ring rounded uppercase tracking-[0.12em] underline decoration-dotted underline-offset-2 hover:text-foreground"
            >
              {label}
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-72 text-meta leading-snug">
            {hint}
          </TooltipContent>
        </Tooltip>
      </dt>
      <dd
        className={cn(
          "numeral text-[0.95rem] font-medium leading-tight",
          muted && "text-body font-normal text-muted-foreground",
          accent,
        )}
      >
        {value}
      </dd>
      {metricId && (
        <dd className="font-mono text-micro leading-tight text-muted-foreground">
          {metricId}
        </dd>
      )}
    </div>
  );
}
