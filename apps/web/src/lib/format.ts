/**
 * Deterministic display formatting. Money arrives as integer cents (the
 * kernel's representation) and is formatted only at the last moment.
 * Signed values use the typographic minus U+2212 so columns align under
 * tabular numerals.
 */

import type { DateBasis, DirectionOfGood, ResolvedWindow } from "@/lib/types";

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
 * "Jul 27–Aug 2 (post date)".
 */
export function windowChipLabel(window: ResolvedWindow): string {
  return `${shortDate(window.start)}–${shortDate(window.end)} (${DATE_BASIS_LABELS[window.basis]})`;
}

/** Comparison chip form: "vs Jul 20–26" (same-month end collapses the month). */
export function comparisonChipLabel(window: ResolvedWindow): string {
  const a = parseIsoDate(window.start);
  const b = parseIsoDate(window.end);
  if (a.y === b.y && a.m === b.m) {
    return `vs ${MONTHS[a.m - 1]} ${a.d}–${b.d}`;
  }
  return `vs ${shortDate(window.start)}–${shortDate(window.end)}`;
}

/** Big count with grouping: 120000 → "120,000". */
export function formatCount(n: number): string {
  return new Intl.NumberFormat("en-US").format(n);
}
