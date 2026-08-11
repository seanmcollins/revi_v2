"use client";

import { TriangleAlert, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/lib/store";

/**
 * Contract drift is loud, never silent: when the API's responses are
 * missing fields this UI requires, the exact paths are shown here (and
 * console.errored by the driver). The affected frames were dropped rather
 * than rendered as partial garbage.
 */
export function ContractDriftBanner() {
  const paths = useSessionStore((s) => s.contractDrift);
  const dismiss = useSessionStore((s) => s.dismissContractDrift);

  if (paths.length === 0) return null;

  return (
    <div
      role="alert"
      className="rounded-lg border border-negative/40 bg-negative/10 px-3.5 py-2.5"
    >
      <div className="flex items-start gap-2.5">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-negative" />
        <div className="min-w-0 flex-1 space-y-1.5">
          <p className="text-body font-medium text-negative">
            Revi and this deployment disagree about the response
          </p>
          <p className="text-meta leading-snug text-muted-foreground">
            The server&apos;s responses are missing required fields, so the affected
            figures were not drawn. Field paths (also in the console):
          </p>
          <div className="flex flex-wrap gap-1">
            {paths.map((path) => (
              <code
                key={path}
                className="rounded border border-negative/30 bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-negative"
              >
                {path}
              </code>
            ))}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Dismiss contract drift banner"
          className="size-6 shrink-0 text-muted-foreground"
          onClick={dismiss}
        >
          <X className="size-3.5" />
        </Button>
      </div>
    </div>
  );
}
