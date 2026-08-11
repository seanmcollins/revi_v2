/**
 * WHAT SHAPE A FIGURE MAY TAKE, WHAT COLOUR ITS MARKS ARE, AND HOW WIDE
 * THEY ARE DRAWN.
 *
 * Three decisions that used to be inline in `InvestigationChart` and are
 * pulled out here for one reason: they are the decisions a reader can
 * CHANGE (the "View as" switcher) or check (the palette, the band), and a
 * rule that can be re-run at a different answer has to be a function that
 * can be re-run in a test. Nothing in this file renders; nothing in it
 * reads the DOM.
 *
 * The honesty rules travel with the shapes rather than sitting beside
 * them: `chartViewForms` is the one place that says which drawings a given
 * payload is ALLOWED to become, and it refuses a form that cannot carry a
 * mark the payload requires. A switcher that offers a donut of ceilings is
 * a switcher that launders a suppression bound into a share of a whole.
 */

import type { ChartSpec } from "@/lib/types";

/* ------------------------------------------------------------------ */
/* Stable entity colour                                                */
/* ------------------------------------------------------------------ */

/**
 * The twelve categorical slots, in the order `globals.css` validated them.
 *
 * Order is load-bearing in two different ways and they must not be
 * confused. For SERIES the order is the assignment (slot 1, then 2, …) and
 * it is the colour-vision safety mechanism: the adjacent pairs are the
 * ones the validator gates. For ENTITIES the order is only the sequence a
 * hash indexes into — see `entityColor`.
 */
export const CATEGORICAL_SLOTS = [
  "var(--chart-cat-1)",
  "var(--chart-cat-2)",
  "var(--chart-cat-3)",
  "var(--chart-cat-4)",
  "var(--chart-cat-5)",
  "var(--chart-cat-6)",
  "var(--chart-cat-7)",
  "var(--chart-cat-8)",
  "var(--chart-cat-9)",
  "var(--chart-cat-10)",
  "var(--chart-cat-11)",
  "var(--chart-cat-12)",
] as const;

/** Not an entity: the rollup, and every category that is not a name. */
export const NEUTRAL_INK = "var(--chart-cat-other)";
/** The single hue a non-entity axis is drawn in. */
export const SINGLE_HUE = "var(--chart-current)";

/**
 * FNV-1a, 32-bit — the whole of the "deterministic assignment".
 *
 * Deliberately the dullest hash in the drawer. What this function must be
 * is STABLE: the same payer name has to index the same slot in this
 * browser, in a screenshot taken last week, and in the copy of the product
 * running in the next room, because that stability is the entire content
 * of the feature. Anything that could be tuned — a seed, a table, a
 * dependency's implementation — is a hue that moves when somebody upgrades
 * something.
 */
