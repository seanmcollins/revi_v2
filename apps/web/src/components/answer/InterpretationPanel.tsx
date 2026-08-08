"use client";

import { ChevronRight, Languages } from "lucide-react";
import { useState } from "react";

import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import type { InterpretationData } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * "Show the interpretation" — how the question was read, BEFORE trusting
 * the results: resolved metric, window, filters, synonym mappings, and the
 * plan diff against the parent turn.
 */
export function InterpretationPanel({ interpretation }: { interpretation: InterpretationData }) {
  const [open, setOpen] = useState(false);
  const mappings = interpretation.synonymMappings;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <CollapsibleTrigger asChild>
        <button
          type="button"
          className="group flex w-full items-center gap-2 rounded-md border border-dashed bg-surface-sunken/60 px-2.5 py-1.5 text-left text-[0.7rem] text-muted-foreground transition-colors duration-150 hover:text-foreground"
        >
          <Languages className="size-3 shrink-0 opacity-70" />
          <span className="truncate">
            <span className="font-medium text-secondary-foreground">Interpreted as:</span>{" "}
            {interpretation.metric.id !== "—"
              ? `${interpretation.metric.name} (${interpretation.metric.id}@${interpretation.metric.version})`
              : "governed knowledge lookup"}
            {mappings.length > 0 && ` · ${mappings[0].from} → ${mappings[0].to}`}
          </span>
          <ChevronRight
            className={cn(
              "ml-auto size-3 shrink-0 transition-transform duration-150",
              open && "rotate-90",
            )}
          />
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-1 grid gap-3 rounded-md border bg-surface-sunken/60 p-3 text-[0.72rem] leading-snug sm:grid-cols-2">
          <section>
            <h4 className="mb-1 text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground">
              Resolution
            </h4>
            <ul className="space-y-1">
              <li>
                <span className="text-muted-foreground">Window · </span>
                {interpretation.windowDescription}
              </li>
              {interpretation.comparisonDescription && (
                <li>
                  <span className="text-muted-foreground">Comparison · </span>
                  {interpretation.comparisonDescription}
                </li>
              )}
              {interpretation.filterDescriptions.map((f) => (
                <li key={f}>
                  <span className="text-muted-foreground">Scope · </span>
                  {f}
                </li>
              ))}
              {interpretation.playbook && (
                <li>
                  <span className="text-muted-foreground">Playbook · </span>
                  <span className="font-mono text-[0.68rem]">{interpretation.playbook}</span>
                </li>
              )}
            </ul>
          </section>
          <section>
            <h4 className="mb-1 text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground">
              {interpretation.planDiff ? "Plan diff" : "Synonym mappings"}
            </h4>
            {interpretation.planDiff ? (
              <ul className="space-y-1">
                {interpretation.planDiff.map((d) => (
                  <li key={d} className="flex gap-1.5">
                    <span className="mt-[0.42em] size-1 shrink-0 rounded-full bg-verified/70" />
                    {d}
                  </li>
                ))}
              </ul>
            ) : (
              <MappingList mappings={mappings} />
            )}
            {interpretation.planDiff && mappings.length > 0 && (
              <>
                <h4 className="mb-1 mt-2.5 text-[0.62rem] font-semibold uppercase tracking-wide text-muted-foreground">
                  Synonym mappings
                </h4>
                <MappingList mappings={mappings} />
              </>
            )}
          </section>
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function MappingList({ mappings }: { mappings: InterpretationData["synonymMappings"] }) {
  return (
    <ul className="space-y-1">
      {mappings.map((m) => (
        <li key={m.from}>
          <span className="font-medium">“{m.from}”</span>
          <span className="text-muted-foreground"> → </span>
          <span className="font-mono text-[0.68rem]">{m.to}</span>
          {m.note && <span className="text-muted-foreground"> — {m.note}</span>}
        </li>
      ))}
    </ul>
  );
}
