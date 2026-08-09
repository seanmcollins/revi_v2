/**
 * Three facts that reached the wire and reached nothing else.
 *
 * `benchmarks` — up to seven governed external ranges per finding, which
 *   `grep -rni benchmark apps/web/src` could not find a single reader for;
 * `context_header.as_of` — the marker that says a number is a balance at a
 *   moment rather than a total over the range published beside it;
 * `context_header` / `narrative` / `metric_display` on a RESTORED
 *   investigation — the context a re-opened session used to render nothing
 *   of, leaving a thread of dollar figures with no stated scope.
 *
 * Every payload below is the shape a live turn against the running API
 * published (denial rate by payer and A/R over 90 days by payer, July
 * 2026, watermark wm_003), trimmed to the fields under test.
 *
 * The restored cases are deliberately written against BOTH payload
 * generations — one that publishes the field and one that does not — since
 * the two are in flight simultaneously and the rendering has to be honest
 * under either.
 */

import { describe, expect, it } from "vitest";

import {
  mapBenchmarks,
  mapContextHeader,
  mapFinding,
  parseInvestigationResponse,
  unitsFromChartSpecs,
  type WirePin,
} from "@/lib/contract";

const PIN: WirePin = {
  watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
  pack: { packId: "base-rcm", version: "1.0.0" },
};

const WIRE_BENCHMARK = {
  id: "benchmark.denial_rate.marketplace_kff",
  metric_id: "denial_rate",
  cohort_label: "ACA marketplace (HealthCare.gov issuers) - in-network claims, plan-reported",
  value_low: "19",
  value_high: "20",
  unit: "percent of in-network claims denied",
  period: "2023-2024",
  authority: "independent analysis of CMS transparency data",
  review_status: "machine_researched",
  cautions: ["Plan-reported data with no initial/final distinction"],
  sources: ["Claims Denials and Appeals in ACA Marketplace Plans in 2024"],
};

const WIRE_FINDING = {
  referent: "F1",
  title: "State Medicaid MCO: 29.5% denial rate",
  statement: "State Medicaid MCO ranks #1 by denial rate over 2026-07-01..2026-07-31: 29.5%.",
  metric_ids: ["denial_rate"],
  values: [
    { name: "denial_rate", value: 0.295082 },
    { name: "rank", value: 1 },
  ],
  grade: "direct",
  impact_cents: null,
  confidence: "high",
  suggested_refinements: ["drill into F1"],
  benchmarks: [WIRE_BENCHMARK],
};

const CHART_SPECS = [{ id: "chart_main", value: "denial_rate", unit: "ratio", rows: [] }];

describe("mapBenchmarks", () => {
  it("carries the range, the cohort, the period, the authority and the review status", () => {
    const [b] = mapBenchmarks([WIRE_BENCHMARK]);
    expect(b.valueLow).toBe("19");
    expect(b.valueHigh).toBe("20");
    expect(b.cohortLabel).toContain("ACA marketplace");
    expect(b.period).toBe("2023-2024");
    expect(b.authority).toBe("independent analysis of CMS transparency data");
    expect(b.reviewStatus).toBe("machine_researched");
    expect(b.cautions).toHaveLength(1);
    expect(b.sources).toHaveLength(1);
  });

  it("keeps the endpoints as the strings the source published", () => {
    // They are a quotation. Rounding one through a float edits it.
    const [b] = mapBenchmarks([{ ...WIRE_BENCHMARK, value_low: "11.81", value_high: "11.99" }]);
    expect(b.valueLow).toBe("11.81");
    expect(b.valueHigh).toBe("11.99");
  });

  it("drops an entry with no range at all and keeps a one-ended one", () => {
    expect(mapBenchmarks([{ ...WIRE_BENCHMARK, value_low: "", value_high: "" }])).toHaveLength(0);
    expect(mapBenchmarks([{ ...WIRE_BENCHMARK, value_high: "" }])).toHaveLength(1);
  });

  it("returns nothing for a payload generation that publishes none", () => {
    expect(mapBenchmarks(undefined)).toEqual([]);
    expect(mapBenchmarks(null)).toEqual([]);
  });
});

