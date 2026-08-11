/**
 * The chart has to agree with the answer above it.
 *
 * Four ways it did not, all of them on the two flagship visuals, none of
 * them publishing a wrong number and all of them making the picture argue
 * with the sentence:
 *
 *   a declared COMPOSITION (`stacked_bar`) drawn as a comparison, because
 *     the wire type was mapped to `grouped_bar` and no renderer ever set a
 *     stack;
 *   a RANKED question drawn alphabetically, because nothing propagated the
 *     resolved ordering onto the spec;
 *   an ORDINAL axis drawn lexicographically — `120+` ($14.1M) between
 *     `0-30` and `31-60`, and the filing-runway profile correct only by
 *     luck of emission order;
 *   thirty series in two colours, because `role` was `index === 0 ?
 *     'current' : 'baseline'` and the renderer knew exactly two hues.
 *
 * The payload shapes here are the live ones: `chart_unbilled_aging_profile`
 * arrives ['0-30','120+','31-60','61-90','91-120'], and the sort half of
 * the wire contract is a later batch — which is why the ordering reader is
 * exercised in several spellings and the un-sorted case asserts that
 * nothing is invented.
 */

import { describe, expect, it } from "vitest";

import {
  capChartSeries,
  isOrdinalBucketAxis,
  mapChartSpec,
  ordinalBucketKey,
  OTHERS_SERIES_KEY,
  readChartSort,
  selectPrimaryChart,
  selectRenderableCharts,
} from "@/lib/contract";
import type { ChartSpec, Finding } from "@/lib/types";

const AGING_ROWS = [
  { x: "0-30", series: null, value: 4_000_000 },
  { x: "120+", series: null, value: 14_105_051.5 },
  { x: "31-60", series: null, value: 3_000_000 },
  { x: "61-90", series: null, value: 2_000_000 },
  { x: "91-120", series: null, value: 1_000_000 },
];

const AGING_CHART = {
  id: "chart_unbilled_aging_profile",
  chart_type: "bar",
  title: "unbilled dollars — aging profile",
  frame_id: "unbilled_aging_profile",
  x: "aging_bucket",
  series: null,
  value: "unbilled_dollars",
  unit: "money_cents",
  rows: AGING_ROWS,
  annotations: [],
};

describe("ordinal buckets — a shape, not a list of known buckets", () => {
  it("places a range on its lower bound and an open edge on its number", () => {
    expect(ordinalBucketKey("0-30")).toBe(0);
    expect(ordinalBucketKey("31-60")).toBe(31);
    expect(ordinalBucketKey("61–90 days")).toBe(61);
    expect(ordinalBucketKey("90+")).toBe(90);
    expect(ordinalBucketKey(">90")).toBe(90);
    expect(ordinalBucketKey("over 120 days")).toBe(120);
    expect(ordinalBucketKey("120 or more")).toBe(120);
    // Just below the range that starts at the same number, so the two
    // cannot tie.
    expect(ordinalBucketKey("under 30")).toBe(29.5);
    expect(ordinalBucketKey("<30 days")).toBe(29.5);
  });

  it("gives no position to a label carrying no number", () => {
    // "expired" is not decided here to mean "fewer than zero days" — that
    // is a semantic the pack owns and the wire has not published.
    expect(ordinalBucketKey("expired")).toBeUndefined();
    expect(ordinalBucketKey("no filing deadline")).toBeUndefined();
  });

  it("recognizes a bucketed axis and refuses a temporal or nominal one", () => {
    expect(isOrdinalBucketAxis(["0-30", "120+", "31-60"])).toBe(true);
    expect(isOrdinalBucketAxis(["expired", "0-30", "31-60"])).toBe(true);
    expect(isOrdinalBucketAxis(["2026-02", "2026-03"])).toBe(false);
    expect(isOrdinalBucketAxis(["Atlas Commercial", "Bluestone Health"])).toBe(false);
  });

  it("orders the live aging profile up its own scale, not up the alphabet", () => {
    const spec = mapChartSpec(AGING_CHART);
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "0-30",
      "31-60",
      "61-90",
      "91-120",
      "120+",
    ]);
    expect(spec?.order?.basis).toBe("ordinal-bucket");
  });

  it("leaves an un-positioned bucket in the slot the engine emitted it in", () => {
    const spec = mapChartSpec({
      ...AGING_CHART,
      rows: [
        { x: "expired", series: null, value: 900 },
        { x: "61-90", series: null, value: 300 },
        { x: "0-30", series: null, value: 100 },
      ],
    });
    // "expired" holds its slot; the two numeric buckets swap into order.
    expect(spec?.rows.map((r) => r.label)).toEqual(["expired", "0-30", "61-90"]);
  });
});

/**
 * `ChartSpec.axis_order` — the catalog's own declared order for an ordinal
 * dimension, added late in the engine's wave-C batch (`chart_integrity.
 * apply_axis_order`). It outranks everything: the server emits its rows in
 * it and clears `sort` when it applies, and it outranks the recognizer
 * above because that recognizer is an inference and this is a published
 * fact. The recognizer stays as the fallback for payloads without one.
 *
 * The shapes here are the live ones, off :8000 —
 * `inv_6455d1b5dbd7/chart_main` carries
 * `["0-30","31-60","61-90","91-120","120+"]` and
 * `inv_e2cb50f44361/chart_filing_runway_profile` carries
 * `["expired","0-30","31-60","61-90","90+","filed"]`, which is the one that
 * matters: `expired` and `filed` carry no number, so the recognizer
 * explicitly declines to place them and the pack knows exactly where they
 * go.
 */
