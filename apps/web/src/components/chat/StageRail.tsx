"use client";

import { Check, ChevronRight, CircleDashed, Zap } from "lucide-react";
import { useState } from "react";

import type { StageStatus } from "@/lib/store";
import { STAGE_LABELS } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The typed pipeline: classified → interpreted → planned → validated →
 * executing (i/n probes, cache ticks) → calculating → reconciled →
 * narrating. Designed for tens-of-seconds latency: while streaming it is
 * fully expanded; after completion it collapses to a one-line summary.
 */
export function StageRail({
  stages,
  streaming,
  cacheHits,
}: {
  stages: StageStatus[];
  streaming: boolean;
  cacheHits: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const showList = streaming || expanded;

  const doneCount = stages.filter((s) => s.state === "done").length;
  const skippedCount = stages.filter((s) => s.state === "skipped").length;
  const executing = stages.find((s) => s.stage === "executing");
  const probeSummary =
    executing?.probesTotal !== undefined
      ? `${executing.probesTotal} probe${executing.probesTotal === 1 ? "" : "s"}`
      : null;

  if (!showList) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="group flex items-center gap-1.5 text-[0.68rem] text-muted-foreground transition-colors duration-150 hover:text-foreground"
      >
        <Check className="size-3 text-verified" />
        {doneCount} stage{doneCount === 1 ? "" : "s"}
        {skippedCount > 0 && ` · ${skippedCount} skipped (zero-probe path)`}
        {probeSummary && ` · ${probeSummary}`}
        {cacheHits > 0 && (
          <span className="inline-flex items-center gap-0.5 text-verified">
            <Zap className="size-3" />
            {cacheHits} cache hit{cacheHits === 1 ? "" : "s"}
          </span>
        )}
        <ChevronRight className="size-3 opacity-0 transition-opacity duration-150 group-hover:opacity-100" />
      </button>
    );
  }

  return (
    <ol
      className="flex flex-wrap items-center gap-x-1 gap-y-1.5"
      onClick={() => !streaming && setExpanded(false)}
    >
      {stages.map((stage, i) => (
        <li key={stage.stage} className="flex items-center gap-1">
          {i > 0 && <span className="mx-0.5 h-px w-2.5 bg-border" />}
          <StageNode stage={stage} />
        </li>
      ))}
    </ol>
  );
}

function StageNode({ stage }: { stage: StageStatus }) {
  const isExecuting = stage.stage === "executing";
  const label =
    isExecuting && stage.probesTotal !== undefined && stage.probesTotal > 0
      ? `probes ${stage.probesDone ?? 0}/${stage.probesTotal}`
      : STAGE_LABELS[stage.stage].toLowerCase();

  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-full px-1.5 text-[0.65rem] transition-all duration-200",
        stage.state === "done" && "text-secondary-foreground",
        stage.state === "active" && "bg-verified/10 font-medium text-verified",
        stage.state === "pending" && "text-muted-foreground/50",
        stage.state === "skipped" && "text-muted-foreground/60 line-through decoration-border",
      )}
      title={stage.detail}
    >
      {stage.state === "done" ? (
        <Check className="size-2.5 text-verified" />
      ) : stage.state === "active" ? (
        <span className="stage-active-dot size-1.5 rounded-full bg-verified" />
      ) : (
        <CircleDashed className="size-2.5 opacity-50" />
      )}
      {label}
      {isExecuting && (stage.cacheHits ?? 0) > 0 && (
        <span className="inline-flex items-center gap-0.5 font-medium text-verified">
          <Zap className="size-2.5" />
          {stage.cacheHits}
        </span>
      )}
      {stage.detail && stage.state !== "pending" && !isExecuting && (
        <span className="hidden max-w-44 truncate text-muted-foreground xl:inline">
          · {stage.detail}
        </span>
      )}
    </span>
  );
}
