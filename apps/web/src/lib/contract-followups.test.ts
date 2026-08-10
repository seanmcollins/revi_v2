/**
 * The wire→UI seam for the fields the backend published in round 2:
 * structured warnings, the cohort block, the anomaly-drill reconciliation,
 * governed metric display names, portfolio lanes and card-level impact
 * agreement, and the two residuals (`ErrorEnvelope.subcode`,
 * `TurnError.usage`).
 *
 * Every fixture read here is CAPTURED, not composed: `wire-samples.json`
 * gained `anomaly_drill_turn_complete` (a live ANM-021 drill posted with
 * `anomaly_ref`), `cohort_turn_complete` (three typed `drill_into`
 * operators pinning a real cohort), `portfolio_lanes` (four cards trimmed
 * from `GET /v1/portfolio/latest`, one per agreement state plus a
 * compliance-lane card) and two live `QUERY_BUDGET_EXCEEDED` envelopes.
 * That is the same discipline the rest of this directory keeps and for the
 * same reason: fixtures written in the UI's vocabulary agree with the
 * parser and disagree with the server.
 */

import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import RAW_SAMPLES from "@/lib/__fixtures__/wire-samples.json";
import {
  applyMetricDisplayNames,
  mapAnomalyReconciliation,
  mapCohort,
  mapContextHeader,
  mapMetricDisplay,
  mapStructuredWarning,
  mapUsage,
  metricDisplayIndex,
  newReceivedState,
  parsePortfolioSnapshot,
  parseTurnFrame,
  parseTurnResponse,
  readTurnWarnings,
  trackReceived,
  turnResponseToEvents,
  type WirePin,
} from "@/lib/contract";
import { UNCLASSIFIED, warningBody, warningTitle, WARNING_TITLES } from "@/lib/warnings";

/* eslint-disable @typescript-eslint/no-explicit-any */
const SAMPLES = RAW_SAMPLES as any;

/**
 * Every warning code the API can publish, read out of the API's own source.
 *
 * `revi_api.warning_codes` is the single definition: a tuple of `_rule(...)`
 * declarations plus `UNCLASSIFIED`, and `WARNING_CODES` is derived from it
 * in one line. There is no JSON artifact of that list on the wire and no
 * generated type carries it, so this test reads the module and takes the
 * codes out of it rather than keeping a second copy that can silently fall
 * behind — which is exactly what happened through the whole of wave B.
 *
 * A missing or unreadable module FAILS. "The server's list could not be
 * read, so nothing was checked" is the failure mode this replaced.
 */
