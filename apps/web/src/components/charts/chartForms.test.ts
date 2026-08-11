/**
 * The decisions a figure makes before it draws anything.
 *
 * These are pure functions on purpose: jsdom gives a Recharts container no
 * size, so the marks themselves cannot be measured here, and every rule
 * that DECIDES a mark — how wide, what colour, which shapes are on offer —
 * lives in `chartForms` where it can be. What the component test can hold
 * is what the figure says in words; what this one holds is the arithmetic
 * and the honesty gates underneath it.
 */

import { describe, expect, it } from "vitest";

import {
  BAND_FILL,
  BAR_THICKNESS_CAP,
  barBandPlan,
  CATEGORICAL_SLOTS,
  chartShape,
  chartViewForms,
  defaultChartView,
  donutHonest,
  entityColor,
  isRankedCategorical,
  MAX_RANKED_PLOT,
  MIN_RANKED_PLOT,
  NEUTRAL_INK,
  rankedPlotHeight,
  resolveChartView,
  rowsAreEntities,
  SERIES_HAIRLINE,
  subjectLabel,
} from "@/components/charts/chartForms";
import type { ChartSpec } from "@/lib/types";

/** The twelve payers the live warehouse publishes, in the wire's order. */
const LIVE_PAYERS = [
  "Atlas Commercial",
  "Bluestone Mutual",
  "Federal Medicare",
  "Lakewood Medicaid MCO",
  "Halvern Health",
  "Northbridge Commercial",
  "Ashvale Health Plan",
  "Silverline Medicare Advantage",
  "State Medicaid",
  "State Medicaid MCO",
  "Summit Peak Medicare Advantage",
  "Veritas Comp Fund",
];

/**
 * …and the same twelve as the portfolio chart DRAWS them, ordered by
 * denied dollars. A ranking is read in this order, so this is the order
 * whose neighbours have to be told apart.
 */
const LIVE_PAYERS_RANKED = [
  "Atlas Commercial",
  "Halvern Health",
  "Silverline Medicare Advantage",
  "Bluestone Mutual",
  "Summit Peak Medicare Advantage",
  "Ashvale Health Plan",
  "State Medicaid MCO",
  "State Medicaid",
  "Northbridge Commercial",
  "Federal Medicare",
  "Veritas Comp Fund",
  "Lakewood Medicaid MCO",
];

function spec(overrides: Partial<ChartSpec> = {}): ChartSpec {
  return {
    id: "chart_main",
    kind: "bar",
    title: "Denied dollars by payer",
    unit: "cents",
    xLabel: "payer",
    series: [{ key: "denied_dollars", label: "denied dollars", role: "current" }],
    rows: [
      { label: "State Medicaid MCO", values: { denied_dollars: 100 } },
      { label: "Ashvale Health Plan", values: { denied_dollars: 80 } },
      { label: "Halvern Health", values: { denied_dollars: 40 } },
    ],
    order: { basis: "value", by: "denied_dollars", descending: true },
    ...overrides,
  };
}

/* ------------------------------------------------------------------ */

