/**
 * Deterministic display formatting. Money arrives as integer cents (the
 * kernel's representation) and is formatted only at the last moment.
 * Signed values use the typographic minus U+2212 so columns align under
 * tabular numerals.
 */

import type { TurnSubmission } from "@/lib/driver";
import { humanizeColumn } from "@/lib/humanize";
import type { DateBasis, DirectionOfGood, Refinement, ResolvedWindow } from "@/lib/types";

export const MINUS = "−";

const usd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** -9909308 → "−$99,093.08" */
export function formatCents(cents: number): string {
  const abs = usd.format(Math.abs(cents) / 100);
  return cents < 0 ? `${MINUS}${abs}` : abs;
}

/** Always signed: 370086 → "+$3,700.86", -9909308 → "−$99,093.08". */
export function formatSignedCents(cents: number): string {
  if (cents === 0) return usd.format(0);
  const abs = usd.format(Math.abs(cents) / 100);
  return cents < 0 ? `${MINUS}${abs}` : `+${abs}`;
}

/**
 * Whole dollars, no cents: 49326600 → "$493,266". Portfolio impact
 * figures are detector estimates rounded to the dollar by construction —
 * printing ".00" on them implies a precision the detection never had.
 */
export function formatWholeDollars(cents: number): string {
  const abs = wholeUsd.format(Math.round(Math.abs(cents) / 100));
  return cents < 0 ? `${MINUS}${abs}` : abs;
}

const wholeUsd = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 0,
  maximumFractionDigits: 0,
});

/** Compact for axis ticks: 132844152 → "$1.33M"; -9909308 → "−$99.1K". */
export function formatCompactCents(cents: number): string {
  const sign = cents < 0 ? MINUS : "";
  const dollars = Math.abs(cents) / 100;
  let body: string;
  if (dollars >= 1_000_000) {
    body = `$${trimZero((dollars / 1_000_000).toFixed(2))}M`;
  } else if (dollars >= 1_000) {
    body = `$${trimZero((dollars / 1_000).toFixed(1))}K`;
  } else {
    body = `$${trimZero(dollars.toFixed(0))}`;
  }
  return `${sign}${body}`;
}

function trimZero(s: string): string {
  return s.includes(".") ? s.replace(/\.?0+$/, "") : s;
}

/** 0.239 → "23.9%". Fraction in, percent string out. */
export function formatPct(fraction: number, digits = 1): string {
  const pct = Math.abs(fraction) * 100;
  const body = `${pct.toFixed(digits)}%`;
  return fraction < 0 ? `${MINUS}${body}` : body;
}

/** Always signed: -0.127155 → "−12.7%", 0.348 → "+34.8%". */
export function formatSignedPct(fraction: number, digits = 1): string {
  if (fraction === 0) return `0.0%`;
  const body = `${(Math.abs(fraction) * 100).toFixed(digits)}%`;
  return fraction < 0 ? `${MINUS}${body}` : `+${body}`;
}

/**
 * Direction-of-good semantics: the sign of the delta says which way the
 * number moved; the metric's convention says whether that is good.
 * A falling denial rate is GOOD (green) even though it is falling.
 */
export function deltaTone(
  delta: number,
  direction: DirectionOfGood,
): "good" | "bad" | "neutral" {
  if (delta === 0 || direction === "neutral") return "neutral";
  const rising = delta > 0;
  if (direction === "up_is_good") return rising ? "good" : "bad";
  return rising ? "bad" : "good";
}

/* ------------------------------------------------------------------ */
/* Dates. All ISO "YYYY-MM-DD" strings are parsed by hand — never via   */
/* `new Date(string)` — to avoid UTC/local drift.                      */
/* ------------------------------------------------------------------ */

const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
] as const;

interface Ymd {
  y: number;
  m: number;
  d: number;
}

export function parseIsoDate(iso: string): Ymd {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  if (!m) throw new Error(`not an ISO date: ${iso}`);
  return { y: Number(m[1]), m: Number(m[2]), d: Number(m[3]) };
}

/** "2026-07-27" → "Jul 27". */
export function shortDate(iso: string): string {
  const { m, d } = parseIsoDate(iso);
  return `${MONTHS[m - 1]} ${d}`;
}

