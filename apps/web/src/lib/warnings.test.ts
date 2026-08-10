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

import {
  foldComposedDisclosures,
  isLoudCode,
  isVerdictCode,
  LOUD_CODES,
  partitionWarnings,
  publicWarningBody,
  WARNING_TITLES,
} from "@/lib/warnings";

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
    // `A/R`, not `AR`. The pack's own spelling — the monitor labelled
    // "days in A/R by payer", the benchmark "percent of A/R aged over 90
    // days" — and this sentence is read beside both.
    expect(body.text).toContain("A/R over 90 %");
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

/* ------------------------------------------------------------------ */
/* Marks on the data, notes below it, warnings only for verdicts       */
/* ------------------------------------------------------------------ */

/**
 * THE REGISTER LIST, pinned.
 *
 * The whole point of `isLoudCode` is that the split is ONE reviewable list
 * rather than a ternary in each of eleven components, so the list itself is
 * what this asserts — a code added to it is a deliberate decision somebody
 * made, and a code that drifts onto it fails here.
 */
describe("isLoudCode — which codes may wear the warning register", () => {
  it("is exactly the verdicts, the refusals and a corrected figure", () => {
    expect([...LOUD_CODES].sort()).toEqual([
      "BOUNDED_CELLS_UNRANKED",
      "DIRECTION_UNMATCHED",
      "PREMISE_FALSE",
      "PREMISE_PARTIAL",
      "PREMISE_UNVERIFIABLE",
      "RANKING_REFUSED",
      "VALUE_CORRECTED",
    ]);
  });

  it("renders every ordinary caveat quiet, whatever its severity", () => {
    // Each of these is a `caution` on the wire — the severity that used to
    // decide the ink. None of them is a finding against the answer.
    for (const code of [
      "WINDOW_ASSUMED",
      "ALTERNATE_BASIS_USED",
      "POPULATION_CAVEAT",
      "ADJUDICATION_INCOMPLETE",
      "SUPPRESSION_APPLIED",
      "SUPPRESSION_BOUNDED",
      "COMPARISON_ASSUMED",
      "COMPARISON_PRIOR_UNKNOWN",
      "CHART_ROWS_COLLAPSED",
      "RESULT_TRUNCATED",
      "FINDINGS_TRUNCATED",
      "COHORT_WINDOW_DROPPED",
      "PROBE_FAMILIES_EMPTY",
    ]) {
      expect(isLoudCode(code), `${code} must render quiet`).toBe(false);
    }
  });

  it("keeps a VERIFIED premise seated with the verdicts and out of the amber", () => {
    // The one premise outcome that is good news. It leads the answer like
    // any other verdict and does not shout, because "the premise you
    // assumed is correct" in the same ink as "your premise is false"
    // teaches a reader that the colour means nothing.
    expect(isVerdictCode("PREMISE_VERIFIED")).toBe(true);
    expect(isLoudCode("PREMISE_VERIFIED")).toBe(false);
  });

  it("makes a refusal loud even where it is not seated as a verdict", () => {
    // Ceilings presented in axis order read as a league table unless the
    // refusal is loud enough to stop the reader.
    expect(isLoudCode("BOUNDED_CELLS_UNRANKED")).toBe(true);
    expect(isVerdictCode("BOUNDED_CELLS_UNRANKED")).toBe(false);
  });

  it("names a code the reader can actually read", () => {
    // A loud row is the one row on the surface a reader must not skim, so
    // every one of them has a plain-language title over the engine's
    // sentence rather than the code prettified.
    for (const code of LOUD_CODES) {
      expect(WARNING_TITLES[code], `${code} needs a title`).toBeTruthy();
    }
  });

  it("says nothing about an unknown code", () => {
    expect(isLoudCode("UNCLASSIFIED")).toBe(false);
    expect(isLoudCode("SOMETHING_THE_SERVER_ADDED_TODAY")).toBe(false);
  });
});

/**
 * THE FOLD NEVER LEAVES A FRAGMENT.
 *
 * Live, on the flagship answer, the end of the write-up read
 *
 *   "…govern how far these figures can be generalized.spread statements on
 *    this answer describe the published slice, not the full population."
 *
 * — a welded stop and a sentence starting mid-phrase, at the bottom of the
 * main prose. The composer appends the `FINDINGS_TRUNCATED` disclosure to
 * the clause before it with no space, and `sentencesOf` splits on a stop
 * FOLLOWED BY SPACE, so the disclosure was never a sentence this function
 * could see: it matched no banner, was printed a second time, and printed
 * welded.
 */