describe("bars fill their band", () => {
  it("gives one mark 86% of its category, not a quarter of it", () => {
    // Twelve payers in a 700px plot. The rule this replaces drew a 14px
    // mark in a 58px band — the "skinny lines" the owner reported.
    const plan = barBandPlan({ categories: 12, marks: 1, extent: 700 });
    expect(plan.band).toBeCloseTo(700 / 12, 5);
    expect(plan.fill).toBeCloseTo(BAND_FILL, 5);
    expect(plan.barSize).toBeGreaterThan(45);
  });

  it("halves the gap it hands Recharts, because Recharts spends it twice", () => {
    // `(bandSize − 2 × gap − seams) / marks` is the library's own
    // arithmetic. A "14%" gap there takes 28% of the band, and the 86%
    // rule silently becomes 72% — measured live as a 22px bar in a 31px
    // band before this line existed.
    const plan = barBandPlan({ categories: 12, marks: 1, extent: 700 });
    expect(plan.categoryGap).toBe("7%");
  });

  it("fills the band with the PAIR on a comparison, hairline between", () => {
    const plan = barBandPlan({ categories: 6, marks: 2, extent: 720 });
    expect(plan.barGap).toBe(SERIES_HAIRLINE);
    // Both marks plus the seam take the band share — the pair reads as one
    // category's two readings rather than as two lonely columns.
    expect(plan.fill).toBeCloseTo(BAND_FILL, 5);
    expect(plan.barSize * 2 + SERIES_HAIRLINE).toBeCloseTo(plan.band * BAND_FILL, 5);
  });

  it("caps a lone bar before it becomes a background", () => {
    // Two categories in a fullscreen plot: 86% of a 700px band is not a
    // bar, it is a wall.
    const plan = barBandPlan({ categories: 2, marks: 1, extent: 1400 });
    expect(plan.barSize).toBe(BAR_THICKNESS_CAP);
    expect(plan.maxBarSize).toBe(BAR_THICKNESS_CAP);
  });

  it("hands back the ratio before the container has been measured", () => {
    // The first paint, and any environment without `ResizeObserver`. A
    // plan that returned NaN here would reach Recharts as a bar of no
    // width at all.
    const plan = barBandPlan({ categories: 8, marks: 1, extent: 0 });
    expect(plan.fill).toBe(BAND_FILL);
    expect(Number.isFinite(plan.maxBarSize)).toBe(true);
  });

  it("never asks for a mark narrower than a pixel", () => {
    // 150 providers in a 208px rail. The band is under 1.5px and the
    // honest drawing of that is a dense figure, not a crash.
    const plan = barBandPlan({ categories: 150, marks: 1, extent: 208 });
    expect(plan.maxBarSize).toBeGreaterThanOrEqual(1);
  });
});

/* ------------------------------------------------------------------ */

describe("an entity keeps its colour", () => {
  it("gives one payer the same hue on two different figures", () => {
    // The whole feature: a reader following State Medicaid MCO down a
    // page of figures is following one colour.
    const denials = spec();
    const cash = spec({ id: "chart_cash", title: "Cash posted by payer" });
    expect(entityColor("State Medicaid MCO")).toBe(entityColor("State Medicaid MCO"));
    for (const chart of [denials, cash]) {
      expect(chart.rows.map((row) => entityColor(row.label))).toEqual([
        entityColor("State Medicaid MCO"),
        entityColor("Ashvale Health Plan"),
        entityColor("Halvern Health"),
      ]);
    }
  });

  it("does not repaint the survivors when the chart loses a row", () => {
    // The rule this replaces coloured by POSITION, so dropping the first
    // payer moved every other payer's hue one slot along.
    const before = LIVE_PAYERS.map(entityColor);
    const after = LIVE_PAYERS.slice(3).map(entityColor);
    expect(after).toEqual(before.slice(3));
  });

  it("only ever hands back a validated slot", () => {
    for (const payer of LIVE_PAYERS) {
      expect(CATEGORICAL_SLOTS).toContain(entityColor(payer));
    }
    expect(CATEGORICAL_SLOTS).toHaveLength(12);
  });

  it("tells neighbours apart on the live twelve-payer set, in both orders", () => {
    // A hash cannot GUARANTEE this — twelve names into twelve slots
    // collide, and two of these payers do share a hue. What it can be is
    // checked, on the corpus, in the orders the product actually draws:
    // no two bars that touch wear the same colour. If a thirteenth payer
    // lands in the warehouse and breaks it, this is where it says so.
    for (const order of [LIVE_PAYERS, LIVE_PAYERS_RANKED]) {
      const hues = order.map(entityColor);
      for (let i = 1; i < hues.length; i += 1) {
        expect(hues[i], `${order[i - 1]} ↔ ${order[i]}`).not.toBe(hues[i - 1]);
      }
    }
  });
});

