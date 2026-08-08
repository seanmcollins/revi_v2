"use client";

import { ShieldCheck } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import { DATE_BASIS_LABELS } from "@/lib/format";
import type { MetricContractSummary, PackVersionRef } from "@/lib/types";

/**
 * "Governed" shield. The badge means a HUMAN governed this definition —
 * never that the AI is confident. Click for the full contract provenance.
 */
export function MetricProvenanceBadge({
  metric,
  packVersion,
}: {
  metric: MetricContractSummary;
  packVersion?: PackVersionRef;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="inline-flex h-5 items-center gap-1 rounded-full border border-verified/40 bg-verified/10 px-2 text-[0.7rem] font-medium text-verified transition-colors duration-150 hover:bg-verified/20"
        >
          <ShieldCheck className="size-3" />
          Governed
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0 text-xs">
        <div className="space-y-0.5 px-4 py-3">
          <p className="text-[0.8rem] font-semibold">{metric.name}</p>
          <p className="font-mono text-[0.68rem] text-muted-foreground">
            {metric.id}@{metric.version} · {metric.kind}
          </p>
        </div>
        <Separator />
        <dl className="space-y-2.5 px-4 py-3">
          <div>
            <dt className="text-[0.65rem] font-medium uppercase tracking-wide text-muted-foreground">
              Numerator
            </dt>
            <dd className="mt-0.5 font-mono text-[0.68rem] leading-snug">{metric.numerator}</dd>
          </div>
          {metric.denominator && (
            <div>
              <dt className="text-[0.65rem] font-medium uppercase tracking-wide text-muted-foreground">
                Denominator
              </dt>
              <dd className="mt-0.5 font-mono text-[0.68rem] leading-snug">{metric.denominator}</dd>
            </div>
          )}
          <div>
            <dt className="text-[0.65rem] font-medium uppercase tracking-wide text-muted-foreground">
              Date basis
            </dt>
            <dd className="mt-0.5">{DATE_BASIS_LABELS[metric.primaryDateBasis]}</dd>
          </div>
          {metric.exclusions.length > 0 && (
            <div>
              <dt className="text-[0.65rem] font-medium uppercase tracking-wide text-muted-foreground">
                Exclusions
              </dt>
              <dd className="mt-0.5">{metric.exclusions.join(" · ")}</dd>
            </div>
          )}
          <div className="flex gap-6">
            <div>
              <dt className="text-[0.65rem] font-medium uppercase tracking-wide text-muted-foreground">
                Fingerprint
              </dt>
              <dd className="mt-0.5 font-mono text-[0.68rem]">{metric.fingerprint}…</dd>
            </div>
            {packVersion && (
              <div>
                <dt className="text-[0.65rem] font-medium uppercase tracking-wide text-muted-foreground">
                  Pack
                </dt>
                <dd className="mt-0.5 font-mono text-[0.68rem]">
                  {packVersion.packId}@{packVersion.version}
                </dd>
              </div>
            )}
          </div>
        </dl>
        <Separator />
        <p className="px-4 py-2.5 text-[0.65rem] leading-snug text-muted-foreground">
          “Governed” means a human authored and versioned this definition in the
          metric contract — it is not a statement of model confidence.
        </p>
      </PopoverContent>
    </Popover>
  );
}
