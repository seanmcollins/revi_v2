"use client";

import { CornerLeftUp } from "lucide-react";

import { useSessionLineageQuery } from "@/lib/queries";
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

interface LineageNodeVM {
  id: string;
  /** Local turn id to scroll to (server nodes map by position). */
  scrollTurnId: string | null;
  label: string;
  turnClass: TurnClass;
  question: string;
  operators: string[];
  citedReferent?: string;
  citedLabel?: string;
}

/**
 * The session DAG: nodes are turns (immutable investigations), edges are
 * the typed refinement operators that produced them. Meta turns point back
 * at the investigation they cite. Clicking a node scrolls to its turn.
 *
 * In api mode the DAG comes from GET /v1/sessions/{sid}/lineage (refetched
 * after every turn); until it answers — and always in mock mode — the same
 * view is derived locally from the streamed turns.
 */
export function LineageGraph() {
  const turns = useSessionStore((s) => s.turns);
  const referents = useSessionStore((s) => s.referents);
  const sessionId = useSessionStore((s) => s.sessionId);
  const sessionLive = useSessionStore((s) => s.sessionLive);
  const mode = useSessionStore((s) => s.connection.mode);
  const lineageQuery = useSessionLineageQuery(
    sessionId,
    turns.length,
    mode === "api" && sessionLive,
  );

  const citation = (operators: string[]) => {
    // Meta turns cite an earlier investigation via their Explain target.
    const explain = operators.find((op) => op.startsWith("Explain("));
    const citedReferent = explain?.slice("Explain(".length, -1);
    const citedTurnId = citedReferent ? referents[citedReferent]?.turnId : undefined;
    const citedIndex = citedTurnId ? turns.findIndex((x) => x.id === citedTurnId) : -1;
    return {
      citedReferent,
      citedLabel: citedIndex >= 0 ? `T${citedIndex + 1}` : undefined,
    };
  };

  const localNodes: LineageNodeVM[] = turns
    .filter((t) => t.answer.turnClass !== undefined)
    .map((t, i) => {
      const operators = t.answer.interpretation?.appliedOperators ?? [];
      return {
        id: t.id,
        scrollTurnId: t.id,
        label: `T${i + 1}`,
        turnClass: t.answer.turnClass as TurnClass,
        question: t.submission.utterance ?? t.submission.clarificationResponse ?? "(typed refinement)",
        operators,
        ...citation(operators),
      };
    });

  const serverData = mode === "api" ? lineageQuery.data : undefined;
  const serverNodes: LineageNodeVM[] | null =
    serverData && serverData.nodes.length > 0
      ? serverData.nodes.map((node, i) => {
          const operators =
            serverData.edges.find((e) => e.childTurnId === node.turnId)?.operators ?? [];
          return {
            id: node.turnId,
            scrollTurnId: turns[i]?.id ?? null,
            label: node.label || `T${i + 1}`,
            turnClass: node.turnClass,
            question: node.question,
            operators,
            ...citation(operators),
          };
        })
      : null;

  const nodes = serverNodes ?? localNodes;

  if (nodes.length === 0) {
    return (
      <p className="text-[0.7rem] leading-relaxed text-muted-foreground">
        No turns yet. Every question you ask is recorded here, linked to the one it
        came from, so you can see how the investigation got where it did.
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
            onClick={() => {
              if (node.scrollTurnId) {
                document
                  .getElementById(`lineage-turn-${node.scrollTurnId}`)
                  ?.scrollIntoView({ behavior: "smooth", block: "start" });
              }
            }}
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
                {TURN_CLASS_LABELS[node.turnClass] ?? node.turnClass}
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
