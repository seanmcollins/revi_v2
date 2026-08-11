"use client";

import { useMemo } from "react";

import { CopyTextButton, DownloadCsvButton } from "@/components/answer/AnswerActions";
import { NarrativeText } from "@/components/answer/NarrativeText";
import { ThingsToKnowGroup } from "@/components/answer/ThingsToKnow";
import { InvestigationChart } from "@/components/charts/InvestigationChart";
import { ResearchEvidence } from "@/components/research/ResearchEvidence";
import { ResearchHeadlineFigures } from "@/components/research/ResearchHeadline";
import { ResearchStrataTable } from "@/components/research/ResearchStrataTable";
import { mapChartSpec } from "@/lib/contract";
import {
  cellInterval,
  cellRate,
  decimal,
  populationLabel,
  type ResearchContrast,
  type ResearchDeadline,
  type ResearchReport,
  type ResearchTimeliness,
} from "@/lib/deepResearch";
import { caveatLines, researchReportToCsv } from "@/lib/export";
import { formatCount, formatPct, mediumDate } from "@/lib/format";
import { researchLinkFor } from "@/lib/links";
import type { ChartSpec, WarningEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * THE REPORT — the product's deepest artifact, composed from the product's
 * own vocabulary and nothing else.
 *
 * Every zone below is an object this app already draws: the determination
 * is `KeyFigure` at display size, the populations are the table vocabulary
 * with the ranking mark in it, the contrasts and the curve are
 * `InvestigationChart` (so full screen, "view as" and the CSV are the same
 * controls they are on any answer), the caveats go through the same
 * "things to know" fold an answer uses, and the prose is `NarrativeText`
 * with its referent citations live. Nothing here is a bespoke widget that
 * would drift from its cousin on the answer surface.
 *
 * THE ORDER IS THE READING ORDER, AND IT IS ARGUED FOR.
 *
 *   THE DETERMINATION FIRST, with its interval inside it. This is a
 *     how-much question and the total is the answer to it.
 *   THE CAVEATS AS A COUNT, immediately under the figure. One line that
 *     opens, exactly as on an answer — the seven deep-research codes are
 *     titled in `warnings.ts` and every one of them changes how the figure
 *     above should be read.
 *   THE WRITE-UP, which is where the disclosures are composed IN FRONT of
 *     the prose by the server's own composer, so a reader who stops after
 *     one paragraph has the number and its limits.
 *   THEN THE WORKING: every population priced or refused, the money by
 *     population, the rates, the two contrasts with their tests in words,
 *     the timeliness curve with its implication beside it, the deadline
 *     table split by whether the limit is confirmed, and the censoring
 *     disclosure.
 *
 * WHAT IS DELIBERATELY ABSENT: "Monitor this" on these figures. The
 * affordance is offered by `InvestigationChart` whenever it is handed an
 * investigation id, and a run's stored analysis carries no measures — so
 * `POST /v1/monitors/pins` refuses it (HTTP 500 on a live run, against a
 * 201 for an ordinary investigation's chart). A control that cannot do
 * what its label promises is worse than no control, so the id is not
 * passed and the button does not appear.
 */
export function ResearchReportView({
  report,
  runId,
  className,
}: {
  report: ResearchReport;
  runId: string;
  className?: string;
}) {
  const warnings = useMemo<WarningEvent[]>(
    () =>
      (report.warnings ?? []).map((warning) => ({
        type: "warning" as const,
        code: warning.code,
        message: warning.message,
        severity: warning.severity,
        structured: true,
        ...(warning.count !== undefined ? { count: warning.count } : {}),
      })),
    [report.warnings],
  );
  const caveats = useMemo(() => caveatLines(warnings), [warnings]);
  const charts = useResearchCharts(report);
  const population = populationLabel(report.population);

  /**
   * SENTENCES THIS REPORT HAS ALREADY SAID SOMEWHERE ELSE.
   *
   * `context_notes` overlaps the figures' own annotations and the two
   * implications: the deadline's "the drop is steeper where the filing
   * limit is confirmed…" is published as a chart annotation AND as a
   * context note, so both land on one screen, forty pixels apart. That is
   * the same defect `foldComposedDisclosures` exists for on the answer
   * path, and the same fix — the sentence is kept where it qualifies
   * something and dropped where it would only be repeated.
   *
   * Exact matches only. A note that says something ADJACENT to an
   * annotation is a different sentence and stays.
   */
  const saidElsewhere = useMemo(() => {
    const said = new Set<string>();
    for (const spec of [
      ...charts.general,
      ...charts.contrast.values(),
      charts.timeliness,
      charts.deadline,
    ]) {
      for (const note of spec?.notes ?? []) said.add(note.trim());
    }
    if (report.timeliness?.implication) said.add(report.timeliness.implication.trim());
    if (report.deadline?.implication) said.add(report.deadline.implication.trim());
    return said;
  }, [charts, report.timeliness, report.deadline]);

  return (
    <article
      data-research-report={runId}
      aria-labelledby="research-report-heading"
      className={cn("space-y-8", className)}
    >
      <header className="space-y-1">
        <p className="text-micro font-semibold uppercase tracking-widest text-muted-foreground">
          Deep research
        </p>
        <h2 id="research-report-heading" className="text-lead font-medium leading-snug">
          {report.research_question}
        </h2>
        <p className="num text-micro text-muted-foreground">
          {population}
          {report.data_load_label !== "" && ` · ${report.data_load_label}`}
          {report.completed_at !== null && report.completed_at !== undefined && (
            <> · {formatCount(Math.round(report.duration_ms / 1000))}s of measuring and writing</>
          )}
        </p>
      </header>

      <ResearchHeadlineFigures
        headline={report.headline}
        dataLoadLabel={report.data_load_label}
      />

      {/* THE EXISTING FOLD. Seven codes, all titled, all real — and stacked
          as seven boxes they would be the loudest thing between the
          determination and the writing. One line that opens, with the
          severity clause saying why it is worth opening. */}
      <ThingsToKnowGroup warnings={warnings} />

      {report.narrative !== "" && (
        <section aria-labelledby="research-narrative-heading" className="space-y-2">
          <h3 id="research-narrative-heading" className="sr-only">
            The write-up
          </h3>
          <div className="max-w-[68ch]">
            <NarrativeText text={report.narrative} size="lead" />
          </div>
        </section>
      )}

      <ResearchStrataTable
        strata={report.strata ?? []}
        notEstimable={report.not_estimable ?? []}
        {...(report.thin_populations ? { thin: report.thin_populations } : {})}
      />

      {/* THE MONEY AND THE RATES, as the figures the engine published. Each
          one is a full `InvestigationChart`: full screen and "view as"
          work on it exactly as on an answer's chart, and the CSV beside it
          carries the report's caveats. */}
      {charts.general.map((spec) => (
        <ResearchChart key={spec.id} spec={spec} report={report} runId={runId} caveats={caveats} />
      ))}

      {(report.contrasts ?? []).length > 0 && (
        <section aria-labelledby="research-contrasts-heading" className="space-y-5">
          <h3
            id="research-contrasts-heading"
            className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
          >
            The ends of the range, tested
          </h3>
          {(report.contrasts ?? []).map((contrast, index) => (
            <ContrastBlock
              key={`${contrast.title}-${index}`}
              contrast={contrast}
              spec={charts.contrast.get(contrast.title)}
              report={report}
              runId={runId}
              caveats={caveats}
            />
          ))}
        </section>
      )}

      {report.timeliness && (
        <TimelinessBlock
          timeliness={report.timeliness}
          spec={charts.timeliness}
          report={report}
          runId={runId}
          caveats={caveats}
        />
      )}

      {report.deadline && (
        <DeadlineBlock
          deadline={report.deadline}
          spec={charts.deadline}
          report={report}
          runId={runId}
          caveats={caveats}
        />
      )}

      <ContextNotes notes={report.context_notes ?? []} alreadySaid={saidElsewhere} />

      <CensoringNote report={report} />

      <ResearchEvidence evidence={report.evidence ?? []} className="xl:hidden" />

      <footer className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-t pt-3">
        <DownloadCsvButton
          label="Download the report"
          title="Save every row this report is built from — the populations, the rates with their intervals, the timeliness bands, both sides of the filing deadline and the two contrasts — under the same caveats the screen carries."
          filenameKind="deep-research"
          filenameTag={report.research_question}
          csv={() => researchReportToCsv(report, { runId })}
        />
        <CopyTextButton
          label="Copy this report's link"
          doneLabel="Link copied"
          title="Copy a link to this report. It opens the finished report, not a snapshot of this screen."
          text={() =>
            researchLinkFor(runId, typeof window === "undefined" ? "" : window.location.origin)
          }
        />
        <p className="num text-micro text-muted-foreground">
          The file carries every row, including the populations no rate could be published for.
        </p>
      </footer>
    </article>
  );
}

/* ------------------------------------------------------------------ */
/* The figures the engine published                                    */
/* ------------------------------------------------------------------ */

/**
 * The report's charts, mapped through the SAME seam an answer's are.
 *
 * `mapChartSpec` is what turns a published frame into the shape this app
 * draws — the unit rescaling, the row keying, the ordering rules, the
 * annotations. Using it here rather than reading the payload directly is
 * what makes "the charts obey every rule" true by construction instead of
 * by repetition.
 *
 * Partitioned by the id the engine gives each figure so a contrast's chart
 * can sit beside the contrast's own sentence. Anything the partition does
 * not recognise stays in `general` and is still drawn — a figure the
 * engine adds later must not silently disappear from the report because
 * this client did not know its name.
 */
function useResearchCharts(report: ResearchReport): {
  general: ChartSpec[];
  contrast: Map<string, ChartSpec>;
  timeliness?: ChartSpec;
  deadline?: ChartSpec;
} {
  return useMemo(() => {
    const general: ChartSpec[] = [];
    const contrast = new Map<string, ChartSpec>();
    let timeliness: ChartSpec | undefined;
    let deadline: ChartSpec | undefined;
    for (const raw of report.charts ?? []) {
      const mapped = mapChartSpec(raw as unknown as Record<string, unknown>);
      if (mapped === null || mapped.id === "") continue;
      /*
       * THIS MODE'S FIGURES ARE ALREADY TITLED FOR A READER, and the
       * composed title is wrong for them.
       *
       * `mapChartSpec` composes `"{measure} by {x}"` because on the answer
       * path the wire's own title is engine bookkeeping ("denied dollars —
       * main 2  compare"). Deep research is the opposite case: the pack
       * authors each figure's title in the reader's words ("Recovery rate
       * by payer", "Recovery rate by denial type", "Recovery rate by
       * denied amount") while every one of them declares the same x column
       * (`population`) — so composition collapses three distinct figures
       * into three identical headings, on the surface that draws them
       * one under another.
       *
       * `wireTitle` is where the mapper keeps the server's own string, so
       * nothing is invented here: the published title wins where the
       * server published one worth reading.
       */
      const spec =
        mapped.wireTitle !== undefined && mapped.wireTitle !== ""
          ? { ...mapped, title: mapped.wireTitle }
          : mapped;
      const id = spec.frameId ?? spec.id;
      if (id.includes("contrast")) contrast.set(spec.title, spec);
      else if (id.includes("timeliness")) timeliness = spec;
      else if (id.includes("deadline")) deadline = spec;
      else general.push(spec);
    }
    return {
      general,
      contrast,
      ...(timeliness ? { timeliness } : {}),
      ...(deadline ? { deadline } : {}),
    };
  }, [report.charts]);
}

/**
 * One figure, with everything the run knows about it attached — the same
 * assembly `AnswerChart` does for a turn, for the same reason: three call
 * sites building these props by hand is how they come to disagree.
 *
 * NO `investigationId`. See the note at the top of this file: passing one
 * would draw a "Monitor this" the server refuses.
 */
function ResearchChart({
  spec,
  report,
  runId,
  caveats,
}: {
  spec: ChartSpec;
  report: ResearchReport;
  runId: string;
  caveats: readonly string[];
}) {
  return (
    <InvestigationChart
      spec={spec}
      turnId={runId}
      windowLabel={report.data_load_label}
      question={report.research_question}
      caveats={caveats}
    />
  );
}

/* ------------------------------------------------------------------ */
/* A contrast, and what the test actually says                         */
/* ------------------------------------------------------------------ */

/**
 * TWO ARMS AND A SENTENCE THAT SAYS WHETHER THE GAP IS REAL.
 *
 * `implication` is the SERVER's own sentence and it is printed verbatim:
 * it already states the separation in plain terms ("the chance of seeing a
 * gap this size if the two were really the same is under 1 in 1,000") and
 * the size of the gap with its bounds. A client that re-derived that from
 * `p_value` would be composing a statistical claim in a browser, and the
 * lexicon's rule about probabilities exists precisely so a reader is never
 * handed a bare number to weigh.
 *
 * A REFUSED test says so instead. `test: "refused"` carries a reason, and
 * the arms are still drawn — the two rates are real measurements even when
 * the comparison between them is not one this run would make.
 */
function ContrastBlock({
  contrast,
  spec,
  report,
  runId,
  caveats,
}: {
  contrast: ResearchContrast;
  spec?: ChartSpec;
  report: ResearchReport;
  runId: string;
  caveats: readonly string[];
}) {
  const refused = contrast.test === "refused";
  const difference = decimal(contrast.risk_difference);

  return (
    <div data-research-contrast={refused ? "refused" : "tested"} className="space-y-2">
      {spec ? (
        <ResearchChart spec={spec} report={report} runId={runId} caveats={caveats} />
      ) : (
        <h4 className="text-body font-medium">{contrast.title}</h4>
      )}
      <div className="max-w-[68ch] space-y-1">
        {/* THE SEPARATION, IN WORDS. The one place a p-value could have
            reached a reader as a number, and does not. */}
        <p className="text-body leading-snug">
          {refused
            ? (contrast.refusal_reason ??
              "This comparison was not made: the two sides are not comparable here.")
            : contrast.implication}
        </p>
        {!refused && difference !== undefined && (
          <p className="num text-meta leading-snug text-muted-foreground">
            {contrast.left.label} recovers {formatPct(decimal(contrast.left.rate) ?? 0)} of the{" "}
            {formatCount(contrast.left.n)} denials it has answered;{" "}
            {contrast.right.label} recovers {formatPct(decimal(contrast.right.rate) ?? 0)} of{" "}
            {formatCount(contrast.right.n)}.
          </p>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* The curve, and the deadline                                         */
/* ------------------------------------------------------------------ */

/**
 * THE PRICE OF A SLOW QUEUE, with the sentence that says what it means
 * NEXT TO IT rather than three zones away.
 *
 * The implication is the server's ("getting the work out inside 0-14 days
 * is worth 40.1 percentage points…") and it sits directly under the curve,
 * because a curve read without it is a shape and a curve read with it is a
 * staffing decision.
 */
function TimelinessBlock({
  timeliness,
  spec,
  report,
  runId,
  caveats,
}: {
  timeliness: ResearchTimeliness;
  spec?: ChartSpec;
  report: ResearchReport;
  runId: string;
  caveats: readonly string[];
}) {
  const bands = timeliness.bands ?? [];
  if (bands.length === 0 && spec === undefined) return null;

  return (
    <section aria-labelledby="research-timeliness-heading" className="space-y-2">
      <h3
        id="research-timeliness-heading"
        className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
      >
        Speed, and what it is worth
      </h3>
      {spec && <ResearchChart spec={spec} report={report} runId={runId} caveats={caveats} />}
      {timeliness.implication !== "" && (
        <p className="max-w-[68ch] text-body leading-snug">{timeliness.implication}</p>
      )}
      {bands.length > 0 && (
        <p className="num max-w-[68ch] text-meta leading-snug text-muted-foreground">
          {bands
            .map(
              (band) =>
                `${band.band} days: ${
                  cellRate(band.cell) === undefined
                    ? "not estimable"
                    : `${formatPct(cellRate(band.cell) ?? 0)} of ${formatCount(band.cell.n)} answered`
                }`,
            )
            .join(" · ")}
        </p>
      )}
    </section>
  );
}

/**
 * THE FILING DEADLINE, SPLIT BY WHETHER THE LIMIT IS CONFIRMED.
 *
 * This is the split the engine went to the trouble of publishing and it is
 * the whole honesty of the zone: a confirmed limit and a planning default
 * are different claims about the same date, and treating every limit as
 * confirmed overstates the cliff — which is the server's own sentence,
 * printed under the table.
 *
 * The rows are grouped by rule rather than sorted, so "limit confirmed"
 * and "limit needs confirming" are two blocks a reader compares, not four
 * rows they have to disentangle. The labels are the payload's own
 * (`position_label`, `rule_label`); nothing here names a state itself.
 */
function DeadlineBlock({
  deadline,
  spec,
  report,
  runId,
  caveats,
}: {
  deadline: ResearchDeadline;
  spec?: ChartSpec;
  report: ResearchReport;
  runId: string;
  caveats: readonly string[];
}) {
  const rows = deadline.rows ?? [];
  const byRule = new Map<string, typeof rows>();
  for (const row of rows) {
    byRule.set(row.rule_label, [...(byRule.get(row.rule_label) ?? []), row]);
  }
  if (rows.length === 0 && spec === undefined) return null;

  return (
    <section aria-labelledby="research-deadline-heading" className="space-y-2">
      <h3
        id="research-deadline-heading"
        className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
      >
        The filing deadline
      </h3>
      {spec && <ResearchChart spec={spec} report={report} runId={runId} caveats={caveats} />}
      {rows.length > 0 && (
        <div className="grid gap-2 sm:grid-cols-2">
          {[...byRule.entries()].map(([rule, group]) => (
            <div key={rule} data-deadline-rule={rule} className="rounded-md border p-2.5">
              <p className="text-meta font-medium">{rule}</p>
              <ul className="mt-1 space-y-1">
                {group.map((row) => {
                  const rate = cellRate(row.cell);
                  const interval = cellInterval(row.cell);
                  return (
                    <li key={row.position_label} className="num text-meta leading-snug">
                      <span className="text-muted-foreground">{row.position_label}: </span>
                      {rate === undefined ? (
                        <span className="text-muted-foreground">not estimable</span>
                      ) : (
                        <>
                          <span className="font-medium">{formatPct(rate)}</span>
                          <span className="text-muted-foreground">
                            {" "}
                            of {formatCount(row.cell.n)} answered
                            {interval &&
                              ` · ${formatPct(interval.low)}–${formatPct(interval.high)}`}
                          </span>
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>
      )}
      {deadline.implication !== "" && (
        <p className="max-w-[68ch] text-body leading-snug">{deadline.implication}</p>
      )}
    </section>
  );
}

/**
 * THE SENTENCES THE RUN WROTE THAT NO ZONE OWNS.
 *
 * `context_notes` is a real published list and it is not decoration: it
 * carries the median days to resubmission per denial type, what the
 * fastest band is worth in points, the bound under a measured zero ("that
 * is not the same as never: on this many denials the true rate could still
 * be as high as 9.0%"), and the pack's own account of what each kind of
 * denial IS.
 *
 * They are rendered as one list rather than distributed into the zones
 * they seem to belong to, and that is deliberate: attributing them by
 * matching their prose would be this client guessing which figure a
 * server-written sentence is about, and a sentence filed under the wrong
 * figure is worse than one filed plainly. Dropping them was the other
 * option, and dropping a platform's own sentences is not an option.
 */
function ContextNotes({
  notes,
  alreadySaid,
}: {
  notes: readonly string[];
  /** Sentences a figure or an implication has already put on this screen. */
  alreadySaid: ReadonlySet<string>;
}) {
  const unsaid = notes.filter((note) => !alreadySaid.has(note.trim()));
  if (unsaid.length === 0) return null;
  return (
    <section aria-labelledby="research-notes-heading" className="space-y-1.5">
      <h3
        id="research-notes-heading"
        className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
      >
        What else is worth knowing
      </h3>
      <ul className="space-y-1">
        {unsaid.map((note) => (
          <li
            key={note}
            className="flex max-w-[70ch] gap-1.5 text-meta leading-snug text-foreground/85"
          >
            <span aria-hidden className="text-muted-foreground">
              ·
            </span>
            <span>{note}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* What the edge of the data left out                                  */
/* ------------------------------------------------------------------ */

/**
 * THE CENSORING DISCLOSURE, AS A QUIET NOTE.
 *
 * Not a warning banner. Nothing here is a verdict, a refusal or a
 * correction — it is the statement of which denials are in the denominator
 * of every rate above and which are counted in neither the wins nor the
 * losses, and the amber register is reserved for verdict-class content. It
 * is the server's own four sentences, verbatim, with the counts and the
 * edge date already in them.
 */
function CensoringNote({ report }: { report: ResearchReport }) {
  const censoring = report.censoring;
  const statements = censoring.statements ?? [];
  if (statements.length === 0) return null;
  /**
   * THE EDGE DATE IS SAID ONCE.
   *
   * The server's own last statement is "Everything above is as the data
   * stood on Aug 2, 2026." — so the line this component would add under it
   * is the same fact in slightly different words, eight pixels away. It is
   * kept only for a payload that did not say it, because the date is the
   * whole point of the disclosure and losing it is worse than repeating
   * it.
   */
  const edge = safeDate(report.data_edge_date);
  const edgeSaid = statements.some((statement) => statement.includes(edge));

  return (
    <section
      aria-labelledby="research-censoring-heading"
      className="rounded-lg border bg-surface-sunken/40 p-3"
    >
      <h3
        id="research-censoring-heading"
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

function safeDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
}
