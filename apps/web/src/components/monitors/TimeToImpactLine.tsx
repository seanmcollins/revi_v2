"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { mediumDate } from "@/lib/format";
import type { TimeToImpact } from "@/lib/monitors";
import { cn } from "@/lib/utils";

/**
 * WHEN this lead's dollars hit cash — the lane, in one clause.
 *
 * Four outcomes and four different sentences, because collapsing them is
 * the whole failure mode:
 *
 *   a DEADLINE is a real, dated limit from the detector's own evidence.
 *     It is printed as a date, plainly, because it is one.
 *   a PROJECTION is an aging estimate with a stated method. It is printed
 *     as "~N days" and marked PROVISIONAL, because an estimate rendered
 *     like a filing limit is indistinguishable from one on the screen
 *     beside it — and on a portfolio card those two lines are two rows
 *     apart.
 *   ALREADY HIT means the cash effect is in the past. What may still be
 *     open is a recovery window, and that rides here with its own label
 *     ("appeal window closes") rather than being dressed up as the cash
 *     date it is not.
 *   UNKNOWN carries the reason. It is rendered, quietly, rather than
 *     hidden: "no honest basis exists for this category" is information,
 *     and an absent line would read as an oversight.
 *
 * Nothing here is a rank. `anomaly_priority@3` orders the worklist and
 * this is published context beside it — no surface sorts on it, which the
 * Monitors tests assert rather than trust.
 */
export function TimeToImpactLine({
  timeToImpact,
  className,
}: {
  timeToImpact: TimeToImpact;
  className?: string;
}) {
  const tti = timeToImpact;
  const body = phrase(tti);
  if (body === null) return null;

  return (
    // A SPAN, not a paragraph. This clause sits inside the brief entry's
    // own meta line — which is a paragraph — and a block element nested in
    // one is invalid HTML that hydrates as a different tree than it
    // rendered. `inline-flex` keeps the wrapping behaviour either way.
    <span
      data-time-to-impact={tti.kind}
      data-time-to-impact-lane={tti.lane}
      className={cn(
        "num inline-flex flex-wrap items-baseline gap-x-1.5 text-micro leading-snug",
        className,
      )}
    >
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className={cn(
              "focus-ring rounded text-left underline decoration-dotted underline-offset-2 transition-colors duration-150 hover:text-foreground",
              tti.kind === "unknown" ? "text-muted-foreground" : "text-foreground/80",
            )}
          >
            {body}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-80 text-meta leading-snug">
          {/* How this was derived, in the server's own sentence, naming
              the evidence facts it read. Required on every outcome
              including `unknown`. */}
          {tti.method || tti.reason}
        </TooltipContent>
      </Tooltip>
      {/* PROVISIONAL is a word, not a shade. A projection that carried its
          uncertainty only in a lighter grey would be a forecast this
          platform has not earned, printed in the same column as a filing
          limit that is a legal fact. */}
      {tti.provisional && <span className="text-warning">provisional</span>}
    </span>
  );
}

/** The clause itself. `null` when the payload says nothing worth a line. */
function phrase(tti: TimeToImpact): string | null {
  if (tti.kind === "deadline") {
    const date = tti.deadlineDate ? safeDate(tti.deadlineDate) : undefined;
    if (date === undefined) return tti.days !== undefined ? `hits cash in ${tti.days} days` : null;
    return tti.days !== undefined
      ? `hits cash ${date} — ${tti.days} days left`
      : `hits cash ${date}`;
  }
  if (tti.kind === "projected") {
    return tti.days !== undefined ? `hits cash in ~${tti.days} days` : null;
  }
  if (tti.kind === "already_hit") {
    // The recovery window, when the detector published one, because that
    // is the only thing still actionable on a card whose cash already
    // moved. A negative figure means the window has closed, and the
    // server's own number says so rather than being clamped to zero.
    if (tti.recoveryDays !== undefined) {
      const label = tti.recoveryLabel || "recovery window closes";
      const date = tti.recoveryDeadlineDate ? ` (${safeDate(tti.recoveryDeadlineDate)})` : "";
      return tti.recoveryDays < 0
        ? `already hit cash — ${label} ${Math.abs(tti.recoveryDays)} days ago${date}`
        : `already hit cash — ${label} in ${tti.recoveryDays} days${date}`;
    }
    return "already hit cash";
  }
  // `unknown` with its reason. The reason is the tooltip; the line says
  // that there is no date rather than leaving a blank where every other
  // card has one.
  return tti.reason !== undefined ? "no cash date for this kind of card" : null;
}

/** `mediumDate` throws on anything that is not an ISO date. */
function safeDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
}
