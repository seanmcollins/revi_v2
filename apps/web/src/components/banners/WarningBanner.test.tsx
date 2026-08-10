/**
 * The five mid-wave titles, against the sentences the engine actually
 * sends.
 *
 * `contract-followups.test.ts` already pins that every code
 * `revi_api/warning_codes.py` can publish has a title here — it parses the
 * server's own module, so a new code fails before it can reach a reader.
 * What that pin cannot check is the OTHER half: that the title reads as a
 * heading over the engine's real sentence rather than over the invented
 * one-liner a fixture usually carries. These five landed mid-wave and had
 * only been seen against invented sentences.
 *
 * The messages below are verbatim. Three are off a live :8000 —
 * `PREMISE_PARTIAL` from `inv_4f6d20d8f14a`, `COMPARISON_PRIOR_UNKNOWN`
 * from `inv_f02d4879463e`, `REFINEMENT_REUSED_PLAN` from a live refinement
 * re-serve on `sess_0c81f27e297d`. The other two are composed from the
 * engine's own format strings (`submit_turn.py`'s
 * `refinement_not_applied:` and `chart_integrity.py`'s
 * `chart_rows_collapsed:`), because a turn that triggers them was not
 * among the stored investigations; the part under test — the machine
 * prefix, which is what the title has to replace — is exact either way.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";

import { WarningBanner } from "@/components/banners/WarningBanner";
import type { WarningEvent } from "@/lib/types";

afterEach(() => cleanup());

interface Live {
  code: string;
  severity: "caution" | "info";
  title: string;
  message: string;
  /** The first words a reader should see after the title. */
  opens: string;
  /** True when a machine literal is taken off the default surface. */
  redacted?: boolean;
  /** What the surface prints where the engine wrote that literal. */
  spelled?: string;
}

const LIVE: Live[] = [
  {
    code: "PREMISE_PARTIAL",
    severity: "caution",
    title: "The premise holds in direction, not in size",
    message:
      "premise_partial: You asked about a doubling in denial rate. It did not double — " +
      "denial rate rose 72.6%, short of the 100.0% a doubling assumes: 7.4% → 12.8% vs prior " +
      "quarter (2026-04-01..2026-05-02, 32d vs 33d, not length-normalized). The direction the " +
      "question assumes is right and the size is not, so nothing below may be described in the " +
      "question's own words for it. What follows is the composition of the movement that did " +
      "happen.",
    opens: "You asked about a doubling in denial rate.",
    // The engine writes the comparison window as an ISO literal, and in
    // the calm layout this sentence is the first thing a VP reads — so
    // the date is spelled the way they would say it, under the same rule
    // and the same guarantee as a plan handle: the surface shows the
    // plain wording, the engine's exact wording is one tap away, and
    // every export carries the message byte for byte.
    redacted: true,
    spelled: "vs prior quarter (Apr 1–May 2, 2026, 32d vs 33d, not length-normalized)",
  },
  {
    code: "COMPARISON_PRIOR_UNKNOWN",
    severity: "caution",
    title: "Some cells have no prior figure to compare",
    message:
      "comparison_prior_unknown: on denial_code_mix__compare, 21 cell(s) in the prior window " +
      "were outside this window's top-N and their current value was never retrieved. Those " +
      "cells publish no prior figure, no movement and no impact — a value this plan did not " +
      "read is UNKNOWN, not zero — and they are excluded from every movement ranking on this " +
      "turn. Ask for the full breakdown to compare them.",
    opens: "on denial_code_mix__compare, 21 cell(s)",
  },
  {
    code: "REFINEMENT_REUSED_PLAN",
    severity: "info",
    title: "The same answer, presented differently",
    message:
      "refinement_reused_plan: this answer re-serves the previous turn's plan " +
      "(00351680c5457c68132b79135ec499e5ac7a4994b57184cf1cbb7da2554ca7ca) — the same evidence, " +
      "the same findings and every caveat that came with them. What changed is the " +
      "presentation only: rank_by.",
    opens: "this answer re-serves the previous turn's plan",
  },
  {
    code: "REFINEMENT_NOT_APPLIED",
    severity: "caution",
    title: "Part of what you asked for was not applied",
    message:
      "refinement_not_applied: what you asked to change does not alter this plan " +
      "(00351680c5457c68132b79135ec499e5ac7a4994b57184cf1cbb7da2554ca7ca) — the operator(s) " +
      "SetGrain left the evidence identical to the previous answer, so these are the same " +
      "numbers re-measured, not a new result. Say what to change about the metric, the cut or " +
      "the window and I will re-run it.",
    opens: "what you asked to change does not alter this plan",
  },
  {
    code: "CHART_ROWS_COLLAPSED",
    severity: "caution",
    title: "This chart's rows aren't uniquely keyed",
    message:
      "chart_rows_collapsed: denial_concentration declared x=month and series=payer over 30 " +
      "rows that share only 3 distinct keys. The rows were summed to the declared grain " +
      "(total 44180798 preserved) rather than letting a renderer keep the last one and drop " +
      "the rest.",
    // BUG 1 — `denial_concentration` is the engine's handle for a plan
    // node, and it was being printed at an analyst inside the sentence
    // that says the chart cannot be keyed. The plain wording is what the
    // banner shows; the exact wording is one tap away on the banner and
    // travels whole in every export.
    opens: "denial concentration declared x=month",
    redacted: true,
  },
];

function event(live: Live): WarningEvent {
  return {
    type: "warning",
    code: live.code,
    severity: live.severity,
    message: live.message,
    structured: true,
  };
}

describe("WarningBanner — the mid-wave titles over the engine's own sentences", () => {
  it.each(LIVE)("titles $code and prints its sentence without the machine prefix", (live) => {
    render(<WarningBanner warning={event(live)} />);

    expect(screen.getByText(live.title)).toBeInTheDocument();

    const banner = screen.getByText(live.title).closest("[data-warning-code]");
    expect(banner).toHaveAttribute("data-warning-code", live.code);
    expect(banner).toHaveAttribute("data-severity", live.severity);

    // The prefix IS the code, so it goes — the title says the same thing
    // in the reader's words, and printing both is a stutter. Everything
    // after it is the engine's, verbatim.
    const body = banner?.textContent ?? "";
    expect(body).not.toContain(`${live.code.toLowerCase()}:`);
    expect(body).toContain(live.opens);
    if (live.spelled) expect(body).toContain(live.spelled);
    if (live.redacted !== true) {
      expect(body).toContain(live.message.slice(live.message.indexOf(": ") + 2));
    }
  });

  it("keeps the engine's exact wording one tap away when a handle came off", async () => {
    const live = LIVE.find((entry) => entry.code === "CHART_ROWS_COLLAPSED");
    expect(live, "one live fixture must carry an engine handle").toBeDefined();
    render(<WarningBanner warning={event(live!)} />);

    // Not on the face of it.
    expect(screen.queryByText(/denial_concentration/)).not.toBeInTheDocument();

    // NOTHING IS DELETED, ONLY RELOCATED.
    await userEvent.click(
      screen.getByRole("button", { name: "Show the engine's exact wording" }),
    );
    expect(
      screen.getByText(live!.message.slice(live!.message.indexOf(": ") + 2)),
    ).toBeInTheDocument();
  });

  it("shows the §12 code only in debug, never beside the analyst's sentence", () => {
    const live = LIVE[0];
    const { rerender } = render(<WarningBanner warning={event(live)} />);
    expect(screen.queryByText(live.code)).not.toBeInTheDocument();

    rerender(<WarningBanner warning={event(live)} debug />);
    expect(screen.getByText(live.code)).toBeInTheDocument();
  });
});
