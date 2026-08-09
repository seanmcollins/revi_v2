"use client";

import { AlertTriangle, FileSearch, History, Info, SearchX, Zap } from "lucide-react";
import { useMemo } from "react";

import { ContextHeader } from "@/components/answer/ContextHeader";
import { GradeBadge } from "@/components/answer/GradeBadge";
import { InterpretationPanel } from "@/components/answer/InterpretationPanel";
import {
  hasGovernedProvenance,
  MetricProvenanceBadge,
} from "@/components/answer/MetricProvenanceBadge";
import { NarrativeText } from "@/components/answer/NarrativeText";
import { ReconciliationBanner } from "@/components/banners/ReconciliationBanner";
import { InvestigationChart } from "@/components/charts/InvestigationChart";
import { StageRail } from "@/components/chat/StageRail";
import { ClarificationPrompt } from "@/components/clarification/ClarificationPrompt";
import { DebugTracePanel } from "@/components/debug/DebugTracePanel";
import { DefinitionCard } from "@/components/definitional/DefinitionCard";
import { FeedbackTriage } from "@/components/feedback/FeedbackTriage";
import { FindingCard } from "@/components/findings/FindingCard";
import { Button } from "@/components/ui/button";
import { humanizeColumn, selectRenderableCharts } from "@/lib/contract";
import { chartWindowLabel, formatWindow } from "@/lib/format";
import { useSessionStore, type TurnRecord } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * The composed answer: stage rail → context header (always) →
 * interpretation strip → trust badges → findings → charts → narrative →
 * warnings → feedback. Nothing renders a number without its context.
 */
