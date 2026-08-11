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
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import {
  axisTickLabels,
  axisTickPlan,
  boundedLegend,
  ChartTooltipContent,
  EXPANDED_MAX_AXIS_HEIGHT,
  flatTickBudget,
  FLAT_TICK_FLOOR,
  InvestigationChart,
  MIN_AXIS_HEIGHT,
  orderNote,
  horizontalAxisWidth,
  horizontalGutterCap,
  horizontalTickBudget,
  MIN_HORIZONTAL_GUTTER,
  rotatedAxisGutter,
  rotatedAxisHeight,
  rotatedTickBase,
  ROTATED_TICK_BASE,
} from "@/components/charts/InvestigationChart";
import { chartToCsv } from "@/lib/export";
import { useSessionStore } from "@/lib/store";
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
    // `A/R`, not `AR` — the spelling the metric pack itself uses, and the
    // one the monitor beside this figure prints ("days in A/R by payer").
    expect(orderNote(spec({ order: { basis: "axis-order", by: "ar_age_bucket" } }))).toBe(
      "in the catalog's declared order for A/R age bucket",
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
      // "Bounded cell" is the engine's word; the reader's word — the one
      // the "≤" legend under this caption uses — is a ceiling.
    ).toBe("ordered by denial rate, high to low; 4 ceilings held out of it, at the end");
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
    // The engine writes its census clause-led ("upper bounds: …"), which
    // is right inside a paragraph and wrong under a figure, where every
    // sentence is a caption of its own — so the render moves the first
    // character and nothing else (`capitalizeOpening`). The census, the
    // counts and the CSV preamble are untouched.
    expect(screen.getByText(/Upper bounds: 4 of 12 marks/)).toBeInTheDocument();
    expect(screen.queryByText(/1 of 2 marks is a ceiling/i)).not.toBeInTheDocument();
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
    expect(screen.getByText("Upper bounds: 1 of 2 marks are ceilings")).toBeInTheDocument();
  });

  /**
   * MARKS ON THE DATA, NOTES BELOW IT, WARNINGS ONLY FOR VERDICTS.
   *
   * The engine's census, the rollup sentence and the keying note were all
   * printed in amber above the plot, which made every figure that had
   * anything to say about itself look like a figure with something wrong
   * with it. They are captions now, in muted ink, under the picture they
   * explain. The refusal is the exception and keeps its box: a reader who
   * takes the leftmost bar for the worst offender has been misled by the
   * drawing, not merely under-informed.
   */
  it("captions the engine's census quietly, under the picture", () => {
    const { container } = render(
      <InvestigationChart
        spec={{ ...bounded, note: "upper bounds: 1 of 2 marks are ceilings" }}
        turnId="turn_1"
      />,
    );
    const note = screen.getByText("Upper bounds: 1 of 2 marks are ceilings");
    const caption = note.closest("p");
    expect(caption?.className).toContain("text-muted-foreground");
    expect(caption?.className).not.toContain("text-warning");
    // Below the plot, not above it.
    const plot = container.querySelector(".recharts-responsive-container")?.parentElement;
    expect(plot).not.toBeNull();
    expect(plot!.compareDocumentPosition(caption!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("keeps the refusal in the warning register, and only the refusal", () => {
    render(
      <InvestigationChart
        spec={{
          ...bounded,
          note: "upper bounds: 1 of 2 marks are ceilings",
          keying: {
            xColumn: "payer",
            seriesColumn: null,
            wireRows: 4,
            keys: 2,
            mode: "summed",
            wireTotal: 111,
            drawnTotal: 111,
            note: "Rows were summed to the declared grain.",
            rows: [],
          },
          order: { basis: "wire", refused: true },
        }}
        turnId="turn_1"
      />,
    );
    const refusal = screen.getByText("No ranking is published on this answer.").closest("p");
    expect(refusal?.className).toContain("border-warning/40");
    // Everything else on the figure reads in the caption's own ink.
    expect(
      screen.getByText("Rows were summed to the declared grain.").closest("p")?.className,
    ).not.toContain("text-warning");
  });

  it("says how many rows are drawn without dressing it as a defect", () => {
    render(
      <InvestigationChart
        spec={{ ...bounded, truncation: { shown: 2, total: 12 } }}
        turnId="turn_1"
      />,
    );
    const expand = screen.getByRole("button", { name: /Showing top 2 of 12/ });
    expect(expand.className).not.toContain("text-warning");
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
  "Halvern Health",
  "Northbridge Commercial",
  "Ashvale Health Plan",
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
    const ticks = axisTickLabels(["Halvern Health", "Atlas Commercial"], 20);
    expect(ticks.get("Halvern Health")).toBe("Halvern Health");
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

  /**
   * A TIME AXIS IS SPELLED THE WAY THE REST OF THE PRODUCT SPELLS DATES.
   *
   * The category labels on a trend are the engine's own ISO dates, and the
   * axis printed them raw: twelve ticks reading "2026-01-01",
   * "2026-02-01", "2026-03-01" under a title that says "by month". Short
   * on the tick, where the year is the same on every one of them; medium
   * in the hover, where a reader goes to read one point exactly.
   */
  it("spells an ISO date on the tick the way a reader says it", () => {
    const days = ["2026-01-01", "2026-02-01", "2026-12-25"];
    const ticks = axisTickLabels(days, 12);
    expect(ticks.get("2026-01-01")).toBe("Jan 1");
    expect(ticks.get("2026-02-01")).toBe("Feb 1");
    expect(ticks.get("2026-12-25")).toBe("Dec 25");
  });

  it("shortens the date BEFORE budgeting the width, so nothing is cut", () => {
    // "2026-01-01" is ten characters and "Jan 1" is five. Budgeting the
    // first while drawing the second is how an axis buys room it does not
    // need — and how a tick that would have fitted gets an ellipsis.
    const ticks = axisTickLabels(["2026-01-01", "2026-02-01"], 6);
    expect([...ticks.values()].every((tick) => !tick.includes("\u2026"))).toBe(true);
  });

  it("leaves a token it cannot prove is a date exactly as the wire wrote it", () => {
    const ticks = axisTickLabels(["2026-13-45"], 20);
    expect(ticks.get("2026-13-45")).toBe("2026-13-45");
  });

  it("prints two names whole rather than printing them wrong", () => {
    const twins = ["A".repeat(60), `${"A".repeat(60)}!`];
    const ticks = axisTickLabels(twins, 12, 20);
    expect(ticks.get(twins[0])).not.toBe(ticks.get(twins[1]));
  });

  /**
   * BUG 2 — the middle cut is for the case it was built for, and nothing
   * else. The thirty-plan filing chart came out as "Bluestone…ral PPO",
   * "Halvern …e PPO": labels legible on neither end, on an axis whose
   * names mostly differ in their first fifteen characters.
   */
  it("cuts from the end on names a plain cut can tell apart", () => {
    const plans = [
      "Atlas HMO Complete",
      "Atlas National PPO",
      "Atlas POS Flex",
      "Atlas PPO Select",
      "Bluestone Federal PPO",
      "Bluestone Select HMO",
      "Halvern Exchange PPO",
      "Halvern HMO Care",
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

  /**
   * …AND THE ROOM BELOW, which was still the constant the left gutter used
   * to be.
   *
   * Swept across every chart spec in the captured fixtures at the four
   * container widths the product draws them in — the Evidence rail at
   * 1280 and 1440, the answer column at its 3xl measure and at 1512 —
   * BEFORE this existed: 421 x-axis tick labels rendered with their tails
   * cut off by the SVG's own bottom edge, worst 16px, on every payer
   * ranking at every width. AFTER: zero, at all four.
   */
  it("gives a rotated axis the depth its own longest name needs", () => {
    const ticks = axisTickLabels(LIVE_PAYERS, 18);
    const rendered = LIVE_PAYERS.map((p) => ticks.get(p)!);
    const longest = rendered.reduce((n, t) => Math.max(n, t.length), 0);
    // drop = width × sin(35°), plus one rotated line box, plus the tick
    // margin — the geometry the fixed 74px did not have.
    const needed = Math.ceil(longest * 6.3 * 0.574 + 14 * 0.82) + 6;
    expect(rotatedAxisHeight(rendered)).toBeGreaterThanOrEqual(needed);
    expect(rotatedAxisHeight(rendered)).toBeGreaterThan(MIN_AXIS_HEIGHT);
  });

  it("keeps the old constant as the floor for a short axis", () => {
    expect(rotatedAxisHeight(["Jul", "Aug", "Sep"])).toBe(MIN_AXIS_HEIGHT);
    expect(rotatedAxisHeight([])).toBe(MIN_AXIS_HEIGHT);
  });

  it("caps the axis before the figure becomes an axis with a chart on it", () => {
    expect(rotatedAxisHeight(["A".repeat(200)])).toBeLessThanOrEqual(116);
  });
});

/* ------------------------------------------------------------------ */
/* A flat tick has to fit its own band                                 */
/* ------------------------------------------------------------------ */

/**
 * THE OTHER HALF OF THE SAME MEASUREMENT.
 *
 * The rotate-or-not rule counts characters and categories and knows
 * nothing about width, so the same five-payer chart was comfortable in
 * the answer column and unreadable in the Evidence rail: measured at
 * 308px, five flat labels drew on top of one another with 111px of
 * overlap. Twenty of the corpus's charts did it at one width or another.
 *
 * The budget is what the BAND can hold, so the same chart shortens its
 * labels where there is room to read them and rotates where there is not.
 */
describe("InvestigationChart — a flat tick is budgeted against its band", () => {
  it("is unbounded until the container has actually been measured", () => {
    // The first paint, and any environment without ResizeObserver: an
    // unmeasured chart keeps exactly the behaviour it had before, rather
    // than shortening labels against a width nobody measured.
    expect(flatTickBudget(0, 5)).toBe(Number.POSITIVE_INFINITY);
    expect(flatTickBudget(720, 0)).toBe(Number.POSITIVE_INFINITY);
  });

  it("gives the answer column's five-payer chart room for a real name", () => {
    // 720px container, five categories: a band of ~134px holds a name.
    expect(flatTickBudget(720, 5)).toBeGreaterThanOrEqual(FLAT_TICK_FLOOR);
  });

  it("says the Evidence rail cannot hold five flat names at all", () => {
    // 308px, five categories: a band of ~52px. Below the floor, so the
    // axis rotates instead of drawing five names over each other.
    expect(flatTickBudget(308, 5)).toBeLessThan(FLAT_TICK_FLOOR);
  });

  it("shrinks as the categories multiply, in the same container", () => {
    expect(flatTickBudget(720, 12)).toBeLessThan(flatTickBudget(720, 5));
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
    { key: "prior", label: "The window compared against", role: "baseline", pinned: true },
  ],
  rows: [
    {
      label: "Halvern PPO Prime",
      values: { current: 76.9231, prior: 41.6667 },
      bounded: true,
      cells: {
        current: { bounded: true, denominator: 13 },
        prior: { bounded: true, denominator: 24 },
      },
    },
    {
      label: "Halvern HMO Care",
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
    expect(screen.getByText("The window compared against (Jun 2026)")).toBeInTheDocument();
  });

  it("still says which is which when the header carries no dates", () => {
    render(<InvestigationChart spec={COMPARISON} turnId="turn_1" />);
    expect(screen.getByText("This window")).toBeInTheDocument();
    expect(screen.getByText("The window compared against")).toBeInTheDocument();
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
    expect(screen.getByText(/Upper bounds: 1 of 4 marks/)).toBeInTheDocument();
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
      seriesLabel={(key) => (key === "current" ? "This window (Jul 2026)" : "The window compared against (Jun 2026)")}
      seriesColor={() => "var(--chart-current)"}
    />
  );

  it("states both windows and the change between them", () => {
    render(hover(COMPARISON.rows[0]!));
    expect(screen.getByText("This window (Jul 2026)")).toBeInTheDocument();
    expect(screen.getByText("The window compared against (Jun 2026)")).toBeInTheDocument();
    // A rate's movement is in percentage POINTS; the relative change keeps
    // the "%". Two different quantities never wear one symbol.
    expect(screen.getByText("Change")).toBeInTheDocument();
    expect(screen.getByText(/\+35\.3pp/)).toBeInTheDocument();
    expect(screen.getByText(/\+84\.6%/)).toBeInTheDocument();
  });

  it("marks the ceiling on each side that has one, with its own population", () => {
    render(hover(COMPARISON.rows[0]!));
    expect(screen.getByText("≤ 76.9%")).toBeInTheDocument();
    // "n = 13" is statistical shorthand; the population is stated the way
    // every other surface in the product states it.
    expect(
      screen.getByText(/This window \(Jul 2026\) is an upper bound over a population of 13/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/The window compared against \(Jun 2026\) is an upper bound over a population of 24/),
    ).toBeInTheDocument();
  });

  it("says 'No figure' where the wire's zero is the join, and computes no delta from it", () => {
    render(hover(COMPARISON.rows[1]!));
    // A MARK ON THE DATA, in sentence case and in quiet ink: the absence
    // stands where the number would be and does not need amber to read.
    const absence = screen.getByText("No figure");
    expect(absence).toBeInTheDocument();
    expect(absence.className).not.toContain("text-warning");
    expect(screen.queryByText("Change")).not.toBeInTheDocument();
    expect(
      screen.getByText(/The zero on the wire is the join between the two windows, not a reading/),
    ).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* A single mark is a figure, not a chart                              */
/* ------------------------------------------------------------------ */

/**
 * ONE NUMBER DRAWN AS A CHART READS AS A FAILED RENDER.
 *
 * The owner's report: a lone hairline bar in a ~700px plot, under a
 * y-axis and a grid and over a single tick, does not look like data — it
 * looks like a chart that did not load. The render policy inside
 * `InvestigationChart` decides from the DRAWN rows: one drawable mark
 * becomes a figure at display size (`KeyFigure`, the same vocabulary
 * Home's key-figure band uses), one category across two windows becomes a
 * stated movement, and everything else is still a chart.
 *
 * What these tests pin is the part that is dangerous to get wrong: every
 * honesty mark travels UP to display size, a refusal never becomes a
 * number, the drill still emits, and the CSV still carries the published
 * rows. Recharts' marks remain unasserted for the reason the header of
 * this file gives — jsdom hands `ResponsiveContainer` no size — so the
 * presence of a chart is asserted by the container it renders into.
 */
const chartDrawn = (container: HTMLElement): boolean =>
  container.querySelector(".recharts-responsive-container") !== null;

const figureDrawn = (container: HTMLElement): Element | null =>
  container.querySelector("[data-key-figure]");

/** The one numeral the card sets at `--text-figure` (30px). */
const displayFigure = (container: HTMLElement): string =>
  container.querySelector(".text-figure")?.textContent ?? "";

describe("InvestigationChart — one drawable mark is a figure, not a chart", () => {
  it("draws a single bar row as a figure card, not a hairline over a one-tick axis", () => {
    const { container } = render(
      <InvestigationChart
        spec={spec({ rows: [{ label: "CARC 16", values: { denied_dollars: 412300 } }] })}
        turnId="turn_1"
      />,
    );

    // The measure's own label, opened with a capital because it is in
    // sentence position — the wire's string ("denied dollars") is not
    // mutated, only its rendering.
    expect(screen.getByText("Denied dollars")).toBeInTheDocument();
    // The value at DISPLAY size, in the spec's unit.
    expect(displayFigure(container)).toContain("$4,123.00");
    // And no chart at all.
    expect(chartDrawn(container)).toBe(false);
  });

  it("names the category as a quiet context line rather than a one-tick axis", () => {
    render(
      <InvestigationChart
        spec={spec({ rows: [{ label: "CARC 16", values: { denied_dollars: 412300 } }] })}
        turnId="turn_1"
      />,
    );

    // The category, then the dimension it came from — humanised the way
    // the footer caption humanises it ("carc" → "CARC").
    expect(screen.getByText("CARC 16 · CARC")).toBeInTheDocument();
  });

  it("draws a single point on a line as a figure too", () => {
    const { container } = render(
      <InvestigationChart
        spec={spec({
          kind: "line",
          xLabel: "month",
          rows: [{ label: "2026-07-06", values: { denied_dollars: 100 } }],
        })}
        turnId="turn_1"
      />,
    );

    expect(chartDrawn(container)).toBe(false);
    expect(displayFigure(container)).toContain("$1.00");
    // The hover's spelling of a date, not the axis tick's: this is read
    // exactly rather than scanned, so it gets the whole date.
    expect(screen.getByText("Jul 6, 2026 · Month")).toBeInTheDocument();
  });

  it("still draws a chart the moment there are two categories to compare", () => {
    const { container } = render(<InvestigationChart spec={spec()} turnId="turn_1" />);

    expect(chartDrawn(container)).toBe(true);
    expect(figureDrawn(container)).toBeNull();
  });

  it("charts one category with two non-period series — the series are comparable", () => {
    // THE TIE-BREAK. One category and several series looks like the defect
    // and is also a real comparison; comparability wins, so it is charted.
    // Only a genuinely single drawable mark becomes a figure.
    const { container } = render(
      <InvestigationChart
        spec={spec({
          xLabel: "payer",
          series: [
            { key: "ortho", label: "Orthopedic Surgery", role: "current" },
            { key: "cardio", label: "Cardiology", role: "baseline" },
          ],
          rows: [{ label: "Federal Medicare", values: { ortho: 300, cardio: 200 } }],
        })}
        turnId="turn_1"
      />,
    );

    expect(chartDrawn(container)).toBe(true);
    expect(figureDrawn(container)).toBeNull();
  });
});

describe("InvestigationChart — the honesty marks survive display size", () => {
  const CEILING = spec({
    unit: "percent",
    xLabel: "provider",
    boundedRows: 1,
    series: [{ key: "denial_rate", label: "denial rate", role: "current" }],
    rows: [
      {
        label: "Dr Reyes",
        values: { denial_rate: 76.9 },
        bounded: true,
        denominator: 13,
        provisional: true,
      },
    ],
  });

  it("carries the ≤, the ceiling wording and the provisional wording to display size", () => {
    const { container } = render(<InvestigationChart spec={CEILING} turnId="turn_1" />);

    // The "≤" the engine published, on the front of the 30px numeral.
    expect(displayFigure(container)).toContain("≤ 76.9%");
    // Both marks, composed — a bucket can be a ceiling AND still settling,
    // and printing one of those at display size while dropping the other
    // is the failure this policy exists to avoid.
    expect(screen.getByText("a ceiling, not a measurement · still settling")).toBeInTheDocument();
    // The provisional caption, said without pointing at an axis mark that
    // is not drawn.
    expect(
      screen.getByText("This bucket is provisional: still settling, so its value will move."),
    ).toBeInTheDocument();
    // And `boundedLegend` still renders under the card, unchanged.
    expect(screen.getByText(boundedLegend(CEILING))).toBeInTheDocument();
  });

  it("renders a withheld single row as a refusal, with no number on it", () => {
    const { container } = render(
      <InvestigationChart
        spec={spec({
          unit: "percent",
          xLabel: "provider",
          series: [{ key: "denial_rate", label: "denial rate", role: "current" }],
          rows: [{ label: "Dr Reyes", values: {}, withheld: true }],
        })}
        turnId="turn_1"
      />,
    );

    expect(screen.getByText("No value was published for this figure")).toBeInTheDocument();
    expect(
      screen.getByText(
        "The engine withheld this cell outright — no value was published for it, so this gap is a refusal, not a zero.",
      ),
    ).toBeInTheDocument();
    // NOT a figure, and above all not a zero: a withheld cell rendered as
    // a 30px "0.0%" is the worst object this policy could produce.
    expect(figureDrawn(container)).toBeNull();
    expect(container.querySelector(".text-figure")).toBeNull();
    expect(container.textContent).not.toContain("0.0%");
  });
});

describe("InvestigationChart — one category across two windows is a movement", () => {
  const ONE_PLAN: ChartSpec = {
    id: "chart_main__compare",
    kind: "grouped_bar",
    title: "Denial rate by plan",
    unit: "percent",
    xLabel: "plan",
    comparison: { currentKey: "current", priorKey: "prior" },
    series: [
      { key: "current", label: "This window", role: "current", pinned: true },
      { key: "prior", label: "The window compared against", role: "baseline", pinned: true },
    ],
    rows: [{ label: "Halvern PPO Prime", values: { current: 21, prior: 15.5 } }],
  };

  it("states a one-category comparison as a delta card: current large, prior quiet", () => {
    const { container } = render(
      <InvestigationChart
        spec={ONE_PLAN}
        turnId="turn_1"
        comparisonWindows={{ current: "Jul 2026", prior: "Jun 2026" }}
      />,
    );

    expect(chartDrawn(container)).toBe(false);
    // The current window at display size, named by the window the turn
    // published rather than by the engine's `current`/`prior` bookkeeping.
    expect(
      container.querySelector('[data-key-figure="This window (Jul 2026)"]'),
    ).not.toBeNull();
    expect(displayFigure(container)).toContain("21.0%");
    // The window it is compared against, quiet — `KeyFigure`'s non-emphasis
    // step, not the display step.
    const prior = screen.getByText("15.5%");
    expect(prior.className).toContain("text-lead");
    expect(prior.className).not.toContain("text-figure");
    expect(screen.getByText("The window compared against (Jun 2026)")).toBeInTheDocument();
  });

  it("says the direction in the digest's own words, in the chart's own unit", () => {
    render(
      <InvestigationChart
        spec={ONE_PLAN}
        turnId="turn_1"
        comparisonWindows={{ current: "Jul 2026", prior: "Jun 2026" }}
      />,
    );

    // "up", the word `DeltaLine` uses, opened with a capital because the
    // node is in sentence position. A rate's MOVEMENT is percentage
    // points, never a percentage — no relative change is ever derived.
    expect(screen.getByText("Up 5.5pp")).toBeInTheDocument();
    // Never as a percentage: "up 5.5%" beside a 21.0% figure is the
    // relative-change confusion `DeltaLine`'s doc comment exists to
    // prevent, and no relative change is derived here at all.
    expect(screen.queryByText(/Up 5\.5%/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\+26\.2%/)).not.toBeInTheDocument();
  });

  it("claims no direction when the two windows measured the same figure", () => {
    const { container } = render(
      <InvestigationChart
        spec={{ ...ONE_PLAN, rows: [{ label: "Halvern PPO Prime", values: { current: 21, prior: 21 } }] }}
        turnId="turn_1"
      />,
    );

    // No arrow and no sign where there is no direction to claim.
    expect(screen.getByText("No change")).toBeInTheDocument();
    expect(container.querySelector('[data-delta-mark="neutral"]')).not.toBeNull();
  });

  it("says down when it went down", () => {
    render(
      <InvestigationChart
        spec={{
          ...ONE_PLAN,
          rows: [{ label: "Halvern PPO Prime", values: { current: 15.5, prior: 21 } }],
        }}
        turnId="turn_1"
      />,
    );
    expect(screen.getByText("Down 5.5pp")).toBeInTheDocument();
  });

  it("refuses to draw a movement when one side is a ceiling", () => {
    render(
      <InvestigationChart
        spec={{
          ...ONE_PLAN,
          rows: [
            {
              label: "Halvern PPO Prime",
              values: { current: 76.9231, prior: 41.6667 },
              bounded: true,
              cells: { current: { bounded: true, denominator: 13 } },
            },
          ],
        }}
        turnId="turn_1"
        comparisonWindows={{ current: "Jul 2026", prior: "Jun 2026" }}
      />,
    );

    // A ceiling minus a measurement is not a change, and it is said in
    // words rather than drawn as a movement.
    expect(
      screen.getByText(
        "No movement is published between these two windows: one of them is a limit rather than a measurement, and a ceiling minus a measurement is not a change.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText(/^Up /)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Down /)).not.toBeInTheDocument();
    // The ceiling still travels, on the side that carries it.
    expect(screen.getByText("≤ 76.9%")).toBeInTheDocument();
  });

  it("refuses to draw a movement when one side has no figure at all", () => {
    render(
      <InvestigationChart
        spec={{
          ...ONE_PLAN,
          rows: [
            {
              label: "Halvern HMO Care",
              values: { current: 0, prior: 35.7143 },
              cells: { current: { absent: true } },
            },
          ],
        }}
        turnId="turn_1"
      />,
    );

    // The compare operator's zero-fill is the join, not a reading, so it
    // is never drawn as a figure and never subtracted.
    expect(screen.getByText("No figure")).toBeInTheDocument();
    expect(screen.queryByText("0.0%")).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "No movement is published between these two windows: one of them has no figure at all, and a measurement minus an absence is not a change.",
      ),
    ).toBeInTheDocument();
  });
});

describe("InvestigationChart — the figure card keeps the chart's affordances", () => {
  const REAL_EMIT = useSessionStore.getState().emitRefinement;
  afterEach(() => {
    useSessionStore.setState({ emitRefinement: REAL_EMIT });
  });

  it("emits the same DrillInto the bar emitted, from a keyboard-reachable button", () => {
    const emitRefinement = vi.fn();
    useSessionStore.setState({ emitRefinement });

    render(
      <InvestigationChart
        spec={spec({
          rows: [
            { label: "CARC 16", referent: "fact_carc_16", values: { denied_dollars: 412300 } },
          ],
        })}
        turnId="turn_1"
      />,
    );

    // A real `<button>`: reachable by keyboard, with an accessible name
    // made of the figure it draws.
    const card = screen.getByRole("button", { name: /CARC 16/ });
    fireEvent.click(card);

    expect(emitRefinement).toHaveBeenCalledWith(
      { op: "DrillInto", target: "fact_carc_16" },
      { turnId: "turn_1", referent: "fact_carc_16" },
    );
  });

  it("falls back to the chart's own target when the wire published no referent", () => {
    const emitRefinement = vi.fn();
    useSessionStore.setState({ emitRefinement });

    render(
      <InvestigationChart
        spec={spec({ rows: [{ label: "CARC 16", values: { denied_dollars: 412300 } }] })}
        turnId="turn_1"
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: /CARC 16/ }));

    expect(emitRefinement).toHaveBeenCalledWith(
      { op: "DrillInto", target: "chart_main:CARC 16" },
      { turnId: "turn_1", referent: undefined },
    );
  });

  it("exports the published rows unchanged — the rule changed the picture, not the file", () => {
    // The CSV is built from the PUBLISHED spec and this policy never
    // touches it: same rows, same series, same values, in the same unit.
    const single = spec({
      rows: [{ label: "CARC 16", referent: "fact_carc_16", values: { denied_dollars: 412300 } }],
    });
    render(<InvestigationChart spec={single} turnId="turn_1" />);

    expect(screen.getByRole("button", { name: /CSV/ })).toBeInTheDocument();

    const csv = chartToCsv(single);
    // The file describes the published spec, not the drawn card.
    expect(csv).toContain("1 row × 1 series");
    // The row, its referent and its value — money in dollars, as the
    // exporter has always written it.
    expect(csv).toContain("CARC 16,fact_carc_16,4123");
    // And the header still names the dimension and the measure.
    expect(csv).toContain("carc,referent,denied dollars");
  });

  it("keeps the footer caption and Expand beside a figure card", () => {
    // Only the PLOT AREA changes. The shell, the title, the notes and the
    // whole footer row are the same objects on all three paths.
    // (`MonitorThis` is driver-gated and renders nothing without one, in
    // this test environment as on a chart.)
    render(
      <InvestigationChart
        spec={spec({
          order: { basis: "value", by: "denied_dollars", descending: true },
          truncation: { shown: 1, total: 12 },
          rows: [{ label: "CARC 16", values: { denied_dollars: 412300 } }],
        })}
        turnId="turn_1"
        investigationId="inv_1"
      />,
    );

    expect(screen.getByText("Denied dollars by CARC")).toBeInTheDocument();
    expect(screen.getByText("CARC · Ordered by denied dollars, high to low")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Showing top 1 of 12/ })).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* FULL SCREEN — the same figure, in a room where it can be read       */
/* ------------------------------------------------------------------ */

/**
 * "We should give the ability to fullscreen graphs so that they're
 * actually readable" — and READABLE is the assertion, not "bigger".
 *
 * The Evidence rail draws these charts 280px wide. Every measured rule on
 * the figure is a function of that width, so the expansion has to re-run
 * them rather than scale the drawn SVG: an enlarged screenshot of
 * "Northbridge Comm…" is still a chart nobody can read.
 *
 * The MARKS are Recharts' and jsdom gives a `ResponsiveContainer` no size,
 * so what the axis draws is asserted where it is decided — `axisTickPlan`,
 * the one function both mounts call — and what the DIALOG carries is
 * asserted in the DOM.
 */
const RANKING = spec({
  title: "Denied dollars by payer",
  xLabel: "payer",
  order: { basis: "value", by: "denied_dollars", descending: true },
  rows: LIVE_PAYERS.map((label, i) => ({
    label,
    values: { denied_dollars: (LIVE_PAYERS.length - i) * 1000 },
  })),
});

describe("InvestigationChart — the axis is re-spelled at the width it is drawn at", () => {
  it("elides the twelve-payer ranking in the rail and spells it whole full screen", () => {
    const rail = axisTickPlan(LIVE_PAYERS, { kind: "bar", plotWidth: 280 });
    const full = axisTickPlan(LIVE_PAYERS, { kind: "bar", plotWidth: 1360, expanded: true });

    // The rail cannot hold these names: it rotates, and it still cuts.
    expect(rail.rotate).toBe(true);
    expect(rail.text.get("Summit Peak Medicare Advantage")).toContain("…");
    expect(rail.text.get("Silverline Medicare Advantage")).toContain("…");
    expect(rail.text.get("Northbridge Commercial")).toContain("…");

    // Full screen every one of them is drawn as the wire published it —
    // which is the whole payoff the owner asked for.
    for (const payer of LIVE_PAYERS) expect(full.text.get(payer)).toBe(payer);
  });

  it("keeps the inline figure exactly as it was", () => {
    // The expansion must not move the axis of the chart already on screen.
    const rail = axisTickPlan(LIVE_PAYERS, { kind: "bar", plotWidth: 280 });
    const before = axisTickLabels(LIVE_PAYERS, ROTATED_TICK_BASE);
    for (const payer of LIVE_PAYERS) expect(rail.text.get(payer)).toBe(before.get(payer));
  });

  it("budgets the fullscreen label against the axis room it will be drawn in", () => {
    // `rotatedTickBase` is `rotatedAxisHeight` solved for length, so a
    // label drawn at the budget must still fit under the cap that bought
    // it. Two constants that disagree here is a name cut off by the SVG's
    // bottom edge — the defect the measured axis height exists to end.
    const base = rotatedTickBase(EXPANDED_MAX_AXIS_HEIGHT);
    expect(base).toBeGreaterThan(ROTATED_TICK_BASE);
    expect(rotatedAxisHeight(["A".repeat(base)], EXPANDED_MAX_AXIS_HEIGHT)).toBeLessThanOrEqual(
      EXPANDED_MAX_AXIS_HEIGHT,
    );
    // …and it grows with the room, rather than being a second constant.
    expect(rotatedTickBase(400)).toBeGreaterThan(base);
  });

  it("still shortens a flat tick to its own band, at either size", () => {
    // Fullscreen is not "print everything": a label wider than its band
    // collides with its neighbour at any width. Twelve categories in a
    // 1360px plot get ~109px each — about 13 characters.
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun"];
    const flat = axisTickPlan(months, { kind: "bar", plotWidth: 1360, expanded: true });
    expect(flat.rotate).toBe(false);
    for (const month of months) expect(flat.text.get(month)).toBe(month);
  });
});

/**
 * …AND THE AXIS THAT DOES NOT NEED RE-SPELLING, because the figure turned.
 *
 * Every fix above is a workaround for one fact: a payer name is 20-30
 * characters and a vertical bar's band is 60px wide. A ranking is now drawn
 * on its side (`isRankedCategorical`), where the label has a gutter to
 * itself and runs left to right — so the rotation, the −35° geometry and
 * most of the elision above simply do not apply to it. What replaces them
 * is one number: how wide the gutter may be.
 */
describe("InvestigationChart — a ranking's axis reads left to right", () => {
  it("never rotates, whatever the names cost", () => {
    const rail = axisTickPlan(LIVE_PAYERS, {
      kind: "bar",
      plotWidth: 280,
      orientation: "horizontal",
    });
    // The same twelve payers rotate at this width when they are drawn as
    // columns — see the test above. Sideways they do not.
    expect(rail.rotate).toBe(false);
    expect(axisTickPlan(LIVE_PAYERS, { kind: "bar", plotWidth: 280 }).rotate).toBe(true);
  });

  it("spells every name whole once the gutter can hold it", () => {
    const wide = axisTickPlan(LIVE_PAYERS, {
      kind: "bar",
      plotWidth: 1360,
      orientation: "horizontal",
      expanded: true,
    });
    for (const payer of LIVE_PAYERS) expect(wide.text.get(payer)).toBe(payer);
  });

  it("elides only past the cap, and still never twice under one label", () => {
    const rail = axisTickPlan(LIVE_PAYERS, {
      kind: "bar",
      plotWidth: 280,
      orientation: "horizontal",
    });
    const drawn = LIVE_PAYERS.map((payer) => rail.text.get(payer));
    // The rule the whole tick machinery exists for survives the turn: two
    // payers are never printed under one label.
    expect(new Set(drawn).size).toBe(LIVE_PAYERS.length);
    expect(rail.text.get("Summit Peak Medicare Advantage")).toContain("…");
  });

  it("buys the gutter from the longest name, and stops buying", () => {
    // A gutter is bought from the plot. Four payers whose longest name is
    // "Atlas Commercial" do not take 280px and leave the bars in a third
    // of the card.
    const short = horizontalAxisWidth(["Atlas Commercial", "State Medicaid"], 720);
    const long = horizontalAxisWidth(["Summit Peak Medicare Advantage"], 720);
    expect(short).toBeLessThan(long);
    expect(long).toBeLessThanOrEqual(horizontalGutterCap(720));
    // …and it is a SHARE of the figure, so the same names cost less of a
    // rail than of a dialog.
    expect(horizontalGutterCap(280)).toBeLessThan(horizontalGutterCap(1360, true));
    // …with a floor, so a figure narrow enough that a third of it is not a
    // name still gets enough gutter to print one.
    expect(horizontalGutterCap(120)).toBe(MIN_HORIZONTAL_GUTTER);
    expect(horizontalGutterCap(0)).toBe(MIN_HORIZONTAL_GUTTER);
  });

  it("budgets characters off the same cap it draws in pixels", () => {
    // Two constants that disagree here is a name budgeted for and then cut
    // off by the gutter that bought it.
    const budget = horizontalTickBudget(720);
    expect(horizontalAxisWidth(["A".repeat(budget)], 720)).toBeLessThanOrEqual(
      horizontalGutterCap(720),
    );
    expect(horizontalTickBudget(1360, true)).toBeGreaterThan(budget);
  });
});

describe("InvestigationChart — the expand affordance", () => {
  it("is on a multi-datum chart, persistently and by its figure's name", () => {
    render(<InvestigationChart spec={RANKING} turnId="turn_1" />);

    const expand = screen.getByRole("button", {
      name: "View full screen: Denied dollars by payer",
    });
    // PERSISTENT, not hover-revealed: the house rule `MonitorThis` states
    // at its own trigger. A control that appears on hover does not exist
    // for a touch user, in a screenshot, or on a projector.
    expect(expand.className).not.toMatch(/opacity-0|group-hover/);
    // And reachable by keyboard, in the card's own order.
    expect(expand).not.toHaveAttribute("tabindex", "-1");
  });

  it("is absent on a figure card — there is nothing in one number to enlarge", () => {
    render(
      <InvestigationChart
        spec={spec({ rows: [{ label: "CARC 16", values: { denied_dollars: 412300 } }] })}
        turnId="turn_1"
      />,
    );

    expect(screen.queryByRole("button", { name: /View full screen/ })).not.toBeInTheDocument();
  });

  it("is absent on a chart that was not drawn at all", () => {
    render(
      <InvestigationChart
        spec={spec({
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

    expect(screen.queryByRole("button", { name: /View full screen/ })).not.toBeInTheDocument();
  });

  it("no longer shares its glyph with the control that asks for more rows", () => {
    // "Expand" fetches the four rows the engine did not send — a new turn,
    // new numbers — and it wore the maximize glyph one gap away from the
    // control that opens this same figure full screen.
    const { container } = render(
      <InvestigationChart
        spec={spec({ ...RANKING, truncation: { shown: 12, total: 30 } })}
        turnId="turn_1"
      />,
    );

    const rows = screen.getByRole("button", { name: /Showing top 12 of 30/ });
    const full = screen.getByRole("button", { name: /View full screen/ });
    const glyph = (node: HTMLElement): string | null =>
      node.querySelector("svg")?.getAttribute("class") ?? null;
    expect(glyph(rows)).not.toBe(glyph(full));
    expect(container.querySelectorAll(".lucide-maximize-2")).toHaveLength(1);
  });
});

describe("InvestigationChart — what the fullscreen dialog carries", () => {
  const REAL_EMIT = useSessionStore.getState().emitRefinement;
  afterEach(() => {
    useSessionStore.setState({ emitRefinement: REAL_EMIT });
  });

  /** The ranking, with every honesty mark the wire can put on one. */
  const MARKED = spec({
    ...RANKING,
    notes: ["upper bounds: 1 of 12 marks are ceilings, not measurements."],
    truncation: { shown: 12, total: 30 },
    rows: RANKING.rows.map((row, i) =>
      i === 0 ? { ...row, bounded: true, denominator: 41 } : row,
    ),
  });

  async function openFullScreen() {
    const user = userEvent.setup();
    render(<InvestigationChart spec={MARKED} turnId="turn_1" investigationId="inv_1" />);
    // Held before the click: an open modal marks everything behind it
    // `aria-hidden`, which is the point of a modal and also means the
    // trigger is no longer findable by role while the dialog is up.
    const expand = screen.getByRole("button", { name: /View full screen/ });
    await user.click(expand);
    return { user, expand, dialog: await screen.findByRole("dialog") };
  }

  it("carries the title, the context line, the notes and the honesty marks", async () => {
    const { dialog } = await openFullScreen();
    const panel = within(dialog);

    // The title is the dialog's own heading — once, not twice.
    expect(panel.getByRole("heading", { name: /Denied dollars by payer/ })).toBeInTheDocument();
    expect(panel.getAllByText(/Denied dollars by payer/)).toHaveLength(1);
    // The context line under the figure.
    expect(
      panel.getByText("Payer · Ordered by denied dollars, high to low"),
    ).toBeInTheDocument();
    // The engine's own census, and what "≤" means — the marks travel.
    expect(
      panel.getByText(/Upper bounds: 1 of 12 marks are ceilings, not measurements./),
    ).toBeInTheDocument();
    expect(panel.getByText(/≤ means at most/)).toBeInTheDocument();
  });

  it("keeps the action row working inside it", async () => {
    const emitRefinement = vi.fn();
    useSessionStore.setState({ emitRefinement });
    const { user, dialog } = await openFullScreen();
    const panel = within(dialog);

    // The export is here, on the same published rows.
    // (`MonitorThis` is driver-gated and renders nothing in this
    // environment — on the fullscreen figure as on the inline one.)
    expect(panel.getByRole("button", { name: /CSV/ })).toBeInTheDocument();

    // …and a refinement asked for from inside the dialog is emitted, then
    // stands down: it starts a new turn, and a 90vw picture of the old one
    // over the answer streaming underneath is not a view of anything.
    await user.click(panel.getByRole("button", { name: /Showing top 12 of 30/ }));
    expect(emitRefinement).toHaveBeenCalledWith({ op: "Expand" }, { turnId: "turn_1" });
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("closes on Esc and gives focus back to the control that opened it", async () => {
    const { user, expand } = await openFullScreen();

    // Focus moved INTO the dialog when it opened.
    expect(document.activeElement).not.toBe(expand);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    // …and back out to where it came from, not to the top of the document.
    await waitFor(() => expect(expand).toHaveFocus());
  });

  it("closes when the overlay behind it is clicked", async () => {
    const { user } = await openFullScreen();
    const overlay = document.querySelector(".overlay-in");
    expect(overlay).not.toBeNull();

    await user.click(overlay as Element);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("opens with the house animation, which reduced motion already switches off", async () => {
    const { dialog } = await openFullScreen();
    expect(dialog.className).toContain("panel-in");
    expect(document.querySelector(".overlay-in")).not.toBeNull();

    // The classes are only half the promise; this is the other half, read
    // from the stylesheet that makes it. A dialog that grew its own
    // keyframes would pass the assertion above and animate anyway for a
    // reader who asked it not to.
    const css = readFileSync(
      path.resolve(import.meta.dirname, "../../globals.css"),
      "utf8",
    );
    const reduced = css.slice(css.indexOf("@media (prefers-reduced-motion: reduce)"));
    expect(reduced).toContain(".panel-in");
    expect(reduced).toContain(".overlay-in");
  });
});
