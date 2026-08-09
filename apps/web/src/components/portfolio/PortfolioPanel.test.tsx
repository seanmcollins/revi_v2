/**
 * The portfolio rail against a LIVE-shaped snapshot.
 *
 * Every fixture below is trimmed from `GET /v1/portfolio/latest` on a
 * running API (33 cards, 27 fields each). The three behaviours pinned
 * here are the three the panel used to get wrong: it drew an identical
 * live "Drill in" button on cards the server had refused, it showed a
 * detection's headline dollars with no hint that a fraction of them were
 * recoverable, and it stacked all 33 rows with no mention of the
 * snapshot's own warning about the 36% it cannot open.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PortfolioPanel } from "@/components/portfolio/PortfolioPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { parsePortfolioSnapshot, type PortfolioSnapshotData } from "@/lib/contract";
import { useSessionStore } from "@/lib/store";

const LIVE_SNAPSHOT = {
  status: "ok",
  tenant: "demo",
  watermark_id: "wm_003",
  formula_version: "anomaly_priority@1",
  warnings: [
    "4 of 33 detected anomalies (36% of ranked impact) are not investigable at this catalog and pack version",
  ],
  items: [
    {
      anomaly_id: "ANM-021",
      provenance: "external_detection",
      priority_formula_version: "anomaly_priority@1",
      source_watermark_id: "wm_003",
      title: "DNFB accumulation: Northgate general-surgery discharges",
      description: "22 unbilled discharges totaling $178,217.",
      category: "dnfb",
      metric_id: "dnfb_dollars",
      severity: "critical",
      confidence: "0.90",
      status: "open",
      impact_cents: 17_821_682,
      age_days: 31,
      recoverable_cents_estimate: 16_930_598,
      actionability_label: "highly recoverable",
      actionability_rationale: "Discharged-not-final-billed dollars are not lost, only unbilled.",
      priority_score: 0.328589,
      compliance_floor_applied: false,
      drill_spec: { metric_ids: ["dnfb_dollars"] },
      drillable: true,
      drill_unavailable_reason: null,
      drill_repointed_from: null,
      drill_repoint_rationale: null,
    },
    {
      anomaly_id: "ANM-013",
      provenance: "external_detection",
      priority_formula_version: "anomaly_priority@1",
      source_watermark_id: "wm_003",
      title: "Gross collection rate dip: Meridian imaging",
      description: "Collection rate below expected on imaging lines.",
      category: "collection_rate",
      metric_id: "gross_collection_rate",
      severity: "high",
      impact_cents: 49_326_600,
      age_days: 44,
      recoverable_cents_estimate: 986_500,
      actionability_label: "marginally recoverable",
      actionability_rationale: "Most of this variance is contractual and not collectable.",
      drillable: false,
      drill_unavailable_reason:
        "GRAIN_INCOMPATIBLE: dimension 'proc_group' is not a legal scope dimension for ratio metric 'gross_collection_rate'",
      drill_repointed_from: null,
      drill_repoint_rationale: null,
    },
    {
      anomaly_id: "ANM-001",
      provenance: "external_detection",
      priority_formula_version: "anomaly_priority@1",
      source_watermark_id: "wm_003",
      title: "Medical-necessity denial spike: Summit Peak MA Cardiology",
      description: "27 denials totaling $170,643.",
      category: "denial_spike",
      metric_id: "denied_dollars",
      severity: "critical",
      impact_cents: 17_064_300,
      age_days: 44,
      recoverable_cents_estimate: 17_064_300,
      actionability_label: "partially recoverable",
      drill_spec: { metric_ids: ["denied_dollars"] },
      drillable: true,
      drill_repointed_from: "denial_rate",
      drill_repoint_rationale:
        "The detector published a dollar impact under a ratio metric that is unprobeable at its own grain.",
    },
  ],
};

function snapshot(): PortfolioSnapshotData {
  const { value } = parsePortfolioSnapshot(LIVE_SNAPSHOT);
  if (!value) throw new Error("live snapshot failed contract validation");
  return value;
}

vi.mock("@/lib/queries", () => ({
  usePortfolioQuery: () => ({
    data: { kind: "ok", snapshot: snapshot() },
    isPending: false,
  }),
}));

function renderPanel() {
  return render(
    <TooltipProvider>
      <PortfolioPanel />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  useSessionStore.setState({
    connection: { mode: "api", state: "online", healthChecked: true },
  });
});

afterEach(cleanup);

describe("PortfolioPanel — reading every published field", () => {
  it("shows the snapshot's own warning above the list", () => {
    renderPanel();
    expect(screen.getByText(/36% of ranked impact.*not investigable/)).toBeInTheDocument();
  });

  it("disables the drill on a card the server refused, and says why", () => {
    renderPanel();
    const refused = screen.getByLabelText(/Cannot drill into Gross collection rate dip/);
    expect(refused).toBeDisabled();
    expect(refused.getAttribute("aria-label")).toContain("GRAIN_INCOMPATIBLE");
  });

  it("keeps a live drill on the cards the server did publish a handle for", () => {
    renderPanel();
    expect(screen.getByLabelText(/Drill into DNFB accumulation/)).toBeEnabled();
  });

  it("renders recoverable dollars alongside detected dollars when they differ", () => {
    renderPanel();
    expect(screen.getByText(/\$493,266/)).toBeInTheDocument();
    expect(screen.getByText(/~\$9,865 recoverable/)).toBeInTheDocument();
    expect(screen.getByText("marginally recoverable")).toBeInTheDocument();
  });

  it("does not repeat itself when the whole impact is recoverable", () => {
    renderPanel();
    // ANM-001's estimate equals its impact — one number, said once.
    expect(screen.queryByText(/~\$170,643 recoverable/)).not.toBeInTheDocument();
  });

  it("shows the detector's severity and the card's age", () => {
    renderPanel();
    expect(screen.getAllByText("critical").length).toBeGreaterThan(0);
    expect(screen.getByText("detected 31d ago")).toBeInTheDocument();
  });

  it("notes a card whose drill probes a different measure than it reports", () => {
    // Live ANM-001 reports `metric_id: denial_rate`, is repointed FROM
    // denial_rate, and its drill_spec probes denied_dollars. Reading the
    // reported metric here renders "drills denial_rate, not denial_rate";
    // the drilled measure is `drill_spec.metric_ids[0]`.
    renderPanel();
    expect(screen.getByText(/Drills denied_dollars, not denial_rate/)).toBeInTheDocument();
  });

  it("says nothing when the repoint names the measure the drill already probes", () => {
    // Guard against the sentence that reads "drills X, not X".
    const { value } = parsePortfolioSnapshot({
      items: [
        {
          anomaly_id: "ANM-999",
          title: "Same measure both sides",
          impact_cents: 1_000,
          provenance: "external_detection",
          metric_id: "denial_rate",
          drill_spec: { metric_ids: ["denial_rate"] },
          drillable: true,
          drill_repointed_from: "denial_rate",
        },
      ],
    });
    expect(value?.items[0]?.drillMetricId).toBe("denial_rate");
    expect(value?.items[0]?.drillRepointedFrom).toBe("denial_rate");
  });
});
