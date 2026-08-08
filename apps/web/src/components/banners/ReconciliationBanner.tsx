"use client";

import { AlertTriangle } from "lucide-react";

import { formatCents } from "@/lib/format";
import type { ReconciliationResult } from "@/lib/types";

/**
 * The failure case is never hidden: when children do not sum to the
 * parent, the answer is flagged loudly and the numbers are shown.
 */
export function ReconciliationBanner({ result }: { result: ReconciliationResult }) {
  if (result.status !== "failed") return null;
  return (
    <div
      role="alert"
      className="flex items-start gap-2.5 rounded-lg border border-negative/50 bg-negative/10 px-3.5 py-2.5"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-negative" />
      <div className="min-w-0">
        <p className="font-mono text-[0.7rem] font-semibold uppercase tracking-wide text-negative">
          RECONCILIATION_FAILED
        </p>
        <p className="mt-0.5 text-[0.75rem] leading-snug">
          {result.detail ??
            "The decomposition does not sum to its parent. Do not act on these rows until resolved."}
        </p>
        {result.parentCents !== undefined && result.childSumCents !== undefined && (
          <p className="num mt-1 text-[0.7rem] text-muted-foreground">
            parent {formatCents(result.parentCents)} · children sum{" "}
            {formatCents(result.childSumCents)} · gap{" "}
            {formatCents(result.childSumCents - result.parentCents)}
          </p>
        )}
      </div>
    </div>
  );
}
