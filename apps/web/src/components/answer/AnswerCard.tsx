"use client";

import { AlertTriangle, FileSearch, Info, Zap } from "lucide-react";

import { ContextHeader } from "@/components/answer/ContextHeader";
import { GradeBadge } from "@/components/answer/GradeBadge";
import { InterpretationPanel } from "@/components/answer/InterpretationPanel";
import { MetricProvenanceBadge } from "@/components/answer/MetricProvenanceBadge";
import { NarrativeText } from "@/components/answer/NarrativeText";
import { ReconciliationBanner } from "@/components/banners/ReconciliationBanner";
import { InvestigationChart } from "@/components/charts/InvestigationChart";
import { StageRail } from "@/components/chat/StageRail";
import { ClarificationPrompt } from "@/components/clarification/ClarificationPrompt";
import { DefinitionCard } from "@/components/definitional/DefinitionCard";
import { FeedbackTriage } from "@/components/feedback/FeedbackTriage";
import { FindingCard } from "@/components/findings/FindingCard";
import { Button } from "@/components/ui/button";
import { useSessionStore, type TurnRecord } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * The composed answer: stage rail → context header (always) →
 * interpretation strip → trust badges → findings → charts → narrative →
 * warnings → feedback. Nothing renders a number without its context.
 */
export function AnswerCard({ turn, active = false }: { turn: TurnRecord; active?: boolean }) {
  const openDrawer = useSessionStore((s) => s.openDrawer);
  const streaming = turn.answer.status === "streaming";
  const a = turn.answer;

  return (
    <div className={cn("space-y-3", active && "relative isolate")}>
      {/* Depth model: a faint accent glow marks the answer being read. */}
      {active && (
        <div
          aria-hidden
          className="answer-glow pointer-events-none absolute -inset-x-10 -top-8 -z-10 h-80"
        />
      )}
      <StageRail stages={a.stages} streaming={streaming} cacheHits={a.cacheHits} />

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

      {(a.answerGrade || a.metric || a.evidence?.zeroProbeTurn) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {a.metric && (
            <MetricProvenanceBadge metric={a.metric} packVersion={a.header?.packVersion} />
          )}
          {a.answerGrade && <GradeBadge grade={a.answerGrade} />}
          {a.evidence?.zeroProbeTurn && (
            <span className="inline-flex h-5 items-center gap-1 rounded-full border border-verified/40 bg-verified/10 px-2 text-[0.7rem] font-medium text-verified">
              <Zap className="size-3" />
              0 warehouse queries
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
            <code className="mr-1.5 font-mono text-[0.62rem] text-muted-foreground">{w.code}</code>
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

      {a.charts.map((spec) => (
        <div key={spec.id} className="fade-up">
          <InvestigationChart spec={spec} turnId={turn.id} />
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

      {a.status === "complete" && (
        <div className="border-t pt-2">
          <FeedbackTriage turnId={turn.id} />
        </div>
      )}
    </div>
  );
}
