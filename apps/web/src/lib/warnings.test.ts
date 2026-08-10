/**
 * BUG 1 — no internal identifiers on a default surface, and nothing
 * deleted to get there.
 *
 * The live sentence this is about, from `inv_94574336232a`:
 *
 *   "probe_families_empty: 8 metric famil(ies) on this plan were read and
 *    produced no published finding, so nothing above speaks for them:
 *    denial_rate (portfolio_denial_trend, 1 row(s)); cash_posted
 *    (portfolio_cash_trend, 1 row(s)); …"
 *
 * Eight plan-node ids and eight row counts, inside the one caution that
 * says which parts of the question went unanswered.
 */

import { describe, expect, it } from "vitest";

import { foldComposedDisclosures, partitionWarnings, publicWarningBody } from "@/lib/warnings";

const PROBE_FAMILIES_EMPTY =
  "probe_families_empty: 8 metric famil(ies) on this plan were read and produced no " +
  "published finding, so nothing above speaks for them: denial_rate " +
  "(portfolio_denial_trend, 1 row(s)); cash_posted (portfolio_cash_trend, 1 row(s)); " +
  "underpayment_variance (portfolio_underpayment, 12 row(s)); dnfb_dollars " +
  "(portfolio_unbilled, 6 row(s)); ar_over_90_pct (portfolio_ar_health, 12 row(s)). " +
  "The findings rank within the families that did publish.";

describe("publicWarningBody — the plan's handles come off the answer", () => {
  const body = publicWarningBody("PROBE_FAMILIES_EMPTY", PROBE_FAMILIES_EMPTY);

  it("drops the plan-node census the reader cannot act on", () => {
    expect(body.text).not.toContain("portfolio_denial_trend");
    expect(body.text).not.toContain("row(s)");
    expect(body.redacted).toBe(true);
  });

  it("spells the measures the way the rest of the product spells them", () => {
    expect(body.text).toContain("denial rate");
    expect(body.text).toContain("cash posted");
    expect(body.text).toContain("DNFB dollars");
    expect(body.text).toContain("AR over 90 %");
    expect(body.text).not.toContain("denial_rate");
  });

  it("keeps every sentence the engine wrote", () => {
    expect(body.text).toContain("8 metric famil(ies) on this plan were read");
    expect(body.text).toContain("The findings rank within the families that did publish.");
  });

  it("keeps the engine's exact wording, so the surface can offer it", () => {
    // NOTHING IS DELETED, ONLY RELOCATED. Every export reads
    // `warning.message` directly and is untouched; this is the copy a
    // reader can open on the row itself.
    expect(body.verbatim).toContain("portfolio_denial_trend, 1 row(s)");
  });

  it("strips the machine prefix that IS the code, and only that", () => {
    expect(body.text.startsWith("8 metric")).toBe(true);
  });
});

describe("publicWarningBody — what it refuses to touch", () => {
  it("leaves a handle it cannot prove is a measure name", () => {
    // "wm 003" is not a friendlier spelling of a data load's id, it is a
    // broken one. Same for a plan node with a hash in it.
    const load = publicWarningBody(
      "UNCLASSIFIED",
      "results were reused from data load wm_003 at plan inv_0899f9defc32",
    );
    expect(load.text).toContain("wm_003");
    expect(load.text).toContain("inv_0899f9defc32");
    expect(load.redacted).toBe(false);
  });

  it("leaves a probe reference exactly as the deduper wrote it", () => {
    const alt = publicWarningBody(
      "ALTERNATE_BASIS_USED",
      "alternate_basis_used: probe 'main__window__prior' reads 'denied dollars'",
    );
    expect(alt.text).toContain("main__window__prior");
  });

  it("returns an ordinary sentence byte-identical", () => {
    const message = "window_assumed: the question named no period, so I used the last full month.";
    const body = publicWarningBody("WINDOW_ASSUMED", message);
    expect(body.redacted).toBe(false);
    expect(body.text).toBe("the question named no period, so I used the last full month.");
  });

  it("spells a quoted measure name inside an ordinary caution", () => {
    const body = publicWarningBody(
      "ALTERNATE_BASIS_USED",
      "alternate_basis_used: 'denial_rate' is computed on the 'service' basis",
    );
    expect(body.text).toBe("'denial rate' is computed on the 'service' basis");
    // 'service' is a word, not a handle — re-spelling it would be this
    // function inventing a correction where there is no defect.
    expect(body.text).toContain("'service'");
  });
});

