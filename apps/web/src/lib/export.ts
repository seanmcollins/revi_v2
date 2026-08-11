/**
 * Taking the numbers out of the browser.
 *
 * Everything here is a PURE function over payload that is already on the
 * wire and already on screen — no second fetch, no server round trip, no
 * recomputation. An analyst who has to walk into a payer meeting with a
 * figure should not have to retype it, and a figure retyped by hand is a
 * figure that has lost its window, its scope and its caveats on the way to
 * the meeting.
 *
 * The one discipline this module enforces: **the caveats travel with the
 * numbers.** A copied answer carries its context header, its findings, the
 * governed external ranges those findings were quoted against, the
 * platform's own warnings, and a provenance line naming the data load and
 * the metric pack. Copying the findings alone would be this product
 * shipping the flattering half of its own answer, which is the exact
 * failure the honesty layer exists to prevent.
 *
 * Nothing here is a chart, an image, or a PDF: plain text and CSV, both of
 * which paste into an email and open in a spreadsheet without an
 * intermediary that could reformat a number.
 */

import type { WorklistData } from "@/lib/contract";
import {
  decimal,
  populationLabel,
  type ResearchRateCell,
  type ResearchReport,
  type ResearchStudy,
} from "@/lib/deepResearch";
import {
  formatCents,
  formatCount,
  formatMeasure,
  formatWindow,
  mediumDate,
  type MeasureUnit,
} from "@/lib/format";
import type { PortfolioItem } from "@/lib/mock/portfolio";
import { tidyProse } from "@/lib/prose";
import type {
  Benchmark,
  ContextHeaderData,
  ChartSpec,
  Finding,
  MetricProvenance,
  WarningEvent,
} from "@/lib/types";
import { warningBody, warningTitle } from "@/lib/warnings";

/* ------------------------------------------------------------------ */
/* CSV (RFC 4180)                                                      */
/* ------------------------------------------------------------------ */

export type CsvValue = string | number | boolean | null | undefined;

/**
 * One cell, quoted per RFC 4180.
 *
 * Quoted whenever the value contains a comma, a quote, a newline or
 * leading/trailing space — and ALSO when it could be read as a formula by
 * a spreadsheet (`=`, `+`, `-`, `@`, tab, CR), which is prefixed with a
 * single quote. That last rule is not paranoia: these exports carry
 * free-text titles and refusal sentences straight off the wire, and a cell
 * beginning with `=` is executed by Excel on open.
 */
