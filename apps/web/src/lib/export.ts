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
  formatCount,
  formatMeasure,
  formatWindow,
  mediumDate,
  type MeasureUnit,
} from "@/lib/format";
import type { PortfolioItem } from "@/lib/mock/portfolio";
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
  /** Injected so the output is a pure function of its inputs (tests pin it). */
  copiedAt?: Date;
}

const RULE = "—".repeat(4);

/**
 * The turn's warnings as the sentences an export prints — cautions first,
 * each one titled by its code and counted when it was raised more than
 * once.
 *
 * Shared rather than duplicated: the text copy and the chart CSV must
 * carry the SAME caveats, or the product has two accounts of one answer
 * and the reader has whichever one they exported.
 */
export function caveatLines(warnings: readonly WarningEvent[]): string[] {
  const ordered = [
    ...warnings.filter((w) => w.severity === "caution"),
    ...warnings.filter((w) => w.severity !== "caution"),
  ];
  return ordered.map((warning) => {
    const title = warningTitle(warning.code);
    const body = title ? warningBody(warning.code, warning.message) : warning.message;
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
      "The written analysis was not stored for this turn — the findings above and the context they were measured under are what the server kept.",
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
        "    ≤ marks an upper bound, not a measurement: the engine withheld a small numerator and published a ceiling over the population instead.",
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
  // empty: "no caveats were attached" is itself a fact about the answer.
  out.push("CAVEATS THAT TRAVEL WITH THESE NUMBERS", RULE);
  const caveats = caveatLines(input.warnings);
  out.push(...(caveats.length === 0 ? ["The platform attached no caveats to this answer."] : caveats));
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
  say(`Revi — ${spec.title}`);
  if (meta?.question) say(`Question: ${meta.question}`);
  if (meta?.windowLabel) say(`Window: ${meta.windowLabel}`);
  say(
    `${spec.rows.length} row${spec.rows.length === 1 ? "" : "s"} × ${spec.series.length} series, in ${unitWord(spec.unit)}${orderPhrase(spec)}`,
  );
  if (spec.truncation && spec.truncation.total > spec.truncation.shown) {
    say(
      `Truncated at the source: ${spec.truncation.shown} of ${spec.truncation.total} categories are here.`,
    );
  }
  if (meta?.renderNote) say(`On screen: ${meta.renderNote}. This file has every series.`);
  if (bounded || hasBound) {
    say(
      "Some rows are UPPER BOUNDS, not measurements: the engine withheld a small numerator and published a ceiling over the population instead. The `bounded` column marks them. A ceiling cannot be ranked against a measurement — ordering the two together sorts by population size.",
    );
  }
  if (hasProvisional) {
    say(
      "Some rows are PROVISIONAL: the bucket is calendar-partial or still adjudicating, so its value will move. The `provisional` column marks them.",
    );
  }
  if (meta?.watermarkId) say(`Data as of: ${meta.watermarkId}`);
  if (meta?.packLabel) say(`Metric definitions: ${meta.packLabel}`);
  say("CAVEATS THAT TRAVEL WITH THESE NUMBERS");
  if (!meta?.caveats || meta.caveats.length === 0) {
    say("The platform attached no caveats to this answer.");
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

  const headers = [
    meta?.windowLabel
      ? `${spec.xLabel ?? "category"} (${meta.windowLabel})`
      : (spec.xLabel ?? "category"),
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

  const rows: CsvValue[][] = spec.rows.map((row) => [
    row.label,
    row.referent ?? "",
    ...spec.series.map((s) => {
      const value = row.values[s.key];
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
