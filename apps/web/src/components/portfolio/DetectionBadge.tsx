"use client";

import { Radar } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatWholeDollars } from "@/lib/format";
import type { PriorityDecomposition } from "@/lib/mock/portfolio";
import { cn } from "@/lib/utils";

/**
 * Provenance chip for a portfolio card — NOT an evidence grade.
 *
 * A grade certifies how this platform computed a number from certified
 * semantics; an anomaly card is a record read out of an external detection
 * system as-of a watermark, so it cannot honestly claim one (AnomalyCard
 * publishes no `grade`, by design). It declares instead what it actually
 * knows: that it was externally detected, the governed priority formula that
 * ranked it, and the watermark it was read at.
 *
 * Deliberately styled apart from `GradeBadge` — squared corners, a dashed
 * border, mono uppercase, no grade colour ramp — so "externally detected" can
 * never be misread at a glance as "certified evidence". Drilling the card
 * starts an ordinary turn, and that answer carries a real grade.
 */
export function DetectionBadge({
  priorityFormulaVersion,
  sourceWatermarkId,
  priority,
  priorityScore,
  className,
}: {
  priorityFormulaVersion?: string;
  sourceWatermarkId?: string;
  /** Every term of the formula named above — see `PriorityDecomposition`. */
  priority?: PriorityDecomposition;
  priorityScore?: number;
  className?: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex h-[1.05rem] cursor-default items-center gap-1 rounded-[3px] border border-dashed",
            "border-muted-foreground/40 px-1.5 font-mono text-micro font-medium uppercase",
            "tracking-[0.06em] text-muted-foreground",
            className,
          )}
        >
          <Radar className="size-2.5" aria-hidden />
          Detection
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-72">
        <p className="mb-1 font-medium">Externally detected — not an evidence grade</p>
        <p className="text-meta leading-snug opacity-90">
          This card is a record read from an external detection system, not a number Revi
          measured from your data — so nothing here says how it was measured. Drill in and the
          answer that comes back does.
        </p>
        {(priorityFormulaVersion || sourceWatermarkId) && (
          <dl className="num mt-1.5 space-y-0.5 text-meta leading-snug opacity-70">
            {priorityFormulaVersion && (
              <div className="flex gap-1.5">
                <dt>Ranked by</dt>
                <dd className="font-mono">{priorityFormulaVersion}</dd>
              </div>
            )}
            {sourceWatermarkId && (
              <div className="flex gap-1.5">
                <dt>Data as of</dt>
                <dd className="font-mono">{sourceWatermarkId}</dd>
              </div>
            )}
          </dl>
        )}
        {/* The formula's own terms. A ranked list that will not show its
            working is asking to be trusted on the ordering, and the
            server publishes every term precisely so it does not have to
            be. `floorApplied` is the one that changes the reading: a
            compliance card whose score was raised to the floor did not
            earn its position by size, and says so. */}
        {priority && (
          <dl className="num mt-1.5 space-y-0.5 border-t border-current/15 pt-1.5 text-meta leading-snug opacity-70">
            <Term label="Impact" value={priority.impactTerm} />
            <Term label="Recency" value={priority.recencyTerm} />
            <Term label="Recoverable" value={priority.actionabilityTerm} />
            <div className="flex justify-between gap-3 font-medium">
              <dt>Score</dt>
              <dd className="font-mono">
                {(priorityScore ?? priority.scoreBeforeFloor).toFixed(3)}
              </dd>
            </div>
            {/* The arithmetic behind the FIRST term, which was the one
                term a reader could not check. `impact_norm` is a bare
                ratio — 0.361299 beside an impact of $178,216.82 — and the
                two numbers that produce it (the figure that ranked this
                card, over the largest ranked figure in the population)
                were published and shown nowhere. The normalizer is the
                same for every card, which is what makes these scores
                comparable rather than a list of unrelated numbers. */}
            {priority.rankedImpactCents !== undefined &&
              priority.impactNormalizerCents !== undefined && (
                <p className="pt-0.5 font-sans opacity-90">
                  Impact term is {formatWholeDollars(priority.rankedImpactCents)} over{" "}
                  {formatWholeDollars(priority.impactNormalizerCents)}, the largest ranked
                  figure on this list ({priority.impactNorm.toFixed(3)})
                  {priority.rankedOn === "platform"
                    ? " — on this platform's re-derived figure, not the detector's."
                    : priority.rankedOn === "not_comparable"
                      ? " — on the detector's figure, because this platform's re-derivation is not a comparable quantity."
                      : " — on the detection system's own figure."}
                </p>
              )}
            {priority.floorApplied && (
              <p className="pt-0.5 font-sans opacity-90">
                Raised to the compliance floor ({priority.floorValue.toFixed(3)},{" "}
                {priority.floorBasis.replace(/_/g, " ")}) from {priority.scoreBeforeFloor.toFixed(3)}
                — worked because the rule requires it, not because of its size.
              </p>
            )}
          </dl>
        )}
      </TooltipContent>
    </Tooltip>
  );
}

/** One weighted term of the priority score, as the server computed it. */
function Term({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex justify-between gap-3">
      <dt>{label}</dt>
      <dd className="font-mono">{value.toFixed(3)}</dd>
    </div>
  );
}
