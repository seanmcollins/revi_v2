"use client";

import { Maximize2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { DownloadCsvButton } from "@/components/answer/AnswerActions";
import { MonitorThis } from "@/components/monitors/MonitorThis";
import { Button } from "@/components/ui/button";
import { capChartSeries, humanizeColumn, OTHERS_SERIES_KEY } from "@/lib/contract";
import { humanizeInline } from "@/lib/humanize";
import { chartToCsv } from "@/lib/export";
import {
  formatMeasure,
  formatMeasureDelta,
  formatMeasureTick,
  formatSignedPct,
  humanizeIsoDates,
} from "@/lib/format";
import { useSessionStore } from "@/lib/store";
import type { ChartCell, ChartSeries, ChartSpec } from "@/lib/types";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";
import { cn } from "@/lib/utils";

/**
 * TWO series are a comparison — current against its baseline — and they
 * keep the semantic pair the rest of the product uses.
 */
const ROLE_COLOR: Record<"current" | "baseline", string> = {
  current: "var(--chart-current)",
  baseline: "var(--chart-baseline)",
};

/**
 * THREE or more are identities, and identity needs a categorical palette:
 * eight validated slots, assigned in fixed order and never cycled (the
 * order is the colour-vision safety mechanism, not a preference). Anything
 * past the eighth is not given a ninth hue — `capChartSeries` has already
 * folded it into the rollup, which wears the neutral ink because it is not
 * an entity.
 */
const CATEGORICAL = [
  "var(--chart-cat-1)",
  "var(--chart-cat-2)",
  "var(--chart-cat-3)",
  "var(--chart-cat-4)",
  "var(--chart-cat-5)",
  "var(--chart-cat-6)",
  "var(--chart-cat-7)",
  "var(--chart-cat-8)",
] as const;

function seriesColors(series: readonly ChartSeries[]): Record<string, string> {
  const out: Record<string, string> = {};
  let slot = 0;
  for (const s of series) {
    if (s.key === OTHERS_SERIES_KEY) {
      out[s.key] = "var(--chart-cat-other)";
      continue;
    }
    out[s.key] =
      series.length <= 2
        ? ROLE_COLOR[s.role]
        : (CATEGORICAL[slot] ?? "var(--chart-cat-other)");
    slot += 1;
  }
  return out;
}

interface RowDatum {
  label: string;
  referent?: string;
  /** This row is a CEILING, not a measurement (see `ChartRow.bounded`). */
  bounded?: boolean;
  /** The population the ceiling was taken over. */
  denominator?: number;
  /** The bucket is calendar-partial or still adjudicating. */
  provisional?: boolean;
  /** The engine published this category with no value at all. */
  withheld?: boolean;
  /**
   * The same facts per MARK, keyed by series key — carried onto the datum
   * so the `<Cell>` loop and the tooltip can tell July's measurement from
   * June's ceiling inside one category (see `ChartRow.cells`).
   */
  cells?: Record<string, ChartCell>;
  [key: string]: string | number | boolean | Record<string, ChartCell> | undefined;
}

/**
 * TWO WINDOWS ARE DRAWN SIDE BY SIDE, NEVER ONE ON TOP OF THE OTHER.
 *
 * The engine emits one chart per comparison — 12 categories × {current,
 * prior} — and it declares that frame `stacked_bar`, which is the one
 * chart_type this shape must not honour: the annotation riding with the
 * rows ends "They are not summed", and a stacked column's height would be
 * July plus June, a number nobody computed and nobody asked for.
 *
 * Grouped is also what the pairing MEANS. Period is a presentation of one
 * measure, not a second dimension of it, so the two marks take the
 * product's existing current/baseline pair — this window in the primary
 * ink, the window it is compared against in the muted one — rather than
 * two categorical hues, which would read as two entities.
 */
function periodSeriesLabel(
  spec: ChartSpec,
  key: string,
  windows?: { current?: string; prior?: string },
): string | undefined {
  if (spec.comparison === undefined) return undefined;
  if (key === spec.comparison.currentKey) {
    return windows?.current ? `This window (${windows.current})` : "This window";
  }
  if (key === spec.comparison.priorKey) {
    return windows?.prior ? `Prior window (${windows.prior})` : "Prior window";
  }
  return undefined;
}

/* ------------------------------------------------------------------ */
/* Axis labels that name one entity each                               */
/* ------------------------------------------------------------------ */

/**
 * A machine enum reaching the axis as a category ("MEDICAL_NECESSITY").
 *
 * Only multi-word tokens are re-spelled. A bare all-caps token is at least
 * as likely to be an acronym the pack means literally (`COB`, `AUTH`,
 * `CO`) and "Cob" is not a better label than "COB" — humanizing it would
 * be this renderer deciding what a governed code means.
 */
const ENUM_TOKEN = /^[A-Z0-9]+(?:_[A-Z0-9]+)+$/;

function humanizeCategory(label: string): string {
  return ENUM_TOKEN.test(label) ? humanizeColumn(label.toLowerCase()) : label;
}

/**
 * A warehouse column reaching the figure as a LABEL — the axis caption,
 * a series name in the legend and the hover.
 *
 * BUG 1, on the figure. `xLabel` and the series keys are the wire's own
 * column names, and a single-series trend was captioned `denial_rate`
 * under a title reading "Denial rate by month". Re-spelled here, at the
 * render site and nowhere else, because these same strings name the CSV's
 * columns — an export whose header changed to suit a caption would be a
 * different file from the one the analyst has been diffing.
 *
 * Only tokens that are unmistakably identifiers are touched: a label with
 * no underscore is somebody's word ("payer", "month", "carc"), and
 * "+13 others" is this client's own rollup name.
 */
const WAREHOUSE_LABEL = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)+$/;

function displayLabel(label: string): string {
  // `humanizeInline`, not a plain lowercase: "AR over 90 %" is the
  // pack's own spelling and "ar over 90 %" is a typo of it.
  return WAREHOUSE_LABEL.test(label) ? humanizeInline(label) : label;
}

/**
 * Shorten from the MIDDLE, never from the identifying end.
 *
 * "Dr. Arden Riverstone (033)" cut to a prefix is "Dr. Arden …" — which is
 * what fifteen consecutive providers were called on one axis. The number
 * in the tail is the identity; so is the "MCO" that separates two payers.
 */
function elideMiddle(text: string, width: number): string {
  if (text.length <= width) return text;
  if (width <= 4) return `${text.slice(0, Math.max(1, width - 1))}…`;
  // Half the budget to the tail: the identifying end of a warehouse name
  // is the "(033)" or the "MCO", and it is what a prefix cut deletes.
  const tail = Math.max(3, Math.floor((width - 1) * 0.5));
  const head = Math.max(1, width - 1 - tail);
  return `${text.slice(0, head).trimEnd()}…${text.slice(-tail).trimStart()}`;
}

/** The ordinary cut: keep the beginning, mark the cut. "Bluestone Fed…" */
function elideTail(text: string, width: number): string {
  if (text.length <= width) return text;
  return `${text.slice(0, Math.max(1, width - 1)).trimEnd()}…`;
}

/**
 * How much wider an axis may go to avoid cutting out of the MIDDLE.
 *
 * A middle cut is the strongest tool here and the least readable
 * result — "Bluestone…ral PPO", "Federal M…Part A" — and it exists for
 * one case: names whose only distinguishing part is the tail. Where a
 * plain cut can tell the labels apart within a few more characters, a
 * few more characters is much the cheaper price, so this is the budget
 * that buys them. Beyond it, correctness wins and the middle cut stands.
 */
const PLAIN_CUT_BUDGET = 6;

