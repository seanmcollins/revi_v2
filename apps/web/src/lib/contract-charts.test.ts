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
} from "@/lib/contract";
import type { ChartSpec } from "@/lib/types";

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
