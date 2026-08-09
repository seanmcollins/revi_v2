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

/*
 * R3-19: no export was both complete and caveated. This one carried the
 * caveats and three of twelve payers — with `WORKLIST_ATTACHED` surviving
 * into the text, so the pasted email announced a ranked worklist it did
 * not contain.
 */
describe("answerToText — complete AND caveated, or it is neither", () => {
  const CHART: ChartSpec = {
    id: "chart_main",
    kind: "bar",
    title: "Denial rate by payer",
    unit: "percent",
    xLabel: "payer",
    order: { basis: "value", by: "denial_rate", descending: true },
    series: [{ key: "denial_rate", label: "denial rate", role: "current" }],
    rows: [
      { label: "State Medicaid MCO", referent: "D10", values: { denial_rate: 29.5082 } },
      { label: "Atlas Commercial", referent: "D1", values: { denial_rate: 8.169 } },
      { label: "Federal Medicare", referent: "D3", values: {} },
      {
        label: "Cascade MA",
        referent: "D7",
        values: { denial_rate: 45.4545 },
        bounded: true,
      },
    ],
  };

  const text = answerToText({
    question: "What is our denial rate by payer for July 2026?",
    header: HEADER,
    findings: [FINDING],
    narrative: "State Medicaid MCO has the highest…",
    warnings: WARNINGS,
    charts: [CHART],
    copiedAt: new Date(2026, 7, 9, 14, 32),
  });

  it("carries every measured row, not just the ones written up", () => {
    expect(text).toContain("DATA — Denial rate by payer");
    expect(text).toContain("State Medicaid MCO: 29.5%");
    expect(text).toContain("Atlas Commercial: 8.2%");
    expect(text).toContain("4 rows × 1 series");
    expect(text).toContain("ordered by denial_rate high to low");
  });

  it("states the slice the findings are", () => {
    expect(text).toContain("1 of 4 measured rows are written up below");
  });

  it("keeps a withheld cell withheld and a bound a bound", () => {
    expect(text).toContain("Federal Medicare: (withheld)");
    expect(text).toContain("Cascade MA: ≤ 45.5%");
    expect(text).toContain("≤ marks an upper bound, not a measurement");
  });

  it("carries the worklist the caveats promise", () => {
    const withWorklist = answerToText({
      header: HEADER,
      findings: [],
      narrative: "",
      warnings: [],
      worklist: {
        matchedOn: "playbook",
        matchedId: "pb_denials",
        statement: "33 cards, 19 ranked on the detector's impact.",
        label: "What to work first",
        description: "",
        formulaVersion: "anomaly_priority@3",
        watermarkId: "wm_003",
        items: [
          {
            rank: 1,
            referent: "A12",
            title: "CARC 197 spike — Bluestone",
            ageDays: 3,
            impactCents: 1_200_000,
            recoverableCentsEstimate: 400_000,
            rankedOn: "detector",
            priorityScore: 0.82,
            priorityFormulaVersion: "anomaly_priority@3",
            drillable: true,
          } as PortfolioItem,
        ],
        lanes: [],
        totalItems: 33,
        limit: 8,
        totalRecoverableCentsEstimate: 400_000,
        warnings: [],
      },
    });
    expect(withWorklist).toContain("RANKED WORKLIST — What to work first");
    expect(withWorklist).toContain("1 of 33 cards listed here");
    expect(withWorklist).toContain("CARC 197 spike — Bluestone");
    expect(withWorklist).toContain("impact $12000");
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

  /** The table half of the document: everything below the `#` preamble. */
  const body = (csv: string): string[] =>
    csv
      .trimEnd()
      .split("\r\n")
      .filter((line) => !line.startsWith("#") && !line.startsWith('"#'));

  it("exports the rows in the unit the chart draws them in", () => {
    const lines = body(chartToCsv(spec, { windowLabel: "Jul 2026" }));
    expect(lines[0]).toBe("payer (Jul 2026),referent,denial rate (percent)");
    expect(lines[1]).toBe("Atlas Commercial,D1,8.169");
    expect(lines[2]).toBe("State Medicaid MCO,D10,29.5082");
  });

  it("exports a withheld cell as empty, never as zero", () => {
    expect(body(chartToCsv(spec))[3]).toBe("Federal Medicare,D3,");
  });

  it("exports money in dollars, not integer cents", () => {
    const money: ChartSpec = {
      ...spec,
      unit: "cents",
      series: [{ key: "denied_dollars", label: "denied dollars", role: "current" }],
      rows: [{ label: "Atlas Commercial", values: { denied_dollars: 3_395_490 } }],
    };
    const lines = body(chartToCsv(money));
    expect(lines[0]).toContain("denied dollars (usd)");
    expect(lines[1]).toBe("Atlas Commercial,,33954.9");
  });

  /*
   * The load-bearing half of R3-19: this file used to carry twelve rows and
   * no caveats, while the text copy carried the caveats and three rows.
   * Neither artifact was both complete and honest.
   */
  it("writes the caveats above the numbers, as comment lines", () => {
    const csv = chartToCsv(spec, {
      windowLabel: "Jul 2026",
      watermarkId: "wm_003",
      investigationId: "inv_42",
      caveats: [
        "[caution] Small cells withheld — 3 payers fall under the publication threshold",
      ],
      exportedAt: new Date(2026, 7, 9, 14, 32),
    });
    expect(csv.startsWith("# Revi — Denial rate by payer")).toBe(true);
    expect(csv).toContain("# Window: Jul 2026");
    expect(csv).toContain("# CAVEATS THAT TRAVEL WITH THESE NUMBERS");
    expect(csv).toContain("Small cells withheld");
    expect(csv).toContain("# Data as of: wm_003");
    expect(csv).toContain("investigation inv_42");
    expect(csv).toContain("exported 2026-08-09 14:32");
  });

  it("says so when the platform attached no caveats, rather than saying nothing", () => {
    expect(chartToCsv(spec)).toContain("The platform attached no caveats to this answer.");
  });

  it("defuses a caveat that would otherwise execute in Excel", () => {
    const csv = chartToCsv(spec, { caveats: ['=cmd|"/c calc"!A0'] });
    // Quoted as one cell, and behind the `#` marker — never a formula.
    expect(csv).toContain('"# =cmd|""/c calc""!A0"');
  });

  it("carries the bound and the denominator once the wire publishes them", () => {
    const bounded: ChartSpec = {
      ...spec,
      rows: [
        { label: "Atlas Commercial", referent: "D1", values: { denial_rate: 8.169 } },
        {
          label: "Cascade Medicare Advantage",
          referent: "D7",
          values: { denial_rate: 45.4545 },
          bounded: true,
          bound: 45.4545,
          denominator: 22,
        },
      ],
    };
    const lines = body(chartToCsv(bounded));
    expect(lines[0]).toBe(
      "payer,referent,denial rate (percent),bounded,bound (percent),denominator",
    );
    expect(lines[1]).toBe("Atlas Commercial,D1,8.169,,,");
    expect(lines[2]).toBe("Cascade Medicare Advantage,D7,45.4545,TRUE,45.4545,22");
    expect(chartToCsv(bounded)).toContain("UPPER BOUNDS, not measurements");
  });

  it("keeps the bound columns off a chart that carries no bounds", () => {
    expect(body(chartToCsv(spec))[0]).not.toContain("bounded");
  });

  it("states the ordering the rows are in", () => {
    const ranked: ChartSpec = {
      ...spec,
      order: { basis: "value", by: "denial_rate", descending: true },
    };
    expect(chartToCsv(ranked)).toContain("ordered by denial_rate high to low");
  });
});

describe("exportFilename", () => {
  it("slugs the tag and keeps the data load in the name", () => {
    expect(exportFilename("worklist", "wm_003", "csv", "wm_003")).toBe(
      "revi-worklist-wm-003.csv",
    );
    // Two exports of one chart at different loads are different documents
    // and must not overwrite one another in a downloads folder — which is
    // exactly what happened while nobody passed the watermark.
    expect(exportFilename("chart", "Denial rate by payer", "csv", "wm_003")).toBe(
      "revi-chart-denial-rate-by-payer-wm-003.csv",
    );
    expect(exportFilename("chart", "Denial rate by payer", "csv", "wm_004")).not.toBe(
      exportFilename("chart", "Denial rate by payer", "csv", "wm_003"),
    );
  });
  it("survives a tag with nothing usable in it", () => {
    expect(exportFilename("worklist", "///", "csv", undefined)).toBe("revi-worklist.csv");
    expect(exportFilename("worklist", undefined, "csv", undefined)).toBe("revi-worklist.csv");
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