describe("a DECLARED axis order outranks every order this client could infer", () => {
  const RUNWAY_ORDER = ["expired", "0-30", "31-60", "61-90", "90+", "filed"];

  it("seats the live aging axis in the catalog's order and says the catalog said so", () => {
    const spec = mapChartSpec({
      ...AGING_CHART,
      x: "ar_age_bucket",
      axis_order: ["0-30", "31-60", "61-90", "91-120", "120+"],
    });
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "0-30",
      "31-60",
      "61-90",
      "91-120",
      "120+",
    ]);
    // Not `ordinal-bucket`: the caption must not credit a shape recognizer
    // for an order the pack published.
    expect(spec?.order).toEqual({ basis: "axis-order", by: "ar_age_bucket" });
  });

  it("places the labels the recognizer refuses to place, because the pack declared them", () => {
    // Emitted alphabetically, which is what the recognizer alone cannot
    // repair: it has no position for `expired` or `filed` and leaves both
    // in the slots they arrived in — `expired` fourth, `filed` sixth.
    const rows = ["0-30", "31-60", "61-90", "90+", "expired", "filed"].map((x) => ({
      x,
      series: null,
      value: 1000,
    }));
    const inferred = mapChartSpec({
      ...AGING_CHART,
      id: "chart_filing_runway_profile",
      x: "filing_runway_bucket",
      rows,
    });
    expect(inferred?.rows.map((r) => r.label)).toEqual([
      "0-30",
      "31-60",
      "61-90",
      "90+",
      "expired",
      "filed",
    ]);
    expect(inferred?.order?.basis).toBe("ordinal-bucket");

    const declared = mapChartSpec({
      ...AGING_CHART,
      id: "chart_filing_runway_profile",
      x: "filing_runway_bucket",
      rows,
      axis_order: RUNWAY_ORDER,
    });
    expect(declared?.rows.map((r) => r.label)).toEqual(RUNWAY_ORDER);
    expect(declared?.order?.basis).toBe("axis-order");
  });

  it("outranks a value sort published on the same payload", () => {
    const spec = mapChartSpec({
      ...AGING_CHART,
      x: "ar_age_bucket",
      axis_order: ["0-30", "31-60", "61-90", "91-120", "120+"],
      sort: { by: "unbilled_dollars", descending: true },
    });
    // By value this is 120+ first ($14.1M). The axis is a scale, not a
    // ranking basis, and the declared scale wins.
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "0-30",
      "31-60",
      "61-90",
      "91-120",
      "120+",
    ]);
    expect(spec?.order?.basis).toBe("axis-order");
  });

  it("seats a bucket the catalog does not declare after the declared ones, in wire order", () => {
    const spec = mapChartSpec({
      ...AGING_CHART,
      x: "ar_age_bucket",
      rows: [
        { x: "unknown", series: null, value: 5 },
        { x: "31-60", series: null, value: 3 },
        { x: "0-30", series: null, value: 1 },
        { x: "no deadline", series: null, value: 7 },
      ],
      axis_order: ["0-30", "31-60", "61-90"],
    });
    // A bucket the pack has not heard of is a fact about the data, not a
    // licence to reorder the ones it has — and the two undeclared labels
    // keep the order they arrived in relative to each other.
    expect(spec?.rows.map((r) => r.label)).toEqual(["0-30", "31-60", "unknown", "no deadline"]);
  });

  it("keeps a refused ranking refused — a declared scale is not a league table", () => {
    const spec = mapChartSpec(
      {
        ...AGING_CHART,
        x: "ar_age_bucket",
        axis_order: ["0-30", "31-60", "61-90", "91-120", "120+"],
      },
      undefined,
      { warningCodes: ["RANKING_REFUSED"] },
    );
    expect(spec?.order).toEqual({ basis: "axis-order", by: "ar_age_bucket", refused: true });
  });

  it("falls back to the recognizer when the payload declares nothing", () => {
    expect(mapChartSpec({ ...AGING_CHART, axis_order: null })?.order?.basis).toBe(
      "ordinal-bucket",
    );
    expect(mapChartSpec({ ...AGING_CHART, axis_order: [] })?.order?.basis).toBe(
      "ordinal-bucket",
    );
  });
});

