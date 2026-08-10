/**
 * The adversary's exact worry, rendered.
 *
 * Live, a finding about State Medicaid MCO (a Medicaid managed-care
 * plan, provider-side, 29.5%) carries an ACA-marketplace band of 19–20%
 * gathered by machine from plan-reported CMS transparency data. Those are
 * different populations measured on different bases, and the narrative
 * quoted the comparison with full confidence while carrying neither the
 * review status nor the cautions.
 *
 * So the assertions here are about what must be ON SCREEN beside the
 * number, not about layout: the cohort label, the period, the authority,
 * an unmistakable "unreviewed" marker, and a route to the cautions.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BenchmarkStrip, compareToRange } from "@/components/findings/BenchmarkStrip";
import type { Benchmark, MeasuredValue } from "@/lib/types";

afterEach(cleanup);

const KFF: Benchmark = {
  id: "benchmark.denial_rate.marketplace_kff",
  metricId: "denial_rate",
  cohortLabel: "ACA marketplace (HealthCare.gov issuers) - in-network claims, plan-reported",
  valueLow: "19",
  valueHigh: "20",
  unit: "percent of in-network claims denied",
  period: "2023-2024",
  authority: "independent analysis of CMS transparency data",
  reviewStatus: "machine_researched",
  cautions: [
    "20% in 2023 (436M claims), 19% in 2024 (451M claims); insurer-level range 1-54% (2023)",
    "Plan-reported data with no initial/final distinction",
  ],
  sources: ["Claims Denials and Appeals in ACA Marketplace Plans in 2024"],
};

const MEASURED: MeasuredValue = { metricId: "denial_rate", value: 29.5082, unit: "percent" };

describe("BenchmarkStrip", () => {
  it("shows the range with its cohort, period and authority on screen", () => {
    render(<BenchmarkStrip benchmarks={[KFF]} measured={MEASURED} referent="F1" />);
    expect(screen.getByText("19–20")).toBeInTheDocument();
    expect(screen.getByText("percent of in-network claims denied")).toBeInTheDocument();
    // The cohort is the whole story of why this band may not apply, so it
    // is rendered in full rather than tucked into a tooltip.
    expect(
      screen.getByText(/ACA marketplace \(HealthCare\.gov issuers\).*2023-2024.*CMS transparency/),
    ).toBeInTheDocument();
  });

  // Sentence case, and the chip is a MARK rather than a warning: the
  // relief is a dashed outline, not amber. An external reference band is
  // not a finding against the analyst's own number.
  it("marks a machine-researched range as unreviewed, prominently", () => {
    render(<BenchmarkStrip benchmarks={[KFF]} measured={MEASURED} referent="F1" />);
    const chip = screen.getByText("Unreviewed");
    expect(chip).toBeInTheDocument();
    expect(chip.className).toContain("border-dashed");
    expect(chip.className).not.toContain("text-warning");
    expect(chip).toHaveAttribute("title", expect.stringContaining("not reviewed by a person"));
    // Never dressed up as the engine's own certification.
    expect(screen.queryByText(/certified/i)).not.toBeInTheDocument();
  });

  it("states the finding's own figure against the range, as arithmetic", () => {
    render(<BenchmarkStrip benchmarks={[KFF]} measured={MEASURED} referent="F1" />);
    expect(screen.getByText(/29\.5% is 9\.5 points above the top of this range/)).toBeInTheDocument();
  });

  it("keeps the cautions one keystroke away and announces the disclosure", () => {
    render(<BenchmarkStrip benchmarks={[KFF]} measured={MEASURED} referent="F1" />);
    const toggle = screen.getByRole("button", { name: /2 cautions on this range/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText(/Plan-reported data/)).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText(/Plan-reported data with no initial\/final distinction/)).toBeInTheDocument();
    expect(screen.getByText(/^Source: Claims Denials and Appeals/)).toBeInTheDocument();
  });

  it("collapses a long list behind a counted, announced disclosure", () => {
    const many = Array.from({ length: 7 }, (_, i) => ({
      ...KFF,
      id: `b${i}`,
      cohortLabel: `cohort ${i}`,
    }));
    render(<BenchmarkStrip benchmarks={many} measured={MEASURED} referent="F1" />);
    expect(screen.getByText("cohort 0", { exact: false })).toBeInTheDocument();
    expect(screen.queryByText("cohort 4", { exact: false })).not.toBeInTheDocument();
    const more = screen.getByRole("button", { name: /5 more ranges for this measure/ });
    expect(more).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(more);
    expect(screen.getByText("cohort 4", { exact: false })).toBeInTheDocument();
  });

  it("renders nothing at all when the turn published no benchmarks", () => {
    const { container } = render(
      <BenchmarkStrip benchmarks={[]} measured={MEASURED} referent="F1" />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});

describe("compareToRange", () => {
  it("says above, below or inside — in points, never as a verdict", () => {
    expect(compareToRange(KFF, MEASURED)).toContain("above the top of this range");
    expect(compareToRange(KFF, { ...MEASURED, value: 11.6 })).toContain(
      "below the bottom of this range",
    );
    expect(compareToRange(KFF, { ...MEASURED, value: 19.5 })).toContain("falls inside this range");
    // Nothing here claims the two populations are comparable — that is
    // what the cohort label beside it is for.
    expect(compareToRange(KFF, MEASURED)).not.toMatch(/worse|better|peer/i);
  });

  it("refuses to compare across units and says so rather than going quiet", () => {
    const money: MeasuredValue = { metricId: "denied_dollars", value: 3395490, unit: "cents" };
    expect(compareToRange(KFF, money)).toContain("stated on a different basis");
  });

  it("compares nothing when the finding published no measure", () => {
    expect(compareToRange(KFF, undefined)).toBeUndefined();
  });

  it("compares nothing when the range's endpoints are not numbers", () => {
    expect(compareToRange({ ...KFF, valueLow: "n/a", valueHigh: "" }, MEASURED)).toBeUndefined();
  });
});
