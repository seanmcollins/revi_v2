/**
 * What the figure says about itself.
 *
 * The marks are Recharts' and are not asserted here (jsdom gives a
 * `ResponsiveContainer` no size, so there is nothing honest to measure).
 * What IS asserted is everything the figure claims in words — the legend
 * that keeps identity off colour alone, the rollup sentence, and the line
 * naming what put the bars in the order they are in. Those are the parts
 * that were contradicting the answer above them.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it } from "vitest";

import {
  axisTickLabels,
  boundedLegend,
  ChartTooltipContent,
  InvestigationChart,
  orderNote,
  rotatedAxisGutter,
} from "@/components/charts/InvestigationChart";
import type { ChartSpec } from "@/lib/types";

// jsdom has no matchMedia; the figure asks it whether motion is reduced.
// Answering "yes" also skips the draw-in, which nothing here measures.
beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
});

afterEach(() => cleanup());

function spec(overrides: Partial<ChartSpec> = {}): ChartSpec {
  return {
    id: "chart_main",
    kind: "bar",
    title: "Denied dollars by CARC",
    unit: "cents",
    xLabel: "carc",
    series: [{ key: "denied_dollars", label: "denied dollars", role: "current" }],
    rows: [
      { label: "CARC 16", values: { denied_dollars: 100 } },
      { label: "CARC 197", values: { denied_dollars: 200 } },
    ],
    ...overrides,
  };
}

function manySeries(count: number): ChartSpec {
  const series = Array.from({ length: count }, (_, i) => ({
    key: `s${i}`,
    label: `CARC ${i}`,
    role: (i === 0 ? "current" : "baseline") as "current" | "baseline",
  }));
  const values: Record<string, number> = {};
  series.forEach((s, i) => {
    values[s.key] = (count - i) * 100;
  });
  return spec({ series, rows: [{ label: "Jul", values }, { label: "Aug", values }] });
}

describe("InvestigationChart — what the figure states in words", () => {
  it("names every drawn series in the legend", () => {
    render(
      <InvestigationChart
        spec={spec({
          series: [
            { key: "current", label: "July", role: "current" },
            { key: "prior", label: "June", role: "baseline" },
          ],
        })}
        turnId="turn_1"
      />,
    );

    expect(screen.getByText("July")).toBeInTheDocument();
    expect(screen.getByText("June")).toBeInTheDocument();
  });

  it("states the rollup rather than quietly dropping thirteen series", () => {
    render(<InvestigationChart spec={manySeries(20)} turnId="turn_1" />);

    expect(screen.getByText("+13 others")).toBeInTheDocument();
    expect(screen.getByText(/showing the 7 largest of 20 series/)).toBeInTheDocument();
    expect(screen.getByText(/separate column in the CSV/)).toBeInTheDocument();
  });

  it("says nothing about a rollup when nothing was rolled up", () => {
    render(<InvestigationChart spec={spec()} turnId="turn_1" />);

    expect(screen.queryByText(/largest of/)).not.toBeInTheDocument();
  });

  it("names what put the bars in this order, and stays silent when nothing did", () => {
    expect(
      orderNote(spec({ order: { basis: "value", by: "denied_dollars", descending: true } })),
      // BUG 1 — the note is composed by this client, not by the engine,
      // so it names the measure the way the figure's own title does.
    ).toBe("ordered by denied dollars, high to low");
    expect(orderNote(spec({ order: { basis: "ordinal-bucket" } }))).toBe("ordered by bucket");
    // A DECLARED order and an INFERRED one read differently on purpose:
    // one is the pack's published fact, the other is this client reading
    // numbers out of label text.
    expect(orderNote(spec({ order: { basis: "axis-order", by: "ar_age_bucket" } }))).toBe(
      "in the catalog's declared order for AR age bucket",
    );
    expect(orderNote(spec({ order: { basis: "axis-order" } }))).toBe(
      "in the catalog's declared bucket order",
    );
    // The engine's own emission order is not dressed up as a ranking.
    expect(orderNote(spec({ order: { basis: "wire" } }))).toBeUndefined();
    expect(orderNote(spec())).toBeUndefined();
  });

  it("says UNRANKED when the answer refused to publish a ranking", () => {
    // A refused ranking and an unstated one are different facts. Silence
    // here let a reader take the leftmost bar for the worst offender.
    expect(orderNote(spec({ order: { basis: "wire", refused: true } }))).toBe(
      "unranked — no ranking was published for this answer",
    );
  });

  it("says which cells were held out of the order it claims", () => {
    expect(
      orderNote(
        spec({
          order: { basis: "value", by: "denial_rate", descending: true, boundedExcluded: 4 },
        }),
      ),
    ).toBe("ordered by denial rate, high to low; 4 bounded cells held out of it, at the end");
  });
});

/**
 * A COMPOSITE series label. When a frame carries a grouping column the
 * spec did not declare, the server folds it into `series` as a `" / "`-
 * joined key rather than publishing rows its own axes cannot tell apart
 * (`chart_integrity.enforce_row_keys`). Live, `chart_main` on
 * `inv_1a0c91304cfa` declares `series: "service_line / group_code"` over
 * keys like `"Orthopedic Surgery / CO"`, and `chart_breach_confirmation`
 * declares `"carc / plan"` over 429 of them.
 *
 * Nothing in the figure parses that label — it is a name, and the only
 * thing that matters is that it survives to the legend and the tooltip
 * whole, and that it gets a palette slot like any other identity.
 */