describe("a published ordering is honoured; an absent one is not invented", () => {
  it("reads the orderings the wire could plausibly spell", () => {
    expect(readChartSort({ sort: { by: "denied_dollars", descending: true } })).toEqual({
      by: "denied_dollars",
      descending: true,
    });
    expect(readChartSort({ order_by: [{ by: "denial_rate", descending: false }] })).toEqual({
      by: "denial_rate",
      descending: false,
    });
    expect(readChartSort({ sort_by: "denial_rate", direction: "asc" })).toEqual({
      by: "denial_rate",
      descending: false,
    });
    expect(readChartSort({ sort: "-denied_dollars" })).toEqual({
      by: "denied_dollars",
      descending: true,
    });
    expect(readChartSort({ sort: "payer asc" })).toEqual({ by: "payer", descending: false });
    expect(readChartSort({})).toBeUndefined();
  });

  it("ranks the bars by the measure the payload ranked them on", () => {
    const spec = mapChartSpec({
      id: "chart_main",
      chart_type: "bar",
      title: "denial rate — main",
      frame_id: "main",
      x: "payer",
      series: null,
      value: "denial_rate",
      unit: "ratio",
      sort: { by: "denial_rate", descending: true },
      rows: [
        { x: "Atlas Commercial", series: null, value: 0.08 },
        { x: "Bluestone Health", series: null, value: 0.21 },
        { x: "Cascade MA", series: null, value: 0.14 },
      ],
    });
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "Bluestone Health",
      "Cascade MA",
      "Atlas Commercial",
    ]);
    expect(spec?.order).toEqual({ basis: "value", by: "denial_rate", descending: true });
  });

  it("orders bucket labels up the scale even when the ordering names the bucket column", () => {
    // "order by aging_bucket ascending" means up the axis, not up the
    // alphabet — which is the whole of the runway bug.
    const spec = mapChartSpec({
      ...AGING_CHART,
      sort: { by: "aging_bucket", descending: false },
    });
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "0-30",
      "31-60",
      "61-90",
      "91-120",
      "120+",
    ]);
  });

  it("leaves nominal categories in the engine's order when nothing published one", () => {
    const spec = mapChartSpec({
      id: "chart_main",
      chart_type: "bar",
      title: "denied dollars — main",
      frame_id: "main",
      x: "payer",
      series: null,
      value: "denied_dollars",
      unit: "money_cents",
      rows: [
        { x: "Zenith Health", series: null, value: 900 },
        { x: "Atlas Commercial", series: null, value: 100 },
      ],
    });
    expect(spec?.rows.map((r) => r.label)).toEqual(["Zenith Health", "Atlas Commercial"]);
    expect(spec?.order?.basis).toBe("wire");
  });

  it("never re-orders a time axis — sorting it by value would delete the trend", () => {
    const spec = mapChartSpec({
      id: "chart_main",
      chart_type: "line",
      title: "cash posted — main",
      frame_id: "main",
      x: "month",
      series: null,
      value: "cash_posted",
      unit: "money_cents",
      sort: { by: "cash_posted", descending: true },
      rows: [
        { x: "2026-02", series: null, value: 300 },
        { x: "2026-03", series: null, value: 100 },
        { x: "2026-04", series: null, value: 200 },
      ],
    });
    expect(spec?.rows.map((r) => r.label)).toEqual(["2026-02", "2026-03", "2026-04"]);
    expect(spec?.order?.basis).toBe("wire");
  });
});

describe("a declared composition stays a composition", () => {
  const stacked = {
    id: "chart_filing_runway_profile",
    chart_type: "stacked_bar",
    title: "claims — filing runway profile",
    frame_id: "filing_runway_profile",
    x: "runway_bucket",
    series: "plan",
    value: "claim_count",
    unit: "count",
    rows: [
      { x: "0-30", series: "Plan A", value: 10 },
      { x: "0-30", series: "Plan B", value: 5 },
      { x: "31-60", series: "Plan A", value: 8 },
      { x: "31-60", series: "Plan B", value: 3 },
    ],
    annotations: [],
  };

  it("marks a `stacked_bar` frame as stacked", () => {
    const spec = mapChartSpec(stacked);
    expect(spec?.stacked).toBe(true);
    expect(spec?.wireChartType).toBe("stacked_bar");
  });

  it("does not mark a grouped or plain bar frame as stacked", () => {
    expect(mapChartSpec({ ...stacked, chart_type: "grouped_bar" })?.stacked).toBeUndefined();
    expect(mapChartSpec({ ...stacked, chart_type: "bar" })?.stacked).toBeUndefined();
  });

  it("does not stack a single-series frame that earned a line", () => {
    const spec = mapChartSpec({
      ...stacked,
      series: null,
      rows: [
        { x: "2026-02", series: null, value: 10 },
        { x: "2026-03", series: null, value: 12 },
      ],
    });
    expect(spec?.kind).toBe("line");
    expect(spec?.stacked).toBeUndefined();
  });
});

/**
 * COMPOSITE series keys. The server's answer to rows its own axes cannot
 * tell apart is to fold the undeclared grouping column into `series` as a
 * `" / "`-joined key (`chart_integrity.enforce_row_keys`) — so a live
 * `chart_main` now declares `series: "service_line / group_code"` over
 * `"Orthopedic Surgery / CO"`, and `chart_breach_confirmation` declares
 * `"carc / plan"` over 429 keys and 492 rows.
 *
 * The client's job is to leave them alone. A composite is a NAME: nothing
 * splits it, and — the part with teeth — the collision census must not fire
 * on a payload the server re-keyed precisely so it would not.
 */
describe("composite series keys are names, not structures", () => {
  const composite = {
    id: "chart_main",
    chart_type: "stacked_bar",
    title: "denied dollars — main",
    frame_id: "main",
    x: "payer",
    series: "service_line / group_code",
    value: "denied_dollars",
    unit: "money_cents",
    rows: [
      { x: "Federal Medicare", series: "Orthopedic Surgery / CO", value: 300 },
      { x: "Federal Medicare", series: "Cardiology / CO", value: 200 },
      { x: "State Medicaid", series: "Orthopedic Surgery / CO", value: 150 },
      { x: "State Medicaid", series: "Cardiology / CO", value: 50 },
    ],
    annotations: [],
  };

  it("keeps the joined key whole as the series key and its label", () => {
    const spec = mapChartSpec(composite);
    expect(spec?.series.map((s) => s.key)).toEqual([
      "Orthopedic Surgery / CO",
      "Cardiology / CO",
    ]);
    expect(spec?.series.map((s) => s.label)).toEqual([
      "Orthopedic Surgery / CO",
      "Cardiology / CO",
    ]);
  });

  it("does not report a collision on rows the server re-keyed to be distinct", () => {
    const spec = mapChartSpec(composite);
    // This is the whole point of the server-side re-key: 4 rows, 4 keys.
    // A census firing here would print "more rows than these axes can tell
    // apart" over a chart whose axes tell them apart exactly.
    expect(spec?.keying).toBeUndefined();
    expect(spec?.rows.map((r) => r.values["Orthopedic Surgery / CO"])).toEqual([300, 150]);
  });

  it("still reports a collision when composites really do collide", () => {
    const spec = mapChartSpec({
      ...composite,
      rows: [
        ...composite.rows,
        { x: "Federal Medicare", series: "Cardiology / CO", value: 25 },
      ],
    });
    expect(spec?.keying?.mode).toBe("summed");
    expect(spec?.rows[0].values["Cardiology / CO"]).toBe(225);
  });

  it("does not sort by a measure that names no drawn composite column", () => {
    // `sort.by` is the measure; the drawn columns are entities. Guessing
    // `seriesKeys[0]` here is how an axis came to be captioned "ordered by
    // Central Physicians Plaza".
    const spec = mapChartSpec({ ...composite, sort: { by: "denied_dollars", descending: true } });
    expect(spec?.order).toEqual({ basis: "wire" });
    expect(spec?.rows.map((r) => r.label)).toEqual(["Federal Medicare", "State Medicaid"]);
  });
});