function publishedWarningCodes(): string[] {
  const here = dirname(fileURLToPath(import.meta.url));
  const source = readFileSync(
    resolve(here, "../../../../apps/api/src/revi_api/warning_codes.py"),
    "utf8",
  );
  const codes = [...source.matchAll(/_rule\(\s*"([A-Z][A-Z0-9_]*)"/g)].map((m) => m[1]);
  const unclassified = /^UNCLASSIFIED\s*=\s*"([A-Z_]+)"/m.exec(source);
  if (unclassified) codes.push(unclassified[1]);
  return [...new Set(codes)];
}

const PIN: WirePin = {
  watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
  pack: { packId: "base-rcm", version: "1.0.0" },
};

function answerOf(sample: unknown) {
  const parse = parseTurnResponse(sample, PIN);
  expect(parse.drift).toEqual([]);
  expect(parse.value?.outcome).toBe("answer");
  if (parse.value?.outcome !== "answer") throw new Error("not an answer");
  return parse.value;
}

/* ------------------------------------------------------------------ */
/* 1. Structured warnings (warnings_v2)                                */
/* ------------------------------------------------------------------ */

describe("structured warnings", () => {
  it("reads the classified list off a live turn instead of the prose", () => {
    const answer = answerOf(SAMPLES.anomaly_drill_turn_complete);
    expect(answer.warnings.map((w) => w.code)).toEqual([
      "SUPPRESSION_APPLIED",
      "NARRATIVE_REDACTED",
    ]);
    // Every one of them is marked as server-classified, so the UI knows the
    // code is a contract and not something it inferred from a sentence.
    expect(answer.warnings.every((w) => w.structured)).toBe(true);
    // The engine's sentence survives verbatim — a code is a handle added
    // beside the text, never a replacement for it.
    expect(answer.warnings[0].message).toBe(
      SAMPLES.anomaly_drill_turn_complete.warnings[0],
    );
  });

  it("keeps the severity the server assigned, per code", () => {
    const answer = answerOf(SAMPLES.cohort_turn_complete);
    const bySeverity = Object.fromEntries(answer.warnings.map((w) => [w.code, w.severity]));
    // A dropped cohort window changes how the population reads; suppression
    // and a redacted sentence do not.
    expect(bySeverity).toMatchObject({
      COHORT_WINDOW_DROPPED: "caution",
      SUPPRESSION_APPLIED: "info",
      NARRATIVE_REDACTED: "info",
    });
  });

  it("falls back to the prose strings when warnings_v2 is absent", () => {
    // Investigations stored before the classifier shipped carry sentences
    // only. Dropping them because the structured list is missing would lose
    // real cautions on every restored turn.
    const legacy = {
      ...SAMPLES.anomaly_drill_turn_complete,
      warnings_v2: undefined,
    };
    delete legacy.warnings_v2;
    const answer = answerOf(legacy);
    expect(answer.warnings).toHaveLength(2);
    expect(answer.warnings.every((w) => w.structured)).toBe(false);
    expect(answer.warnings[0].message).toBe(SAMPLES.anomaly_drill_turn_complete.warnings[0]);
  });

  /**
   * A REFUSED MONITOR DECLARATION IS A STATE CHANGE, NOT A CAVEAT.
   *
   * The live drop this closes: the server appends the refusal to
   * `warnings` after `warnings_v2` has been built, and this client prefers
   * the structured list whenever it is non-empty — so the one sentence
   * saying that nothing is being monitored reached no surface at all. It is
   * read here from whichever half of the contract is live: the first-class
   * `monitor_refused` object, or the classified `MONITOR_NOT_CREATED` warning.
   */
  describe("a refused monitor declaration", () => {
    const REFUSAL =
      "this turn read as a monitor declaration, and the monitor was NOT created: a threshold in " +
      "'cents' is only honest for a 'money_cents' contract, and this monitor measures 'ratio'.";

    it("is lifted out of the classified warnings when there is no field for it", () => {
      const answer = answerOf({
        ...SAMPLES.anomaly_drill_turn_complete,
        warnings_v2: [
          ...SAMPLES.anomaly_drill_turn_complete.warnings_v2,
          { code: "MONITOR_NOT_CREATED", severity: "caution", message: REFUSAL },
        ],
      });
      if (answer.outcome !== "answer") throw new Error("not an answer");
      expect(answer.monitorRefused?.reason).toBe(REFUSAL);
      // And it stays in the warning list as well: the integrity line counts
      // it and the sheet renders it. Lifting it out is an ADDITIONAL
      // surface, never a relocation that leaves the count short.
      expect(answer.warnings.map((w) => w.code)).toContain("MONITOR_NOT_CREATED");
    });

    it("prefers the first-class field, with the alternatives it names", () => {
      const answer = answerOf({
        ...SAMPLES.anomaly_drill_turn_complete,
        monitor_refused: {
          reason_code: "threshold_illegal",
          reason: REFUSAL,
          legal_alternatives: ["more than half a point", "more than 5%"],
          subject: "Pinnacle Health Plan denial rate",
          threshold_phrase: "moves more than $5,000",
        },
      });
      if (answer.outcome !== "answer") throw new Error("not an answer");
      expect(answer.monitorRefused).toEqual({
        reasonCode: "threshold_illegal",
        reason: REFUSAL,
        legalAlternatives: ["more than half a point", "more than 5%"],
        subject: "Pinnacle Health Plan denial rate",
        thresholdPhrase: "moves more than $5,000",
      });
    });

    it("is absent on an ordinary turn, and is never guessed from prose", () => {
      // The prose list is the pre-classification fallback. A client that
      // matched sentences would start rendering a state change off a
      // phrase, which is a different defect from the one being fixed.
      const answer = answerOf({
        ...SAMPLES.anomaly_drill_turn_complete,
        warnings: [
          ...SAMPLES.anomaly_drill_turn_complete.warnings,
          `population_caveat: ${REFUSAL}`,
        ],
      });
      if (answer.outcome !== "answer") throw new Error("not an answer");
      expect(answer.monitorRefused).toBeUndefined();
    });
  });

  it("believes the prose over an EMPTY warnings_v2 beside non-empty warnings", () => {
    // The server never drops a warning while classifying, so an empty
    // structured list next to real sentences means this payload predates
    // the classifier — not that the warnings went away.
    const warnings = readTurnWarnings([], ["window_assumed: no window was named"]);
    expect(warnings).toHaveLength(1);
    expect(warnings[0].structured).toBeUndefined();
  });

  it("collapses identical warnings with a count rather than stacking rows", () => {
    const warning = mapStructuredWarning({
      code: "POPULATION_CAVEAT",
      severity: "caution",
      message: "population_caveat: this counts inventory, not exposure",
      count: 4,
    });
    expect(warning).toMatchObject({ code: "POPULATION_CAVEAT", count: 4, severity: "caution" });
  });

  it("drops a count of 1 — a badge saying ×1 is noise", () => {
    const warning = mapStructuredWarning({
      code: "SUPPRESSION_APPLIED",
      severity: "info",
      message: "suppression: small cells removed",
      count: 1,
    });
    expect(warning?.count).toBeUndefined();
  });

  it("reads an unknown severity as caution, never as a note", () => {
    // Guessing "info" over something that changes the reading is the failure
    // that costs a reader money; guessing the other way costs an icon.
    const warning = mapStructuredWarning({
      code: "SOMETHING_NEW",
      severity: "fatal",
      message: "a family this build has never seen",
    });
    expect(warning?.severity).toBe("caution");
  });

  it("skips entries with no code or no sentence", () => {
    expect(mapStructuredWarning({ code: "", message: "x" })).toBeNull();
    expect(mapStructuredWarning({ code: "X", message: "" })).toBeNull();
  });

  it("titles every code the API's warning_codes module publishes", () => {
    // READ FROM THE SERVER'S OWN MODULE, never re-typed here.
    //
    // This pin used to be a hand-maintained list of 38 codes. Wave B added
    // ten — the entire bounds/premise/window vocabulary, including the
    // flagship RANKING_REFUSED — and did not update it, so the assertion
    // below (an exact-equality check that reads like the strictest guard in
    // the suite) compared a stale list against a client that matched it and
    // passed green for the whole time it was drifting. An analyst read
    // "ranking_refused: 52 of the 52 publishable denial rate cells…".
    //
    // A list that has to be kept in step by hand is not a pin, it is a
    // second copy of the contract. `publishedWarningCodes()` parses
    // `revi_api/warning_codes.py` itself, so ANY code the server can emit
    // fails here the moment it exists and before it can reach a reader.
    const published = publishedWarningCodes();
    // A parse that found nothing would make this whole test vacuous.
    expect(published.length).toBeGreaterThan(40);
    expect(published).toContain("RANKING_REFUSED");

    for (const code of published) {
      // UNCLASSIFIED is published and deliberately untitled: the server is
      // saying it has no handle for the sentence, and a confident heading
      // over it would be this client making the call the server declined.
      if (code === UNCLASSIFIED) continue;
      expect(warningTitle(code), `${code} needs a plain-language title`).toBeTruthy();
    }
    // And nothing is titled that the server cannot emit — a title for a
    // code that does not exist is a sentence nobody will ever read, kept
    // alive by a test.
    const titleable = published.filter((code) => code !== UNCLASSIFIED);
    expect(Object.keys(WARNING_TITLES).sort()).toEqual([...titleable].sort());
  });

  it("gives UNCLASSIFIED no title — the server said it has no handle", () => {
    expect(warningTitle("UNCLASSIFIED")).toBeUndefined();
    expect(warningTitle("ANSWER_NOTE")).toBeUndefined();
  });

  it("strips a machine prefix only when it IS the code", () => {
    expect(
      warningBody("POPULATION_CAVEAT", "population_caveat: counts inventory, not exposure"),
    ).toBe("counts inventory, not exposure");
    expect(
      warningBody("COMPARISON_WINDOW_MISMATCH", "COMPARISON_WINDOW_MISMATCH: 90d vs 91d"),
    ).toBe("90d vs 91d");
    // "suppression" is not SUPPRESSION_APPLIED, and this function does not
    // get to decide that it means the same thing.
    const suppression = "suppression: cells counting fewer than 11 entities are suppressed";
    expect(warningBody("SUPPRESSION_APPLIED", suppression)).toBe(suppression);
    // Nothing that carries information is ever removed.
    const redacted = "narrative sentence redacted: 1 sentence(s) dropped";
    expect(warningBody("NARRATIVE_REDACTED", redacted)).toBe(redacted);
  });
});

/* ------------------------------------------------------------------ */
/* 2. The cohort chip                                                  */
/* ------------------------------------------------------------------ */

describe("cohort", () => {
  it("reads the live cohort block as a definition, a grain and a size", () => {
    const answer = answerOf(SAMPLES.cohort_turn_complete);
    expect(answer.cohort).toMatchObject({
      id: "cohort_f35d90b18482b2ea",
      definition: "payer in [State Medicaid, Atlas Commercial, Meridian Health]",
      entityGrain: "claim",
      size: 86_415,
      originReferent: "D9",
      pinned: true,
      pinnedWatermarkId: "wm_003",
      detailed: true,
    });
  });

  it("merges the block onto the header, where the chip lives", () => {
    const answer = answerOf(SAMPLES.cohort_turn_complete);
    // The header payload publishes only the id and the size — the chip used
    // to render a hash because that was all it was handed.
    expect(SAMPLES.cohort_turn_complete.context_header.cohort).toBe(
      "cohort_f35d90b18482b2ea",
    );
    expect(answer.header?.cohort?.definition).toBe(
      "payer in [State Medicaid, Atlas Commercial, Meridian Health]",
    );
    expect(answer.header?.cohort?.entityGrain).toBe("claim");
  });

  it("re-emits the header from turn_complete so a streamed chip is upgraded", () => {
    // The live `context_header` frame arrives first and carries the hash;
    // the definition rides only on the terminal payload. The store REPLACES
    // the header, so re-emitting is how the chip stops being a hash.
    const received = newReceivedState();
    const frame = parseTurnFrame(
      "context_header",
      SAMPLES.cohort_turn_complete.context_header,
      PIN,
    );
    expect(frame?.ok).toBe(true);
    if (frame?.ok) trackReceived(received, frame.value);
    expect(received.hasHeader).toBe(true);

    const events = turnResponseToEvents(answerOf(SAMPLES.cohort_turn_complete), received);
    const header = events.find((e) => e.type === "context_header");
    expect(header).toBeDefined();
    if (header?.type === "context_header") {
      expect(header.header.cohort?.detailed).toBe(true);
    }
  });

  it("does not re-emit the header when there is no cohort to upgrade", () => {
    const received = newReceivedState();
    received.hasHeader = true;
    const events = turnResponseToEvents(answerOf(SAMPLES.anomaly_drill_turn_complete), received);
    expect(events.some((e) => e.type === "context_header")).toBe(false);
  });

  it("marks a payload with no definition as NOT detailed", () => {
    // A chip that dresses a handle up as a definition is the defect, not
    // the fix. `detailed` is what lets the UI say which one it has.
    const cohort = mapCohort({ id: "coh_9f2a11", size: 312 });
    expect(cohort).toMatchObject({ id: "coh_9f2a11", definition: "coh_9f2a11", size: 312 });
    expect(cohort?.entityGrain).toBeUndefined();
  });

  it("is nothing at all when there is no cohort", () => {
    expect(mapCohort(null)).toBeUndefined();
    expect(mapCohort({ size: 12 })).toBeUndefined();
  });
});

/* ------------------------------------------------------------------ */
/* 3. Anomaly drill reconciliation                                     */
/* ------------------------------------------------------------------ */

describe("anomaly drill reconciliation", () => {
  it("carries both figures off a live ANM-021 drill", () => {
    const answer = answerOf(SAMPLES.anomaly_drill_turn_complete);
    expect(answer.anomalyReconciliation).toMatchObject({
      anomalyId: "ANM-021",
      status: "diverged",
      cardImpactCents: 17_821_682,
      answerImpactCents: 19_587_392,
      deltaCents: 1_765_710,
      answerMetricId: "dnfb_dollars",
      cardMetricId: "dnfb_dollars",
    });
    expect(answer.anomalyReconciliation?.deltaFraction).toBeCloseTo(0.099077, 6);
    // The platform's own account of WHY, kept verbatim.
    expect(answer.anomalyReconciliation?.detail).toContain("valuation basis is not the contract's");
  });

  it("rides the terminal event, like the grade and the provenance badge", () => {
    const events = turnResponseToEvents(answerOf(SAMPLES.anomaly_drill_turn_complete));
    const complete = events.find((e) => e.type === "turn_complete");
    expect(complete?.type === "turn_complete" && complete.anomalyReconciliation?.status).toBe(
      "diverged",
    );
  });

  it("drops a payload whose status is not one of the three published", () => {
    // "unavailable" and "agreed" are opposite claims about the same pair of
    // numbers; defaulting between them is the silence the strip ends.
    expect(
      mapAnomalyReconciliation({ anomaly_id: "A", status: "maybe", card_impact_cents: 1 }),
    ).toBeUndefined();
    expect(
      mapAnomalyReconciliation({ anomaly_id: "A", status: "agreed" }),
    ).toBeUndefined();
  });

  it("keeps an unavailable verdict rather than treating it as agreement", () => {
    const value = mapAnomalyReconciliation({
      anomaly_id: "ANM-015",
      status: "unavailable",
      card_impact_cents: 4_893_861,
      answer_impact_cents: null,
      detail: "no governed contract re-derives this cell",
    });
    expect(value).toMatchObject({ status: "unavailable", cardImpactCents: 4_893_861 });
    expect(value?.answerImpactCents).toBeUndefined();
  });

  it("is absent on a turn that drilled no card", () => {
    const answer = answerOf(SAMPLES.turn_complete);
    expect(answer.anomalyReconciliation).toBeUndefined();
  });
});

/* ------------------------------------------------------------------ */
/* 4. Governed metric display names                                    */
/* ------------------------------------------------------------------ */

describe("metric display names", () => {
  it("rewrites a live finding title from the turn's own governed entries", () => {
    // The engine composes titles from `metric_id.replace("_", " ")`, so the
    // wire says "dnfb dollars" and the pack says what that measures.
    expect(SAMPLES.anomaly_drill_turn_complete.findings[0].title).toContain("dnfb dollars");
    const answer = answerOf(SAMPLES.anomaly_drill_turn_complete);
    expect(answer.findings[0].title).toBe(
      "Northgate Regional Hospital / Federal Medicare / General Surgery: $195,873.92 " +
        "Discharged not final billed (unbilled discharges)",
    );
  });

  it("corrects the statement too — a card must not name one number twice", () => {
    // Title and statement are composed by the same server-side formatter
    // from the same `metric_label()` spelling and sit two lines apart.
    expect(SAMPLES.anomaly_drill_turn_complete.findings[0].statement).toContain("dnfb dollars");
    const answer = answerOf(SAMPLES.anomaly_drill_turn_complete);
    expect(answer.findings[0].statement).toContain(
      "Discharged not final billed (unbilled discharges)",
    );
    expect(answer.findings[0].statement).not.toContain("dnfb dollars");
  });

  it("carries the caveat that was authored with the name", () => {
    const answer = answerOf(SAMPLES.anomaly_drill_turn_complete);
    expect(answer.findings[0].metricDisplay).toMatchObject({
      metricId: "dnfb_dollars",
      displayName: "Discharged not final billed (unbilled discharges)",
    });
    expect(answer.findings[0].metricDisplay?.caveat).toContain("coding backlog");
  });

  it("applies the same correction to the chart over the same measure", () => {
    const answer = answerOf(SAMPLES.anomaly_drill_turn_complete);
    expect(answer.charts[0].title).toBe(
      "Discharged not final billed (unbilled discharges) by facility",
    );
  });

  it("handles the review's own example end to end", () => {
    const display = metricDisplayIndex(
      mapMetricDisplay([
        {
          metric_id: "timely_filing_at_risk_dollars",
          display_name: "Unbilled open inventory on a running filing clock",
          caveat: "Counts every unbilled open claim regardless of runway.",
        },
      ]),
    );
    expect(applyMetricDisplayNames("timely filing at risk dollars: $22.4M", display)).toBe(
      "Unbilled open inventory on a running filing clock: $22.4M",
    );
    // The raw id spelling is corrected too — a debug surface should not
    // disagree with the card beside it.
    expect(applyMetricDisplayNames("timely_filing_at_risk_dollars", display)).toBe(
      "Unbilled open inventory on a running filing clock",
    );
  });

  it("touches nothing it was not asked to", () => {
    const display = metricDisplayIndex(
      mapMetricDisplay([{ metric_id: "dnfb_dollars", display_name: "DNFB (unbilled)" }]),
    );
    // A measure with no entry, and a word that merely contains one.
    expect(applyMetricDisplayNames("denied dollars by payer", display)).toBe(
      "denied dollars by payer",
    );
    expect(applyMetricDisplayNames("adjusted dnfb dollars ratio", display)).toBe(
      "adjusted DNFB (unbilled) ratio",
    );
    expect(applyMetricDisplayNames("dnfb_dollars_ratio", display)).toBe("dnfb_dollars_ratio");
  });

  it("is idempotent — a title composed server-side comes back byte-identical", () => {
    // The failure this guards: governed names routinely CONTAIN the phrase
    // they replace, so a second pass over an already-corrected title
    // splices the name into the middle of itself ("First-pass First-pass
    // denial rate (all payers) (all payers)"). Both payload generations
    // are in flight — one composes titles server-side, one does not — so
    // the client half has to survive being right twice.
    const display = metricDisplayIndex(
      mapMetricDisplay([
        { metric_id: "denial_rate", display_name: "First-pass denial rate (all payers)" },
      ]),
    );
    const once = applyMetricDisplayNames("denial rate rose to 12%", display);
    expect(once).toBe("First-pass denial rate (all payers) rose to 12%");
    expect(applyMetricDisplayNames(once, display)).toBe(once);
    // The server-composed title, which never held a raw id: untouched.
    const composed = "First-pass denial rate (all payers): 12.4% (2026-07-01..2026-07-31)";
    expect(applyMetricDisplayNames(composed, display)).toBe(composed);
  });

  it("prefers the longest metric id so a prefix cannot claim a suffix's phrase", () => {
    const display = metricDisplayIndex(
      mapMetricDisplay([
        { metric_id: "denial_rate", display_name: "Denial rate (all payers)" },
        { metric_id: "initial_denial_rate", display_name: "First-pass denial rate" },
      ]),
    );
    expect(applyMetricDisplayNames("initial denial rate rose", display)).toBe(
      "First-pass denial rate rose",
    );
  });

  it("skips entries missing either half of the correction", () => {
    expect(mapMetricDisplay([{ metric_id: "x" }, { display_name: "y" }, 7])).toEqual([]);
  });

  it("re-emits streamed findings and charts so a live turn gets the correction", () => {
    // `metric_display` rides on the TERMINAL payload, so the `finding`
    // frame that arrived mid-stream necessarily carries the engine's raw
    // spelling. Skipping it as "already received" is how a live answer
    // ended up with a card reading "dnfb dollars" above a narrative
    // reading "discharged not final billed".
    const answer = answerOf(SAMPLES.anomaly_drill_turn_complete);
    const received = newReceivedState();
    received.findingReferents.add("F1");
    received.chartIds.add("chart_main");

    const events = turnResponseToEvents(answer, received);
    const finding = events.find((e) => e.type === "finding");
    expect(finding?.type === "finding" && finding.finding.title).toContain(
      "Discharged not final billed",
    );
    expect(events.some((e) => e.type === "chart_spec")).toBe(true);
  });

  it("still skips what the stream delivered when nothing was corrected", () => {
    const answer = answerOf(SAMPLES.turn_complete);
    const received = newReceivedState();
    for (const finding of answer.findings) received.findingReferents.add(finding.referent.value);
    for (const chart of answer.charts) received.chartIds.add(chart.id);
    const events = turnResponseToEvents(answer, received);
    expect(events.some((e) => e.type === "finding")).toBe(false);
    expect(events.some((e) => e.type === "chart_spec")).toBe(false);
  });

  it("leaves a restored investigation's titles as the store kept them", () => {
    // `InvestigationResponse` publishes no metric_display block, and
    // captioning an older answer with today's pack would be the overclaim
    // the provenance badge exists to prevent.
    const parse = parseTurnResponse(
      { ...SAMPLES.anomaly_drill_turn_complete, metric_display: [] },
      PIN,
    );
    expect(parse.value?.outcome === "answer" && parse.value.findings[0].title).toContain(
      "dnfb dollars",
    );
  });
});

/* ------------------------------------------------------------------ */
/* 5. Portfolio lanes + card-level impact agreement                    */
/* ------------------------------------------------------------------ */

describe("portfolio lanes and impact agreement", () => {
  it("reads the published lanes in the server's own order", () => {
    const { value, drift } = parsePortfolioSnapshot(SAMPLES.portfolio_lanes);
    expect(drift).toEqual([]);
    expect(value?.lanes.map((lane) => lane.id)).toEqual(["compliance", "value"]);
    expect(value?.lanes[0]).toMatchObject({
      id: "compliance",
      label: "Must do regardless of size",
      itemCount: 2,
    });
    expect(value?.lanes[0].description).toContain("worked because the rule says so");
  });

  it("carries each card's figure AND the platform's re-derivation of it", () => {
    const { value } = parsePortfolioSnapshot(SAMPLES.portfolio_lanes);
    const byId = Object.fromEntries((value?.items ?? []).map((i) => [i.referent, i]));
    expect(byId["ANM-021"]).toMatchObject({
      impactAgreement: "diverged",
      impactCents: 17_821_682,
      reconciledImpactCents: 19_587_392,
      reconciledImpactMetricId: "dnfb_dollars",
      lane: "value",
      metricDisplayName: "Discharged not final billed (unbilled discharges)",
    });
    expect(byId["ANM-021"].impactDeltaFraction).toBeCloseTo(0.099077, 6);
    expect(byId["ANM-021"].impactReconciliationNote).toContain("re-derived");
    expect(byId["ANM-023"]).toMatchObject({ impactAgreement: "agreed", lane: "compliance" });
    expect(byId["ANM-015"]).toMatchObject({ impactAgreement: "unavailable" });
  });

  it("publishes the priority formula's terms so the ordering is not a black box", () => {
    const { value } = parsePortfolioSnapshot(SAMPLES.portfolio_lanes);
    const card = value?.items.find((i) => i.referent === "ANM-021");
    expect(card?.priority).toMatchObject({ floorApplied: false, floorBasis: "relative_median" });
    expect(card?.priority?.impactTerm).toBeCloseTo(0.090325, 6);
  });

  it("classifies the snapshot's own warnings", () => {
    const { value } = parsePortfolioSnapshot(SAMPLES.portfolio_lanes);
    expect(value?.warnings.map((w) => w.code)).toEqual([
      "PORTFOLIO_CARDS_NOT_INVESTIGABLE",
      "PORTFOLIO_IMPACT_UNRECONCILED",
      "PORTFOLIO_IMPACT_DIVERGED",
    ]);
    expect(value?.warnings.every((w) => w.severity === "caution")).toBe(true);
  });

  it("draws one list when the deployment publishes no lanes", () => {
    const { value } = parsePortfolioSnapshot({
      items: SAMPLES.portfolio_lanes.items,
      warnings: [],
    });
    expect(value?.lanes).toEqual([]);
  });

  it("drops a lane with no id or no label rather than heading a bucket of work", () => {
    const { value } = parsePortfolioSnapshot({
      items: [],
      lanes: [{ id: "", label: "x" }, { id: "y", label: "" }, { id: "z", label: "Z" }],
    });
    expect(value?.lanes.map((l) => l.id)).toEqual(["z"]);
  });
});

/* ------------------------------------------------------------------ */
/* 6. Residuals: subcode + error usage                                 */
/* ------------------------------------------------------------------ */

describe("budget error subcode and failed-turn usage", () => {
  it("reads the live warehouse-read stop", () => {
    const parse = parseTurnResponse(SAMPLES.budget_error_warehouse, PIN);
    expect(parse.value?.outcome).toBe("error");
    if (parse.value?.outcome !== "error") throw new Error("not an error");
    expect(parse.value.error).toMatchObject({
      code: "QUERY_BUDGET_EXCEEDED",
      subcode: "WAREHOUSE_READ_BUDGET",
    });
    // A typed turn spends nothing on the model, and "$0 over 0 model calls"
    // on a card is noise dressed as disclosure.
    expect(parse.value.usage).toBeUndefined();
  });

  it("reads the live model-spend stop and what it cost", () => {
    const parse = parseTurnResponse(SAMPLES.budget_error_model_spend, PIN);
    if (parse.value?.outcome !== "error") throw new Error("not an error");
    expect(parse.value.error.subcode).toBe("MODEL_SPEND_BUDGET");
    expect(parse.value.usage).toMatchObject({ llmCalls: 2, costUsd: "0.021321" });
    // The decimal string is never rounded through a float on its way here.
    expect(typeof parse.value.usage?.costUsd).toBe("string");
  });

  it("carries both onto the error event the store reduces", () => {
    const parse = parseTurnResponse(SAMPLES.budget_error_model_spend, PIN);
    if (!parse.value) throw new Error("no value");
    const [event] = turnResponseToEvents(parse.value);
    expect(event).toMatchObject({
      type: "error",
      subcode: "MODEL_SPEND_BUDGET",
      usage: { llmCalls: 2 },
    });
  });

  it("reads the subcode off a streamed error frame too", () => {
    const frame = parseTurnFrame(
      "error",
      {
        code: "QUERY_BUDGET_EXCEEDED",
        message: "…",
        correlation_id: "corr_1",
        subcode: "MODEL_SPEND_BUDGET",
      },
      PIN,
    );
    expect(frame?.ok).toBe(true);
    if (frame?.ok && frame.value.type === "error") {
      expect(frame.value.subcode).toBe("MODEL_SPEND_BUDGET");
    }
  });

  it("leaves subcode off an envelope that publishes none", () => {
    const parse = parseTurnResponse(
      {
        outcome: "error",
        error: { code: "GRAIN_INCOMPATIBLE", message: "…", correlation_id: "c" },
      },
      PIN,
    );
    if (parse.value?.outcome !== "error") throw new Error("not an error");
    expect(parse.value.error.subcode).toBeUndefined();
  });

  it("is nothing when the server published no usage block", () => {
    expect(mapUsage(undefined)).toBeUndefined();
    expect(mapUsage({ llm_calls: 0, cost_usd: "0" })).toBeUndefined();
    expect(mapUsage({ llm_calls: 1, cost_usd: "0.0004" })).toMatchObject({ llmCalls: 1 });
  });
});

/* ------------------------------------------------------------------ */
/* 8. Filter chips carry the predicate that RAN (FN-9)                 */
/* ------------------------------------------------------------------ */

describe("filter chips", () => {
  const chip = (extra: Record<string, unknown>) =>
    mapContextHeader(
      {
        window_start: "2026-07-01",
        window_end: "2026-07-31",
        basis: "service",
        filter_chips: [
          { dimension: "payer", op: "eq", origin_turn: "turn_b5dbdb9ad05e", ...extra },
        ],
      },
      PIN,
    )?.filters[0];

  it("carries the user's original phrasing beside the corrected value", () => {
    expect(
      chip({ values: ["Lakewood Medicaid MCO"], requested_values: ["lakewood medicaid mco"] }),
    ).toMatchObject({
      values: ["Lakewood Medicaid MCO"],
      requestedValues: ["lakewood medicaid mco"],
    });
  });

  it("keeps nothing when the two agree — an uncorrected value has no history", () => {
    // Both payload generations land here: one publishes requested_values
    // identical to values, the older one publishes none at all. Neither
    // says anything worth a line under the chip.
    expect(
      chip({ values: ["Lakewood Medicaid MCO"], requested_values: ["Lakewood Medicaid MCO"] })
        ?.requestedValues,
    ).toBeUndefined();
    expect(chip({ values: ["Lakewood Medicaid MCO"] })?.requestedValues).toBeUndefined();
  });
});