describe("foldComposedDisclosures — whole sentences, and a repaired join", () => {
  const TRUNCATED =
    "findings_truncated: 6 of 65 computed cells are published as findings; the remaining 59 " +
    "are in the chart and the evidence frame but carry no finding. Superlatives and spread " +
    "statements on this answer describe the published slice, not the full population.";
  const warnings = [{ code: "FINDINGS_TRUNCATED", message: TRUNCATED }];

  it("folds a disclosure the composer welded onto the clause before it", () => {
    const narrative =
      "Cash posted fell $193,525.79 last week. The caution-severity notes published above " +
      "this text govern how far these figures can be generalized.Superlatives and spread " +
      "statements on this answer describe the published slice, not the full population.";
    const { text, folded } = foldComposedDisclosures(narrative, warnings);
    expect(folded).toBe(1);
    expect(text.endsWith("can be generalized.")).toBe(true);
    expect(text).not.toContain("Superlatives");
  });

  it("leaves no sentence welded onto a terminal stop", () => {
    const narrative =
      "Cash posted fell $193,525.79 last week.The three payers below account for most of it.";
    const { text } = foldComposedDisclosures(narrative, warnings);
    expect(/[.!?][A-Za-z]/.test(text)).toBe(false);
    expect(text).toContain("last week. The three payers");
  });

  it("drops NOTHING when a sentence only partially matches a warning", () => {
    // The rule the mangled line was mistaken for: a fold removes whole
    // sentences or nothing. It never cuts a sentence in half and splices
    // the tail back onto the previous one.
    const narrative =
      "Cash posted fell $193,525.79 last week. Superlatives and spread statements on this " +
      "answer describe the published slice, not the full population, so the three payers " +
      "named here are not necessarily the largest three.";
    const { text, folded } = foldComposedDisclosures(narrative, warnings);
    expect(folded).toBe(0);
    expect(text).toContain("not necessarily the largest three");
    // No fragment welded onto a stop, and no sentence left starting
    // mid-phrase.
    expect(/[.!?][a-z]/.test(text)).toBe(false);
    expect(/[.!?]\s+[a-z]/.test(text)).toBe(false);
  });

  it("counts exactly what it removed", () => {
    const narrative =
      "Cash posted fell $193,525.79 last week. Superlatives and spread statements on this " +
      "answer describe the published slice, not the full population. The three payers below " +
      "account for most of the move.";
    const before = narrative.split(/(?<=[.!?])\s+/).length;
    const { text, folded } = foldComposedDisclosures(narrative, warnings);
    expect(folded).toBe(1);
    expect(text.split(/(?<=[.!?])\s+/).length).toBe(before - folded);
  });

  it("does not separate a dot that is not a sentence boundary", () => {
    // "export.csv" is not two sentences and a rule that split it would be
    // inventing a defect. Neither is an ISO range or a decimal.
    const narrative =
      "Cash posted fell 3.5% over 2026-07-01..2026-07-31, and the rows are in export.csv.";
    const { text } = foldComposedDisclosures(narrative, warnings);
    expect(text).toBe(narrative);
  });

  /**
   * THE MEND MUST NOT SPLIT A DOMAIN — the client half of the server's
   * `_NAME_INTERNAL_SUFFIXES` veto (`revi_presentation.narrative`).
   *
   * The word-count guard passes a nine-word tail, so the mend fired on the
   * stop inside a certified benchmark source and the reader was shown
   * "HealthCare. gov" — a broken domain inside a governed citation, on the
   * line whose whole job is to say where the benchmark came from.
   */
  it("does not split a domain the composer was instructed to quote", () => {
    const narrative =
      "ACA marketplace (HealthCare.gov) issuer data for 2023-2024 shows different " +
      "denominators.";
    const { text, folded } = foldComposedDisclosures(narrative, warnings);
    expect(text).toBe(narrative);
    expect(folded).toBe(0);
    expect(text).toContain("HealthCare.gov");
    expect(text).not.toContain("HealthCare. gov");
  });

  it("still mends a genuine welded stop with the domain rule in place", () => {
    // The defect this mend exists for, unchanged: two real sentences the
    // composer joined with no space between them.
    const narrative =
      "The caution-severity notes published above this text govern how far these figures " +
      "can be generalized.Superlatives and spread statements on this answer describe the " +
      "published slice.";
    const { text } = foldComposedDisclosures(narrative, []);
    expect(text).toContain("can be generalized. Superlatives");
  });
});

/**
 * The fold deleted the answer.
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
