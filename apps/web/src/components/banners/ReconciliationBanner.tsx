"use client";

import { AlertTriangle } from "lucide-react";

import type { ReconciliationResult } from "@/lib/types";

/**
 * The failure case is never hidden: when children do not sum to the
 * parent, the answer is flagged loudly.
 *
 * What it no longer prints is the parent total and the row sum. The
 * engine records the §7.8 verdict as a status and a reason — the two
 * totals are computed inside the reconcile operator and not carried out
 * of it — so those figures had no source on a live turn. The recorded
 * reason is shown instead, and the raw summary sits in the drawer.
 */
export function ReconciliationBanner({ result }: { result?: ReconciliationResult }) {
  if (result?.status !== "failed") return null;
  return (
    <div
      role="alert"
      className="flex items-start gap-2.5 rounded-lg border border-negative/50 bg-negative/10 px-3.5 py-2.5"
    >
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-negative" />
      <div className="min-w-0">
        {/* Softened wording, identical meaning: the breakdown is still
            called out loudly and the numbers are still shown. The stable
            §12 code stays available in debug mode's decision trace. */}
        <p className="text-[0.75rem] font-semibold text-negative">
          These rows don&rsquo;t add up to the total
        </p>
        <p className="mt-0.5 text-[0.75rem] leading-snug">
          {result.detail ??
            "The breakdown does not sum to the number it came from. Don't act on these rows until it's resolved."}
        </p>
      </div>
    </div>
  );
}
