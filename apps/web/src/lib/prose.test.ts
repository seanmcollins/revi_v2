/**
 * The two mechanical repairs, against the strings that are live on the
 * wire — both read out of the captured fixtures rather than invented, so
 * a repair that stops matching the product's own output fails here.
 */

import { describe, expect, it } from "vitest";

import liveRounds from "@/lib/__fixtures__/live-rounds.json";
import liveTurns from "@/lib/__fixtures__/live-turns.json";
import { readableStatement, scalarizeRankOfOne, tidyProse } from "@/lib/prose";

describe("tidyProse — a stop printed twice where two builders met", () => {
  it("collapses the stacked stop the curated note survived two rewrites of", () => {
    // The INNER stop goes and the outer one stays: the quoted note ends
    // inside a sentence that carries on afterwards, so the stop that
    // belongs to the sentence is the one to keep.
    expect(
      tidyProse(
        "this watch briefs at 1 points and the tile moved 3.6 points (Anything over a point on any payer is worth my morning.). Since you started watching it",
      ),
    ).toBe(
      "this watch briefs at 1 points and the tile moved 3.6 points (Anything over a point on any payer is worth my morning). Since you started watching it",
    );
  });

  it("collapses the maturity guard's doubled stop", () => {
    expect(tidyProse("Ask again once the thinner side matures.. The question's own")).toBe(
      "Ask again once the thinner side matures. The question's own",
    );
  });

  it("leaves an ISO range's operator alone — those dots are not punctuation", () => {
    const iso = "denial rate is 29.5% over 2026-07-01..2026-07-31.";
    expect(tidyProse(iso)).toBe(iso);
  });

  it("leaves an ellipsis whole", () => {
    expect(tidyProse("Reading this watch's settings...")).toBe(
      "Reading this watch's settings...",
    );
  });

  it("returns a clean sentence byte-identical", () => {
    const clean = "State Medicaid MCO ranks #1 of 8 measured by denial rate: 29.5%.";
    expect(tidyProse(clean)).toBe(clean);
  });

  it("repairs the live brief entry that carries it", () => {
    // The `.).` the reviewer measured on /rounds, read out of the capture
    // rather than transcribed: it is in the brief line for the JOC
    // account's watch, where the curated reviewer note is quoted.
    const statement = (liveRounds.brief.entries as { statement?: string }[])
      .map((entry) => entry.statement ?? "")
      .find((text) => text.includes(".)."));
    expect(statement, "the capture must still carry the stacked stop").toBeDefined();
    const repaired = tidyProse(statement as string);
    expect(repaired).not.toContain(".).");
    expect(repaired).toContain("worth my morning). Since you started watching");
    // And the ISO range three clauses later is untouched.
    expect(repaired).toContain("2026-07-01..2026-07-31");
  });
});

describe("scalarizeRankOfOne — a rank over a set of one is not a rank", () => {
  it("rewrites the clause the single-payer tiles publish", () => {
    expect(
      scalarizeRankOfOne(
        "Pinnacle Health Plan ranks #1 of 1 measured by denial rate over 2026-07-01..2026-07-31: 22.9%.",
      ),
    ).toBe(
      "Pinnacle Health Plan is the only cell measured here — denial rate over 2026-07-01..2026-07-31: 22.9%.",
    );
  });

  it("leaves a real rank alone", () => {
    const ranked =
      "Atlas Commercial ranks #1 of 12 measured by denied dollars over 2026-07-01..2026-07-31.";
    expect(scalarizeRankOfOne(ranked)).toBe(ranked);
  });

  it("rewrites the live finding statement that carries it", () => {
    // Read out of the capture rather than transcribed: the drill result
    // the reviewer measured, on a breakdown narrowed to one payer.
    const statement = (JSON.stringify(liveTurns).match(/"([^"]*ranks #1 of 1[^"]*)"/) ?? [])[1];
    expect(statement, "the capture must still carry a #1-of-1 statement").toBeDefined();
    const repaired = scalarizeRankOfOne(statement as string);
    expect(repaired).not.toContain("#1 of 1");
    expect(repaired).toContain("is the only cell measured here —");
    // Every figure the engine wrote survives.
    expect(repaired).toContain("$300.70");
    expect(repaired).toContain("2026-07-27..2026-08-02");
  });

  it("publishes no statement containing the string at all", () => {
    expect(
      readableStatement(
        "Silverline Medicare Advantage ranks #1 of 1 measured by denied dollars over 2026-07-27..2026-08-02: $300.70.",
      ),
    ).not.toContain("#1 of 1");
  });
});