describe("which axes are entities at all", () => {
  it("calls a payer axis an entity axis", () => {
    expect(rowsAreEntities(spec())).toBe(true);
  });

  it("does not put a rainbow over months", () => {
    expect(
      rowsAreEntities(
        spec({
          xLabel: "service_month",
          rows: [
            { label: "2026-01-01", values: { denied_dollars: 1 } },
            { label: "2026-02-01", values: { denied_dollars: 2 } },
          ],
        }),
      ),
    ).toBe(false);
  });

  it("does not put a rainbow over an ordered bucket", () => {
    // "0–30 / 31–60 / 61–90 / 90+" is a scale, and four hues over it say
    // those buckets are four different KINDS of thing.
    expect(rowsAreEntities(spec({ order: { basis: "axis-order", by: "ar_age_bucket" } }))).toBe(
      false,
    );
    expect(rowsAreEntities(spec({ order: { basis: "ordinal-bucket" } }))).toBe(false);
  });

  it("does not colour a trend or a pair of windows by identity", () => {
    expect(rowsAreEntities(spec({ kind: "line" }))).toBe(false);
    expect(rowsAreEntities(spec({ windowAxis: true }))).toBe(false);
  });

  it("keeps the rollup out of the palette", () => {
    // "+13 others" is not somebody's name.
    expect(NEUTRAL_INK).toBe("var(--chart-cat-other)");
  });
});

describe("which row the answer is about", () => {
  it("takes the subject the engine named", () => {
    expect(subjectLabel(spec({ highlightLabel: "Ashvale Health Plan" }))).toBe(
      "Ashvale Health Plan",
    );
  });

  it("dims nothing on an entity axis the engine said nothing about", () => {
    // Eleven payers faded to point at the twelfth would spend the tracking
    // hues to make an emphasis.
    expect(subjectLabel(spec())).toBeUndefined();
  });

  it("takes the leader on a single-hue ranking", () => {
    // A month axis is not an entity axis, so every mark is the one accent
    // hue and there is nothing to damage by pointing at the biggest.
    const months = spec({
      xLabel: "service_month",
      order: { basis: "value", by: "denied_dollars", descending: true },
      rows: [
        { label: "2026-03-01", values: { denied_dollars: 9 } },
        { label: "2026-01-01", values: { denied_dollars: 4 } },
      ],
    });
    expect(rowsAreEntities(months)).toBe(false);
    expect(subjectLabel(months)).toBe("2026-03-01");
  });

  it("points at nothing when the order runs the other way", () => {
    // "Low to high" makes the FIRST row the smallest, and calling it the
    // leader would emphasise the opposite of the finding.
    const months = spec({
      xLabel: "service_month",
      order: { basis: "value", by: "denied_dollars", descending: false },
      rows: [
        { label: "2026-01-01", values: { denied_dollars: 4 } },
        { label: "2026-03-01", values: { denied_dollars: 9 } },
      ],
    });
    expect(subjectLabel(months)).toBeUndefined();
  });
});

/* ------------------------------------------------------------------ */

describe("rankings turn on their side", () => {
  it("turns a chart ordered by the measure", () => {
    expect(isRankedCategorical(spec())).toBe(true);
  });

  it("leaves a trend across the page, where time runs", () => {
    expect(isRankedCategorical(spec({ kind: "line" }))).toBe(false);
  });

  it("leaves a bucket axis alone — an ordered scale reads across", () => {
    expect(isRankedCategorical(spec({ order: { basis: "axis-order" } }))).toBe(false);
    expect(isRankedCategorical(spec({ order: { basis: "wire" } }))).toBe(false);
    expect(isRankedCategorical(spec({ order: undefined }))).toBe(false);
  });

  it("does not draw a REFUSED ranking as a league table", () => {
    // The figure states that these rows are not ranked; drawing them down
    // a column in order is the contradiction the refusal banner exists to
    // prevent.
    expect(isRankedCategorical(spec({ order: { basis: "wire", refused: true } }))).toBe(false);
    expect(
      isRankedCategorical(spec({ order: { basis: "value", by: "x", refused: true } })),
    ).toBe(false);
  });

  it("grows with its rows and then stops", () => {
    expect(rankedPlotHeight(2)).toBe(MIN_RANKED_PLOT);
    expect(rankedPlotHeight(12)).toBeGreaterThan(rankedPlotHeight(6));
    expect(rankedPlotHeight(150)).toBe(MAX_RANKED_PLOT);
    expect(rankedPlotHeight(150, true)).toBeGreaterThan(MAX_RANKED_PLOT);
  });
});