export function labelHash(label: string): number {
  let hash = 0x811c9dc5;
  for (let i = 0; i < label.length; i += 1) {
    hash ^= label.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

/**
 * COLOUR AS INFORMATION: one payer, one hue, everywhere.
 *
 * The rule this replaces coloured by POSITION — first series slot 1,
 * second slot 2 — so State Medicaid was blue on the denial-rate ranking,
 * orange on the underpayment ranking and green on the A/R chart three
 * paragraphs later. A reader tracking one payer across an investigation
 * was tracking nothing; the colour changed meaning between two figures on
 * the same screen. Hashing the NAME instead makes the hue a property of
 * the payer rather than of the sort order, which is what lets the eye
 * follow one entity down a page of figures.
 *
 * THE COLOUR-VISION ARGUMENT, WHICH THIS DESIGN OWES A STATEMENT.
 *
 * A hash cannot guarantee that two entities on one axis get different
 * slots, and twelve categorical hues cannot be mutually distinct under
 * dichromacy at all — under `--pairs all` the worst pair in this palette
 * is ΔE 0.7 for a deuteranope (`globals.css` names it). So hue is NOT the
 * identity channel on these figures and is never asked to be:
 *
 *   · a ranked bar is a HORIZONTAL bar, and its own name is set in full
 *     at the left of it, in text ink, at the axis's own size;
 *   · its value is printed at the end of the bar;
 *   · every multi-series figure carries a legend naming each series beside
 *     its swatch, and every mark states itself on hover;
 *   · the table view carries the whole thing with no colour at all.
 *
 * Colour here is a TRACKING aid layered on top of an identity that is
 * already written down — the case the dataviz rules call relief. What the
 * palette is gated on is the check that still binds: adjacent slots in the
 * validated sequence, which pass (ΔE 9.1 protan / 19.6 normal), and — on
 * the live twelve-payer set in both orders it is drawn in — no two
 * neighbouring bars landing on one hue. That last one is a corpus fact,
 * not a proof, and it is pinned by a test so it stays one.
 */
export function entityColor(label: string): string {
  return CATEGORICAL_SLOTS[labelHash(label) % CATEGORICAL_SLOTS.length] ?? NEUTRAL_INK;
}

/**
 * A category label that names a DATE — "2026-01-01", the month buckets a
 * trend is drawn over. Kept here rather than imported from the renderer so
 * the entity test and the tick speller cannot drift into two opinions
 * about what a date looks like.
 */
const ISO_DATE_LABEL = /^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$/;

/**
 * Dimensions whose members are TIME, not entities. Matched on the whole
 * column name and on its tail (`service_month`, `posting_week`), because
 * the warehouse spells the same bucket four ways.
 */
const TIME_COLUMN = /(^|_)(month|week|day|date|quarter|year|period|bucket_start)$/;

/**
 * ARE THESE ROWS ENTITIES?
 *
 * The question decides whether the axis gets twelve hues or one, and
 * getting it wrong in either direction is a real cost. A rainbow over
 * "0–30 days / 31–60 / 61–90 / 90+" tells the reader those four buckets
 * are four different KINDS of thing, which is exactly what an ordered
 * bucket is not — and it burns the one channel that could have carried
 * the thing the figure is about. A single hue over twelve payers throws
 * away the cross-chart tracking this palette exists for.
 *
 * An entity is a NAME the warehouse assigns to somebody: a payer, a plan,
 * a facility, a provider, a CARC. A non-entity is a position in an order
 * somebody defined: a month, an age band, a runway bucket, a window. So
 * the test is for the non-entity cases, all four of which the payload
 * states outright rather than leaving to be guessed at:
 *
 *   · the axis is windows (`windowAxis`) — one measure in two periods;
 *   · a trend (`kind: "line"`) — the x is time by construction;
 *   · the catalog published a bucket ORDER for the dimension, or this
 *     client recognised one (`axis-order` / `ordinal-bucket`);
 *   · the dimension is named for time, or its labels are ISO dates.
 *
 * Everything else is a name, and a name gets a hue.
 */
export function rowsAreEntities(spec: ChartSpec): boolean {
  if (spec.windowAxis === true) return false;
  if (spec.kind === "line") return false;
  if (spec.order?.basis === "axis-order" || spec.order?.basis === "ordinal-bucket") return false;
  if (spec.xLabel !== undefined && TIME_COLUMN.test(spec.xLabel)) return false;
  if (spec.rows.length > 0 && spec.rows.every((row) => ISO_DATE_LABEL.test(row.label))) return false;
  return true;
}

/**
 * WHICH ROW THE ANSWER IS ABOUT.
 *
 * The engine publishes it: an annotation that names a drawn category is
 * lifted onto `highlightLabel` at the boundary (`mapChartSpec`). Where it
 * published none and the rows are a value ranking, the leader is the
 * subject by construction — a chart ordered high-to-low is a chart whose
 * first mark is the finding.
 *
 * Only on a single-hue axis, and that restriction is the point: on an
 * entity axis every mark already carries its own colour, and dimming
 * eleven payers to point at the twelfth would repaint the tracking hues
 * the reader is following. There, the subject gets a ring instead
 * (`InvestigationChart`), which adds emphasis without taking identity
 * away from anything.
 */
export function subjectLabel(spec: ChartSpec): string | undefined {
  if (spec.highlightLabel !== undefined && spec.highlightLabel !== "") return spec.highlightLabel;
  if (rowsAreEntities(spec)) return undefined;
  if (spec.order?.basis !== "value" || spec.order.descending === false) return undefined;
  return spec.rows[0]?.label;
}

/* ------------------------------------------------------------------ */
/* Bars that look like bars                                            */
/* ------------------------------------------------------------------ */

/**
 * HOW MUCH OF ITS BAND A BAR TAKES.
 *
 * The owner's report was "skinny lines … they look so off", and the
 * arithmetic agreed with him: a 34% category gap with a 14–22px cap on
 * top of it drew a 12-payer ranking as twelve 14px marks in a 700px plot,
 * i.e. 24% of the room, three quarters of the picture being the gaps
 * between the data. That is a texture, not a chart.
 *
 * 86% is a bar with a breathing gap — the neighbours are near-touching
 * and still separate, which is the read for a categorical axis (a
 * histogram's bars touch because its bins are contiguous; these are not).
 * The remaining 14% is split either side of each band, so the visible gap
 * between two bars is 14% of one band.
 */
export const BAND_FILL = 0.86;

/**
 * The hairline inside a grouped pair. The two marks of a comparison are
 * one category's two windows, so they sit together and are separated by a
 * seam rather than by a gap — the pair has to read as a pair before either
 * half reads as a value.
 */
export const SERIES_HAIRLINE = 2;

/**
 * A single bar is still not allowed to become a slab. Two categories in a
 * fullscreen plot would take 500px each at 86%, which is not a bar, it is
 * a background. Above this the band keeps growing and the mark does not.
 */
export const BAR_THICKNESS_CAP = 88;

export interface BarBandPlan {
  /** `barCategoryGap` — the share of the band that is NOT bars. */
  categoryGap: string;
  /** `barGap` — the seam between two marks inside one category. */
  barGap: number;
  /** `maxBarSize` — the thickness cap, in px. */
  maxBarSize: number;
  /** The band one category owns, in px, at the measured extent. */
  band: number;
  /** What one mark actually gets, in px. */
  barSize: number;
  /** `barSize × marks / band` — the number the geometry test reads. */
  fill: number;
}

/**
 * The band, and what fits in it.
 *
 * `extent` is the plot's length ALONG THE CATEGORY AXIS: the width of a
 * column chart, the height of a horizontal ranking. Everything else is the
 * same arithmetic either way, which is the reason the two orientations
 * share one function rather than each growing their own constants.
 *
 * Returns `Infinity`-free numbers even before the container has been
 * measured (`extent: 0`), because the caller hands these to Recharts on
 * the first paint: an unmeasured chart gets the ratio and the cap, and the
 * observer corrects the pixels on the next frame.
 */
export function barBandPlan({
  categories,
  marks,
  extent,
}: {
  categories: number;
  /** Marks drawn per category — 1 for a single series or a stack. */
  marks: number;
  extent: number;
}): BarBandPlan {
  const n = Math.max(1, categories);
  const perCategory = Math.max(1, marks);
  const band = extent > 0 ? extent / n : 0;
  const seams = (perCategory - 1) * SERIES_HAIRLINE;
  const forMarks = Math.max(0, band * BAND_FILL - seams);
  const wanted = forMarks / perCategory;
  // The cap only ever binds where the band is enormous. Below it the
  // maxBarSize IS the band share, which is what makes the fill a rule
  // rather than a hope: Recharts centres a capped bar in its band, so a
  // cap lower than the share is a gap the category gap did not ask for.
  const maxBarSize = band > 0 ? Math.max(1, Math.min(BAR_THICKNESS_CAP, Math.floor(wanted))) : BAR_THICKNESS_CAP;
  const barSize = band > 0 ? Math.min(wanted, BAR_THICKNESS_CAP) : 0;
  return {
    // HALF, because Recharts spends this gap TWICE — its own arithmetic is
    // `(bandSize − 2 × gap − seams) / marks`, so a "14%" category gap takes
    // 28% of the band and the 86% rule silently became 72%. Measured live
    // before this line was written: a 31px band drawing a 22px bar.
    categoryGap: `${Math.round(((1 - BAND_FILL) / 2) * 100)}%`,
    barGap: SERIES_HAIRLINE,
    maxBarSize,
    band,
    barSize,
    fill: band > 0 ? (barSize * perCategory + seams) / band : BAND_FILL,
  };
}

/* ------------------------------------------------------------------ */
/* Rankings go horizontal                                              */
/* ------------------------------------------------------------------ */

/**
 * A RANKING IS READ DOWN A COLUMN, NOT ACROSS AN AXIS.
 *
 * Every fix this file's tick machinery has shipped — the growing width,
 * the middle cut, the measured gutter, the measured axis depth, the
 * rotation budget — is a workaround for one fact: a payer name is 20-30
 * characters and a vertical bar's band is 60px wide. Turned on its side
 * the problem disappears. The label reads left-to-right at full length in
 * a gutter that is as wide as the longest name, the order runs top to
 * bottom the way a league table does, and the value sits at the end of
 * its own bar.
 *
 * ONLY a ranking. A trend has a time axis and time runs across; a bucket
 * axis ("0–30 / 31–60 / 61–90") is an ordered scale and reads across too.
 * What earns the rotation is rows ordered BY THE MEASURE — the shape whose
 * whole content is "which is biggest", and whose labels are names.
 *
 * A REFUSED ranking does not qualify: the figure states that these rows
 * are not a league table, and drawing them as one is the contradiction the
 * refusal banner exists to prevent.
 */
export function isRankedCategorical(spec: ChartSpec): boolean {
  if (spec.kind === "line") return false;
  if (spec.order === undefined) return false;
  if (spec.order.refused === true) return false;
  if (spec.order.basis !== "value") return false;
  return spec.rows.length > 1;
}

/** Room one category gets on a horizontal ranking, before the cap bites. */
export const RANKED_BAND_PX = 34;
export const MIN_RANKED_PLOT = 168;
/** Inline: a figure that is taller than this is a page, not a card. */
export const MAX_RANKED_PLOT = 560;
/** Full screen the dialog's own column decides; this is only the floor. */
export const EXPANDED_MAX_RANKED_PLOT = 1400;

/**
 * How tall the ranking is drawn.
 *
 * It GROWS with the rows rather than squashing them into a fixed box —
 * the same rule the rotated axis already follows (`rotatedAxisHeight`):
 * a chart of twelve payers is taller than a chart of four, because twelve
 * bars at a legible thickness are taller than four. Capped, because past
 * the cap the reader is scrolling a figure rather than reading one, and
 * the row count that gets there is the case `Expand` and truncation were
 * built for.
 */
export function rankedPlotHeight(rows: number, expanded = false): number {
  const max = expanded ? EXPANDED_MAX_RANKED_PLOT : MAX_RANKED_PLOT;
  return Math.max(MIN_RANKED_PLOT, Math.min(max, Math.ceil(rows * RANKED_BAND_PX)));
}

/* ------------------------------------------------------------------ */
/* View as                                                             */
/* ------------------------------------------------------------------ */

/**
 * The drawings one payload may become. Every one of them draws the SAME
 * certified rows — switching is a client re-render, never a re-query, and
 * the CSV is byte-identical across all of them because the CSV is built
 * from the published spec and knows nothing about which of these is on
 * screen.
 */
export type ChartView = "line" | "area" | "bar" | "grouped" | "slope" | "donut" | "table";

/** What the control calls each one. Sentence case, the house register. */
export const VIEW_LABEL: Record<ChartView, string> = {
  line: "Line",
  area: "Area",
  bar: "Bar",
  grouped: "Grouped bars",
  slope: "Slope",
  donut: "Donut",
  table: "Table",
};

export type ChartShape = "series" | "comparison" | "categorical";

/** What KIND of payload this is — the thing the offered set derives from. */
export function chartShape(spec: ChartSpec): ChartShape {
  if (spec.comparison !== undefined) return "comparison";
  if (spec.kind === "line") return "series";
  return "categorical";
}

/** Every mark on the figure, flattened — the census the guards read. */
function census(spec: ChartSpec): {
  bounded: boolean;
  withheld: number;
  absent: boolean;
  negative: boolean;
} {
  let bounded = false;
  let withheld = 0;
  let absent = false;
  let negative = false;
  for (const row of spec.rows) {
    if (row.bounded === true) bounded = true;
    if (row.withheld === true) withheld += 1;
    for (const cell of Object.values(row.cells ?? {})) {
      if (cell.bounded === true) bounded = true;
      if (cell.absent === true) absent = true;
    }
    for (const value of Object.values(row.values)) if (value < 0) negative = true;
  }
  return { bounded, withheld, absent, negative };
}

/**
 * IS A DONUT AN HONEST DRAWING OF THIS PAYLOAD?
 *
 * A donut says one thing a bar chart does not: these parts are a WHOLE.
 * Every rule below is that sentence being checked, and each one is a way
 * the drawing would assert something nobody measured.
 *
 *   · ONE SERIES. Two series are two wholes, and a donut has one ring.
 *   · AN ADDITIVE UNIT. Percentages and day counts do not sum, so there
 *     is no whole for the arcs to be parts of. (The same rule
 *     `capChartSeries` applies before it will roll a tail into "+N
 *     others".)
 *   · NO NEGATIVES. A negative arc has no length; drawn as its absolute
 *     value it is a credit balance rendered as a contribution.
 *   · NOTHING TRUNCATED AT THE SOURCE. Eight of twelve payers drawn as a
 *     complete ring is a picture whose shares are all wrong — each arc
 *     claims a fraction of a total that is missing four rows. The bar
 *     chart survives truncation because a bar means "this much", not
 *     "this share of that".
 *   · NO CEILINGS. This is the one that matters most and the one a
 *     switcher would get wrong. "≤ $176,112" has no arc: the mark's
 *     LENGTH is the claim, and there is no dashed outline that turns an
 *     arc drawn at a bound into an arc that is not asserting that bound
 *     as a share. The mark cannot survive the form, so the form is not
 *     offered — rather than offered with the mark quietly dropped.
 *
 * WITHHELD IS THE EXCEPTION, and it is an exception because it CAN be
 * carried: a cell the engine published no number for is drawn as its own
 * neutral segment saying so ("and 2 withheld"), sized by nothing and
 * counted by name. A ring that silently omitted them would rescale every
 * other share to fill the gap, which is the same lie by subtraction.
 */
export function donutHonest(spec: ChartSpec): { ok: boolean; withheld: number; reason?: string } {
  const marks = census(spec);
  const drawn = spec.rows.filter((row) => row.withheld !== true);
  if (spec.series.length !== 1)
    return { ok: false, withheld: marks.withheld, reason: "two series are two wholes" };
  if (spec.unit !== "cents" && spec.unit !== "count")
    return { ok: false, withheld: marks.withheld, reason: "this unit does not add up to a whole" };
  if (marks.negative)
    return { ok: false, withheld: marks.withheld, reason: "a negative figure has no arc" };
  if (spec.truncation !== undefined && spec.truncation.total > spec.truncation.shown)
    return { ok: false, withheld: marks.withheld, reason: "these rows are not the whole" };
  if (marks.bounded || marks.absent)
    return { ok: false, withheld: marks.withheld, reason: "a ceiling is not a share" };
  if (drawn.length < 2)
    return { ok: false, withheld: marks.withheld, reason: "one part is not a whole" };
  return { ok: true, withheld: marks.withheld };
}

/**
 * THE SET OF DRAWINGS THIS FIGURE OFFERS, derived from the data's shape.
 *
 * Not a fixed menu with things greyed out: a control that lists seven
 * shapes and refuses five of them teaches the reader that the control is
 * decoration. What is offered is what this payload can honestly become,
 * and the list is short because most payloads have two or three honest
 * drawings.
 *
 * TABLE IS ALWAYS THERE. It is the form that can carry every mark this
 * product has — the ≤, the dashes, the †, the ‡, the star — because its
 * marks are text, and it is the fallback the dataviz accessibility rule
 * requires when a palette leans on relief. Any figure a reader cannot
 * read is a figure they can read as rows.
 */
export function chartViewForms(spec: ChartSpec): ChartView[] {
  const shape = chartShape(spec);
  if (shape === "comparison") return ["grouped", "slope", "table"];
  if (shape === "series") {
    // An overlay of translucent areas is unreadable past one series, and
    // stacking them would claim a composition the frame did not declare.
    const area: ChartView[] = spec.series.length === 1 ? ["area"] : [];
    return ["line", ...area, "bar", "table"];
  }
  const donut: ChartView[] = donutHonest(spec).ok ? ["donut"] : [];
  return ["bar", ...donut, "table"];
}

/** The drawing a figure opens in — the one the engine's own kind implies. */
export function defaultChartView(spec: ChartSpec): ChartView {
  const shape = chartShape(spec);
  if (shape === "comparison") return "grouped";
  if (shape === "series") return "line";
  return "bar";
}

/**
 * The view actually drawn, given what the reader chose.
 *
 * A choice is kept per figure and travels into the fullscreen copy — but
 * a figure's payload can change under a persisted choice (a refinement
 * lands, the new rows carry a ceiling, the donut stops being honest), and
 * a choice that outlives its own legality is exactly the path that would
 * draw the unpublished picture. So the choice is re-checked against the
 * offered set on every render and falls back to the default rather than
 * being trusted.
 */
export function resolveChartView(spec: ChartSpec, chosen: ChartView | undefined): ChartView {
  if (chosen !== undefined && chartViewForms(spec).includes(chosen)) return chosen;
  return defaultChartView(spec);
}