/**
 * Axis ticks that never put two different entities under one label.
 *
 * The rule that shipped was `v.length > 11 ? v.slice(0, 10) + "…" : v`, and
 * live it printed "State Medi…" twice on one twelve-payer axis: slot 1 is
 * State Medicaid MCO at 29.5% — the answer's #1 finding — and slot 5 is
 * State Medicaid at 15.8%. On the 150-provider ranking every label in the
 * warehouse is "Dr. Firstname Lastname (NNN)", so the axis read as fifteen
 * groups of indistinguishable names with no identity recoverable for any
 * of them. This is a wrong-identity defect on the surface the product's
 * own pitch tells analysts to screenshot into a deck.
 *
 * So the width is not a constant: it starts at what the figure can fit and
 * GROWS until every drawn label is unique. Two labels that are still
 * indistinguishable at the ceiling are printed whole — an axis that is
 * cramped is a worse figure, an axis that is wrong is a worse decision.
 *
 * BUG 2 — and the cut is a PLAIN one wherever a plain one will do.
 *
 * The middle cut was applied unconditionally, so the thirty-plan filing
 * chart came out as "Bluestone…ral PPO", "Meridian …e PPO", "Federal
 * M…Part A": labels that are legible on neither end, on an axis whose
 * names are perfectly ordinary and mostly differ in their first fifteen
 * characters. A middle cut is for the case it was built for — "Dr. Arden
 * Riverstone (033)", where the tail is the identity — and nothing else.
 *
 * So each width is tried with a plain cut first, and the middle cut only
 * wins when no plain cut within `PLAIN_CUT_BUDGET` more characters can
 * tell the labels apart. One style per axis, never mixed: two cut shapes
 * on one row of ticks reads as a rendering fault.
 *
 * Returned as a map from the full label so the tooltip, the CSV and the
 * drill target keep using the identity the wire published; only the tick
 * is shortened.
 */
export function axisTickLabels(
  labels: readonly string[],
  base: number,
  max = 40,
): Map<string, string> {
  const unique = [...new Set(labels)];
  const spelled = new Map(unique.map((label) => [label, humanizeCategory(label)]));

  /** Every label shortened this way at this width — or null if two collide. */
  const attempt = (
    width: number,
    cut: (text: string, width: number) => string,
  ): Map<string, string> | null => {
    const short = new Map<string, string>();
    const seen = new Map<string, string>();
    for (const label of unique) {
      const text = cut(spelled.get(label) ?? label, width);
      const owner = seen.get(text);
      if (owner !== undefined && owner !== label) return null;
      seen.set(text, label);
      short.set(label, text);
    }
    return short;
  };

  let middle: { width: number; map: Map<string, string> } | null = null;
  for (let width = base; width <= max; width += 2) {
    const plain = attempt(width, elideTail);
    if (plain !== null) {
      // A plain cut works here. Take it unless it costs more than the
      // budget over a middle cut that already worked further back.
      if (middle === null || width <= middle.width + PLAIN_CUT_BUDGET) return plain;
      return middle.map;
    }
    if (middle === null) {
      const cut = attempt(width, elideMiddle);
      if (cut !== null) middle = { width, map: cut };
    }
  }
  if (middle !== null) return middle.map;
  // Nothing short enough tells them apart. The names go on whole.
  return new Map(unique.map((label) => [label, spelled.get(label) ?? label]));
}

/**
 * How much room a ROTATED axis needs on the left, in pixels.
 *
 * A tick at −35° with `textAnchor: "end"` runs down and to the LEFT of its
 * own category, so the FIRST label is the one that leaves the figure: live
 * on the twelve-payer ranking the leftmost tick read "ate Medicaid MCO" —
 * "St" cut off by the container's edge, on the axis whose whole job is
 * saying which payer each bar is. The margin was a constant 10px against
 * labels up to 40 characters long.
 *
 * So the gutter is measured from the label that has to fit:
 *
 *   width  ≈ 6.3px per character at the 12px tick size (the axis is set in
 *            the UI sans, whose average advance at 12px measures ~6.2-6.4);
 *   extent  = width × cos(35°) ≈ 0.82 × width, the horizontal run of the
 *            rotated text;
 *   room    = the 48px the y-axis already occupies to the left of the plot,
 *            which the tick may legitimately sweep under.
 *
 * Capped, because a gutter is bought from the plot: past `MAX` the figure
 * is being squeezed to fit a name, and the tick shortener — which grows
 * labels only until they are UNIQUE — has already made the label as short
 * as it can be told apart at.
 */
const TICK_CHAR_PX = 6.3;
const TICK_ROTATION_COS = 0.82;
const AXIS_Y_WIDTH = 48;
const MIN_AXIS_GUTTER = 10;
const MAX_AXIS_GUTTER = 72;

export function rotatedAxisGutter(firstTick: string | undefined): number {
  if (firstTick === undefined || firstTick === "") return MIN_AXIS_GUTTER;
  const extent = firstTick.length * TICK_CHAR_PX * TICK_ROTATION_COS;
  return Math.max(MIN_AXIS_GUTTER, Math.min(MAX_AXIS_GUTTER, Math.ceil(extent) - AXIS_Y_WIDTH));
}

/**
 * …AND HOW MUCH ROOM IT NEEDS BELOW, which is the other half of the same
 * geometry and was still a constant.
 *
 * The left gutter fix covered the label that leaves the figure sideways.
 * A −35° label also runs DOWN, by `width × sin(35°)`, and the axis was
 * given a fixed `height: 74` — so the moment the tick shortener let a name
 * through at its full 18 characters (plus a "≤ " ceiling mark and a " †"
 * withheld mark, which is 22), the bottom of the name was cut off by the
 * SVG's own edge.
 *
 * Measured across the captured corpus at all four container widths before
 * this existed: 421 clipped tick labels, worst 16px — "Northbridge
 * Comme…", "Summit Peak Medic…" and "Lakewood Medicaid…" on every payer
 * ranking the product draws, at every width including the widest. It is
 * the defect the owner saw.
 *
 *   drop   = width × sin(35°) ≈ 0.574 × width, the vertical run;
 *   line   = one 12px line box (~14px) × cos(35°), the tick's own depth;
 *   margin = the 6px `tickMargin` between the axis line and the text.
 *
 * The figure GROWS to hold it rather than the plot shrinking: the drawing
 * keeps the same 214px of vertical room it had at the old constant, and a
 * chart whose names need 100px of axis is 26px taller than one whose names
 * need 74. A ranking squeezed to fit its own labels is the fix that
 * produces the next screenshot.
 */
const TICK_ROTATION_SIN = 0.574;
const TICK_LINE_PX = 14;
const AXIS_TICK_MARGIN = 6;
/** The height the axis had as a constant — and still the floor. */
export const MIN_AXIS_HEIGHT = 74;
const MAX_AXIS_HEIGHT = 116;
/** Vertical room the drawing itself keeps, whatever the axis costs. */
export const PLOT_ROOM_PX = 214;

export function rotatedAxisHeight(ticks: readonly string[]): number {
  const longest = ticks.reduce((n, tick) => Math.max(n, tick.length), 0);
  if (longest === 0) return MIN_AXIS_HEIGHT;
  const drop = longest * TICK_CHAR_PX * TICK_ROTATION_SIN + TICK_LINE_PX * TICK_ROTATION_COS;
  // The 8px is not slack, it is measurement error paid for: 6.3px is the
  // AVERAGE advance, and a name of capitals and an ellipsis ("Northbridge
  // Comme…", "Summit Peak Medic…") runs wider than its average. At +4 the
  // sweep still cut 1-3px off those three labels; at +8 the corpus is
  // clean at every width with room to spare inside the cap.
  return Math.max(
    MIN_AXIS_HEIGHT,
    Math.min(MAX_AXIS_HEIGHT, Math.ceil(drop) + AXIS_TICK_MARGIN + 8),
  );
}