/* ------------------------------------------------------------------ */

describe("the shapes a figure offers", () => {
  it("offers a trend line, area, bar and table", () => {
    const trend = spec({ kind: "line", order: { basis: "wire" } });
    expect(chartShape(trend)).toBe("series");
    expect(chartViewForms(trend)).toEqual(["line", "area", "bar", "table"]);
    expect(defaultChartView(trend)).toBe("line");
  });

  it("does not offer an area for two overlapping series", () => {
    // Translucent areas stacked on each other are unreadable, and
    // stacking them would claim a composition the frame never declared.
    const trend = spec({
      kind: "line",
      order: { basis: "wire" },
      series: [
        { key: "a", label: "A", role: "current" },
        { key: "b", label: "B", role: "baseline" },
      ],
    });
    expect(chartViewForms(trend)).toEqual(["line", "bar", "table"]);
  });

  it("offers a comparison grouped bars, a slope and a table", () => {
    const compare = spec({
      kind: "grouped_bar",
      comparison: { currentKey: "current", priorKey: "prior" },
      series: [
        { key: "current", label: "This window", role: "current", pinned: true },
        { key: "prior", label: "The window it is compared against", role: "baseline", pinned: true },
      ],
      rows: [
        { label: "State Medicaid MCO", values: { current: 10, prior: 8 } },
        { label: "Ashvale Health Plan", values: { current: 4, prior: 6 } },
      ],
    });
    expect(chartShape(compare)).toBe("comparison");
    expect(chartViewForms(compare)).toEqual(["grouped", "slope", "table"]);
    expect(defaultChartView(compare)).toBe("grouped");
  });

  it("offers a categorical chart bars, a donut and a table", () => {
    expect(chartShape(spec())).toBe("categorical");
    expect(chartViewForms(spec())).toEqual(["bar", "donut", "table"]);
    expect(defaultChartView(spec())).toBe("bar");
  });

  it("treats a declared composition of ONE series as the categorical it is", () => {
    // Live, "Denied dollars by financial class" arrives as `stacked_bar`
    // with a single measure over six categories. A stack of one series is
    // a bar, and the figure that honoured the declaration lost its
    // per-row colours, its measured-zero floor and its donut.
    const composed = spec({ stacked: true, kind: "grouped_bar" });
    expect(chartShape(composed)).toBe("categorical");
    expect(chartViewForms(composed)).toEqual(["bar", "donut", "table"]);
    expect(isRankedCategorical(composed)).toBe(true);
  });

  it("puts a table on every figure", () => {
    for (const shape of [
      spec(),
      spec({ kind: "line", order: { basis: "wire" } }),
      spec({ comparison: { currentKey: "current", priorKey: "prior" } }),
      spec({ unit: "percent" }),
    ]) {
      expect(chartViewForms(shape)).toContain("table");
    }
  });
});

