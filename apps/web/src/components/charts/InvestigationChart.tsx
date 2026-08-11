"use client";

import { ArrowDownRight, ArrowUpRight, ListPlus, Maximize2, X } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { DownloadCsvButton } from "@/components/answer/AnswerActions";
import { ChartDonutView, donutCensusNote } from "@/components/charts/ChartDonutView";
import {
  barBandPlan,
  CATEGORICAL_SLOTS,
  chartViewForms,
  donutHonest,
  entityColor,
  isRankedCategorical,
  NEUTRAL_INK,
  rankedPlotHeight,
  resolveChartView,
  rowsAreEntities,
  SINGLE_HUE,
  subjectLabel,
  type ChartView,
} from "@/components/charts/chartForms";
import { ChartSlopeView } from "@/components/charts/ChartSlopeView";
import { ChartTableView } from "@/components/charts/ChartTableView";
import { ChartViewMenu } from "@/components/charts/ChartViewMenu";
import { KeyFigure } from "@/components/figures/KeyFigure";
import { MonitorThis } from "@/components/monitors/MonitorThis";
import { Button } from "@/components/ui/button";
import {
  capChartSeries,
  humanizeColumn,
  OTHERS_SERIES_KEY,
  WINDOW_LABEL_CURRENT,
  WINDOW_LABEL_PRIOR,
} from "@/lib/contract";
import { humanizeInline } from "@/lib/humanize";
import { chartToCsv } from "@/lib/export";
import {
  formatCount,
  formatMeasure,
  formatMeasureDelta,
  formatMeasureTick,
  formatSignedPct,
  humanizeIsoDates,
  shortDate,
} from "@/lib/format";
import { capitalizeOpening } from "@/lib/prose";
import { useSessionStore } from "@/lib/store";
import type { ChartCell, ChartRow, ChartSeries, ChartSpec } from "@/lib/types";
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
 * validated slots, assigned in fixed order and never cycled (the order is
 * the colour-vision safety mechanism, not a preference). Anything past the
 * eighth is not given a further hue — `capChartSeries` has already folded
 * it into the rollup, which wears the neutral ink because it is not an
 * entity.
 *
 * A SERIES IS COLOURED BY POSITION AND A ROW IS COLOURED BY ITS NAME, and
 * the difference is not an inconsistency — it is which channel carries the
 * identity in each case.
 *
 * On a multi-series figure the LEGEND is the identity: a swatch beside a
 * name, and the reader matches the mark to the swatch by hue. Hue is doing
 * the work, so the assignment has to guarantee that no two series share a
 * slot, which only a positional assignment can. A hash cannot: eight names
 * into twelve slots collide better than nine times in ten.
 *
 * On a categorical axis the LABEL is the identity — it is set beside its
 * own bar, at full length, in text ink — so hue is free to carry something
 * else, and what it carries is the entity across figures (`entityColor`).
 */
const CATEGORICAL = CATEGORICAL_SLOTS;

function seriesColors(series: readonly ChartSeries[]): Record<string, string> {
  const out: Record<string, string> = {};
  let slot = 0;
  for (const s of series) {
    if (s.key === OTHERS_SERIES_KEY) {
      out[s.key] = NEUTRAL_INK;
      continue;
    }
    out[s.key] =
      series.length <= 2 ? ROLE_COLOR[s.role] : (CATEGORICAL[slot] ?? NEUTRAL_INK);
    slot += 1;
  }
  return out;
}

/**
 * A ROW THAT IS NOT AN ENTITY EITHER — the engine's own rollup, arriving
 * as a category rather than as a series.
 *
 * "+13 others" and "Other" are not somebody's name: they are everything
 * that did not fit, and giving them a hue would put a made-up entity in
 * the reader's colour memory alongside twelve real ones.
 */
const ROLLUP_LABEL = /^(\+\d+\s+others|others?)$/i;

/**
 * The colour ONE MARK on a categorical axis is drawn in.
 *
 * Three cases, and the middle one is the whole feature:
 *
 *   · a rollup keeps the neutral ink, because it is not an entity;
 *   · an ENTITY (a payer, a plan, a facility) takes the hue its own name
 *     hashes to, so the same payer is the same colour on every figure in
 *     the product and the eye can follow it down a page — see
 *     `entityColor` for why that is safe when a hash cannot guarantee
 *     distinctness;
 *   · everything else — months, age bands, runway buckets — stays in the
 *     single accent hue. A rainbow over an ordered scale says those
 *     buckets are different KINDS of thing, which is the one thing an
 *     ordered scale is not, and it spends the colour channel on nothing.
 */
function rowColor(spec: ChartSpec, label: string, entities: boolean): string {
  if (ROLLUP_LABEL.test(label)) return NEUTRAL_INK;
  return entities ? entityColor(label) : SINGLE_HUE;
}

/**
 * THE SUBJECT — the row the answer is about — and how it is emphasised.
 *
 * Two treatments, because the two axes have two different amounts of
 * colour to spend.
 *
 * On a SINGLE-HUE axis the emphasis is contrast: the subject is drawn at
 * full strength and the rest recede. There is no identity to damage,
 * because every mark is the same hue already.
 *
 * On an ENTITY axis nothing is dimmed. The eleven other payers are wearing
 * the hues a reader is tracking them by, and fading them to point at the
 * twelfth would spend the tracking to make an emphasis. So the subject
 * takes an INK RING instead — a treatment that adds a mark rather than
 * subtracting from its neighbours, and one that survives greyscale, a
 * projector and colour-vision deficiency because it is not a hue.
 *
 * Composed with `BOUNDED_MARK` rather than replacing it: a subject that is
 * also a ceiling keeps the dashed outline (the honesty mark wins the
 * stroke pattern) and gains the ink (the emphasis wins the colour). A rule
 * that let emphasis overwrite a ceiling's dashes would be an accent
 * deleting a qualification.
 */
