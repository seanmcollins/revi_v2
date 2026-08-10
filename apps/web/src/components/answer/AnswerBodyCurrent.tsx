"use client";

import { AnswerChart } from "@/components/answer/AnswerChart";
import { AnswerTrustRow } from "@/components/answer/AnswerTrustRow";
import { ContextHeader } from "@/components/answer/ContextHeader";
import { EmptyResult } from "@/components/answer/EmptyResult";
import { InterpretationPanel } from "@/components/answer/InterpretationPanel";
import { NarrativeText } from "@/components/answer/NarrativeText";
import type { AnswerModel } from "@/components/answer/useAnswerModel";
import { AnomalyReconciliationStrip } from "@/components/banners/AnomalyReconciliationStrip";
import { ReconciliationBanner } from "@/components/banners/ReconciliationBanner";
import { WarningList } from "@/components/banners/WarningBanner";
import { DefinitionCard } from "@/components/definitional/DefinitionCard";
import { FindingCard } from "@/components/findings/FindingCard";
import type { TurnRecord } from "@/lib/store";

/**
 * THE CURRENT LAYOUT — unchanged, and the default.
 *
 * Context chips → interpretation → trust badges → every warning as its
 * own banner → findings as cards → charts → the write-up. It is the
 * layout the A/B is judged against, so it is preserved exactly as it
 * stands apart from the eight bug-list fixes, which apply to all three
 * (a leaked plan-node id and a clipped axis label are defects in every
 * layout, not features of this one).
 */
export function AnswerBodyCurrent({
  turn,
  model,
  debug,
}: {
  turn: TurnRecord;
  model: AnswerModel;
  debug: boolean;
}) {
  const a = turn.answer;

  return (
    <>
      {a.header && (
        <div className="fade-up">
          {/* BUG 3 — the restored marker is stated once. The shell draws
              the line above; this chip is the same fact, so it stands
              down when the line is there. */}
          <ContextHeader header={a.header} suppressRestored={a.rehydrated === true} />
        </div>
      )}
      {a.interpretation && <InterpretationPanel interpretation={a.interpretation} />}

      {/* Space is reserved while the pipeline runs — content replaces the
          shimmer in place, never jumping the layout. */}
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

      {/* This turn opened a portfolio card: the card's figure and the
          answer's, side by side, with the verdict. */}
      <AnomalyReconciliationStrip reconciliation={a.anomalyReconciliation} />

      {/* Code-driven, not prose-parsed: severity decides the treatment,
          the code decides the title, and identical warnings collapse with
          a count. See `WarningBanner`. */}
      <WarningList warnings={model.warnings} debug={debug} />

      {a.definition && <DefinitionCard definition={a.definition} />}

      {/* MEASURED findings, then CEILINGS, in two blocks. A ceiling has no
          position in an order it was never measured for. */}
      {model.measuredFindings.length > 0 && (
        <div className="grid gap-2.5 min-[1450px]:grid-cols-2">
          {model.measuredFindings.map((finding, i) => (
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

      {model.boundedFindings.length > 0 && (
        <section aria-label="Upper bounds" className="space-y-2">
          <p className="text-meta leading-snug text-muted-foreground">
            <span className="font-medium text-foreground">
              Upper bounds — not ranked
              {model.measuredFindings.length > 0
                ? ` (${model.boundedFindings.length} of ${a.findings.length})`
                : ""}
            </span>
            <span className="ml-1">
              — the numerator was suppressed on{" "}
              {model.boundedFindings.length === 1 ? "this cell" : "these cells"} and a ceiling over
              the publishable population was published instead. A ceiling has no position in an
              order it was never measured for.
            </span>
          </p>
          <div className="grid gap-2.5 min-[1450px]:grid-cols-2">
            {model.boundedFindings.map((finding, i) => (
              <div
                key={finding.referent.value}
                className="fade-up h-full"
                style={{ animationDelay: `${Math.min(i, 6) * 45}ms` }}
              >
                <FindingCard finding={finding} turnId={turn.id} />
              </div>
            ))}
          </div>
        </section>
      )}

      {model.emptyResult && <EmptyResult answer={a} chartCount={model.charts.length} />}

      {model.charts.map((spec) => (
        <div key={spec.id} className="fade-up">
          <AnswerChart turn={turn} model={model} spec={spec} />
        </div>
      ))}

      {(a.narrative || model.streaming) && a.status !== "clarification" && (
        <>
          {/* While it streams, the prose is whatever has arrived so far —
              folding a half-written sentence against a banner would take
              text out from under a caret mid-word. */}
          <NarrativeText
            text={model.streaming ? a.narrative : model.prose.text}
            streaming={model.streaming}
          />
          {!model.streaming && model.prose.folded > 0 && <FoldNote folded={model.prose.folded} />}
        </>
      )}

      {/* A restored turn with findings and no prose. The write-up is
          composed at answer time and, on the payload generations that do
          not persist it, is simply not among the things the server kept. */}
      {a.rehydrated &&
        !model.streaming &&
        a.narrative.trim() === "" &&
        a.findings.length > 0 && <RestoredWithoutProse />}
    </>
  );
}

/**
 * What the write-up repeated, and where the repeated sentence lives.
 *
 * Deliberately says "this answer carries" rather than "above": the
 * cautions are above the writing in two layouts and behind the integrity
 * line in the third, and a note that names a position would be wrong on
 * whichever layout it was not written for.
 */
export function FoldNote({ folded }: { folded: number }) {
  return (
    <p className="text-micro leading-snug text-muted-foreground">
      {folded === 1 ? "One sentence" : `${folded} sentences`} of this write-up repeated{" "}
      {folded === 1 ? "a caution" : "cautions"} this answer already carries, word for word, and{" "}
      {folded === 1 ? "is" : "are"} not printed twice. Every caveat is stated in full, and travels
      with the copied answer and the CSV.
    </p>
  );
}

/**
 * A restored turn with findings and no write-up.
 *
 * Says "these are the findings" and not "the findings above": this note
 * renders ABOVE the findings in the detailed layout and below them in the
 * current one, and a sentence that names a direction is wrong on
 * whichever layout it was not written for. Live, in variant A, it pointed
 * a reader up at a chip row and away from the cards underneath it — the
 * same defect as the fold note beside it, which is why that one has said
 * "this answer carries" since the day it was written.
 */
export function RestoredWithoutProse() {
  return (
    <p className="rounded-md border border-dashed bg-card/60 px-3 py-2 text-meta leading-snug text-muted-foreground">
      The written analysis was not stored for this turn — these are the findings the server
      kept, and the context they were measured under.
    </p>
  );
}
