/**
 * Taking the numbers out of the browser — and taking their bounds with
 * them.
 *
 * The load-bearing assertion in this file is the one about caveats: a
 * copied answer that carries findings and drops warnings is this product
 * shipping the flattering half of its own answer, which is precisely the
 * failure the honesty layer exists to prevent. Everything else here is
 * about not corrupting a spreadsheet on the way out — cents rendered as
 * dollars, a withheld cell rendered as empty rather than as zero, and a
 * free-text title that begins with "=" not executed by Excel.
 *
 * The payloads are shapes captured from a live turn against the running
 * API (denial rate by payer, July 2026, watermark wm_003), not invented.
 */

import { describe, expect, it } from "vitest";

import {
  answerToText,
  buildCsv,
  chartToCsv,
  csvCell,
  exportFilename,
  portfolioToCsv,
  windowLine,
} from "@/lib/export";
import type { PortfolioItem } from "@/lib/mock/portfolio";
import type { Benchmark, ChartSpec, ContextHeaderData, Finding, WarningEvent } from "@/lib/types";

const BENCHMARK: Benchmark = {
  id: "benchmark.denial_rate.marketplace_kff",
  metricId: "denial_rate",
  cohortLabel: "ACA marketplace (HealthCare.gov issuers) - in-network claims, plan-reported",
  valueLow: "19",
  valueHigh: "20",
  unit: "percent of in-network claims denied",
  period: "2023-2024",
  authority: "independent analysis of CMS transparency data",
  reviewStatus: "machine_researched",
  cautions: [
    "20% in 2023 (436M claims), 19% in 2024 (451M claims); insurer-level range 1-54% (2023); out-of-network 36-37%",
    "Plan-reported data with no initial/final distinction",
  ],
  sources: ["Claims Denials and Appeals in ACA Marketplace Plans in 2024"],
};

const HEADER: ContextHeaderData = {
  window: { start: "2026-07-01", end: "2026-07-31", basis: "service" },
  filters: [],
  watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
  packVersion: { packId: "base-rcm", version: "1.0.0" },
};

const FINDING: Finding = {
  referent: { value: "F1", kind: "finding" },
  title: "State Medicaid MCO: 29.5% denial rate",
  statement: "State Medicaid MCO ranks #1 by denial rate over 2026-07-01..2026-07-31: 29.5%.",
  metricRefs: ["denial_rate"],
  values: { denial_rate: 0.295082, rank: 1 },
  grade: "direct",
  directionOfGood: "neutral",
  confidence: "high",
  suggestedRefinements: [],
  benchmarks: [BENCHMARK],
  measured: { metricId: "denial_rate", value: 29.5082, unit: "percent" },
};

const WARNINGS: WarningEvent[] = [
  {
    type: "warning",
    code: "POPULATION_CAVEAT",
    severity: "caution",
    structured: true,
    message:
      "population_caveat: denial_rate — claims still awaiting their first remittance (status OPEN) are excluded from both sides",
  },
  {
    type: "warning",
    code: "SUPPRESSION_APPLIED",
    severity: "info",
    structured: true,
    count: 1,
    message: "suppression: cells counting fewer than 11 entities are suppressed",
  },
];

describe("csvCell", () => {
  it("leaves a plain value alone", () => {
    expect(csvCell("Atlas Commercial")).toBe("Atlas Commercial");
    expect(csvCell(178216.82)).toBe("178216.82");
  });
  it("quotes commas, quotes and newlines", () => {
    expect(csvCell("Federal Medicare, General Surgery")).toBe(
      '"Federal Medicare, General Surgery"',
    );
    expect(csvCell('he said "no"')).toBe('"he said ""no"""');
    expect(csvCell("line one\nline two")).toBe('"line one\nline two"');
  });
  it("renders null and undefined as an empty cell, never as zero", () => {
    // A suppressed cell is not a zero. Exporting one as 0 puts a number
    // the engine deliberately withheld into a payer meeting.
    expect(csvCell(undefined)).toBe("");
    expect(csvCell(null)).toBe("");
  });
  it("defuses a value a spreadsheet would execute as a formula", () => {
    // These exports carry free-text titles and refusal sentences straight
    // off the wire; a cell starting with "=" runs on open in Excel.
    expect(csvCell("=1+1")).toBe("'=1+1");
    expect(csvCell("-lookup()")).toBe("'-lookup()");
    expect(csvCell("@SUM(A1)")).toBe("'@SUM(A1)");
  });
});

