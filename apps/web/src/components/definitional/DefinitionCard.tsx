"use client";

import { BookMarked } from "lucide-react";

import { Separator } from "@/components/ui/separator";
import type { DefinitionCardData } from "@/lib/types";

const AUTHORITY_LABELS: Record<string, string> = {
  governed_pack: "Governed pack content",
  standard_paraphrase: "Standard, paraphrased",
  concept_dictionary: "Concept dictionary",
};

/**
 * A governed definition answer — visually distinct from analytical
 * answers: term, group-code + CARC semantics, sources with authority
 * labels, and the pack version that served it. Zero probes.
 */
export function DefinitionCard({ definition }: { definition: DefinitionCardData }) {
  return (
    <div className="overflow-hidden rounded-lg border bg-card">
      <div className="flex items-start justify-between gap-3 border-b bg-surface-sunken/50 px-4 py-3">
        <div className="flex items-center gap-2.5">
          <span className="flex size-8 items-center justify-center rounded-md border border-verified/40 bg-verified/10">
            <BookMarked className="size-4 text-verified" />
          </span>
          <div>
            <h3 className="text-[0.9rem] font-semibold leading-tight">{definition.term}</h3>
            <p className="font-mono text-meta text-muted-foreground">
              {definition.normalizedTo}
            </p>
          </div>
        </div>
        <span className="rounded-full border px-2 py-0.5 font-mono text-micro text-muted-foreground">
          {definition.packVersion.packId}@{definition.packVersion.version}
        </span>
      </div>

      <div className="space-y-3 px-4 py-3.5">
        <p className="text-body leading-relaxed">{definition.definition}</p>

        {(definition.groupCode || definition.carc) && (
          <div className="grid gap-2 sm:grid-cols-2">
            {definition.groupCode && (
              <div className="rounded-md border bg-surface-sunken/50 p-2.5">
                <p className="font-mono text-meta font-semibold text-verified">
                  Group {definition.groupCode.code}
                </p>
                <p className="mt-0.5 text-meta leading-snug text-muted-foreground">
                  {definition.groupCode.meaning}
                </p>
              </div>
            )}
            {definition.carc && (
              <div className="rounded-md border bg-surface-sunken/50 p-2.5">
                <p className="font-mono text-meta font-semibold text-verified">
                  CARC {definition.carc.code} · {definition.carc.category}
                </p>
                <p className="mt-0.5 text-meta leading-snug text-muted-foreground">
                  {definition.carc.paraphrase}
                </p>
              </div>
            )}
          </div>
        )}

        <Separator />

        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h4 className="mb-1 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
              Sources
            </h4>
            <ul className="space-y-0.5">
              {definition.sources.map((source) => (
                <li key={source.label} className="flex items-center gap-1.5 text-meta">
                  <span className="rounded-full border px-1.5 py-px text-micro uppercase tracking-wide text-muted-foreground">
                    {AUTHORITY_LABELS[source.authority] ?? source.authority}
                  </span>
                  {source.label}
                </li>
              ))}
            </ul>
          </div>
          <div>
            <h4 className="mb-1 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
              Related
            </h4>
            <div className="flex max-w-56 flex-wrap gap-1">
              {definition.relatedConcepts.map((c) => (
                <span
                  key={c}
                  className="rounded-full bg-surface-sunken px-2 py-0.5 text-meta text-secondary-foreground"
                >
                  {c}
                </span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