export function AnswerCard({ turn, active = false }: { turn: TurnRecord; active?: boolean }) {
  const openDrawer = useSessionStore((s) => s.openDrawer);
  const debug = useSessionStore((s) => s.settings.debug);
  const streaming = turn.answer.status === "streaming";
  const a = turn.answer;

  // A comparison turn publishes the same measure twice (`main` and
  // `main__compare`, byte-identical rows) and single-row frames for
  // scalars. Both were being drawn: two identical charts stacked, and a
  // "trend" through one point. See `selectRenderableCharts`.
  const charts = useMemo(() => selectRenderableCharts(a.charts), [a.charts]);
  const windowLabel = a.header ? chartWindowLabel(a.header.window) : undefined;

  /**
   * An answer that completed with nothing to show. This is a real, typed
   * outcome — the question was legible, the probes ran, and the governed
   * data had no rows to report — and it used to render as a card that was
   * blank apart from a "Governed" badge, which reads as a bug and, worse,
   * as a certified nothing. The branch below says what was checked
   * instead, from what the payload actually carries.
   */
  const emptyResult =
    !streaming &&
    a.status === "complete" &&
    a.findings.length === 0 &&
    !a.definition &&
    !a.clarification &&
    !a.error &&
    a.narrative.trim() === "";

  return (
    <div className={cn("space-y-3", active && "relative isolate")}>
      {/* Depth model: a faint accent glow marks the answer being read. */}
      {active && (
        <div
          aria-hidden
          className="answer-glow pointer-events-none absolute -inset-x-10 -top-8 -z-10 h-80"
        />
      )}
      {/* A turn rebuilt when this session was re-opened was never watched
          running, and the server keeps no stage timings — so it says where
          it came from instead of drawing a pipeline nobody observed. */}
      {a.rehydrated ? (
        <p
          className="flex items-center gap-1.5 text-[0.68rem] text-muted-foreground"
          title="Re-opening a session replays what the server kept: this turn's findings, its charts (rebuilt from the frames it stored) and its evidence bundle (projected from its recorded trace). Its stage timings and streamed narrative were never persisted."
        >
          <History className="size-3" />
          Restored from this session&apos;s history
        </p>
      ) : (
        <StageRail
          stages={a.stages}
          streaming={streaming}
          cacheHits={a.cacheHits}
          debug={debug}
        />
      )}

      {a.header && (
        <div className="fade-up">
          <ContextHeader header={a.header} />
        </div>
      )}
      {a.interpretation && <InterpretationPanel interpretation={a.interpretation} />}

      {/* Space is reserved while the pipeline runs — content replaces the
          shimmer in place, never jumping the layout. */}
      {streaming &&
        a.findings.length === 0 &&
        a.charts.length === 0 &&
        a.narrative === "" &&
        !a.definition &&
        !a.clarification && (
          <div aria-hidden className="grid gap-2.5 min-[1450px]:grid-cols-2">
            <div className="skeleton h-28" />
            <div className="skeleton hidden h-28 min-[1450px]:block" />
          </div>
        )}

      {(a.answerGrade || hasGovernedProvenance(a.metric) || a.evidence?.zeroProbeTurn) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {/* A turn that measured nothing governed (definitional, META)
              publishes the block with an empty metric list — no badge,
              because there is no governed contract to point at. */}
          {a.metric && <MetricProvenanceBadge metric={a.metric} />}
          {a.answerGrade && <GradeBadge grade={a.answerGrade} />}
          {a.evidence?.zeroProbeTurn && (
            <span
              className="inline-flex h-5 items-center gap-1 rounded-full border border-verified/40 bg-verified/10 px-2 text-[0.7rem] font-medium text-verified"
              title="Every probe this turn needed was already in the evidence cache from earlier in this session, at this same data load. The warehouse was not queried again — the numbers are not newer than the ones above them."
            >
              <Zap className="size-3" />
              {/* "No new queries" read as a performance boast about the
                  whole answer. What is actually true is narrower and more
                  useful: the results came from cache, at the SAME data
                  load, so nothing here is fresher than what preceded it. */}
              Answered from cached results
              {a.header?.watermark.id ? ` (same data load ${a.header.watermark.id})` : ""} — no new
              warehouse query
            </span>
          )}
          {a.evidence && (
            <Button
              variant="ghost"
              size="xs"
              className="h-5 gap-1 rounded-full px-2 text-[0.68rem] font-normal text-muted-foreground hover:text-foreground"
              onClick={() => openDrawer(turn.id)}
            >
              <FileSearch className="size-3" />
              Evidence
            </Button>
          )}
        </div>
      )}

      {a.evidence && <ReconciliationBanner result={a.evidence.reconciliation} />}

      {a.warnings.map((w) => (
        <div
          key={w.code}
          className={cn(
            "flex items-start gap-2 rounded-md border px-3 py-2 text-[0.72rem] leading-snug",
            w.severity === "caution"
              ? "border-warning/40 bg-warning/10"
              : "border-border bg-surface-sunken/60 text-muted-foreground",
          )}
        >
          {w.severity === "caution" ? (
            <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />
          ) : (
            <Info className="mt-0.5 size-3.5 shrink-0" />
          )}
          <p>
            {/* The §12 code is engine vocabulary — precise, and useless to
                an analyst reading a caution. The sentence carries the same
                meaning; the code stays one debug toggle away. */}
            {debug && (
              <code className="mr-1.5 font-mono text-[0.62rem] text-muted-foreground">
                {w.code}
              </code>
            )}
            {w.message}
          </p>
        </div>
      ))}

      {a.definition && <DefinitionCard definition={a.definition} />}

      {a.findings.length > 0 && (
        <div className="grid gap-2.5 min-[1450px]:grid-cols-2">
          {a.findings.map((finding, i) => (
            <div
              key={finding.referent.value}
              className="fade-up h-full"
              style={{ animationDelay: `${Math.min(i, 6) * 45}ms` }}
            >
              <FindingCard finding={finding} turnId={turn.id} />
            </div>
          ))}
        </div>
      )}

      {emptyResult && <EmptyResult answer={a} chartCount={charts.length} />}

      {charts.map((spec) => (
        <div key={spec.id} className="fade-up">
          <InvestigationChart spec={spec} turnId={turn.id} windowLabel={windowLabel} />
        </div>
      ))}

      {(a.narrative || streaming) && a.status !== "clarification" && (
        <NarrativeText text={a.narrative} streaming={streaming} />
      )}

      {a.clarification && <ClarificationPrompt clarification={a.clarification} />}

      {a.error && (
        <div className="flex items-start gap-2 rounded-md border border-negative/50 bg-negative/10 px-3 py-2 text-[0.75rem]">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-negative" />
          <p>
            <code className="mr-1.5 font-mono text-[0.65rem]">{a.error.code}</code>
            {a.error.message}
          </p>
        </div>
      )}

      {/* Debug mode: how this turn was decided, from the server's own
          recorded trace. Never rendered in the default experience. */}
      {debug && !streaming && <DebugTracePanel turnId={turn.id} answer={a} />}

      {a.status === "complete" && (
        <div className="border-t pt-2">
          <FeedbackTriage turnId={turn.id} />
        </div>
      )}
    </div>
  );
}

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
function EmptyResult({
  answer,
  chartCount,
}: {
  answer: TurnRecord["answer"];
  chartCount: number;
}) {
  const checked: string[] = [];
  if (answer.header) checked.push(formatWindow(answer.header.window));
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
          <p className="text-[0.8rem] font-medium leading-snug">
            No findings for this question — here&apos;s what was checked
          </p>
          {checked.length > 0 ? (
            <ul className="space-y-0.5 text-[0.7rem] leading-snug text-muted-foreground">
              {checked.map((line) => (
                <li key={line} className="flex gap-1.5">
                  <span aria-hidden>·</span>
                  {line}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[0.7rem] leading-snug text-muted-foreground">
              This turn came back with no window, no measure and no record of what ran — so
              there is nothing further this card can honestly say about it.
            </p>
          )}
          <p className="text-[0.7rem] leading-snug text-muted-foreground">
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
