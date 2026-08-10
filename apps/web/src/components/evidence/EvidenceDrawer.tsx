"use client";

import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Database,
  HelpCircle,
  Zap,
} from "lucide-react";
import { useId, useState } from "react";

import { GradeBadge } from "@/components/answer/GradeBadge";
import { formatCount } from "@/lib/format";
import { useSessionStore } from "@/lib/store";
import type { EvidenceBundle, ProbeEvidence, ReconciliationResult } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The full working behind an answer: the checks it ran (with row counts
 * and cache reuse) → the metrics and contract versions they read → whether
 * the parts add up. Truncation and suppression are surfaced, never hidden.
 * "Every aggregate auditable to a source remit."
 *
 * Every line here is a field the server recorded and published on
 * `answer.evidence`, projected from the same trace `GET .../trace` reads.
 * The drawer used to show two more sections, and both were cut when it was
 * pointed at the live API: a masked row sample (nothing stores row-level
 * content — the planner emits no row-evidence probe, so the table had no
 * source) and a prose "how this was answered" note (hand-written in the
 * fixtures, with no recorded counterpart). Showing either against a real
 * turn would have meant composing it in the browser.
 *
 * The labels are the analyst's; the engine's own vocabulary (probe hashes,
 * contract versions, timings) is one disclosure away, and opens by default
 * in debug mode.
 */
export function EvidenceDrawer({ evidence }: { evidence: EvidenceBundle }) {
  const debug = useSessionStore((s) => s.settings.debug);
  return (
    <div className="space-y-4 text-xs">
      {evidence.zeroProbeTurn && (
        <div className="flex items-start gap-2 rounded-md border border-verified/40 bg-verified/10 p-2.5 text-meta leading-snug text-verified">
          <Zap className="mt-0.5 size-3.5 shrink-0" />
          <p>
            Answered without going back to the warehouse — everything this answer needed
            was already computed in this session.{" "}
            {evidence.cacheHits > 0
              ? `${formatCount(evidence.cacheHits)} check${evidence.cacheHits === 1 ? " was" : "s were"} reused; no new query ran.`
              : "No new query ran."}
          </p>
        </div>
      )}

      <section>
        <SectionTitle icon={<Database className="size-3" />}>
          Data checks ({evidence.probes.length})
        </SectionTitle>
        {evidence.probes.length === 0 ? (
          <p className="text-meta text-muted-foreground">None ran this turn.</p>
        ) : (
          <>
            <ul className="space-y-1.5">
              {evidence.probes.map((probe, i) => (
                <ProbeNode key={probe.probeId} probe={probe} index={i + 1} debug={debug} />
              ))}
            </ul>
            <p className="num mt-1.5 text-micro text-muted-foreground">
              {formatCount(evidence.warehouseQueries)} queried the warehouse ·{" "}
              {formatCount(evidence.cacheHits)} reused from this session
            </p>
          </>
        )}
      </section>

      <ReconciliationSection reconciliation={evidence.reconciliation} debug={debug} />
    </div>
  );
}

/**
 * "Does it add up?" — with a fourth answer the engine can give and the
 * drawer used not to have: nothing was recorded at all. A META citation or
 * a kernel-only refinement never reaches the §7.8 check, and saying
 * "nothing to reconcile" there would be a verdict nobody returned.
 */
function ReconciliationSection({
  reconciliation,
  debug,
}: {
  reconciliation?: ReconciliationResult;
  debug: boolean;
}) {
  const status = reconciliation?.status;
  const passed = status === "passed" || status === "passed_with_suppression";
  return (
    <section>
      <SectionTitle
        icon={
          status === "failed" ? (
            <AlertTriangle className="size-3 text-negative" />
          ) : passed ? (
            <CheckCircle2 className="size-3 text-verified" />
          ) : (
            <HelpCircle className="size-3" />
          )
        }
      >
        Does it add up?
      </SectionTitle>
      <div
        className={cn(
          "rounded-md border p-2.5 text-meta leading-snug",
          passed && "border-verified/40 bg-verified/5",
          status === "failed" && "border-negative/50 bg-negative/10",
        )}
      >
        <p
          className={cn(
            "mb-1 text-meta font-semibold",
            passed && "text-verified",
            status === "failed" && "text-negative",
            !passed && status !== "failed" && "text-muted-foreground",
          )}
        >
          {status === "passed"
            ? "Yes — the parts sum to the total"
            : status === "passed_with_suppression"
              ? "Yes — within the allowance for hidden small cells"
              : status === "failed"
                ? "No — the parts don’t sum to the total"
                : status === "not_applicable"
                  ? "This turn ran the check and it didn’t apply"
                  : status === "unknown"
                    ? "The recorded verdict wasn’t in a form this build can read"
                    : "No reconciliation check ran on this turn"}
        </p>
        {reconciliation?.detail && <p>{reconciliation.detail}</p>}
        {debug && reconciliation && (
          <code className="mt-1.5 block font-mono text-micro text-muted-foreground">
            {reconciliation.summary}
          </code>
        )}
      </div>
    </section>
  );
}

