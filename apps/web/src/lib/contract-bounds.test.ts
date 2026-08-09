/**
 * The client half of the honesty seam: bounds, refusals, provisional
 * points, and rows the declared axes cannot tell apart.
 *
 * Round 4's verdict on this lane was that "the web lane consumed roughly
 * zero of ten engine-side wave-B claims" — the wire had been carrying
 * `is_bound`, `bound_population` and `provisional` for a release, and the
 * client dropped every one of them at the first mapper. What is pinned
 * here is the consumption, not the publication:
 *
 *   R4-01  a finding's `<metric>__is_bound` reaches the finding model, and
 *          the hero stat renders "≤ 76.9%" rather than "76.9%".
 *   R4-02  a chart does not rank what the answer refused to rank, and
 *          bounded cells are held out of any order it does claim.
 *   R4-03  a provisional point does not terminate a solid line.
 *   R4-09  rows that collide under `(x, series)` are added or refused,
 *          never silently overwritten.
 *   R4-14  a bucketed axis keeps its own scale, and a sort naming no drawn
 *          column orders nothing.
 *
 * `bounded_investigation` is CAPTURED, not composed: it is the body of
 * `GET /v1/investigations/inv_d2d5e9d7e858` at wm_003, the live
 * twelve-payer answer four of whose cells are suppression ceilings.
 */

import { describe, expect, it } from "vitest";

import RAW_SAMPLES from "@/lib/__fixtures__/wire-samples.json";
import { mapChartSpec, parseInvestigationResponse } from "@/lib/contract";
import { chartToCsv } from "@/lib/export";

/* eslint-disable @typescript-eslint/no-explicit-any */
const SAMPLES = RAW_SAMPLES as any;

/* ------------------------------------------------------------------ */
/* R4-01 — the bound reaches the finding, and the stat says so         */
/* ------------------------------------------------------------------ */

describe("a bounded finding, off the live wire", () => {
  function answer() {
    const parse = parseInvestigationResponse(SAMPLES.bounded_investigation);
    expect(parse.drift).toEqual([]);
    if (parse.value?.outcome !== "answer") throw new Error("not an answer");
    return parse.value;
  }

  it("carries the boolean the mapper used to throw away", () => {
    const f4 = answer().findings.find((f) => f.referent.value === "F4");
    // The wire publishes it as a value named after its measure. It is a
    // BOOLEAN, and the old filter kept only numbers and strings.
    expect(f4?.values.denial_rate__is_bound).toBe(true);
    expect(f4?.values.denial_rate__bound_population).toBe(13);
  });

  it("renders the hero stat as a ceiling, with the population it is over", () => {
    const f4 = answer().findings.find((f) => f.referent.value === "F4");
    expect(f4?.measured?.isBound).toBe(true);
    expect(f4?.measured?.boundPopulation).toBe(13);
    // 0.7692307692307693 → 76.9%, and the "≤" is the difference between a
    // measurement and an edge.
    expect(f4?.impactDisplay).toBe("≤ 76.9%");
    // The engine's own title and confidence agree with it, which is how
    // this was caught: one card saying both things at once.
    expect(f4?.title).toContain("≤ 76.9%");
    expect(f4?.confidence).toBe("qualified");
  });

  it("leaves a measured finding measured", () => {
    const f1 = answer().findings.find((f) => f.referent.value === "F1");
    expect(f1?.measured?.isBound).toBeUndefined();
    expect(f1?.impactDisplay).toBe("29.5%");
  });

  it("maps every bounded chart cell the wire flagged, and no others", () => {
    const chart = answer().charts[0];
    const bounded = chart.rows.filter((row) => row.bounded === true);
    expect(chart.rows).toHaveLength(12);
    expect(bounded.map((row) => row.label).sort()).toEqual([
      "Federal Medicare",
      "Lakewood Medicaid MCO",
      "Summit Peak Medicare Advantage",
      "Veritas Comp Fund",
    ]);
    // The population rides with the ceiling — a bound without its
    // denominator is unreadable.
    const federal = chart.rows.find((row) => row.label === "Federal Medicare");
    expect(federal?.denominator).toBe(214);
    expect(chart.boundedRows).toBe(4);
  });

  it("prints the engine's census instead of feeding it to a reference line", () => {
    const chart = answer().charts[0];
    // `annotations[0]` is a sentence about the figure. It was being handed
    // to `<ReferenceLine x=…>`, where it matched no category and drew
    // nothing at all.
    expect(chart.note).toContain("4 of 12 marks are ceilings");
    expect(chart.highlightLabel).toBeUndefined();
  });
});

/* ------------------------------------------------------------------ */
/* R4-02 — the chart does not rank what the answer refused to rank     */
/* ------------------------------------------------------------------ */

