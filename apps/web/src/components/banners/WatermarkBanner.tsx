"use client";

import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { mediumDate } from "@/lib/format";
import { useSessionStore } from "@/lib/store";

/**
 * WATERMARK_STALE UX: data refreshed since this session began. The analyst
 * chooses — stay pinned (results stay reproducible at the old watermark)
 * or re-anchor (new epoch; numbers may change).
 */
export function WatermarkBanner() {
  const banner = useSessionStore((s) => s.watermarkBanner);
  const resolve = useSessionStore((s) => s.resolveWatermarkBanner);
  const current = useSessionStore((s) => s.watermark);

  if (!banner.visible || !banner.newWatermark) return null;

  return (
    <div
      role="status"
      className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-warning/40 bg-warning/10 px-3.5 py-2.5"
    >
      <RefreshCw className="size-4 shrink-0 text-warning" />
      <div className="min-w-0 flex-1">
        <p className="text-[0.78rem] font-medium">Data refreshed since this session began</p>
        <p className="num text-[0.68rem] text-muted-foreground">
          Session pinned to {current.loadedAt} (data through {mediumDate(current.newestDataDate)}) ·
          new load {banner.newWatermark.loadedAt} (through {mediumDate(banner.newWatermark.newestDataDate)})
        </p>
      </div>
      <div className="flex shrink-0 gap-1.5">
        <Button variant="outline" size="sm" className="h-7 text-xs" onClick={() => resolve("stay_pinned")}>
          Stay pinned
        </Button>
        <Button size="sm" className="h-7 text-xs" onClick={() => resolve("re_anchor")}>
          Re-anchor
        </Button>
      </div>
    </div>
  );
}