export function csvCell(value: CsvValue): string {
  if (value === null || value === undefined) return "";
  const raw = typeof value === "string" ? value : String(value);
  const guarded = /^[=+\-@\t\r]/.test(raw) ? `'${raw}` : raw;
  return /[",\n\r]|^\s|\s$/.test(guarded) ? `"${guarded.replace(/"/g, '""')}"` : guarded;
}

/**
 * Header row + body rows as one CRLF-delimited CSV document, optionally
 * under a block of `#` comment lines.
 *
 * The preamble is where an export's caveats live. Each line is emitted as a
 * SINGLE cell through `csvCell`, so a sentence containing a comma stays one
 * cell and a sentence beginning with "=" is text rather than something
 * Excel executes on open. `#` is not a formula trigger, so the guard never
 * fires on the marker itself.
 */
export function buildCsv(
  headers: readonly string[],
  rows: readonly CsvValue[][],
  preamble: readonly string[] = [],
): string {
  const lines = preamble.map((line) =>
    csvCell(`# ${line.replace(/\s*[\r\n]+\s*/g, " ").trim()}`),
  );
  lines.push(headers.map(csvCell).join(","));
  for (const row of rows) lines.push(row.map(csvCell).join(","));
  return `${lines.join("\r\n")}\r\n`;
}

/* ------------------------------------------------------------------ */
/* Shared money / value rendering for exports                          */
/* ------------------------------------------------------------------ */

/**
 * Cents as a plain decimal number for a spreadsheet cell: 17821682 →
 * `178216.82`. Deliberately NOT `formatWholeDollars` — a CSV column is
 * going to be summed, and "$178,217" is text to every spreadsheet on
 * earth. The currency is named once, in the column heading.
 */
function centsToDollars(cents: number | undefined): number | undefined {
  return cents === undefined ? undefined : Math.round(cents) / 100;
}

/* ------------------------------------------------------------------ */
/* Copy an answer as text                                              */
/* ------------------------------------------------------------------ */

/**
 * Everything the copied answer is composed from. A plain shape rather than
 * the store's `TurnRecord` so this is testable without React and so the
 * caller has to hand over the caveats explicitly — they are not optional
 * and a shape that made them optional would eventually ship without them.
 */
export interface AnswerCopyInput {
  question?: string;
  header?: ContextHeaderData;
  findings: readonly Finding[];
  narrative: string;
  warnings: readonly WarningEvent[];
  metric?: MetricProvenance;
  investigationId?: string;
  /**
   * The rows behind the charts — every one of them.
   *
   * A copied answer used to carry three of twelve payers: the caveats were
   * immaculate and the numbers were a slice, with nothing saying so. The
   * two halves of an export cannot be split like that — a complete artifact
   * without caveats and a caveated artifact without the data are the same
   * failure wearing different clothes. So the table travels too, and the
   * findings section states which rows it named.
   */
  charts?: readonly ChartSpec[];
  /**
   * The ranked worklist the turn attached. `WORKLIST_ATTACHED` already
   * survives into the caveats, so a text export without this announces a
   * ranked worklist it does not contain.
   */
  worklist?: WorklistData;
  /** True when the turn was rebuilt from stored state, not watched live. */
  restored?: boolean;
  /**
   * `InvestigationResponse.restoration_notes` — the SERVER's account of
   * what a restore rebuilt and what the store does not keep.
   *
   * This is the share path. The permalink is a first-class header button,
   * and on a payload generation that does not persist the composed prose
   * the artifact a prospect forwards is, by construction, this product's
   * caveats with its reasoning removed — which inverts the calibration,
   * because the reasoning is what makes the caveats read as rigour rather
   * than as panic. These notes are the only thing on the wire that says
   * WHICH of "there was nothing to say" and "the sentences were not
   * stored" is true, in the server's own words, so they travel with the
   * export instead of one apologetic client-side line standing in for
   * them.
   */
  restorationNotes?: readonly string[];
  /** Injected so the output is a pure function of its inputs (tests pin it). */
  copiedAt?: Date;
}

const RULE = "—".repeat(4);

/**
 * What an export may say when it is holding no warnings.
 *
 * NOT "the platform attached no caveats to this answer". That is a claim
 * about the analysis — that nothing needed saying — and this browser cannot
 * establish it. It holds a payload. Live, a refinement turn re-served its
 * parent's plan with all eleven warnings stripped, and the CSV it produced
 * affirmed their absence under a watermark, an investigation id and a full
 * provenance block: the most confident artifact in the product, asserting
 * the one thing it had just lost.
 */
export const NO_CAVEATS_LINE =
  "No caveats were attached to this turn's payload. That is what this export holds — not a finding that the analysis needed none.";

/**
 * The turn's warnings as the sentences an export prints — cautions first,
 * each one titled by its code and counted when it was raised more than
 * once.
 *
 * Shared rather than duplicated: the text copy and the chart CSV must
 * carry the SAME caveats, or the product has two accounts of one answer
 * and the reader has whichever one they exported.
 *
 * THE ONE REPAIR AN EXPORT IS ALLOWED TO MAKE. `tidyProse`, and nothing
 * else. Everything in this module exists to be reproducible, so the
 * engine's wording is passed through — no identifier redaction, no date
 * respelling, none of the presentation hygiene the SCREEN applies through
 * `publicWarningBody`. A stop printed twice is the exception, because it
 * is not wording: it is a mechanical defect in a join, and `tidyProse`
 * returns anything without one byte-identical.
 *
 * WHERE IT COMES FROM, exactly, because the real fix is not here. The
 * premise verdicts compose their warning as
 * `f"premise_unverifiable: {sentence}. The question's own assumption is
 * neither…"` in
 * `packages/investigation/src/revi_investigation/application/findings/premise.py:791`,
 * over a `sentence` that already ends on a full stop — "Ask again once the
 * thinner side matures." at `:643`. So the wire carries
 * "…matures.. The question's own assumption…", and has for four review
 * rounds. That join is backend territory and stays filed there; what is
 * fixed here is that the same string reached the CSV with the stop still
 * doubled while the on-screen copy of it had been repaired since the
 * banner shipped. One answer must not have two spellings depending on
 * which button was pressed.
 */
export function caveatLines(warnings: readonly WarningEvent[]): string[] {
  const ordered = [
    ...warnings.filter((w) => w.severity === "caution"),
    ...warnings.filter((w) => w.severity !== "caution"),
  ];
  return ordered.map((warning) => {
    const title = warningTitle(warning.code);
    // Unconditional, exactly as on screen: an untitled code costs the
    // reader a heading, not a machine prefix in the middle of a caveat.
    const body = tidyProse(warningBody(warning.code, warning.message));
    const times = warning.count && warning.count > 1 ? ` (raised ${warning.count} times)` : "";
    return `[${warning.severity}] ${title ? `${title} — ` : ""}${body}${times}`;
  });
}

/** "as of Aug 2, 2026 (service date)" or "Jul 1 – Jul 31, 2026 (service date)". */
export function windowLine(header: ContextHeaderData): string {
  if (header.asOf) {
    // A snapshot contract states a balance AT a moment; the payload still
    // carries a window on those turns and it is not what was measured.
    return `as of ${safeMediumDate(header.asOf)} — a balance at that moment, not a total over a range`;
  }
  return formatWindow(header.window);
}

function safeMediumDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
}

function benchmarkLine(benchmark: Benchmark): string {
  const range =
    benchmark.valueLow === benchmark.valueHigh
      ? benchmark.valueLow
      : `${benchmark.valueLow}–${benchmark.valueHigh}`;
  const parts = [
    `${range}${benchmark.unit ? ` ${benchmark.unit}` : ""}`,
    benchmark.cohortLabel,
    benchmark.period,
    benchmark.authority,
  ].filter((p) => p !== "");
  const review =
    benchmark.reviewStatus === "machine_researched"
      ? " [unreviewed — machine-researched, not checked by a person]"
      : benchmark.reviewStatus
        ? ` [${benchmark.reviewStatus}]`
        : "";
  return `${parts.join(" · ")}${review}`;
}

/**
 * The answer as plain text, with its caveats attached.
 *
 * Section order mirrors the card: context, findings (each with the
 * external ranges it was quoted against and their cautions), the written
 * analysis, the platform's warnings, then provenance. A reader who pastes
 * this into an email sends the same claim the screen made, bounded the
 * same way.
 */
export function answerToText(input: AnswerCopyInput): string {
  const out: string[] = [];
  if (input.question) out.push(input.question, "");

  const header = input.header;
  if (header) {
    out.push("CONTEXT", RULE);
    out.push(`Window: ${windowLine(header)}`);
    if (header.comparison) out.push(`Compared with: ${formatWindow(header.comparison.window)}`);
    out.push(
      `Scope: ${
        header.filters.length === 0
          ? "all — no filters applied"
          : header.filters
              .map(
                (f) =>
                  `${f.dimensionLabel} ${f.op === "not_in" ? "excludes" : "="} ${f.values.join(", ")}`,
              )
              .join(" · ")
      }`,
    );
    if (header.cohort) {
      out.push(
        `Cohort: ${header.cohort.detailed ? header.cohort.definition : header.cohort.id} (${formatCount(header.cohort.size)} ${header.cohort.entityGrain ?? "entities"})`,
      );
    }
    out.push(
      `Data as of: ${header.watermark.loadedAt} (newest activity ${safeMediumDate(header.watermark.newestDataDate)})`,
    );
    out.push(`Metric definitions: ${header.packVersion.packId}@${header.packVersion.version}`);
    out.push("");
  } else {
    out.push("CONTEXT", RULE);
    out.push(
      "This turn published no context header — the window, scope and data load it used were not stored, so they are not stated here rather than guessed at.",
    );
    out.push("");
  }

  // How much of the measured table the findings actually name. Twelve
  // payers were measured, three were written up, and the copied text said
  // nothing about the other nine — so the email read as the whole answer.
  const measuredRows = Math.max(0, ...(input.charts ?? []).map((c) => c.rows.length));

  if (input.findings.length > 0) {
    out.push("FINDINGS", RULE);
    if (measuredRows > input.findings.length) {
      out.push(
        `${input.findings.length} of ${measuredRows} measured rows are written up below. All ${measuredRows} are in the DATA section further down.`,
        "",
      );
    }
    for (const finding of input.findings) {
      out.push(`${finding.referent.value}  ${finding.title}`);
      if (finding.statement) out.push(`    ${finding.statement}`);
      if (finding.metricDisplay?.caveat) {
        out.push(`    How to read it: ${finding.metricDisplay.caveat}`);
      }
      out.push(
        `    Evidence: ${finding.grade} · confidence ${finding.confidence}`,
      );
      for (const benchmark of finding.benchmarks ?? []) {
        out.push(`    External range: ${benchmarkLine(benchmark)}`);
        for (const caution of benchmark.cautions) out.push(`      caution: ${caution}`);
        for (const source of benchmark.sources) out.push(`      source: ${source}`);
      }
      out.push("");
    }
  }

  if (input.narrative.trim() !== "") {
    out.push("ANALYSIS", RULE, input.narrative.trim(), "");
  } else if (input.restored) {
    out.push(
      "ANALYSIS",
      RULE,
      // Names what is in this file rather than only what is missing from
      // it. The DATA sections below are the charts the restore rebuilt,
      // and a note that stops at "the findings above" tells a reader who
      // scrolls that the rows underneath are not part of the record.
      `The written analysis was not stored for this answer. What the server kept is in this file: the findings above${
        (input.charts?.length ?? 0) > 0
          ? ", the chart data below"
          : ""
      }, and the context they were measured under.`,
      // The server's own sentences about the restore, verbatim. A reader
      // who receives this file is owed the difference between an answer
      // that had nothing to say and an answer whose sentences were not
      // persisted, and the client cannot establish that difference.
      ...(input.restorationNotes ?? []),
      "",
    );
  }

  // The rows behind the pictures, in full. Written as aligned text rather
  // than CSV because this artifact is pasted into an email, and the CSV
  // button beside it is what a spreadsheet is for.
  for (const chart of input.charts ?? []) {
    if (chart.rows.length === 0) continue;
    out.push(
      `DATA — ${chart.title}`,
      RULE,
      `${chart.rows.length} row${chart.rows.length === 1 ? "" : "s"} × ${chart.series.length} series, in ${unitWord(chart.unit)}${orderPhrase(chart)}`,
    );
    // The rows below are cells, and when the wire's rows did not key
    // uniquely onto them a reader is owed the arithmetic before the list.
    if (chart.keying) out.push(`    ${chart.keying.note}`);
    for (const row of chart.rows) {
      const cells = chart.series.map((series) => {
        const value = row.values[series.key];
        // A withheld cell is a blank, never a zero — the same discipline
        // the CSV keeps. And a bounded one is a ceiling, said as one.
        const text =
          typeof value !== "number" || !Number.isFinite(value)
            ? "(withheld)"
            : `${row.bounded ? "≤ " : ""}${formatMeasure(value, chart.unit)}`;
        return chart.series.length === 1 ? text : `${series.label} ${text}`;
      });
      out.push(`    ${row.label}: ${cells.join(" · ")}`);
    }
    if (chart.rows.some((row) => row.bounded)) {
      out.push(
        // The same plain voice the figure's own legend uses. One idea, in
        // the reader's nouns: three surfaces of one answer describing one
        // control in three vocabularies is how a reader learns to skip all
        // three. See `boundedLegend`.
        "    ≤ means at most: too few things sit behind that mark to measure it exactly, so the real figure is at or below it.",
      );
    }
    out.push("");
  }

  if (input.worklist) {
    const worklist = input.worklist;
    out.push(`RANKED WORKLIST — ${worklist.label || "what to work first"}`, RULE);
    if (worklist.statement) out.push(worklist.statement);
    out.push(
      `${worklist.items.length} of ${worklist.totalItems} cards listed here · ranked by ${worklist.formulaVersion} · data load ${worklist.watermarkId}`,
    );
    for (const item of worklist.items) {
      const parts = [
        `#${item.rank}`,
        item.lane ? `[${item.lane}]` : "",
        item.title,
        item.impactCents === undefined ? "" : `impact $${centsToDollars(item.impactCents)}`,
        item.recoverableCentsEstimate === undefined
          ? ""
          : `recoverable $${centsToDollars(item.recoverableCentsEstimate)}`,
        item.rankedOn ? `ranked on ${item.rankedOn}` : "",
        item.impactAgreement ? `reconciliation: ${item.impactAgreement}` : "",
      ].filter((p) => p !== "");
      out.push(`    ${parts.join(" · ")}`);
    }
    for (const warning of worklist.warnings) {
      out.push(`    caution: ${warningBody(warning.code, warning.message)}`);
    }
    out.push("");
  }

  // Never optional, never below the fold, never dropped when the list is
  // empty. But what it says when the list IS empty is a claim about the
  // PAYLOAD, not about the analysis: "the platform attached no caveats to
  // this answer" asserts that nothing needed saying, and a refinement turn
  // that re-served a plan with its warnings stripped published exactly that
  // sentence under a watermark and an investigation id. This client cannot
  // know why a warning list is empty, so it reports what it holds.
  out.push("CAVEATS THAT TRAVEL WITH THESE NUMBERS", RULE);
  const caveats = caveatLines(input.warnings);
  out.push(...(caveats.length === 0 ? [NO_CAVEATS_LINE] : caveats));
  out.push("");

  out.push("PROVENANCE", RULE);
  const provenance: string[] = ["Revi"];
  if (input.investigationId) provenance.push(`investigation ${input.investigationId}`);
  if (header) {
    provenance.push(`data load ${header.watermark.id} (${header.watermark.loadedAt})`);
    provenance.push(`metric pack ${header.packVersion.packId}@${header.packVersion.version}`);
  } else if (input.metric) {
    provenance.push(`metric pack ${input.metric.pack.packId}@${input.metric.pack.version}`);
  }
  if (input.metric?.playbookId) provenance.push(`playbook ${input.metric.playbookId}`);
  if (input.restored) provenance.push("rebuilt from this session's stored history");
  provenance.push(`copied ${isoInstant(input.copiedAt ?? new Date())}`);
  out.push(provenance.join(" · "));
  out.push(
    "These numbers are as of the data load named above. Re-running the same question against a newer load can change them.",
  );

  return `${out.join("\n").replace(/\n{3,}/g, "\n\n").trimEnd()}\n`;
}

/** "2026-08-09 14:32" in the reader's own timezone — never a bare epoch. */
function isoInstant(at: Date): string {
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${pad(at.getHours())}:${pad(at.getMinutes())}`;
}

/* ------------------------------------------------------------------ */
/* Portfolio worklist → CSV                                            */
/* ------------------------------------------------------------------ */

/**
 * Every column the worklist argues with itself about.
 *
 * A worklist CSV that carried only rank, title and impact would export the
 * detector's assertion and leave behind the four facts this product spent
 * the release earning: what this platform re-derived for the same cell,
 * whether the two agree, how much of the impact is actually recoverable,
 * and which lane the card is in (compliance work is done because the rule
 * says so; value work is ranked). Those are the columns a staffing
 * decision is made from.
 *
 * `ranked_on` and `ranked_impact_usd` say WHICH of the two dollar figures
 * on the row actually ordered it — live, that is 19 cards on the
 * detector's, 9 on this platform's and 5 on the detector's because this
 * platform's is not a comparable quantity. A spreadsheet carrying both
 * numbers, sorted by a rank whose basis it does not state, is the same
 * ambiguity the rail had.
 *
 * `drill_dimension_repoint` says the card's CUT was substituted: the
 * detector counted lines in a procedure group, the drill counts claims
 * whose largest procedure group is that one. Whoever re-derives this list
 * from the ids in it needs that or their numbers will not match.
 *
 * Both are emitted only when some card publishes them, and omitted while
 * none does — an always-empty column trains a reader to ignore it before
 * it ever means anything.
 */
export interface PortfolioCsvInput {
  items: readonly PortfolioItem[];
  watermark?: string;
  rankingPolicy?: string;
}

export function portfolioToCsv(input: PortfolioCsvInput): string {
  const hasRankedOn = input.items.some((item) => item.rankedOn !== undefined);
  const hasRepoint = input.items.some(
    (item) => (item.drillDimensionRepoints?.length ?? 0) > 0,
  );
  const headers = [
    "rank",
    "lane",
    "anomaly_id",
    "title",
    "metric_id",
    "measures",
    "severity",
    "detected_age_days",
    "window_start",
    "window_end",
    "impact_usd",
    "reconciled_impact_usd",
    "impact_agreement",
    "impact_delta_usd",
    "impact_delta_pct",
    "recoverable_usd",
    "actionability",
    "actionability_rationale",
    "priority_score",
    "compliance_floor_applied",
    ...(hasRankedOn ? ["ranked_on", "ranked_impact_usd", "ranked_on_note"] : []),
    "drillable",
    ...(hasRepoint ? ["drill_dimension_repoint"] : []),
    "drill_unavailable_reason",
    "impact_reconciliation_note",
    "data_as_of",
    "priority_formula",
  ];

  const rows: CsvValue[][] = input.items.map((item) => [
    item.rank,
    item.lane ?? "",
    item.referent,
    item.title,
    item.metricId ?? "",
    item.metricDisplayName ?? "",
    item.severity ?? "",
    item.ageDays,
    item.windowStart ?? "",
    item.windowEnd ?? "",
    centsToDollars(item.impactCents),
    centsToDollars(item.reconciledImpactCents),
    item.impactAgreement ?? "",
    centsToDollars(item.impactDeltaCents),
    item.impactDeltaFraction === undefined
      ? undefined
      : Number((item.impactDeltaFraction * 100).toFixed(4)),
    centsToDollars(item.recoverableCentsEstimate),
    item.actionabilityLabel ?? "",
    item.actionabilityRationale ?? "",
    item.priorityScore,
    item.complianceFloorApplied === undefined ? "" : String(item.complianceFloorApplied),
    ...(hasRankedOn
      ? [
          item.rankedOn ?? "",
          centsToDollars(item.rankedImpactCents),
          item.rankedOnNote ?? "",
        ]
      : []),
    String(item.drillable),
    // "proc_group→primary_proc_group". The rationale is long prose and
    // lives on the card; what a spreadsheet needs is the substitution
    // itself, so a re-derivation cuts the same way the drill does.
    ...(hasRepoint
      ? [
          (item.drillDimensionRepoints ?? [])
            .map((r) => `${r.fromDimension}→${r.toDimension}`)
            .join("; "),
        ]
      : []),
    item.drillUnavailableReason ?? "",
    item.impactReconciliationNote ?? "",
    input.watermark ?? "",
    item.priorityFormulaVersion,
  ]);

  return buildCsv(headers, rows);
}

/* ------------------------------------------------------------------ */
/* One turn's chart → CSV                                              */
/* ------------------------------------------------------------------ */

/**
 * The chart's own rows, in the unit the chart draws them in.
 *
 * The unit is named in the column heading rather than baked into the cell,
 * so the numbers stay summable: a `ratio` frame is scaled to percentage
 * points once at the wire seam and exported as `29.5`, under a heading
 * that says `percent`. A row whose value the engine withheld exports as an
 * EMPTY cell with a `withheld` note — never as a zero, which is a number
 * the engine did not publish and a payer meeting would act on.
 */
export interface ChartCsvMeta {
  /** The turn's window, so the sheet is not a table of undated numbers. */
  windowLabel?: string;
  /** The data load these rows were measured at — the same id the header states. */
  watermarkId?: string;
  /** The metric pack that defined the measure. */
  packLabel?: string;
  /** The question this chart answered. */
  question?: string;
  /** The turn's caveats, already rendered to sentences by the caller. */
  caveats?: readonly string[];
  /** What the picture did that this file does not (a series rollup, a cap). */
  renderNote?: string;
  investigationId?: string;
  /** Injected so the output is a pure function of its inputs (tests pin it). */
  exportedAt?: Date;
}

const UNIT_NAME: Record<MeasureUnit, string> = {
  cents: "usd",
  percent: "percent",
  count: "count",
  days: "days",
};

function unitWord(unit: MeasureUnit): string {
  return unit === "cents" ? "US dollars" : unit === "percent" ? "percent" : unit;
}

function orderPhrase(spec: ChartSpec): string {
  const order = spec.order;
  if (order === undefined || order.basis === "wire") return "";
  if (order.basis === "axis-order")
    return order.by
      ? `, in the catalog's declared order for ${order.by}`
      : ", in the catalog's declared bucket order";
  if (order.basis === "ordinal-bucket") return ", ordered by bucket";
  const direction = order.descending === false ? "low to high" : "high to low";
  return order.by ? `, ordered by ${order.by} ${direction}` : `, ordered ${direction}`;
}

/**
 * The chart's own rows, in the unit the chart draws them in — with the
 * caveats above them.
 *
 * The unit is named in the column heading rather than baked into the cell,
 * so the numbers stay summable: a `ratio` frame is scaled to percentage
 * points once at the wire seam and exported as `29.5`, under a heading
 * that says `percent`. A row whose value the engine withheld exports as an
 * EMPTY cell with a `withheld` note — never as a zero, which is a number
 * the engine did not publish and a payer meeting would act on.
 *
 * Above the header row sits the same preamble `answerToText` writes, as
 * `#` comment lines: window, scope, data load, ordering, and a caveats
 * block that prints "no caveats were attached" rather than vanishing. This
 * is the export most likely to reach a payer or a board deck, and it was
 * the one artifact in the product carrying a hundred and fifty rows of
 * suppression ceilings with nothing on the sheet saying so. Every comment
 * line goes through `csvCell`, so a caveat sentence beginning with "=" is
 * text in a spreadsheet rather than a formula.
 */
export function chartToCsv(spec: ChartSpec, meta?: ChartCsvMeta): string {
  const suffix = UNIT_NAME[spec.unit];
  const bounded = spec.rows.some((row) => row.bounded === true);
  const hasBound = spec.rows.some((row) => row.bound !== undefined);
  const hasDenominator = spec.rows.some((row) => row.denominator !== undefined);
  const hasProvisional = spec.rows.some((row) => row.provisional === true);

  const preamble: string[] = [];
  const say = (text: string): void => {
    preamble.push(text);
  };
  // When the wire's rows were not uniquely keyed by the axes this chart
  // declares, the FILE carries the rows as they arrived — long format, one
  // line per wire row — not the collapsed cells the picture draws. An
  // export built from the collapsed cells would repeat the picture's
  // understatement (live: $848.50 written into a May cell whose true total
  // was $160,744.15) with a provenance block on top of it.
  const wire = spec.keying;

  say(`Revi — ${spec.title}`);
  if (meta?.question) say(`Question: ${meta.question}`);
  if (meta?.windowLabel) say(`Window: ${meta.windowLabel}`);
  say(
    wire
      ? `${wire.rows.length} row${wire.rows.length === 1 ? "" : "s"} as the server sent them, in ${unitWord(spec.unit)}${orderPhrase(spec)}`
      : `${spec.rows.length} row${spec.rows.length === 1 ? "" : "s"} × ${spec.series.length} series, in ${unitWord(spec.unit)}${orderPhrase(spec)}`,
  );
  if (wire) say(wire.note);
  if (spec.truncation && spec.truncation.total > spec.truncation.shown) {
    say(
      `Truncated at the source: ${spec.truncation.shown} of ${spec.truncation.total} categories are here.`,
    );
  }
  if (meta?.renderNote) say(`On screen: ${meta.renderNote}. This file has every series.`);
  if (bounded || hasBound) {
    say(
      "Some rows are LIMITS, not measurements: too few things sit behind them to measure exactly, so the real figure is at or below what is shown. The `bounded` column marks them. A limit cannot be ranked against a measurement — ordering the two together sorts by how big each group is.",
    );
  }
  if (hasProvisional) {
    say(
      "Some rows are PROVISIONAL: the bucket is calendar-partial or still adjudicating, so its value will move. The `provisional` column marks them.",
    );
  }
  if (spec.rows.some((row) => Object.values(row.cells ?? {}).some((cell) => cell.absent === true))) {
    say(
      "Some cells are EMPTY because the category exists in one of the two windows and not the other. The comparison joins the windows and fills the missing side with a zero; that zero is the join, not a measurement, so it is not written here.",
    );
  }
  if (meta?.watermarkId) say(`Data as of: ${meta.watermarkId}`);
  if (meta?.packLabel) say(`Metric definitions: ${meta.packLabel}`);
  say("CAVEATS THAT TRAVEL WITH THESE NUMBERS");
  if (!meta?.caveats || meta.caveats.length === 0) {
    say(NO_CAVEATS_LINE);
  } else {
    for (const caveat of meta.caveats) say(caveat);
  }
  const provenance = ["Revi"];
  if (meta?.investigationId) provenance.push(`investigation ${meta.investigationId}`);
  if (meta?.watermarkId) provenance.push(`data load ${meta.watermarkId}`);
  provenance.push(`exported ${isoInstant(meta?.exportedAt ?? new Date())}`);
  say(provenance.join(" · "));
  say(
    "These numbers are as of the data load named above. Re-running the same question against a newer load can change them.",
  );

  const category = meta?.windowLabel
    ? `${spec.xLabel ?? "category"} (${meta.windowLabel})`
    : (spec.xLabel ?? "category");

  const headers = wire
    ? [
        category,
        // The series column is a COLUMN here, not a header per series: the
        // rows are long-format because that is the only shape in which
        // rows the declared axes cannot tell apart stay distinguishable.
        wire.seriesColumn ?? "series",
        "referent",
        `value (${suffix})`,
        ...(wire.rows.some((row) => row.bounded === true) ? ["bounded"] : []),
        ...(wire.rows.some((row) => row.bound !== undefined) ? [`bound (${suffix})`] : []),
        ...(wire.rows.some((row) => row.denominator !== undefined) ? ["denominator"] : []),
        ...(wire.rows.some((row) => row.provisional === true) ? ["provisional"] : []),
      ]
    : [
        category,
        "referent",
        ...spec.series.map((s) => `${s.label} (${suffix})`),
        // Emitted only when the wire carries them: an always-empty column
        // trains a reader to ignore it before it ever means anything.
        ...(bounded || hasBound ? ["bounded"] : []),
        ...(hasBound ? [`bound (${suffix})`] : []),
        ...(hasDenominator ? ["denominator"] : []),
        ...(hasProvisional ? ["provisional"] : []),
      ];

  const scaled = (value: number): number =>
    // Money is exported in dollars for the same reason the worklist is:
    // integer cents in a spreadsheet column reads as a 100× error.
    spec.unit === "cents"
      ? Math.round(value) / 100
      : // A `ratio` frame is scaled ×100 at the wire seam, and binary
        // floating point turns 0.229167 into 22.916700000000002. Twelve
        // significant digits is far more precision than any governed metric
        // carries and exactly enough to erase the epsilon — the published
        // value is unchanged, its representation stops leaking the
        // arithmetic that got it here.
        Number(value.toPrecision(12));

  const rows: CsvValue[][] = wire
    ? wire.rows.map((row) => [
        row.x,
        row.series,
        row.referent ?? "",
        typeof row.value === "number" && Number.isFinite(row.value) ? scaled(row.value) : "",
        ...(wire.rows.some((r) => r.bounded === true) ? [row.bounded === true ? "TRUE" : ""] : []),
        ...(wire.rows.some((r) => r.bound !== undefined)
          ? [row.bound === undefined ? "" : scaled(row.bound)]
          : []),
        ...(wire.rows.some((r) => r.denominator !== undefined) ? [row.denominator ?? ""] : []),
        ...(wire.rows.some((r) => r.provisional === true)
          ? [row.provisional === true ? "TRUE" : ""]
          : []),
      ])
    : spec.rows.map((row) => [
        row.label,
        row.referent ?? "",
        ...spec.series.map((s) => {
          const value = row.values[s.key];
          // A cell the engine zero-filled across the join is left EMPTY,
          // like a withheld one: the figure draws no bar for it, and a
          // spreadsheet column of comparisons in which an absence reads as
          // 0.00 is the one surface most likely to be summed.
          if (row.cells?.[s.key]?.absent === true) return "";
          if (typeof value !== "number" || !Number.isFinite(value)) return "";
          return scaled(value);
        }),
        ...(bounded || hasBound ? [row.bounded === true ? "TRUE" : ""] : []),
        ...(hasBound ? [row.bound === undefined ? "" : scaled(row.bound)] : []),
        ...(hasDenominator ? [row.denominator ?? ""] : []),
        ...(hasProvisional ? [row.provisional === true ? "TRUE" : ""] : []),
      ]);

  return buildCsv(headers, rows, preamble);
}

/* ------------------------------------------------------------------ */
/* Browser side-effects (the only impure functions here)               */
/* ------------------------------------------------------------------ */

/**
 * A filename that survives a file system and a Slack upload: lowercase,
 * ASCII, dashes, and the data load in it — two exports of the same
 * worklist at different watermarks are different documents and must not
 * overwrite one another in a downloads folder.
 *
 * The watermark is a REQUIRED parameter for exactly that reason. It used to
 * be documented as the point of the function and passed by nobody: the
 * worklist happened to send its watermark as the free-text `tag`, and the
 * chart sent its title — so yesterday's chart export and today's landed on
 * the same name and the newer one silently replaced the older. A caller
 * with genuinely no pin passes `undefined` and gets a name with no load in
 * it, which is at least visibly missing rather than quietly wrong.
 */
/* ------------------------------------------------------------------ */
/* Deep research — the whole report, in one file                       */
/* ------------------------------------------------------------------ */

/**
 * THE REPORT AS ROWS, WITH EVERY QUALIFICATION ABOVE THEM.
 *
 * One long-format sheet rather than six: `section` says which part of the
 * report a row came from — the headline, a stratum, a rate cell, a
 * timeliness band, a side of the filing deadline, a contrast arm — so a
 * spreadsheet can pivot the whole artifact and nothing has to be exported
 * twice to be complete. The alternative (a file per zone) is how an
 * analyst ends up with the strata and not the censoring, or the rates and
 * not the intervals.
 *
 * FOUR RULES, and each one is a way this file could lie.
 *
 *   A REFUSED RATE EXPORTS AS AN EMPTY CELL, never as a zero. `evidence`
 *     carries `not_estimable` beside it, so a reader who sorts on the rate
 *     column cannot mistake the gap for a floor. This is the same rule the
 *     chart CSV follows for a withheld cell, for the same reason: a
 *     spreadsheet-shaped thing is the artifact a reader trusts without
 *     checking.
 *   EVERY INTERVAL TRAVELS BESIDE ITS POINT ESTIMATE. A column of expected
 *     dollars with the bounds left in the browser is the flattering half
 *     of this product's own answer.
 *   MONEY IS A NUMBER, NOT A STRING. Cents become plain decimal dollars
 *     (`centsToDollars`) because a CSV column is going to be summed; the
 *     currency is named once, in the heading.
 *   THE CAVEATS ARE THE PREAMBLE, INCLUDING THE CENSORING. The statements
 *     that say which denials are in the denominator and which are counted
 *     in neither the wins nor the losses are what make every rate below
 *     readable, and they are the first thing in the file.
 */
export function researchReportToCsv(
  report: ResearchReport,
  meta: { runId?: string; exportedAt?: Date } = {},
): string {
  const headline = report.headline;
  const preamble: string[] = [];
  const say = (line: string): void => {
    preamble.push(line);
  };

  say(`Revi — deep research: ${report.research_question}`);
  say(`Population: ${populationLabel(report.population)}`);
  say(`Data load: ${report.data_load_label} (data through ${report.data_edge_date})`);
  if (meta.runId) say(`Run: ${meta.runId}`);
  say(
    `Expected recoverable: ${formatCents(headline.total_expected_cents)}, between ` +
      `${formatCents(headline.total_expected_interval.low_cents)} and ` +
      `${formatCents(headline.total_expected_interval.high_cents)} ` +
      `(${headline.total_expected_interval.confidence} confidence)`,
  );
  say(
    `Priced over ${formatCents(headline.priced_open_dollars_cents)} of ` +
      `${formatCents(headline.total_open_dollars_cents)} open across ` +
      `${headline.total_open_denials} denials; ` +
      `${formatCents(headline.unpriced_open_dollars_cents)} could not be priced.`,
  );
  if (headline.range_assumes_independence) {
    say(
      "The range around the total is the sum of each population's own range. Populations that share payers, staffing and seasons move together, so read it as a spread rather than a guarantee.",
    );
  }
  for (const statement of report.censoring.statements ?? []) say(statement);
  for (const note of report.context_notes ?? []) say(note);

  const warnings: WarningEvent[] = (report.warnings ?? []).map((warning) => ({
    type: "warning",
    code: warning.code,
    message: warning.message,
    severity: warning.severity,
    ...(warning.count !== undefined ? { count: warning.count } : {}),
  }));
  const caveats = caveatLines(warnings);
  if (caveats.length === 0) say(NO_CAVEATS_LINE);
  for (const line of caveats) say(line);
  say(`Exported ${(meta.exportedAt ?? new Date()).toISOString()}`);

  const headers = [
    "section",
    "population",
    "basis",
    "evidence",
    "rate",
    "rate_ci_low",
    "rate_ci_high",
    "rate_confidence",
    "denials_answered",
    "recovered",
    "open_denials",
    "open_denied_usd",
    "inside_deadline_usd",
    "past_deadline_usd",
    "no_limit_on_file_usd",
    "expected_usd",
    "expected_low_usd",
    "expected_high_usd",
  ];

  const cellCells = (cell: ResearchRateCell | undefined): CsvValue[] => {
    if (cell === undefined) return ["", "", "", "", "", "", "", ""];
    const measured = cell.evidence === "measured";
    return [
      cell.basis,
      cell.evidence,
      // EMPTY, not zero: a rate the run declined to publish has no number.
      measured ? (decimal(cell.rate) ?? "") : "",
      measured ? (decimal(cell.interval?.low) ?? "") : "",
      measured ? (decimal(cell.interval?.high) ?? "") : "",
      measured ? (cell.interval?.confidence ?? "") : "",
      cell.n,
      cell.successes,
    ];
  };

  const rows: CsvValue[][] = [];

  rows.push([
    "headline",
    populationLabel(report.population),
    report.censoring.basis,
    "measured",
    "",
    "",
    "",
    headline.total_expected_interval.confidence,
    report.censoring.in_denominator,
    "",
    headline.total_open_denials,
    centsToDollars(headline.total_open_dollars_cents),
    centsToDollars(headline.catchable_dollars_cents),
    centsToDollars(headline.deadline_passed_dollars_cents),
    centsToDollars(headline.deadline_unknown_dollars_cents),
    centsToDollars(headline.total_expected_cents),
    centsToDollars(headline.total_expected_interval.low_cents),
    centsToDollars(headline.total_expected_interval.high_cents),
  ]);

  for (const stratum of [...(report.strata ?? []), ...(report.not_estimable ?? [])]) {
    const measured = stratum.evidence === "measured";
    rows.push([
      "stratum",
      stratum.label,
      ...cellCells(stratum.rate_cell),
      stratum.open_denials,
      centsToDollars(stratum.open_dollars_cents),
      centsToDollars(stratum.catchable_dollars_cents),
      centsToDollars(stratum.deadline_passed_dollars_cents),
      centsToDollars(stratum.deadline_unknown_dollars_cents),
      measured ? centsToDollars(stratum.expected_cents ?? undefined) : "",
      measured ? centsToDollars(stratum.expected_interval?.low_cents) : "",
      measured ? centsToDollars(stratum.expected_interval?.high_cents) : "",
    ]);
  }

  for (const cell of report.rates ?? []) {
    rows.push(["rate", cell.label, ...cellCells(cell), "", "", "", "", "", "", "", ""]);
  }

  for (const band of report.timeliness?.bands ?? []) {
    rows.push(["timeliness", band.band, ...cellCells(band.cell), "", "", "", "", "", "", "", ""]);
  }

  for (const row of report.deadline?.rows ?? []) {
    rows.push([
      "deadline",
      `${row.position_label} / ${row.rule_label}`,
      ...cellCells(row.cell),
      "",
      "",
      "",
      "",
      "",
      "",
      "",
      "",
    ]);
  }

  for (const contrast of report.contrasts ?? []) {
    for (const [side, arm] of [
      ["left", contrast.left],
      ["right", contrast.right],
    ] as const) {
      rows.push([
        "contrast",
        `${contrast.title} — ${side === "left" ? "stronger" : "weaker"} side: ${arm.label}`,
        report.censoring.basis,
        arm.rate === null || arm.rate === undefined ? "not_estimable" : "measured",
        decimal(arm.rate) ?? "",
        decimal(arm.interval?.low) ?? "",
        decimal(arm.interval?.high) ?? "",
        arm.interval?.confidence ?? "",
        arm.n,
        arm.successes,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
      ]);
    }
  }

  return buildCsv(headers, rows, preamble);
}

/**
 * A RESEARCH STUDY AS A FILE — every group, including the ones no figure
 * could be published for.
 *
 * The export is the surface with no register: `docs/client-language.md`
 * exempts it and `AGENTS.md` requires it to keep full fidelity forever.
 * So each row carries what the screen carries plus what the screen puts
 * behind a mark — the evidence tier, the raw breakdown value the data
 * holds, the population behind a rate, and the read that produced it.
 *
 * A WITHHELD FIGURE IS EMPTY, NEVER ZERO. Writing 0 where a rate was not
 * published turns a disclosure into a measurement the moment somebody
 * opens the file in a spreadsheet and sums the column.
 */
export function researchStudyToCsv(
  study: ResearchStudy,
  meta: { runId?: string; exportedAt?: Date } = {},
): string {
  const preamble: string[] = [];
  const say = (line: string): void => {
    preamble.push(line);
  };

  say(`Revi — deep research study: ${study.research_question}`);
  say(`Reads: ${study.population_label || populationLabel(study.population)}`);
  say(`Period: ${study.window_label}`);
  say(`Data load: ${study.data_load_label} (data through ${study.data_edge_date})`);
  if (meta.runId) say(`Run: ${meta.runId}`);
  if (study.determination?.statement) say(study.determination.statement);
  say(
    `Readings: ${study.readings?.length ?? 0} over ` +
      `${study.walk?.rounds_taken ?? 1} of ${study.walk?.rounds_allowed ?? 1} rounds, ` +
      `chosen by ${study.walk?.authored_by === "model" ? "the analysis planner" : "Revi's standing opening read"}.`,
  );
  for (const choice of study.path_choices ?? []) say(choice.statement);
  if (study.knowledge_statement) say(study.knowledge_statement);
  for (const statement of study.censoring?.statements ?? []) say(statement);
  for (const round of study.walk?.rounds ?? []) {
    for (const step of round.steps ?? []) {
      say(`Round ${round.index} — ${step.action} ${step.subject}: ${step.reason}`);
    }
  }

  const warnings: WarningEvent[] = (study.warnings ?? []).map((warning) => ({
    type: "warning",
    code: warning.code,
    message: warning.message,
    severity: warning.severity,
    ...(warning.count !== undefined ? { count: warning.count } : {}),
  }));
  const caveats = caveatLines(warnings);
  if (caveats.length === 0) say(NO_CAVEATS_LINE);
  for (const line of caveats) say(line);
  say(`Exported ${(meta.exportedAt ?? new Date()).toISOString()}`);

  const headers = [
    "reading",
    "round",
    "shape",
    "measure",
    "unit",
    "reason",
    "settled",
    "group",
    "breakdown",
    "breakdown_value",
    "evidence",
    "value",
    "displayed",
    "is_ceiling",
    "withheld",
    "population",
    "successes",
    "interval_low",
    "interval_high",
    "interval_confidence",
    "measured_on",
    "window",
    "read_fingerprint",
  ];

  const rows: CsvValue[][] = [];
  for (const reading of study.readings ?? []) {
    const shared: CsvValue[] = [
      reading.title,
      reading.round ?? 0,
      reading.shape,
      reading.measure_label,
      reading.unit,
      reading.reason,
      reading.settled ?? "",
    ];
    const tail: CsvValue[] = [
      reading.basis_label ?? "",
      reading.window_label ?? "",
      reading.read_fingerprint ?? "",
    ];
    if ((reading.figures ?? []).length === 0) {
      rows.push([
        ...shared,
        reading.refusal || "(no figure published)",
        "",
        "",
        "not_estimable",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        ...tail,
      ]);
      continue;
    }
    for (const figure of reading.figures ?? []) {
      const measured = figure.evidence === "measured";
      const part = (figure.parts ?? [])[0];
      rows.push([
        ...shared,
        figure.label,
        part?.dimension_label ?? "",
        part?.value ?? "",
        figure.evidence,
        // EMPTY, not zero: a figure the run declined to publish has no
        // number, and a ceiling's number is not the figure.
        measured ? (decimal(figure.value) ?? "") : "",
        figure.display,
        figure.bounded ? "yes" : "no",
        figure.withheld ? "yes" : "no",
        figure.population ?? "",
        figure.successes ?? "",
        decimal(figure.interval?.low) ?? "",
        decimal(figure.interval?.high) ?? "",
        figure.interval?.confidence ?? "",
        ...tail,
      ]);
    }
  }

  return buildCsv(headers, rows, preamble);
}


export function exportFilename(
  kind: string,
  tag: string | undefined,
  ext: string,
  watermarkId: string | undefined,
): string {
  const slug = (value: string): string =>
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48);
  const parts = ["revi", slug(kind), tag ? slug(tag) : "", watermarkId ? slug(watermarkId) : ""]
    .filter((p) => p !== "")
    // A tag that IS the watermark (the worklist's old convention) must not
    // print it twice.
    .filter((part, index, all) => all.indexOf(part) === index);
  return `${parts.join("-")}.${ext}`;
}

/**
 * Copy to the clipboard, reporting whether it worked.
 *
 * `navigator.clipboard` is unavailable on insecure origins and inside some
 * embedded browsers; the caller renders a failure rather than a checkmark
 * over nothing, because a button that always says "Copied" is worse than
 * one that sometimes says it could not.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the failure below — never a silent success.
  }
  return false;
}

/**
 * Save text as a file, entirely client-side: a Blob URL, a synthetic
 * click, and an immediate revoke. Nothing leaves this browser, which is
 * the whole point — the numbers are already here.
 */
export function downloadTextFile(filename: string, mime: string, text: string): void {
  if (typeof document === "undefined") return;
  const blob = new Blob([text], { type: `${mime};charset=utf-8` });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