describe("the wire's suppression ceiling, read defensively", () => {
  it("carries a bound and its population when the payload publishes them", () => {
    const spec = mapChartSpec({
      id: "chart_main",
      chart_type: "bar",
      title: "denial rate — main",
      frame_id: "main",
      x: "provider",
      series: null,
      value: "denial_rate",
      unit: "ratio",
      rows: [
        { x: "Dr Reyes", series: null, value: 0.454545, bound: 0.454545, denominator: 22 },
        { x: "Dr Chen", series: null, value: 0.08 },
      ],
    });
    expect(spec?.rows[0]?.bounded).toBe(true);
    // Scaled into the display unit exactly like the value beside it.
    expect(spec?.rows[0]?.bound).toBeCloseTo(45.4545, 6);
    expect(spec?.rows[0]?.denominator).toBe(22);
    expect(spec?.rows[1]?.bounded).toBeUndefined();
  });

  it("reads the spelling the engine half is landing (`is_bound` + `bound_population`)", () => {
    const spec = mapChartSpec({
      id: "chart_main",
      chart_type: "bar",
      title: "denial rate — main",
      frame_id: "main",
      x: "provider",
      series: null,
      value: "denial_rate",
      unit: "ratio",
      rows: [
        {
          x: "Dr Reyes",
          series: null,
          value: 0.454545,
          is_bound: true,
          bound_population: 22,
          provisional: false,
        },
        { x: "Dr Chen", series: null, value: 0.08, is_bound: false, provisional: true },
      ],
    });
    expect(spec?.rows[0]?.bounded).toBe(true);
    expect(spec?.rows[0]?.denominator).toBe(22);
    expect(spec?.rows[1]?.bounded).toBeUndefined();
    expect(spec?.rows[1]?.provisional).toBe(true);
  });

  it("claims nothing on a payload generation that publishes no flag", () => {
    const spec = mapChartSpec(AGING_CHART);
    expect(spec?.rows.every((row) => row.bounded === undefined)).toBe(true);
  });
});

describe("series cap — eight slots, and the rest said out loud", () => {
  function manySeries(unit: ChartSpec["unit"], count: number): ChartSpec {
    const series = Array.from({ length: count }, (_, i) => ({
      key: `s${i}`,
      label: `CARC ${i}`,
      role: (i === 0 ? "current" : "baseline") as "current" | "baseline",
    }));
    const values: Record<string, number> = {};
    // Descending magnitude, so the largest are unambiguous.
    series.forEach((s, i) => {
      values[s.key] = (count - i) * 100;
    });
    return {
      id: "chart_denial_code_mix",
      kind: "grouped_bar",
      title: "denied dollars by CARC",
      unit,
      series,
      rows: [
        { label: "Jul", values },
        { label: "Aug", values },
      ],
    };
  }

  it("leaves a chart inside the cap untouched", () => {
    const spec = manySeries("cents", 4);
    const capped = capChartSeries(spec);
    expect(capped.spec).toBe(spec);
    expect(capped.hiddenSeries).toBe(0);
    expect(capped.note).toBeUndefined();
  });

  it("draws the largest seven and sums the rest into one stated rollup", () => {
    const capped = capChartSeries(manySeries("cents", 20));
    expect(capped.spec.series).toHaveLength(8);
    expect(capped.spec.series.at(-1)?.key).toBe(OTHERS_SERIES_KEY);
    expect(capped.spec.series.at(-1)?.label).toBe("+13 others");
    expect(capped.hiddenSeries).toBe(13);
    expect(capped.rolledUp).toBe(true);
    expect(capped.note).toContain("+13 others");
    // 13 tail series at 100 … 1300 in this fixture.
    const rollup = capped.spec.rows[0]?.values[OTHERS_SERIES_KEY];
    expect(rollup).toBe(
      Array.from({ length: 13 }, (_, i) => (13 - i) * 100).reduce((a, b) => a + b, 0),
    );
  });

  it("refuses to sum a tail of percentages, and says why", () => {
    const capped = capChartSeries(manySeries("percent", 20));
    expect(capped.rolledUp).toBe(false);
    expect(capped.spec.series).toHaveLength(7);
    expect(capped.spec.rows[0]?.values[OTHERS_SERIES_KEY]).toBeUndefined();
    expect(capped.note).toContain("do not add up");
  });

  it("keeps survivors in wire order, so a colour follows the entity and not its rank", () => {
    const spec = manySeries("cents", 12);
    const capped = capChartSeries(spec);
    const kept = capped.spec.series.filter((s) => s.key !== OTHERS_SERIES_KEY);
    expect(kept.map((s) => s.key)).toEqual(["s0", "s1", "s2", "s3", "s4", "s5", "s6"]);
  });

  it("keeps a pinned comparison series however small it is", () => {
    const spec = manySeries("cents", 20);
    const withCompare: ChartSpec = {
      ...spec,
      // The smallest column on the chart, and the other half of what its
      // title claims.
      series: [
        ...spec.series,
        { key: "prior", label: "prior", role: "baseline", pinned: true },
      ],
      rows: spec.rows.map((row) => ({ ...row, values: { ...row.values, prior: 1 } })),
    };
    const capped = capChartSeries(withCompare);
    expect(capped.spec.series.map((s) => s.key)).toContain("prior");
    expect(capped.spec.series).toHaveLength(8);
  });

  it("never fabricates a zero for a row whose tail is empty", () => {
    const spec = manySeries("cents", 20);
    const sparse: ChartSpec = {
      ...spec,
      rows: [
        spec.rows[0]!,
        // A row carrying only the leading series: nothing in the tail.
        { label: "Sep", values: { s0: 900 } },
      ],
    };
    const capped = capChartSeries(sparse);
    expect(capped.spec.rows[1]?.values[OTHERS_SERIES_KEY]).toBeUndefined();
  });
});

