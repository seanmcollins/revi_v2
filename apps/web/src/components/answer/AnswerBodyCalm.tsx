"use client";

import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";

import { AnswerChart } from "@/components/answer/AnswerChart";
import { CopyTextButton } from "@/components/answer/AnswerActions";
import { ContextLine } from "@/components/answer/ContextLine";
import { EmptyResult } from "@/components/answer/EmptyResult";
import { IntegrityLine } from "@/components/answer/IntegrityLine";
import { InterpretationPanel } from "@/components/answer/InterpretationPanel";
import { NarrativeText } from "@/components/answer/NarrativeText";
import { FoldNote, RestoredWithoutProse } from "@/components/answer/AnswerBodyCurrent";
import { verificationClause, type AnswerModel } from "@/components/answer/useAnswerModel";
import { VerdictLead } from "@/components/answer/VerdictLead";
import { AnomalyReconciliationStrip } from "@/components/banners/AnomalyReconciliationStrip";
import { ReconciliationBanner } from "@/components/banners/ReconciliationBanner";
import { DefinitionCard } from "@/components/definitional/DefinitionCard";
import { FactList } from "@/components/findings/FactRow";
import { answerToText } from "@/lib/export";
import { useSessionStore, type TurnRecord } from "@/lib/store";

/**
 * VARIANT B — "the calm answer". The bet.
 *
 * The owner's sketch, built: "I would love the summary to be in the main
 * display and maybe the facts pop up in Evidence… we're somewhere between
 * Windows and Linux and I want us to be iOS."
 *
 * The main display is the ANSWER — a short piece of writing a VP could
 * read aloud — and nothing else:
 *
 *   ONE context line, set as a sentence, that opens the whole header.
 *   The VERDICT as a calm lead-in, because a premise correction or a
 *     refused ranking IS the answer and belongs in the first paragraph.
 *   The NARRATIVE at reading size, with its referent citations as chips
 *     that open the Evidence rail on the fact they cite.
 *   ONE chart — the engine's own primary frame — and only when the turn
 *     published one worth drawing.
 *   The INTEGRITY LINE, the signature: what was verified, how many things
 *     there are to know, how many checks ran. Every count real, every
 *     count a way in.
 *
 * Everything else is one tap away and nothing is gone: the facts and the
 * other charts are in the rail, the caveats are in the sheet the line
 * opens, and the export is byte-for-byte the export the other layouts
 * produce.
 *
 * The review round that chose this layout attached one condition above
 * all the others, and it is the reason the integrity line moves: the
 * caveat count — "12 things to know · 10 change how a number here should
 * be read" — has to be on the first screen on EVERY path. On the prose
 * path it is, under a paragraph. On the `factsInline` path (every
 * restored turn, and `inv_534567aee34a` publishes thirty findings) it was
 * landing under the whole fact list, which is the one place this layout
 * was quieter than it was honest. So on that path the line is hoisted
 * above the rows, where variant A's group already sat.
 */