describe("mapFinding — benchmarks and the figure they are quoted against", () => {
  const unitByMetric = unitsFromChartSpecs(CHART_SPECS);

  it("attaches the finding's own benchmarks", () => {
    const finding = mapFinding(WIRE_FINDING, { unitByMetric });
    expect(finding?.benchmarks).toHaveLength(1);
    expect(finding?.benchmarks?.[0].cohortLabel).toContain("ACA marketplace");
  });

  it("scales the finding's measure into its display unit so a range is comparable", () => {
    // A `ratio` frame publishes 0.295082 and the card reads 29.5%. A
    // range of "19–20 percent" beside 0.295082 is unreadable.
    const finding = mapFinding(WIRE_FINDING, { unitByMetric });
    expect(finding?.measured?.metricId).toBe("denial_rate");
    expect(finding?.measured?.unit).toBe("percent");
    expect(finding?.measured?.value).toBeCloseTo(29.5082, 6);
  });

  it("falls back to the turn-level list when the finding publishes none", () => {
    const withoutOwn = { ...WIRE_FINDING, benchmarks: undefined };
    const finding = mapFinding(withoutOwn, {
      unitByMetric,
      benchmarks: mapBenchmarks([WIRE_BENCHMARK]),
    });
    expect(finding?.benchmarks).toHaveLength(1);
  });

  it("never quotes a range under a number it does not describe", () => {
    // A two-metric answer must not hang the denial-rate band on a cash
    // finding: the pool is filtered by the finding's own metric ids.
    const cash = { ...WIRE_FINDING, metric_ids: ["cash_posted"] };
    const finding = mapFinding(cash, {
      unitByMetric,
      benchmarks: mapBenchmarks([WIRE_BENCHMARK]),
    });
    expect(finding?.benchmarks).toBeUndefined();
  });

  it("leaves the headline stat exactly as it was", () => {
    const finding = mapFinding(WIRE_FINDING, { unitByMetric });
    expect(finding?.impactDisplay).toBe("29.5%");
    expect(finding?.impactLabel).toBe("denial rate");
  });
});

describe("mapContextHeader — as_of", () => {
  const SNAPSHOT_HEADER = {
    window_start: "2026-07-01",
    window_end: "2026-07-31",
    basis: "service",
    filter_chips: [],
    watermark_id: "wm_003",
    as_of: "2026-08-02",
    display: "as of 2026-08-02 (service) · watermark wm_003",
  };

  it("carries the as-of date a snapshot contract publishes", () => {
    // Live: `ar_over_90_pct` answers as of 2026-08-02 while the payload
    // still carries a July window that the calculation never applied.
    expect(mapContextHeader(SNAPSHOT_HEADER, PIN)?.asOf).toBe("2026-08-02");
  });

  it("leaves it undefined for a flow metric, and for a payload without the field", () => {
    expect(mapContextHeader({ ...SNAPSHOT_HEADER, as_of: null }, PIN)?.asOf).toBeUndefined();
    const older = { ...SNAPSHOT_HEADER, as_of: undefined };
    expect(mapContextHeader(older, PIN)?.asOf).toBeUndefined();
  });

  it("keeps the published window beside it rather than discarding it", () => {
    // The renderer decides which to show; the parser does not silently
    // lose a field the server sent.
    expect(mapContextHeader(SNAPSHOT_HEADER, PIN)?.window.start).toBe("2026-07-01");
  });
});