/* ------------------------------------------------------------------ */
/* One chart, two windows (round-5 D-02, client half)                  */
/* ------------------------------------------------------------------ */

/**
 * The engine emits exactly ONE chart per comparison now: `series:
 * "period"`, rows labelled `current` and `prior`, 12 categories × 2, and
 * no byte-identical base twin beside it. The payloads below are the live
 * ones, off `GET /v1/investigations/inv_76852c0ddaa6` (denied dollars by
 * payer, July against June — 24 rows, declared `stacked_bar`) and
 * `inv_0899f9defc32/chart_main__compare` (denial rate by plan — prior-side
 * ceilings, two prior-only plans and five annotations on one figure).
 *
 * Three things have to be true of the mapping, and the first is the one
 * with teeth: the frame declares `stacked_bar`, and stacking July on June
 * produces a column whose height is the sum of two windows — a number
 * nobody computed, under an annotation that ends "They are not summed".
 */
describe("a comparison is one chart with two windows in it", () => {
  const compare = {
    id: "chart_main__compare",
    chart_type: "stacked_bar",
    title: "denied dollars — main  compare",
    frame_id: "main__compare",
    x: "payer",
    series: "period",
    value: "denied_dollars",
    unit: "money_cents",
    rows: [
      { x: "Atlas Commercial", series: "current", value: 13_680_438, referent_id: "D1" },
      { x: "Atlas Commercial", series: "prior", value: 16_048_793, referent_id: "D1" },
      { x: "Lakewood Medicaid MCO", series: "current", value: 10_240_987, referent_id: "F2" },
      { x: "Lakewood Medicaid MCO", series: "prior", value: 1_978_647, referent_id: "F2" },
    ],
    annotations: [
      "comparison: two series per category — current is this window and prior is the window it is compared against. They are not summed.",
    ],
  };

  it("never stacks the two windows, whatever chart_type the frame declares", () => {
    const spec = mapChartSpec(compare);
    expect(spec?.wireChartType).toBe("stacked_bar");
    expect(spec?.stacked).toBeUndefined();
    expect(spec?.kind).toBe("grouped_bar");
  });

  it("names the pair, and pins both halves against a series cap", () => {
    const spec = mapChartSpec(compare);
    expect(spec?.comparison).toEqual({ currentKey: "current", priorKey: "prior" });
    expect(spec?.series).toEqual([
      { key: "current", label: "This window", role: "current", pinned: true },
      { key: "prior", label: "The window compared against", role: "baseline", pinned: true },
    ]);
    expect(spec?.rows[1]).toMatchObject({
      label: "Lakewood Medicaid MCO",
      values: { current: 10_240_987, prior: 1_978_647 },
      referent: "F2",
    });
  });

  it("draws this window first however the wire emitted the two", () => {
    const spec = mapChartSpec({
      ...compare,
      rows: [...compare.rows].reverse(),
    });
    expect(spec?.series.map((s) => s.key)).toEqual(["current", "prior"]);
    expect(spec?.series.map((s) => s.role)).toEqual(["current", "baseline"]);
  });

  it("leaves a compare frame that kept a real second dimension alone", () => {
    // `denial_code_mix__compare` is a comparison OUTPUT whose series is
    // `carc` — two dimensions plus two windows is a third axis, so the
    // engine keeps drawing the current window and says so. It is a
    // composition and it keeps its stack.
    const spec = mapChartSpec({
      ...compare,
      series: "carc",
      rows: [
        { x: "Atlas Commercial", series: "16", value: 100 },
        { x: "Atlas Commercial", series: "197", value: 200 },
      ],
    });
    expect(spec?.comparison).toBeUndefined();
    expect(spec?.stacked).toBe(true);
  });

  it("flags a ceiling on the side it was published on, not on the category", () => {
    // Live (`inv_0899f9defc32`): Halvern HMO Care's JUNE numerator was
    // suppressed and its July one was not. Held on the row alone, that
    // ceiling desaturated both bars and printed one `n` under both.
    const spec = mapChartSpec({
      ...compare,
      unit: "ratio",
      value: "denial_rate",
      rows: [
        { x: "Halvern PPO Prime", series: "current", value: 0.769231, is_bound: true, bound_population: 13 },
        { x: "Halvern PPO Prime", series: "prior", value: 0.416667, is_bound: true, bound_population: 24 },
        { x: "Halvern HMO Care", series: "current", value: 0.2 },
        { x: "Halvern HMO Care", series: "prior", value: 0.357143, is_bound: true, bound_population: 28 },
      ],
    });
    expect(spec?.rows[1]?.cells).toEqual({
      prior: { bounded: true, denominator: 28 },
    });
    // The category still counts as holding a ceiling — that is what the
    // axis tick and the ordering rule read — but the current mark does not.
    expect(spec?.rows[1]?.bounded).toBe(true);
    expect(spec?.rows[0]?.cells).toEqual({
      current: { bounded: true, denominator: 13 },
      prior: { bounded: true, denominator: 24 },
    });
  });

  it("marks a prior-only category as an absence rather than a measured zero", () => {
    // The compare operator outer-joins and zero-fills additive units, so a
    // payer present only in June arrives with a July value of 0. The
    // engine counts those by exactly this rule and says so in words.
    const spec = mapChartSpec({
      ...compare,
      rows: [
        { x: "Atlas Commercial", series: "current", value: 13_680_438 },
        { x: "Atlas Commercial", series: "prior", value: 16_048_793 },
        { x: "Gone This Month", series: "current", value: 0 },
        { x: "Gone This Month", series: "prior", value: 900_000 },
        { x: "New This Month", series: "current", value: 500_000 },
        { x: "New This Month", series: "prior", value: 0 },
      ],
      annotations: [
        ...compare.annotations,
        "prior-only categories: 1 of 3 categories carry a figure on the comparison window and none on this one — their current mark is an absence, not a measured zero.",
      ],
    });
    expect(spec?.rows[1]?.cells).toEqual({ current: { absent: true } });
    // The value is NOT deleted — the CSV keeps what the wire sent — and a
    // category that is new this month is a measured zero on the OTHER
    // side, which is a different fact and is not marked.
    expect(spec?.rows[1]?.values.current).toBe(0);
    expect(spec?.rows[2]?.cells).toBeUndefined();
    expect(spec?.notes?.[1]).toContain("an absence, not a measured zero");
  });

  it("carries every sentence the engine wrote about the figure, not the first", () => {
    // Live, `inv_0899f9defc32/chart_main__compare` publishes five. The
    // comparison sentence is always index 0, so reading it alone dropped
    // the prior-only census, the upper-bound census, the refused ranking
    // and the withheld census on the figures that carry the most of them.
    const spec = mapChartSpec({
      ...compare,
      annotations: [
        ...compare.annotations,
        "prior-only categories: 2 of 4 categories carry a figure on the comparison window and none on this one — their current mark is an absence, not a measured zero.",
        "upper bounds: 1 of 4 marks are ceilings, not measurements — their numerator was suppressed and they cannot be ranked against the measured marks",
        "withheld: 3 of 4 cells were withheld outright per the small-cell policy and are drawn with no value",
      ],
    });
    expect(spec?.notes).toHaveLength(4);
    expect(spec?.note).toBe(spec?.notes?.[0]);
    expect(spec?.notes?.[2]).toContain("upper bounds: 1 of 4 marks");
  });

  it("orders a comparison by this window, and names the measure that ordered it", () => {
    const spec = mapChartSpec({
      ...compare,
      sort: { by: "denied_dollars", descending: true },
    });
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "Atlas Commercial",
      "Lakewood Medicaid MCO",
    ]);
    // Never "ordered by current": the caption names the measure the wire
    // sorted on, which is what the reader asked for.
    expect(spec?.order).toEqual({ basis: "value", by: "denied_dollars", descending: true });
  });
});

