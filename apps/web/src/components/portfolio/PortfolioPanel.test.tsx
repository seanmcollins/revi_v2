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
import userEvent from "@testing-library/user-event";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PortfolioPanel } from "@/components/portfolio/PortfolioPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import RAW_SAMPLES from "@/lib/__fixtures__/wire-samples.json";
import { parsePortfolioSnapshot, type PortfolioSnapshotData } from "@/lib/contract";
import { useSessionStore } from "@/lib/store";

/* eslint-disable-next-line @typescript-eslint/no-explicit-any */
const SAMPLES = RAW_SAMPLES as any;

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

function parse(raw: unknown): PortfolioSnapshotData {
  const { value } = parsePortfolioSnapshot(raw);
  if (!value) throw new Error("live snapshot failed contract validation");
  return value;
}

function snapshot(): PortfolioSnapshotData {
  return parse(LIVE_SNAPSHOT);
}

/**
 * Which snapshot the panel is rendered against. Set by the lane suite
 * below, which needs the four-card `portfolio_lanes` capture rather than
 * this file's three-card one; cleared after every test.
 */
let served: PortfolioSnapshotData | null = null;

vi.mock("@/lib/queries", () => ({
  usePortfolioQuery: () => ({
    data: { kind: "ok", snapshot: served ?? snapshot() },
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

afterEach(() => {
  cleanup();
  served = null;
});

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

/* ------------------------------------------------------------------ */
/* Lanes, impact agreement and the drill's anomaly_ref                 */
/* ------------------------------------------------------------------ */

/**
 * `portfolio_lanes` is four cards trimmed verbatim from a live
 * `GET /v1/portfolio/latest`: one per agreement state (ANM-021 diverged,
 * ANM-023 agreed, ANM-015 unavailable) plus ANM-001, with both published
 * lanes and their real memberships.
 */
describe("PortfolioPanel — lanes and impact agreement", () => {
  beforeEach(() => {
    served = parse(SAMPLES.portfolio_lanes);
  });

  it("splits the rail by the lanes the server published", () => {
    renderPanel();
    expect(screen.getByRole("button", { name: "Must do regardless of size" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Ranked by value recoverable" }),
    ).toBeInTheDocument();
  });

  it("keeps a compliance card visible instead of letting size bury it", () => {
    // ANM-023 is $48,939 against ANM-021's $178,217. In one ranked list it
    // sinks; in its own lane it is the first thing in that lane.
    renderPanel();
    const headings = [...document.querySelectorAll("section")].map(
      (section) => section.textContent ?? "",
    );
    const compliance = headings.find((text) => text.includes("Must do regardless of size"));
    expect(compliance).toContain("Double-posted payments awaiting refund");
  });

  it("shows both figures on a card this platform re-derived differently", () => {
    renderPanel();
    // The detector's assertion (also quoted in its own description) …
    expect(screen.getAllByText(/\$178,217/).length).toBeGreaterThan(0);
    // … and this platform's re-derivation of the same cell, with the gap.
    expect(screen.getByText(/this platform: \$195,874/)).toBeInTheDocument();
    expect(screen.getByText(/\+9\.9%/)).toBeInTheDocument();
  });

  it("confirms an agreement quietly rather than repeating the number", () => {
    renderPanel();
    expect(screen.getByText("Matches this platform's own figure")).toBeInTheDocument();
  });

  it("says when the detector's figure stands alone", () => {
    renderPanel();
    expect(
      screen.getByText("not re-derived here — the detector's figure alone"),
    ).toBeInTheDocument();
  });

  it("names the measure in the pack's governed words when the id overclaims", () => {
    renderPanel();
    expect(
      screen.getByText("Discharged not final billed (unbilled discharges)"),
    ).toBeInTheDocument();
  });

  it("carries the card's id on the drill so the answer can reconcile to it", async () => {
    const submit = vi.fn();
    useSessionStore.setState({ submit });
    renderPanel();
    await userEvent.click(screen.getByLabelText(/Drill into DNFB accumulation/));
    expect(submit).toHaveBeenCalledWith(
      expect.objectContaining({ anomalyRef: "ANM-021", spec: expect.any(Object) }),
    );
  });

  it("draws one ungrouped list when no lanes are published", () => {
    served = snapshot();
    renderPanel();
    expect(
      screen.queryByRole("button", { name: "Ranked by value recoverable" }),
    ).not.toBeInTheDocument();
  });

  it("titles the snapshot's cautions from their codes", () => {
    renderPanel();
    expect(screen.getByText("Some of this list cannot be opened")).toBeInTheDocument();
    expect(
      screen.getByText("Some figures disagree with this platform's own"),
    ).toBeInTheDocument();
  });
});

/**
 * One focus ring, in one place.
 *
 * The round-1 fix pass hand-rolled `focus-visible:outline-none
 * focus-visible:ring-2 focus-visible:ring-ring/60` onto six bare buttons
 * — five of them in this panel. `--ring` at 60% measures 2.15:1 over card
 * and 2.06:1 over page in light, under the 3:1 SC 1.4.11 floor, and every
 * one of those elements had a COMPLIANT user-agent outline before
 * `outline-none` removed it: the fix made them worse than the bug.
 *
 * `.focus-ring` in globals.css is the one implementation (solid `--ring`,
 * 3.74:1 / 3.43:1 / 3.61:1 / 3.26:1 light against card, page, the
 * translucent rail and sunken; 10.05:1 dark). This sweep is what stops a
 * seventh hand-rolled ring from landing next to it.
 */
describe("focus rings do not drift back into components", () => {
  const SRC = path.resolve(import.meta.dirname, "../..");

  function sources(dir: string): string[] {
    return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) return sources(full);
      return /\.tsx?$/.test(entry.name) && !entry.name.includes(".test.") ? [full] : [];
    });
  }

  it("suppresses no UA outline and dilutes no ring in any app component", () => {
    // `components/ui/*` are the vendored primitives, and they implement
    // the compliant pattern already: they pair the soft `ring-ring/50`
    // halo with a SOLID `focus-visible:border-ring`, which is the part
    // that carries the 3:1. Everything else belongs to this app and uses
    // the shared utility.
    const offenders = sources(SRC)
      .filter((file) => !file.includes(`${path.sep}components${path.sep}ui${path.sep}`))
      .map((file) => [file, readFileSync(file, "utf8")] as const)
      .filter(([, text]) => /focus-visible:outline-none|ring-ring\/\d/.test(text))
      .map(([file]) => path.relative(SRC, file));

    expect(offenders).toEqual([]);
  });
});
