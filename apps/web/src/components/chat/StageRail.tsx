"use client";

import { Check, ChevronRight, CircleDashed, Zap } from "lucide-react";
import { useId, useState } from "react";

import type { StageStatus } from "@/lib/store";
import { PLAIN_STAGE_GROUPS, STAGE_LABELS, type StageId } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Progress for a turn, at two levels of precision.
 *
 * Default: four plain-language steps — "Reading your question", "Deciding
 * what to check", "Checking the numbers", "Writing it up". The eight engine
 * stages are grouped, not hidden: a group's state is derived from the
 * stages inside it, so a skipped step still reads as skipped and a stalled
 * one still reads as stalled.
 *
 * Debug: the engine's own eight stages, named the way the engine names
 * them (classified → interpreted → planned → validated → executing i/n
 * probes → calculating → reconciled → narrating). Same data, one toggle
 * apart — the precision is moved, never dropped.
 *
 * Designed for tens-of-seconds latency: fully expanded while streaming,
 * collapsed to a one-line summary afterwards.
 */
export function StageRail({
  stages,
  streaming,
  cacheHits,
  debug = false,
}: {
  stages: StageStatus[];
  streaming: boolean;
  cacheHits: number;
  debug?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const showList = streaming || expanded;
  const listId = useId();

  const executing = stages.find((s) => s.stage === "executing");
  const probeCount = executing?.probesTotal;
  const groups = groupStages(stages);
  const doneCount = debug
    ? stages.filter((s) => s.state === "done").length
    : groups.filter((g) => g.state === "done").length;
  const skippedCount = debug
    ? stages.filter((s) => s.state === "skipped").length
    : groups.filter((g) => g.state === "skipped").length;

  if (!showList) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        // The chevron is the only thing that said this summary opens.
        aria-expanded={false}
        aria-controls={listId}
        className="group flex items-center gap-1.5 text-[0.68rem] text-muted-foreground transition-colors duration-150 hover:text-foreground"
      >
        <Check className="size-3 text-verified" />
        {debug ? (
          <>
            {doneCount} stage{doneCount === 1 ? "" : "s"}
            {skippedCount > 0 && ` · ${skippedCount} skipped (zero-probe path)`}
            {probeCount !== undefined && ` · ${probeCount} probe${probeCount === 1 ? "" : "s"}`}
          </>
        ) : (
          <>
            {doneCount} step{doneCount === 1 ? "" : "s"}
            {skippedCount > 0 && " · some steps weren’t needed"}
            {probeCount !== undefined &&
              ` · ${probeCount} data check${probeCount === 1 ? "" : "s"}`}
          </>
        )}
        {cacheHits > 0 && (
          <span className="inline-flex items-center gap-0.5 text-verified">
            <Zap className="size-3" />
            {cacheHits} {debug ? `cache hit${cacheHits === 1 ? "" : "s"}` : "reused"}
          </span>
        )}
        <ChevronRight className="size-3 opacity-0 transition-opacity duration-150 group-hover:opacity-100" />
      </button>
    );
  }

  const nodes = debug
    ? stages.map((stage) => ({
        key: stage.stage as string,
        state: stage.state,
        label: technicalLabel(stage),
        detail: stage.detail,
        cacheHits: stage.stage === "executing" ? (stage.cacheHits ?? 0) : 0,
        showDetail: stage.stage !== "executing",
      }))
    : groups.map((group) => ({
        key: group.id,
        state: group.state,
        label: group.label,
        // Ids, hashes and stage detail strings are engine vocabulary — the
        // plain rail carries the step name and the honest counters only.
        detail: undefined,
        cacheHits: 0,
        showDetail: false,
      }));

  return (
    <ol
      id={listId}
      className="flex flex-wrap items-center gap-x-1 gap-y-1.5"
      onClick={() => !streaming && setExpanded(false)}
    >
      {nodes.map((node, i) => (
        <li key={node.key} className="flex items-center gap-1">
          {i > 0 && <span className="mx-0.5 h-px w-2.5 bg-border" />}
          <StageNode node={node} />
        </li>
      ))}
    </ol>
  );
}

/* ------------------------------------------------------------------ */
/* Grouping                                                            */
/* ------------------------------------------------------------------ */

interface GroupStatus {
  id: string;
  label: string;
  state: StageStatus["state"];
}

/**
 * Roll the eight engine stages up into the four plain steps. A group is
 * skipped only when every stage in it was skipped, active while any stage
 * in it is running, and done once every stage in it has finished or been
 * skipped — so nothing reports progress the pipeline has not made.
 */
export function groupStages(stages: StageStatus[]): GroupStatus[] {
  const byId = new Map<StageId, StageStatus>(stages.map((s) => [s.stage, s]));
  return PLAIN_STAGE_GROUPS.map((group) => {
    const members = group.stages
      .map((stage) => byId.get(stage))
      .filter((s): s is StageStatus => s !== undefined);
    let state: StageStatus["state"] = "pending";
    if (members.length > 0) {
      if (members.every((m) => m.state === "skipped")) state = "skipped";
      else if (members.some((m) => m.state === "active")) state = "active";
      else if (members.every((m) => m.state === "done" || m.state === "skipped")) state = "done";
    }
    return { id: group.id, label: group.label, state };
  });
}

function technicalLabel(stage: StageStatus): string {
  if (stage.stage === "executing" && stage.probesTotal !== undefined && stage.probesTotal > 0) {
    return `probes ${stage.probesDone ?? 0}/${stage.probesTotal}`;
  }
  return STAGE_LABELS[stage.stage].toLowerCase();
}

/* ------------------------------------------------------------------ */
/* Node                                                                */
/* ------------------------------------------------------------------ */

interface RailNode {
  key: string;
  state: StageStatus["state"];
  label: string;
  detail?: string;
  cacheHits: number;
  showDetail: boolean;
}

function StageNode({ node }: { node: RailNode }) {
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-full px-1.5 text-[0.65rem] transition-all duration-200",
        node.state === "done" && "text-secondary-foreground",
        node.state === "active" && "bg-verified/10 font-medium text-verified",
        // Opacity on TEXT is a contrast failure, not a hierarchy device:
        // /50 muted-foreground lands near 2:1 in light mode. The pending
        // and skipped states keep their meaning through the icon, the
        // strike-through and the weight instead.
        node.state === "pending" && "text-muted-foreground",
        node.state === "skipped" && "text-muted-foreground line-through decoration-border/70",
      )}
      title={node.detail}
    >
      {node.state === "done" ? (
        <Check className="size-2.5 text-verified" />
      ) : node.state === "active" ? (
        <span className="stage-active-dot size-1.5 rounded-full bg-verified" />
      ) : (
        <CircleDashed className="size-2.5 opacity-50" />
      )}
      {node.label}
      {node.cacheHits > 0 && (
        <span className="inline-flex items-center gap-0.5 font-medium text-verified">
          <Zap className="size-2.5" />
          {node.cacheHits}
        </span>
      )}
      {node.detail && node.showDetail && node.state !== "pending" && (
        <span className="hidden max-w-44 truncate text-muted-foreground xl:inline">
          · {node.detail}
        </span>
      )}
    </span>
  );
}
