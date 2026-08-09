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

import { formatCount, formatWindow, mediumDate, type MeasureUnit } from "@/lib/format";
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

/** Header row + body rows as one CRLF-delimited CSV document. */
export function buildCsv(headers: readonly string[], rows: readonly CsvValue[][]): string {
  const lines = [headers.map(csvCell).join(",")];
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
  /** True when the turn was rebuilt from stored state, not watched live. */
  restored?: boolean;
  /** Injected so the output is a pure function of its inputs (tests pin it). */
  copiedAt?: Date;
}

const RULE = "—".repeat(4);

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

  if (input.findings.length > 0) {
    out.push("FINDINGS", RULE);
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

  // Never optional, never below the fold, never dropped when the list is
  // empty: "no caveats were attached" is itself a fact about the answer.
  out.push("CAVEATS THAT TRAVEL WITH THESE NUMBERS", RULE);
  if (input.warnings.length === 0) {
    out.push("The platform attached no caveats to this answer.");
  } else {
    const ordered = [
      ...input.warnings.filter((w) => w.severity === "caution"),
      ...input.warnings.filter((w) => w.severity !== "caution"),
    ];
    for (const warning of ordered) {
      const title = warningTitle(warning.code);
      const body = title ? warningBody(warning.code, warning.message) : warning.message;
      const times = warning.count && warning.count > 1 ? ` (raised ${warning.count} times)` : "";
      out.push(`[${warning.severity}] ${title ? `${title} — ` : ""}${body}${times}`);
    }
  }
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
export function chartToCsv(spec: ChartSpec, meta?: { windowLabel?: string }): string {
  const unitName: Record<MeasureUnit, string> = {
    cents: "usd",
    percent: "percent",
    count: "count",
    days: "days",
  };
  const suffix = unitName[spec.unit];
  const headers = [
    meta?.windowLabel ? `${spec.xLabel ?? "category"} (${meta.windowLabel})` : (spec.xLabel ?? "category"),
    "referent",
    ...spec.series.map((s) => `${s.label} (${suffix})`),
  ];
  const rows: CsvValue[][] = spec.rows.map((row) => [
    row.label,
    row.referent ?? "",
    ...spec.series.map((s) => {
      const value = row.values[s.key];
      if (typeof value !== "number" || !Number.isFinite(value)) return "";
      // Money is exported in dollars for the same reason the worklist is:
      // integer cents in a spreadsheet column reads as a 100× error.
      if (spec.unit === "cents") return Math.round(value) / 100;
      // A `ratio` frame is scaled ×100 at the wire seam, and binary
      // floating point turns 0.229167 into 22.916700000000002. Twelve
      // significant digits is far more precision than any governed metric
      // carries and exactly enough to erase the epsilon — the published
      // value is unchanged, its representation stops leaking the
      // arithmetic that got it here.
      return Number(value.toPrecision(12));
    }),
  ]);
  return buildCsv(headers, rows);
}

/* ------------------------------------------------------------------ */
/* Browser side-effects (the only impure functions here)               */
/* ------------------------------------------------------------------ */

/**
 * A filename that survives a file system and a Slack upload: lowercase,
 * ASCII, dashes, and the data load in it — two exports of the same
 * worklist at different watermarks are different documents and must not
 * overwrite one another in a downloads folder.
 */
export function exportFilename(kind: string, tag: string | undefined, ext: string): string {
  const slug = (value: string): string =>
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48);
  const parts = ["revi", slug(kind), tag ? slug(tag) : ""].filter((p) => p !== "");
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