/* ------------------------------------------------------------------ */
/* A frame with no dimension has a WINDOW axis, not a value axis        */
/* ------------------------------------------------------------------ */

/**
 * ROUND 8's FIGURE DEFECT, read defensively at the client.
 *
 * Live, "Why did our denial rate go up in July 2026?" drew a chart whose
 * entire category axis was the single tick `0.127591`, and the prior
 * window's mark (0.091386) was filed under the current value's category
 * key: a frame with no dimension has no x column, and the engine fell back
 * to the VALUE column for one. 18 of 85 stored specs on the demo tenant
 * carry an axis like it, including "What is my denial rate?" and "days in
 * A/R for Atlas Commercial" (x `179.468320`).
 *
 * The engine is being fixed to key that axis on the PERIOD. Both shapes
 * are stored and both must read, so both are exercised here — and neither
 * is allowed to put a bare decimal on screen as a category.
 */
describe("a dimensionless frame is drawn over its window, never over its own value", () => {
  const WINDOWS = { current: "Jul 2026", prior: "Jun 2026" };

  const scalar = {
    id: "chart_main",
    chart_type: "bar",
    title: "denial rate — main",
    frame_id: "main",
    // The tell: the x column IS the measure, because there is no dimension.
    x: "denial_rate",
    series: null,
    value: "denial_rate",
    unit: "ratio",
    rows: [{ x: "0.127591", series: null, value: 0.127591 }],
    annotations: [],
  };

  it("OLD SHAPE, scalar: the tick is the window, not the number", () => {
    const spec = mapChartSpec(scalar, undefined, { windows: WINDOWS });
    expect(spec?.rows.map((r) => r.label)).toEqual(["Jul 2026"]);
    // The value is untouched — only the category it was filed under was
    // ever wrong.
    expect(spec?.rows[0]?.values).toEqual({ denial_rate: 12.7591 });
    // And the axis is captioned for what its ticks are. "denial rate"
    // under a tick reading "Jul 2026" names the wrong thing.
    expect(spec?.xLabel).toBe("window");
  });

  it("OLD SHAPE, comparison: one column of the measure, two window marks", () => {
    const spec = mapChartSpec(
      {
        ...scalar,
        id: "chart_main__compare",
        frame_id: "main__compare",
        series: "period",
        rows: [
          { x: "0.127591", series: "current", value: 0.127591 },
          { x: "0.127591", series: "prior", value: 0.091386 },
        ],
      },
      undefined,
      { windows: WINDOWS },
    );
    // The two windows are the two MARKS (the legend names their dates), so
    // the single column is the measure — never one window's number, and
    // never one window's label over a pair that includes the other.
    expect(spec?.rows.map((r) => r.label)).toEqual(["Denial rate"]);
    expect(spec?.rows[0]?.values).toEqual({ current: 12.7591, prior: 9.1386 });
    expect(spec?.comparison).toEqual({ currentKey: "current", priorKey: "prior" });
    // Two readings, not a series: paired bars, never a two-point line.
    expect(spec?.kind).toBe("grouped_bar");
  });

  it("NEW SHAPE: period keys become the windows they stand for", () => {
    const spec = mapChartSpec(
      {
        ...scalar,
        x: "period",
        rows: [
          { x: "current", series: null, value: 0.127591 },
          { x: "prior", series: null, value: 0.091386 },
        ],
      },
      undefined,
      { windows: WINDOWS },
    );
    expect(spec?.rows.map((r) => r.label)).toEqual(["Jul 2026", "Jun 2026"]);
    expect(spec?.xLabel).toBe("window");
    // `current`/`prior` are the engine's bookkeeping, and an axis reading
    // "current" tells a reader nothing about which dates it covers.
    expect(spec?.kind).toBe("bar");
    // This window first, in the order the engine emitted them: a value
    // sort here would put June ahead of July whenever June was larger.
    expect(spec?.order).toEqual({ basis: "wire" });
  });

  it("names the measure when the turn published no window at all", () => {
    // A streamed frame arrives before any header does. The floor is a true
    // statement about the column — never the measurement as its own label.
    const spec = mapChartSpec(scalar);
    expect(spec?.rows.map((r) => r.label)).toEqual(["Denial rate"]);
  });

  it("takes the governed display name over the raw column when there is one", () => {
    const spec = mapChartSpec(
      { ...scalar, x: "dnfb_dollars", value: "dnfb_dollars", unit: "money_cents",
        rows: [{ x: "19587392", series: null, value: 19_587_392 }] },
      { dnfb_dollars: { metricId: "dnfb_dollars", displayName: "Discharged not final billed" } },
    );
    expect(spec?.rows.map((r) => r.label)).toEqual(["Discharged not final billed"]);
  });

  it("says the moment, not a range, on a snapshot contract", () => {
    // An A/R balance taken as of a date was never measured over a window,
    // and an axis reading "Jul 2026" over it would re-commit the header
    // defect this codebase already fixed once.
    const spec = mapChartSpec({ ...scalar, x: "ar_over_90", value: "ar_over_90", unit: "days",
      rows: [{ x: "179.468320", series: null, value: 179.46832 }] },
      undefined,
      { windows: { current: "as of 2026-08-02" } },
    );
    expect(spec?.rows.map((r) => r.label)).toEqual(["as of 2026-08-02"]);
  });

  it("leaves a REAL dimension whose members happen to be numeric alone", () => {
    // CARC codes are numbers and they are categories. The rule is scoped
    // to a frame whose x column is the measure itself, so this is
    // untouched — the guard against a fix that renames real cells.
    const spec = mapChartSpec(
      {
        id: "chart_by_carc",
        chart_type: "bar",
        title: "denied dollars — by carc",
        frame_id: "by_carc",
        x: "carc",
        series: null,
        value: "denied_dollars",
        unit: "money_cents",
        rows: [
          { x: "16", series: null, value: 100 },
          { x: "197", series: null, value: 200 },
        ],
        annotations: [],
      },
      undefined,
      { windows: WINDOWS },
    );
    expect(spec?.rows.map((r) => r.label)).toEqual(["16", "197"]);
  });
});

