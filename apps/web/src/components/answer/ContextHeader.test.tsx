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

/**
 * The scope chip after the auto-correction fix (FN-9).
 *
 * A turn asking for "lakewood medicaid mco" runs against the payer that
 * exists, `Lakewood Medicaid MCO`, and says so in a VALUE_CORRECTED
 * caution. The chip used to render the string the user typed — so the one
 * surface whose entire job is to state which population ran named a payer
 * this warehouse has never held, two rows above the caution saying so.
 */
describe("ContextHeader — the scope chip states the predicate that ran", () => {
  const CORRECTED = {
    dimension: "payer",
    dimensionLabel: "payer",
    op: "eq" as const,
    values: ["Lakewood Medicaid MCO"],
    requestedValues: ["lakewood medicaid mco"],
    originTurn: "turn_b5dbdb9ad05e",
  };

  it("shows the corrected value on the chip, never the uncorrected one", () => {
    render(<ContextHeader header={{ ...BASE, filters: [CORRECTED] }} />);
    expect(screen.getByText("payer: Lakewood Medicaid MCO")).toBeInTheDocument();
    expect(screen.queryByText(/payer: lakewood medicaid mco/)).not.toBeInTheDocument();
  });

  it("keeps what the user typed, one level in, as history", async () => {
    render(<ContextHeader header={{ ...BASE, filters: [CORRECTED] }} />);
    await userEvent.click(screen.getByRole("button", { name: /Scope/ }));
    expect(await screen.findByText(/you typed “lakewood medicaid mco”/)).toBeInTheDocument();
  });

  it("says nothing about typing when nothing was corrected", async () => {
    render(
      <ContextHeader
        header={{
          ...BASE,
          // The older payload generation: values only, no requested_values.
          filters: [{ ...CORRECTED, requestedValues: undefined }],
        }}
      />,
    );
    expect(screen.getByText("payer: Lakewood Medicaid MCO")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Scope/ }));
    expect(await screen.findByText(/from turn_b5dbdb9ad05e/)).toBeInTheDocument();
    expect(screen.queryByText(/you typed/)).not.toBeInTheDocument();
  });
});

/**
 * A SNAPSHOT contract states a balance AT a moment.
 *
 * Live: "Show me percent of A/R over 90 days by payer" answers as of
 * 2026-08-02 and publishes `as_of: "2026-08-02"` — while STILL carrying
 * `window_start: 2026-07-01, window_end: 2026-07-31` on the same payload.
 * Rendering that range is how the header, the caution and the prose came
 * to describe a July scope the calculation never applied, over an all-time
 * aging figure. When `as_of` is present the chip states it and nothing
 * else: a range the number does not honour is not a smaller error than no
 * range at all.
 */
describe("ContextHeader — snapshot metrics (as_of)", () => {
  const SNAPSHOT: ContextHeaderData = {
    ...BASE,
    window: { start: "2026-07-01", end: "2026-07-31", basis: "service" },
    asOf: "2026-08-02",
  };

  it("states the as-of date instead of the window the payload also carries", () => {
    render(<ContextHeader header={SNAPSHOT} />);
    expect(screen.getByText("Aug 2, 2026 (service date)")).toBeInTheDocument();
    expect(screen.getByText("As of")).toBeInTheDocument();
    expect(screen.queryByText(/Jul 1–Jul 31/)).not.toBeInTheDocument();
    expect(screen.queryByText("Window")).not.toBeInTheDocument();
  });

  it("says in words that no start–end window applies", async () => {
    render(<ContextHeader header={SNAPSHOT} />);
    await userEvent.click(screen.getByRole("button", { name: /As of/ }));
    expect(
      await screen.findByText(/a level at that moment, not a total accumulated over a period/),
    ).toBeInTheDocument();
    expect(screen.getByText(/No start–end window applies to this number/)).toBeInTheDocument();
  });

  it("renders the window exactly as before for a flow metric", () => {
    render(<ContextHeader header={BASE} />);
    expect(screen.getByText("Window")).toBeInTheDocument();
    expect(screen.getByText("Jun 1–Jul 31 (service date)")).toBeInTheDocument();
  });
});

/**
 * A turn rebuilt from the store, and the difference between "this turn had
 * no context" and "this turn's context was not kept".
 */
describe("ContextHeader — a restored turn", () => {
  it("marks the header as restored and says what a restore keeps", async () => {
    render(<ContextHeader header={{ ...BASE, restored: true }} />);
    const chip = screen.getByRole("button", { name: /Restored/ });
    await userEvent.click(chip);
    expect(
      await screen.findByText(/rebuilt from the record the server kept for it/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/anything missing below is missing because it was not stored/),
    ).toBeInTheDocument();
  });

  it("says nothing about restoration on a live turn", () => {
    render(<ContextHeader header={BASE} />);
    expect(screen.queryByRole("button", { name: /Restored/ })).not.toBeInTheDocument();
  });

  /**
   * `InvestigationResponse.restoration_notes`, verbatim from a live
   * `GET /v1/investigations/{iid}`. The server states two things this
   * component cannot know: that the window, scope, cohort and watermark
   * were rebuilt from the turn's stored spec rather than re-computed, and
   * that the narrative trace keeps its template, redactions and length but
   * not its sentences — which is a far more precise account of the limit
   * than the static copy's "the write-up was not stored".
   */
  it("prefers the server's own account of the restore over the static copy", async () => {
    const notes = [
      "Restored context: the window, scope, cohort and watermark below are rebuilt from this turn's stored investigation spec at watermark wm_003, not re-computed — the figures are the ones this turn published when it ran.",
      "The composed narrative is not stored anywhere — the narrative trace keeps its template, redactions and length, not its sentences — so this turn restores without prose. Its findings, warnings and charts are its own record.",
    ];
    render(<ContextHeader header={{ ...BASE, restored: true, restorationNotes: notes }} />);
    await userEvent.click(screen.getByRole("button", { name: /Restored/ }));

    expect(await screen.findByText(/not re-computed/)).toBeInTheDocument();
    expect(
      screen.getByText(/keeps its template, redactions and length, not its sentences/),
    ).toBeInTheDocument();
    // The client's approximation gives way rather than being printed
    // beside the server's more precise sentence.
    expect(
      screen.queryByText(/rebuilt from the record the server kept for it/),
    ).not.toBeInTheDocument();
  });

  it("keeps explaining itself when the server published no notes", async () => {
    render(<ContextHeader header={{ ...BASE, restored: true, restorationNotes: [] }} />);
    await userEvent.click(screen.getByRole("button", { name: /Restored/ }));
    expect(
      await screen.findByText(/rebuilt from the record the server kept for it/),
    ).toBeInTheDocument();
  });
});