/**
 * A HORIZONTAL axis has two defects of its own, and one number fixes
 * both: how many characters a FLAT tick may take before its own band
 * cannot hold it.
 *
 * A flat tick is centred on its band. When the label is wider than the
 * band it collides with its neighbours — measured on the five-payer
 * underpayment chart in the Evidence rail: four colliding pairs, 111px of
 * overlap, one illegible smear where the payer names should be — and when
 * it is wider than the LAST band it also runs past the plot's right edge,
 * which is how "Veritas Comp Fund †" lost its dagger by 22px and
 * "Northbridge Commerc…" lost 14.
 *
 * Both are the same fact: the label does not fit the room it is drawn in.
 * A budget measured from the band ends both, and it ends them by
 * shortening the label rather than by buying room from the drawing — and
 * where the band cannot hold a NAME at all, `rotateTicks` takes over,
 * which is what rotation is for.
 *
 * `Infinity` when the container has not been measured yet (the first
 * paint, and any environment without `ResizeObserver`): an unmeasured
 * chart keeps exactly the behaviour it had before this existed, and the
 * observer corrects it on the next frame. A budget that guessed would be
 * worse than one that waits.
 *
 *   band  = the plot's width, less the y-axis, over the category count;
 *   pad   = 6px of air between two neighbouring labels;
 *   marks = the four characters `categoryTick` can add on top of the
 *           shortened name ("≤ " and " †").
 */
const TICK_BAND_PAD = 6;
const TICK_MARK_PAD = 4;
/** Below this a name is no longer a name, so the axis rotates instead. */
export const FLAT_TICK_FLOOR = 8;

export function flatTickBudget(plotWidth: number, categories: number): number {
  if (plotWidth <= 0 || categories <= 0) return Number.POSITIVE_INFINITY;
  const band = (plotWidth - AXIS_Y_WIDTH) / categories;
  return Math.floor((band - TICK_BAND_PAD) / TICK_CHAR_PX) - TICK_MARK_PAD;
}

/**
 * The mark treatment for a cell the engine published as a ceiling.
 *
 * Desaturated fill plus a dashed outline: the bar reads as an EDGE rather
 * than a quantity, and the difference survives a screenshot, a projector
 * and colour-vision deficiency, because none of it is carried by hue. The
 * "≤" on the axis tick and in the tooltip is the text half of the same
 * signal — relief, never colour alone.
 */
const BOUNDED_MARK = {
  fillOpacity: 0.2,
  strokeWidth: 1.5,
  strokeDasharray: "3 2",
} as const;

/**
 * Charts are live objects: clicking a bar emits a typed
 * `{op: "DrillInto", target}` refinement — no natural language in the
 * loop. Truncation is always surfaced ("showing top 8 of 12 — Expand").
 */