describe("InvestigationChart — composite series labels", () => {
  const COMPOSITE = spec({
    xLabel: "payer",
    stacked: true,
    series: [
      { key: "Orthopedic Surgery / CO", label: "Orthopedic Surgery / CO", role: "current" },
      { key: "Cardiology / CO", label: "Cardiology / CO", role: "baseline" },
      { key: "Imaging / OA", label: "Imaging / OA", role: "baseline" },
    ],
    rows: [
      {
        label: "Federal Medicare",
        values: {
          "Orthopedic Surgery / CO": 300,
          "Cardiology / CO": 200,
          "Imaging / OA": 100,
        },
      },
    ],
  });

  it("names each composite in the legend, whole and unsplit", () => {
    render(<InvestigationChart spec={COMPOSITE} turnId="turn_1" />);

    // One text node per label — a name split across spans is not readable,
    // copyable or searchable, and this is the label an analyst has to match
    // against a CSV column.
    expect(screen.getByText("Orthopedic Surgery / CO")).toBeInTheDocument();
    expect(screen.getByText("Cardiology / CO")).toBeInTheDocument();
    expect(screen.getByText("Imaging / OA")).toBeInTheDocument();
  });

  it("rolls composites up by the same rule as any other identity", () => {
    const many = Array.from({ length: 12 }, (_, i) => ({
      key: `Service ${i} / CO`,
      label: `Service ${i} / CO`,
      role: (i === 0 ? "current" : "baseline") as "current" | "baseline",
    }));
    const values: Record<string, number> = {};
    many.forEach((s, i) => {
      values[s.key] = (12 - i) * 100;
    });

    render(
      <InvestigationChart
        spec={spec({ series: many, rows: [{ label: "Federal Medicare", values }] })}
        turnId="turn_1"
      />,
    );

    expect(screen.getByText("+5 others")).toBeInTheDocument();
    expect(screen.getByText("Service 0 / CO")).toBeInTheDocument();
  });
});