/** "2026-07-27" → "Jul 27, 2026". */
export function mediumDate(iso: string): string {
  const { y, m, d } = parseIsoDate(iso);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

/**
 * An ISO timestamp as an age: "now", "12m", "3h", "5d", then a calendar
 * date. Coarse on purpose — a session list is scanned, not read, and the
 * exact instant is one `title` attribute away.
 *
 * `now` is a parameter so the output is a pure function of its inputs
 * (tests pin an instant; the caller passes `Date.now()`).
 */
export function relativeTime(iso: string, now: number = Date.now()): string {
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  const seconds = Math.max(0, Math.round((now - at) / 1000));
  if (seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  const date = new Date(at);
  return `${MONTHS[date.getMonth()]} ${date.getDate()}`;
}

export const DATE_BASIS_LABELS: Record<DateBasis, string> = {
  service: "service date",
  post: "post date",
  submission: "submission date",
  remit: "remit date",
  discharge: "discharge date",
};

/**
 * Full window rendering: "Jul 27 – Aug 2, 2026 (post date)".
 * Cross-year windows render both years: "Dec 29, 2025 – Jan 4, 2026 (post date)".
 */
export function formatWindow(window: ResolvedWindow): string {
  const a = parseIsoDate(window.start);
  const b = parseIsoDate(window.end);
  const basis = DATE_BASIS_LABELS[window.basis];
  if (a.y !== b.y) {
    return `${mediumDate(window.start)} – ${mediumDate(window.end)} (${basis})`;
  }
  return `${shortDate(window.start)} – ${shortDate(window.end)}, ${a.y} (${basis})`;
}

/**
 * The compact chip form used in the §10.3 verbatim header:
 * "Jul 27–Aug 2, 2026 (post date)".
 *
 * The YEAR is not optional, and dropping it was a live defect rather than
 * a tightening. On a year-over-year answer the header printed "WINDOW
 * Jan 1–Aug 2" beside "VS Jan 1–Aug 2" — two identical strings over two
 * different years — and the first honest reading of that is "it compared
 * the period to itself". One digit is the difference between a comparison
 * a reader can check and one they will not trust.
 */
export function windowChipLabel(window: ResolvedWindow): string {
  return `${bareRangeLabel(window.start, window.end)} (${DATE_BASIS_LABELS[window.basis]})`;
}

/** Comparison chip form: "vs Jul 20–26, 2026" — same rule, same years. */
export function comparisonChipLabel(window: ResolvedWindow): string {
  return `vs ${bareRangeLabel(window.start, window.end)}`;
}

/**
 * A date range with its years, as compact as it can be without losing one.
 *
 *   same month   "Jul 20–26, 2026"
 *   same year    "Jan 1–Aug 2, 2026"
 *   otherwise    "Dec 29, 2025–Jan 4, 2026"
 *
 * The month collapses when both ends share it; the year never collapses
 * out of the string entirely, only out of the first half of it.
 */
function bareRangeLabel(start: string, end: string): string {
  const a = parseIsoDate(start);
  const b = parseIsoDate(end);
  if (a.y !== b.y) return `${mediumDate(start)}–${mediumDate(end)}`;
  if (a.m === b.m) return `${MONTHS[a.m - 1]} ${a.d}–${b.d}, ${a.y}`;
  return `${shortDate(start)}–${shortDate(end)}, ${a.y}`;
}

/**
 * "2026-07-01..2026-07-31" → "Jul 1–31, 2026".
 *
 * The engine writes windows into finding titles and statements as ISO
 * literals, and a machine date literal on the surface a VP reads is the
 * same class of leak as a snake_case column id: "Atlas Commercial ranks
 * #1 of 12 measured by denied dollars over 2026-07-01..2026-07-31" is a
 * log line with a company name in it.
 *
 * Applied to the FACT surfaces — the rows on the answer and the rows in
 * the rail's Facts section, which are the same rows in two places. The
 * evidence bundle's own probe descriptions, the debug trace and every
 * export keep the engine's literal, because those are read by whoever has
 * to reproduce the query and an ISO date is the reproducible form.
 */
export function humanizeIsoDates(text: string): string {
  return text
    .replace(ISO_RANGE, (whole, start: string, end: string) => {
      try {
        return isoRangeLabel(start, end);
      } catch {
        return whole;
      }
    })
    .replace(ISO_DAY, (iso) => {
      try {
        return mediumDate(iso);
      } catch {
        return iso;
      }
    })
    .replace(ISO_MONTH, (whole, year: string, month: string) => {
      const index = Number(month) - 1;
      return MONTHS[index] === undefined ? whole : `${MONTHS[index]} ${year}`;
    });
}

/**
 * Strict on purpose: month 01–12, day 01–31.
 *
 * `2026-13-45` is not a date and re-spelling it would print
 * "undefined 45, 2026" over whatever the engine actually meant. A token
 * this cannot prove is a date is left exactly as it was written — the
 * same rule `isMeasureName` follows for identifiers.
 */
const ISO_DAY_SOURCE = "\\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\\d|3[01])";
const ISO_DAY = new RegExp(`\\b${ISO_DAY_SOURCE}\\b`, "g");
const ISO_RANGE = new RegExp(
  `\\b(${ISO_DAY_SOURCE})\\s*(?:\\.\\.|–|—|\\sto\\s|\\s-\\s)\\s*(${ISO_DAY_SOURCE})\\b`,
  "g",
);
const ISO_MONTH = /\b(\d{4})-(0[1-9]|1[0-2])\b(?!-)/g;

/** "2026-07-01", "2026-07-31" → "Jul 1–31, 2026". Throws on non-ISO input. */
export function isoRangeLabel(start: string, end: string): string {
  return bareRangeLabel(start, end);
}

/**
 * A data load, spelled as a date or not spelled at all.
 *
 * The server names one load two ways — an id (`wm_003`) and the instant the
 * loader finished ("2026-08-03 04:10") — and neither belongs inside a
 * sentence an analyst reads: the id is a log token and the minute is a
 * precision nobody acts on. This returns the calendar date when the value
 * carries one and `undefined` when it is only an id, so a caller can drop
 * the clause rather than print the token.
 *
 * The exact value is never lost: callers keep it on a `title`, and the
 * evidence drawer, the decision trace and every export still print it
 * verbatim, because those exist to reproduce a query.
 */
export function dataLoadDate(value: string | undefined): string | undefined {
  if (!value) return undefined;
  const m = /^(\d{4}-\d{2}-\d{2})/.exec(value.trim());
  if (!m) return undefined;
  try {
    return mediumDate(m[1]);
  } catch {
    return undefined;
  }
}

/**
 * "anomaly_priority@3" → "Anomaly priority v3"; "dollar_impact@1" →
 * "Dollar impact v1".
 *
 * The ranking formula's identifier is published on the worklist and the
 * portfolio rail, where it sits as a bare token beside a heading. It is a
 * real fact — two lists ranked by different versions are not comparable —
 * so it stays; it just stops being spelled the way the payload spells it.
 * Anything this cannot parse is returned unchanged rather than guessed at,
 * the same rule `humanizeIsoDates` follows.
 */
export function rankingVersionLabel(policy: string): string {
  const m = /^([a-z][a-z0-9_]*)@(\d+)$/.exec(policy.trim());
  return m ? `${humanizeColumn(m[1])} v${m[2]}` : policy;
}

/** Big count with grouping: 120000 → "120,000". */
export function formatCount(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}

/* ------------------------------------------------------------------ */
/* Measures: one formatter, shared by charts and finding stats         */
/* ------------------------------------------------------------------ */

/**
 * The display units `ChartSpec.unit` reduces to. `percent` values arrive
 * ALREADY scaled to percentage points — the 0–1 → 0–100 conversion happens
 * once, in `mapChartSpec`, against the published wire unit (`ratio`), so
 * that no renderer has to know which convention its numbers came in on.
 */
export type MeasureUnit = "cents" | "percent" | "count" | "days";

/** 7.9945 percent → "8.0%"; 5.34 days → "5.3 d"; 4893861 cents → "$48,938.61". */
export function formatMeasure(value: number, unit: MeasureUnit, digits = 1): string {
  switch (unit) {
    case "cents":
      return formatCents(value);
    case "percent": {
      const body = `${Math.abs(value).toFixed(digits)}%`;
      return value < 0 ? `${MINUS}${body}` : body;
    }
    case "days": {
      const body = `${Math.abs(value).toFixed(digits)} d`;
      return value < 0 ? `${MINUS}${body}` : body;
    }
    case "count":
      return formatCount(value);
  }
}

/**
 * A MOVEMENT in a measure's own unit, always signed.
 *
 * A rate's movement is in percentage POINTS, and "+2.1%" beside a relative
 * "+50.0%" on the same tooltip line is two different quantities wearing
 * one symbol — so a percent measure's delta says `pp` and only the
 * relative change keeps the `%`.
 */
export function formatMeasureDelta(value: number, unit: MeasureUnit, digits = 1): string {
  if (unit === "cents") return formatSignedCents(value);
  if (unit === "count") {
    if (value === 0) return "0";
    return `${value < 0 ? MINUS : "+"}${formatCount(Math.abs(value))}`;
  }
  if (value === 0) return unit === "percent" ? "0.0pp" : "0.0 d";
  const body = `${Math.abs(value).toFixed(digits)}${unit === "percent" ? "pp" : " d"}`;
  return `${value < 0 ? MINUS : "+"}${body}`;
}

/** The axis-tick form: compact money, everything else as-is but terse. */
export function formatMeasureTick(value: number, unit: MeasureUnit): string {
  switch (unit) {
    case "cents":
      return formatCompactCents(value);
    case "percent":
      return `${trimZero(value.toFixed(1))}%`;
    case "days":
      return `${trimZero(value.toFixed(1))}d`;
    case "count":
      return formatCount(value);
  }
}

/**
 * The window as a chart subtitle: "Jul 2026" when it is exactly one
 * calendar month, else "May 6 – Aug 3, 2026". Charts get the window in
 * their title because a chart read on its own — screenshotted into a deck,
 * scrolled past the header — otherwise carries no period at all.
 */
export function chartWindowLabel(window: ResolvedWindow): string {
  const a = parseIsoDate(window.start);
  const b = parseIsoDate(window.end);
  if (a.y === b.y && a.m === b.m && a.d === 1 && b.d === lastDayOfMonth(b.y, b.m)) {
    return `${MONTHS[a.m - 1]} ${a.y}`;
  }
  if (a.y !== b.y) return `${mediumDate(window.start)} – ${mediumDate(window.end)}`;
  return `${shortDate(window.start)} – ${shortDate(window.end)}, ${a.y}`;
}

function lastDayOfMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/**
 * Terse display form of a typed refinement operator — the user-bubble and
 * lineage-edge rendering of gesture turns: `DrillInto(F2)`, `RankBy(…)`.
 */
export function describeRefinement(refinement: Refinement): string {
  switch (refinement.op) {
    case "SetDimensions":
      return `SetDimensions(${refinement.dimensions.join(", ")})`;
    case "AddFilter":
      return `AddFilter(${refinement.filter.dimension} ${refinement.filter.op} ${refinement.filter.values.join("|")})`;
    case "RemoveFilter":
      return `RemoveFilter(${refinement.dimension})`;
    case "SetWindow":
      return `SetWindow(${refinement.window.start}…${refinement.window.end})`;
    case "SetComparison":
      return refinement.comparison
        ? `SetComparison(${refinement.comparison.label ?? refinement.comparison.kind})`
        : "SetComparison(none)";
    case "SetGrain":
      return `SetGrain(${refinement.grain.entity})`;
    case "DrillInto":
      return `DrillInto(${Array.isArray(refinement.target) ? refinement.target.join(", ") : refinement.target})`;
    case "Pivot":
      return `Pivot(${refinement.measures.join(", ")})`;
    case "Explain":
      return `Explain(${refinement.target})`;
    case "RankBy":
      return `RankBy(${refinement.metric} ${refinement.descending ? "desc" : "asc"})`;
    case "Expand":
      return "Expand";
    case "ResetContext":
      return `ResetContext(keepPins=${String(refinement.keepPins)})`;
  }
}

/* ------------------------------------------------------------------ */
/* Turns with no freeform text of their own                            */
/* ------------------------------------------------------------------ */

const DIRECT_QUERY_LABEL = "Direct query";
const DRILL_DOWN_LABEL = "Drill-down";

/**
 * A turn's display label when it carries no utterance and no
 * clarification reply — a typed investigation spec (a portfolio card's
 * drill handle, or a click with no prior answer to refine) or refinement
 * operators alone (a click on a chart, finding or portfolio card that
 * narrows an existing answer — see the `emitRefinement` callers). Both
 * are legitimate ways to ask a question without typing one; this says
 * what happened in plain language.
 */
export function untitledTurnLabel(submission: Pick<TurnSubmission, "spec">): string {
  return submission.spec ? DIRECT_QUERY_LABEL : DRILL_DOWN_LABEL;
}

/**
 * Session titles arrive verbatim from the server — the session's first
 * question, unedited (`SessionSummary.title`). Two exact strings are the
 * server's own placeholders for a first turn that carried no freeform
 * text (see `untitledTurnLabel`), and read as internal shorthand in a
 * list meant to be scanned at a glance. This is a display-only
 * substitution for those two strings; every other title still renders
 * exactly as the server sent it.
 */
export function displaySessionTitle(title: string): string {
  switch (title) {
    case "(typed investigation)":
      return DIRECT_QUERY_LABEL;
    case "(typed gesture)":
      return DRILL_DOWN_LABEL;
    default:
      return title;
  }
}