describe("buildCsv", () => {
  it("writes a header row and CRLF line endings", () => {
    expect(buildCsv(["a", "b"], [[1, "x"]])).toBe("a,b\r\n1,x\r\n");
  });
});

describe("answerToText", () => {
  const text = answerToText({
    question: "What is our denial rate by payer for July 2026?",
    header: HEADER,
    findings: [FINDING],
    narrative: "Among the payers that cleared reporting, State Medicaid MCO has the highest…",
    warnings: WARNINGS,
    investigationId: "inv_b8267d9b585a",
    copiedAt: new Date(2026, 7, 9, 14, 32),
  });

  it("states the window, the scope and the data load", () => {
    expect(text).toContain("Jul 1 – Jul 31, 2026 (service date)");
    expect(text).toContain("Scope: all — no filters applied");
    expect(text).toContain("Data as of: 2026-08-03 04:10");
  });

  it("carries the caveats with the numbers", () => {
    // The whole reason this function exists. A pasted answer that drops
    // the population caveat asserts a denial rate over a population the
    // engine explicitly bounded.
    expect(text).toContain("CAVEATS THAT TRAVEL WITH THESE NUMBERS");
    expect(text).toContain("How to read this number");
    expect(text).toContain("claims still awaiting their first remittance");
    expect(text).toContain("Small cells were suppressed");
  });

  it("says so explicitly when there are no caveats rather than omitting the section", () => {
    const clean = answerToText({
      header: HEADER,
      findings: [FINDING],
      narrative: "n",
      warnings: [],
    });
    expect(clean).toContain("The platform attached no caveats to this answer.");
  });

  it("carries each benchmark's cohort, review status and cautions", () => {
    expect(text).toContain("19–20 percent of in-network claims denied");
    expect(text).toContain("ACA marketplace (HealthCare.gov issuers)");
    expect(text).toContain("[unreviewed — machine-researched, not checked by a person]");
    expect(text).toContain("caution: Plan-reported data with no initial/final distinction");
    expect(text).toContain("source: Claims Denials and Appeals in ACA Marketplace Plans in 2024");
  });

  it("names the data load and the metric pack in the provenance line", () => {
    expect(text).toContain("investigation inv_b8267d9b585a");
    expect(text).toContain("data load wm_003 (2026-08-03 04:10)");
    expect(text).toContain("metric pack base-rcm@1.0.0");
    expect(text).toContain("copied 2026-08-09 14:32");
    expect(text).toContain("Re-running the same question against a newer load can change them.");
  });

  it("states the absence when the turn published no context header", () => {
    const headerless = answerToText({ findings: [FINDING], narrative: "n", warnings: [] });
    expect(headerless).toContain("This turn published no context header");
  });

  it("says a restored turn's write-up was not stored rather than leaving a gap", () => {
    const restored = answerToText({
      header: HEADER,
      findings: [FINDING],
      narrative: "",
      warnings: [],
      restored: true,
    });
    expect(restored).toContain("The written analysis was not stored for this turn");
    expect(restored).toContain("rebuilt from this session's stored history");
  });

  it("states an as-of date for a snapshot contract, never a range it did not use", () => {
    const snapshot = answerToText({
      header: { ...HEADER, asOf: "2026-08-02" },
      findings: [],
      narrative: "",
      warnings: [],
    });
    expect(snapshot).toContain("as of Aug 2, 2026");
    expect(snapshot).toContain("a balance at that moment, not a total over a range");
    expect(snapshot).not.toContain("Jul 1 – Jul 31");
  });
});