export function InvestigationChart({
  spec: published,
  turnId,
  windowLabel,
  comparisonWindows,
  watermarkId,
  packLabel,
  question,
  investigationId,
  caveats,
}: {
  spec: ChartSpec;
  turnId: string;
  /** The turn's window, appended to the composed title (see AnswerCard). */
  windowLabel?: string;
  /**
   * The two windows this turn compared, from its context header, when it
   * published a comparison — "Jul 2026" and "Jun 2026". The legend of a
   * period chart names them: "current"/"prior" is the engine's
   * bookkeeping, and a reader looking at a screenshot of a paired bar
   * chart is entitled to know which two months they are looking at.
   */
  comparisonWindows?: { current?: string; prior?: string };
  /** The data load these rows were measured at — it names the CSV file. */
  watermarkId?: string;
  /** The metric pack that defined the measure, for the CSV's provenance. */
  packLabel?: string;
  /** The question this chart answered. */
  question?: string;
  investigationId?: string;
  /** The turn's caveats, so the CSV cannot leave without them. */
  caveats?: readonly string[];
}) {
  const emitRefinement = useSessionStore((s) => s.emitRefinement);
  const reducedMotion = usePrefersReducedMotion();

  // What is DRAWN is capped; what is EXPORTED is everything. Thirty plan
  // series in two colours was unreadable, and a CSV that matched the
  // picture would have dropped the rows an analyst came for.
  const { spec, hiddenSeries, note: capNote } = useMemo(
    () => capChartSeries(published),
    [published],
  );
  const colors = useMemo(() => seriesColors(spec.series), [spec.series]);
  // A composition is drawn as one. Recharts stacks by shared `stackId`, and
  // a `stacked_bar` frame drawn without one claims a comparison the engine
  // never published. A COMPARISON is the mirror case and is never stacked
  // whatever chart_type it declares — see `periodSeriesLabel` above.
  const stackId =
    spec.stacked && spec.kind !== "line" && spec.comparison === undefined ? spec.id : undefined;
  const seriesLabel = (s: ChartSeries): string =>
    periodSeriesLabel(spec, s.key, comparisonWindows) ?? displayLabel(s.label);

  /**
   * Draw in ONCE, fast (260ms ease-out) — Recharts' 1.5s default is a
   * different product's motion language, and it re-runs on every data
   * change. `animationId` is pinned to the spec so a re-render (theme
   * flip, focus change) never replays the draw.
   */
  const drawIn = {
    isAnimationActive: !reducedMotion,
    animationDuration: 260,
    animationEasing: "ease-out",
    animationId: spec.id,
  } as const;

  const hasBounded = spec.rows.some((row) => row.bounded === true);
  const hasProvisional = spec.rows.some((row) => row.provisional === true);
  const hasWithheld = spec.rows.some((row) => row.withheld === true);

  /**
   * A mark that is not a measurement is not a point on a solid line.
   *
   * This began as the provisional fix — the engine publishes the sentence
   * ("the week of 2026-07-20 point is PROVISIONAL and is excluded from that
   * movement") and the wire carries the flag, and the path drew straight
   * through to it anyway. The bounded half never landed on the line at all:
   * `BOUNDED_MARK` lives in the BarChart branch's `<Cell>` loop, so a live
   * eight-month denial-rate series whose Jan/Feb/Mar/Apr/Jun points are
   * ceilings over 133, 93, 106, 143 and 111 records drew as one solid
   * measured trend ending in a spike to 76.9% that is a ceiling over
   * thirteen. That is the figure a CFO screenshots.
   *
   * The two are the same rendering problem — a point the engine did not
   * measure — so they take the same treatment. Each series is drawn TWICE
   * from the same rows: the measured key, blank on qualified buckets so the
   * solid path terminates at the last measured one, and a `…__qualified`
   * key carrying the qualified points PLUS their measured neighbours on
   * BOTH sides, so every segment that touches a ceiling is dashed and no
   * segment goes undrawn. (The old rule joined forwards only, which left
   * the segment LEAVING a provisional bucket drawn by neither series.)
   *
   * Written for the general case, not the terminal one: a censored middle
   * bucket is exactly as unmeasured as a censored last one.
   */
  const qualifiedKey = (key: string): string => `${key}__qualified`;
  const unmeasured = (row: { bounded?: boolean; provisional?: boolean } | undefined): boolean =>
    row?.bounded === true || row?.provisional === true;
  const hasQualified = hasBounded || hasProvisional;
  /**
   * A category the comparison window has and this one does not.
   *
   * The compare operator outer-joins the two windows and zero-fills, so
   * the wire carries a current value of 0 that nothing measured — and the
   * engine says exactly that in its own annotation ("their current mark is
   * an absence, not a measured zero"). A zero-height bar with the
   * measured-zero baseline tick under it would say the opposite: that this
   * payer's denials collapsed to nothing. So no mark is drawn, the axis
   * tick carries a "‡", and the tooltip and the legend say why.
   */
  const absent = (row: { cells?: Record<string, ChartCell> }, key: string): boolean =>
    row.cells?.[key]?.absent === true;
  const absentLabels = new Set(
    spec.rows
      .filter((row) => spec.series.some((s) => absent(row, s.key)))
      .map((row) => row.label),
  );

  const data: RowDatum[] = spec.rows.map((row, index) => {
    const datum: RowDatum = {
      label: row.label,
      referent: row.referent,
      ...(row.bounded === true ? { bounded: true } : {}),
      ...(row.denominator !== undefined ? { denominator: row.denominator } : {}),
      ...(row.provisional === true ? { provisional: true } : {}),
      ...(row.withheld === true ? { withheld: true } : {}),
      ...(row.cells !== undefined ? { cells: row.cells } : {}),
    };
    const pending = unmeasured(row);
    const joins = pending || unmeasured(spec.rows[index - 1]) || unmeasured(spec.rows[index + 1]);
    for (const [key, value] of Object.entries(row.values)) {
      // An absence is the one value that is NOT drawn: it is the only case
      // where the wire's number is an artifact of the join rather than a
      // reading, and `minPointSize` would floor it to a visible tick.
      if (absent(row, key)) continue;
      // BARS keep their value on the measured key whatever they are: a bar
      // is marked by its own fill (the `<Cell>` loop below), and blanking
      // the value drew a provisional bucket as no bar at all — a category
      // with nothing over it, which reads as a zero.
      if (!pending || spec.kind !== "line") datum[key] = value;
      if (spec.kind === "line" && hasQualified && joins) datum[qualifiedKey(key)] = value;
    }
    return datum;
  });

  const handleBarClick = (entry: unknown) => {
    const payload = (entry as { payload?: RowDatum }).payload;
    if (!payload) return;
    emitRefinement(
      { op: "DrillInto", target: payload.referent ?? `${spec.id}:${payload.label}` },
      { turnId, referent: payload.referent },
    );
  };

  // Values arrive in their DISPLAY unit: `mapChartSpec` scales a wire
  // `ratio` frame (0.079945) into percentage points (7.9945) once, at the
  // boundary, so no renderer has to know which convention it was handed.
  const formatValue = (value: number): string => formatMeasure(value, spec.unit);
  const formatTick = (value: number): string => formatMeasureTick(value, spec.unit);

  const axisProps = {
    stroke: "var(--chart-axis)",
    tickLine: false,
    axisLine: false,
    tick: { fontSize: 12, fill: "var(--chart-axis)" },
  } as const;

  // The axis says which categories are ceilings, in text. Colour and
  // outline carry it on the mark; the tick carries it for anyone reading
  // the labels, printing in greyscale, or looking at a screenshot.
  const boundedLabels = new Set(
    spec.rows.filter((row) => row.bounded === true).map((row) => row.label),
  );
  const provisionalLabels = new Set(
    spec.rows.filter((row) => row.provisional === true).map((row) => row.label),
  );
  const withheldLabels = new Set(
    spec.rows.filter((row) => row.withheld === true).map((row) => row.label),
  );
  /**
   * BUG 2 — horizontal labels wherever they fit.
   *
   * The rule was "more than six categories, rotate", which put a 35°
   * slant under axes whose labels are "0–30 days" and "Jan" — unreadable
   * for no reason, and the rotation is what pushes the first and last
   * label past the container's edge. Rotation is a cost, so it is paid
   * only when the labels cannot sit flat: short names across a modest
   * number of categories stay horizontal.
   *
   * Twelve characters over up to twelve categories is roughly the point
   * at which a 3xl-column figure runs out of room at the 12px tick size.
   */
  const longestLabel = spec.rows.reduce(
    (longest, row) => Math.max(longest, humanizeCategory(row.label).length),
    0,
  );
  /**
   * …AND THE SAME RULE, MEASURED AGAINST THE CONTAINER IT IS DRAWN IN.
   *
   * The rule above counts characters and categories and knows nothing
   * about width, so the same five-payer chart is comfortable in the answer
   * column and unreadable in the Evidence rail: measured at 308px, five
   * flat labels up to 20 characters each drew on top of one another —
   * 111px of overlap, four colliding pairs, one illegible grey smear where
   * the payer names should be. Twenty of the corpus's charts did it at one
   * width or another, and three of them did it at every width.
   *
   * So the tick budget is the BAND's, and it is measured: a flat label
   * that cannot fit its own band is shortened to fit, and when the band is
   * too narrow to hold a name at all (below eight characters, where a
   * payer is no longer identifiable) the axis rotates, which is what
   * rotation is for.
   */
  const plotRef = useRef<HTMLDivElement>(null);
  const [plotWidth, setPlotWidth] = useState(0);
  useEffect(() => {
    const node = plotRef.current;
    if (node === null || typeof ResizeObserver === "undefined") return;
    setPlotWidth(node.getBoundingClientRect().width);
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) setPlotWidth(entry.contentRect.width);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const tickBudget = flatTickBudget(plotWidth, data.length);
  const rotateTicks =
    spec.kind !== "line" &&
    ((data.length > 6 && !(longestLabel <= 12 && data.length <= 12)) ||
      (longestLabel > tickBudget && tickBudget < FLAT_TICK_FLOOR));
  const tickText = useMemo(
    () =>
      axisTickLabels(
        spec.rows.map((row) => row.label),
        rotateTicks
          ? 18
          : Math.max(4, Math.min(data.length > 8 ? 12 : 20, tickBudget)),
      ),
    [spec.rows, rotateTicks, data.length, tickBudget],
  );
  // Composed rather than exclusive: on a comparison one category can hold
  // a ceiling on one window and no figure at all on the other, and an
  // early return would print one of those two facts and drop the other.
  const categoryTick = (v: string): string => {
    const short = tickText.get(v) ?? v;
    const prefix = boundedLabels.has(v) ? "≤ " : "";
    const suffix = withheldLabels.has(v)
      ? " †"
      : absentLabels.has(v)
        ? " ‡"
        : provisionalLabels.has(v)
          ? "*"
          : "";
    return `${prefix}${short}${suffix}`;
  };

  /**
   * FEW CATEGORIES, REAL BARS.
   *
   * `maxBarSize` and a 34% category gap are written for the dense case —
   * twelve payers, and at the far end 150 providers — where an uncapped
   * bar becomes a slab and the eye reads a fence instead of heights. At
   * two or three categories the same two numbers ARE the defect: a
   * two-bar comparison in a ~700px plot drew 8–14px hairlines with three
   * hundred pixels of nothing between them, which on a projector reads as
   * a chart that failed to render rather than as data.
   *
   * So the band share has a floor as well as a ceiling. Below five
   * categories the gap closes to 20% and the cap opens to 64px — wide
   * enough to be a bar, still capped so a single category cannot paint a
   * quarter of the card. Nothing about the SCALE changes: this is the
   * width of the mark, never its height.
   */
  const fewCategories = data.length > 0 && data.length <= 4;
  const barCap = fewCategories
    ? 64
    : stackId
      ? 26
      : spec.series.length > 1
        ? 14
        : 22;

  // The room the FIRST tick needs to the left of the plot — it is the one
  // a rotated axis pushes past the card's edge, and it is the tick that
  // says which entity the first bar is.
  const leftGutter = rotateTicks
    ? rotatedAxisGutter(data[0] === undefined ? undefined : categoryTick(String(data[0].label)))
    : MIN_AXIS_GUTTER;
  // …and the room it needs BELOW, measured from the same labels. Both
  // gutters read the tick AS RENDERED — `categoryTick` adds the "≤"
  // ceiling mark and the "†"/"‡"/"*" marks, and those characters are the
  // ones that were being cut off.
  const renderedTicks = useMemo(
    () => data.map((row) => categoryTick(String(row.label))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [data, tickText, boundedLabels, withheldLabels, absentLabels, provisionalLabels],
  );
  const axisHeight = rotateTicks ? rotatedAxisHeight(renderedTicks) : MIN_AXIS_HEIGHT;

  const tooltipContent = (props: unknown) => (
    <ChartTooltipContent
      {...(props as TooltipRenderProps)}
      formatValue={formatValue}
      unit={spec.unit}
      {...(spec.comparison !== undefined ? { comparison: spec.comparison } : {})}
      seriesLabel={(key: string) =>
        periodSeriesLabel(spec, key, comparisonWindows) ??
        displayLabel(spec.series.find((s) => s.key === key)?.label ?? key)
      }
      seriesColor={(key: string) => colors[key] ?? "var(--chart-current)"}
    />
  );

  return (
    <figure className="rounded-lg border bg-card p-3.5">
      <figcaption className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-meta font-medium" title={spec.wireTitle}>
          {/* Composed from the frame's own columns ("Cash posted by
              payer"), not the engine's frame bookkeeping ("cash posted —
              cash by payer  compare"). The published title stays on the
              `title` attribute so the reduction is checkable. */}
          {spec.title}
          {windowLabel && (
            <span className="font-normal text-muted-foreground"> — {windowLabel}</span>
          )}
        </span>
        {/* A legend is present for every multi-series chart and names each
            series beside its swatch: three of the eight light-mode hues
            sit below 3:1 on white, and the rule for that is relief — the
            identity must never be carried by colour alone. The swatch is
            the cue; the label is the fact. */}
        {spec.series.length > 1 && (
          <span className="flex max-w-[60%] flex-wrap items-center justify-end gap-x-2.5 gap-y-0.5">
            {spec.series.map((s) => (
              <span
                key={s.key}
                className="flex items-center gap-1 text-micro text-muted-foreground"
              >
                <span className="size-2 rounded-[2px]" style={{ background: colors[s.key] }} />
                {seriesLabel(s)}
              </span>
            ))}
          </span>
        )}
      </figcaption>

      {/* Said above the picture, not in a tooltip: a reader who does not
          know eleven series were folded into one mark is reading a
          different chart from the one the data supports. */}
      {capNote && (
        <p className="mb-1.5 text-micro leading-snug text-muted-foreground">{capNote}</p>
      )}

      {/* The engine's own census of this figure. It rides on the wire as
          `annotations[0]` ("upper bounds: 4 of 12 marks are ceilings, not
          measurements…") and was being fed to a `ReferenceLine` as an x
          value, where it matched no category and drew nothing at all. */}
      {/* EVERY sentence, not the first one. A comparison publishes its own
          annotation ahead of the rest ("comparison: two series per
          category — current is this window and prior is the window it is
          compared against. They are not summed."), so reading index 0
          alone dropped the upper-bound census, the prior-only census and
          the withheld census on exactly the figures that carry them. */}
      {(spec.notes ?? (spec.note !== undefined ? [spec.note] : [])).map((note) => (
        <p key={note} className="mb-1.5 text-micro leading-snug text-warning">
          {note}
        </p>
      ))}

      {/* THE RANKING WAS REFUSED. Said above the picture, at full width,
          because the alternative is what shipped: a bar chart sorted by
          value 400px below a banner explaining that ordering ceilings
          against measurements sorts by population size. */}
      {spec.order?.refused && (
        <p className="mb-1.5 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-meta leading-snug">
          <span className="font-medium">No ranking is published on this answer.</span>{" "}
          <span className="text-muted-foreground">
            These marks are in the order the engine emitted them — read them as a set, not as a
            league table.
          </span>
        </p>
      )}

      {/* The rows the wire sent are not uniquely keyed by the axes this
          chart declares, and this measure cannot be added up — so there is
          no figure to draw and none is drawn. The rows are still in the
          CSV, exactly as they arrived. */}
      {spec.keying?.mode === "unkeyable" ? (
        <div className="rounded-md border border-dashed bg-surface-sunken/60 px-3 py-4 text-meta leading-snug text-muted-foreground">
          <p className="font-medium text-foreground">This chart is not drawn</p>
          <p className="mt-1">{spec.keying.note}</p>
        </div>
      ) : (
      // A little more room than the marks strictly need. 13rem was the
      // height at which a twelve-bar ranking became a picket fence; 14
      // gives the plot the same air the wider category gap gives it
      // across.
      //
      // A ROTATED axis is measured, not fixed: the figure keeps the same
      // 214px of drawing room and GROWS by whatever its own names need
      // below them (`rotatedAxisHeight`). At the old `h-72` the axis was a
      // constant 74px and 421 tick labels across the captured corpus were
      // cut off at the bottom — up to 16px, at every container width.
      <div
        ref={plotRef}
        className={cn("w-full", !rotateTicks && "h-56")}
        {...(rotateTicks ? { style: { height: PLOT_ROOM_PX + axisHeight } } : {})}
      >
        <ResponsiveContainer width="100%" height="100%">
          {spec.kind === "line" ? (
            // BUG 2 — the figure keeps its own padding. `preserveStartEnd`
            // draws the first and last tick, and both of them sit ON the
            // plot's edge: at the old zero margins the axis printed
            // "…s Medicaid HMO" with the rest of the name outside the
            // card. The margin is the gutter those two labels need.
            <LineChart data={data} margin={{ top: 8, right: 18, bottom: 0, left: 6 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              {/* The same tick vocabulary the bars use. A time axis whose
                  ceilings say nothing is the axis under the trend that drew
                  six of them as measured points. */}
              <XAxis
                dataKey="label"
                {...axisProps}
                interval="preserveStartEnd"
                padding={{ left: 8, right: 8 }}
                tickFormatter={categoryTick}
              />
              <YAxis {...axisProps} tickFormatter={formatTick} width={48} />
              <Tooltip
                content={tooltipContent}
                cursor={{ stroke: "var(--chart-axis)", strokeDasharray: "3 3", strokeOpacity: 0.4 }}
              />
              {spec.highlightLabel && (
                <ReferenceLine
                  x={spec.highlightLabel}
                  stroke="var(--warning)"
                  strokeDasharray="4 3"
                  strokeOpacity={0.7}
                />
              )}
              {spec.series.map((s) => (
                <Line
                  key={s.key}
                  dataKey={s.key}
                  name={seriesLabel(s)}
                  stroke={colors[s.key]}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                  {...drawIn}
                />
              ))}
              {/* Every segment that touches a mark the engine did not
                  measure. Dashed, hollow-dotted, and drawn through the
                  measured points on either side so the path joins up
                  without claiming a ceiling is a reading.
                  `legendType: none` — it is the same series in a different
                  state, not a second one. */}
              {hasQualified &&
                spec.series.map((s) => (
                  <Line
                    key={qualifiedKey(s.key)}
                    dataKey={qualifiedKey(s.key)}
                    name={`${seriesLabel(s)} (${hasBounded ? "upper bounds" : "provisional"})`}
                    stroke={colors[s.key]}
                    strokeWidth={2}
                    strokeDasharray="4 3"
                    strokeOpacity={0.75}
                    legendType="none"
                    dot={{ r: 3, strokeWidth: 1.5, fill: "var(--card)", stroke: colors[s.key] }}
                    activeDot={{ r: 4, strokeWidth: 0 }}
                    {...drawIn}
                  />
                ))}
            </LineChart>
          ) : (
            <BarChart
              data={data}
              // A rotated tick runs down and to the LEFT of its bar, so
              // the leftmost label needs a gutter the plot area does not
              // otherwise give it — and the rightmost needs one for its
              // ascender. See BUG 2.
              //
              // MEASURED from the label that has to fit, not a constant:
              // at the fixed 10px, the first tick of the twelve-payer
              // ranking rendered "ate Medicaid MCO" with its head outside
              // the card (`rotatedAxisGutter`).
              // THE WARMTH PASS, in geometry. A denser plot is not a more
              // informative one: at 26% category gap the bars were a
              // palisade, and the eye read the fence rather than the
              // heights. 34% lets each category breathe and makes the
              // 2-3px gap between a comparison's two bars legible as a
              // pair rather than as one thick mark.
              margin={
                rotateTicks
                  ? { top: 12, right: 16, bottom: 0, left: leftGutter }
                  : { top: 12, right: 14, bottom: 0, left: 0 }
              }
              barGap={3}
              barCategoryGap={fewCategories ? "20%" : "34%"}
            >
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              {/* `interval={0}` forced every one of 150 provider ticks to
                  draw into a 208px figure. Above what an axis can hold,
                  Recharts thins them and the tooltip carries the rest:
                  an unlabelled bar is a bar you have to hover, which is a
                  far smaller cost than fifteen bars wearing one name. */}
              <XAxis
                dataKey="label"
                {...axisProps}
                interval={data.length <= 24 ? 0 : "preserveStartEnd"}
                tickFormatter={categoryTick}
                {...(rotateTicks
                  ? // MEASURED from the labels, not a constant. At the
                    // fixed 74px every name longer than about fifteen
                    // characters had its tail cut off by the SVG's own
                    // bottom edge — 421 of them across the captured
                    // corpus, worst 16px. See `rotatedAxisHeight`.
                    {
                      angle: -35,
                      textAnchor: "end" as const,
                      height: axisHeight,
                      tickMargin: AXIS_TICK_MARGIN,
                    }
                  : {})}
              />
              <YAxis {...axisProps} tickFormatter={formatTick} width={48} />
              {/* A softer hover band. The cursor is a "you are here", not
                  a selection: at the grid's own opacity it read as a
                  second, darker gridline sliding across the plot. */}
              <Tooltip
                content={tooltipContent}
                cursor={{ fill: "var(--chart-grid)", fillOpacity: 0.6, radius: 4 }}
              />
              {spec.highlightLabel && (
                <ReferenceLine
                  x={spec.highlightLabel}
                  stroke="var(--warning)"
                  strokeDasharray="4 3"
                  strokeOpacity={0.7}
                />
              )}
              {spec.series.map((s, i) => (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  name={seriesLabel(s)}
                  {...(stackId ? { stackId } : {})}
                  fill={colors[s.key]}
                  // A stack's segments are one column: only the top one
                  // gets the rounded cap, and a 2px surface gap keeps
                  // adjacent fills from reading as a single mass.
                  // 4px, the data-end radius the mark spec calls for —
                  // enough to take the hard corner off a column without
                  // rounding the mark into a lozenge. Anchored at the
                  // baseline end, which stays square: a bar that curved
                  // where it meets zero would read as starting somewhere
                  // other than zero.
                  radius={stackId && i < spec.series.length - 1 ? 0 : [4, 4, 0, 0]}
                  {...(stackId ? { stroke: "var(--card)", strokeWidth: 2 } : {})}
                  // A MEASURED ZERO gets a baseline tick. Without one it
                  // draws as nothing at all, which is exactly what a
                  // withheld cell draws as — and on a 150-cell ranking the
                  // engine's own sentence said "85 were withheld outright
                  // and 13 are measured" over a picture showing 85 and 3 as
                  // the same blank. Only exact zeros are floored, so no
                  // other value is drawn larger than it is (and never on a
                  // stack, where Recharts cannot honour it cleanly).
                  {...(stackId ? {} : { minPointSize: (value: number | undefined | null) => (value === 0 ? 2 : 0) })}
                  maxBarSize={barCap}
                  className="cursor-pointer"
                  onClick={handleBarClick}
                  {...drawIn}
                >
                  {/* A ceiling does not draw as a quantity. Per-cell, so a
                      bounded category is visibly a different KIND of mark
                      from the measured ones beside it — the defect a buyer
                      would have screenshotted was twelve identical bars,
                      four of which were suppression bounds.

                      Per (category, SERIES) once a chart draws two windows
                      per category: the prior side's ceilings ride the same
                      `is_bound` machinery and land on their own cell, so a
                      payer whose June numerator was suppressed and whose
                      July one was not draws one solid mark and one dashed
                      one. Falls back to the row when the payload carries
                      no per-mark detail — nothing is un-marked because a
                      generation of the wire was less specific. */}
                  {(hasBounded || hasProvisional) &&
                    data.map((row) => {
                      // Per-mark when the row carries per-mark detail at
                      // all, not per-mark when THIS mark happens to have
                      // an entry — a row that says "the prior cell is a
                      // ceiling" is also saying the current one is not.
                      const qualified =
                        (row.cells !== undefined
                          ? row.cells[s.key]?.bounded === true
                          : row.bounded === true) || row.provisional === true;
                      return (
                        <Cell
                          key={`${s.key}:${row.label}`}
                          fill={colors[s.key]}
                          {...(qualified ? { ...BOUNDED_MARK, stroke: colors[s.key] } : {})}
                        />
                      );
                    })}
                </Bar>
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      )}

      {/* The keying census, under the picture it explains. Live, a chart
          declaring `x=month, series=payer` sent thirty rows over three
          distinct keys; the old mapper kept whichever arrived last and drew
          $3,468 of $441,808. */}
      {spec.keying?.mode === "summed" && (
        <p className="mt-1.5 text-micro leading-snug text-warning">{spec.keying.note}</p>
      )}

      {/* What the marks mean when some of them are not measurements.
          Composed as ONE string per fact rather than interpolated across
          spans: this sentence is read aloud, copied out of a screenshot
          and searched for, and a phrase split across three text nodes is
          none of those things. */}
      {(hasBounded || hasProvisional || hasWithheld || absentLabels.size > 0) && (
        <p className="mt-1.5 text-micro leading-snug text-muted-foreground">
          {hasBounded && <span className="block">{boundedLegend(spec)}</span>}
          {/* The absence, said out loud on the figure. The engine's own
              annotation above the picture counts them; this says what the
              gap in the picture IS. */}
          {absentLabels.size > 0 && (
            <span className="block">
              ‡ marks a category with a figure in the window this one is compared against and none
              in this one — its mark here is an absence, not a measured zero, so no bar is drawn.
            </span>
          )}
          {hasBounded && spec.kind === "line" && (
            <span className="block">
              Segments touching a ceiling are drawn dashed with a hollow point — the line between
              two ceilings is not a measured movement.
            </span>
          )}
          {hasProvisional && (
            <span className="block">
              * marks a provisional bucket: still settling, so its value will move.
            </span>
          )}
          {/* A blank that is a REFUSAL, said out loud. Without it a
              withheld cell and a measured 0.0% are the same nothing. */}
          {hasWithheld && (
            <span className="block">
              † marks a cell the engine withheld outright — no value was published for it, so its
              gap on this figure is a refusal, not a zero.
            </span>
          )}
        </p>
      )}

      <div className="mt-1.5 flex items-center justify-between gap-2">
        <span className="text-micro text-muted-foreground">
          {spec.xLabel !== undefined
            ? displayLabel(spec.xLabel)
            : spec.kind !== "line"
              ? "click a bar to drill in"
              : ""}
          {/* What put the bars in this order. The chart sits under findings
              that read "best to worst", so an axis whose order it does not
              state is an axis the reader has to assume — and the assumption
              was wrong: it was alphabetical. */}
          {orderNote(spec) && (
            <span className="text-muted-foreground"> · {orderNote(spec)}</span>
          )}
        </span>
        <span className="flex items-center gap-1">
          {/* MONITOR THIS, at the figure's own pin point — beside the export,
              which is the other "take this away with you" gesture on the
              chart. What it registers is the SPEC behind the figure, so
              tomorrow's tile re-runs this question at the new load rather
              than remembering today's bars. */}
          {investigationId && (
            <MonitorThis
              artifactKey={`${investigationId}:chart:${published.id}`}
              investigationId={investigationId}
              referent={published.id}
              presentation="chart"
              label={published.title || published.id}
              size="row"
            />
          )}
          {spec.truncation && spec.truncation.total > spec.truncation.shown && (
            <Button
              variant="ghost"
              size="xs"
              className="h-5 gap-1 px-1.5 text-meta font-normal text-warning hover:text-warning"
              onClick={() => emitRefinement({ op: "Expand" }, { turnId })}
            >
              showing top {spec.truncation.shown} of {spec.truncation.total}
              <Maximize2 className="size-2.5" />
              Expand
            </Button>
          )}
          {/* The rows behind the picture, in the unit the picture draws
              them in. A chart an analyst cannot get the numbers out of is
              a chart they photograph and retype. Client-side only: these
              rows are already in this browser. */}
          {/* The PUBLISHED spec, not the drawn one: every series, including
              the ones folded into the rollup, and every caveat the turn
              attached. An export that matched the picture would be
              complete-looking and short by eleven columns. */}
          <DownloadCsvButton
            label="CSV"
            title={`Download the ${published.rows.length} row${published.rows.length === 1 ? "" : "s"} and ${published.series.length} series behind this chart as CSV, in the unit shown, with this answer's caveats as comment lines above them. Nothing leaves this browser.`}
            filenameKind="chart"
            filenameTag={published.title || published.id}
            {...(watermarkId ? { watermark: watermarkId } : {})}
            className="h-5 px-1.5 text-micro"
            csv={() =>
              chartToCsv(published, {
                ...(windowLabel ? { windowLabel } : {}),
                ...(watermarkId ? { watermarkId } : {}),
                ...(packLabel ? { packLabel } : {}),
                ...(question ? { question } : {}),
                ...(investigationId ? { investigationId } : {}),
                ...(caveats && caveats.length > 0 ? { caveats } : {}),
                ...(hiddenSeries > 0 && capNote ? { renderNote: capNote } : {}),
              })
            }
          />
        </span>
      </div>
    </figure>
  );
}

/**
 * What "≤" MEANS on this figure — one idea, in the reader's nouns.
 *
 * Rewritten, and both halves of the rewrite are the point.
 *
 * IT SAYS ONE THING. The old sentence carried three words for two ideas —
 * "upper bound", "ceiling", "measurement" — plus a clause about a
 * "suppressed numerator", which is the machine's account of WHY and not
 * the reader's account of what they are looking at. The engine itself
 * stopped writing like this in round 6: its own vocabulary is now "too
 * small to measure exactly", stated once, with the full partition sent to
 * the trace where an auditor can check it and a reader is not asked to.
 * This line follows it, because two surfaces of one product describing one
 * control in two vocabularies is how a reader learns to skip both.
 *
 * IT STOPS COMPETING WITH THE ENGINE'S CENSUS. The old single-series form
 * printed its own count of the ceilings — and counted a different thing:
 * the engine counts the marks it EMITTED and this counts the rows still
 * DRAWN after selection and capping, so a live figure showed "1 of 1 mark
 * is a ceiling" directly under the engine's "4 of 12 marks are ceilings".
 * Two numbers about one control, three lines apart, is exactly the defect
 * that six rounds of honesty work exists to prevent. The engine's sentence
 * is the census; this one is the legend.
 *
 * The COMPARISON form keeps a count, and only there, because the engine's
 * census is silent about the prior side — one current ceiling reported and
 * three prior ones not, live. Per side, this adds the half nobody counted
 * rather than restating the half that was.
 *
 * Exported so the wording is pinned in one place: the same sentence
 * reaches the CSV preamble and the copied text.
 */
export function boundedLegend(spec: ChartSpec): string {
  /*
   * A COMPARISON IS COUNTED PER SIDE.
   *
   * The engine's own census counts the current window against the
   * categories on the axis ("upper bounds: 1 of 4 marks are ceilings"),
   * and it is silent about the prior side — live, on
   * `inv_0899f9defc32/chart_main__compare`, that is one current ceiling
   * reported and three prior ones not. A single combined figure here
   * would print "4 of 8" a paragraph below the engine's "1 of 4" and read
   * as two surfaces disagreeing about one control. Per side it agrees
   * with the engine on its half and adds the half it never counted.
   */
  const pair = spec.comparison;
  if (pair !== undefined && spec.rows.some((row) => row.cells !== undefined)) {
    const onSide = (key: string): number =>
      spec.rows.filter((row) => row.cells?.[key]?.bounded === true).length;
    const total = spec.rows.length;
    const current = onSide(pair.currentKey);
    const prior = onSide(pair.priorKey);
    const marks = total === 1 ? "mark" : "marks";
    return (
      `≤ means at most: too few things sit behind that mark to measure it exactly, ` +
      `so the real figure is at or below it. ${current} of ${total} ${marks} in this window ` +
      `and ${prior} of ${total} in the window it is compared against are limits, ` +
      "and a limit is not ranked against a measured mark."
    );
  }
  return (
    "≤ means at most: too few things sit behind that mark to measure it exactly, " +
    "so the real figure is at or below it. A limit is not ranked against a measured mark."
  );
}

/**
 * "ordered by denied_dollars, high to low" — or nothing at all when the
 * rows are simply in the order the engine emitted them, which is a fact
 * this figure should not dress up as a ranking.
 *
 * Two facts it now also states. A REFUSED ranking is not the same as an
 * unstated one, and saying "unranked" out loud is the difference between a
 * reader treating the leftmost bar as the worst offender and treating it as
 * the first row the engine happened to emit. And an order that HELD BOUNDED
 * CELLS OUT of itself says so, because those marks sit at the end of the
 * axis and would otherwise read as the smallest values on it.
 */
export function orderNote(spec: ChartSpec): string | undefined {
  const order = spec.order;
  if (order === undefined) return undefined;
  if (order.refused === true) return "unranked — no ranking was published for this answer";
  if (order.basis === "wire") return undefined;
  const held =
    order.boundedExcluded !== undefined
      ? `; ${order.boundedExcluded} bounded cell${order.boundedExcluded === 1 ? "" : "s"} held out of it, at the end`
      : "";
  // The catalog SAID so, and the difference from the line below it is
  // worth the extra words: one is a published fact about the dimension,
  // the other is this client reading numbers out of label text.
  // BUG 1 — the measure is named the way the rest of the product names
  // it. This note is composed HERE, not by the engine, and it was putting
  // `timely_filing_at_risk_dollars` under a figure whose own title reads
  // "Timely filing at risk dollars by plan". The raw column keeps
  // travelling on the CSV, where a machine reads it.
  const by = order.by === undefined ? undefined : humanizeInline(order.by);
  if (order.basis === "axis-order")
    return by
      ? `in the catalog's declared order for ${by}${held}`
      : `in the catalog's declared bucket order${held}`;
  if (order.basis === "ordinal-bucket") return `ordered by bucket${held}`;
  const direction = order.descending === false ? "low to high" : "high to low";
  return by ? `ordered by ${by}, ${direction}${held}` : `ordered ${direction}${held}`;
}

interface TooltipEntry {
  dataKey?: string | number;
  value?: number | string;
  name?: string | number;
  color?: string;
  payload?: RowDatum;
}

interface TooltipRenderProps {
  active?: boolean;
  label?: string | number;
  payload?: TooltipEntry[];
}

/**
 * The hover, exported for the same reason `boundedLegend` is: it is where
 * a reader goes to read one number exactly, and its wording — the "≤" on a
 * ceiling, "no figure" on an absence, the change between two windows — is
 * pinned by tests rather than left to a container jsdom gives no size to.
 */
export function ChartTooltipContent({
  active,
  label,
  payload,
  formatValue,
  unit,
  comparison,
  seriesLabel,
  seriesColor,
}: TooltipRenderProps & {
  formatValue: (value: number) => string;
  unit?: ChartSpec["unit"];
  /** Set when this figure draws two windows per category. */
  comparison?: NonNullable<ChartSpec["comparison"]>;
  seriesLabel?: (key: string) => string;
  seriesColor?: (key: string) => string;
}) {
  // NARROWER THAN THE NARROWEST CHART IT CAN OPEN OVER. Recharts keeps a
  // tooltip inside the chart's own box, so a tooltip wider than the box
  // is one that has to hang out of it: the Evidence rail's chart measures
  // 280px at a 1280px viewport (21rem rail, less the panel's and the
  // figure's padding) and the sheet was capped at `max-w-72` — 288. At
  // `max-w-64` it is 256 and clears the narrowest surface it can be
  // opened on by 24px.
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload;
  const referent = row?.referent;
  // The hover is where a reader goes to read one number exactly. A ceiling
  // read there as a measurement is the same lie the bar told, at higher
  // precision.
  const cellOf = (key: string): ChartCell | undefined => row?.cells?.[key];
  // Per-mark when the row carries per-mark detail; per-row when it does
  // not. A row whose prior cell is flagged is also stating that its
  // current cell is a measurement.
  const boundedCell = (key: string): boolean =>
    row?.cells !== undefined ? cellOf(key)?.bounded === true : row?.bounded === true;
  const bounded = row?.bounded === true;
  const provisional = row?.provisional === true;
  const denominator = typeof row?.denominator === "number" ? row.denominator : undefined;

  /*
   * A COMPARISON IS READ AS A MOVEMENT, so the hover states one.
   *
   * Both windows and the delta between them, composed from the row rather
   * than from Recharts' payload entries: the current side of a prior-only
   * category is drawn as no bar at all, which means it is absent from the
   * payload, and the one thing this popover must not do on that category
   * is show June's figure alone with no mention of July.
   */
  if (comparison !== undefined && row !== undefined) {
    const read = (key: string): number | undefined => {
      const raw = row[key];
      return typeof raw === "number" ? raw : undefined;
    };
    const currentAbsent = cellOf(comparison.currentKey)?.absent === true;
    const priorAbsent = cellOf(comparison.priorKey)?.absent === true;
    const current = currentAbsent ? undefined : read(comparison.currentKey);
    const prior = priorAbsent ? undefined : read(comparison.priorKey);
    const delta =
      current !== undefined && prior !== undefined ? current - prior : undefined;
    const lines: { key: string; value: number | undefined; absent: boolean }[] = [
      { key: comparison.currentKey, value: current, absent: currentAbsent },
      { key: comparison.priorKey, value: prior, absent: priorAbsent },
    ];
    return (
      <div className="max-w-64 rounded-lg border border-border/70 bg-popover/95 px-3 py-2.5 text-xs shadow-lg backdrop-blur-sm">
        <p className="mb-1 flex items-center gap-1.5 font-medium">
          {humanizeIsoDates(humanizeCategory(String(label)))}
          {referent && (
            <span className="rounded border border-verified/40 bg-verified/10 px-1 font-mono text-micro text-verified">
              {referent}
            </span>
          )}
        </p>
        <ul className="space-y-0.5">
          {lines.map((line) => (
            <li key={line.key} className="flex items-start justify-between gap-4 leading-snug">
              <span className="flex min-w-0 items-start gap-1.5 text-muted-foreground">
                <span
                  className="mt-[0.28rem] size-2 shrink-0 rounded-[2px]"
                  style={{ background: seriesColor?.(line.key) }}
                />
                {seriesLabel?.(line.key) ?? line.key}
              </span>
              <span className="num shrink-0 font-medium">
                {line.absent ? (
                  <span className="text-warning">no figure</span>
                ) : line.value === undefined ? (
                  "—"
                ) : (
                  `${boundedCell(line.key) ? "≤ " : ""}${formatValue(line.value)}`
                )}
              </span>
            </li>
          ))}
          {/* Stated, not left to be computed off two bars of similar
              height — reading a movement off a paired bar chart by eye is
              exactly the thing the chart is bad at. */}
          {delta !== undefined && (
            <li className="mt-1 flex items-start justify-between gap-4 border-t pt-1 leading-snug">
              <span className="text-muted-foreground">Change</span>
              <span className="num shrink-0 font-medium">
                {formatMeasureDelta(delta, unit ?? "count")}
                {prior !== 0 && prior !== undefined && (
                  <span className="font-normal text-muted-foreground">
                    {" "}
                    ({formatSignedPct(delta / Math.abs(prior))})
                  </span>
                )}
              </span>
            </li>
          )}
        </ul>
        {/* Which SIDE the ceiling is on. One "≤" over a pair of marks left
            the reader to guess, and the two windows are flagged
            independently on the wire. */}
        {lines
          .filter((line) => boundedCell(line.key))
          .map((line) => {
            const n = cellOf(line.key)?.denominator;
            return (
              <p
                key={`bound:${line.key}`}
                className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-warning"
              >
                {seriesLabel?.(line.key) ?? line.key} is an upper bound
                {n !== undefined ? ` over n = ${n}` : ""} — a ceiling, not a measurement. It has no
                position in a ranking.
              </p>
            );
          })}
        {(currentAbsent || priorAbsent) && (
          <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-warning">
            No figure was measured for this category in that window. The zero on the wire is the
            join between the two windows, not a reading — an absence, not a measured zero.
          </p>
        )}
        {provisional && (
          <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-warning">
            Provisional — this bucket is calendar-partial or still adjudicating, so the value will
            move.
          </p>
        )}
      </div>
    );
  }

  return (
    // BOUNDED. A series key is not always a short name: the server folds an
    // undeclared grouping column into `series` as a `" / "`-joined
    // composite ("Orthopedic Surgery / CO"), so eight rows of an unbounded
    // popover grew wider than the figure it belongs to. The names wrap
    // inside the cap instead of being truncated — a half-read identity on
    // the one surface an analyst goes to for an exact number is worse than
    // two lines — and the number itself never wraps away from its label.
    <div className="max-w-64 rounded-lg border border-border/70 bg-popover/95 px-3 py-2.5 text-xs shadow-lg backdrop-blur-sm">
      <p className="mb-1 flex items-center gap-1.5 font-medium">
        {humanizeIsoDates(humanizeCategory(String(label)))}
        {referent && (
          <span className="rounded border border-verified/40 bg-verified/10 px-1 font-mono text-micro text-verified">
            {referent}
          </span>
        )}
      </p>
      <ul className="space-y-0.5">
        {payload.map((entry) => (
          <li
            key={String(entry.dataKey)}
            className="flex items-start justify-between gap-4 leading-snug"
          >
            <span className="flex min-w-0 items-start gap-1.5 text-muted-foreground">
              <span
                className="mt-[0.28rem] size-2 shrink-0 rounded-[2px]"
                style={{ background: entry.color }}
              />
              {String(entry.name)}
            </span>
            <span className="num shrink-0 font-medium">
              {typeof entry.value === "number"
                ? `${boundedCell(String(entry.dataKey)) ? "≤ " : ""}${formatValue(entry.value)}`
                : entry.value}
            </span>
          </li>
        ))}
      </ul>
      {bounded && (
        <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-warning">
          Upper bound{denominator !== undefined ? ` over n = ${denominator}` : ""} — a ceiling, not
          a measurement. It has no position in a ranking.
        </p>
      )}
      {provisional && (
        <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-warning">
          Provisional — this bucket is calendar-partial or still adjudicating, so the value will
          move.
        </p>
      )}
      {row?.withheld === true && (
        <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-warning">
          Withheld — the engine published no value for this cell under the small-cell policy. The
          gap is a refusal, not a zero.
        </p>
      )}
    </div>
  );
}
