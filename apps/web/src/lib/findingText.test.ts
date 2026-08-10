/**
 * BUG 6 — a finding does not print its own title twice.
 *
 * The pairs below are live, off `inv_edfe29ddef8f` (a refused ranking)
 * and `inv_94574336232a` (the worklist answer). The first restates its
 * title and adds one window clause; the second says something new in its
 * first word, and must come back untouched.
 */

import { describe, expect, it } from "vitest";

import { statementBeyondTitle, titleCarriesValue } from "@/lib/findingText";

describe("statementBeyondTitle — the duplicate clause is not printed twice", () => {
  it("drops a leading sentence that is the title plus a window", () => {
    const title = "Ashvale HMO: 47.2% denial rate";
    const statement =
      "Ashvale HMO: 47.2% denial rate over 2026-07-01..2026-07-31. No position is claimed " +
      "for it — too much of this population carries suppressed numerators for an order to " +
      "mean anything.";
    expect(statementBeyondTitle(title, statement)).toBe(
      "No position is claimed for it — too much of this population carries suppressed " +
        "numerators for an order to mean anything.",
    );
  });

  it("keeps a statement that says something the title does not", () => {
    // "ranks #1 of 12 measured" is the fact the card exists to carry.
    const title = "Atlas Commercial: $33,954.90 denied dollars";
    const statement =
      "Atlas Commercial ranks #1 of 12 measured by denied dollars over " +
      "2026-07-01..2026-07-31: $33,954.90.";
    expect(statementBeyondTitle(title, statement)).toBe(statement);
  });

  it("keeps a movement statement whole", () => {
    const title =
      "Summit Peak Medicare Advantage denied dollars up $98,222.19 vs prior period";
    const statement =
      "Summit Peak Medicare Advantage: denied dollars moved from $77,890.06 to $176,112.25.";
    expect(statementBeyondTitle(title, statement)).toBe(statement);
  });

  it("returns nothing when the statement was only the title said again", () => {
    expect(statementBeyondTitle("Ashvale HMO: 47.2%", "Ashvale HMO: 47.2%.")).toBe("");
  });

  it("sees through a label the engine put in front of the title", () => {
    // Live, on `inv_9435ccf2bbef`: the title is "Premise partly
    // supported: " plus the statement's first two sentences verbatim, so
    // the card printed a 180-character clause twice — once as a heading
    // and once as the paragraph under it.
    const title =
      "Premise partly supported: You asked about a doubling in denial rate. It did not " +
      "double — denial rate rose 11.5%, short of the 100.0% a doubling assumes";
    const statement =
      "You asked about a doubling in denial rate. It did not double — denial rate rose " +
      "11.5%, short of the 100.0% a doubling assumes. The movement is real and it is not " +
      "the movement the question names.";
    expect(statementBeyondTitle(title, statement)).toBe(
      "The movement is real and it is not the movement the question names.",
    );
  });

  it("keeps a statement that only begins like the title", () => {
    // The statement runs out before the title does, which means it is a
    // fragment of the heading and not a repetition of it.
    const title = "Atlas Commercial: $33,954.90 denied dollars over the whole window";
    const statement = "Atlas Commercial: $33,954.90 denied";
    expect(statementBeyondTitle(title, statement)).toBe(statement);
  });

  it("never drops a sentence carrying a qualification it cannot recognize", () => {
    const title = "Veritas Comp Fund: ≤ 76.9% denial rate";
    const statement =
      "Veritas Comp Fund: ≤ 76.9% denial rate over a population of 13. This is a ceiling.";
    // "over a population of 13" is not a bare window clause, so the whole
    // statement stands — keeping a redundant clause costs far less than
    // dropping one that carried a bound.
    expect(statementBeyondTitle(title, statement)).toBe(statement);
  });
});

describe("titleCarriesValue — the figure is not printed twice either", () => {
  it("sees the figure the engine built into the title", () => {
    expect(titleCarriesValue("Atlas Commercial: $33,954.90 denied dollars", "$33,954.90")).toBe(
      true,
    );
  });

  it("says no when the title carries a different figure", () => {
    expect(titleCarriesValue("Atlas Commercial: $33,954.90 denied dollars", "+$1,204.00")).toBe(
      false,
    );
  });

  it("says no when there is no figure at all", () => {
    expect(titleCarriesValue("Atlas Commercial", undefined)).toBe(false);
  });
});