describe("windowLine", () => {
  it("is the resolved range for a flow metric", () => {
    expect(windowLine(HEADER)).toBe("Jul 1 – Jul 31, 2026 (service date)");
  });
  it("is the as-of date for a snapshot metric", () => {
    expect(windowLine({ ...HEADER, asOf: "2026-08-02" })).toContain("as of Aug 2, 2026");
  });
});

describe("portfolioToCsv", () => {
  const item: PortfolioItem = {
    rank: 1,
    referent: "ANM-021",
    title: "DNFB accumulation: Northgate general-surgery discharges",
    issueClass: "",
    impactCents: 17_821_682,
    impactLabel: "detected",
    detail: "22 unbilled discharges",
    provenance: "external_detection",
    priorityFormulaVersion: "anomaly_priority@2",
    sourceWatermarkId: "wm_003",
    severity: "critical",
    ageDays: 31,
    recoverableCentsEstimate: 16_930_598,
    actionabilityLabel: "highly recoverable",
    actionabilityRationale: "DNFB dollars are not lost, only unbilled.",
    priorityScore: 0.328589,
    lane: "value",
    reconciledImpactCents: 19_587_392,
    impactAgreement: "diverged",
    impactDeltaCents: 1_765_710,
    impactDeltaFraction: 0.099077,
    impactReconciliationNote: "The detection system reported $178,216.82 for this cell.",
    metricId: "dnfb_dollars",
    metricDisplayName: "Discharged not final billed (unbilled discharges)",
    windowStart: "2026-07-03",
    windowEnd: "2026-07-29",
    drillable: true,
  };

  it("carries every honesty column, in dollars a spreadsheet can sum", () => {
    const csv = portfolioToCsv({ items: [item], watermark: "wm_003" });
    const [header, row] = csv.trimEnd().split("\r\n");
    for (const column of [
      "impact_usd",
      "reconciled_impact_usd",
      "impact_agreement",
      "impact_delta_pct",
      "recoverable_usd",
      "actionability",
      "lane",
      "priority_score",
      "impact_reconciliation_note",
    ]) {
      expect(header).toContain(column);
    }
    // Cents → dollars, unformatted: "$178,217" is text to a spreadsheet.
    expect(row).toContain("178216.82");
    expect(row).toContain("195873.92");
    expect(row).toContain("diverged");
    expect(row).toContain("9.9077");
    expect(row).toContain("169305.98");
  });

  it("omits ranked_on while no card publishes it, and emits it the moment one does", () => {
    // A column that is always empty trains a reader to ignore it before
    // it ever means anything.
    expect(portfolioToCsv({ items: [item] })).not.toContain("ranked_on");
    // Live shape (ANM-001): the two figures disagree and the ORDERING used
    // this platform's, which is not derivable from the other columns —
    // a sheet carrying both numbers and a rank whose basis it does not
    // state is the same ambiguity the rail had.
    const ranked = portfolioToCsv({
      items: [
        {
          ...item,
          rankedOn: "platform",
          rankedImpactCents: 17_720_287,
          rankedOnNote:
            "ranked on this platform's re-derived figure ($177,202.87): the detection system's figure diverges from it.",
        },
      ],
    });
    expect(ranked).toContain("ranked_on");
    expect(ranked).toContain("ranked_impact_usd");
    expect(ranked).toContain("platform");
    expect(ranked).toContain("177202.87");
    expect(ranked).toContain("re-derived figure");
  });

  it("omits the dimension repoint column until a card carries one", () => {
    expect(portfolioToCsv({ items: [item] })).not.toContain("drill_dimension_repoint");
    // Live shape (ANM-013): the detector cuts at `proc_group`, which binds
    // on claim_line, so a claim-grain contract substitutes the claim's
    // dominant procedure group. Whoever re-derives this list from the ids
    // in it needs the substitution or their numbers will not match.
    const repointed = portfolioToCsv({
      items: [
        {
          ...item,
          drillDimensionRepoints: [
            {
              fromDimension: "proc_group",
              toDimension: "primary_proc_group",
              rationale:
                "Procedures bind at claim_line, so a claim-grain contract cannot be cut by `proc_group` at all.",
            },
          ],
        },
      ],
    });
    expect(repointed).toContain("drill_dimension_repoint");
    expect(repointed).toContain("proc_group→primary_proc_group");
  });

  it("keeps the platform's refusal on an un-drillable card", () => {
    const refused = portfolioToCsv({
      items: [
        {
          ...item,
          drillable: false,
          drillUnavailableReason:
            "GRAIN_INCOMPATIBLE: dimension 'proc_group' is not a legal scope dimension",
        },
      ],
    });
    expect(refused).toContain("GRAIN_INCOMPATIBLE");
    expect(refused).toContain("false");
  });
});

