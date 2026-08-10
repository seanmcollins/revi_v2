"use client";

import { Bug, ChevronRight } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useSessionStore, type AnswerState } from "@/lib/store";
import type { DebugTrace } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * Debug mode's per-turn decision breakdown, rendered from the server's own
 * `DebugTracePayload`.
 *
 * This is the one surface where internal vocabulary is the correct
 * vocabulary: it exists to explain how the engine reached an answer, to
 * someone who works on the engine. Probes are called probes, plan hashes
 * are shown, template ids and failure kinds are spelled the way the
 * traces spell them. Everything the analyst-facing UI moved behind plain
 * language is here, exact.
 *
 * Nothing is computed here — every value is read from the trace. A field
 * the server did not record is absent, not defaulted into something that
 * looks recorded.
 */
export function DebugTracePanel({ turnId, answer }: { turnId: string; answer: AnswerState }) {
  const [open, setOpen] = useState(false);
  const loadTrace = useSessionStore((s) => s.loadTrace);
  const trace = answer.debug;

  if (!trace) {
    // Debug is on, but this turn was answered before it was — the server
    // recorded the trace anyway, so it can still be read.
    if (!answer.investigationId) return null;
    return (
      <div className="flex flex-wrap items-center gap-2 text-meta text-muted-foreground">
        <Button
          variant="outline"
          size="xs"
          className="gap-1 text-meta font-normal"
          disabled={answer.traceFetch === "loading"}
          onClick={() => void loadTrace(turnId)}
        >
          <Bug className="size-3" />
          {answer.traceFetch === "loading" ? "Loading trace…" : "Load decision trace"}
        </Button>
        <span>answered before debug mode was on — the server recorded it anyway</span>
        {answer.traceError && (
          <span role="alert" className="w-full text-warning">
            {answer.traceError}
          </span>
        )}
      </div>
    );
  }

  const llmCost = trace.llmCalls.reduce((sum, call) => sum + (Number(call.costUsd) || 0), 0);
  const totalMs = Object.values(trace.timingsMs).reduce((sum, ms) => sum + ms, 0);

  return (
    <div className="rounded-lg border border-warning/30 bg-warning/[0.04]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-3 py-2 text-left"
      >
        <ChevronRight
          className={cn(
            "size-3 shrink-0 text-muted-foreground transition-transform duration-150",
            open && "rotate-90",
          )}
        />
        <Bug className="size-3 shrink-0 text-warning" />
        <span className="text-meta font-medium">Decision trace</span>
        <span className="num min-w-0 flex-1 truncate text-meta text-muted-foreground">
          {trace.turnClass ?? "unclassified"}
          {trace.classificationConfidence !== undefined &&
            ` (${trace.classificationConfidence.toFixed(2)})`}
          {trace.probes.length > 0 && ` · ${trace.probes.length} probes`}
          {trace.llmCalls.length > 0 &&
            ` · ${trace.llmCalls.length} llm calls · $${llmCost.toFixed(4)}`}
          {totalMs > 0 && ` · ${totalMs} ms`}
        </span>
      </button>

      {open && (
        <div className="space-y-3.5 border-t border-warning/25 px-3 py-3 text-meta">
          <Classification trace={trace} />
          <Interpretation trace={trace} />
          <Refinements trace={trace} />
          <PlanSection trace={trace} />
          <Probes trace={trace} />
          <LlmCalls trace={trace} />
          <GradeDerivation trace={trace} />
          <Provenance trace={trace} />
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Sections                                                            */
/* ------------------------------------------------------------------ */

function Classification({ trace }: { trace: DebugTrace }) {
  return (
    <Section title="Classification">
      <Rows>
        <Row label="turn_class" value={trace.turnClass ?? "—"} mono />
        <Row
          label="confidence"
          value={
            trace.classificationConfidence !== undefined
              ? trace.classificationConfidence.toFixed(3)
              : "—"
          }
          mono
        />
        {trace.clarificationReason && (
          <Row label="clarification_reason" value={trace.clarificationReason} />
        )}
        {trace.question && <Row label="question" value={trace.question} />}
      </Rows>
    </Section>
  );
}

function Interpretation({ trace }: { trace: DebugTrace }) {
  const i = trace.interpretation;
  if (!i) return null;
  return (
    <Section title="Interpretation">
      <Rows>
        {i.intentSummary && <Row label="intent" value={i.intentSummary} />}
        <Row label="metric_ids" value={i.metricIds.join(", ") || "—"} mono />
        <Row label="dimension_ids" value={i.dimensionIds.join(", ") || "—"} mono />
        <Row label="concept_ids" value={i.conceptIds.join(", ") || "—"} mono />
        <Row label="playbook_id" value={i.playbookId ?? "—"} mono />
        <Row
          label="window"
          value={
            i.windowStart || i.windowEnd
              ? `${i.windowStart ?? "?"} → ${i.windowEnd ?? "?"}${i.basis ? ` (${i.basis} basis)` : ""}`
              : "—"
          }
          mono
        />
      </Rows>
    </Section>
  );
}

function Refinements({ trace }: { trace: DebugTrace }) {
  if (
    trace.refinementOperators.length === 0 &&
    trace.referentResolutions.length === 0 &&
    !trace.refinementRationale
  ) {
    return null;
  }
  return (
    <Section title="Refinement">
      {trace.refinementOperators.length > 0 && (
        <ul className="space-y-1">
          {trace.refinementOperators.map((op, i) => (
            <li key={i}>
              <Json value={op} />
            </li>
          ))}
        </ul>
      )}
      {trace.refinementRationale && (
        <p className="mt-1 leading-snug text-muted-foreground">{trace.refinementRationale}</p>
      )}
      {trace.referentResolutions.length > 0 && (
        <div className="mt-1.5 space-y-1">
          <p className="text-micro uppercase tracking-wide text-muted-foreground">
            referent resolutions
          </p>
          {trace.referentResolutions.map((entry, i) => (
            <Json key={i} value={entry} />
          ))}
        </div>
      )}
    </Section>
  );
}

function PlanSection({ trace }: { trace: DebugTrace }) {
  return (
    <Section title="Plan + validation (§6.6)">
      <Rows>
        <Row label="plan_hash" value={trace.planHash ?? "—"} mono wrap />
        <Row label="playbook_id" value={trace.playbookId ?? "—"} mono />
      </Rows>
      {trace.warnings.length > 0 ? (
        <ul className="mt-1 space-y-0.5">
          {trace.warnings.map((warning, i) => (
            <li key={i} className="leading-snug text-warning">
              {warning}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-muted-foreground">No validation warnings.</p>
      )}
      {trace.calculationOperators.length > 0 && (
        <div className="mt-1.5 space-y-1">
          <p className="text-micro uppercase tracking-wide text-muted-foreground">
            calculation operators
          </p>
          {trace.calculationOperators.map((op, i) => (
            <Json key={i} value={op} />
          ))}
        </div>
      )}
    </Section>
  );
}

function Probes({ trace }: { trace: DebugTrace }) {
  if (trace.probes.length === 0) {
    return (
      <Section title="Probes">
        <p className="text-muted-foreground">
          None recorded — a zero-probe turn answers from the session trace and evidence
          cache.
        </p>
      </Section>
    );
  }
  return (
    <Section title={`Probes (${trace.probes.length})`}>
      <div className="overflow-x-auto rounded-md border">
        <Table className="text-micro">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              {["probe", "hash", "purpose", "rows", "limit", "grade", "ms", "flags"].map((h) => (
                <TableHead key={h} className="h-6 whitespace-nowrap px-2 font-mono text-micro">
                  {h}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {trace.probes.map((probe) => (
              <TableRow key={`${probe.id}-${probe.hash}`}>
                <TableCell className="whitespace-nowrap px-2 py-1 font-mono">{probe.id}</TableCell>
                {/* Content hashes are 64 hex chars; the prefix identifies
                    them and the full value is on the title for copying. */}
                <TableCell
                  className="whitespace-nowrap px-2 py-1 font-mono text-verified"
                  title={probe.hash}
                >
                  {probe.hash.slice(0, 12)}
                </TableCell>
                <TableCell className="max-w-56 truncate px-2 py-1">{probe.purpose}</TableCell>
                <TableCell className="num whitespace-nowrap px-2 py-1">
                  {probe.rows === null ? "not executed" : probe.rows}
                </TableCell>
                <TableCell className="num whitespace-nowrap px-2 py-1">
                  {probe.limit ?? "—"}
                </TableCell>
                <TableCell className="whitespace-nowrap px-2 py-1 font-mono">
                  {probe.grade ?? "—"}
                </TableCell>
                <TableCell className="num whitespace-nowrap px-2 py-1">
                  {probe.durationMs}
                </TableCell>
                <TableCell className="whitespace-nowrap px-2 py-1">
                  {[
                    probe.cacheHit ? "cache" : null,
                    probe.truncated ? "truncated" : null,
                    probe.suppressedCells > 0 ? `${probe.suppressedCells} suppressed` : null,
                  ]
                    .filter(Boolean)
                    .join(" · ") || "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </Section>
  );
}

function LlmCalls({ trace }: { trace: DebugTrace }) {
  if (trace.llmCalls.length === 0) {
    return (
      <Section title="LLM calls">
        <p className="text-muted-foreground">None — this turn ran without a model call.</p>
      </Section>
    );
  }
  return (
    <Section title={`LLM calls (${trace.llmCalls.length})`}>
      <div className="overflow-x-auto rounded-md border">
        <Table className="text-micro">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              {["template", "model", "in", "out", "cost", "retries", "attempts", "ms", "failure"].map(
                (h) => (
                  <TableHead
                    key={h}
                    className="h-6 whitespace-nowrap px-2 font-mono text-micro"
                  >
                    {h}
                  </TableHead>
                ),
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {trace.llmCalls.map((call, i) => (
              <TableRow key={`${call.template}-${i}`}>
                <TableCell className="whitespace-nowrap px-2 py-1 font-mono">
                  {call.template}
                </TableCell>
                <TableCell className="whitespace-nowrap px-2 py-1 font-mono">{call.model}</TableCell>
                <TableCell className="num whitespace-nowrap px-2 py-1">{call.inputTokens}</TableCell>
                <TableCell className="num whitespace-nowrap px-2 py-1">
                  {call.outputTokens}
                </TableCell>
                <TableCell className="num whitespace-nowrap px-2 py-1">${call.costUsd}</TableCell>
                <TableCell className="num whitespace-nowrap px-2 py-1">
                  {call.schemaRetries}
                </TableCell>
                <TableCell className="num whitespace-nowrap px-2 py-1">{call.attempts}</TableCell>
                <TableCell className="num whitespace-nowrap px-2 py-1">{call.durationMs}</TableCell>
                <TableCell
                  className={cn(
                    "whitespace-nowrap px-2 py-1 font-mono",
                    call.failure && "text-negative",
                  )}
                >
                  {call.failure ?? "—"}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {Object.keys(trace.templateHashes).length > 0 && (
        <p className="num mt-1 break-all text-micro text-muted-foreground">
          {Object.entries(trace.templateHashes)
            .map(([template, hash]) => `${template}=${hash}`)
            .join(" · ")}
        </p>
      )}
    </Section>
  );
}

function GradeDerivation({ trace }: { trace: DebugTrace }) {
  const nodeGrades = Object.entries(trace.grades);
  const findingGrades = Object.entries(trace.findingGrades);
  return (
    <Section title="Grade derivation">
      <Rows>
        <Row label="weakest_grade" value={trace.weakestGrade ?? "—"} mono />
        {trace.reconciliation && <Row label="reconciliation" value={trace.reconciliation} />}
      </Rows>
      {nodeGrades.length > 0 && (
        <ChipList
          label="node grades"
          entries={nodeGrades.map(([node, grade]) => `${node} → ${grade}`)}
        />
      )}
      {findingGrades.length > 0 && (
        <ChipList
          label="finding grades"
          entries={findingGrades.map(([referent, grade]) => `${referent} → ${grade}`)}
        />
      )}
    </Section>
  );
}

function Provenance({ trace }: { trace: DebugTrace }) {
  const timings = Object.entries(trace.timingsMs);
  return (
    <Section title="Provenance">
      <Rows>
        <Row
          label="watermark"
          value={`${trace.watermarkId || "—"}${trace.watermarkStale ? " (stale)" : ""}`}
          mono
        />
        <Row label="epoch" value={`${trace.epoch}${trace.reAnchored ? " (re-anchored)" : ""}`} mono />
        <Row
          label="pack"
          value={`${trace.packId}@${trace.packVersion}${
            trace.packSnapshotId ? ` · ${trace.packSnapshotId}` : ""
          }`}
          mono
        />
        <Row
          label="settings"
          value={`model_tier=${trace.settings.modelTier ?? "pin"} · max_turn_cost_usd=${
            trace.settings.maxTurnCostUsd ?? "unset"
          } · narrative=${trace.settings.narrativeDepth} · evidence=${trace.settings.evidenceDepth}`}
          mono
        />
        <Row label="trace_id" value={trace.traceId} mono />
        <Row label="investigation_id" value={trace.investigationId} mono />
        <Row label="turn_id" value={trace.turnId} mono />
      </Rows>
      {timings.length > 0 && (
        <ChipList
          label="stage timings (ms)"
          entries={timings.map(([stage, ms]) => `${stage} ${ms}`)}
        />
      )}
      {trace.redactions.length > 0 && (
        <ChipList label="withheld by the payload guard" entries={trace.redactions} />
      )}
    </Section>
  );
}

/* ------------------------------------------------------------------ */
/* Primitives                                                          */
/* ------------------------------------------------------------------ */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-1 text-micro font-semibold uppercase tracking-[0.12em] text-muted-foreground">
        {title}
      </h4>
      {children}
    </section>
  );
}

function Rows({ children }: { children: React.ReactNode }) {
  return (
    <dl className="grid grid-cols-[8.5rem_minmax(0,1fr)] gap-x-3 gap-y-0.5">{children}</dl>
  );
}

function Row({
  label,
  value,
  mono = false,
  wrap = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
  wrap?: boolean;
}) {
  return (
    <>
      <dt className="font-mono text-micro text-muted-foreground">{label}</dt>
      <dd className={cn("min-w-0", mono && "num", wrap ? "break-all" : "break-words")}>{value}</dd>
    </>
  );
}

function ChipList({ label, entries }: { label: string; entries: string[] }) {
  return (
    <div className="mt-1.5">
      <p className="mb-0.5 text-micro uppercase tracking-wide text-muted-foreground">{label}</p>
      <div className="flex flex-wrap gap-1">
        {entries.map((entry) => (
          <span
            key={entry}
            className="rounded border bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-muted-foreground"
          >
            {entry}
          </span>
        ))}
      </div>
    </div>
  );
}

function Json({ value }: { value: Record<string, unknown> }) {
  return (
    <pre className="overflow-x-auto rounded border bg-surface-sunken px-2 py-1 font-mono text-micro leading-snug">
      {JSON.stringify(value)}
    </pre>
  );
}
