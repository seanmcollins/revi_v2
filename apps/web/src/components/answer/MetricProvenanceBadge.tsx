"use client";

import { ShieldCheck } from "lucide-react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Separator } from "@/components/ui/separator";
import type { MetricContractRef, MetricProvenance } from "@/lib/types";

/**
 * "Governed" shield. The badge means a HUMAN governed this definition —
 * never that the AI is confident. Click for the contract provenance the
 * turn actually recorded.
 *
 * What it shows is bounded by what the server projects from the turn's own
 * trace: metric ids, the contract version each was READ at, the playbook
 * that chose them, and the pack version and snapshot. The contract's
 * display name, numerator, denominator, date basis, exclusions and
 * fingerprint used to appear here from hand-written fixtures; nothing on
 * the wire backs them, and reading them out of today's pack would caption
 * an older answer with a definition it never used. An id at a version is
 * the honest handle — it is enough to look the rest up.
 *
 * A playbook turn runs several governed metrics and no one of them is the
 * headline. The server says so (`primary` absent), and this renders the
 * list rather than promoting one, because promoting one would be the badge
 * asserting a contract the turn never designated.
 */
export function hasGovernedProvenance(metric: MetricProvenance | undefined): boolean {
  if (!metric) return false;
  return metric.primary !== undefined || metric.metrics.length > 0;
}

function refLabel(ref: MetricContractRef): string {
  // No version means the probe was planned and never ran; "@?" would read
  // as a broken template, so the id stands alone and the caption explains.
  return ref.contractVersion === undefined ? ref.id : `${ref.id}@${ref.contractVersion}`;
}

export function MetricProvenanceBadge({ metric }: { metric: MetricProvenance }) {
  if (!hasGovernedProvenance(metric)) return null;
  const { primary, metrics, playbookId, pack, packSnapshotId } = metric;
  const unread = metrics.some((m) => m.contractVersion === undefined);

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
          <p className="text-[0.8rem] font-semibold">
            {primary ? "Governed metric contract" : `${metrics.length} governed metric contracts`}
          </p>
          <p className="font-mono text-[0.68rem] text-muted-foreground">
            {primary ? refLabel(primary) : (playbookId ?? "no playbook recorded")}
          </p>
        </div>
        <Separator />
        <dl className="space-y-2.5 px-4 py-3">
          {!primary && (
            <div>
              <dt className="text-[0.65rem] font-medium uppercase tracking-wide text-muted-foreground">
                Metrics read
              </dt>
              <dd className="mt-0.5 space-y-0.5 font-mono text-[0.68rem] leading-snug">
                {metrics.map((ref) => (
                  <p key={ref.id}>{refLabel(ref)}</p>
                ))}
              </dd>
            </div>
          )}
          {primary && playbookId && (
            <div>
              <dt className="text-[0.65rem] font-medium uppercase tracking-wide text-muted-foreground">
                Playbook
              </dt>
              <dd className="mt-0.5 font-mono text-[0.68rem]">{playbookId}</dd>
            </div>
          )}
          <div>
            <dt className="text-[0.65rem] font-medium uppercase tracking-wide text-muted-foreground">
              Pack
            </dt>
            <dd className="mt-0.5 font-mono text-[0.68rem]">
              {pack.packId}@{pack.version}
            </dd>
            {packSnapshotId && (
              <dd className="mt-0.5 font-mono text-[0.62rem] text-muted-foreground">
                snapshot {packSnapshotId.slice(0, 12)}…
              </dd>
            )}
          </div>
        </dl>
        <Separator />
        <p className="px-4 py-2.5 text-[0.65rem] leading-snug text-muted-foreground">
          “Governed” means a human authored and versioned these definitions in the
          metric contract — it is not a statement of model confidence. The version
          shown is the one this turn read
          {unread && ", where one was read at all"}.
        </p>
      </PopoverContent>
    </Popover>
  );
}
