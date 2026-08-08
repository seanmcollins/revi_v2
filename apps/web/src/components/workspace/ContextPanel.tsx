"use client";

import { GitBranch, Microscope } from "lucide-react";

import { EvidenceDrawer } from "@/components/evidence/EvidenceDrawer";
import { LineageGraph } from "@/components/lineage/LineageGraph";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSessionStore } from "@/lib/store";

/**
 * Right contextual panel: hosts the evidence drawer (lineage tree of the
 * selected answer) and the session lineage DAG.
 */
export function ContextPanel() {
  const turns = useSessionStore((s) => s.turns);
  const drawerTurnId = useSessionStore((s) => s.drawerTurnId);

  const selected =
    (drawerTurnId ? turns.find((t) => t.id === drawerTurnId) : undefined) ??
    [...turns].reverse().find((t) => t.answer.evidence !== undefined);
  const selectedIndex = selected ? turns.findIndex((t) => t.id === selected.id) : -1;

  return (
    <aside className="flex h-full min-h-0 flex-col border-l bg-surface-sunken/40">
      <Tabs defaultValue="evidence" className="flex h-full min-h-0 flex-col gap-0">
        <div className="border-b px-3 py-2">
          <TabsList className="h-7 w-full bg-surface-sunken">
            <TabsTrigger value="evidence" className="h-5 gap-1 text-[0.68rem]">
              <Microscope className="size-3" />
              Evidence
            </TabsTrigger>
            <TabsTrigger value="lineage" className="h-5 gap-1 text-[0.68rem]">
              <GitBranch className="size-3" />
              Lineage
            </TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="evidence" className="min-h-0 flex-1">
          <ScrollArea className="h-full">
            <div className="px-3.5 py-3">
              {selected?.answer.evidence ? (
                <>
                  <p className="mb-2.5 flex items-baseline gap-1.5 text-[0.68rem] text-muted-foreground">
                    <span className="rounded border border-verified/40 bg-verified/10 px-1 font-mono text-[0.62rem] text-verified">
                      T{selectedIndex + 1}
                    </span>
                    {selected.submission.utterance}
                  </p>
                  <EvidenceDrawer evidence={selected.answer.evidence} />
                </>
              ) : (
                <p className="py-6 text-center text-[0.7rem] leading-relaxed text-muted-foreground">
                  No evidence yet. Every analytical answer carries a full lineage:
                  probes → contracts → operators → reconciliation → masked rows.
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