describe("a donut only where the census is one", () => {
  const withRows = (rows: ChartSpec["rows"], over: Partial<ChartSpec> = {}) =>
    spec({ rows, ...over });

  it("refuses a ceiling: an arc's LENGTH is the claim, and ≤ has no length", () => {
    const bounded = withRows([
      { label: "State Medicaid MCO", values: { denied_dollars: 100 } },
      { label: "Federal Medicare", values: { denied_dollars: 40 }, bounded: true },
    ]);
    expect(donutHonest(bounded).ok).toBe(false);
    expect(donutHonest(bounded).reason).toBe("a ceiling is not a share");
    expect(chartViewForms(bounded)).toEqual(["bar", "table"]);
  });

  it("refuses a per-mark ceiling too, not only a per-row one", () => {
    const bounded = withRows([
      { label: "State Medicaid MCO", values: { denied_dollars: 100 } },
      {
        label: "Federal Medicare",
        values: { denied_dollars: 40 },
        cells: { denied_dollars: { bounded: true } },
      },
    ]);
    expect(chartViewForms(bounded)).not.toContain("donut");
  });

  it("DOES offer one where cells were withheld, and counts them", () => {
    // A withheld cell has no number, so it cannot have an arc — but it CAN
    // be a segment of the key that says so, and a ring that dropped those
    // rows silently would rescale every other share to fill the gap.
    const withheld = withRows([
      { label: "State Medicaid MCO", values: { denied_dollars: 100 } },
      { label: "Ashvale Health Plan", values: { denied_dollars: 60 } },
      { label: "Veritas Comp Fund", values: {}, withheld: true },
      { label: "Halvern Health", values: {}, withheld: true },
    ]);
    const census = donutHonest(withheld);
    expect(census.ok).toBe(true);
    expect(census.withheld).toBe(2);
    expect(chartViewForms(withheld)).toContain("donut");
  });

  it("refuses a unit that does not add up to a whole", () => {
    expect(donutHonest(spec({ unit: "percent" })).reason).toBe(
      "this unit does not add up to a whole",
    );
    expect(donutHonest(spec({ unit: "days" })).ok).toBe(false);
    expect(donutHonest(spec({ unit: "count" })).ok).toBe(true);
  });

  it("refuses rows that are not the whole", () => {
    // Eight of twelve payers drawn as a complete ring is a picture whose
    // every share is wrong. The bar chart survives truncation because a
    // bar means "this much", not "this share of that".
    expect(donutHonest(spec({ truncation: { shown: 8, total: 12 } })).reason).toBe(
      "these rows are not the whole",
    );
    expect(donutHonest(spec({ truncation: { shown: 12, total: 12 } })).ok).toBe(true);
  });

  it("refuses a negative figure, which has no arc", () => {
    const credits = withRows([
      { label: "State Medicaid MCO", values: { denied_dollars: 100 } },
      { label: "Ashvale Health Plan", values: { denied_dollars: -40 } },
    ]);
    expect(donutHonest(credits).reason).toBe("a negative figure has no arc");
  });

  it("refuses two series, which are two wholes", () => {
    const two = spec({
      series: [
        { key: "a", label: "A", role: "current" },
        { key: "b", label: "B", role: "baseline" },
      ],
    });
    expect(donutHonest(two).reason).toBe("two series are two wholes");
  });

  it("refuses an absence, which is the join and not a reading", () => {
    const absent = withRows([
      { label: "State Medicaid MCO", values: { denied_dollars: 100 } },
      {
        label: "Ashvale Health Plan",
        values: { denied_dollars: 0 },
        cells: { denied_dollars: { absent: true } },
      },
    ]);
    expect(donutHonest(absent).ok).toBe(false);
  });
});

describe("a persisted choice is re-checked, never trusted", () => {
  it("keeps a choice the payload still supports", () => {
    expect(resolveChartView(spec(), "donut")).toBe("donut");
    expect(resolveChartView(spec(), "table")).toBe("table");
  });

  it("falls back when the payload stops supporting it", () => {
    // A refinement lands, the new rows carry a ceiling, and the donut the
    // reader chose two turns ago is no longer an honest drawing of them.
    const bounded = spec({
      rows: [
        { label: "State Medicaid MCO", values: { denied_dollars: 100 } },
        { label: "Federal Medicare", values: { denied_dollars: 40 }, bounded: true },
      ],
    });
    expect(resolveChartView(bounded, "donut")).toBe("bar");
  });

  it("opens in the drawing the engine's own kind implies", () => {
    expect(resolveChartView(spec(), undefined)).toBe("bar");
    expect(resolveChartView(spec({ kind: "line", order: { basis: "wire" } }), undefined)).toBe(
      "line",
    );
  });
});