export function AnswerBodyCalm({
  turn,
  model,
  debug,
  worklist,
}: {
  turn: TurnRecord;
  model: AnswerModel;
  debug: boolean;
  /**
   * The ranked worklist, handed in by the shell.
   *
   * It renders INSIDE this body, above the integrity line, because the
   * line is this layout's closing signature and a signature followed by
   * thirty-three ranked cards is not a signature. The other two layouts
   * keep it in the shell, below the body, where their own anatomy ends.
   */
  worklist?: ReactNode;
}) {
  const openDrawer = useSessionStore((s) => s.openDrawer);
  const a = turn.answer;

  // When the writing is not there, the facts ARE the answer — and the
  // Evidence rail stands its own facts section down so the same rows are
  // not printed twice on one screen. See `AnswerModel.factsInline`.
  const factsInline = model.factsInline;

  /**
   * THE SIGNATURE, and where it sits.
   *
   * Under the writing, normally: what was verified, how many things there
   * are to know, how many checks ran. But on the `factsInline` path —
   * every restored turn, which is the first thing anyone does each
   * morning — there is no writing, and the line was landing under the
   * whole fact list: three rows on a worklist turn, THIRTY on
   * `inv_534567aee34a`. The one honesty affordance on the answer was
   * below the fold on the one path that has no prose to justify it.
   *
   * So on that path it is hoisted directly under the restored note, above
   * the facts, where variant A's group already sat. Same element, same
   * counts, same sheet — a different position on the one layout where the
   * bottom of the page is not the bottom of the answer.
   */
  const integrityLine = (
    <IntegrityLine
      verification={verificationClause(a)}
      thingsToKnow={model.thingsToKnow}
      checks={model.checks}
      turnId={turn.id}
      // Anything at all for this turn in the rail: the bundle, the facts,
      // the supporting figures. A zero-probe, zero-finding turn used to
      // offer no way in at all.
      hasEvidence={
        a.evidence !== undefined || a.findings.length > 0 || model.secondaryCharts.length > 0
      }
      trailing={
        model.copyable ? (
          <CopyTextButton
            label="Copy answer"
            title="Copy this answer as text — findings, the written analysis, every caveat Revi attached, and a line naming the data load and the definitions it was measured against. Nothing leaves this browser."
            className="h-5 px-2 text-micro"
            text={() =>
              answerToText({
                ...(turn.submission.utterance ? { question: turn.submission.utterance } : {}),
                ...(a.header ? { header: a.header } : {}),
                findings: a.findings,
                narrative: model.prose.text,
                warnings: a.warnings,
                ...(model.charts.length > 0 ? { charts: model.charts } : {}),
                ...(a.worklist ? { worklist: a.worklist } : {}),
                ...(a.metric ? { metric: a.metric } : {}),
                ...(a.investigationId ? { investigationId: a.investigationId } : {}),
                ...(a.rehydrated ? { restored: true } : {}),
                ...(a.header?.restorationNotes
                  ? { restorationNotes: a.header.restorationNotes }
                  : {}),
              })
            }
          />
        ) : undefined
      }
    />
  );

  return (
    <>
      {a.header && (
        <ContextLine
          header={a.header}
          windowAssumed={a.warnings.some((w) => w.code === "WINDOW_ASSUMED")}
          className="fade-up"
        />
      )}
      {a.interpretation && <InterpretationPanel interpretation={a.interpretation} />}

      {model.streaming && a.narrative === "" && !a.definition && !a.clarification && (
        <div aria-hidden className="space-y-2">
          <div className="skeleton h-4 w-full" />
          <div className="skeleton h-4 w-11/12" />
          <div className="skeleton h-4 w-4/5" />
        </div>
      )}

      {/* The two reconciliation surfaces are verdicts about the numbers
          themselves — "the parts don't add up", "this answer differs from
          the card that opened it". They stay on the answer. */}
      {a.evidence && <ReconciliationBanner result={a.evidence.reconciliation} />}
      <AnomalyReconciliationStrip reconciliation={a.anomalyReconciliation} />

      {/* THE VERDICT — calm lead-in prose, never an amber box, never
          collapsed. It is the answer to the question that was asked, so
          it is set on the same measure as the writing it leads into: two
          paragraphs of one voice, not a notice above a paragraph. */}
      <VerdictLead verdicts={model.verdicts} debug={debug} className="max-w-[64ch]" />

      {a.definition && <DefinitionCard definition={a.definition} />}

      {(a.narrative || model.streaming) && a.status !== "clarification" && (
        <div className="max-w-[64ch]">
          <NarrativeText
            text={model.streaming ? a.narrative : model.prose.text}
            streaming={model.streaming}
            size="lead"
          />
          {!model.streaming && model.prose.folded > 0 && <FoldNote folded={model.prose.folded} />}
        </div>
      )}

      {factsInline && (
        <>
          {/* ONE note, one component. This layout carried its own copy of
              the sentence, so the two drifted the moment the other one
              learned to count what actually restored — and this is the
              default surface, which is the one that was understating the
              answer on a page a buyer forwards. */}
          {a.rehydrated && (
            <RestoredWithoutProse
              charts={model.charts.length}
              hasEvidence={a.evidence !== undefined}
            />
          )}
          {/* HOISTED, above the rows. The caveat count is on the first
              screen on this path too, which is the condition this layout
              ships under. */}
          {integrityLine}
          <section
            aria-label="Findings"
            className="rounded-lg border bg-card/60 px-2 py-1.5"
          >
            <FactList
              measured={model.measuredFindings}
              bounded={model.boundedFindings}
              turnId={turn.id}
              totalFindings={a.findings.length}
              verdictBodies={model.verdictBodies}
            />
          </section>
        </>
      )}

      {model.emptyResult && <EmptyResult answer={a} chartCount={model.charts.length} />}

      {/* ONE figure: the engine's own primary frame. The rest are in the
          rail behind "N more charts" — see `selectPrimaryChart`. */}
      {model.primaryChart && (
        <div className="fade-up">
          <AnswerChart turn={turn} model={model} spec={model.primaryChart} />
        </div>
      )}

      {/* The link from the writing to the working, always present when
          there are facts to reach — so a reader who never hovers a
          citation, and a screen reader that never sees one, still has a
          named path to every finding on this turn. */}
      {!factsInline && a.findings.length > 0 && (
        <button
          type="button"
          onClick={() => openDrawer(turn.id)}
          className="focus-ring group inline-flex items-center gap-1 self-start rounded text-meta text-muted-foreground transition-colors duration-150 hover:text-foreground"
        >
          {a.findings.length} fact{a.findings.length === 1 ? "" : "s"} behind this answer
          {model.secondaryCharts.length > 0 && (
            <span>
              {" "}
              · {model.secondaryCharts.length} more chart
              {model.secondaryCharts.length === 1 ? "" : "s"}
            </span>
          )}
          <ArrowRight
            aria-hidden
            className="size-3 transition-transform duration-150 group-hover:translate-x-0.5"
          />
        </button>
      )}

      {/* The ranked work, INSIDE the answer and above its signature. */}
      {worklist}

      {/* THE SIGNATURE — unless it was hoisted above the facts, in which
          case it has already been said, and saying it twice would make
          the one line whose counts must be exact into two. */}
      {!factsInline && integrityLine}
    </>
  );
}
