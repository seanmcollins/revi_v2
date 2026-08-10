/**
 * ONE MEASURE, ONE SPELLING.
 *
 * `days_in_ar` reached a single monitor card wearing three spellings at
 * once: "days in A/R by payer" on the label (the pack's own words),
 * "Days in ar" in the settings panel headed "What this monitor measures",
 * and "days in ar" inside the tile's own statement. A reader who is being
 * asked to trust a number cannot be shown three names for it on one card.
 *
 * This file pins the spelling table and the two things that were losing
 * it: a blanket `.toLowerCase()` over an already-humanized phrase, and a
 * phrase that arrived from a humanizer somewhere else with the case
 * already gone.
 */

import { describe, expect, it } from "vitest";

import { humanizeColumn, humanizeInline, respellInitialisms } from "@/lib/humanize";

describe("humanizeColumn — the pack's own initialisms, at the head of a title", () => {
  it("spells A/R the way the metric pack spells it, not as a bare AR", () => {
    // The convention every authored surface already follows: the server
    // publishes the monitor label "days in A/R by payer" and the benchmark
    // "percent of A/R aged over 90 days".
    expect(humanizeColumn("days_in_ar")).toBe("Days in A/R");
    expect(humanizeColumn("ar_over_90_pct")).toBe("A/R over 90 %");
    expect(humanizeColumn("ar_balance")).toBe("A/R balance");
  });

  it("keeps the other initialisms it already knew", () => {
    expect(humanizeColumn("carc_code")).toBe("CARC code");
    expect(humanizeColumn("dnfb_dollars")).toBe("DNFB dollars");
    expect(humanizeColumn("denial_rate")).toBe("Denial rate");
  });
});

describe("humanizeInline — mid-sentence, and still not a typo of the pack", () => {
  /**
   * The defect this replaces: `humanizeColumn(id).toLowerCase()`. The
   * lower-casing is for the FIRST word of a phrase that is no longer
   * starting a title; applied to the whole string it also un-spells every
   * initialism the humanizer just got right.
   */
  it("lower-cases the opening word only, never the initialisms", () => {
    expect(humanizeInline("days_in_ar")).toBe("days in A/R");
    expect(humanizeInline("denial_rate")).toBe("denial rate");
    expect(humanizeInline("primary_carc")).toBe("primary CARC");
  });

  it("leaves a phrase that OPENS on an initialism capitalized", () => {
    expect(humanizeInline("ar_over_90_pct")).toBe("A/R over 90 %");
  });
});

describe("respellInitialisms — a phrase that lost its case somewhere else", () => {
  /**
   * `MonitorsPin.spec_summary`, composed in
   * `apps/api/src/revi_api/monitors/pins.py::_spec_summary` from
   * `metric_display.name_for(id) or metric_label(id)`. For a metric the
   * pack publishes no display name for, the fallback splits the id on
   * underscores and knows nothing about initialisms.
   */
  it("repairs the server's own de-cased summary", () => {
    expect(
      respellInitialisms("Days in ar, broken down by payer — the last full month"),
    ).toBe("Days in A/R, broken down by payer — the last full month");
  });

  it("repairs it mid-sentence too, in a monitor's headline statement", () => {
    expect(
      respellInitialisms(
        "Atlas Commercial ranks #1 of 12 measured by days in ar as of 2026-08-02: 179.5 days.",
      ),
    ).toBe(
      "Atlas Commercial ranks #1 of 12 measured by days in A/R as of 2026-08-02: 179.5 days.",
    );
  });

  it("touches whole words only — a payer's name is not a spelling mistake", () => {
    // Nothing in these is the WORD "ar": substrings are not matches, and a
    // word the table has never heard of comes back untouched.
    for (const text of [
      "Ashvale Health Plan — arbitration backlog",
      "Argus Managed Care",
      "the arrears report",
    ]) {
      expect(respellInitialisms(text)).toBe(text);
    }
  });

  it("comes back byte-identical when there is nothing to repair", () => {
    const clean = "Denial rate, broken down by payer — the last full month.";
    expect(respellInitialisms(clean)).toBe(clean);
    expect(respellInitialisms("days in A/R by payer")).toBe("days in A/R by payer");
  });

  it("never rewrites 'pct' — that is a translation, not a case repair", () => {
    expect(respellInitialisms("the pct column")).toBe("the pct column");
  });
});