const SUBJECT_RING = {
  stroke: "var(--foreground)",
  strokeWidth: 2,
  strokeOpacity: 0.55,
} as const;
/** What a mark that is NOT the subject fades to, on a single-hue axis. */
const RECEDED_FILL_OPACITY = 0.42;

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
    return windows?.current
      ? `${WINDOW_LABEL_CURRENT} (${windows.current})`
      : WINDOW_LABEL_CURRENT;
  }
  if (key === spec.comparison.priorKey) {
    // "Prior window" was this file's own spelling of the concept the
    // engine calls "the window compared against". Two words for one thing
    // is how a reader concludes they are two things, and the legend and
    // the annotation under it are eight pixels apart.
    return windows?.prior ? `${WINDOW_LABEL_PRIOR} (${windows.prior})` : WINDOW_LABEL_PRIOR;
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
 * A bucket the engine named by its own date: "2026-01-01".
 *
 * Strict on the month and the day, for the same reason `humanizeIsoDates`
 * is: "2026-13-45" is not a date, and re-spelling it would print
 * "undefined 45" over whatever the engine actually meant.
 */
const ISO_DATE_LABEL = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/;

/**
 * The same category, spelled for an AXIS TICK.
 *
 * A time series arrives with ISO dates as its category labels, and the
 * axis was printing them raw — twelve ticks reading "2026-01-01",
 * "2026-02-01", "2026-03-01" under a title that says "by month". It is
 * the same machine literal the rest of the product spells out
 * (`humanizeIsoDates`), on the one surface that had no rule for it.
 *
 * SHORT on the tick and MEDIUM in the hover, which is the same split
 * `shortDate`/`mediumDate` exist for: an axis is scanned and the year is
 * the same on every tick, while the hover is where a reader goes to read
 * one point exactly and is entitled to the whole date.
 *
 * Applied before the width budget rather than after it, so a shortened
 * label is measured as drawn: "Jan 1" is five characters and "2026-01-01"
 * is ten, and budgeting the second while drawing the first is how an axis
 * rotates for room it does not need. The "≤" and the "†"/"‡"/"*" marks
 * are added on top by `categoryTick`, untouched.
 */
function tickLabel(label: string): string {
  if (ISO_DATE_LABEL.test(label)) {
    try {
      return shortDate(label);
    } catch {
      // Not a date after all ("2026-13-45"). The engine's own string is a
      // better tick than a repaired guess at what it meant.
      return label;
    }
  }
  return humanizeCategory(label);
}

/**
 * A warehouse column reaching the figure as a LABEL — the axis caption,
 * a series name in the legend and the hover.
 *
 * NO WAREHOUSE IDENTIFIER REACHES A LABEL. `xLabel` and the series keys are the wire's own
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

/**
 * A caption starts with a capital. `orderNote` and the axis label are both
 * composed as clauses so they can be joined, and a clause that ends up
 * first on the line is a sentence — "ordered by denied dollars" under a
 * figure reads as a fragment somebody forgot to finish.
 *
 * Only the first character, and only when it is a lowercase letter: an
 * acronym the pack spells itself ("CARC", "DNFB") is already right, and
 * a "≤" or a digit must not be touched.
 */
function sentenceCase(text: string): string {
  const first = text[0];
  if (first === undefined || first !== first.toLowerCase()) return text;
  return first.toUpperCase() + text.slice(1);
}

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
 * AND THE CUT IS A PLAIN ONE WHEREVER A PLAIN ONE WILL DO.
 *
 * The middle cut was applied unconditionally, so the thirty-plan filing
 * chart came out as "Bluestone…ral PPO", "Halvern …e PPO", "Federal
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
  const spelled = new Map(unique.map((label) => [label, tickLabel(label)]));

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
export const MAX_AXIS_HEIGHT = 116;
/**
 * The 8px is not slack, it is measurement error paid for: 6.3px is the
 * AVERAGE advance, and a name of capitals and an ellipsis ("Northbridge
 * Comme…", "Summit Peak Medic…") runs wider than its average. At +4 the
 * sweep still cut 1-3px off those three labels; at +8 the corpus is clean
 * at every width with room to spare inside the cap.
 */
const TICK_ADVANCE_SLACK = 8;
/** Vertical room the drawing itself keeps, whatever the axis costs. */
export const PLOT_ROOM_PX = 214;

export function rotatedAxisHeight(ticks: readonly string[], max = MAX_AXIS_HEIGHT): number {
  const longest = ticks.reduce((n, tick) => Math.max(n, tick.length), 0);
  if (longest === 0) return MIN_AXIS_HEIGHT;
  const drop = longest * TICK_CHAR_PX * TICK_ROTATION_SIN + TICK_LINE_PX * TICK_ROTATION_COS;
  return Math.max(
    MIN_AXIS_HEIGHT,
    Math.min(max, Math.ceil(drop) + AXIS_TICK_MARGIN + TICK_ADVANCE_SLACK),
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

/* ------------------------------------------------------------------ */
/* THE SAME AXIS, IN A ROOM TEN TIMES THE SIZE                         */
/* ------------------------------------------------------------------ */

/**
 * How many characters a ROTATED tick may take, given the room the axis is
 * allowed to occupy below the plot — `rotatedAxisHeight` solved for length
 * instead of for height, off the same geometry and the same constants, so
 * the two cannot drift.
 *
 * This is the number that makes fullscreen worth opening. Every other
 * width on this figure is measured (`flatTickBudget` reads the band, the
 * gutters read the label) and the rotated budget was the one constant left
 * — 18 characters, whether the figure was 280px wide in the Evidence rail
 * or filled the screen. So the twelve-payer ranking elided "Northbridge
 * Commercial PPO" to "Northbridge Comm…" in both, and an expansion that
 * enlarged the picture without re-spelling the axis would have been a
 * bigger copy of the unreadable thing.
 */
export function rotatedTickBase(maxAxisHeight: number): number {
  const room =
    maxAxisHeight - AXIS_TICK_MARGIN - TICK_ADVANCE_SLACK - TICK_LINE_PX * TICK_ROTATION_COS;
  return Math.max(FLAT_TICK_FLOOR, Math.floor(room / (TICK_CHAR_PX * TICK_ROTATION_SIN)));
}

/**
 * The room the axis may take on a figure that owns 85% of the viewport.
 * A 220px axis is a fifth of that figure and buys ~53 characters, which is
 * longer than every payer, plan and provider name in the corpus.
 */
export const EXPANDED_MAX_AXIS_HEIGHT = 220;
/**
 * …and the budget the INLINE figure has always used, unchanged. Its axis
 * is capped at 116px, and 18 characters is what a reader needs to tell two
 * payers apart in a card that is 214px of plot tall.
 */
export const ROTATED_TICK_BASE = 18;
/**
 * A FLAT tick fullscreen is still budgeted against its own band — a label
 * wider than its band collides with its neighbours at any size — but the
 * band is now wide enough that the inline ceilings (12 and 20 characters,
 * chosen for a column) are the binding constraint rather than the room.
 */
const EXPANDED_FLAT_CEILING = 40;

/* ------------------------------------------------------------------ */
/* THE SAME AXIS, TURNED ON ITS SIDE                                   */
/* ------------------------------------------------------------------ */

/**
 * A LEFT GUTTER MEASURED FROM THE LONGEST NAME, AND CAPPED.
 *
 * On a horizontal ranking the category label is not competing with a 60px
 * band any more — it has a whole gutter to itself and runs left to right,
 * which is why this orientation deletes most of the elision this file
 * exists to manage. What it does not delete is the ceiling: a gutter is
 * bought from the plot, and a 300px gutter on a 420px card leaves 120px
 * for the bars. So the gutter is `longest name`, and past the cap the
 * shortener takes over exactly as it does on a rotated axis.
 *
 * The cap is a SHARE of the figure, not a constant, because this is the
 * one measurement that has to work at 280px in the Evidence rail and at
 * 1400px in the dialog. A third of the width leaves two thirds for the
 * marks, which is the ratio at which a bar is still a bar.
 */
export const HORIZONTAL_GUTTER_SHARE = 0.34;
export const MIN_HORIZONTAL_GUTTER = 72;
export const MAX_HORIZONTAL_GUTTER = 280;
export const EXPANDED_MAX_HORIZONTAL_GUTTER = 420;

export function horizontalGutterCap(plotWidth: number, expanded = false): number {
  const ceiling = expanded ? EXPANDED_MAX_HORIZONTAL_GUTTER : MAX_HORIZONTAL_GUTTER;
  if (plotWidth <= 0) return MIN_HORIZONTAL_GUTTER;
  return Math.max(MIN_HORIZONTAL_GUTTER, Math.min(ceiling, Math.floor(plotWidth * HORIZONTAL_GUTTER_SHARE)));
}

/**
 * …the same cap, in CHARACTERS, which is what the shortener budgets in.
 * Off the same 6.3px advance every other measurement on this figure uses,
 * less the tick margin and the four characters `categoryTick` can add on
 * top of the name ("≤ " and " †").
 */
export function horizontalTickBudget(plotWidth: number, expanded = false): number {
  const room = horizontalGutterCap(plotWidth, expanded) - AXIS_TICK_MARGIN;
  return Math.max(FLAT_TICK_FLOOR, Math.floor(room / TICK_CHAR_PX) - TICK_MARK_PAD);
}

/**
 * How wide the axis is actually drawn, given the labels it ended up with.
 *
 * The cap is what the gutter may take; this is what it needs. A four-payer
 * ranking whose longest name is "Atlas Commercial" does not buy 280px of
 * gutter and leave the bars in a third of the card — it takes what the
 * name costs and gives the rest to the drawing.
 */
export function horizontalAxisWidth(
  ticks: readonly string[],
  plotWidth: number,
  expanded = false,
): number {
  const longest = ticks.reduce((n, tick) => Math.max(n, tick.length), 0);
  const needed = Math.ceil(longest * TICK_CHAR_PX) + AXIS_TICK_MARGIN + TICK_ADVANCE_SLACK;
  return Math.max(
    MIN_HORIZONTAL_GUTTER,
    Math.min(horizontalGutterCap(plotWidth, expanded), needed),
  );
}

/**
 * ROTATE OR NOT, AND AT WHAT LENGTH — one decision, in one place.
 *
 * Both halves were inline in the component and both read the container's
 * measured width, which is exactly why they can be re-run at a different
 * width and produce a different axis. `expanded` is the fullscreen figure
 * asking the same question with a much larger answer.
 *
 * A HORIZONTAL axis never rotates — that is most of the point of turning
 * the figure — so its whole plan is a width budget, and the budget is the
 * gutter's rather than the band's.
 */
export function axisTickPlan(
  labels: readonly string[],
  {
    kind,
    plotWidth,
    expanded = false,
    orientation = "vertical",
  }: {
    kind: ChartSpec["kind"];
    plotWidth: number;
    expanded?: boolean;
    orientation?: "vertical" | "horizontal";
  },
): { rotate: boolean; text: Map<string, string> } {
  if (orientation === "horizontal") {
    // ELISION ONLY PAST THE CAP. `axisTickLabels` grows from its base until
    // every drawn label is unique, so base and max are the same number
    // here: at the cap it tries a plain cut, then a middle cut, and if
    // neither tells two names apart it prints them whole — the same
    // "an axis that is cramped beats an axis that is wrong" rule the
    // rotated path follows.
    const budget = horizontalTickBudget(plotWidth, expanded);
    return { rotate: false, text: axisTickLabels(labels, budget, budget) };
  }
  /**
   * HORIZONTAL LABELS WHEREVER THEY FIT.
   *
   * The rule was "more than six categories, rotate", which put a 35° slant
   * under axes whose labels are "0–30 days" and "Jan" — unreadable for no
   * reason, and the rotation is what pushes the first and last label past
   * the container's edge. Rotation is a cost, so it is paid only when the
   * labels cannot sit flat: short names across a modest number of
   * categories stay horizontal, and a name that cannot fit its own
   * measured band rotates whatever the count.
   */
  const longest = labels.reduce((n, label) => Math.max(n, tickLabel(label).length), 0);
  const budget = flatTickBudget(plotWidth, labels.length);
  const rotate =
    kind !== "line" &&
    ((labels.length > 6 && !(longest <= 12 && labels.length <= 12)) ||
      (longest > budget && budget < FLAT_TICK_FLOOR));

  if (rotate) {
    const base = expanded ? rotatedTickBase(EXPANDED_MAX_AXIS_HEIGHT) : ROTATED_TICK_BASE;
    // `axisTickLabels` only ever GROWS from the base, so the ceiling has
    // to clear it: at 53 characters a base above the old 40 would have
    // made the growth loop unreachable and quietly stopped disambiguating.
    return { rotate, text: axisTickLabels(labels, base, Math.max(base, 40)) };
  }
  const ceiling = expanded ? EXPANDED_FLAT_CEILING : labels.length > 8 ? 12 : 20;
  return { rotate, text: axisTickLabels(labels, Math.max(4, Math.min(ceiling, budget))) };
}

/**
 * THE FOUR MARKS, ON ONE TICK.
 *
 * Composed rather than exclusive: on a comparison one category can hold a
 * ceiling on one window and no figure at all on the other, and an early
 * return would print one of those two facts and drop the other.
 *
 * IT IS THE SAME VOCABULARY WHICHEVER WAY THE FIGURE RUNS. A ranking drawn
 * on its side moves this tick from under a bar to beside one, and that is
 * the whole of the change: the "≤" still opens the label, the "†", "‡" and
 * "*" still close it. A horizontal figure that grew its own set of marks
 * would be a second honesty vocabulary for a reader to learn, on the shape
 * this product draws most often.
 *
 * Exported because it is what the marks ARE, and jsdom gives a Recharts
 * container no size — so this is the surface a test can hold the four
 * marks to.
 */
export function honestyTick(
  text: string,
  marks: { bounded?: boolean; withheld?: boolean; absent?: boolean; provisional?: boolean },
): string {
  const prefix = marks.bounded === true ? "≤ " : "";
  const suffix =
    marks.withheld === true
      ? " †"
      : marks.absent === true
        ? " ‡"
        : marks.provisional === true
          ? "*"
          : "";
  return `${prefix}${text}${suffix}`;
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

/* ------------------------------------------------------------------ */
/* A SINGLE MARK IS NOT A CHART                                        */
/* ------------------------------------------------------------------ */

/**
 * ONE NUMBER, DRAWN AS A CHART, READS AS A FAILED RENDER.
 *
 * `barCap` and `barCategoryGap` above fought the top half of this defect —
 * a two-bar comparison in a ~700px plot drew 8–14px hairlines with three
 * hundred pixels of nothing between them — and the floor they put under
 * the band share does not reach the case underneath it: ONE category. A
 * lone bar over a single tick, wearing a y-axis, a grid and 214px of empty
 * plot, is not a picture of a number. It is a picture of a chart that
 * failed to load, and that is what the owner reported seeing. A single
 * point on a LINE is worse still: a shape whose entire job is to show a
 * movement, drawing a dot.
 *
 * A chart earns its frame by having something to COMPARE. Where there is
 * nothing to compare the frame is cost with no return, and the honest
 * drawing of one measured number is the number — at display size, in the
 * product's own figure vocabulary (`KeyFigure`), with every mark the
 * engine put on it travelling at that size rather than being dropped on
 * the way up. A figure that loses its honesty marks on the way to display
 * size is the object most likely to be screenshotted.
 *
 * THE TIE-BREAK, WHICH IS THE PART A RULE LIKE THIS GETS WRONG.
 *
 * One category and MORE THAN ONE series is ambiguous: it looks like the
 * defect above (several hairlines over a single tick) and it is also a
 * real comparison — the series are comparable to each other, which is
 * exactly what a chart is for. Comparability wins. Such a spec is
 * CHARTED, and only a genuinely single drawable mark — one category × one
 * series — becomes a figure card.
 *
 * A PERIOD COMPARISON is the one two-series shape that still does not
 * chart, because its two marks are not two things: they are one measure
 * in two windows, i.e. a movement. This product already refuses to let a
 * movement be read off two bars by eye — `ChartTooltipContent` states the
 * change in words rather than leaving the reader to subtract two heights —
 * and at one category there is nothing left for the bars to do that the
 * statement does not do better.
 */
type ChartRenderPolicy =
  | { mode: "chart" }
  | { mode: "figure"; row: ChartRow; series: ChartSeries }
  | { mode: "delta"; row: ChartRow; current: ChartSeries; prior: ChartSeries };

function singleMarkPolicy(spec: ChartSpec): ChartRenderPolicy {
  // Decided on the DRAWABLE rows — the post-`capChartSeries` spec, after
  // the selection this figure actually draws. What the CSV carries is a
  // different and larger thing (the PUBLISHED spec) and nothing here
  // touches it.
  if (spec.rows.length !== 1) return { mode: "chart" };
  const row = spec.rows[0];
  if (row === undefined) return { mode: "chart" };

  const pair = spec.comparison;
  if (pair !== undefined) {
    const current = spec.series.find((s) => s.key === pair.currentKey);
    const prior = spec.series.find((s) => s.key === pair.priorKey);
    // A comparison whose own keys are not among the drawn series is not a
    // movement this renderer can state. The chart path still draws
    // whatever survived, which is the conservative outcome.
    if (current === undefined || prior === undefined) return { mode: "chart" };
    return { mode: "delta", row, current, prior };
  }

  // THE TIE-BREAK. See above: series that are comparable to each other
  // stay charted, whatever the category count.
  if (spec.series.length !== 1) return { mode: "chart" };
  const series = spec.series[0];
  if (series === undefined) return { mode: "chart" };
  return { mode: "figure", row, series };
}

/**
 * THE MARK VOCABULARY, SAID WITHOUT AN AXIS TO POINT AT.
 *
 * The captions under a chart name the glyph the axis is wearing — "*
 * marks a provisional bucket", "† marks a cell the engine withheld". A
 * figure card has no axis and no glyph, so the same FACT is stated about
 * the figure the reader is looking at instead. The sentences are the
 * chart's, minus the pointer: a caption that names a mark which is
 * nowhere on the surface teaches a reader to stop reading the captions,
 * which is the one thing six rounds of honesty work cannot afford.
 *
 * Held as constants rather than inline JSX so the two forms of one
 * sentence sit next to each other and cannot drift apart.
 */
const PROVISIONAL_AXIS_NOTE =
  "* marks a provisional bucket: still settling, so its value will move.";
const PROVISIONAL_CARD_NOTE =
  "This bucket is provisional: still settling, so its value will move.";
const WITHHELD_AXIS_NOTE =
  "† marks a cell the engine withheld outright — no value was published for it, so its gap on this figure is a refusal, not a zero.";
const WITHHELD_CARD_NOTE =
  "The engine withheld this cell outright — no value was published for it, so this gap is a refusal, not a zero.";
const ABSENT_AXIS_NOTE =
  "‡ marks a category with a figure in the window this one is compared against and none in this one — its mark here is an absence, not a measured zero, so no bar is drawn.";
const ABSENT_CARD_NOTE =
  "This category carries a figure in the window it is compared against and none in this one — its mark here is an absence, not a measured zero, so no figure is drawn.";
/** The wire sent this category with no number on it at all. */
const NO_VALUE_CARD_NOTE =
  "The engine published no figure for this mark, so none is drawn — the gap is an absence, not a measured zero.";

/**
 * What KIND of number this is, in the words the rest of the product uses:
 * `ChartTooltipContent`'s "a ceiling, not a measurement" and the
 * "still settling" `KeyFigure`'s own doc comment names. Lower case,
 * because a mark is never in sentence position — it rides on the same
 * line as the numeral, after it.
 */
const CEILING_MARK = "a ceiling, not a measurement";
const SETTLING_MARK = "still settling";

/**
 * THE AXIS CONTEXT, AS A LINE OF TEXT RATHER THAN A ONE-TICK AXIS.
 *
 * On a chart the category is a tick and the dimension is the footer
 * caption. With one mark there is no scale for a tick to sit on, and an
 * axis drawn for a single label is a label pretending to be a scale. So
 * the fact — "this is CARC 16, and the dimension was CARC" — becomes a
 * quiet sentence under the number.
 *
 * Spelled the way the HOVER spells a category rather than the way a tick
 * does: a tick is scanned and gets `shortDate` plus a width budget, while
 * this is the surface a reader reads exactly and is entitled to the whole
 * date and the whole name. The dimension is humanised exactly as
 * `footerCaption` humanises it — "carc" is what the pack calls it and
 * "CARC" is what a reader calls it.
 */
function markContext(spec: ChartSpec, row: ChartRow): string {
  const parts = [humanizeIsoDates(humanizeCategory(row.label))];
  if (spec.xLabel !== undefined && spec.xLabel !== "") parts.push(humanizeColumn(spec.xLabel));
  // The owner's rule: a text node in sentence position opens with a
  // capital. Applied at the render site only — the wire's own string keeps
  // travelling to the CSV and the drill target exactly as it arrived.
  return capitalizeOpening(parts.join(" · "));
}

/**
 * Per-MARK when the row carries per-mark detail, per-ROW when it does
 * not — the same rule the `<Cell>` loop and the tooltip already follow. A
 * row that says "the prior cell is a ceiling" is also saying the current
 * one is not.
 */
function cellBounded(
  // Structural rather than `ChartRow`, because the same question is asked
  // of the wire's row and of the datum the plot is drawn from, and the two
  // carry the same two fields for the same reason.
  row: { cells?: Record<string, ChartCell>; bounded?: boolean },
  key: string,
): boolean {
  return row.cells !== undefined ? row.cells[key]?.bounded === true : row.bounded === true;
}

/**
 * A REFUSAL WHERE THE FIGURE WOULD HAVE BEEN.
 *
 * The same shell as the `unkeyable` block, for the same reason: a figure
 * that was not drawn is not a smaller figure, it is a different object,
 * and it says so in the place the figure would have occupied. No number
 * reaches this path at all — a withheld cell rendered as a 30px "0", or
 * as a 30px "—" that a reader takes for one, is the single worst thing
 * this render policy could produce.
 */
function NoFigureCard({
  headline,
  note,
  context,
}: {
  headline: string;
  note: string;
  context: string;
}) {
  return (
    <div className="rounded-md border border-dashed bg-surface-sunken/60 px-3 py-4 text-meta leading-snug text-muted-foreground">
      <p className="font-medium text-foreground">{headline}</p>
      <p className="mt-1">{note}</p>
      <p className="mt-1 text-micro">{context}</p>
    </div>
  );
}

/**
 * ONE DRAWABLE MARK, AT DISPLAY SIZE.
 *
 * `KeyFigure` is the product's display-figure vocabulary and it is
 * imported rather than re-invented: the size steps, the accent-as-a-rule
 * and the mark-beside-the-numeral rule are its, so this card cannot
 * quietly grow a second way of making a number big.
 *
 * A REAL BUTTON, not a div with a click handler. The bar this replaces
 * emitted `{op: "DrillInto", target}` and was reachable by pointer only;
 * the card that replaces it is reachable by keyboard and carries an
 * accessible name made of the label, the figure and its context.
 */
function SingleFigureCard({
  spec,
  row,
  series,
  label,
  formatValue,
  onDrill,
}: {
  spec: ChartSpec;
  row: ChartRow;
  series: ChartSeries;
  /** The string the legend and the hover would have used for this mark. */
  label: string;
  formatValue: (value: number) => string;
  onDrill: () => void;
}) {
  const context = markContext(spec, row);
  const cell = row.cells?.[series.key];
  const raw = row.values[series.key];

  // AN ABSENCE IS NOT A FIGURE, AND A REFUSAL IS NOT A ZERO. Both are
  // checked before anything is formatted, so there is no path on which a
  // number is composed for a cell the engine declined to publish.
  if (row.withheld === true) {
    return (
      <NoFigureCard
        headline="No value was published for this figure"
        note={WITHHELD_CARD_NOTE}
        context={context}
      />
    );
  }
  if (cell?.absent === true) {
    return (
      <NoFigureCard headline="No figure was measured here" note={ABSENT_CARD_NOTE} context={context} />
    );
  }
  if (typeof raw !== "number") {
    return (
      <NoFigureCard
        headline="No figure was measured here"
        note={NO_VALUE_CARD_NOTE}
        context={context}
      />
    );
  }

  const bounded = cellBounded(row, series.key);
  const provisional = row.provisional === true;
  // EVERY mark travels, and they compose: a bucket can be both a ceiling
  // and still settling, and printing one of those two facts at 30px while
  // dropping the other is the failure mode this whole policy exists to
  // avoid.
  const marks = [bounded ? CEILING_MARK : undefined, provisional ? SETTLING_MARK : undefined].filter(
    (mark): mark is string => mark !== undefined,
  );

  return (
    <button
      type="button"
      onClick={onDrill}
      title={`Drill into ${row.label}`}
      className="focus-ring block w-full rounded-lg text-left"
    >
      <KeyFigure
        // Sentence position, and the wire's series label is frequently a
        // lower-case measure phrase ("denied dollars"). Capitalised at the
        // render site; the exported value is untouched.
        label={capitalizeOpening(label)}
        // The "≤" the engine published, on the front of a 30px numeral —
        // and the unit is the spec's, because the number goes through the
        // same `formatValue` the axis and the hover use.
        value={`${bounded ? "≤ " : ""}${formatValue(raw)}`}
        {...(marks.length > 0 ? { mark: marks.join(" · ") } : {})}
        context={context}
        emphasis
      />
    </button>
  );
}

/**
 * THE MOVEMENT VOCABULARY, RESTATED OVER TWO NUMBERS.
 *
 * `DeltaLine.tsx` owns this rule and exports `deltaMark`/`directionWord`
 * for it — but both take a `MonitorsDelta`, which a chart row is not and
 * cannot be made into without inventing a `comparable`, a `sameWindow`
 * and a `deltaText` that no payload published. Forcing a fake monitor
 * through them would be worse than restating the rule, so the rule is
 * restated verbatim and `DeltaLine.tsx` is named as its source: an arrow
 * only where a direction may be claimed, "up"/"down"/"no change", and no
 * sign at all when there is nothing to claim.
 */
type ChartDeltaMark = "up" | "down" | "neutral";

function chartDeltaMark(delta: number): ChartDeltaMark {
  if (delta === 0) return "neutral";
  return delta > 0 ? "up" : "down";
}

function chartDirectionWord(mark: ChartDeltaMark): string {
  return mark === "neutral" ? "" : mark;
}

/**
 * The movement's SIZE, in the measure's own unit, UNSIGNED — the direction
 * is carried by the word beside it, exactly as the monitors digest carries
 * it ("up 7.3 points").
 *
 * `formatMeasureDelta` rather than `formatValue`, and the difference is
 * the honesty: a rate's VALUE is a percentage and a rate's MOVEMENT is
 * percentage POINTS. This file's own tooltip already states the same
 * quantity as "+35.3pp" three lines from where it states "76.9%", and a
 * card that said "up 35.3%" under a "76.9%" would print the relative
 * change confusion `DeltaLine`'s doc comment exists to prevent. No
 * percentage is ever derived here — the change is only ever the two
 * published numbers subtracted.
 */
function changeSize(delta: number, unit: ChartSpec["unit"]): string {
  return formatMeasureDelta(Math.abs(delta), unit).replace(/^\+/, "");
}

/**
 * TWO WINDOWS OF ONE MEASURE, AT ONE CATEGORY — a movement, not a chart.
 *
 * The current window at display size, the window it is compared against
 * quiet beside it, and the change stated in words. Which is what the
 * grouped bars were for, minus the part where the reader has to measure
 * two heights by eye.
 *
 * AND THE MOVEMENT IS WITHHELD WHENEVER EITHER SIDE IS NOT A
 * MEASUREMENT. A ceiling minus a measurement is not a change: subtracting
 * a suppression bound from a reading produces a number with no meaning,
 * and drawing it as a movement at display size is the same lie the solid
 * trend line through six ceilings was telling. Same for an absence — the
 * compare operator's zero-fill is the join, not a reading.
 */
function SingleDeltaCard({
  spec,
  row,
  current,
  prior,
  currentLabel,
  priorLabel,
  formatValue,
  onDrill,
}: {
  spec: ChartSpec;
  row: ChartRow;
  current: ChartSeries;
  prior: ChartSeries;
  /** `periodSeriesLabel`'s naming — "This window (Jul 2026)". */
  currentLabel: string;
  priorLabel: string;
  formatValue: (value: number) => string;
  onDrill: () => void;
}) {
  const context = markContext(spec, row);

  if (row.withheld === true) {
    return (
      <NoFigureCard
        headline="No value was published for this figure"
        note={WITHHELD_CARD_NOTE}
        context={context}
      />
    );
  }

  const currentAbsent = row.cells?.[current.key]?.absent === true;
  const priorAbsent = row.cells?.[prior.key]?.absent === true;
  const read = (key: string, missing: boolean): number | undefined => {
    if (missing) return undefined;
    const raw = row.values[key];
    return typeof raw === "number" ? raw : undefined;
  };
  const currentValue = read(current.key, currentAbsent);
  const priorValue = read(prior.key, priorAbsent);
  const currentBounded = cellBounded(row, current.key);
  const priorBounded = cellBounded(row, prior.key);
  const provisional = row.provisional === true;

  const valueText = (value: number | undefined, bounded: boolean): string =>
    // "No figure" is the tooltip's own word for a side the engine did not
    // measure. It stands where the number would be, and it is not a dash a
    // reader could take for a minus.
    value === undefined ? "No figure" : `${bounded ? "≤ " : ""}${formatValue(value)}`;
  const sideMark = (
    value: number | undefined,
    bounded: boolean,
    absentSide: boolean,
  ): string | undefined => {
    if (value === undefined) {
      return absentSide ? "an absence, not a measured zero" : "no value was published";
    }
    const marks = [bounded ? CEILING_MARK : undefined, provisional ? SETTLING_MARK : undefined].filter(
      (mark): mark is string => mark !== undefined,
    );
    return marks.length > 0 ? marks.join(" · ") : undefined;
  };

  const measured =
    currentValue !== undefined && priorValue !== undefined && !currentBounded && !priorBounded;
  const delta = measured ? currentValue - priorValue : undefined;
  const mark = delta === undefined ? undefined : chartDeltaMark(delta);
  const word = mark === undefined ? "" : chartDirectionWord(mark);
  const changeText =
    delta === undefined || mark === undefined
      ? ""
      : mark === "neutral"
        ? "no change"
        : `${word} ${changeSize(delta, spec.unit)}`;
  const currentSideMark = sideMark(currentValue, currentBounded, currentAbsent);
  const priorSideMark = sideMark(priorValue, priorBounded, priorAbsent);

  return (
    <div className="flex flex-col gap-2">
      <div className="grid gap-2 sm:grid-cols-2">
        <button
          type="button"
          onClick={onDrill}
          title={`Drill into ${row.label}`}
          className="focus-ring block w-full rounded-lg text-left"
        >
          <KeyFigure
            label={capitalizeOpening(currentLabel)}
            value={valueText(currentValue, currentBounded)}
            {...(currentSideMark !== undefined ? { mark: currentSideMark } : {})}
            context={context}
            emphasis
          />
        </button>
        {/* The window it is compared against, at the size `KeyFigure`
            reserves for "the ones beside it". Not a button: the drill this
            figure offers is into the category, and there is one category. */}
        <KeyFigure
          label={capitalizeOpening(priorLabel)}
          value={valueText(priorValue, priorBounded)}
          {...(priorSideMark !== undefined ? { mark: priorSideMark } : {})}
        />
      </div>
      {delta !== undefined && mark !== undefined ? (
        <p
          data-delta-mark={mark}
          className="num flex flex-wrap items-baseline gap-1.5 text-meta leading-snug text-foreground/80"
        >
          {/* THE MARK IS NOT A SIGN — `DeltaLine.tsx`. An arrow is a claim
              about the world and is drawn only where the direction may be
              claimed; the neutral case takes an unsigned middot, never a
              glyph a reader reads as a minus. */}
          {mark === "up" ? (
            <ArrowUpRight aria-hidden className="size-3 translate-y-0.5" />
          ) : mark === "down" ? (
            <ArrowDownRight aria-hidden className="size-3 translate-y-0.5" />
          ) : (
            <span aria-hidden className="text-muted-foreground">
              ·
            </span>
          )}
          {/* The digest's own words ("up", "down", "no change"), opened
              with a capital because this node is in sentence position and
              the owner's rule says so — the word itself is unchanged. */}
          <span>{capitalizeOpening(changeText)}</span>
        </p>
      ) : (
        <p className="text-micro leading-snug text-muted-foreground">
          {currentValue === undefined || priorValue === undefined
            ? "No movement is published between these two windows: one of them has no figure at all, and a measurement minus an absence is not a change."
            : "No movement is published between these two windows: one of them is a limit rather than a measurement, and a ceiling minus a measurement is not a change."}
        </p>
      )}
    </div>
  );
}

export interface InvestigationChartProps {
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
}

/**
 * Charts are live objects: clicking a bar emits a typed
 * `{op: "DrillInto", target}` refinement — no natural language in the
 * loop. Truncation is always surfaced ("showing top 8 of 12 — Expand").
 */
function ChartFigure({
  spec: published,
  turnId,
  windowLabel,
  comparisonWindows,
  watermarkId,
  packLabel,
  question,
  investigationId,
  caveats,
  expanded = false,
  expandable = false,
  onDrilled,
  chosenView,
  onViewChange,
}: InvestigationChartProps & {
  /**
   * This figure IS the fullscreen one: it fills the dialog rather than a
   * card, its title is carried by the dialog's header, and every measured
   * rule on it — the tick budget, the axis height, the plot's own room —
   * is re-derived at the width it is actually drawn at.
   */
  expanded?: boolean;
  /**
   * Draw the control that opens the fullscreen copy — a real
   * `Dialog.Trigger`, so Radix owns the open state, the `aria-expanded`
   * and the focus that has to come back to it. False on the fullscreen
   * copy itself, which is already inside the dialog.
   */
  expandable?: boolean;
  /**
   * A gesture that starts a NEW turn was made from inside the dialog. The
   * fullscreen figure is a picture of the turn that is on screen behind
   * it, so leaving it open over a refinement would be showing yesterday's
   * chart at 90vw while today's answer streams underneath.
   */
  onDrilled?: () => void;
  /**
   * The drawing the reader chose, held ONE LEVEL UP — in `InvestigationChart`,
   * which owns both this figure and its fullscreen copy. That is what makes
   * the choice travel into the dialog: the two mounts are the same component
   * reading the same state, so a reader who switched the rail's chart to a
   * table and then expanded it gets the table, larger, rather than the bar
   * chart they had just replaced.
   *
   * Per figure, never global. Two charts on one turn are two questions, and
   * one of them being better as a donut says nothing about the other.
   */
  chosenView?: ChartView;
  onViewChange?: (view: ChartView) => void;
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
  //
  // …AND A COMPOSITION OF ONE IS NOT A COMPOSITION. The engine declares
  // `stacked_bar` on single-measure frames too — live, "Denied dollars by
  // financial class" arrives that way with one series over six categories —
  // and a stack of one series is a bar. Honouring it cost that figure two
  // real things: a 2px card-coloured outline around every mark (the spacer
  // that keeps two stacked fills apart, drawn where there is nothing to
  // keep apart), and the measured-zero floor, which is switched off on a
  // stack because Recharts cannot honour it there.
  const stackId =
    spec.stacked && spec.kind !== "line" && spec.comparison === undefined && spec.series.length > 1
      ? spec.id
      : undefined;
  const seriesLabel = (s: ChartSeries): string =>
    periodSeriesLabel(spec, s.key, comparisonWindows) ?? displayLabel(s.label);
  /**
   * IS THERE ANYTHING TO COMPARE? See `singleMarkPolicy`. Decided from the
   * DRAWN spec, so the answer is about the picture rather than about the
   * payload — and it changes ONLY the plot area: the shell, the title, the
   * refusal banner, the notes and the whole footer row (caption, Monitor
   * this, Expand, CSV) are the same objects on all three paths, and the
   * CSV still receives the PUBLISHED spec.
   */
  const policy = singleMarkPolicy(spec);
  const asCard = policy.mode !== "chart";

  /**
   * WHICH DRAWING, AND WHICH DRAWINGS WERE ON OFFER.
   *
   * Derived from the DRAWN spec for the same reason `singleMarkPolicy` is:
   * the offered set is a statement about the picture in front of the
   * reader, and a donut offered because the payload's twelve rows add up —
   * over a figure showing eight of them plus a rollup — would be a menu
   * describing a chart that is not on screen.
   *
   * `resolveChartView` re-checks the choice rather than trusting it: a
   * persisted "donut" whose payload has since grown a ceiling falls back to
   * the bar chart, because a choice is not a licence to draw something the
   * census no longer supports.
   */
  const views = useMemo(() => chartViewForms(spec), [spec]);
  const view = resolveChartView(spec, chosenView);
  const donutCensus = useMemo(() => donutHonest(spec), [spec]);

  /**
   * COLOUR, PER MARK — see `rowColor` and `entityColor`. Computed once per
   * figure rather than per `<Cell>`, so the same hash runs twelve times and
   * not twelve times per re-render.
   */
  const entities = useMemo(() => rowsAreEntities(spec), [spec]);
  const subject = useMemo(() => subjectLabel(spec), [spec]);
  const markColor = (label: string): string => rowColor(spec, label, entities);

  /**
   * A RANKING TURNS ON ITS SIDE. See `isRankedCategorical` — rows ordered
   * by the measure, with names for labels, are read down a column. The
   * decision is the same in the card and in the dialog, because it is a
   * fact about the data rather than about the room.
   */
  const horizontal = isRankedCategorical(spec) && (view === "bar" || view === "grouped");

  const hasBounded = spec.rows.some((row) => row.bounded === true);
  const hasProvisional = spec.rows.some((row) => row.provisional === true);
  const hasWithheld = spec.rows.some((row) => row.withheld === true);

  /**
   * WHICH INK ONE MARK IS DRAWN IN.
   *
   * Per ROW where the rows are the identities — one series over a
   * categorical axis, which is what a ranking is — and per SERIES
   * otherwise, because on a multi-series figure the legend's swatch is
   * what the reader matches the mark to. See the note on `seriesColors`.
   */
  const perRowColor = spec.series.length === 1 && spec.comparison === undefined;
  const markFill = (key: string, label: string): string =>
    perRowColor ? markColor(label) : (colors[key] ?? SINGLE_HUE);

  /**
   * DOES THIS FIGURE NEED PER-MARK CELLS AT ALL?
   *
   * Three things live on a `<Cell>` and nothing else can carry them: the
   * ceiling's dashed outline, one row's own entity hue, and the subject's
   * ring. A figure with none of the three — a single-hue axis of ordered
   * buckets, all measured, nothing to point at — is drawn from the Bar's
   * own `fill` and keeps its draw-in (see `drawIn`, which explains why
   * those two facts are connected).
   */
  const needsCells =
    hasBounded ||
    hasProvisional ||
    subject !== undefined ||
    (perRowColor && entities) ||
    (perRowColor && spec.rows.some((row) => ROLLUP_LABEL.test(row.label)));

  /**
   * Draw in ONCE, fast (260ms ease-out) — Recharts' 1.5s default is a
   * different product's motion language, and it re-runs on every data
   * change. `animationId` is pinned to the spec so a re-render (theme
   * flip, focus change) never replays the draw.
   */
  const drawIn = {
    // …AND NOT AT ALL ON THE FULLSCREEN COPY, which is not a draw — the
    // marks are already on screen behind the dialog and this is the same
    // ones again, larger. It is also the one mount whose container is
    // sized AFTER it: the dialog's plot takes the height that is left over
    // from a `85vh` column, so `ResponsiveContainer` measures 0 on the
    // first pass. Recharts keys its animation to `animationId`, which is
    // pinned to the spec on purpose (a theme flip must not replay the
    // draw) — so the animation that ran at height 0 was the only one that
    // ever ran, and every bar stayed a 1px sliver on the baseline. Live:
    // twelve payers, full axis, no marks.
    //
    // …AND NOT ON A RANKING, where the motion costs a number. Recharts
    // suppresses a bar's own labels for as long as that bar is animating
    // (`BarLabelListProvider`, `showLabels: !isAnimating`), and on a
    // horizontal ranking the label at the end of the bar IS the value —
    // the y-axis a reader would otherwise read it off is carrying names
    // now. Measured live: the inline ranking drew no values at all while
    // the fullscreen copy, which already skips the draw-in, drew all
    // twelve. A 260ms flourish is not worth a figure whose numbers are
    // missing, and a list of rows is not a shape that wants to grow
    // sideways out of a wall in the first place.
    //
    // …AND NOT ON A CHART THAT CARRIES `<Cell>` CHILDREN, which is a
    // Recharts fact and a nasty one. `selectBarRectangles` takes the cell
    // list as a selector input, and Recharts derives that list from the
    // Bar's children on every render (`findAllByType`), so it is a new
    // array reference every time: the memo misses, the rectangles are
    // recomputed, `useAnimationId` sees new input and the draw-in
    // restarts. Measured live on the twelve-payer underpayment ranking:
    // the marks froze at 26.8px of the 615px they should have been —
    // a chart drawing every bar at four per cent of its own value, which
    // is not a slow animation, it is a wrong picture. It was already true
    // of every figure carrying a ceiling (those have always had cells);
    // it became true of more of them when the cells started carrying
    // colour as well. Motion is the cheaper thing to give up.
    isAnimationActive: !reducedMotion && !expanded && !horizontal && !needsCells,
    animationDuration: 260,
    animationEasing: "ease-out",
    animationId: spec.id,
  } as const;

  /** Every sentence the engine published about this figure. */
  const figureNotes = spec.notes ?? (spec.note !== undefined ? [spec.note] : []);

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

  /**
   * ONE DRILL, TWO AFFORDANCES. The bar and the figure card emit the same
   * refinement for the same row — the referent when the wire published
   * one, else `${spec.id}:${label}` — because they are the same gesture
   * on the same mark, and a card that drilled somewhere else would be a
   * second contract nobody declared.
   */
  const drillInto = (target: { label: string; referent?: string }) => {
    emitRefinement(
      { op: "DrillInto", target: target.referent ?? `${spec.id}:${target.label}` },
      { turnId, referent: target.referent },
    );
    onDrilled?.();
  };

  const handleBarClick = (entry: unknown) => {
    const payload = (entry as { payload?: RowDatum }).payload;
    if (!payload) return;
    drillInto({
      label: payload.label,
      ...(payload.referent !== undefined ? { referent: payload.referent } : {}),
    });
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
   * THE AXIS, MEASURED AGAINST THE CONTAINER IT IS DRAWN IN.
   *
   * A rule that counts characters and categories and knows nothing about
   * width gives the same five-payer chart a comfortable axis in the answer
   * column and an unreadable one in the Evidence rail: measured at 308px,
   * five flat labels up to 20 characters each drew on top of one another —
   * 111px of overlap, four colliding pairs, one illegible grey smear where
   * the payer names should be.
   *
   * So the tick budget is the BAND's, and it is measured here, per mounted
   * figure. The fullscreen copy is a second mount with a second observer,
   * which is exactly why expanding re-spells the axis instead of scaling
   * up the rail's elisions. See `axisTickPlan`.
   */
  const plotRef = useRef<HTMLDivElement>(null);
  const [plotWidth, setPlotWidth] = useState(0);
  // …and its HEIGHT, which used to be a constant this figure decided and
  // is now the extent a ranking's bands are measured along. Re-run when
  // the FORM changes, because a table has no plot node at all and the
  // observer would otherwise still be watching one that was unmounted two
  // switches ago.
  const [plotHeight, setPlotHeight] = useState(0);
  useEffect(() => {
    const node = plotRef.current;
    if (node === null || typeof ResizeObserver === "undefined") return;
    const box = node.getBoundingClientRect();
    setPlotWidth(box.width);
    setPlotHeight(box.height);
    const observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        setPlotWidth(entry.contentRect.width);
        setPlotHeight(entry.contentRect.height);
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [view]);
  const { rotate: rotateTicks, text: tickText } = useMemo(
    () =>
      axisTickPlan(
        spec.rows.map((row) => row.label),
        {
          kind: spec.kind,
          plotWidth,
          expanded,
          orientation: horizontal ? "horizontal" : "vertical",
        },
      ),
    [spec.rows, spec.kind, plotWidth, expanded, horizontal],
  );
  const categoryTick = (v: string): string =>
    honestyTick(tickText.get(v) ?? v, {
      bounded: boundedLabels.has(v),
      withheld: withheldLabels.has(v),
      absent: absentLabels.has(v),
      provisional: provisionalLabels.has(v),
    });

  /**
   * BARS THAT FILL THEIR BAND.
   *
   * What was here was a table of constants — a 34% category gap, a cap of
   * 14px on a multi-series chart, 22 on a single one, 64 below five
   * categories — and the owner's word for the result was "skinny lines …
   * they look so off". He was reading arithmetic: twelve payers in a 700px
   * plot got a 58px band each and a 14px bar in it, so a quarter of the
   * figure was data and three quarters were the gaps between it.
   *
   * The constants are gone and the rule is a RATIO: a mark takes 86% of
   * its band, near-touching its neighbour without the contiguity a
   * histogram's touching bars claim, and the cap only exists to stop two
   * categories in a fullscreen plot from painting 500px slabs. Grouped,
   * the pair fills the band together with a 2px seam between the windows —
   * they read as one category's two readings, which is what they are.
   *
   * See `barBandPlan`: one function, both orientations, because a
   * horizontal ranking's band is measured down the figure and everything
   * else about it is identical.
   */
  // The extent runs DOWN a ranking and ACROSS everything else — and it is
  // measured either way, so the fullscreen copy (whose plot is whatever
  // the dialog's column left over) gets bars sized for the room it is
  // actually drawn in rather than for the card's arithmetic.
  const bandExtent = horizontal
    ? Math.max(0, (plotHeight > 0 ? plotHeight : rankedPlotHeight(data.length, expanded)) - 24)
    : Math.max(0, plotWidth - AXIS_Y_WIDTH);
  const marksPerCategory = stackId ? 1 : spec.series.length;
  const band = barBandPlan({
    categories: data.length,
    marks: marksPerCategory,
    extent: bandExtent,
  });
  const barCap = band.maxBarSize;

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
  // The cap the axis grows against is the figure's own: a card can spare
  // 116px below its plot, a figure that owns the screen can spare 220 —
  // which is what lets a whole payer name be drawn rather than budgeted
  // for and then cut off by the SVG's bottom edge.
  const axisHeight = rotateTicks
    ? rotatedAxisHeight(renderedTicks, expanded ? EXPANDED_MAX_AXIS_HEIGHT : MAX_AXIS_HEIGHT)
    : MIN_AXIS_HEIGHT;
  /**
   * …and the same measurement turned on its side: what the gutter COSTS,
   * against what it may take. A four-payer ranking whose longest name is
   * "Atlas Commercial" does not buy 280px and squeeze the bars into a
   * third of the card — see `horizontalAxisWidth`.
   */
  const categoryAxisWidth = horizontal
    ? horizontalAxisWidth(renderedTicks, plotWidth, expanded)
    : AXIS_Y_WIDTH;

  /**
   * The room the VALUE at the end of the longest bar needs. Measured from
   * the widest string that will actually be printed rather than guessed
   * at: a value clipped by the figure's own right edge is the same defect
   * as a tick clipped by its bottom edge, on the other axis.
   */
  const valueGutter = useMemo(() => {
    if (!horizontal) return 14;
    let widest = 0;
    for (const row of data) {
      for (const s of spec.series) {
        const raw = row[s.key];
        if (typeof raw !== "number") continue;
        const bounded = cellBounded(row, s.key);
        widest = Math.max(widest, `${bounded ? "≤ " : ""}${formatValue(raw)}`.length);
      }
    }
    return Math.min(140, Math.ceil(widest * TICK_CHAR_PX) + 14);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [horizontal, data, spec.series, spec.unit]);

  /**
   * The value, drawn at the end of its own bar.
   *
   * A `content` renderer rather than a `formatter`, because the mark is
   * per (row, series) and a formatter is handed the number alone: the "≤"
   * lives on the cell, not on the value, and a ceiling printed as a bare
   * numeral beside a dashed bar is the one place on this figure where the
   * two halves of the mark could come apart.
   */
  const barEndLabel = (props: unknown, key: string): React.ReactElement | null => {
    const p = props as { x?: number; y?: number; width?: number; height?: number; index?: number };
    if (p.x === undefined || p.y === undefined || p.index === undefined) return null;
    const row = data[p.index];
    if (row === undefined) return null;
    const raw = row[key];
    if (typeof raw !== "number") return null;
    const bounded = cellBounded(row, key);
    return (
      <text
        x={p.x + (p.width ?? 0) + 6}
        y={p.y + (p.height ?? 0) / 2}
        dominantBaseline="central"
        className="num"
        fontSize={11}
        fill="var(--chart-axis)"
      >
        {`${bounded ? "≤ " : ""}${formatValue(raw)}`}
      </text>
    );
  };

  // The caption under the figure: what the axis is, then what ordered it.
  // `humanizeColumn` rather than `displayLabel` because this is a heading
  // and the wire's own word for it is a column name — "carc" is what the
  // pack calls it and "CARC" is what a reader calls it.
  //
  // The drill HINT follows the affordance it describes: a figure card has
  // no bar to click, and a caption naming one is a caption a reader
  // checks once and never trusts again.
  //
  // …AND IT FOLLOWS THE FORM, not the payload. "Click a bar to drill in"
  // over a donut names a mark that is not on the figure, which is the
  // same defect as a caption naming a glyph the axis is not wearing.
  const drillHint = asCard
    ? "Click the figure to drill in"
    : view === "table"
      ? "Click a row to drill in"
      : view === "donut"
        ? "Click a segment to drill in"
        : view === "line" || view === "area" || view === "slope"
          ? undefined
          : "Click a bar to drill in";
  const footerCaption = [
    spec.xLabel !== undefined ? humanizeColumn(spec.xLabel) : drillHint,
    orderNote(spec),
  ]
    .filter((part): part is string => part !== undefined && part !== "")
    .map(sentenceCase)
    .join(" · ");

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
    <figure
      className={cn(
        "rounded-lg border bg-card p-3.5",
        // FULLSCREEN IS THE SAME FIGURE, NOT A SECOND ONE. It loses the
        // card's frame — the dialog is already a surface, and a card drawn
        // inside a card reads as a screenshot pasted into a modal — and
        // becomes a column so the plot can take every pixel the notes and
        // the action row do not need.
        expanded && "flex h-full min-h-0 flex-col rounded-none border-0 bg-transparent px-5 py-4",
      )}
    >
      {/* Nothing to caption on a fullscreen single-series figure: the
          title is the dialog's heading and there is no legend, so the row
          is not drawn rather than drawn empty. */}
      {(!expanded || (spec.series.length > 1 && !asCard)) && (
      <figcaption
        className={cn(
          "mb-1 flex items-baseline gap-2",
          // The title is the DIALOG's heading when this figure fills one —
          // printed twice, eight pixels apart, it would read as two charts.
          expanded ? "justify-end" : "justify-between",
        )}
      >
        {!expanded && (
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
        )}
        {/* A legend is present for every multi-series chart and names each
            series beside its swatch: three of the eight light-mode hues
            sit below 3:1 on white, and the rule for that is relief — the
            identity must never be carried by colour alone. The swatch is
            the cue; the label is the fact.

            NOT ON A CARD. A legend is a key to marks in a plot, and a
            delta card has no marks: its swatches would name colours that
            appear nowhere on the figure, and each series name would be
            printed twice — once in a key to nothing, once as the label of
            the figure it belongs to. The card's own labels ARE the key. */}
        {spec.series.length > 1 && !asCard && (
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
      )}

      {/* THE RANKING WAS REFUSED — the one sentence on this figure that
          keeps the warning register, and the one that stays above the
          picture. It is a refusal, not a caveat: a reader who takes the
          leftmost bar for the worst offender has been misled by the
          drawing. Everything else this figure has to say about itself is a
          quiet caption under it (`FigureNotes`).

          At full width, because the alternative is what shipped: a bar
          chart sorted by value 400px below a banner explaining that
          ordering ceilings against measurements sorts by population
          size. */}
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
      ) : policy.mode === "figure" ? (
        /* ONE MARK — the number, not a hairline over a one-tick axis.
           See `singleMarkPolicy`. */
        <SingleFigureCard
          spec={spec}
          row={policy.row}
          series={policy.series}
          label={seriesLabel(policy.series)}
          formatValue={formatValue}
          onDrill={() =>
            drillInto({
              label: policy.row.label,
              ...(policy.row.referent !== undefined ? { referent: policy.row.referent } : {}),
            })
          }
        />
      ) : policy.mode === "delta" ? (
        /* ONE CATEGORY, TWO WINDOWS — a movement, stated. */
        <SingleDeltaCard
          spec={spec}
          row={policy.row}
          current={policy.current}
          prior={policy.prior}
          currentLabel={seriesLabel(policy.current)}
          priorLabel={seriesLabel(policy.prior)}
          formatValue={formatValue}
          onDrill={() =>
            drillInto({
              label: policy.row.label,
              ...(policy.row.referent !== undefined ? { referent: policy.row.referent } : {}),
            })
          }
        />
      ) : view === "table" ? (
        /* THE ROWS, AS ROWS. The universal fallback — see
           `ChartTableView`. Same certified rows, every mark in the cell. */
        <ChartTableView
          spec={spec}
          seriesLabel={(key) =>
            periodSeriesLabel(spec, key, comparisonWindows) ??
            displayLabel(spec.series.find((s) => s.key === key)?.label ?? key)
          }
          formatValue={formatValue}
          onDrill={(row) =>
            drillInto({
              label: row.label,
              ...(row.referent !== undefined ? { referent: row.referent } : {}),
            })
          }
          absentIn={(row, key) => absent(row, key)}
          {...(subject !== undefined ? { subject } : {})}
        />
      ) : view === "donut" ? (
        /* SHARES OF A WHOLE, and only where the census is one — see
           `donutHonest`. */
        <ChartDonutView
          spec={spec}
          formatValue={formatValue}
          colorFor={(row) => markColor(row.label)}
          onDrill={(row) =>
            drillInto({
              label: row.label,
              ...(row.referent !== undefined ? { referent: row.referent } : {}),
            })
          }
          {...(subject !== undefined ? { subject } : {})}
          expanded={expanded}
        />
      ) : view === "slope" && spec.comparison !== undefined ? (
        /* THE MOVEMENT, DRAWN AS A MOVEMENT — see `ChartSlopeView`. */
        <ChartSlopeView
          spec={spec}
          currentKey={spec.comparison.currentKey}
          priorKey={spec.comparison.priorKey}
          currentLabel={
            periodSeriesLabel(spec, spec.comparison.currentKey, comparisonWindows) ??
            WINDOW_LABEL_CURRENT
          }
          priorLabel={
            periodSeriesLabel(spec, spec.comparison.priorKey, comparisonWindows) ??
            WINDOW_LABEL_PRIOR
          }
          currentAxisLabel={comparisonWindows?.current ?? WINDOW_LABEL_CURRENT}
          priorAxisLabel={comparisonWindows?.prior ?? WINDOW_LABEL_PRIOR}
          formatValue={formatValue}
          formatTick={formatTick}
          colorFor={(row) => markColor(row.label)}
          onDrill={(row) =>
            drillInto({
              label: row.label,
              ...(row.referent !== undefined ? { referent: row.referent } : {}),
            })
          }
          {...(subject !== undefined ? { subject } : {})}
          tooltip={tooltipContent}
          expanded={expanded}
        />
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
      //
      // A HORIZONTAL RANKING IS MEASURED THE OTHER WAY. Its bands run down
      // the figure, so the figure's HEIGHT is what twelve legible bars
      // cost — `rankedPlotHeight` — and it grows with the rows exactly as
      // the rotated axis grows with its names.
      <div
        ref={plotRef}
        className={cn(
          "w-full",
          // FULLSCREEN, THE HEIGHT IS THE ROOM THAT IS LEFT. The card's
          // rule is "214px of drawing plus whatever the names need below
          // it"; in a dialog that is 85vh tall, the same arithmetic would
          // leave four hundred pixels of overlay under a chart the reader
          // opened precisely to make bigger. So the plot takes the column
          // and the notes, the caption and the actions keep their own
          // intrinsic height — with a floor, so a short viewport degrades
          // to a scroll rather than to a squashed plot.
          expanded ? "min-h-[18rem] flex-1" : !rotateTicks && !horizontal && "h-56",
        )}
        {...(horizontal
          ? {
              style: expanded
                ? { minHeight: rankedPlotHeight(data.length, false) }
                : { height: rankedPlotHeight(data.length, false) },
            }
          : rotateTicks && !expanded
            ? { style: { height: PLOT_ROOM_PX + axisHeight } }
            : {})}
      >
        <ResponsiveContainer width="100%" height="100%">
          {view === "area" ? (
            /* THE SAME TREND, WITH THE GROUND UNDER IT.
               An area says "how much", where a line says "which way" — the
               reason it is offered and not the default. Every honesty rule
               the line follows is followed here, because they are the same
               two data keys: the measured stretch is a solid fill, and any
               stretch touching a ceiling or a provisional bucket is drawn
               dashed over a much fainter fill, so the shaded quantity
               under an unmeasured point never reads as a measured one. */
            <AreaChart data={data} margin={{ top: 8, right: 18, bottom: 0, left: 6 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
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
                <Area
                  key={s.key}
                  dataKey={s.key}
                  name={seriesLabel(s)}
                  stroke={colors[s.key]}
                  strokeWidth={2}
                  fill={colors[s.key]}
                  fillOpacity={0.16}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                  {...drawIn}
                />
              ))}
              {hasQualified &&
                spec.series.map((s) => (
                  <Area
                    key={qualifiedKey(s.key)}
                    dataKey={qualifiedKey(s.key)}
                    name={`${seriesLabel(s)} (${hasBounded ? "upper bounds" : "provisional"})`}
                    stroke={colors[s.key]}
                    strokeWidth={2}
                    strokeDasharray="4 3"
                    strokeOpacity={0.75}
                    fill={colors[s.key]}
                    fillOpacity={0.05}
                    legendType="none"
                    dot={{ r: 3, strokeWidth: 1.5, fill: "var(--card)", stroke: colors[s.key] }}
                    activeDot={{ r: 4, strokeWidth: 0 }}
                    {...drawIn}
                  />
                ))}
            </AreaChart>
          ) : view === "line" ? (
            // The figure keeps its own padding. `preserveStartEnd`
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
              // Recharts calls a horizontal-bar chart `layout="vertical"`
              // — its name is for the axis that carries the categories,
              // not for the direction the bars run. What this product
              // calls a ranking is that one.
              layout={horizontal ? "vertical" : "horizontal"}
              // A rotated tick runs down and to the LEFT of its bar, so
              // the leftmost label needs a gutter the plot area does not
              // otherwise give it — and the rightmost needs one for its
              // ascender. See `rotatedAxisGutter`.
              //
              // MEASURED from the label that has to fit, not a constant:
              // at the fixed 10px, the first tick of the twelve-payer
              // ranking rendered "ate Medicaid MCO" with its head outside
              // the card (`rotatedAxisGutter`).
              //
              // Horizontal, the gutter is the y-axis's own width and the
              // margin that matters is on the RIGHT — the value printed at
              // the end of the longest bar has to fit inside the figure.
              margin={
                horizontal
                  ? { top: 4, right: valueGutter, bottom: 0, left: 0 }
                  : rotateTicks
                    ? { top: 12, right: 16, bottom: 0, left: leftGutter }
                    : { top: 12, right: 14, bottom: 0, left: 0 }
              }
              // 86% of the band to the marks, a 2px seam inside a pair —
              // see `barBandPlan`. The old pair (a 34% gap, a 14px cap)
              // is what the owner was looking at when he said the bars
              // "look so off".
              barGap={band.barGap}
              barCategoryGap={band.categoryGap}
            >
              {/* The grid runs ACROSS the measure, whichever way the bars
                  run: a gridline is a value to read a mark against, so on
                  a ranking it is vertical and on a column chart it is
                  horizontal. */}
              <CartesianGrid
                vertical={horizontal}
                horizontal={!horizontal}
                stroke="var(--chart-grid)"
              />
              {/* `interval={0}` forced every one of 150 provider ticks to
                  draw into a 208px figure. Above what an axis can hold,
                  Recharts thins them and the tooltip carries the rest:
                  an unlabelled bar is a bar you have to hover, which is a
                  far smaller cost than fifteen bars wearing one name. */}
              {horizontal ? (
                <>
                  <XAxis type="number" {...axisProps} tickFormatter={formatTick} />
                  {/* THE PAYOFF. The name reads left to right at its own
                      full length, in a gutter measured from the longest
                      one — no rotation, no −35°, and elision only where
                      the gutter's cap bites (`horizontalTickBudget`). The
                      honesty marks ride on the same tick they always did:
                      "≤ " in front, " †" / " ‡" / "*" behind. */}
                  <YAxis
                    type="category"
                    dataKey="label"
                    {...axisProps}
                    interval={0}
                    width={categoryAxisWidth}
                    tickFormatter={categoryTick}
                    tickMargin={AXIS_TICK_MARGIN}
                  />
                </>
              ) : (
                <>
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
                </>
              )}
              {/* A softer hover band. The cursor is a "you are here", not
                  a selection: at the grid's own opacity it read as a
                  second, darker gridline sliding across the plot. */}
              <Tooltip
                content={tooltipContent}
                cursor={{ fill: "var(--chart-grid)", fillOpacity: 0.6, radius: 4 }}
              />
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
                  // other than zero. Which end that is depends on which
                  // way the bar runs, so the radius turns with it.
                  radius={
                    stackId && i < spec.series.length - 1
                      ? 0
                      : horizontal
                        ? [0, 4, 4, 0]
                        : [4, 4, 0, 0]
                  }
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
                      generation of the wire was less specific.

                      DRAWN FOR THREE REASONS NOW, not one: the cell is
                      also where a row's own entity hue lands (`markColor`)
                      and where the subject's ring goes, and a loop that
                      only ran when something was bounded would have made a
                      twelve-payer ranking one colour whenever the engine
                      happened to measure all twelve.

                      AND NOT AT ALL WHERE NONE OF THE THREE APPLIES
                      (`needsCells`), because a cell list costs this figure
                      its draw-in — see `drawIn`. */}
                  {needsCells &&
                    data.map((row) => {
                      // Per-mark when the row carries per-mark detail at
                      // all, not per-mark when THIS mark happens to have
                      // an entry — a row that says "the prior cell is a
                      // ceiling" is also saying the current one is not.
                      const qualified =
                        (row.cells !== undefined
                          ? row.cells[s.key]?.bounded === true
                          : row.bounded === true) || row.provisional === true;
                      const fill = markFill(s.key, String(row.label));
                      const isSubject = subject !== undefined && row.label === subject;
                      return (
                        <Cell
                          key={`${s.key}:${row.label}`}
                          fill={fill}
                          // ON A SINGLE-HUE AXIS the emphasis is contrast, so
                          // the marks that are not the subject recede. Never
                          // on an entity axis — see `SUBJECT_RING`.
                          {...(!entities && subject !== undefined && !isSubject
                            ? { fillOpacity: RECEDED_FILL_OPACITY }
                            : {})}
                          {...(qualified ? { ...BOUNDED_MARK, stroke: fill } : {})}
                          // LAST, so the ring's ink wins the stroke colour
                          // while the ceiling keeps its dashes and its
                          // desaturated fill: an emphasis must not be able
                          // to overwrite a qualification.
                          {...(isSubject ? SUBJECT_RING : {})}
                        />
                      );
                    })}
                  {/* THE VALUE AT THE END OF ITS OWN BAR. On a ranking the
                      reader's question is "how much", and the y-axis they
                      would otherwise read it off is now carrying names.
                      The "≤" travels with it: a ceiling printed as a bare
                      number beside a bar is the same mark going missing
                      that the whole `<Cell>` loop above exists to prevent. */}
                  {horizontal && (
                    <LabelList
                      dataKey={s.key}
                      content={(props: unknown) => barEndLabel(props, s.key)}
                    />
                  )}
                </Bar>
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      )}

      {/* MARKS ON THE DATA, NOTES BELOW IT. Everything this figure has to
          say about itself — what was rolled up, the engine's own census,
          the keying, what "≤" and the tick marks mean — is one quiet
          caption under the picture, in muted ink. The marks in the drawing
          carry the signal; the caption explains them without dressing the
          figure in alarm.

          Composed as ONE string per fact rather than interpolated across
          spans: these sentences are read aloud, copied out of a screenshot
          and searched for, and a phrase split across three text nodes is
          none of those things. */}
      {(capNote !== undefined ||
        figureNotes.length > 0 ||
        spec.keying?.mode === "summed" ||
        hasBounded ||
        hasProvisional ||
        hasWithheld ||
        absentLabels.size > 0) && (
        <p className="mt-1.5 space-y-1 text-micro leading-snug text-muted-foreground">
          {/* A reader who does not know eleven series were folded into one
              mark is reading a different chart from the one the data
              supports. */}
          {capNote && <span className="block">{capNote}</span>}

          {/* The engine's own census of this figure. It rides on the wire
              as `annotations` ("upper bounds: 4 of 12 marks are ceilings,
              not measurements…") and was being fed to a `ReferenceLine` as
              an x value, where it matched no category and drew nothing at
              all.

              EVERY sentence, not the first one. A comparison publishes its
              own annotation ahead of the rest ("comparison: two series per
              category…"), so reading index 0 alone dropped the upper-bound
              census, the prior-only census and the withheld census on
              exactly the figures that carry them.

              EACH ONE OPENS ITS OWN LINE, so each one opens in capitals.
              The engine writes these as clause-led labels — "comparison:
              two series per category…", "upper bounds: 4 of 12 marks are
              ceilings…", "withheld: 1 of 8 cells were withheld outright…" —
              which is the right grammar inside a paragraph and the wrong
              one under a figure, where every sentence is a caption of its
              own. `capitalizeOpening` moves the first character and
              nothing else; the census and its numbers are untouched, and
              the CSV preamble still carries the engine's exact string. */}
          {figureNotes.map((note) => (
            <span key={note} className="block">
              {capitalizeOpening(note)}
            </span>
          ))}

          {/* The keying census. Live, a chart declaring `x=month,
              series=payer` sent thirty rows over three distinct keys; the
              old mapper kept whichever arrived last and drew $3,468 of
              $441,808. */}
          {spec.keying?.mode === "summed" && (
            <span className="block">{spec.keying.note}</span>
          )}

          {hasBounded && <span className="block">{boundedLegend(spec)}</span>}
          {/* The absence, said out loud on the figure. The engine's own
              census counts them; this says what the gap in the picture
              IS. */}
          {/* THE SAME FACTS, WITHOUT POINTING AT A GLYPH THAT IS NOT
              THERE. On a chart these sentences name the mark the axis is
              wearing; on a figure card there is no axis, so they name the
              figure instead (see the `*_CARD_NOTE` constants). Nothing is
              dropped — an honesty caption that goes quiet because the
              plot area changed shape would be the defect. */}
          {absentLabels.size > 0 && (
            <span className="block">{asCard ? ABSENT_CARD_NOTE : ABSENT_AXIS_NOTE}</span>
          )}
          {/* The VIEW's mark, not the payload's kind: the same rows drawn
              as bars wear a dashed outline rather than a dashed segment,
              and a caption naming a segment on a bar chart is a caption
              pointing at something that is not there. */}
          {hasBounded && (view === "line" || view === "area" || view === "slope") && !asCard && (
            <span className="block">
              Segments touching a ceiling are drawn dashed with a hollow point — the line between
              two ceilings is not a measured movement.
            </span>
          )}
          {hasProvisional && (
            <span className="block">{asCard ? PROVISIONAL_CARD_NOTE : PROVISIONAL_AXIS_NOTE}</span>
          )}
          {/* A blank that is a REFUSAL, said out loud. Without it a
              withheld cell and a measured 0.0% are the same nothing.
              On a card the refusal IS the plot area — `NoFigureCard`
              carries this sentence where the number would have been — so
              it is not also repeated down here. */}
          {hasWithheld && !asCard && <span className="block">{WITHHELD_AXIS_NOTE}</span>}
          {/* WHAT THE RING IS A RING OF. Only on the form that draws one:
              a note about shares under a bar chart is a caption for a
              picture that is not there. */}
          {view === "donut" && donutCensusNote(donutCensus.withheld) !== undefined && (
            <span className="block">{donutCensusNote(donutCensus.withheld)}</span>
          )}
        </p>
      )}

      <div className="mt-1.5 flex items-center justify-between gap-2">
        {/* What the axis is, and what put the marks in the order they are
            in — one caption, sentence case, middot-separated. The chart
            sits under findings that read "best to worst", so an axis whose
            order it does not state is an axis the reader has to assume,
            and the assumption was wrong: it was alphabetical. */}
        <span className="text-micro text-muted-foreground">{footerCaption}</span>
        <span className="flex shrink-0 items-center gap-1">
          {/* VIEW AS — the reader's own choice of drawing, beside Full
              screen because they are the same kind of gesture: neither one
              re-measures anything, both change how the rows already in
              this browser are shown.

              NOT ON A CARD, and not on a chart that was refused: a figure
              card is one number at display size and there is no second
              shape for one number, and a payload the keying could not draw
              at all is not drawn as a donut either. */}
          {!asCard && spec.keying?.mode !== "unkeyable" && onViewChange !== undefined && (
            <ChartViewMenu
              value={view}
              options={views}
              onChange={onViewChange}
              figureTitle={spec.title}
            />
          )}
          {/* FULL SCREEN — the readability affordance, and the reason it is
              a persistent control rather than a hover reveal.

              The Evidence rail draws this same figure 280px wide. At that
              width a twelve-payer ranking cannot spell its own axis: the
              names are elided to fit and the picture is a shape rather
              than a finding. The fix is not a bigger card — the answer
              column has one of those and the rail's chart is still the one
              a reader wants to see properly — it is the same figure, drawn
              again in a room where its own width-aware machinery reaches a
              different answer.
              PERSISTENT, in the muted ink beside the other chart actions:
              a control that appears on hover does not exist for a touch
              user, in a screenshot, or on a projector (the rule
              `MonitorThis` states at its own trigger). Tabbable in the
              card's natural order, and named for the figure it opens —
              "View full screen" alone is four identical controls on a turn
              that published four charts.
              NOT ON A CARD: a figure card is one number at display size,
              and there is nothing in it to enlarge. */}
          {expandable && !asCard && spec.keying?.mode !== "unkeyable" && (
            <DialogPrimitive.Trigger asChild>
              <Button
                variant="ghost"
                size="xs"
                aria-label={`View full screen: ${spec.title}`}
                title="Open this figure full screen, where the axis has room to spell its own labels"
                className="h-5 gap-1 px-1.5 text-micro font-normal text-muted-foreground hover:text-foreground"
              >
                <Maximize2 aria-hidden className="size-2.5" />
                Full screen
              </Button>
            </DialogPrimitive.Trigger>
          )}
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
          {/* TRUNCATION IS CONTEXT, NOT AN ALARM. "Showing top 8 of 12" is
              a fact about the drawing with the fix attached to it, and in
              amber it read as a defect in the data. Quiet ink, same words,
              same one-click expansion.

              AND IT IS NOT THE MAXIMIZE GLYPH. This control asks the
              ENGINE for the four rows it did not send — a new turn, new
              numbers — and it wore the same corner-arrows icon that now
              opens the figure full screen, one gap apart in the same row.
              Two gestures, one symbol, and the difference between them is
              a round trip to the warehouse. `ListPlus` says what this one
              does: more rows. */}
          {spec.truncation && spec.truncation.total > spec.truncation.shown && (
            <Button
              variant="ghost"
              size="xs"
              className="h-5 gap-1 px-1.5 text-meta font-normal text-muted-foreground hover:text-foreground"
              onClick={() => {
                emitRefinement({ op: "Expand" }, { turnId });
                onDrilled?.();
              }}
            >
              Showing top {spec.truncation.shown} of {spec.truncation.total}
              <ListPlus aria-hidden className="size-2.5" />
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
 * THE FIGURE, AND THE SAME FIGURE WITH ROOM TO BE READ.
 *
 * The owner's report was "we should give the ability to fullscreen graphs
 * so that they're actually readable", and the second half is the
 * requirement. Every measured rule on this chart — which labels are
 * elided, whether the axis rotates, how deep the axis is, how wide the
 * bars are — is a function of the width the figure was mounted at. So the
 * expansion is not a transform of the drawn SVG (which would enlarge the
 * elisions along with everything else) and not a second chart component
 * (which would be a second set of honesty rules to keep in step): it is
 * `ChartFigure` mounted a second time, at 90vw, where its own machinery
 * reaches different answers. The twelve-payer ranking that reads
 * "Northbridge Comm…" in a 280px rail spells the name out in the dialog
 * because `axisTickPlan` was asked again with a bigger budget.
 *
 * Everything else travels because it is the same component: the title (in
 * the dialog's own heading, so it is not printed twice), the context line,
 * every note and honesty caption, and a working action row — drill,
 * Monitor this, CSV.
 *
 * The dialog is modal: focus moves in and returns to the control that
 * opened it, Esc and the overlay close it, and the page behind it does not
 * scroll. `overlay-in`/`panel-in` are the house's own enter animations and
 * they are already switched off under `prefers-reduced-motion`
 * (`globals.css`); the draw-in inside the plot asks the same question
 * through `usePrefersReducedMotion`.
 */
export function InvestigationChart(props: InvestigationChartProps) {
  const [open, setOpen] = useState(false);
  /**
   * THE CHOSEN DRAWING LIVES HERE, above both mounts.
   *
   * Which is the whole implementation of "the choice carries into full
   * screen": the inline figure and the fullscreen one are the same
   * component rendered twice from this one piece of state, so switching
   * the rail's chart to a table and then expanding it enlarges the table.
   * Held in the dialog's own root rather than in a store — a chart form is
   * a property of looking at THIS figure, not of the session, and two
   * charts on one turn are two questions.
   *
   * `undefined` until the reader chooses, so the figure opens in the
   * drawing the engine's own kind implies and a payload that changes shape
   * under it does not inherit a stale choice (`resolveChartView`).
   */
  const [chosenView, setChosenView] = useState<ChartView | undefined>(undefined);
  const { spec, windowLabel } = props;

  return (
    // The inline figure sits INSIDE the dialog's root so its expand
    // control can be the real `Dialog.Trigger`: Radix then owns the
    // `aria-expanded`, the `aria-haspopup` and — the part a hand-rolled
    // open state gets wrong — the focus that must return to this exact
    // button when the dialog closes. A modal `Dialog.Content` focuses its
    // trigger ref on close and prevents the default restore, so with no
    // trigger registered, focus landed on `<body>`.
    <DialogPrimitive.Root open={open} onOpenChange={setOpen}>
      <ChartFigure
        {...props}
        expandable
        {...(chosenView !== undefined ? { chosenView } : {})}
        onViewChange={setChosenView}
      />
      <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="overlay-in fixed inset-0 z-50 bg-black/50 backdrop-blur-[2px]" />
          <DialogPrimitive.Content
            // NO DESCRIPTION, DELIBERATELY. Radix wants either a
            // `Description` or this explicit opt-out; everything this
            // figure has to say about itself is already inside it, as
            // captions the reader can see, and a hidden paraphrase would
            // be a second wording of the honesty notes to keep in step.
            aria-describedby={undefined}
            className="panel-in fixed left-1/2 top-1/2 z-50 flex h-[85vh] w-[90vw] max-w-[120rem] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border bg-surface-overlay shadow-2xl shadow-black/20"
          >
            <div className="flex items-start justify-between gap-4 border-b px-5 py-3">
              <DialogPrimitive.Title
                className="text-body font-semibold tracking-tight"
                title={spec.wireTitle}
              >
                {spec.title}
                {windowLabel && (
                  <span className="font-normal text-muted-foreground"> — {windowLabel}</span>
                )}
              </DialogPrimitive.Title>
              <DialogPrimitive.Close
                aria-label="Close full screen"
                className="focus-ring -mr-1 rounded p-1 text-muted-foreground hover:text-foreground"
              >
                <X aria-hidden className="size-4" />
              </DialogPrimitive.Close>
            </div>
            <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
              <ChartFigure
                {...props}
                expanded
                {...(chosenView !== undefined ? { chosenView } : {})}
                onViewChange={setChosenView}
                onDrilled={() => setOpen(false)}
              />
            </div>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
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
  // "Bounded cell" is the engine's word for it. The reader's word — the
  // one the "≤" legend under this very caption uses — is a ceiling.
  const held =
    order.boundedExcluded !== undefined
      ? `; ${order.boundedExcluded} ceiling${order.boundedExcluded === 1 ? "" : "s"} held out of it, at the end`
      : "";
  // The catalog SAID so, and the difference from the line below it is
  // worth the extra words: one is a published fact about the dimension,
  // the other is this client reading numbers out of label text.
  // The measure is named the way the rest of the product names
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
                  // A MARK ON THE DATA, in the data's own place — the
                  // absence stands where the number would be. It does not
                  // need amber to be read: nothing else in this column is
                  // a word.
                  <span className="font-normal text-muted-foreground">No figure</span>
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
                className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-muted-foreground"
              >
                {seriesLabel?.(line.key) ?? line.key} is an upper bound
                {n !== undefined ? ` over a population of ${formatCount(n)}` : ""} — a ceiling, not
                a measurement. It has no position in a ranking.
              </p>
            );
          })}
        {(currentAbsent || priorAbsent) && (
          <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-muted-foreground">
            No figure was measured for this category in that window. The zero on the wire is the
            join between the two windows, not a reading — an absence, not a measured zero.
          </p>
        )}
        {provisional && (
          <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-muted-foreground">
            Provisional — this period is not over, or its claims are still adjudicating, so the
            value will move.
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
        <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-muted-foreground">
          Upper bound
          {denominator !== undefined ? ` over a population of ${formatCount(denominator)}` : ""} — a
          ceiling, not a measurement. It has no position in a ranking.
        </p>
      )}
      {provisional && (
        <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-muted-foreground">
          Provisional — this period is not over, or its claims are still adjudicating, so the value
          will move.
        </p>
      )}
      {row?.withheld === true && (
        <p className="mt-1 max-w-56 border-t pt-1 text-meta leading-snug text-muted-foreground">
          Withheld — the engine published no value for this cell under the small-cell policy. The
          gap is a refusal, not a zero.
        </p>
      )}
    </div>
  );
}
