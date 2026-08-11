"use client";

import { ChevronsRight, GitBranch, Microscope } from "lucide-react";

import { EvidenceDrawer } from "@/components/evidence/EvidenceDrawer";
import { EvidenceCharts, EvidenceFacts } from "@/components/evidence/EvidenceFacts";
import { LineageGraph } from "@/components/lineage/LineageGraph";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { PANE_SHORTCUTS, paneToggleLabel } from "@/lib/panes";
import { useSessionStore } from "@/lib/store";
import { useAnswerVariant } from "@/lib/useAnswerVariant";

/** The one id every "focus the evidence toggle" path reaches for. */
export const EVIDENCE_TOGGLE_ID = "pane-toggle-evidence";
/** The region a focus handoff asks "was the focus in here?" about. */
export const EVIDENCE_PANE_ID = "pane-evidence";

/**
 * Right contextual panel: hosts the evidence drawer (lineage tree of the
 * selected answer) and the session lineage DAG.
 *
 * `onCollapse` is optional for the same reason the rail's toggle is: this
 * panel only folds where a grid is prepared to give the column back.
 */
export function ContextPanel({ onCollapse }: { onCollapse?: () => void } = {}) {
  const turns = useSessionStore((s) => s.turns);
  const drawerTurnId = useSessionStore((s) => s.drawerTurnId);
  // In the calm layout the facts and the supporting figures live here —
  // the answer is the writing, and this is where its working is kept.
  const variant = useAnswerVariant();

  const selected =
    (drawerTurnId ? turns.find((t) => t.id === drawerTurnId) : undefined) ??
    [...turns].reverse().find((t) => t.answer.evidence !== undefined) ??
    turns[turns.length - 1];
  const selectedIndex = selected ? turns.findIndex((t) => t.id === selected.id) : -1;

  return (
    <aside id={EVIDENCE_PANE_ID} className="panel flex h-full min-h-0 flex-col border-l">
      <Tabs defaultValue="evidence" className="flex h-full min-h-0 flex-col gap-0">
        <div className="flex items-center gap-2 border-b px-3 py-2">
          {/* THE PANE'S INNER EDGE is its LEFT one — the side facing the
              answer, which is where a reader's hand already is when they
              decide they are done with the working. */}
          {onCollapse && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  id={EVIDENCE_TOGGLE_ID}
                  type="button"
                  onClick={onCollapse}
                  aria-label={paneToggleLabel("evidence", false)}
                  aria-expanded
                  aria-controls={EVIDENCE_PANE_ID}
                  className="focus-ring flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground transition-colors duration-150 hover:bg-accent/60 hover:text-foreground"
                >
                  <ChevronsRight aria-hidden className="size-3.5" />
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="text-meta">
                {paneToggleLabel("evidence", false)} · {PANE_SHORTCUTS.evidence}
              </TooltipContent>
            </Tooltip>
          )}
          <TabsList className="h-7 w-full bg-surface-sunken">
            <TabsTrigger value="evidence" className="h-5 gap-1 text-meta">
              <Microscope className="size-3" />
              Evidence
            </TabsTrigger>
            <TabsTrigger value="lineage" className="h-5 gap-1 text-meta">
              <GitBranch className="size-3" />
              Lineage
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="evidence" className="min-h-0 flex-1">
          <ScrollArea className="h-full">
            <div className="px-3.5 py-3">
              {selected ? (
                /*
                 * The rail renders what this turn HAS and states what it
                 * does not. It used to gate its whole body — the facts
                 * included — on `answer.evidence`, and the calm layout
                 * defers the facts to here: a turn whose bundle is absent
                 * (the server's own restoration notes anticipate exactly
                 * that: "its evidence and governed-provenance blocks are
                 * absent rather than empty") therefore had its facts
                 * nowhere, while the panel showed some OTHER turn's
                 * bundle under this turn's question. Facts are not
                 * coupled to the bundle.
                 */
                <>
                  <p className="mb-2.5 flex items-baseline gap-1.5 text-micro text-muted-foreground">
                    <span className="rounded border border-verified/40 bg-verified/10 px-1 font-mono text-micro text-verified">
                      T{selectedIndex + 1}
                    </span>
                    {selected.submission.utterance}
                  </p>
                  {/* THE FACTS, above the checks that produced them. Only
                      in the calm layout — the other two keep the cards on
                      the answer, and the same rows in both places would be
                      the repetition this change exists to remove. */}
                  {variant === "b" && (
                    <>
                      <EvidenceFacts turn={selected} />
                      <EvidenceCharts turn={selected} />
                    </>
                  )}
                  {selected.answer.evidence ? (
                    <EvidenceDrawer evidence={selected.answer.evidence} />
                  ) : (
                    <p className="text-meta leading-relaxed text-muted-foreground">
                      This answer published no evidence — either no data check ran, or the
                      record the server kept has none. Anything it did publish is above.
                    </p>
                  )}
                </>
              ) : (
                /*
                 * One empty state for both modes now. It used to fork:
                 * api mode said "this deployment does not publish the
                 * evidence bundle yet", which was true — `answer.evidence`
                 * was never populated — and mock mode promised "a masked
                 * sample of the underlying rows", which the platform has
                 * never stored. The server publishes the bundle on every
                 * answer now, so the first branch is gone; the sample-rows
                 * promise went with it rather than being carried over.
                 */
                <p className="py-6 text-center text-meta leading-relaxed text-muted-foreground">
                  No evidence yet. Every answer keeps its full working — the checks it
                  ran, what each returned, and whether the parts add up.
                </p>
              )}
            </div>
          </ScrollArea>
        </TabsContent>

        <TabsContent value="lineage" className="min-h-0 flex-1">
          <ScrollArea className="h-full">
            <div className="px-3.5 py-3">
              <LineageGraph />
            </div>
          </ScrollArea>
        </TabsContent>
      </Tabs>
    </aside>
  );
}

/**
 * WHAT IS LEFT OF THE EVIDENCE RAIL WHEN IT IS FOLDED.
 *
 * The right pane collapses all the way — unlike the left one, nothing in
 * it is wayfinding and nothing in it is needed to keep working. But "all
 * the way" cannot mean "gone without trace": a reader who folded it a
 * week ago and now wants to check a number would have no way back that
 * did not involve remembering a keyboard shortcut.
 *
 * So one slim vertical affordance stays at the viewport's edge, reading
 * "Evidence" — the word the tab inside the rail uses, because two names
 * for one panel is how a reader concludes they are two panels
 * (docs/client-language.md §2). It is a real button in the tab order, not
 * a hover-revealed edge: a control only a pointer can find is a control
 * half the readers do not have.
 *
 * It carries the same id as the toggle inside the expanded rail, so
 * "focus this pane's toggle" resolves to whichever one is on screen.
 */
export function EvidenceEdgeTab({ onExpand }: { onExpand: () => void }) {
  const label = paneToggleLabel("evidence", true);
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          id={EVIDENCE_TOGGLE_ID}
          type="button"
          onClick={onExpand}
          aria-label={label}
          aria-expanded={false}
          className="focus-ring panel absolute right-0 top-1/2 z-20 flex -translate-y-1/2 flex-col items-center gap-2 rounded-l-md border border-r-0 px-1 py-3.5 text-muted-foreground shadow-sm transition-colors duration-150 hover:text-foreground"
        >
          <Microscope aria-hidden className="size-3" />
          <span className="evidence-edge-tab text-micro font-medium uppercase tracking-[0.14em]">
            Evidence
          </span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="left" className="text-meta">
        {label} · {PANE_SHORTCUTS.evidence}
      </TooltipContent>
    </Tooltip>
  );
}
