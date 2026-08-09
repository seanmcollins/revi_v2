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

import { InvestigationChart, orderNote } from "@/components/charts/InvestigationChart";
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
    ).toBe("ordered by denied_dollars, high to low");
    expect(orderNote(spec({ order: { basis: "ordinal-bucket" } }))).toBe("ordered by bucket");
    // A DECLARED order and an INFERRED one read differently on purpose:
    // one is the pack's published fact, the other is this client reading
    // numbers out of label text.
    expect(orderNote(spec({ order: { basis: "axis-order", by: "ar_age_bucket" } }))).toBe(
      "in the catalog's declared order for ar_age_bucket",
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
    ).toBe("ordered by denial_rate, high to low; 4 bounded cells held out of it, at the end");
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

  it("states the ceiling census under the figure, in words", () => {
    render(<InvestigationChart spec={bounded} turnId="turn_1" />);
    expect(
      screen.getByText(
        "≤ marks an upper bound — 1 of 2 marks is a ceiling over a suppressed numerator, not a measurement, and it is not ranked against the measured ones.",
      ),
    ).toBeInTheDocument();
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
