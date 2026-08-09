/**
 * The §7.2 context header, and specifically its cohort chip (review F15).
 *
 * The chip used to read "312 payers (pinned)" over a `cohort` field
 * holding `coh_9f2a11…`, and both halves were wrong: the "definition" was
 * the hash (unreadable, and a label that cannot be checked is decoration
 * on a platform whose whole claim is that the context is inspectable), and
 * "payers" was a hardcoded noun over a population whose grain the payload
 * publishes — these are claims, or lines, or remits.
 *
 * The definition and grain below are the live values from a typed
 * three-`drill_into` turn against the running API:
 * `payer in [State Medicaid, Atlas Commercial, Meridian Health]`,
 * 86,415 claims, pinned at wm_003 from D9.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { ContextHeader } from "@/components/answer/ContextHeader";
import type { ContextHeaderData, CohortSummary } from "@/lib/types";

afterEach(cleanup);

const BASE: ContextHeaderData = {
  window: { start: "2026-06-01", end: "2026-07-31", basis: "service" },
  filters: [],
  watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
  packVersion: { packId: "base-rcm", version: "1.0.0" },
};

const LIVE_COHORT: CohortSummary = {
  id: "cohort_f35d90b18482b2ea",
  definition: "payer in [State Medicaid, Atlas Commercial, Meridian Health]",
  entityGrain: "claim",
  size: 86_415,
  pinned: true,
  originTurn: "turn_69a3e994c1d0",
  originReferent: "D9",
  pinnedWatermarkId: "wm_003",
  detailed: true,
};

function renderHeader(cohort?: CohortSummary) {
  return render(<ContextHeader header={{ ...BASE, ...(cohort ? { cohort } : {}) }} />);
}

describe("ContextHeader — the cohort chip", () => {
  it("labels the chip with the definition, not the hash", () => {
    renderHeader(LIVE_COHORT);
    expect(
      screen.getByText("payer in [State Medicaid, Atlas Commercial, Meridian Health]"),
    ).toBeInTheDocument();
    expect(screen.queryByText("cohort_f35d90b18482b2ea")).not.toBeInTheDocument();
  });

  it("states the size in the population's own grain", () => {
    renderHeader(LIVE_COHORT);
    // Not "86,415 payers": the members are claims, and the payload says so.
    expect(screen.getByText("86,415 claims")).toBeInTheDocument();
  });

  it("keeps the hash reachable as a debugging handle, not as a name", async () => {
    renderHeader(LIVE_COHORT);
    await userEvent.click(screen.getByRole("button", { name: /Cohort/ }));
    expect(await screen.findByText("cohort_f35d90b18482b2ea")).toBeInTheDocument();
    // The origin is a reference underneath, not the headline.
    expect(screen.getByText(/Pinned from D9 in turn_69a3e994c1d0/)).toBeInTheDocument();
  });

  it("explains a cohort pinned without its own window", async () => {
    renderHeader(LIVE_COHORT);
    await userEvent.click(screen.getByRole("button", { name: /Cohort/ }));
    expect(
      await screen.findByText(/covers this population across all time/),
    ).toBeInTheDocument();
  });

  it("says what it has when only the header's id and size were published", async () => {
    // A payload with no definition block must not dress the handle up as
    // one. It says the handle, the size, and that the rule was not given.
    renderHeader({
      id: "coh_9f2a11",
      definition: "coh_9f2a11",
      size: 312,
      pinned: true,
      originTurn: "",
    });
    expect(screen.getByText("coh_9f2a11")).toBeInTheDocument();
    expect(screen.getByText("312 entities")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Cohort/ }));
    expect(await screen.findByText(/not the rule that selected it/)).toBeInTheDocument();
  });

  it("says an unpinned cohort is re-evaluated each turn", async () => {
    renderHeader({ ...LIVE_COHORT, pinned: false });
    await userEvent.click(screen.getByRole("button", { name: /Cohort/ }));
    expect(await screen.findByText(/the members can change/)).toBeInTheDocument();
  });

  it("draws no chip at all when the turn pinned no population", () => {
    renderHeader();
    expect(screen.queryByRole("button", { name: /Cohort/ })).not.toBeInTheDocument();
  });
});