describe("chartToCsv", () => {
  const spec: ChartSpec = {
    id: "chart_main",
    kind: "bar",
    title: "Denial rate by payer",
    unit: "percent",
    xLabel: "payer",
    series: [{ key: "denial_rate", label: "denial rate", role: "current" }],
    rows: [
      { label: "Atlas Commercial", referent: "D1", values: { denial_rate: 8.169 } },
      { label: "State Medicaid MCO", referent: "D10", values: { denial_rate: 29.5082 } },
      // A suppressed cell: the engine published `value: null`.
      { label: "Federal Medicare", referent: "D3", values: {} },
    ],
  };

  it("exports the rows in the unit the chart draws them in", () => {
    const csv = chartToCsv(spec, { windowLabel: "Jul 2026" });
    const lines = csv.trimEnd().split("\r\n");
    expect(lines[0]).toBe("payer (Jul 2026),referent,denial rate (percent)");
    expect(lines[1]).toBe("Atlas Commercial,D1,8.169");
    expect(lines[2]).toBe("State Medicaid MCO,D10,29.5082");
  });

  it("exports a withheld cell as empty, never as zero", () => {
    const lines = chartToCsv(spec).trimEnd().split("\r\n");
    expect(lines[3]).toBe("Federal Medicare,D3,");
  });

  it("exports money in dollars, not integer cents", () => {
    const money: ChartSpec = {
      ...spec,
      unit: "cents",
      series: [{ key: "denied_dollars", label: "denied dollars", role: "current" }],
      rows: [{ label: "Atlas Commercial", values: { denied_dollars: 3_395_490 } }],
    };
    const lines = chartToCsv(money).trimEnd().split("\r\n");
    expect(lines[0]).toContain("denied dollars (usd)");
    expect(lines[1]).toBe("Atlas Commercial,,33954.9");
  });
});

describe("exportFilename", () => {
  it("slugs the tag and keeps the data load in the name", () => {
    expect(exportFilename("worklist", "wm_003", "csv")).toBe("revi-worklist-wm-003.csv");
    expect(exportFilename("chart", "Denial rate by payer", "csv")).toBe(
      "revi-chart-denial-rate-by-payer.csv",
    );
  });
  it("survives a tag with nothing usable in it", () => {
    expect(exportFilename("worklist", "///", "csv")).toBe("revi-worklist.csv");
    expect(exportFilename("worklist", undefined, "csv")).toBe("revi-worklist.csv");
  });
});

describe("chartToCsv — a scaled ratio does not leak its arithmetic", () => {
  it("erases the float epsilon a ×100 scaling introduces", () => {
    // The wire publishes 0.229167 as a `ratio`; the seam scales it to
    // percentage points and binary floating point makes that
    // 22.916700000000002, which is not a number anyone published.
    const spec: ChartSpec = {
      id: "chart_main",
      kind: "bar",
      title: "Denial rate by payer",
      unit: "percent",
      series: [{ key: "denial_rate", label: "denial rate", role: "current" }],
      rows: [{ label: "Pinnacle Health Plan", values: { denial_rate: 0.229167 * 100 } }],
    };
    expect(chartToCsv(spec)).toContain("22.9167");
    expect(chartToCsv(spec)).not.toContain("22.916700000000002");
  });
});
