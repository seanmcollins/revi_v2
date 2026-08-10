/**
 * The two mechanical repairs, against the strings that are live on the
 * wire — both read out of the captured fixtures rather than invented, so
 * a repair that stops matching the product's own output fails here.
 */

import { describe, expect, it } from "vitest";

import liveMonitors from "@/lib/__fixtures__/live-monitors.json";
import liveTurns from "@/lib/__fixtures__/live-turns.json";
import {
  capitalizeOpening,
  humanizeLoadHandles,
  readableStatement,
  scalarizeRankOfOne,
  tidyProse,
} from "@/lib/prose";

describe("tidyProse — a stop printed twice where two builders met", () => {
  it("collapses the stacked stop the curated note survived two rewrites of", () => {
    // The INNER stop goes and the outer one stays: the quoted note ends
    // inside a sentence that carries on afterwards, so the stop that
    // belongs to the sentence is the one to keep.
    expect(
      tidyProse(
        "this monitor briefs at 1 points and the tile moved 3.6 points (Anything over a point on any payer is worth my morning.). Since you started monitoring it",
      ),
    ).toBe(
      "this monitor briefs at 1 points and the tile moved 3.6 points (Anything over a point on any payer is worth my morning). Since you started monitoring it",
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
    expect(tidyProse("Reading this monitor's settings...")).toBe(
      "Reading this monitor's settings...",
    );
  });

  it("returns a clean sentence byte-identical", () => {
    const clean = "State Medicaid MCO ranks #1 of 8 measured by denial rate: 29.5%.";
    expect(tidyProse(clean)).toBe(clean);
  });

  it("repairs the live brief entry that carries it", () => {
    // The `.).` the reviewer measured on /monitors, read out of the capture
    // rather than transcribed: it is in the brief line for the JOC
    // account's monitor, where the curated reviewer note is quoted.
    const statement = (liveMonitors.brief.entries as { statement?: string }[])
      .map((entry) => entry.statement ?? "")
      .find((text) => text.includes(".)."));
    expect(statement, "the capture must still carry the stacked stop").toBeDefined();
    const repaired = tidyProse(statement as string);
    expect(repaired).not.toContain(".).");
    expect(repaired).toContain("worth my morning). Since you started monitoring");
    // And the ISO range three clauses later is untouched.
    expect(repaired).toContain("2026-07-01..2026-07-31");
  });
});

describe("scalarizeRankOfOne — a rank over a set of one is not a rank", () => {
  it("rewrites the clause the single-payer tiles publish", () => {
    expect(
      scalarizeRankOfOne(
        "Ashvale Health Plan ranks #1 of 1 measured by denial rate over 2026-07-01..2026-07-31: 22.9%.",
      ),
    ).toBe(
      "Ashvale Health Plan is the only cell measured here — denial rate over 2026-07-01..2026-07-31: 22.9%.",
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

describe("humanizeLoadHandles — a database id in front of an analyst", () => {
  it("rewrites the live worklist sentence that carries the handle", () => {
    expect(
      humanizeLoadHandles(
        "8 of 33 ranked cards at watermark wm_003, highest governed priority first.",
      ),
    ).toBe("8 of 33 ranked cards at this data load, highest governed priority first.");
  });

  it("takes the handle with the phrase that introduces it, either spelling", () => {
    expect(humanizeLoadHandles("results were reused from data load wm_003")).toBe(
      "results were reused from this data load",
    );
  });

  it("leaves a bare handle alone rather than guessing what it names", () => {
    // A sentence whose subject IS the id has nothing to fall back on, and
    // an id is never re-spelled or word-split (`publicWarningBody` pins
    // that "wm 003" is a broken spelling, not a friendlier one).
    const bare = "wm_003 and wm_004 disagree about the newest data date.";
    expect(humanizeLoadHandles(bare)).toBe(bare);
  });

  it("touches nothing else in a sentence that has no handle", () => {
    const clean = "8 of 33 ranked cards, highest governed priority first.";
    expect(humanizeLoadHandles(clean)).toBe(clean);
  });

  it("publishes no wm_ handle from the live worklist statement", () => {
    const statement = (JSON.stringify(liveTurns).match(/"([^"]*at watermark wm_[^"]*)"/) ?? [])[1];
    if (statement === undefined) return; // capture carries none — nothing to prove
    expect(humanizeLoadHandles(statement)).not.toMatch(/wm_\d+/);
  });
});

describe("capitalizeOpening — a composed sentence that starts in lower case", () => {
  it("capitalizes the opening word of a tile statement", () => {
    expect(
      capitalizeOpening("denial rate is ≤ 76.9% over 2026-07-01..2026-07-31 — an UPPER BOUND"),
    ).toBe("Denial rate is ≤ 76.9% over 2026-07-01..2026-07-31 — an UPPER BOUND");
  });

  it("leaves an identifier alone — a capitalized id is a wrong id", () => {
    const id = "denial_rate is the governed name for this measure.";
    expect(capitalizeOpening(id)).toBe(id);
  });

  it("returns anything already capitalized byte-identical", () => {
    for (const text of [
      "Atlas Commercial ranks #1 of 12 measured by days in A/R.",
      "$176,112.25 is the published figure.",
      "≤ 76.9% — an upper bound.",
      "",
      "   ",
    ]) {
      expect(capitalizeOpening(text)).toBe(text);
    }
  });

  it("skips leading whitespace rather than giving up on the sentence", () => {
    expect(capitalizeOpening("  the prior load produced no value.")).toBe(
      "  The prior load produced no value.",
    );
  });

  it("runs last in readableStatement, on whatever ends up first", () => {
    // The rank repair rewrites the opening clause; the capital lands on
    // the result of that rewrite, not on the original text.
    expect(
      readableStatement("veritas Comp Fund ranks #1 of 1 measured by denial rate: 22.9%."),
    ).toBe("Veritas Comp Fund is the only cell measured here — denial rate: 22.9%.");
  });
});
