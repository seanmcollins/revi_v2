"use client";

import { AnswerChart } from "@/components/answer/AnswerChart";
import { AnswerTrustRow } from "@/components/answer/AnswerTrustRow";
import { ContextHeader } from "@/components/answer/ContextHeader";
import { EmptyResult } from "@/components/answer/EmptyResult";
import { FoldNote, RestoredWithoutProse } from "@/components/answer/AnswerBodyCurrent";
import { InterpretationPanel } from "@/components/answer/InterpretationPanel";
import { NarrativeText } from "@/components/answer/NarrativeText";
import { ThingsToKnowGroup } from "@/components/answer/ThingsToKnow";
import type { AnswerModel } from "@/components/answer/useAnswerModel";
import { VerdictLead } from "@/components/answer/VerdictLead";
import { AnomalyReconciliationStrip } from "@/components/banners/AnomalyReconciliationStrip";
import { ReconciliationBanner } from "@/components/banners/ReconciliationBanner";
import { DefinitionCard } from "@/components/definitional/DefinitionCard";
import { FactList } from "@/components/findings/FactRow";
import { FindingCard } from "@/components/findings/FindingCard";
import type { TurnRecord } from "@/lib/store";

/**
 * VARIANT A — "the refined current".
 *
 * The conservative half of the A/B. It keeps the present anatomy — chips,
 * cards, charts, all on the answer — and changes four things, each aimed
 * at one measured complaint:
 *
 *   the VERDICT leads, in prose. It is the answer to the question that
 *     was asked and it was arriving eighth in a stack of amber.
 *   the WRITE-UP is above the findings. "One answer, one voice": the
 *     sentence that says what happened should not be below three screens
 *     of the cards it is about.
 *   every other caution is ONE expandable group. Eight banners become
 *     "8 things to know"; nothing is dropped and the group says how many
 *     of them change how a number reads.
 *   the findings are ROWS, with the hero number kept for F1 only. One
 *     display figure per answer is a hierarchy; eight is a wall.
 */
export function AnswerBodyDetailed({
  turn,
  model,
  debug,
}: {
  turn: TurnRecord;
  model: AnswerModel;
  debug: boolean;
}) {
  const a = turn.answer;
  // The hero keeps its card; everything after it is a row. Bounded cells
  // are never the hero — a ceiling is not the number an answer leads on.
  const [hero, ...rest] = model.measuredFindings;

  return (
    <>
      {a.header && (
        <div className="fade-up">
          <ContextHeader header={a.header} suppressRestored={a.rehydrated === true} />
        </div>
      )}
      {a.interpretation && <InterpretationPanel interpretation={a.interpretation} />}

      {model.streaming &&
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

      <AnswerTrustRow turn={turn} model={model} />

      {a.evidence && <ReconciliationBanner result={a.evidence.reconciliation} />}
      <AnomalyReconciliationStrip reconciliation={a.anomalyReconciliation} />

      {/* THE VERDICT, first and in prose. Never collapsed, never behind a
          disclosure — see `VerdictLead`. */}
      <VerdictLead verdicts={model.verdicts} debug={debug} />

      {a.definition && <DefinitionCard definition={a.definition} />}

      {/* THE WRITE-UP, above the findings it is about. */}
      {(a.narrative || model.streaming) && a.status !== "clarification" && (
        <>
          <NarrativeText
            text={model.streaming ? a.narrative : model.prose.text}
            streaming={model.streaming}
          />
          {!model.streaming && model.prose.folded > 0 && <FoldNote folded={model.prose.folded} />}
        </>
      )}

      {a.rehydrated &&
        !model.streaming &&
        a.narrative.trim() === "" &&
        a.findings.length > 0 && (
          <RestoredWithoutProse
            charts={model.charts.length}
            hasEvidence={a.evidence !== undefined}
          />
        )}

      {/* Everything that is not the verdict, in one group under the
          writing. Eight amber banners, stated once, counted honestly. */}
      <ThingsToKnowGroup warnings={model.thingsToKnow} />

      {model.emptyResult && <EmptyResult answer={a} chartCount={model.charts.length} />}

      {hero && (
        <div className="fade-up">
          <FindingCard finding={hero} turnId={turn.id} />
        </div>
      )}

      {(rest.length > 0 || model.boundedFindings.length > 0) && (
        <section aria-label="Findings" className="rounded-lg border bg-card/60 px-2 py-1.5">
          <FactList
            measured={rest}
            bounded={model.boundedFindings}
            turnId={turn.id}
            totalFindings={a.findings.length}
          />
        </section>
      )}

      {model.charts.map((spec) => (
        <div key={spec.id} className="fade-up">
          <AnswerChart turn={turn} model={model} spec={spec} />
        </div>
      ))}
    </>
  );
}
