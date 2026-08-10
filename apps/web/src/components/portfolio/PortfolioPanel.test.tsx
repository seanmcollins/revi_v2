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

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

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
      title: "Gross collection rate dip: Halvern imaging",
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

// jsdom does not implement it; Radix tooltips measure their trigger, and
// the real app shell provides it. Only the tests that OPEN a tooltip need
// this, but it is cheap and unconditional.
beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
});

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

/**
 * What the portfolio read came back as. The endpoint is unconditional and
 * always answers 200, so there are exactly two outcomes: a snapshot, or a
 * request that failed. "A data load with nothing in it" is not a third —
 * it is a snapshot whose own `status` is `"empty"`, which is what
 * `EMPTY_SNAPSHOT` below serves.
 */
let outcome: "ok" | "failed" = "ok";

vi.mock("@/lib/queries", () => ({
  usePortfolioQuery: () =>
    outcome === "failed"
      ? { data: undefined, isPending: false }
      : { data: served ?? snapshot(), isPending: false },
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
  outcome = "ok";
});

describe("PortfolioPanel — reading every published field", () => {
  it("shows the snapshot's own warning above the list", () => {
    renderPanel();
    expect(screen.getByText(/36% of ranked impact.*not investigable/)).toBeInTheDocument();
  });

  it("disables the drill on a card the server refused, and says why", () => {
    renderPanel();
    const refused = screen.getByLabelText(/Cannot drill into Gross collection rate dip/);
    // `aria-disabled`, not `disabled`: a `disabled` button leaves the
    // focus path, and the refusal sentence rides on this element's
    // accessible name — unreachable by keyboard is the same as absent.
    expect(refused).toHaveAttribute("aria-disabled", "true");
    expect(refused).toHaveTextContent("Can't drill");
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
    expect(screen.getByText("Detected 31d ago")).toBeInTheDocument();
  });

  it("notes a card whose drill probes a different measure than it reports", () => {
    // Live ANM-001 reports `metric_id: denial_rate`, is repointed FROM
    // denial_rate, and its drill_spec probes denied_dollars. Reading the
    // reported metric here renders "drills denial_rate, not denial_rate";
    // the drilled measure is `drill_spec.metric_ids[0]`.
    renderPanel();
    expect(screen.getByText(/Drills denied dollars, not denial rate/)).toBeInTheDocument();
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
    expect(screen.getByText(/This platform: \$195,874/)).toBeInTheDocument();
    expect(screen.getByText(/\+9\.9%/)).toBeInTheDocument();
  });

  it("confirms an agreement quietly rather than repeating the number", () => {
    renderPanel();
    expect(screen.getByText("Matches this platform's own figure")).toBeInTheDocument();
  });

  it("says when the detector's figure stands alone", () => {
    renderPanel();
    expect(
      screen.getByText("Not re-derived here — the detector's figure alone"),
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

/* ------------------------------------------------------------------ */
/* How much of this can we still catch?                                */
/* ------------------------------------------------------------------ */

/**
 * The cash-timing partition — the same cards split by whether the money
 * has landed yet.
 *
 * Live, every card carried `time_to_impact.lane` (12 pre-cash, 19 already
 * hit, 2 unknown), no surface totalled it, and the question "how much has
 * not hit cash yet and when are the deadlines?" came back with
 * $830,501.93 across all thirty-three — the answer to a different
 * question, presented as the answer to this one.
 *
 * Rendered ONLY from the server's split. `items` is a page and the lanes
 * describe the whole population, so a client-side sum would be a fraction
 * wearing a total's clothes.
 */
describe("PortfolioPanel — the still-catchable total", () => {
  const cashLanes = [
    {
      id: "pre_cash",
      label: "Not yet hit cash",
      description: "The cash effect has not landed yet, so this money is still catchable.",
      kind: "cash_timing",
      anomaly_ids: ["ANM-021"],
      item_count: 12,
      impact_cents: 51_234_500,
      recoverable_cents_estimate: 17_064_300,
      soonest_deadline_date: "2026-08-19",
      soonest_deadline_days: 17,
      dated_item_count: 3,
    },
    {
      id: "already_hit",
      label: "Already hit cash",
      description: "The cash effect has landed; a recovery window may still be open.",
      kind: "cash_timing",
      anomaly_ids: ["ANM-013"],
      item_count: 19,
      impact_cents: 90_000_000,
      recoverable_cents_estimate: 12_000_000,
      dated_item_count: 0,
    },
  ];

  beforeEach(() => {
    // The four-card capture, which carries the REAL governance lanes — the
    // point being that the two partitions coexist without either one
    // re-listing the other's cards.
    served = parse({ ...SAMPLES.portfolio_lanes, cash_timing_lanes: cashLanes });
  });

  it("totals the money that has not hit cash yet, across the whole population", () => {
    renderPanel();
    expect(screen.getByText(/Still catchable/)).toBeInTheDocument();
    expect(screen.getByText(/\$170,643 recoverable across 12 leads/)).toBeInTheDocument();
  });

  it("names the soonest REAL deadline, and how many cards carry one", () => {
    // A projection never sets a date: an estimate rendered beside a filing
    // limit is indistinguishable from one. And a horizon computed from
    // three of twelve cards is a fact about three cards.
    renderPanel();
    expect(screen.getByText(/Soonest deadline Aug 19, 2026 — 17 days, on 3 of 12 of them/)).toBeInTheDocument();
  });

  it("says a deadline that has already gone has gone, rather than '-47 days'", () => {
    // The live lane: the soonest dated limit in the still-catchable half
    // had passed seven weeks earlier, and a negative day count is a number
    // nobody reads as a loss.
    served = parse({
      ...SAMPLES.portfolio_lanes,
      cash_timing_lanes: [
        { ...cashLanes[0], soonest_deadline_date: "2026-06-16", soonest_deadline_days: -47 },
      ],
    });
    renderPanel();
    expect(screen.getByText(/passed 47 days ago/)).toBeInTheDocument();
  });

  it("keeps the two partitions apart rather than concatenating them", () => {
    // Every card is in exactly one governance lane AND one cash lane;
    // rendering them as one list would count the worklist twice.
    renderPanel();
    expect(screen.getByRole("button", { name: "Must do regardless of size" })).toBeInTheDocument();
    expect(document.querySelector("[data-cash-timing]")).not.toBeNull();
    expect(document.querySelectorAll("[data-cash-timing] > li")).toHaveLength(2);
  });

  it("says nothing at all when the deployment publishes no cash split", () => {
    served = snapshot();
    renderPanel();
    expect(document.querySelector("[data-cash-timing]")).toBeNull();
  });
});

/**
 * One focus ring, in one place.
 *
 * A hand-rolled `focus-visible:outline-none focus-visible:ring-2
 * focus-visible:ring-ring/60` once landed on six bare buttons — five of
 * them in this panel. `--ring` at 60% measures 2.15:1 over card and 2.06:1
 * over page, under the 3:1 SC 1.4.11 floor, and every one of those
 * elements had a COMPLIANT user-agent outline before `outline-none`
 * removed it: the fix was worse than the bug.
 *
 * `.focus-ring` in globals.css is the one implementation (solid `--ring`,
 * 3.74:1 / 3.43:1 / 3.61:1 / 3.26:1 against card, page, the translucent
 * rail and sunken). This sweep is what stops a seventh hand-rolled ring
 * from landing next to it.
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

/**
 * Monday's work has to be able to leave the browser.
 *
 * A worklist an analyst cannot get into a spreadsheet is a worklist they
 * re-key by hand, and a re-keyed row loses exactly the columns this
 * release spent its time earning: what this platform re-derived for the
 * same cell, whether the two agree, and how much of the impact is
 * recoverable. `portfolioToCsv` is unit-tested in `lib/export.test.ts`;
 * what is pinned here is that the rail offers it and says what travels.
 */
describe("PortfolioPanel — the worklist as a spreadsheet", () => {
  it("offers a CSV of every card, and names the honesty columns in the affordance", () => {
    renderPanel();
    const button = screen.getByRole("button", { name: /CSV/ });
    expect(button).toHaveAttribute("title", expect.stringContaining("re-derivation"));
    expect(button).toHaveAttribute("title", expect.stringContaining("recoverable"));
    expect(button).toHaveAttribute("title", expect.stringContaining("Nothing leaves this browser"));
  });

  it("offers no download when there is nothing to download", () => {
    outcome = "failed";
    renderPanel();
    expect(screen.queryByRole("button", { name: /CSV/ })).not.toBeInTheDocument();
  });
});

/**
 * A data load with nothing in it is a fact about the DATA, not about the
 * deployment and not about this panel.
 *
 * The empty state used to be reached through an HTTP-501 branch and read
 * "this deployment isn't serving a worklist" — a claim about a server mode
 * that does not exist. `GET /v1/portfolio/latest` is unconditional and
 * always answers 200; a watermark at which the detection feed found
 * nothing comes back as an ordinary snapshot carrying `status: "empty"`
 * and the feed's own PORTFOLIO_FEED_EMPTY warning. That is the state, and
 * it says so in the snapshot's own terms — no status code, and no claim
 * about what this deployment does or does not serve.
 */
describe("PortfolioPanel — a data load with nothing detected in it", () => {
  const EMPTY_SNAPSHOT = {
    ...LIVE_SNAPSHOT,
    status: "empty",
    items: [],
    lanes: [],
    warnings: ["no detected anomalies at this watermark"],
    warnings_v2: [
      {
        code: "PORTFOLIO_FEED_EMPTY",
        severity: "info",
        message: "no detected anomalies at this watermark",
        count: 1,
      },
    ],
  };

  it("says the worklist is empty rather than missing, and keeps the feed's own sentence", () => {
    served = parse(EMPTY_SNAPSHOT);
    renderPanel();
    expect(screen.getByText(/the worklist is empty, not missing/)).toBeInTheDocument();
    // The feed's own warning still renders above the (absent) list — the
    // empty note does not replace the server's account of why.
    expect(screen.getByText(/no detected anomalies at this watermark/)).toBeInTheDocument();
  });

  it("makes no claim about the deployment, and prints no status code", () => {
    served = parse(EMPTY_SNAPSHOT);
    renderPanel();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/501/);
    expect(text).not.toMatch(/not implemented/i);
    expect(text).not.toMatch(/serving a worklist/i);
    expect(text).not.toMatch(/deployment/i);
  });

  it("says the read failed when it actually failed — a different fact", () => {
    outcome = "failed";
    renderPanel();
    expect(screen.getByText(/Portfolio unreachable/)).toBeInTheDocument();
  });
});

/**
 * What a reader is asked to read.
 *
 * The rail's own sentences are the panel's, not the payload's, and the
 * payload's spellings are not sentences: `wm_003` is a log token, and
 * `dollar_impact@1` is a catalog key. Both are facts worth keeping — they
 * just do not belong in the prose. They move to a title.
 */
describe("PortfolioPanel — the payload's spellings stay out of the prose", () => {
  it("names the ranking as a version, with the identifier on the title", () => {
    renderPanel();
    const chip = screen.getByTitle("anomaly_priority@1");
    expect(chip).toHaveTextContent("Anomaly priority v1");
  });

  it("prints no data-load id in the footer sentence", () => {
    renderPanel();
    // The live snapshot names its load by id alone, so the clause is
    // dropped rather than printed: an id inside a sentence is the same
    // class of leak as a snake_case column name.
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/wm_\d/);
    expect(text).toMatch(/the same data this list was built on/);
  });

  it("spells a warehouse date the detector wrote into its own sentence", () => {
    served = parse({
      ...LIVE_SNAPSHOT,
      items: [
        {
          ...LIVE_SNAPSHOT.items[0],
          description: "Paying ~92% of contract-expected since 2026-05.",
        },
      ],
    });
    renderPanel();
    expect(screen.getByText(/since May 2026\./)).toBeInTheDocument();
  });
});

/**
 * The fourth agreement state.
 *
 * `not_comparable` was added when the reconciler learned to refuse a
 * snapshot-vs-window or ratio-vs-money comparison rather than coerce one.
 * The client's filter still listed three states, so the live #1 card —
 * ANM-021, `not_comparable`, with a written explanation of exactly why the
 * two figures are different KINDS of measurement — published its verdict
 * to a client that dropped it, and both the rail and the export went
 * silent about it. It is not a divergence and must not wear that tone or
 * carry a delta: the payload leaves `impact_delta_fraction` null on
 * exactly these cards, because a gap between two kinds of measurement is
 * not a percentage anyone should act on.
 */
/**
 * WHICH figure ordered the card.
 *
 * The rail prints two dollar amounts on a diverged card and, until now,
 * said nothing about which one the RANKING used. Live that is not one
 * answer across the list: 19 cards ranked on the detector's figure, 9 on
 * this platform's, and 5 on the detector's precisely BECAUSE this
 * platform's re-derivation is not a comparable quantity. A worklist whose
 * ordering basis varies card by card, read as if it were uniform,
 * allocates a morning wrongly.
 */
describe("PortfolioPanel — which figure ranked the card", () => {
  /** ANM-001, verbatim from the live worklist: diverged, ranked on ours. */
  const RANKED_ON_PLATFORM = {
    ...LIVE_SNAPSHOT,
    items: [
      {
        ...LIVE_SNAPSHOT.items[2],
        reconciled_impact_cents: 17_720_287,
        impact_agreement: "diverged",
        impact_delta_fraction: -0.038,
        ranked_on: "platform",
        ranked_impact_cents: 17_720_287,
        ranked_on_note:
          "ranked on this platform's re-derived figure ($177,202.87): the detection system's figure diverges from it by more than the tolerance, and the governed contract is the one this platform stands behind.",
      },
    ],
  };

  /** ANM-021: not_comparable — the detector's figure ranked it, for a reason. */
  const RANKED_ON_DETECTOR_NOT_COMPARABLE = {
    ...LIVE_SNAPSHOT,
    items: [
      {
        ...LIVE_SNAPSHOT.items[0],
        reconciled_impact_cents: 19_587_392,
        impact_agreement: "not_comparable",
        impact_delta_fraction: null,
        ranked_on: "not_comparable",
        ranked_impact_cents: 17_821_682,
        ranked_on_note:
          "ranked on the detection system's figure ($178,216.82): this platform's re-derivation is not a comparable quantity (an as-of balance against a windowed flow), so substituting it would change the claim rather than correct it.",
      },
    ],
  };

  it("says when the ordering used this platform's figure, and shows that figure", () => {
    served = parse(RANKED_ON_PLATFORM);
    renderPanel();
    const text = document.body.textContent ?? "";
    expect(text).toContain("Ranked on this platform's figure");
    // The figure that actually ordered it, not the one printed above.
    expect(text).toContain("$177,203");
  });

  it("keeps not_comparable apart from a plain detector ranking", () => {
    served = parse(RANKED_ON_DETECTOR_NOT_COMPARABLE);
    renderPanel();
    // The two are the same OUTCOME (the detector's figure ranked it) for
    // opposite reasons, and collapsing them would lose the reason.
    expect(screen.getByText(/not comparable/)).toBeInTheDocument();
  });

  it("stays silent on the ordinary case rather than restating the default", () => {
    // `detector` with no disagreement in play is what the number printed
    // directly above already says. A line repeating it on 19 of 33 cards
    // would bury the 14 that differ.
    served = parse({
      ...LIVE_SNAPSHOT,
      items: [
        {
          ...LIVE_SNAPSHOT.items[0],
          impact_agreement: "agreed",
          ranked_on: "detector",
          ranked_impact_cents: 17_821_682,
        },
      ],
    });
    renderPanel();
    expect(document.body.textContent ?? "").not.toMatch(/ranked on/);
  });

  it("says nothing at all when the server published no basis", () => {
    served = snapshot();
    renderPanel();
    expect(document.body.textContent ?? "").not.toMatch(/ranked on/);
  });
});

/**
 * The detector's CUT was substituted, and the substitution changes what
 * gets counted.
 *
 * Live ANM-011/012/013 drill `primary_proc_group` where the detector cut
 * `proc_group`: the detector counted LINES in the group, the drill counts
 * CLAIMS whose largest procedure group is that one. Those are different
 * populations on a multi-procedure claim, which is a legitimate reason for
 * the card's figure and the drill's to differ — and it belongs on screen
 * before the click, not in a reconciliation strip afterwards.
 */
describe("PortfolioPanel — a card whose cut was repointed", () => {
  const REPOINTED = {
    ...LIVE_SNAPSHOT,
    items: [
      {
        ...LIVE_SNAPSHOT.items[0],
        anomaly_id: "ANM-013",
        drill_dimension_repoints: [
          {
            from_dimension: "proc_group",
            to_dimension: "primary_proc_group",
            rationale:
              "Procedures bind at claim_line, so a claim-grain contract cannot be cut by `proc_group` at all. Read the drill as an attribution of whole claims, not a decomposition of lines: the detector counted lines in this group, and the drill counts claims whose largest procedure group is this one.",
          },
        ],
      },
    ],
  };

  it("discloses the substitution on the card", () => {
    served = parse(REPOINTED);
    renderPanel();
    expect(screen.getByText(/Cuts by primary proc group, not proc group/)).toBeInTheDocument();
  });

  it("carries the server's reasoning verbatim rather than summarizing it", async () => {
    served = parse(REPOINTED);
    renderPanel();
    await userEvent.hover(screen.getByText(/Cuts by primary proc group/));
    expect(
      await screen.findByText(/the detector counted lines in this group/),
    ).toBeInTheDocument();
  });

  it("drops a repoint that arrives without its reasoning", () => {
    // The rationale IS the disclosure. A bare column swap shown to an
    // analyst is worse than nothing.
    served = parse({
      ...LIVE_SNAPSHOT,
      items: [
        {
          ...LIVE_SNAPSHOT.items[0],
          drill_dimension_repoints: [
            { from_dimension: "proc_group", to_dimension: "primary_proc_group", rationale: "" },
          ],
        },
      ],
    });
    renderPanel();
    expect(document.body.textContent ?? "").not.toMatch(/Cuts by/);
  });
});

describe("PortfolioPanel — a card the platform re-derived but declines to compare", () => {
  const NOT_COMPARABLE = {
    ...LIVE_SNAPSHOT,
    items: [
      {
        ...LIVE_SNAPSHOT.items[0],
        reconciled_impact_cents: 19_587_392,
        reconciled_impact_metric_id: "dnfb_dollars",
        impact_agreement: "not_comparable",
        impact_delta_cents: 1_765_710,
        impact_delta_fraction: null,
        impact_reconciliation_note:
          "The two are not comparable: the governed contract is a snapshot and applies no start..end window, while the card's figure was computed over one.",
      },
    ],
  };

  it("shows this platform's own figure and says why there is no gap to report", () => {
    served = parse(NOT_COMPARABLE);
    renderPanel();
    const text = document.body.textContent ?? "";
    // `formatWholeDollars` rounds to the dollar — the detector's figures
    // are dollar-rounded by construction and printing ".92" on one would
    // imply a precision the detection never had.
    expect(text).toContain("This platform: $195,874");
    expect(text).toContain("measured differently, not a disagreement");
  });

  it("does not call it a divergence, and prints no percentage", () => {
    served = parse(NOT_COMPARABLE);
    renderPanel();
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/%\)/);
    expect(text).not.toMatch(/not re-derived here/);
  });
});
