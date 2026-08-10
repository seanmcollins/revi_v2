"use client";

import { ArrowDownRight, ArrowUpRight } from "lucide-react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { tidyProse } from "@/lib/prose";
import type { MonitorsDelta } from "@/lib/monitors";
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
 *
 * THE MARK IS NOT A SIGN. The refusal above used to be drawn as lucide
 * `Minus` — `path d="M5 12h14"` — which at 10px beside a numeral is a
 * minus sign, not the absence of an arrow. Every monitor on the demo tenant
 * is `sameWindow: true`, so the exec's marquee tile read "— 7.3 points
 * from 22.2%" on a denial rate that got 7.3 points WORSE, twelve pixels
 * under brief prose saying "up 7.3 points". Withholding an arrow and
 * painting a minus are not the same act: one declines to claim a
 * direction, the other claims the wrong one. The neutral mark is now an
 * unsigned middot, and the direction the payload DOES carry is stated in
 * the word the brief beside it already uses.
 */
export function DeltaLine({
  delta,
  className,
}: {
  delta: MonitorsDelta;
  className?: string;
}) {
  if (!delta.comparable) {
    return (
      <p
        data-delta-direction={delta.direction}
        data-delta-mark="none"
        className={cn("text-micro leading-snug text-muted-foreground", className)}
      >
        {tidyProse(
          delta.notComparableReason ??
            "No movement is published for this monitor — the two loads are not two measurements of one thing.",
        )}
      </p>
    );
  }

  const flat = delta.direction === "flat" || delta.delta === 0;
  const mark = deltaMark(delta);
  const word = flat ? "" : directionWord(delta);

  return (
    <p
      data-delta-direction={delta.direction}
      data-delta-mark={mark}
      data-same-window={delta.sameWindow ? "true" : "false"}
      className={cn("num flex flex-wrap items-baseline gap-x-1.5 text-micro leading-snug", className)}
    >
      <span className="inline-flex items-baseline gap-1 text-foreground/80">
        {mark === "up" ? (
          <ArrowUpRight aria-hidden className="size-2.5 translate-y-0.5" />
        ) : mark === "down" ? (
          <ArrowDownRight aria-hidden className="size-2.5 translate-y-0.5" />
        ) : (
          // Unsigned, and deliberately not an icon: every glyph in the
          // lucide set that reads as "flat" also reads as "minus", which
          // is the defect. A middot is a separator — it says a mark
          // belongs here and claims nothing about which way anything went.
          <span aria-hidden className="text-muted-foreground">
            ·
          </span>
        )}
        {flat ? "no change" : word === "" ? delta.deltaText : `${word} ${delta.deltaText}`}
      </span>
      <span className="text-muted-foreground">
        {delta.reference === "baseline"
          ? `since you started monitoring (${delta.priorValueText || delta.priorWatermarkId})`
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
 * WHAT MAY BE PAINTED for this movement: an arrow, or nothing signed.
 *
 * Exported so the guard in `Monitors.test.tsx` can assert the property that
 * matters — a movement that went up shares no mark with one that went
 * down — rather than asserting one particular icon.
 */
export type DeltaMark = "up" | "down" | "neutral";

export function deltaMark(delta: MonitorsDelta): DeltaMark {
  // No movement at all, and a re-measurement of one period, are both
  // claims about the data rather than about the world. Neither earns an
  // arrow, and neither may be given a sign.
  if (delta.direction === "flat" || delta.delta === 0) return "neutral";
  if (delta.sameWindow) return "neutral";
  if (delta.direction === "up") return "up";
  if (delta.direction === "down") return "down";
  // `unknown` is the server declining to name a direction. So does this.
  return "neutral";
}

/**
 * The direction word the payload carries, for the cases where it carries
 * one. Empty for `flat` (the text is "no change", which is the direction)
 * and for `unknown` (there is nothing to say).
 */
export function directionWord(delta: MonitorsDelta): string {
  if (delta.direction === "up") return "up";
  if (delta.direction === "down") return "down";
  return "";
}

/**
 * WHOSE threshold briefed this, said once, quietly.
 *
 * A monitor may set a threshold looser than the pack's governed gate. That
 * is a real need and it is paid for rather than refused: every entry says
 * which threshold briefed it, so a movement the governed gate calls normal
 * variation never looks governed.
 */
export function ThresholdNote({ delta }: { delta: MonitorsDelta }) {
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
          {delta.thresholdSource === "monitor"
            ? delta.belowGovernedGate
              ? "your threshold, below the governed one"
              : "your threshold"
            : "the governed threshold"}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-80 text-meta leading-snug">
        {/* The server's own sentence: which rule decided and what it
            compared against, so the gate is checkable rather than
            trusted. Its stacked stop is repaired and nothing else — the
            note quotes the analyst's own reason, which arrives with its
            own full stop inside a sentence that already has one. */}
        {tidyProse(delta.materialityNote)}
      </TooltipContent>
    </Tooltip>
  );
}
