"use client";

import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { RoundsDelta } from "@/lib/rounds";
import { cn } from "@/lib/utils";

/**
 * A movement, in the metric's own unit, with the two things that decide
 * how to read it.
 *
 * The unit is the server's: `deltaText` arrives already rendered ("3.6
 * points", "$4,201.00"), and this component does not re-derive it. A rate's
 * movement is POINTS, and a client that turned 0.035823 into "+3.6%" would
 * print a number a reader cannot tell from a relative change — which is the
 * single most common way a denial-rate chart lies.
 *
 * Two facts travel with it and neither is optional:
 *
 *   NOT COMPARABLE. A first evaluation, a changed metric, a suppressed
 *     prior value — the server withholds the percentage and says why, and
 *     the surface says why rather than drawing a flat line.
 *   SAME WINDOW. Two loads that resolved to the same dates are two
 *     measurements of ONE period, so the change between them is
 *     late-arriving data settling — adjudication run-out, back-dated
 *     charges — not a movement in the business. It is drawn without a
 *     direction arrow for exactly that reason: an arrow is a claim about
 *     the world, and this is a claim about the data catching up.
 */
export function DeltaLine({
  delta,
  className,
}: {
  delta: RoundsDelta;
  className?: string;
}) {
  if (!delta.comparable) {
    return (
      <p className={cn("text-micro leading-snug text-muted-foreground", className)}>
        {delta.notComparableReason ??
          "No movement is published for this watch — the two loads are not two measurements of one thing."}
      </p>
    );
  }

  const flat = delta.direction === "flat" || delta.delta === 0;
  const Arrow =
    delta.sameWindow || flat
      ? Minus
      : delta.direction === "up"
        ? ArrowUpRight
        : delta.direction === "down"
          ? ArrowDownRight
          : Minus;

  return (
    <p
      data-delta-direction={delta.direction}
      data-same-window={delta.sameWindow ? "true" : "false"}
      className={cn("num flex flex-wrap items-baseline gap-x-1.5 text-micro leading-snug", className)}
    >
      <span className="inline-flex items-baseline gap-1 text-foreground/80">
        <Arrow aria-hidden className="size-2.5 translate-y-0.5" />
        {flat ? "no change" : delta.deltaText}
      </span>
      <span className="text-muted-foreground">
        {delta.reference === "baseline"
          ? `since you started watching (${delta.priorValueText || delta.priorWatermarkId})`
          : `from ${delta.priorValueText || "the prior load"}`}
      </span>
      {delta.sameWindow && !flat && (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="focus-ring rounded text-left text-micro text-warning underline decoration-dotted underline-offset-2"
            >
              same period, re-measured
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-72 text-meta leading-snug">
            Against the previous load, both readings measured the same dates — so this is the data catching up — claims
            finishing adjudication, charges arriving late — rather than a change in what is
            happening.
          </TooltipContent>
        </Tooltip>
      )}
    </p>
  );
}

/**
 * WHOSE threshold briefed this, said once, quietly.
 *
 * A watch may set a threshold looser than the pack's governed gate. That
 * is a real need and it is paid for rather than refused: every entry says
 * which threshold briefed it, so a movement the governed gate calls normal
 * variation never looks governed.
 */
export function ThresholdNote({ delta }: { delta: RoundsDelta }) {
  if (delta.materialityNote === "") return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          data-threshold-source={delta.thresholdSource}
          className={cn(
            "focus-ring rounded text-left text-micro leading-snug underline decoration-dotted underline-offset-2",
            delta.belowGovernedGate ? "text-warning" : "text-muted-foreground",
          )}
        >
          {delta.thresholdSource === "watch"
            ? delta.belowGovernedGate
              ? "your threshold, below the governed one"
              : "your threshold"
            : "the governed threshold"}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-80 text-meta leading-snug">
        {/* The server's own sentence: which rule decided and what it
            compared against, so the gate is checkable rather than
            trusted. */}
        {delta.materialityNote}
      </TooltipContent>
    </Tooltip>
  );
}
