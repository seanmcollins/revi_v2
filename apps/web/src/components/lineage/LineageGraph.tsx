"use client";

import { CornerLeftUp } from "lucide-react";

import { untitledTurnLabel } from "@/lib/format";
import { useSessionLineageQuery } from "@/lib/queries";
import { useSessionStore, type ReferentEntry, type TurnRecord } from "@/lib/store";
import type { SessionLineageData, TurnClass } from "@/lib/types";
import { scrollIntoViewRespectingMotion } from "@/lib/useReducedMotion";
import { cn } from "@/lib/utils";

const TURN_CLASS_LABELS: Record<TurnClass, string> = {
  new_investigation: "New investigation",
  refinement: "Refinement",
  presentation_only: "Presentation",
  context_control: "Context",
  meta: "Meta",
  clarification_response: "Clarification",
  definitional: "Definition",
};

/** Never print a wire enum at the reader: an unmapped class falls back to prose. */
function turnClassLabel(turnClass: TurnClass): string {
  return TURN_CLASS_LABELS[turnClass] ?? "Turn";
}

interface LineageNodeVM {
  /** React key — the turn id this node was built from. */
  id: string;
  /**
   * The ORDINAL shown in the badge ("T1", "T2"): a position in the thread,
   * never a name. The badge is a fixed 28px square, so anything longer
   * than three characters breaks the row — see `question` for the name.
   */
  ordinal: string;
  /** Local turn id to scroll to (server nodes map by id, then by position). */
  scrollTurnId: string | null;
  /** The turn's name — the question as asked. Rendered as the row's title. */
  question: string;
  turnClass: TurnClass;
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

  const nodes = buildLineageNodes({
    turns,
    referents,
    serverData: mode === "api" ? lineageQuery.data : undefined,
  });

  if (nodes.length === 0) {
    return (
      <p className="text-meta leading-relaxed text-muted-foreground">
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
            /* The spine runs under the CENTRE of the badge above it, not
               under the row's left edge: the badge is 28px inside a 1px
               border and 8px of padding, so its centre is 23px in and a
               1px rule centred there starts at 22.5px. It was at 14.4px,
               which drew the connector through the badge's left shoulder
               and made the thread look unhooked from its nodes. */
            <div className="ml-[22.5px] flex items-stretch gap-2 py-0.5">
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
                      className="rounded border bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-muted-foreground"
                    >
                      {op}
                    </code>
                  ))
                ) : (
                  <span className="rounded border bg-surface-sunken px-1.5 py-0.5 text-micro text-muted-foreground">
                    {node.turnClass === "new_investigation"
                      ? "New context"
                      : turnClassLabel(node.turnClass)}
                  </span>
                )}
              </div>
            </div>
          )}
          <button
            type="button"
            onClick={() => {
              if (node.scrollTurnId) {
                scrollIntoViewRespectingMotion(
                  document.getElementById(`lineage-turn-${node.scrollTurnId}`),
                  { block: "start" },
                );
              }
            }}
            className="group flex w-full items-start gap-2.5 rounded-md border bg-card p-2 text-left transition-colors duration-150 hover:border-ring/40"
          >
            <span
              className={cn(
                // `overflow-hidden` is structural, not cosmetic: this badge
                // is a fixed 28px square and whatever it is handed must be
                // clipped rather than allowed to reflow the row around it.
                "flex size-7 shrink-0 items-center justify-center overflow-hidden rounded-md font-mono text-meta font-semibold leading-none",
                node.turnClass === "meta"
                  ? "border border-dashed text-muted-foreground"
                  : "border border-verified/40 bg-verified/10 text-verified",
              )}
            >
              {node.ordinal}
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-meta font-medium leading-snug">
                {node.question}
              </span>
              <span className="mt-0.5 flex items-center gap-1.5 text-micro text-muted-foreground">
                {turnClassLabel(node.turnClass)}
                {node.citedLabel && (
                  <span className="inline-flex items-center gap-0.5 text-verified">
                    <CornerLeftUp className="size-2.5" />
                    Cites {node.citedLabel} · {node.citedReferent}
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

/**
 * The DAG as rows, from the server when it has answered and from the local
 * thread until then.
 *
 * Exported and pure so the two shapes can be pinned side by side: the
 * server's node list has no ordinal and no display name of its own, and
 * conflating those two with the wire's `label` is what put a whole question
 * inside the 28px badge (`label` is a NAME — `parseSessionLineage` derives
 * it from the question — and the badge wants a POSITION).
 */
export function buildLineageNodes({
  turns,
  referents,
  serverData,
}: {
  turns: TurnRecord[];
  referents: Record<string, ReferentEntry>;
  serverData: SessionLineageData | undefined;
}): LineageNodeVM[] {
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

  if (serverData && serverData.nodes.length > 0) {
    const localIds = new Set(turns.map((t) => t.id));
    return serverData.nodes.map((node, i) => {
      // Joined on `turnId`, the edge's own turn — NOT on `child_id`, which
      // is an investigation id and matches no node's `turnId` ever.
      const operators = serverData.edges.find((e) => e.turnId === node.turnId)?.operators ?? [];
      return {
        id: node.turnId,
        ordinal: `T${i + 1}`,
        // The server's turn ids and the store's are different namespaces
        // (the store mints `turn_1`, `turn_2`… as it streams), so this
        // prefers a real id match and falls back to position — which is
        // sound because the store rebuilds the thread from this same list,
        // in this same order.
        scrollTurnId: localIds.has(node.turnId) ? node.turnId : (turns[i]?.id ?? null),
        question: node.question || node.label,
        turnClass: node.turnClass,
        operators,
        ...citation(operators),
      };
    });
  }

  return turns
    .filter((t) => t.answer.turnClass !== undefined)
    .map((t, i) => {
      const operators = t.answer.interpretation?.appliedOperators ?? [];
      return {
        id: t.id,
        ordinal: `T${i + 1}`,
        scrollTurnId: t.id,
        question:
          t.submission.utterance ??
          t.submission.clarificationResponse ??
          untitledTurnLabel(t.submission),
        turnClass: t.answer.turnClass as TurnClass,
        operators,
        ...citation(operators),
      };
    });
}
