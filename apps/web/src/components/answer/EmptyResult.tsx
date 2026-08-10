"use client";

import { SearchX } from "lucide-react";

import { humanizeColumn } from "@/lib/contract";
import { windowLine } from "@/lib/export";
import type { TurnRecord } from "@/lib/store";

/**
 * "Nothing found" rendered as an answer rather than as an absence.
 *
 * Everything in it comes off the payload that arrived: the window and
 * basis from the context header, the governed contracts from the metric
 * block, the probe count from the evidence bundle. Nothing is inferred —
 * a turn that published none of those says so by simply not listing them,
 * which is still a great deal more than an empty card wearing a badge.
 *
 * Deliberately tolerant of both payload shapes: today emptiness is a
 * `findings: []` answer, and the backend is making it typed. This branch
 * keys off what is on screen, so a typed empty answer lands here too.
 */
export function EmptyResult({
  answer,
  chartCount,
}: {
  answer: TurnRecord["answer"];
  chartCount: number;
}) {
  const checked: string[] = [];
  // `windowLine` states an as-of date for a snapshot contract and a range
  // for a flow one — the same distinction the header makes, so an empty
  // card cannot describe a scope the header just refused to claim.
  if (answer.header) checked.push(windowLine(answer.header));
  const filters = answer.header?.filters ?? [];
  for (const filter of filters) {
    checked.push(`${filter.dimensionLabel} ${filter.op} ${filter.values.join(", ")}`);
  }
  // Governed measure names in the analyst's spelling — the badge above
  // already carries the contract ids and versions for anyone who wants them.
  const metrics = answer.metric?.metrics.map((m) => humanizeColumn(m.id)) ?? [];
  if (metrics.length > 0) checked.push(metrics.join(", "));
  const checks = answer.evidence?.probes.length ?? 0;
  if (checks > 0) {
    checked.push(`${checks} data check${checks === 1 ? "" : "s"} against this data load`);
  }

  return (
    <div className="rounded-lg border border-dashed bg-card/60 p-3.5">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border bg-surface-sunken">
          <SearchX className="size-3.5 text-muted-foreground" />
        </span>
        <div className="min-w-0 space-y-2">
          <p className="text-body font-medium leading-snug">
            No findings for this question — here&apos;s what was checked
          </p>
          {checked.length > 0 ? (
            <ul className="space-y-0.5 text-meta leading-snug text-muted-foreground">
              {checked.map((line) => (
                <li key={line} className="flex gap-1.5">
                  <span aria-hidden>·</span>
                  {line}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-meta leading-snug text-muted-foreground">
              This turn came back with no window, no measure and no record of what ran — so
              there is nothing further this card can honestly say about it.
            </p>
          )}
          <p className="text-meta leading-snug text-muted-foreground">
            {answer.warnings.length > 0
              ? "The notes above are the engine's own account of why."
              : chartCount > 0
                ? "The chart below carries the rows the probes did return."
                : "The governed data has no rows matching that question at this data load — narrow it differently, or widen the window."}
          </p>
        </div>
      </div>
    </div>
  );
}