const RANKING = {
  id: "chart_main",
  chart_type: "bar",
  title: "denial rate — main",
  frame_id: "main",
  x: "provider",
  series: null,
  value: "denial_rate",
  unit: "ratio",
  sort: { by: "denial_rate", descending: true },
  rows: [
    { x: "Dr Abel", series: null, value: 0.08 },
    { x: "Dr Chen", series: null, value: 0.21 },
    { x: "Dr Reyes", series: null, value: 0.9, is_bound: true, bound_population: 11 },
    { x: "Dr Sato", series: null, value: 0.14 },
  ],
};

describe("a refused ranking is not re-created by the figure", () => {
  it("keeps wire order and says the ranking was refused", () => {
    const spec = mapChartSpec(RANKING, undefined, { warningCodes: ["RANKING_REFUSED"] });
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "Dr Abel",
      "Dr Chen",
      "Dr Reyes",
      "Dr Sato",
    ]);
    expect(spec?.order).toEqual({ basis: "wire", refused: true });
  });

  it("still ranks when the turn published no refusal", () => {
    const spec = mapChartSpec(RANKING, undefined, { warningCodes: ["POPULATION_CAVEAT"] });
    expect(spec?.order?.basis).toBe("value");
    expect(spec?.order?.refused).toBeUndefined();
  });

  it("holds bounded cells OUT of the order it does claim", () => {
    const spec = mapChartSpec(RANKING);
    // 0.9 is the largest number on the chart and it is a ceiling; ordering
    // it against measurements sorts by population size, which is the
    // engine's own sentence for why it refuses.
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "Dr Chen",
      "Dr Sato",
      "Dr Abel",
      "Dr Reyes",
    ]);
    expect(spec?.order).toEqual({
      basis: "value",
      by: "denial_rate",
      descending: true,
      boundedExcluded: 1,
    });
  });
});

/* ------------------------------------------------------------------ */
/* R4-03 — a provisional point is not a settled terminus               */
/* ------------------------------------------------------------------ */

describe("the provisional flag, read defensively", () => {
  it("carries `provisional` off the wire onto the row", () => {
    const spec = mapChartSpec({
      id: "chart_main",
      chart_type: "line",
      title: "denial rate — weekly",
      frame_id: "main",
      x: "week",
      series: null,
      value: "denial_rate",
      unit: "ratio",
      rows: [
        { x: "2026-07-06", series: null, value: 0.11, provisional: false },
        { x: "2026-07-13", series: null, value: 0.12, provisional: false },
        { x: "2026-07-20", series: null, value: 0.667, provisional: true },
      ],
    });
    expect(spec?.kind).toBe("line");
    expect(spec?.rows.at(-1)?.provisional).toBe(true);
    expect(spec?.rows[0]?.provisional).toBeUndefined();
  });

  it("claims nothing when the wire publishes the flag as false", () => {
    // The engine half of this is one missing call site; until it lands the
    // wire says `provisional: false` everywhere and nothing may be drawn
    // as unsettled on the strength of a guess.
    const parse = parseInvestigationResponse(SAMPLES.bounded_investigation);
    if (parse.value?.outcome !== "answer") throw new Error("not an answer");
    expect(parse.value.charts[0].rows.every((r) => r.provisional === undefined)).toBe(true);
  });
});

/* ------------------------------------------------------------------ */
/* R4-09 — rows the declared axes cannot tell apart                    */
/* ------------------------------------------------------------------ */

const COLLIDING = {
  id: "chart_denial_concentration",
  chart_type: "bar",
  title: "denied dollars — concentration",
  frame_id: "denial_concentration",
  x: "month",
  series: "payer",
  value: "denied_dollars",
  unit: "money_cents",
  rows: [
    { x: "2026-05", series: "Atlas Commercial", value: 100, referent_id: "D1" },
    { x: "2026-05", series: "Atlas Commercial", value: 250, referent_id: "D2" },
    { x: "2026-05", series: "Atlas Commercial", value: 400, referent_id: "D3" },
    { x: "2026-06", series: "Atlas Commercial", value: 900, referent_id: "D4" },
  ],
};

