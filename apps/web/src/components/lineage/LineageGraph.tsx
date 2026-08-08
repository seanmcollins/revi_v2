"use client";

import { CornerLeftUp } from "lucide-react";

import { useSessionStore } from "@/lib/store";
import type { TurnClass } from "@/lib/types";
import { cn } from "@/lib/utils";

const TURN_CLASS_LABELS: Record<TurnClass, string> = {
  new_investigation: "new investigation",
  refinement: "refinement",
  presentation_only: "presentation",
  context_control: "context",
  meta: "meta",
  clarification_response: "clarification",
  definitional: "definitional",
};

/**
 * The session DAG: nodes are turns (immutable investigations), edges are
 * the typed refinement operators that produced them. Meta turns point back
 * at the investigation they cite. Clicking a node scrolls to its turn.
 */
export function LineageGraph() {
  const turns = useSessionStore((s) => s.turns);
  const referents = useSessionStore((s) => s.referents);

  const nodes = turns
    .filter((t) => t.answer.turnClass !== undefined)
    .map((t, i) => {
      const operators = t.answer.interpretation?.appliedOperators ?? [];
      // Meta turns cite an earlier investigation via their Explain target.
      const explain = operators.find((op) => op.startsWith("Explain("));
      const citedReferent = explain?.slice("Explain(".length, -1);
      const citedTurnId = citedReferent ? referents[citedReferent]?.turnId : undefined;
      const citedIndex = citedTurnId
        ? turns.findIndex((x) => x.id === citedTurnId)
        : -1;
      return {
        id: t.id,
        label: `T${i + 1}`,
        turnClass: t.answer.turnClass as TurnClass,
        question: t.submission.utterance ?? "(typed refinement)",
        operators,
        citedReferent,
        citedLabel: citedIndex >= 0 ? `T${citedIndex + 1}` : undefined,
      };
    });

  if (nodes.length === 0) {
    return (
      <p className="text-[0.7rem] leading-relaxed text-muted-foreground">
        No turns yet. The lineage graph records each investigation as an immutable
        node linked by typed refinement edges.
      </p>
    );
  }

  return (
    <ol className="relative space-y-0">
      {nodes.map((node, i) => (
        <li key={node.id} className="relative">
          {i > 0 && (
            <div className="ml-[0.9rem] flex items-stretch gap-2 py-0.5">
              <div
                className={cn(
                  "w-px min-h-5 bg-border",
                  node.turnClass === "meta" && "border-l border-dashed border-border bg-transparent",
                )}
              />
              <div className="flex flex-wrap items-center gap-1 py-1">
                {node.operators.length > 0 ? (
                  node.operators.map((op) => (
                    <code
                      key={op}
                      className="rounded border bg-surface-sunken px-1.5 py-0.5 font-mono text-[0.58rem] text-muted-foreground"
                    >
                      {op}
                    </code>
                  ))
                ) : (
                  <code className="rounded border bg-surface-sunken px-1.5 py-0.5 font-mono text-[0.58rem] text-muted-foreground">
                    {node.turnClass === "new_investigation" ? "new context" : node.turnClass}
                  </code>
                )}
              </div>
            </div>
          )}
          <button
            type="button"
            onClick={() =>
              document
                .getElementById(`lineage-turn-${node.id}`)
                ?.scrollIntoView({ behavior: "smooth", block: "start" })
            }
            className="group flex w-full items-start gap-2.5 rounded-md border bg-card p-2 text-left transition-colors duration-150 hover:border-ring/40"
          >
            <span
              className={cn(
                "flex size-7 shrink-0 items-center justify-center rounded-md font-mono text-[0.68rem] font-semibold",
                node.turnClass === "meta"
                  ? "border border-dashed text-muted-foreground"
                  : "border border-verified/40 bg-verified/10 text-verified",
              )}
            >
              {node.label}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-[0.7rem] font-medium leading-snug">
                {node.question}
              </span>
              <span className="mt-0.5 flex items-center gap-1.5 text-[0.6rem] text-muted-foreground">
                {TURN_CLASS_LABELS[node.turnClass]}
                {node.citedLabel && (
                  <span className="inline-flex items-center gap-0.5 text-verified">
                    <CornerLeftUp className="size-2.5" />
                    cites {node.citedLabel} · {node.citedReferent}
                  </span>
                )}
              </span>
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}