function SectionTitle({
  icon,
  children,
}: {
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <h4 className="mb-1.5 flex items-center gap-1.5 text-meta font-semibold uppercase tracking-wide text-muted-foreground">
      {icon}
      {children}
    </h4>
  );
}

/**
 * One data check. The headline is what it looked up and what came back —
 * completeness facts (cut off at the limit, small cells hidden) stay on
 * the face of it, because those change what the answer can be used for.
 * The ids, hashes and contract versions sit under "Technical details",
 * open by default in debug mode.
 */
function ProbeNode({
  probe,
  index,
  debug,
}: {
  probe: ProbeEvidence;
  index: number;
  debug: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [showTechnical, setShowTechnical] = useState(debug);
  const detailId = useId();
  return (
    <li className="rounded-md border bg-card">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        // A disclosure whose state is drawn by a rotating chevron and
        // announced by nothing: a screen-reader user heard the probe's
        // description and had no way to know it opened.
        aria-expanded={open}
        aria-controls={detailId}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
      >
        <ChevronRight
          className={cn("size-3 shrink-0 text-muted-foreground transition-transform duration-150", open && "rotate-90")}
        />
        <span className="num shrink-0 rounded bg-surface-sunken px-1 py-0.5 text-micro text-muted-foreground">
          {index}
        </span>
        <span className="min-w-0 flex-1 truncate text-meta">{probe.description}</span>
        {probe.cacheHit && (
          <span
            className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-verified/10 px-1.5 py-0.5 text-micro font-medium text-verified"
            title="Reused from an earlier turn in this session instead of querying again."
          >
            <Zap className="size-2.5" />
            Reused
          </span>
        )}
      </button>
      {open && (
        <div id={detailId} className="space-y-1.5 border-t px-2.5 py-2 pl-7 text-meta">
          {probe.grade && (
            <div className="flex flex-wrap items-center gap-1.5">
              <GradeBadge grade={probe.grade} size="xs" />
            </div>
          )}
          <p className="num text-muted-foreground">
            {/* No row count means the probe was planned and never ran —
                which is not the same as coming back empty, so it says
                that instead of printing a zero. */}
            {probe.rowCount === undefined ? (
              <span>Planned — not executed this turn</span>
            ) : (
              <>
                {formatCount(probe.rowCount)} row{probe.rowCount === 1 ? "" : "s"} returned
              </>
            )}
            {probe.truncated && (
              <span className="ml-1.5 text-warning">
                · cut off at the row limit
                {probe.limit !== undefined ? ` (${formatCount(probe.limit)})` : ""}
              </span>
            )}
            {probe.suppressedCells > 0 && (
              <span className="ml-1.5 text-warning">
                · {probe.suppressedCells} small cell{probe.suppressedCells === 1 ? "" : "s"} hidden
                for privacy
              </span>
            )}
          </p>
          <button
            type="button"
            onClick={() => setShowTechnical(!showTechnical)}
            className="text-micro text-muted-foreground underline-offset-2 hover:underline"
          >
            {showTechnical ? "Hide technical details" : "Technical details"}
          </button>
          {showTechnical && (
            <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
              <code
                className="max-w-[12rem] truncate rounded bg-surface-sunken px-1 py-0.5 font-mono text-micro text-verified"
                title={probe.probeHash}
              >
                {probe.probeHash.slice(0, 12)}
              </code>
              {probe.kind && (
                <span className="rounded-full border px-1.5 py-0.5 font-mono text-micro text-muted-foreground">
                  {probe.kind}
                </span>
              )}
              {probe.metrics.map((metric) => (
                <span
                  key={metric.id}
                  className="rounded-full border border-verified/40 px-1.5 py-0.5 font-mono text-micro text-verified"
                >
                  {metric.id}
                  {metric.contractVersion !== undefined ? `@${metric.contractVersion}` : ""}
                </span>
              ))}
              {probe.durationMs > 0 && (
                <span className="num rounded-full border px-1.5 py-0.5 text-micro text-muted-foreground">
                  {probe.durationMs} ms
                </span>
              )}
            </div>
          )}
        </div>
      )}
    </li>
  );
}