describe("rows that collide under the declared axes", () => {
  it("ADDS them when the measure is additive, and nothing is lost", () => {
    const spec = mapChartSpec(COLLIDING);
    // Last-write-wins drew 400 here and dropped 350 of 750.
    expect(spec?.rows[0]?.values["Atlas Commercial"]).toBe(750);
    expect(spec?.keying?.mode).toBe("summed");
    expect(spec?.keying?.wireRows).toBe(4);
    expect(spec?.keying?.keys).toBe(2);
    expect(spec?.keying?.wireTotal).toBe(1650);
    expect(spec?.keying?.drawnTotal).toBe(1650);
    expect(spec?.keying?.note).toContain("4 rows over 2 distinct");
  });

  it("drops the drill target of a collided cell rather than pointing it somewhere", () => {
    const spec = mapChartSpec(COLLIDING);
    // Three referents collapsed into one bar. Keeping the last one is how
    // a bar's drill-through came to open a cell it does not draw.
    expect(spec?.rows[0]?.referent).toBeUndefined();
    // The uncollided cell keeps its own.
    expect(spec?.rows[1]?.referent).toBe("D4");
  });

  it("REFUSES to draw when the measure cannot be added up", () => {
    const spec = mapChartSpec({ ...COLLIDING, unit: "ratio" });
    expect(spec?.keying?.mode).toBe("unkeyable");
    expect(spec?.keying?.note).toContain("cannot be added up");
    expect(spec?.keying?.rows).toHaveLength(4);
  });

  it("says nothing at all when every row keys uniquely", () => {
    const spec = mapChartSpec({
      ...COLLIDING,
      rows: [
        { x: "2026-05", series: "Atlas Commercial", value: 100 },
        { x: "2026-06", series: "Atlas Commercial", value: 900 },
      ],
    });
    expect(spec?.keying).toBeUndefined();
  });

  it("exports the rows AS THEY ARRIVED, not the cells the picture drew", () => {
    const spec = mapChartSpec(COLLIDING);
    const csv = chartToCsv(spec!, { exportedAt: new Date("2026-08-09T14:32:00") });
    const lines = csv.split("\r\n");
    const header = lines.findIndex((l) => !l.startsWith("#") && !l.startsWith('"#'));
    // Long format: one line per wire row, with the series column named.
    expect(lines[header]).toBe("month,payer,referent,value (usd)");
    expect(lines.slice(header + 1).filter((l) => l !== "")).toHaveLength(4);
    expect(csv).toContain("2026-05,Atlas Commercial,D1,1");
    expect(csv).toContain("2026-05,Atlas Commercial,D3,4");
    // And the preamble states the count the file actually carries.
    expect(csv).toContain("4 rows as the server sent them");
    expect(csv).toContain("4 rows over 2 distinct");
  });
});

/* ------------------------------------------------------------------ */
/* R4-14 — the axis's own scale, and a sort that names nothing drawn   */
/* ------------------------------------------------------------------ */

const RUNWAY = {
  id: "chart_filing_runway_profile",
  chart_type: "stacked_bar",
  title: "claims — filing runway profile",
  frame_id: "filing_runway_profile",
  x: "filing_runway_bucket",
  series: "plan",
  value: "claim_count",
  unit: "count",
  // The wire ranks by the MEASURE, and the measure is not a drawn column:
  // the drawn columns are plans.
  sort: { by: "claim_count", descending: true },
  rows: [
    { x: "expired", series: "Atlas HMO Complete", value: 90 },
    { x: "0-30", series: "Atlas HMO Complete", value: 10 },
    { x: "90+", series: "Atlas HMO Complete", value: 70 },
    { x: "31-60", series: "Atlas HMO Complete", value: 20 },
    { x: "61-90", series: "Atlas HMO Complete", value: 30 },
    { x: "expired", series: "Central Physicians Plaza", value: 5 },
    { x: "0-30", series: "Central Physicians Plaza", value: 1 },
    { x: "90+", series: "Central Physicians Plaza", value: 4 },
    { x: "31-60", series: "Central Physicians Plaza", value: 2 },
    { x: "61-90", series: "Central Physicians Plaza", value: 3 },
  ],
};

describe("a bucketed axis keeps its own scale", () => {
  it("orders up the scale even when the wire published a value sort", () => {
    const spec = mapChartSpec(RUNWAY);
    // Before: expired, 90+, 0-30, 61-90, 31-60 — one arbitrary plan's
    // counts, captioned "ordered by Atlas HMO Complete, high to low".
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "expired",
      "0-30",
      "31-60",
      "61-90",
      "90+",
    ]);
    expect(spec?.order).toEqual({ basis: "ordinal-bucket" });
  });

  it("orders nothing when the sort names no column this chart draws", () => {
    const spec = mapChartSpec({
      ...RUNWAY,
      x: "plan",
      rows: [
        { x: "Central Physicians Plaza", series: "Jul", value: 5 },
        { x: "Atlas HMO Complete", series: "Jul", value: 90 },
        { x: "Central Physicians Plaza", series: "Aug", value: 4 },
        { x: "Atlas HMO Complete", series: "Aug", value: 70 },
      ],
    });
    // `claim_count` is not one of {Jul, Aug}. Falling back to the first
    // series is how a by-plan ranking came to be captioned "ordered by
    // Central Physicians Plaza".
    expect(spec?.rows.map((r) => r.label)).toEqual([
      "Central Physicians Plaza",
      "Atlas HMO Complete",
    ]);
    expect(spec?.order).toEqual({ basis: "wire" });
  });

  it("still ranks a single-series chart on its own measure", () => {
    const spec = mapChartSpec(RANKING);
    expect(spec?.order?.basis).toBe("value");
    expect(spec?.order?.by).toBe("denial_rate");
  });
});
