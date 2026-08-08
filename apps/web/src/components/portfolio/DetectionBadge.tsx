"use client";

import { Radar } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
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
  className,
}: {
  priorityFormulaVersion?: string;
  sourceWatermarkId?: string;
  className?: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex h-[1.05rem] cursor-default items-center gap-1 rounded-[3px] border border-dashed",
            "border-muted-foreground/40 px-1.5 font-mono text-[0.58rem] font-medium uppercase",
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
        <p className="text-[0.7rem] leading-snug opacity-90">
          This card is a record read from an external detection system, not a number this platform
          computed from certified semantics — so it carries no DIRECT/DERIVED/PROXY grade. Drill in
          and the resulting answer does.
        </p>
        {(priorityFormulaVersion || sourceWatermarkId) && (
          <dl className="num mt-1.5 space-y-0.5 text-[0.65rem] leading-snug opacity-70">
            {priorityFormulaVersion && (
              <div className="flex gap-1.5">
                <dt>Ranked by</dt>
                <dd className="font-mono">{priorityFormulaVersion}</dd>
              </div>
            )}
            {sourceWatermarkId && (
              <div className="flex gap-1.5">
                <dt>Read at</dt>
                <dd className="font-mono">{sourceWatermarkId}</dd>
              </div>
            )}
          </dl>
        )}
      </TooltipContent>
    </Tooltip>
  );
}
