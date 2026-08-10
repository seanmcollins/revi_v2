"use client";

import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { mediumDate } from "@/lib/format";
import { useSessionStore } from "@/lib/store";

/**
 * A newer data load arrived while this session was open. The analyst
 * chooses — stay pinned (results stay reproducible at the load this
 * session has been reading) or move (new load; numbers may change).
 *
 * A NOTE WITH A CHOICE ATTACHED, not a warning. Nothing here says anything
 * is wrong with the figures on screen: they are correct at the load they
 * were measured on, and a fresher one exists. The two buttons are the call
 * to action, so the panel does not need amber to be answered — and a fresh
 * load announced in alarm ink is how a reader learns to distrust numbers
 * that were never in doubt.
 */
export function WatermarkBanner() {
  const banner = useSessionStore((s) => s.watermarkBanner);
  const resolve = useSessionStore((s) => s.resolveWatermarkBanner);
  const current = useSessionStore((s) => s.watermark);

  if (!banner.visible || !banner.newWatermark) return null;

  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border bg-surface-sunken/60 px-3.5 py-2.5"
    >
      <RefreshCw className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <p className="text-body font-medium">Newer data has arrived since this session began</p>
        {/* Both loads, named the way a reader says a date rather than as
            the stamps the wire carries. */}
        <p className="num text-meta text-muted-foreground">
          This session reads the load of {loadLabel(current.loadedAt)}, through{" "}
          {mediumDate(current.newestDataDate)}. The newer load,{" "}
          {loadLabel(banner.newWatermark.loadedAt)}, reads through{" "}
          {mediumDate(banner.newWatermark.newestDataDate)}.
        </p>
      </div>
      {/* Same decision, same consequences — the analyst chooses between
          reproducibility and freshness. Only the button words changed. */}
      <div className="flex shrink-0 gap-1.5">
        <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => resolve("stay_pinned")}>
          Keep this data
        </Button>
        <Button size="sm" className="h-7 text-xs" onClick={() => resolve("re_anchor")}>
          Move to the newer data
        </Button>
      </div>
    </div>
  );
}

/**
 * "2026-08-03 04:10" → "Aug 3, 2026 at 04:10".
 *
 * The stamp is what tells two loads on one day apart, so it is kept; what
 * changes is that it is spelled the way a reader says a date. Anything
 * this cannot parse is printed exactly as the wire wrote it.
 */
function loadLabel(stamp: string): string {
  const [day, time] = stamp.split(" ");
  try {
    return time ? `${mediumDate(day)} at ${time}` : mediumDate(day);
  } catch {
    return stamp;
  }
}
