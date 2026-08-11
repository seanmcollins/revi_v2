"use client";

import { ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";

import { CopyTextButton, DownloadCsvButton } from "@/components/answer/AnswerActions";
import { NarrativeText } from "@/components/answer/NarrativeText";
import { ThingsToKnowGroup } from "@/components/answer/ThingsToKnow";
import { InvestigationChart } from "@/components/charts/InvestigationChart";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { mapChartSpec } from "@/lib/contract";
import {
  confidenceLabel,
  decimal,
  measuredFigures,
  type ResearchFigure,
  type ResearchReading,
  type ResearchStudy,
  type ResearchStudyRound,
} from "@/lib/deepResearch";
import { caveatLines, researchStudyToCsv } from "@/lib/export";
import { formatCount, formatPct, mediumDate } from "@/lib/format";
import { researchLinkFor } from "@/lib/links";
import type { ChartSpec, WarningEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * A RESEARCH STUDY, READ THE WAY A CONSULTANT HANDS ONE OVER.
 *
 * The recoverability review's report opens on a priced headline because
 * the question it answers IS a dollar figure. A study's question is not:
 * "why has our A/R over 90 been climbing and what will it take to bring it
 * down" has no total that answers it, and a figure standing where the
 * answer should be would be answering something nobody asked. So the
 * DETERMINATION stands there instead, at display size, and everything
 * below it is the working that supports it.
 *
 * THE ORDER IS THE READING ORDER, AND EACH POSITION IS ARGUED FOR.
 *
 *   THE DETERMINATION FIRST, with its disclosures composed in front of it
 *     by the server's own composer — so a reader who stops after one
 *     paragraph has the answer and its limits, never the answer alone.
 *   THE CAVEATS AS A COUNT, immediately under it. One line that opens,
 *     exactly as on an answer.
 *   THE READINGS, each as the figure it published with the REASON it was
 *     taken above it and what it SETTLED beside it. A research report that
 *     showed tables without either would be a data dump wearing a report's
 *     clothes, and the reasons are the half a reader cannot reconstruct.
 *   HOW REVI GOT HERE — the walk, quiet and collapsed. It is the "how I
 *     got there" a consultant shows when asked, which is exactly the
 *     register a fold is for: present, complete, and not spent on a reader
 *     who trusts the answer.
 *   WHAT IT ESTABLISHED BEFORE IT CHOSE, and what it read first. The path
 *     choices and the background notes are disclosures about METHOD rather
 *     than about the figures, so they sit under the working rather than
 *     over it.
 *   WHAT IS COUNTED AND WHAT IS NOT, where outcome-like data was involved.
 *
 * EVERY FIGURE GOES THROUGH `mapChartSpec` AND `InvestigationChart`. Not a
 * bespoke widget: full screen, "view as" and the CSV are the same controls
 * they are on any answer, and the unit rescaling, ordering and annotation
 * rules are the ones the whole product obeys rather than a second set that
 * would drift from them.
 *
 * WHAT IS DELIBERATELY ABSENT: "Monitor this" on these figures, for the
 * reason the review's report gives — a run's stored analysis carries no
 * measures, so the pin would be refused, and a control that cannot do what
 * its label promises is worse than no control.
 */
export function ResearchStudyView({
  study,
  runId,
  className,
}: {
  study: ResearchStudy;
  runId: string;
  className?: string;
}) {
  const warnings = useMemo<WarningEvent[]>(
    () =>
      (study.warnings ?? []).map((warning) => ({
        type: "warning" as const,
        code: warning.code,
        message: warning.message,
        severity: warning.severity,
        structured: true,
        ...(warning.count !== undefined ? { count: warning.count } : {}),
      })),
    [study.warnings],
  );
  const caveats = useMemo(() => caveatLines(warnings), [warnings]);
  const charts = useStudyCharts(study);
  const readings = study.readings ?? [];
  const rounds = study.walk?.rounds ?? [];

  return (
    <article
      data-research-study={runId}
      aria-labelledby="research-study-heading"
      className={cn("space-y-8", className)}
    >
      <header className="space-y-1">
        <p className="text-micro font-semibold uppercase tracking-widest text-muted-foreground">
          Deep research
        </p>
        <h2 id="research-study-heading" className="text-lead font-medium leading-snug">
          {study.research_question}
        </h2>
        <p className="num text-micro text-muted-foreground">
          {study.population_label !== "" ? study.population_label : "your data"}
          {study.window_label !== "" && ` · ${study.window_label}`}
          {study.data_load_label !== "" && ` · ${study.data_load_label}`}
        </p>
      </header>

      <Determination study={study} />

      {/* The same fold an answer uses. Stacked as boxes these would be the
          loudest thing between the determination and the working. */}
      <ThingsToKnowGroup warnings={warnings} />

      {readings.length > 0 && (
        <section aria-labelledby="research-readings-heading" className="space-y-7">
          <h3
            id="research-readings-heading"
            className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
          >
            What it read
          </h3>
          {readings.map((reading) => (
            <ReadingBlock
              key={reading.id}
              reading={reading}
              spec={charts.get(reading.chart_id)}
              study={study}
              runId={runId}
              caveats={caveats}
            />
          ))}
        </section>
      )}

      {rounds.length > 0 && <HowItGotHere study={study} />}

      <MethodNotes study={study} />

      <CountedAndNot study={study} />

      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t pt-3">
        <DownloadCsvButton
          label="Download this study"
          title="Save every figure this study is built from — each reading with the reason it was taken, every group it published, and the ceilings and withheld rows marked as what they are — under the same caveats the screen carries."
          filenameKind="deep-research"
          filenameTag={study.research_question}
          csv={() => researchStudyToCsv(study, { runId })}
        />
        <CopyTextButton
          label="Copy this study's link"
          doneLabel="Link copied"
          title="Copy a link to this study. It opens the finished study, not a snapshot of this screen."
          text={() =>
            researchLinkFor(runId, typeof window === "undefined" ? "" : window.location.origin)
          }
        />
        <p className="num text-micro text-muted-foreground">
          The file carries every group, including the ones no figure could be published for.
        </p>
      </footer>
    </article>
  );
}

/* ------------------------------------------------------------------ */
/* The determination                                                   */
/* ------------------------------------------------------------------ */

/**
 * THE ANSWER, IN THE PRONOUNCED TREATMENT.
 *
 * `NarrativeText` at lead size with its referent citations live — the same
 * component and the same size the review's write-up gets, because it is
 * the same kind of object: server-composed prose whose every figure was
 * checked against a value an estimator produced.
 *
 * A DETERMINATION NOBODY COMPOSED SAYS SO. `composed: false` means the
 * model produced nothing publishable and what is on screen is the
 * disclosures alone. That is an honest outcome and a DIFFERENT one, and
 * presenting it as the study's answer would be this surface asserting that
 * a question was answered when it was not.
 */
function Determination({ study }: { study: ResearchStudy }) {
  const determination = study.determination;
  const text = determination?.statement ?? "";
  if (text === "") return null;
  return (
    <section
      data-research-determination={determination?.composed ? "composed" : "disclosures-only"}
      aria-labelledby="research-determination-heading"
      className="space-y-2"
    >
      <h3
        id="research-determination-heading"
        className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
      >
        What Revi determined
      </h3>
      <div className="max-w-[68ch]">
        <NarrativeText text={text} size="lead" />
      </div>
      {determination?.composed === false && (
        <p className="max-w-[68ch] text-meta leading-snug text-muted-foreground">
          Revi could not write this up. What is above is the study&apos;s own disclosures; every
          reading it took is below, with what each one settled.
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* One reading                                                         */
/* ------------------------------------------------------------------ */

/**
 * ONE READING: WHY IT WAS TAKEN, THE FIGURE, AND WHAT IT SETTLED.
 *
 * The reason sits ABOVE the figure and the verdict BELOW it, and that
 * order is the argument: a reader arriving at a chart wants to know why
 * they are looking at it before they look, and what it means after. Both
 * sentences are the server's own — one written by whatever chose the
 * reading, one composed from the figures it published — and neither is
 * re-worded here.
 *
 * A CHASE SAYS WHAT IT IS CHASING. A reading chosen in a later round
 * because an earlier one separated is a different kind of object from an
 * opening read, and the payload records the relation rather than leaving
 * it to be inferred from a round number.
 *
 * A REFUSED READING KEEPS ITS PLACE. It was tried, and a reader who cannot
 * see that it was tried cannot tell the difference between a question this
 * study declined to ask and one it could not answer.
 */
function ReadingBlock({
  reading,
  spec,
  study,
  runId,
  caveats,
}: {
  reading: ResearchReading;
  spec?: ChartSpec;
  study: ResearchStudy;
  runId: string;
  caveats: readonly string[];
}) {
  const measured = measuredFigures(reading);
  const marked = (reading.figures ?? []).filter(
    (figure) => figure.evidence !== "measured",
  );

  return (
    <div data-research-reading={reading.id} data-reading-round={reading.round} className="space-y-2">
      <div className="max-w-[70ch] space-y-0.5">
        <h4 className="text-body font-medium leading-snug">{reading.title}</h4>
        {reading.reason !== "" && (
          <p className="text-meta leading-snug text-muted-foreground">{reading.reason}</p>
        )}
        {reading.chases !== "" && (
          <p className="text-micro leading-snug text-muted-foreground">
            Chasing what {reading.chases} turned up.
          </p>
        )}
      </div>

      {reading.refusal !== "" ? (
        <p className="max-w-[70ch] text-meta leading-snug text-foreground/85">
          {reading.refusal}
        </p>
      ) : (
        <>
          {spec ? (
            <InvestigationChart
              spec={spec}
              turnId={runId}
              windowLabel={study.data_load_label}
              question={study.research_question}
              caveats={caveats}
            />
          ) : (
            <FigureList figures={measured} />
          )}
          {reading.settled !== "" && (
            <p className="max-w-[70ch] text-body leading-snug">{reading.settled}</p>
          )}
          {/* THE MARKS, WHERE THE FIGURE COULD NOT BE DRAWN WITH THEM. A
              ceiling never enters a chart — it would draw as a bar the same
              height as a measurement — so the rows that carry one are named
              here with the mark on them. Dropping them would make the
              chart's population look complete. */}
          {marked.length > 0 && (
            <p className="num max-w-[70ch] text-meta leading-snug text-muted-foreground">
              {marked
                .map((figure) => `${figure.label}: ${figure.display}`)
                .join(" · ")}
            </p>
          )}
          {reading.ranking_refused !== "" && (
            <p className="max-w-[70ch] text-meta leading-snug text-muted-foreground">
              {reading.ranking_refused}
            </p>
          )}
          {reading.contrast && reading.contrast.implication !== "" && (
            <p className="max-w-[70ch] text-body leading-snug">
              {reading.contrast.test === "refused"
                ? (reading.contrast.refusal_reason ?? reading.contrast.implication)
                : reading.contrast.implication}
            </p>
          )}
          {(reading.notes ?? []).map((note) => (
            <p key={note} className="max-w-[70ch] text-meta leading-snug text-muted-foreground">
              {note}
            </p>
          ))}
        </>
      )}

      {/* THE PROVENANCE, QUIET AND ALWAYS THERE. Which date it was measured
          on and which period — the two facts that decide whether two
          readings on this page are even comparable. */}
      <p className="num text-micro leading-snug text-muted-foreground">
        {reading.measure_label}
        {reading.basis_label !== "" && `, measured ${reading.basis_label}`}
        {reading.window_label !== "" && `, over ${reading.window_label}`}
        {reading.figures_withheld > 0 &&
          ` · ${formatCount(reading.figures_withheld)} group${
            reading.figures_withheld === 1 ? "" : "s"
          } not published`}
      </p>
    </div>
  );
}

/**
 * The figures, where there is no chart to draw them.
 *
 * Two marks are not a chart and one is a figure, so a reading that
 * published fewer than two measurements gets its numbers as text rather
 * than a bar chart with one bar in it.
 */
function FigureList({ figures }: { figures: readonly ResearchFigure[] }) {
  if (figures.length === 0) return null;
  return (
    <ul className="space-y-0.5">
      {figures.map((figure) => {
        const low = decimal(figure.interval?.low);
        const high = decimal(figure.interval?.high);
        return (
          <li key={figure.label} className="num text-meta leading-snug">
            <span className="text-muted-foreground">{figure.label}: </span>
            <span className="font-medium">{figure.display}</span>
            {low !== undefined && high !== undefined && (
              <span className="text-muted-foreground">
                {" "}
                · {formatPct(low)}–{formatPct(high)}
                {figure.interval?.confidence !== undefined &&
                  ` (${confidenceLabel(figure.interval.confidence)})`}
              </span>
            )}
            {figure.population !== undefined && figure.population !== null && (
              <span className="text-muted-foreground">
                {" "}
                · over {formatCount(figure.population)}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/* ------------------------------------------------------------------ */
/* The walk                                                            */
/* ------------------------------------------------------------------ */

/**
 * HOW REVI GOT HERE — collapsed, complete, and quiet.
 *
 * This is the record of what the run decided and why: which readings it
 * opened with, what separated, what it therefore chased, what it dropped
 * and under which rule. It is the single most convincing thing in the
 * artifact for a reader who wants to argue with it, and the single most
 * skippable for one who does not — which is exactly what a fold is for.
 *
 * IT IS NOT A WARNING REGISTER. Nothing in here is a verdict, a refusal or
 * a correction; the amber register is reserved for those. A drop is a
 * decision with a stated rule, and it reads as one.
 *
 * WHO CHOSE THE READINGS is stated, because a fallback presented as a
 * choice is a small lie about how the analysis was decided.
 */
function HowItGotHere({ study }: { study: ResearchStudy }) {
  const [open, setOpen] = useState(false);
  const walk = study.walk;
  const rounds = walk?.rounds ?? [];
  const taken = walk?.rounds_taken ?? 1;

  return (
    <Collapsible open={open} onOpenChange={setOpen}>
      <section
        data-research-walk={open ? "open" : "closed"}
        aria-labelledby="research-walk-heading"
        className="rounded-lg border bg-surface-sunken/40"
      >
        <CollapsibleTrigger className="focus-ring flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left">
          <ChevronRight
            aria-hidden
            className={cn("size-3 shrink-0 text-muted-foreground transition-transform", open && "rotate-90")}
          />
          <span className="min-w-0 flex-1">
            <span
              id="research-walk-heading"
              className="block text-micro font-semibold uppercase tracking-widest text-muted-foreground"
            >
              How Revi got here
            </span>
            <span className="num block text-meta leading-snug text-foreground/85">
              {formatCount(taken)} {taken === 1 ? "pass" : "passes"} over your data
              {walk?.authored_by === "model"
                ? ", with the readings chosen for this question"
                : ", from Revi's own standing opening read"}
              .
            </span>
          </span>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="space-y-3 border-t px-3 py-2.5">
            {walk?.rationale !== undefined && walk.rationale !== "" && (
              <p className="max-w-[70ch] text-meta leading-snug text-foreground/85">
                {walk.rationale}
              </p>
            )}
            {rounds.map((round) => (
              <RoundBlock key={round.index} round={round} />
            ))}
          </div>
        </CollapsibleContent>
      </section>
    </Collapsible>
  );
}

/** The verbs a walk step can carry, in the reader's own words. */
const STEP_WORDS: Record<string, string> = {
  orient: "Checked",
  consult: "Read",
  plan: "Chose",
  chase: "Went after",
  broaden: "Tried",
  drop: "Did not take",
  refuse: "Could not read",
  synthesize: "Wrote it up",
  execute: "Measured",
};

function RoundBlock({ round }: { round: ResearchStudyRound }) {
  const steps = round.steps ?? [];
  return (
    <div data-research-round={round.index} className="space-y-1">
      <p className="text-meta font-medium leading-snug">
        {round.index === 0 ? "The opening read" : `Round ${formatCount(round.index)}`}
      </p>
      {round.reason !== "" && (
        <p className="max-w-[70ch] text-meta leading-snug text-muted-foreground">
          {round.reason}
        </p>
      )}
      <ul className="space-y-1">
        {steps.map((step, index) => (
          <li
            key={`${step.action}-${step.subject}-${index}`}
            data-walk-action={step.action}
            className="flex max-w-[70ch] gap-1.5 text-micro leading-snug text-muted-foreground"
          >
            <span aria-hidden>·</span>
            <span>
              <span className="text-foreground/85">
                {STEP_WORDS[step.action] ?? step.action} {step.subject}
              </span>
              {step.reason !== "" && <> — {step.reason}</>}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Method: what it established, and what it read first                 */
/* ------------------------------------------------------------------ */

/**
 * THE DISCLOSURES ABOUT METHOD, in their own home.
 *
 * PATH CHOICES are what the run established about this data before it
 * chose anything — "your data carries this mainly in the remit codes; the
 * category field is filled on 12% of lines here". Each arrives already
 * composed beside the coverage figure it quotes, and re-wording one here
 * would make this the second place that fact is phrased. The second
 * phrasing is always the one that loses the coverage.
 *
 * BACKGROUND NOTES are titles only, which is the server's own rule and
 * worth keeping on the report as well as on the card: a note's content
 * shapes which reading ran and can never shape what a number says, so
 * printing its key points beside a measured figure would put an industry
 * number next to a measured one on the same screen.
 */
function MethodNotes({ study }: { study: ResearchStudy }) {
  const choices = study.path_choices ?? [];
  const notes = study.knowledge_consulted ?? [];
  if (choices.length === 0 && notes.length === 0 && study.knowledge_statement === "") return null;

  return (
    <section
      data-research-method
      aria-labelledby="research-method-heading"
      className="space-y-2.5"
    >
      <h3
        id="research-method-heading"
        className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
      >
        What Revi checked before it chose
      </h3>
      {choices.length > 0 && (
        <ul className="space-y-1">
          {choices.map((choice) => (
            <li
              key={`${choice.subject}:${choice.statement}`}
              className="flex max-w-[70ch] gap-1.5 text-meta leading-snug text-foreground/85"
            >
              <span aria-hidden className="text-muted-foreground">
                ·
              </span>
              <span>{choice.statement}</span>
            </li>
          ))}
        </ul>
      )}
      {study.knowledge_statement !== "" && (
        <p className="max-w-[70ch] text-meta leading-snug text-muted-foreground">
          {study.knowledge_statement}
        </p>
      )}
      {notes.length > 0 && (
        <ul className="space-y-0.5">
          {notes.map((note) => (
            <li
              key={note.title}
              className="flex max-w-[70ch] gap-1.5 text-meta leading-snug text-foreground/85"
            >
              <span aria-hidden className="text-muted-foreground">
                ·
              </span>
              <span>{note.title}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* What the edge of the data left out                                  */
/* ------------------------------------------------------------------ */

/**
 * THE CENSORING DISCLOSURE, AS A QUIET NOTE — and only where it applies.
 *
 * A rate over a counted population can be censored: rows the payer has not
 * answered are in neither the numerator nor the denominator. A dollars or
 * days figure has no population to be censored out of, so on a study that
 * read none, this block is absent rather than empty. That is the server's
 * decision and this renders it rather than second-guessing it.
 */
function CountedAndNot({ study }: { study: ResearchStudy }) {
  const censoring = study.censoring;
  const statements = censoring?.statements ?? [];
  if (statements.length === 0) return null;
  const edge = safeDate(study.data_edge_date);
  const edgeSaid = statements.some((statement) => statement.includes(edge));

  return (
    <section
      aria-labelledby="research-study-censoring-heading"
      className="rounded-lg border bg-surface-sunken/40 p-3"
    >
      <h3
        id="research-study-censoring-heading"
        className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
      >
        What is counted, and what is not
      </h3>
      <ul className="mt-1.5 space-y-1">
        {statements.map((statement) => (
          <li key={statement} className="max-w-[70ch] text-meta leading-snug text-foreground/85">
            {statement}
          </li>
        ))}
      </ul>
      {!edgeSaid && (
        <p className="num mt-1.5 text-micro leading-snug text-muted-foreground">
          Everything above stands as the data did on {edge}.
        </p>
      )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Charts                                                              */
/* ------------------------------------------------------------------ */

/**
 * The study's figures, mapped through the SAME seam an answer's are, and
 * keyed by the id the reading names.
 *
 * The published title wins over the composed one for the reason the
 * review's report gives: this mode titles each figure in the reader's own
 * words ("A/R over 90 by payer within Veritas Comp Fund"), while every one
 * of them declares the same x column — so composition would collapse
 * distinct figures into identical headings on the surface that draws them
 * one under another.
 */
function useStudyCharts(study: ResearchStudy): Map<string, ChartSpec> {
  return useMemo(() => {
    const specs = new Map<string, ChartSpec>();
    for (const raw of study.charts ?? []) {
      const mapped = mapChartSpec(raw as unknown as Record<string, unknown>);
      if (mapped === null || mapped.id === "") continue;
      const spec =
        mapped.wireTitle !== undefined && mapped.wireTitle !== ""
          ? { ...mapped, title: mapped.wireTitle }
          : mapped;
      specs.set(spec.id, spec);
    }
    return specs;
  }, [study.charts]);
}

function safeDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
}
