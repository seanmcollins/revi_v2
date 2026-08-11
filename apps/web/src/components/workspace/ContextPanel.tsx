"use client";

import { GitBranch, Microscope } from "lucide-react";

import { EvidenceDrawer } from "@/components/evidence/EvidenceDrawer";
import { EvidenceCharts, EvidenceFacts } from "@/components/evidence/EvidenceFacts";
import { LineageGraph } from "@/components/lineage/LineageGraph";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSessionStore } from "@/lib/store";
import { useAnswerVariant } from "@/lib/useAnswerVariant";

/**
 * Right contextual panel: hosts the evidence drawer (lineage tree of the
 * selected answer) and the session lineage DAG.
 */
export function ContextPanel() {
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
    <aside className="panel flex h-full min-h-0 flex-col border-l">
      <Tabs defaultValue="evidence" className="flex h-full min-h-0 flex-col gap-0">
        <div className="border-b px-3 py-2">
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