/**
 * THE LIVE PAYLOAD, this hour, off `POST /v1/sessions/{sid}/turns`
 * {"utterance": "What is my denial rate?"} against the deployment on :8000
 * with the engine's FIX-8 half landed:
 *
 *   chart_main | chart_type bar | x "period" | series null | value denial_rate
 *   rows: [{x: "this window", series: null, value: 0.127591}]
 *
 * The axis is no longer the measurement — and the tick is the engine's own
 * placeholder, because its composer was handed no dates. This client was
 * handed them: they are on `context_header` of the same response.
 */
describe("the engine's period axis, read as the client finds it", () => {
  const LIVE_SCALAR = {
    id: "chart_main",
    chart_type: "bar",
    title: "denial rate — main",
    frame_id: "main",
    x: "period",
    series: null,
    value: "denial_rate",
    unit: "ratio",
    rows: [{ x: "this window", series: null, value: 0.127591, referent_id: null }],
    annotations: [],
  };

  it("dates the engine's placeholder tick from the turn's own header", () => {
    const spec = mapChartSpec(LIVE_SCALAR, undefined, { windows: { current: "Jul 2026" } });
    expect(spec?.rows.map((r) => r.label)).toEqual(["Jul 2026"]);
    expect(spec?.xLabel).toBe("window");
  });

  it("leaves the engine's own words alone when there are no dates to say", () => {
    const spec = mapChartSpec(LIVE_SCALAR);
    expect(spec?.rows.map((r) => r.label)).toEqual(["this window"]);
  });

  it("draws the stat figure instead of dropping it for having one mark", () => {
    // The two-mark rule is for a lone point on a CATEGORY axis. A labelled
    // column over a window claims no trend, and dropping it is why the
    // first question anyone asks rendered no picture at all.
    const spec = mapChartSpec(LIVE_SCALAR, undefined, { windows: { current: "Jul 2026" } })!;
    expect(spec.windowAxis).toBe(true);
    expect(selectRenderableCharts([spec]).map((c) => c.id)).toEqual(["chart_main"]);
  });

  it("still drops a lone point on a category axis", () => {
    const lonely = mapChartSpec({
      ...LIVE_SCALAR,
      x: "payer",
      rows: [{ x: "Atlas Commercial", series: null, value: 0.1 }],
    })!;
    expect(lonely.windowAxis).toBeUndefined();
    expect(selectRenderableCharts([lonely])).toEqual([]);
  });

  it("does not call a window's own mark an absence on the two-window shape", () => {
    // The engine gives each window its own tick and its own series
    // (baseline first, left to right as time). Read as an ordinary
    // comparison that is 'a category whose current mark is missing', and
    // the figure would print "‡ … an absence, not a measured zero" under
    // June — about a mark that is exactly where it belongs.
    const spec = mapChartSpec(
      {
        ...LIVE_SCALAR,
        id: "chart_main__compare",
        frame_id: "main__compare",
        rows: [
          { x: "Jun 2026", series: "prior", value: 0.091386 },
          { x: "Jul 2026", series: "current", value: 0.127591 },
        ],
      },
      undefined,
      { windows: { current: "Jul 2026", prior: "Jun 2026" } },
    );
    expect(spec?.rows.map((r) => r.label)).toEqual(["Jun 2026", "Jul 2026"]);
    expect(spec?.rows.every((r) => r.cells === undefined)).toBe(true);
    // Two windows, side by side, in the order the engine drew them.
    expect(spec?.kind).toBe("grouped_bar");
    expect(spec?.order).toEqual({ basis: "wire" });
    expect(spec?.stacked).toBeUndefined();
  });
});

