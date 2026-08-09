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
    // The engine's own emission order is not dressed up as a ranking.
    expect(orderNote(spec({ order: { basis: "wire" } }))).toBeUndefined();
    expect(orderNote(spec())).toBeUndefined();
  });
});
