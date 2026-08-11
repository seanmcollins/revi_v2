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
          className="inline-flex h-5 items-center gap-1 rounded-full border border-verified/40 bg-verified/10 px-2 text-meta font-medium text-verified transition-colors duration-150 hover:bg-verified/20"
        >
          <ShieldCheck className="size-3" />
          {/* NOT "Governed". §2 translates the platform's `governed` to
              "standard" and §2.1 bans it as a bare authority claim; what
              this chip actually asserts is that a person wrote and
              versioned the definition this answer measured. */}
          Standard definition
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0 text-xs">
        <div className="space-y-0.5 px-4 py-3">
          <p className="text-body font-semibold">
            {primary
              ? "Standard definition"
              : `${metrics.length} standard definitions`}
          </p>
          <p className="font-mono text-meta text-muted-foreground">
            {primary ? refLabel(primary) : (playbookId ?? "no approach recorded")}
          </p>
        </div>
        <Separator />
        <dl className="space-y-2.5 px-4 py-3">
          {!primary && (
            <div>
              <dt className="text-meta font-medium uppercase tracking-wide text-muted-foreground">
                Measures read
              </dt>
              <dd className="mt-0.5 space-y-0.5 font-mono text-meta leading-snug">
                {metrics.map((ref) => (
                  <p key={ref.id}>{refLabel(ref)}</p>
                ))}
              </dd>
            </div>
          )}
          {primary && playbookId && (
            <div>
              <dt className="text-meta font-medium uppercase tracking-wide text-muted-foreground">
                How it was worked
              </dt>
              <dd className="mt-0.5 font-mono text-meta">{playbookId}</dd>
            </div>
          )}
          <div>
            <dt className="text-meta font-medium uppercase tracking-wide text-muted-foreground">
              Definitions library
            </dt>
            <dd className="mt-0.5 font-mono text-meta">
              {pack.packId}@{pack.version}
            </dd>
            {packSnapshotId && (
              <dd className="mt-0.5 font-mono text-micro text-muted-foreground">
                snapshot {packSnapshotId.slice(0, 12)}…
              </dd>
            )}
          </div>
        </dl>
        <Separator />
        <p className="px-4 py-2.5 text-meta leading-snug text-muted-foreground">
          “Standard” means a person authored and versioned these definitions in your
          definitions library — it is not a statement of model confidence. The version
          shown is the one this answer read
          {unread && ", where one was read at all"}.
        </p>
      </PopoverContent>
    </Popover>
  );
}