/**
 * WHICH OF FOUR FIGURES THE ANSWER LEADS WITH.
 *
 * Captured from a live turn — "how are we doing on A/R?", watermark
 * wm_003. Four frames, no `main` among them (a playbook turn names its
 * frames after the plan nodes that ran), and three findings, every one of
 * them measuring `denied_ar_dollars` by payer.
 *
 * The old fallback drew `charts[0]` — `ar_profile`, which is `ar_balance`
 * by age bucket, and which is first only because it is the first node in
 * the plan. So the calm layout's one figure was about a different measure
 * from every sentence of its own write-up, and the payer chart the answer
 * was actually about sat behind "3 more charts".
 */
describe("selectPrimaryChart — the figure the write-up is about", () => {
  const frame = (frameId: string, value: string, x: string): ChartSpec =>
    mapChartSpec({
      id: `chart_${frameId}`,
      frame_id: frameId,
      chart_type: "bar",
      title: `${value} — ${frameId}`,
      x,
      value,
      unit: "money_cents",
      rows: [
        { x: "Atlas Commercial", series: null, value: 26_745_409 },
        { x: "State Medicaid", series: null, value: 33_594_858 },
      ],
    })!;

  const CHARTS: ChartSpec[] = [
    frame("ar_profile", "ar_balance", "ar_age_bucket"),
    frame("ar_aging_by_payer", "ar_over_90_pct", "payer"),
    frame("denied_inventory", "denied_ar_dollars", "payer"),
    frame("denied_inventory_aging", "denied_ar_dollars", "payer"),
  ];

  const finding = (referent: string, metricId: string): Finding => ({
    referent: { value: referent, kind: "finding" },
    title: `${referent}: a measured cell`,
    statement: "",
    metricRefs: [metricId],
    values: {},
    grade: "direct",
    directionOfGood: "neutral",
    confidence: "high",
    suggestedRefinements: [],
  });

  it("leads with the chart drawing what the findings measure", () => {
    const primary = selectPrimaryChart(CHARTS, [
      finding("F1", "denied_ar_dollars"),
      finding("F2", "denied_ar_dollars"),
    ]);
    expect(primary?.frameId).toBe("denied_inventory");
    // Wire order among the two frames that draw it — never a re-rank by
    // size, which would promote the 60-row aging frame over the answer.
    expect(primary?.measureId).toBe("denied_ar_dollars");
  });

  it("gives the LEADING finding first claim on the figure", () => {
    expect(
      selectPrimaryChart(CHARTS, [
        finding("F1", "ar_over_90_pct"),
        finding("F2", "denied_ar_dollars"),
      ])?.frameId,
    ).toBe("ar_aging_by_payer");
  });

  it("never overrides the engine — a named main frame still leads", () => {
    const withMain = [frame("main", "denial_rate", "payer"), ...CHARTS];
    expect(
      selectPrimaryChart(withMain, [finding("F1", "denied_ar_dollars")])?.frameId,
    ).toBe("main");
  });

  it("falls back to wire order when nothing connects the two", () => {
    // A turn whose findings measure something no frame drew, and a turn
    // with no findings at all: neither invents a connection.
    expect(selectPrimaryChart(CHARTS, [finding("F1", "cash_posted")])?.frameId).toBe(
      "ar_profile",
    );
    expect(selectPrimaryChart(CHARTS)?.frameId).toBe("ar_profile");
    expect(selectPrimaryChart([])).toBeUndefined();
  });
});