describe("partitionWarnings — the verdict is never one of the others", () => {
  const warnings = [
    { code: "SUPPRESSION_APPLIED", severity: "info" },
    { code: "POPULATION_CAVEAT", severity: "caution" },
    { code: "RANKING_REFUSED", severity: "caution" },
    { code: "ALTERNATE_BASIS_USED", severity: "caution" },
    { code: "PREMISE_PARTIAL", severity: "caution" },
  ];

  it("pulls every verdict code out, in the engine's own order", () => {
    const { verdicts, rest } = partitionWarnings(warnings);
    expect(verdicts.map((w) => w.code)).toEqual(["RANKING_REFUSED", "PREMISE_PARTIAL"]);
    expect(rest.map((w) => w.code)).toEqual([
      "POPULATION_CAVEAT",
      "ALTERNATE_BASIS_USED",
      "SUPPRESSION_APPLIED",
    ]);
  });

  it("loses nothing across the two bands", () => {
    const { verdicts, rest } = partitionWarnings(warnings);
    expect(verdicts.length + rest.length).toBe(warnings.length);
  });

  it("puts the cautions before the notes inside the rest", () => {
    const { rest } = partitionWarnings(warnings);
    const firstNote = rest.findIndex((w) => w.severity !== "caution");
    expect(rest.slice(0, firstNote).every((w) => w.severity === "caution")).toBe(true);
  });
});

/**
 * ROUND-9 P0 — the fold deleted the answer.
 *
 * On a provisional window the composer opens the write-up with the SETTLED
 * reading, and the engine publishes the same paragraph as the body of
 * `ADJUDICATION_INCOMPLETE`. Byte-identical, so this function dropped it:
 * live, the default layout's first screen carried the provisional 12.8%
 * three times and the settled 9.1% zero times, the latter reachable only
 * by opening a disclosure that is collapsed by default.
 *
 * The sentences below are the shape of that live turn, shortened.
 */
describe("foldComposedDisclosures — the lead sentence is not a caution", () => {
  const SETTLED =
    "Through June 2026 — the last period that has finished settling — denial rate reads 9.1%.";
  const REST =
    "July 2026 is 26.3% settled, so the 12.8% it reports is provisional and will move.";
  const WARNING = {
    code: "ADJUDICATION_INCOMPLETE",
    message: `adjudication_incomplete: ${SETTLED} ${REST}`,
  };

  it("keeps the settled figure in the prose that is actually rendered", () => {
    const { text } = foldComposedDisclosures(
      `${SETTLED} ${REST} Two payers account for most of the movement.`,
      [WARNING],
    );
    // The assertion the review asked for by name: the FOLDED prose, not
    // the narrative the server sent.
    expect(text).toContain("9.1%");
    expect(text).toContain(SETTLED);
  });

  it("still folds the caution sentences that follow it", () => {
    const { text, folded } = foldComposedDisclosures(
      `${SETTLED} ${REST} Two payers account for most of the movement.`,
      [WARNING],
    );
    expect(text).not.toContain(REST);
    expect(text).toContain("Two payers account for most of the movement.");
    expect(folded).toBe(1);
  });

  it("exempts the lead by POSITION, not by wording", () => {
    // The same sentence, further down, is an ordinary repeat of a banner
    // and folds — otherwise "never fold this string" would leak into
    // every paragraph that happens to open the same way.
    const { text } = foldComposedDisclosures(
      `Denial rate is concentrated in the tail.\n\n${REST}`,
      [WARNING],
    );
    expect(text).toBe("Denial rate is concentrated in the tail.");
  });

  it("leaves a narrative that shares nothing with a banner byte-identical", () => {
    const narrative = "Denial rate is concentrated in the tail. Two payers drive it.";
    expect(foldComposedDisclosures(narrative, [WARNING])).toEqual({
      text: narrative,
      folded: 0,
    });
  });
});