describe("InvestigationChart — marks that are not measurements", () => {
  const bounded = spec({
    unit: "percent",
    series: [{ key: "denial_rate", label: "denial rate", role: "current" }],
    boundedRows: 1,
    rows: [
      { label: "Dr Chen", values: { denial_rate: 21 } },
      { label: "Dr Reyes", values: { denial_rate: 90 }, bounded: true, denominator: 11 },
    ],
  });

  it("says what ≤ MEANS, and leaves the census to the engine", () => {
    // One idea, in the reader's nouns, with no count in it. The old
    // sentence carried its own census — of the rows still DRAWN after
    // selection and capping — directly under the engine's census of the
    // marks it EMITTED, and live the two printed different numbers about
    // one control three lines apart.
    render(<InvestigationChart spec={bounded} turnId="turn_1" />);
    expect(
      screen.getByText(
        "≤ means at most: too few things sit behind that mark to measure it exactly, so the real figure is at or below it. A limit is not ranked against a measured mark.",
      ),
    ).toBeInTheDocument();
  });

  it("does not print a second ceiling count beside the engine's", () => {
    // The regression this rewrite closes: a figure whose drawn rows and
    // whose emitted marks are different populations must not state both
    // as though they were one.
    render(
      <InvestigationChart
        spec={{ ...bounded, note: "upper bounds: 4 of 12 marks are ceilings, not measurements" }}
        turnId="turn_1"
      />,
    );
    expect(screen.getByText(/upper bounds: 4 of 12 marks/)).toBeInTheDocument();
    expect(screen.queryByText(/1 of 2 marks is a ceiling/)).not.toBeInTheDocument();
  });

  it("prints the refusal above the picture, not 400px below it", () => {
    render(
      <InvestigationChart
        spec={{ ...bounded, order: { basis: "wire", refused: true } }}
        turnId="turn_1"
      />,
    );
    expect(screen.getByText("No ranking is published on this answer.")).toBeInTheDocument();
  });

  it("prints the engine's own census sentence when the wire published one", () => {
    render(
      <InvestigationChart
        spec={{ ...bounded, note: "upper bounds: 1 of 2 marks are ceilings" }}
        turnId="turn_1"
      />,
    );
    expect(screen.getByText("upper bounds: 1 of 2 marks are ceilings")).toBeInTheDocument();
  });

  it("marks a provisional bucket rather than drawing it as settled", () => {
    render(
      <InvestigationChart
        spec={spec({
          kind: "line",
          rows: [
            { label: "2026-07-06", values: { denied_dollars: 100 } },
            { label: "2026-07-13", values: { denied_dollars: 200 }, provisional: true },
          ],
        })}
        turnId="turn_1"
      />,
    );
    expect(
      screen.getByText(
        "* marks a provisional bucket: still settling, so its value will move.",
      ),
    ).toBeInTheDocument();
  });

  it("refuses to draw rows its declared axes cannot tell apart", () => {
    render(
      <InvestigationChart
        spec={spec({
          unit: "percent",
          keying: {
            xColumn: "month",
            seriesColumn: "payer",
            wireRows: 30,
            keys: 3,
            mode: "unkeyable",
            wireTotal: 0,
            drawnTotal: 0,
            note: "The server sent 30 rows over 3 distinct (month, payer) keys.",
            rows: [],
          },
        })}
        turnId="turn_1"
      />,
    );
    expect(screen.getByText("This chart is not drawn")).toBeInTheDocument();
    expect(
      screen.getByText("The server sent 30 rows over 3 distinct (month, payer) keys."),
    ).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* D-03 — no two entities under one label                              */
/* ------------------------------------------------------------------ */

/**
 * The LIVE twelve-payer set, read off `GET /v1/investigations/
 * inv_0b707e3c49bb` (chart_main, x=payer). Under the rule that shipped —
 * `v.length > 11 ? v.slice(0, 10) + "…"` — slot 1 (State Medicaid MCO,
 * 29.5%, the answer's #1 finding) and slot 5 (State Medicaid, 15.8%) both
 * printed "State Medi…" on the surface this product's own pitch tells
 * analysts to screenshot into a deck.
 */
const LIVE_PAYERS = [
  "Atlas Commercial",
  "Bluestone Mutual",
  "Federal Medicare",
  "Lakewood Medicaid MCO",
  "Meridian Health",
  "Northbridge Commercial",
  "Pinnacle Health Plan",
  "Silverline Medicare Advantage",
  "State Medicaid",
  "State Medicaid MCO",
  "Summit Peak Medicare Advantage",
  "Veritas Comp Fund",
];

describe("axis labels name one entity each", () => {
  it("never prints two different payers under one label — the live set", () => {
    const ticks = axisTickLabels(LIVE_PAYERS, 18);
    const drawn = LIVE_PAYERS.map((p) => ticks.get(p));
    expect(new Set(drawn).size).toBe(LIVE_PAYERS.length);
    // The pair that collided, named.
    expect(ticks.get("State Medicaid")).not.toBe(ticks.get("State Medicaid MCO"));
    // The old rule, for the record.
    expect("State Medicaid".slice(0, 10)).toBe("State Medicaid MCO".slice(0, 10));
  });

  it("keeps the identifying END, which a prefix cut throws away", () => {
    // Every provider in the warehouse is "Dr. Firstname Lastname (NNN)",
    // and the number is the only thing telling fifteen of them apart.
    const providers = [
      "Dr. Arden Riverstone (033)",
      "Dr. Arden Riverstone (071)",
      "Dr. Avery Riverstone (018)",
    ];
    const ticks = axisTickLabels(providers, 12);
    expect(new Set(providers.map((p) => ticks.get(p))).size).toBe(3);
    for (const provider of providers) {
      expect(ticks.get(provider)).toContain(provider.slice(-5));
    }
  });

  it("leaves a label that fits exactly as the wire spelled it", () => {
    const ticks = axisTickLabels(["Meridian Health", "Atlas Commercial"], 20);
    expect(ticks.get("Meridian Health")).toBe("Meridian Health");
  });

  it("spells a machine enum the way the rest of the product does", () => {
    // The axis printed "MEDICAL_NE…" under a narrative writing it out.
    const ticks = axisTickLabels(["MEDICAL_NECESSITY", "TIMELY_FILING", "OTHER"], 24);
    expect(ticks.get("MEDICAL_NECESSITY")).toBe("Medical necessity");
    expect(ticks.get("TIMELY_FILING")).toBe("Timely filing");
    // A bare all-caps token is as likely an acronym the pack means
    // literally; "Other" is not a better label than "OTHER" and this
    // renderer does not get to decide what a governed code means.
    expect(ticks.get("OTHER")).toBe("OTHER");
  });

  it("prints two names whole rather than printing them wrong", () => {
    const twins = ["A".repeat(60), `${"A".repeat(60)}!`];
    const ticks = axisTickLabels(twins, 12, 20);
    expect(ticks.get(twins[0])).not.toBe(ticks.get(twins[1]));
  });

  /**
   * BUG 2 — the middle cut is for the case it was built for, and nothing
   * else. The thirty-plan filing chart came out as "Bluestone…ral PPO",
   * "Meridian …e PPO": labels legible on neither end, on an axis whose
   * names mostly differ in their first fifteen characters.
   */
  it("cuts from the end on names a plain cut can tell apart", () => {
    const plans = [
      "Atlas HMO Complete",
      "Atlas National PPO",
      "Atlas POS Flex",
      "Atlas PPO Select",
      "Bluestone Federal PPO",
      "Bluestone HMO Blue",
      "Meridian Exchange PPO",
      "Meridian HMO Care",
    ];
    const ticks = axisTickLabels(plans, 18);
    expect(new Set(plans.map((p) => ticks.get(p))).size).toBe(plans.length);
    for (const plan of plans) {
      const drawn = ticks.get(plan) ?? "";
      expect(drawn, `${plan} must not be cut out of the middle`).not.toMatch(/\S…\S/);
      // Whatever is drawn, it starts where the name starts.
      expect(plan.startsWith(drawn.replace(/…$/, "").trimEnd())).toBe(true);
    }
  });

  it("still cuts the middle when only the tail tells two names apart", () => {
    // "Federal Medicare Part A" and "…Part B" differ nowhere else, and a
    // plain cut cannot separate them inside the budget.
    const plans = ["Federal Medicare Part A", "Federal Medicare Part B", "Atlas POS Flex"];
    const ticks = axisTickLabels(plans, 14, 18);
    expect(new Set(plans.map((p) => ticks.get(p))).size).toBe(3);
    expect(ticks.get("Federal Medicare Part A")).toContain("Part A");
    expect(ticks.get("Federal Medicare Part B")).toContain("Part B");
  });

  /**
   * …and then the figure has to give the label somewhere to be drawn.
   *
   * Live on the twelve-payer ranking the leftmost tick read "ate Medicaid
   * MCO": the shortener had correctly kept "State Medicaid MCO" whole, and
   * the rotated text ran off the left of the card because the margin was
   * a constant 10px. A label cut by the container is the same wrong-
   * identity defect as a label cut by the shortener.
   */
  it("gives a rotated first tick the room it actually needs", () => {
    const ticks = axisTickLabels(LIVE_PAYERS, 18);
    const first = ticks.get("State Medicaid MCO")!;
    const gutter = rotatedAxisGutter(first);
    // The horizontal run of the rotated text, less the y-axis width it may
    // legitimately sweep under, is what has to fit.
    const needed = Math.ceil(first.length * 6.3 * 0.82) - 48;
    expect(gutter).toBeGreaterThanOrEqual(needed);
    // The old constant did not.
    expect(gutter).toBeGreaterThan(10);
  });

  it("does not buy a gutter a short axis has no use for", () => {
    expect(rotatedAxisGutter("Jul")).toBe(10);
    expect(rotatedAxisGutter(undefined)).toBe(10);
  });

  it("stops buying room before the plot is squeezed out of the card", () => {
    // Two names that can only be told apart whole (`axisTickLabels` prints
    // them in full) must not take half the figure with them.
    expect(rotatedAxisGutter("A".repeat(200))).toBe(72);
  });
});

/* ------------------------------------------------------------------ */
/* D-01 — a line through ceilings is not a measured trend              */
/* ------------------------------------------------------------------ */

describe("InvestigationChart — a trend drawn through ceilings", () => {
  /** The live Veritas series: 6 of 8 months are suppression ceilings. */
  const TREND = spec({
    kind: "line",
    unit: "percent",
    xLabel: "month",
    boundedRows: 6,
    series: [{ key: "denial_rate", label: "denial rate", role: "current" }],
    rows: [
      { label: "2026-01-01", values: { denial_rate: 7.5 }, bounded: true, denominator: 133 },
      { label: "2026-05-01", values: { denial_rate: 10.4 } },
      { label: "2026-06-01", values: { denial_rate: 9.0 }, bounded: true, denominator: 111 },
      {
        label: "2026-07-01",
        values: { denial_rate: 76.9 },
        bounded: true,
        denominator: 13,
        provisional: true,
      },
      { label: "2026-08-01", values: {}, withheld: true },
    ],
  });

  it("says in words that the segments touching a ceiling are not measured", () => {
    render(<InvestigationChart spec={TREND} turnId="turn_1" />);
    expect(
      screen.getByText(
        "Segments touching a ceiling are drawn dashed with a hollow point — the line between two ceilings is not a measured movement.",
      ),
    ).toBeInTheDocument();
    // And what ≤ MEANS, on the line too — with no count in it.
    //
    // This assertion used to read "6 of 5 marks", which is what the old
    // caption actually printed on this fixture: `boundedRows` is the
    // engine's count of the marks it EMITTED (6) and `rows.length` is what
    // survived selection to be DRAWN (5). An impossible fraction, on the
    // one line whose job is to say a number cannot be trusted. The caption
    // states the meaning and leaves every census to the engine.
    expect(
      screen.getByText(
        /≤ means at most: too few things sit behind that mark to measure it exactly/,
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/6 of 5 marks/)).not.toBeInTheDocument();
  });

  it("names a withheld cell as a refusal rather than leaving it a gap", () => {
    render(<InvestigationChart spec={TREND} turnId="turn_1" />);
    expect(
      screen.getByText(
        "† marks a cell the engine withheld outright — no value was published for it, so its gap on this figure is a refusal, not a zero.",
      ),
    ).toBeInTheDocument();
  });

  it("says nothing about dashed segments on a line with no ceilings on it", () => {
    render(
      <InvestigationChart
        spec={spec({
          kind: "line",
          rows: [
            { label: "2026-07-06", values: { denied_dollars: 100 } },
            { label: "2026-07-13", values: { denied_dollars: 200 } },
          ],
        })}
        turnId="turn_1"
      />,
    );
    expect(screen.queryByText(/Segments touching a ceiling/)).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* Two windows, side by side (round-5 D-02)                            */
/* ------------------------------------------------------------------ */

/**
 * The live shape, off `GET /v1/investigations/inv_0899f9defc32`: one
 * chart, `series: "period"`, both windows in it, prior-side ceilings and
 * two plans that exist in June and not in July.
 *
 * What the figure has to say about that in words, since the marks belong
 * to Recharts and jsdom gives its container no size: which two windows
 * these are, which side a ceiling is on, and that a missing bar is an
 * absence rather than a collapse to zero.
 */
const COMPARISON: ChartSpec = {
  id: "chart_main__compare",
  kind: "grouped_bar",
  title: "Denial rate by plan",
  unit: "percent",
  xLabel: "plan",
  comparison: { currentKey: "current", priorKey: "prior" },
  series: [
    { key: "current", label: "This window", role: "current", pinned: true },
    { key: "prior", label: "Prior window", role: "baseline", pinned: true },
  ],
  rows: [
    {
      label: "Meridian PPO Prime",
      values: { current: 76.9231, prior: 41.6667 },
      bounded: true,
      cells: {
        current: { bounded: true, denominator: 13 },
        prior: { bounded: true, denominator: 24 },
      },
    },
    {
      label: "Meridian HMO Care",
      values: { prior: 35.7143 },
      bounded: true,
      cells: { prior: { bounded: true, denominator: 28 }, current: { absent: true } },
    },
  ],
};

describe("a comparison chart says which two windows it is drawing", () => {
  it("names the windows from the header when the turn published them", () => {
    render(
      <InvestigationChart
        spec={COMPARISON}
        turnId="turn_1"
        comparisonWindows={{ current: "Jul 2026", prior: "Jun 2026" }}
      />,
    );
    expect(screen.getByText("This window (Jul 2026)")).toBeInTheDocument();
    expect(screen.getByText("Prior window (Jun 2026)")).toBeInTheDocument();
  });

  it("still says which is which when the header carries no dates", () => {
    render(<InvestigationChart spec={COMPARISON} turnId="turn_1" />);
    expect(screen.getByText("This window")).toBeInTheDocument();
    expect(screen.getByText("Prior window")).toBeInTheDocument();
  });

  it("counts ceilings per side, agreeing with the engine on the half it counted", () => {
    // The engine's own census counts the current window against the
    // categories on the axis and is silent about the prior side. One
    // combined figure here would print a different number a paragraph
    // below it and read as two surfaces disagreeing about one control.
    expect(boundedLegend(COMPARISON)).toBe(
      "≤ means at most: too few things sit behind that mark to measure it exactly, so the " +
        "real figure is at or below it. 1 of 2 marks in this window and 2 of 2 in the window " +
        "it is compared against are limits, and a limit is not ranked against a measured mark.",
    );
  });

  it("says a missing bar is an absence, not a measured zero", () => {
    render(<InvestigationChart spec={COMPARISON} turnId="turn_1" />);
    expect(
      screen.getByText(
        "‡ marks a category with a figure in the window this one is compared against and none in " +
          "this one — its mark here is an absence, not a measured zero, so no bar is drawn.",
      ),
    ).toBeInTheDocument();
  });

  it("prints every sentence the engine wrote about the figure", () => {
    render(
      <InvestigationChart
        spec={{
          ...COMPARISON,
          note: "comparison: two series per category — current is this window and prior is the window it is compared against. They are not summed.",
          notes: [
            "comparison: two series per category — current is this window and prior is the window it is compared against. They are not summed.",
            "prior-only categories: 2 of 4 categories carry a figure on the comparison window and none on this one — their current mark is an absence, not a measured zero.",
            "upper bounds: 1 of 4 marks are ceilings, not measurements — their numerator was suppressed and they cannot be ranked against the measured marks",
          ],
        }}
        turnId="turn_1"
      />,
    );
    expect(screen.getByText(/They are not summed/)).toBeInTheDocument();
    expect(screen.getByText(/an absence, not a measured zero\.$/)).toBeInTheDocument();
    expect(screen.getByText(/upper bounds: 1 of 4 marks/)).toBeInTheDocument();
  });
});

describe("the hover on a comparison reads as a movement", () => {
  /**
   * The datum the chart hands Recharts: the row's values flattened onto
   * it, minus any mark the figure declined to draw (an absence is not a
   * bar, so it is not on the datum either).
   */
  const datum = (row: ChartSpec["rows"][number]) => {
    const values = Object.fromEntries(
      Object.entries(row.values).filter(([key]) => row.cells?.[key]?.absent !== true),
    );
    return { label: row.label, ...values, ...(row.cells ? { cells: row.cells } : {}) };
  };

  const hover = (row: ChartSpec["rows"][number]) => (
    <ChartTooltipContent
      active
      label={row.label}
      payload={[{ dataKey: "current", payload: datum(row) }]}
      formatValue={(v) => `${v.toFixed(1)}%`}
      unit="percent"
      comparison={COMPARISON.comparison}
      seriesLabel={(key) => (key === "current" ? "This window (Jul 2026)" : "Prior window (Jun 2026)")}
      seriesColor={() => "var(--chart-current)"}
    />
  );

  it("states both windows and the change between them", () => {
    render(hover(COMPARISON.rows[0]!));
    expect(screen.getByText("This window (Jul 2026)")).toBeInTheDocument();
    expect(screen.getByText("Prior window (Jun 2026)")).toBeInTheDocument();
    // A rate's movement is in percentage POINTS; the relative change keeps
    // the "%". Two different quantities never wear one symbol.
    expect(screen.getByText("Change")).toBeInTheDocument();
    expect(screen.getByText(/\+35\.3pp/)).toBeInTheDocument();
    expect(screen.getByText(/\+84\.6%/)).toBeInTheDocument();
  });

  it("marks the ceiling on each side that has one, with its own population", () => {
    render(hover(COMPARISON.rows[0]!));
    expect(screen.getByText("≤ 76.9%")).toBeInTheDocument();
    expect(screen.getByText(/This window \(Jul 2026\) is an upper bound over n = 13/)).toBeInTheDocument();
    expect(screen.getByText(/Prior window \(Jun 2026\) is an upper bound over n = 24/)).toBeInTheDocument();
  });

  it("says 'no figure' where the wire's zero is the join, and computes no delta from it", () => {
    render(hover(COMPARISON.rows[1]!));
    expect(screen.getByText("no figure")).toBeInTheDocument();
    expect(screen.queryByText("Change")).not.toBeInTheDocument();
    expect(
      screen.getByText(/The zero on the wire is the join between the two windows, not a reading/),
    ).toBeInTheDocument();
  });
});