describe("parseInvestigationResponse — what a restored turn gets back", () => {
  const STORED = {
    investigation_id: "inv_b8267d9b585a",
    session_id: "sess_d9e9a5f15c12",
    turn_id: "turn_1",
    turn_class: "new_investigation",
    status: "complete",
    created_at: "2026-08-09T09:20:00",
    question: "What is our denial rate by payer for July 2026?",
    findings: [WIRE_FINDING],
    warnings: [],
    warnings_v2: [
      {
        code: "POPULATION_CAVEAT",
        severity: "caution",
        message: "population_caveat: denial_rate — OPEN claims are excluded from both sides",
        count: 1,
      },
    ],
    chart_specs: CHART_SPECS,
    metric_display: [],
    cohort: null,
  };

  it("renders the stored context header, marked as restored", () => {
    const parse = parseInvestigationResponse(
      {
        ...STORED,
        context_header: {
          window_start: "2026-07-01",
          window_end: "2026-07-31",
          basis: "service",
          filter_chips: [],
          watermark_id: "wm_003",
        },
        narrative: "Among the payers that cleared reporting…",
      },
      PIN,
    );
    const value = parse.value;
    expect(value?.outcome).toBe("answer");
    if (value?.outcome !== "answer") return;
    expect(value.header?.window.start).toBe("2026-07-01");
    expect(value.header?.watermark.id).toBe("wm_003");
    // The load time and the pack version are the SESSION's — the
    // investigation payload carries neither, so they come from the pin.
    expect(value.header?.watermark.loadedAt).toBe("2026-08-03 04:10");
    expect(value.header?.packVersion.version).toBe("1.0.0");
    expect(value.header?.restored).toBe(true);
    expect(value.narrative).toContain("Among the payers");
  });

  it("publishes no header at all when the store kept none — never an invented one", () => {
    const parse = parseInvestigationResponse(STORED, PIN);
    if (parse.value?.outcome !== "answer") throw new Error("expected an answer");
    expect(parse.value.header).toBeUndefined();
    expect(parse.value.narrative).toBeUndefined();
    // The findings and the caveats still survive — which is exactly why
    // the missing context has to be STATED by the card rather than left
    // as a gap: what comes back otherwise is caveats with no answer.
    expect(parse.value.findings).toHaveLength(1);
    expect(parse.value.warnings).toHaveLength(1);
  });

  it("declines to build a header with no session pin to date it", () => {
    const parse = parseInvestigationResponse({
      ...STORED,
      context_header: { window_start: "2026-07-01", window_end: "2026-07-31", basis: "service" },
    });
    if (parse.value?.outcome !== "answer") throw new Error("expected an answer");
    expect(parse.value.header).toBeUndefined();
  });

  /**
   * The restored-header block, verbatim from a live
   * `GET /v1/investigations/inv_c1f081650a4d`: the server publishes
   * `context_header`, `context_header_restored: true`, `watermark_id`,
   * `newest_data_date` and two `restoration_notes`.
   */
  it("reads the server's own restored flag and its account of the restore", () => {
    const parse = parseInvestigationResponse(
      {
        ...STORED,
        context_header: {
          window_start: "2026-07-27",
          window_end: "2026-08-02",
          basis: "service",
          filters: [],
          filter_chips: [],
          watermark_id: "wm_003",
          display: "2026-07-27..2026-08-02 (service) · watermark wm_003",
        },
        context_header_restored: true,
        watermark_id: "wm_003",
        newest_data_date: "2026-08-02",
        restoration_notes: [
          "Restored context: the window, scope, cohort and watermark below are rebuilt from this turn's stored investigation spec at watermark wm_003, not re-computed — the figures are the ones this turn published when it ran.",
          "The composed narrative is not stored anywhere — the narrative trace keeps its template, redactions and length, not its sentences — so this turn restores without prose.",
        ],
      },
      PIN,
    );
    if (parse.value?.outcome !== "answer") throw new Error("expected an answer");
    expect(parse.value.header?.restored).toBe(true);
    expect(parse.value.header?.restorationNotes).toHaveLength(2);
    expect(parse.value.header?.restorationNotes?.[1]).toContain("not its sentences");
  });

  it("honours the server saying a header was NOT restored", () => {
    const parse = parseInvestigationResponse(
      {
        ...STORED,
        context_header: {
          window_start: "2026-07-01",
          window_end: "2026-07-31",
          basis: "service",
        },
        context_header_restored: false,
      },
      PIN,
    );
    if (parse.value?.outcome !== "answer") throw new Error("expected an answer");
    // The parser used to assert `restored: true` from the mere fact that
    // it was the one doing the reading. The server publishes the flag now,
    // and it wins.
    expect(parse.value.header?.restored).toBe(false);
  });

  it("applies the stored metric_display corrections to restored titles", () => {
    const parse = parseInvestigationResponse(
      {
        ...STORED,
        findings: [
          {
            ...WIRE_FINDING,
            title: "timely filing at risk dollars: $22,426,000.28",
            metric_ids: ["timely_filing_at_risk_dollars"],
          },
        ],
        metric_display: [
          {
            metric_id: "timely_filing_at_risk_dollars",
            display_name: "Unbilled open inventory on a running filing clock",
            caveat: "Counts every unbilled open claim regardless of runway.",
          },
        ],
      },
      PIN,
    );
    if (parse.value?.outcome !== "answer") throw new Error("expected an answer");
    expect(parse.value.findings[0].title).toContain("Unbilled open inventory");
    expect(parse.value.findings[0].metricDisplay?.caveat).toContain("regardless of runway");
  });

  it("restores the cohort onto the header rather than floating it alone", () => {
    const parse = parseInvestigationResponse(
      {
        ...STORED,
        context_header: {
          window_start: "2026-07-01",
          window_end: "2026-07-31",
          basis: "service",
          watermark_id: "wm_003",
        },
        cohort: {
          id: "cohort_6ad45a83",
          definition: "payer eq Atlas Commercial",
          entity_grain: "claim",
          size: 35655,
          pinned: true,
        },
      },
      PIN,
    );
    if (parse.value?.outcome !== "answer") throw new Error("expected an answer");
    expect(parse.value.header?.cohort?.definition).toBe("payer eq Atlas Commercial");
    expect(parse.value.header?.cohort?.detailed).toBe(true);
  });
});
