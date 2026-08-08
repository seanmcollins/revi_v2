"use client";

import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Database,
  EyeOff,
  FileSearch,
  Zap,
} from "lucide-react";
import { useState } from "react";

import { GradeBadge } from "@/components/answer/GradeBadge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatCents, formatCount } from "@/lib/format";
import type { EvidenceBundle, ProbeEvidence } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The lineage tree behind an answer: probes (hash, cache-hit, row counts)
 * → contracts (id@version) → operators (name@version) → reconciliation →
 * masked sample rows. Truncation and suppression are surfaced, never
 * hidden. "Every aggregate auditable to a source remit."
 */
export function EvidenceDrawer({ evidence }: { evidence: EvidenceBundle }) {
  return (
    <div className="space-y-4 text-xs">
      {evidence.zeroProbeTurn && (
        <div className="flex items-start gap-2 rounded-md border border-verified/40 bg-verified/10 p-2.5 text-[0.7rem] leading-snug text-verified">
          <Zap className="mt-0.5 size-3.5 shrink-0" />
          <p>
            Zero-probe turn — answered entirely from the session trace and evidence
            cache. The execution service asserts no warehouse queries ran.
          </p>
        </div>
      )}

      <section>
        <SectionTitle icon={<Database className="size-3" />}>
          Probes ({evidence.probes.length})
        </SectionTitle>
        {evidence.probes.length === 0 ? (
          <p className="text-[0.7rem] text-muted-foreground">None executed this turn.</p>
        ) : (
          <ul className="space-y-1.5">
            {evidence.probes.map((probe) => (
              <ProbeNode key={probe.probeId} probe={probe} />
            ))}
          </ul>
        )}
      </section>

      <section>
        <SectionTitle
          icon={
            evidence.reconciliation.status === "failed" ? (
              <AlertTriangle className="size-3 text-negative" />
            ) : (
              <CheckCircle2 className="size-3 text-verified" />
            )
          }
        >
          Reconciliation
        </SectionTitle>
        <div
          className={cn(
            "rounded-md border p-2.5 text-[0.7rem] leading-snug",
            evidence.reconciliation.status === "passed" &&
              "border-verified/40 bg-verified/5",
            evidence.reconciliation.status === "failed" &&
              "border-negative/50 bg-negative/10",
          )}
        >
          <p
            className={cn(
              "mb-1 font-mono text-[0.65rem] font-semibold uppercase tracking-wide",
              evidence.reconciliation.status === "passed" && "text-verified",
              evidence.reconciliation.status === "failed" && "text-negative",
              evidence.reconciliation.status === "not_applicable" && "text-muted-foreground",
            )}
          >
            {evidence.reconciliation.status === "passed"
              ? "PASSED"
              : evidence.reconciliation.status === "failed"
                ? "RECONCILIATION_FAILED"
                : "N/A"}
          </p>
          {evidence.reconciliation.detail && <p>{evidence.reconciliation.detail}</p>}
          {evidence.reconciliation.parentCents !== undefined &&
            evidence.reconciliation.childSumCents !== undefined && (
              <p className="num mt-1 text-muted-foreground">
                parent {formatCents(evidence.reconciliation.parentCents)} · children{" "}
                {formatCents(evidence.reconciliation.childSumCents)}
              </p>
            )}
        </div>
      </section>

      {evidence.sampleRows && (
        <section>
          <SectionTitle icon={<FileSearch className="size-3" />}>
            Sample rows (masked)
          </SectionTitle>
          <div className="overflow-x-auto rounded-md border">
            <Table className="text-[0.65rem]">
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  {evidence.sampleRows.columns.map((col) => (
                    <TableHead
                      key={col}
                      className="h-7 whitespace-nowrap px-2 font-mono text-[0.6rem]"
                    >
                      {col}
                      {evidence.sampleRows?.maskedColumns.includes(col) && (
                        <EyeOff className="ml-1 inline size-2.5 text-muted-foreground" />
                      )}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {evidence.sampleRows.rows.map((row, i) => (
                  <TableRow key={i}>
                    {row.map((cell, j) => (
                      <TableCell key={j} className="num whitespace-nowrap px-2 py-1.5">
                        {cell}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
          <p className="mt-1 text-[0.62rem] leading-snug text-muted-foreground">
            PHI masked; purpose: {evidence.sampleRows.purpose}.
          </p>
        </section>
      )}

      {evidence.traceNote && (
        <section>
          <SectionTitle icon={<FileSearch className="size-3" />}>Trace</SectionTitle>
          <p className="rounded-md border bg-surface-sunken/50 p-2.5 text-[0.68rem] leading-relaxed text-muted-foreground">
            {evidence.traceNote}
          </p>
        </section>
      )}
    </div>
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
    <h4 className="mb-1.5 flex items-center gap-1.5 text-[0.65rem] font-semibold uppercase tracking-wide text-muted-foreground">
      {icon}
      {children}
    </h4>
  );
}

function ProbeNode({ probe }: { probe: ProbeEvidence }) {
  const [open, setOpen] = useState(false);
  return (
    <li className="rounded-md border bg-card">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 px-2.5 py-2 text-left"
      >
        <ChevronRight
          className={cn("size-3 shrink-0 text-muted-foreground transition-transform duration-150", open && "rotate-90")}
        />
        <code className="shrink-0 rounded bg-surface-sunken px-1 py-0.5 font-mono text-[0.62rem] text-verified">
          {probe.probeHash}
        </code>
        <span className="min-w-0 flex-1 truncate text-[0.7rem]">{probe.description}</span>
        {probe.cacheHit && (
          <span className="inline-flex shrink-0 items-center gap-0.5 rounded-full bg-verified/10 px-1.5 py-0.5 text-[0.6rem] font-medium text-verified">
            <Zap className="size-2.5" />
            cache
          </span>
        )}
      </button>
      {open && (
        <div className="space-y-1.5 border-t px-2.5 py-2 pl-7 text-[0.68rem]">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="rounded-full border px-1.5 py-0.5 font-mono text-[0.6rem] text-muted-foreground">
              {probe.kind}
            </span>
            {probe.contract && (
              <span className="rounded-full border border-verified/40 px-1.5 py-0.5 font-mono text-[0.6rem] text-verified">
                {probe.contract.id}@{probe.contract.version}
              </span>
            )}
            {probe.operators.map((op) => (
              <span
                key={`${op.name}@${op.version}`}
                className="rounded-full border px-1.5 py-0.5 font-mono text-[0.6rem] text-muted-foreground"
              >
                {op.name}@{op.version}
              </span>
            ))}
            <GradeBadge grade={probe.grade} size="xs" />
          </div>
          <p className="num text-muted-foreground">
            {formatCount(probe.rowCount)} row{probe.rowCount === 1 ? "" : "s"}
            {probe.truncated && (
              <span className="ml-1.5 text-warning">· truncated (LIMIT reached)</span>
            )}
            {probe.suppressedCells > 0 && (
              <span className="ml-1.5 text-warning">
                · {probe.suppressedCells} small cell{probe.suppressedCells === 1 ? "" : "s"} suppressed
              </span>
            )}
          </p>
        </div>
      )}
    </li>
  );
}
