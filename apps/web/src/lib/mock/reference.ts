/**
 * The reference five-turn drill-down (design doc §10.3), scripted as typed
 * TurnEvent streams. Every dollar figure is taken from the generated
 * warehouse's answer key (data/answer_key.json, seed 20260807, snap_003,
 * watermark 2026-08-03 04:10) — never invented.
 *
 *   T1  "Why did cash decline last week?"        −12.7% · F1 State Medicaid · F2 Atlas
 *   T2  "Break that down by payer"               reconciliation: children sum to parent
 *   T3  top-3 payers CARC mix                    proxy-grade example, pinned cohort
 *   T4  "Compare that to Q1"                     cache-hit indicator
 *   T5  "Why do you say F2?"                     META: answered from trace, zero queries
 */

import type {
  ContextHeaderData,
  DataWatermark,
  Finding,
  MetricContractSummary,
  PackVersionRef,
  TurnEvent,
} from "@/lib/types";

export const WATERMARK: DataWatermark = {
  id: "wm_003",
  loadedAt: "2026-08-03 04:10",
  newestDataDate: "2026-08-02",
};

export const PACK: PackVersionRef = { packId: "base-rcm", version: "1.0.0" };

export const CASH_POSTED_CONTRACT: MetricContractSummary = {
  id: "cash_posted",
  version: 3,
  name: "Cash posted (payer)",
  kind: "FLOW",
  numerator: "Σ fact_transaction.amount_cents where txn_type = PAYMENT",
  primaryDateBasis: "post",
  exclusions: ["patient payments", "refunds"],
  unit: "cents",
  directionOfGood: "up_is_good",
  fingerprint: "9c41f2ab",
};

export const DENIED_DOLLARS_CONTRACT: MetricContractSummary = {
  id: "denied_dollars",
  version: 2,
  name: "Denied dollars",
  kind: "FLOW",
  numerator: "Σ fact_denial.denied_amount_cents (group code + CARC pair)",
  primaryDateBasis: "remit",
  exclusions: ["reversed denials"],
  unit: "cents",
  directionOfGood: "down_is_good",
  fingerprint: "5e88d0c3",
};

/* ------------------------------------------------------------------ */
/* Shared context pieces                                               */
/* ------------------------------------------------------------------ */

const T1_HEADER: ContextHeaderData = {
  window: { start: "2026-07-27", end: "2026-08-02", basis: "post", requested: "last week" },
  comparison: {
    kind: "prior_period",
    window: { start: "2026-07-20", end: "2026-07-26", basis: "post" },
  },
  filters: [],
  grain: { entity: "transaction", timeBucket: "week" },
  watermark: WATERMARK,
  packVersion: PACK,
};

const T3_HEADER: ContextHeaderData = {
  window: { start: "2026-07-27", end: "2026-08-02", basis: "remit", requested: "last week" },
  filters: [],
  cohort: {
    id: "c1",
    definition:
      "Top-3 payers by WoW cash decline: State Medicaid, Atlas Commercial, Meridian Health",
    pinned: true,
    originTurn: "T3",
    size: 3,
  },
  grain: { entity: "denial" },
  watermark: WATERMARK,
  packVersion: PACK,
};

const T4_HEADER: ContextHeaderData = {
  ...T3_HEADER,
  comparison: {
    kind: "custom",
    window: { start: "2026-01-01", end: "2026-03-31", basis: "remit" },
    label: "Q1 2026",
  },
};

/* ------------------------------------------------------------------ */
/* Findings (values in integer cents, straight from the answer key)    */
/* ------------------------------------------------------------------ */

const F1: Finding = {
  referent: { value: "F1", kind: "finding" },
  title: "State Medicaid cash down $99,093.08 — posting-timing gap",
  statement:
    "State Medicaid posted cash fell from $187,221.51 to $88,128.43 (−52.9%). Its remit→post lag stretched from ~4.1 to ~7.5 days for remits dated Jul 24 onward, pushing postings past the week boundary.",
  metricRefs: ["cash_posted"],
  values: {
    week_prior_cents: 18_722_151,
    week_decline_cents: 8_812_843,
    delta_cents: -9_909_308,
    lag_before_days: 4.1,
    lag_after_days: 7.5,
  },
  grade: "direct",
  impactCents: -9_909_308,
  impactKind: "delta",
  impactLabel: "WoW change",
  deltaPct: -0.5293,
  directionOfGood: "up_is_good",
  confidence: "high",
  comparison: {
    priorCents: 18_722_151,
    currentCents: 8_812_843,
    priorLabel: "Jul 20–26",
    currentLabel: "Jul 27–Aug 2",
  },
  suggestedRefinements: [
    { label: "Drill into State Medicaid", refinement: { op: "DrillInto", target: "F1" } },
    { label: "Explain this", refinement: { op: "Explain", target: "F1" } },
    {
      label: "Compare to Q1",
      refinement: {
        op: "SetComparison",
        comparison: {
          kind: "custom",
          window: { start: "2026-01-01", end: "2026-03-31", basis: "post" },
          label: "Q1 2026",
        },
      },
    },
  ],
};

const F2: Finding = {
  referent: { value: "F2", kind: "finding" },
  title: "Atlas Commercial cash down $48,940.41 — volume-led",
  statement:
    "Atlas Commercial posted cash fell from $312,245.27 to $263,304.86 (−15.7%). Submission volume dropped ~20% beginning the week of Jul 13; remits lag submissions, so the cash gap lands in the decline week.",
  metricRefs: ["cash_posted"],
  values: {
    week_prior_cents: 31_224_527,
    week_decline_cents: 26_330_486,
    delta_cents: -4_894_041,
    submissions_wk_jul06: 235,
    submissions_wk_jul13: 204,
  },
  grade: "direct",
  impactCents: -4_894_041,
  impactKind: "delta",
  impactLabel: "WoW change",
  deltaPct: -0.1567,
  directionOfGood: "up_is_good",
  confidence: "high",
  comparison: {
    priorCents: 31_224_527,
    currentCents: 26_330_486,
    priorLabel: "Jul 20–26",
    currentLabel: "Jul 27–Aug 2",
  },
  suggestedRefinements: [
    { label: "Drill into Atlas Commercial", refinement: { op: "DrillInto", target: "F2" } },
    { label: "Explain this", refinement: { op: "Explain", target: "F2" } },
  ],
};

const F3: Finding = {
  referent: { value: "F3", kind: "finding" },
  title: "Patient cash essentially flat (−1.1%)",
  statement:
    "Patient-source cash was $161,959.68 vs $163,805.20 the prior week (−1.1%). The decline is payer-side, not patient-side.",
  metricRefs: ["patient_cash_posted"],
  values: {
    week_prior_cents: 16_380_520,
    week_decline_cents: 16_195_968,
    delta_cents: -184_552,
  },
  grade: "derived",
  impactCents: -184_552,
  impactKind: "delta",
  impactLabel: "WoW change",
  deltaPct: -0.0113,
  directionOfGood: "up_is_good",
  confidence: "high",
  comparison: {
    priorCents: 16_380_520,
    currentCents: 16_195_968,
    priorLabel: "Jul 20–26",
    currentLabel: "Jul 27–Aug 2",
  },
  suggestedRefinements: [
    { label: "Pivot to patient cash", refinement: { op: "Pivot", measures: ["patient_cash_posted"] } },
  ],
};

/** All 12 payer rows, sorted by delta ascending (answer key by_payer). */
export const PAYER_ROWS = [
  { referent: "F6", payer: "State Medicaid", prior: 18_722_151, current: 8_812_843 },
  { referent: "F7", payer: "Atlas Commercial", prior: 31_224_527, current: 26_330_486 },
  { referent: "F8", payer: "Meridian Health", prior: 18_419_647, current: 14_613_237 },
  { referent: "F9", payer: "Summit Peak Medicare Advantage", prior: 8_294_939, current: 6_492_297 },
  { referent: "F10", payer: "Silverline Medicare Advantage", prior: 12_541_163, current: 11_173_021 },
  { referent: "F11", payer: "State Medicaid MCO", prior: 6_532_275, current: 5_313_058 },
  { referent: "F12", payer: "Veritas Comp Fund", prior: 4_697_184, current: 3_593_489 },
  { referent: "F13", payer: "Northbridge Commercial", prior: 15_204_340, current: 14_620_806 },
  { referent: "F14", payer: "Lakewood Medicaid MCO", prior: 5_039_732, current: 5_409_818 },
  { referent: "F15", payer: "Bluestone Mutual", prior: 9_773_174, current: 10_734_821 },
  { referent: "F16", payer: "Federal Medicare", prior: 15_947_514, current: 17_932_498 },
  { referent: "F17", payer: "Pinnacle Health Plan", prior: 5_800_085, current: 7_817_778 },
] as const;

const F4: Finding = {
  referent: { value: "F4", kind: "finding" },
  title: "Three payers drive 96% of the decline",
  statement:
    "State Medicaid (−$99,093.08), Atlas Commercial (−$48,940.41) and Meridian Health (−$38,064.10) sum to −$186,097.59 — 96.2% of the net decline.",
  metricRefs: ["cash_posted"],
  values: {
    top3_delta_cents: -18_609_759,
    parent_delta_cents: -19_352_579,
    share_of_decline: 0.962,
  },
  grade: "direct",
  impactCents: -18_609_759,
  impactKind: "delta",
  impactLabel: "top-3 payers, WoW",
  deltaPct: -0.122,
  directionOfGood: "up_is_good",
  confidence: "high",
  suggestedRefinements: [
    { label: "Drill into top 3", refinement: { op: "DrillInto", target: ["F6", "F7", "F8"] } },
    { label: "Rank by delta", refinement: { op: "RankBy", metric: "cash_posted", descending: false } },
  ],
};

const F5: Finding = {
  referent: { value: "F5", kind: "finding" },
  title: "Partial offsets: Pinnacle +34.8%, Federal Medicare +12.4%",
  statement:
    "Two payers moved the other way: Pinnacle Health Plan +$20,176.93 (+34.8%) and Federal Medicare +$19,849.84 (+12.4%), masking deeper drops in the total.",
  metricRefs: ["cash_posted"],
  values: {
    pinnacle_delta_cents: 2_017_693,
    federal_medicare_delta_cents: 1_984_984,
  },
  grade: "direct",
  impactCents: 4_002_677,
  impactKind: "delta",
  impactLabel: "combined offset, WoW",
  deltaPct: 0.184,
  directionOfGood: "up_is_good",
  confidence: "high",
  suggestedRefinements: [
    { label: "Drill into Pinnacle", refinement: { op: "DrillInto", target: "F17" } },
  ],
};

/** Decline-week CARC mix, combined top-3 cohort (snap_003 v_denial). */
export const CARC_MIX_ROWS = [
  { label: "CO·27", cents: 1_243_992, events: 3, q1Cents: 7_512_260 },
  { label: "CO·97", cents: 839_984, events: 3, q1Cents: 4_749_847 },
  { label: "CO·96", cents: 772_592, events: 3, q1Cents: 10_346_120 },
  { label: "CO·204", cents: 446_962, events: 2, q1Cents: 7_505_012 },
  { label: "CO·4", cents: 306_478, events: 2, q1Cents: 5_341_815 },
  { label: "OA·109", cents: 267_292, events: 2, q1Cents: 4_615_484 },
  { label: "CO·11", cents: 236_887, events: 2, q1Cents: 2_697_855 },
  { label: "CO·197", cents: 228_270, events: 5, q1Cents: 6_059_251 },
] as const;

export const CARC_WEEK_TOTAL_CENTS = 5_202_878;
export const CARC_WEEK_EVENTS = 39;
export const CARC_Q1_TOTAL_CENTS = 127_078_886;
export const CARC_Q1_EVENTS = 607;

const F18: Finding = {
  referent: { value: "F18", kind: "finding" },
  title: "CO·27 (coverage terminated) leads the denial mix",
  statement:
    "CO·27 accounts for $12,439.92 of the cohort's $52,028.78 denied this week — 23.9% of denied dollars across 3 events, concentrated in State Medicaid.",
  metricRefs: ["denied_dollars"],
  values: {
    co27_cents: 1_243_992,
    cohort_total_cents: 5_202_878,
    share: 0.239,
    events: 3,
  },
  grade: "direct",
  impactCents: 1_243_992,
  impactKind: "level",
  impactLabel: "denied this week",
  directionOfGood: "down_is_good",
  confidence: "medium",
  suggestedRefinements: [
    { label: "Drill into CO·27", refinement: { op: "DrillInto", target: "carc:CO-27" } },
    { label: "Row evidence", refinement: { op: "Explain", target: "F18" } },
  ],
};

const F19: Finding = {
  referent: { value: "F19", kind: "finding" },
  title: "CO·197 (prior auth) most frequent, small dollars",
  statement:
    "CO·197 appears in 5 of 39 denial events — the most frequent code — but only $2,282.70 in dollars. Frequency and dollars disagree; both views matter.",
  metricRefs: ["denied_dollars"],
  values: { co197_cents: 228_270, events: 5, total_events: 39 },
  grade: "proxy",
  impactCents: 228_270,
  impactKind: "level",
  impactLabel: "denied this week",
  directionOfGood: "down_is_good",
  confidence: "medium",
  suggestedRefinements: [
    { label: "Drill into CO·197", refinement: { op: "DrillInto", target: "carc:CO-197" } },
  ],
};

const F20: Finding = {
  referent: { value: "F20", kind: "finding" },
  title: "CO·27 share is 4.0× its Q1 baseline",
  statement:
    "CO·27 is 23.9% of denied dollars this week vs 5.9% across Q1 2026 — a 4.0× elevation. CO·97 shows the same pattern at 4.3× (16.1% vs 3.7%).",
  metricRefs: ["denied_dollars"],
  values: {
    week_share: 0.239,
    q1_share: 0.059,
    ratio: 4.0,
  },
  grade: "proxy",
  impactDisplay: "4.0×",
  impactKind: "level",
  impactLabel: "share vs Q1 baseline",
  directionOfGood: "down_is_good",
  confidence: "medium",
  suggestedRefinements: [
    { label: "Drill into CO·27", refinement: { op: "DrillInto", target: "carc:CO-27" } },
  ],
};

const F21: Finding = {
  referent: { value: "F21", kind: "finding" },
  title: "CO·16 and CO·18 running well under Q1",
  statement:
    "Administrative codes collapsed against baseline: CO·16 is 2.9% of denied dollars vs 13.1% in Q1; CO·18 is 2.7% vs 9.6%. The mix shifted toward eligibility/coverage codes.",
  metricRefs: ["denied_dollars"],
  values: {
    co16_week_share: 0.029,
    co16_q1_share: 0.131,
    co18_week_share: 0.027,
    co18_q1_share: 0.096,
  },
  grade: "proxy",
  impactDisplay: "0.2×",
  impactKind: "level",
  impactLabel: "CO·16 share vs Q1",
  directionOfGood: "neutral",
  confidence: "medium",
  suggestedRefinements: [
    { label: "Drill into CO·16", refinement: { op: "DrillInto", target: "carc:CO-16" } },
  ],
};

/* ------------------------------------------------------------------ */
/* Weekly cash series for the T1 chart (snap_003 fact_transaction)     */
/* ------------------------------------------------------------------ */

export const WEEKLY_PAYER_CASH = [
  { week: "May 25", cents: 161_917_568 },
  { week: "Jun 1", cents: 154_155_455 },
  { week: "Jun 8", cents: 148_868_935 },
  { week: "Jun 15", cents: 152_845_928 },
  { week: "Jun 22", cents: 157_469_146 },
  { week: "Jun 29", cents: 160_754_232 },
  { week: "Jul 6", cents: 140_979_962 },
  { week: "Jul 13", cents: 151_728_917 },
  { week: "Jul 20", cents: 152_196_731 },
  { week: "Jul 27", cents: 132_844_152 },
] as const;

export const ATLAS_SUBMISSIONS_BY_WEEK = [
  { week: "Jun 15", count: 274 },
  { week: "Jun 22", count: 249 },
  { week: "Jun 29", count: 283 },
  { week: "Jul 6", count: 235 },
  { week: "Jul 13", count: 204 },
  { week: "Jul 20", count: 203 },
  { week: "Jul 27", count: 225 },
] as const;

/* ------------------------------------------------------------------ */
/* Scripted turns                                                      */
/* ------------------------------------------------------------------ */

export interface ScriptedTurn {
  id: string;
  question: string;
  trigger: RegExp;
  events: TurnEvent[];
}

const T1_EVENTS: TurnEvent[] = [
  { type: "stage", stage: "classified", status: "completed", detail: "NEW_INVESTIGATION · 0.98" },
  { type: "stage", stage: "interpreted", status: "started" },
  {
    type: "interpretation",
    interpretation: {
      metric: { id: "cash_posted", name: "Cash posted (payer)", version: 3 },
      windowDescription: "“last week” → Jul 27 – Aug 2, 2026, resolved at plan time (post date, calendar week)",
      comparisonDescription: "prior period → Jul 20 – 26, 2026",
      filterDescriptions: ["scope: all (no filters)"],
      synonymMappings: [
        {
          from: "cash",
          to: "cash_posted@3",
          note: "payer-source posted payments; patient payments tracked separately",
        },
      ],
      playbook: "cash_decline",
      appliedOperators: [],
    },
  },
  { type: "stage", stage: "interpreted", status: "completed", detail: "cash → cash_posted@3" },
  { type: "context_header", header: T1_HEADER, turnClass: "new_investigation" },
  { type: "stage", stage: "planned", status: "completed", detail: "playbook cash_decline · 5 probes" },
  { type: "stage", stage: "validated", status: "completed", detail: "plan valid" },
  { type: "stage", stage: "executing", status: "started", probesDone: 0, probesTotal: 5, cacheHits: 0 },
  { type: "stage", stage: "executing", status: "started", probesDone: 2, probesTotal: 5, cacheHits: 0 },
  { type: "stage", stage: "executing", status: "started", probesDone: 4, probesTotal: 5, cacheHits: 0 },
  { type: "stage", stage: "executing", status: "completed", probesDone: 5, probesTotal: 5, cacheHits: 0 },
  { type: "stage", stage: "calculating", status: "completed", detail: "compare · delta · lag_distribution" },
  { type: "stage", stage: "reconciled", status: "completed", detail: "n/a — no decomposition this turn" },
  { type: "finding", finding: F1 },
  { type: "finding", finding: F2 },
  { type: "finding", finding: F3 },
  {
    type: "chart_spec",
    spec: {
      id: "t1_weekly_cash",
      kind: "line",
      title: "Payer cash posted by week",
      unit: "cents",
      series: [{ key: "cents", label: "Payer cash", role: "current" }],
      rows: WEEKLY_PAYER_CASH.map((w) => ({ label: w.week, values: { cents: w.cents } })),
      highlightLabel: "Jul 27",
      xLabel: "week beginning (post date)",
    },
  },
  {
    type: "evidence",
    evidence: {
      zeroProbeTurn: false,
      probes: [
        {
          probeId: "p_t1_1",
          probeHash: "a3f2c9d1",
          kind: "aggregation",
          description: "Payer cash, decline week vs prior week (post basis)",
          contract: { id: "cash_posted", version: 3 },
          operators: [
            { name: "compare", version: "1.2.0" },
            { name: "delta", version: "1.0.0" },
          ],
          cacheHit: false,
          rowCount: 2,
          truncated: false,
          suppressedCells: 0,
          grade: "direct",
        },
        {
          probeId: "p_t1_2",
          probeHash: "b7e40a52",
          kind: "aggregation",
          description: "Patient cash, both weeks",
          contract: { id: "patient_cash_posted", version: 2 },
          operators: [{ name: "compare", version: "1.2.0" }],
          cacheHit: false,
          rowCount: 2,
          truncated: false,
          suppressedCells: 0,
          grade: "direct",
        },
        {
          probeId: "p_t1_3",
          probeHash: "c91d3e08",
          kind: "aggregation",
          description: "State Medicaid remit→post lag distribution, Jul 6 – Aug 2",
          operators: [{ name: "lag_distribution", version: "0.3.0" }],
          cacheHit: false,
          rowCount: 2,
          truncated: false,
          suppressedCells: 0,
          grade: "derived",
        },
        {
          probeId: "p_t1_4",
          probeHash: "77b1e0aa",
          kind: "aggregation",
          description: "Atlas Commercial submission volume by week, Jun 15 – Aug 2",
          operators: [{ name: "trend", version: "1.0.0" }],
          cacheHit: false,
          rowCount: 7,
          truncated: false,
          suppressedCells: 0,
          grade: "direct",
        },
        {
          probeId: "p_t1_5",
          probeHash: "912fc6b3",
          kind: "row_evidence",
          description: "Masked transaction sample, decline week",
          operators: [{ name: "reservoir_sample", version: "1.0.0" }],
          cacheHit: false,
          rowCount: 3,
          truncated: false,
          suppressedCells: 0,
          grade: "direct",
        },
      ],
      reconciliation: {
        status: "not_applicable",
        detail: "Single-measure comparison — no decomposition to reconcile this turn.",
      },
      sampleRows: {
        columns: ["txn_id", "claim_id", "patient", "payer", "amount", "post_date"],
        maskedColumns: ["patient"],
        purpose: "analyst spot-check of posted payments (purpose recorded in trace)",
        rows: [
          ["TXN-4471982", "CLM-008841…", "▒▒▒▒▒▒", "State Medicaid", "$412.18", "2026-07-28"],
          ["TXN-4472136", "CLM-011209…", "▒▒▒▒▒▒", "Atlas Commercial", "$1,286.44", "2026-07-29"],
          ["TXN-4473055", "CLM-009476…", "▒▒▒▒▒▒", "Federal Medicare", "$734.02", "2026-07-31"],
        ],
      },
    },
  },
  { type: "stage", stage: "narrating", status: "started" },
  {
    type: "narrative_delta",
    text:
      "Payer cash posted fell $193,525.79 (−12.7%) week-over-week: $1,328,441.52 posted Jul 27 – Aug 2 against $1,521,967.31 the prior week. Two payers explain 76% of it. [F1] State Medicaid fell $99,093.08 (−52.9%); its remit-to-post lag stretched from ~4.1 to ~7.5 days for remits dated Jul 24 onward — a posting-timing gap, not lost revenue, and it should reverse as postings catch up. [F2] Atlas Commercial fell $48,940.41 (−15.7%) on a submission-volume drop of roughly 20% that began the week of Jul 13 — a leading-indicator decline worth a look at charge flow. [F3] Patient cash was essentially flat (−1.1%), so the decline is payer-side.",
  },
  {
    type: "turn_complete",
    investigationId: "inv_t1",
    status: "complete",
    answerGrade: "direct",
    metric: CASH_POSTED_CONTRACT,
  },
];

const T2_EVENTS: TurnEvent[] = [
  { type: "stage", stage: "classified", status: "completed", detail: "REFINEMENT · 0.97" },
  {
    type: "interpretation",
    interpretation: {
      metric: { id: "cash_posted", name: "Cash posted (payer)", version: 3 },
      windowDescription: "kept: Jul 27 – Aug 2, 2026 (post date)",
      comparisonDescription: "kept: vs Jul 20 – 26, 2026",
      filterDescriptions: ["scope: all (no filters)"],
      synonymMappings: [
        { from: "that", to: "cash_posted@3", note: "resolves to the investigation's primary measure" },
      ],
      planDiff: [
        "kept window Jul 27 – Aug 2 (post date)",
        "kept comparison vs Jul 20 – 26",
        "added dimension: payer — from this turn",
      ],
      appliedOperators: ["SetDimensions(payer)"],
    },
  },
  { type: "stage", stage: "interpreted", status: "completed", detail: "SetDimensions(payer)" },
  { type: "context_header", header: T1_HEADER, turnClass: "refinement" },
  { type: "stage", stage: "planned", status: "completed", detail: "plan diff: 1 changed probe" },
  { type: "stage", stage: "validated", status: "completed", detail: "payer is a certified cut for cash_posted" },
  { type: "stage", stage: "executing", status: "started", probesDone: 0, probesTotal: 1, cacheHits: 1 },
  {
    type: "stage",
    stage: "executing",
    status: "completed",
    probesDone: 1,
    probesTotal: 1,
    cacheHits: 1,
    detail: "parent totals served from evidence cache",
  },
  { type: "stage", stage: "calculating", status: "completed", detail: "compare · delta · reconcile" },
  {
    type: "stage",
    stage: "reconciled",
    status: "completed",
    detail: "12 payer rows sum to parent delta — PASSED",
  },
  { type: "finding", finding: F4 },
  { type: "finding", finding: F5 },
  {
    type: "chart_spec",
    spec: {
      id: "t2_payer_breakdown",
      kind: "grouped_bar",
      title: "Payer cash by payer — decline week vs prior",
      unit: "cents",
      series: [
        { key: "current", label: "Jul 27–Aug 2", role: "current" },
        { key: "prior", label: "Jul 20–26", role: "baseline" },
      ],
      rows: PAYER_ROWS.slice(0, 8).map((p) => ({
        label: p.payer,
        referent: p.referent,
        values: { current: p.current, prior: p.prior },
      })),
      truncation: { shown: 8, total: 12 },
    },
  },
  {
    type: "evidence",
    evidence: {
      zeroProbeTurn: false,
      probes: [
        {
          probeId: "p_t2_1",
          probeHash: "d4a91b77",
          kind: "aggregation",
          description: "Payer cash by payer × week (both windows)",
          contract: { id: "cash_posted", version: 3 },
          operators: [
            { name: "compare", version: "1.2.0" },
            { name: "reconcile", version: "1.1.0" },
          ],
          cacheHit: false,
          rowCount: 24,
          truncated: false,
          suppressedCells: 0,
          grade: "direct",
        },
        {
          probeId: "p_t1_1",
          probeHash: "a3f2c9d1",
          kind: "aggregation",
          description: "Parent totals (reused from T1)",
          contract: { id: "cash_posted", version: 3 },
          operators: [{ name: "compare", version: "1.2.0" }],
          cacheHit: true,
          rowCount: 2,
          truncated: false,
          suppressedCells: 0,
          grade: "direct",
        },
      ],
      reconciliation: {
        status: "passed",
        detail: "12 payer deltas sum to the parent decline exactly (tolerance 0¢).",
        parentCents: -19_352_579,
        childSumCents: -19_352_579,
      },
    },
  },
  { type: "stage", stage: "narrating", status: "started" },
  {
    type: "narrative_delta",
    text:
      "The twelve payer rows reconcile exactly to the parent decline of $193,525.79. [F4] Three payers — State Medicaid, Atlas Commercial and Meridian Health — account for $186,097.59 of it, 96% of the net decline. [F5] Two payers moved the other way: Pinnacle Health Plan +$20,176.93 (+34.8%) and Federal Medicare +$19,849.84 (+12.4%), partially masking the drop in the total.",
  },
  {
    type: "turn_complete",
    investigationId: "inv_t2",
    status: "complete",
    answerGrade: "direct",
    metric: CASH_POSTED_CONTRACT,
  },
];

const T3_EVENTS: TurnEvent[] = [
  { type: "stage", stage: "classified", status: "completed", detail: "REFINEMENT · 0.95" },
  {
    type: "interpretation",
    interpretation: {
      metric: { id: "denied_dollars", name: "Denied dollars", version: 2 },
      windowDescription: "kept dates Jul 27 – Aug 2; basis follows denied_dollars@2 → remit date",
      comparisonDescription: "dropped (measure pivot)",
      filterDescriptions: ["cohort: top-3 payers by WoW cash decline (pinned)"],
      synonymMappings: [
        { from: "CARC mix", to: "denied_dollars by group code + CARC pair" },
        { from: "top three payers", to: "DrillInto F6, F7, F8 → pinned cohort c1" },
      ],
      planDiff: [
        "kept window dates Jul 27 – Aug 2; basis → remit (contract primary)",
        "pinned cohort c1: 3 payers (from turn 3)",
        "pivoted measures → denied_dollars@2",
        "set dimension: group code + CARC",
      ],
      appliedOperators: ["DrillInto(F6,F7,F8)", "Pivot(denied_dollars)", "SetDimensions(carc)"],
    },
  },
  { type: "stage", stage: "interpreted", status: "completed", detail: "DrillInto · Pivot · SetDimensions" },
  { type: "context_header", header: T3_HEADER, turnClass: "refinement" },
  { type: "stage", stage: "planned", status: "completed", detail: "2 probes · cohort c1 materialized" },
  {
    type: "stage",
    stage: "validated",
    status: "completed",
    detail: "CARC is a legal cut at denial grain (line-grain law)",
  },
  { type: "stage", stage: "executing", status: "started", probesDone: 0, probesTotal: 2, cacheHits: 0 },
  { type: "stage", stage: "executing", status: "completed", probesDone: 2, probesTotal: 2, cacheHits: 0 },
  { type: "stage", stage: "calculating", status: "completed", detail: "top_k · share_of_total" },
  {
    type: "stage",
    stage: "reconciled",
    status: "completed",
    detail: "17 CARC rows sum to cohort total — PASSED",
  },
  {
    type: "warning",
    code: "PROXY_GRADE_INPUT",
    message:
      "State Medicaid submits no claim-level denial feed; its denial dollars are inferred from CO/OA adjustment groups on the 835. Per the grade law, this answer carries PROXY grade.",
    severity: "caution",
  },
  { type: "finding", finding: F18 },
  { type: "finding", finding: F19 },
  {
    type: "chart_spec",
    spec: {
      id: "t3_carc_mix",
      kind: "bar",
      title: "Denied dollars by CARC — pinned top-3 cohort, Jul 27–Aug 2",
      unit: "cents",
      series: [{ key: "cents", label: "Denied dollars", role: "current" }],
      rows: CARC_MIX_ROWS.map((c) => ({
        label: c.label,
        values: { cents: c.cents },
      })),
      truncation: { shown: 8, total: 17 },
    },
  },
  {
    type: "evidence",
    evidence: {
      zeroProbeTurn: false,
      probes: [
        {
          probeId: "p_t3_1",
          probeHash: "e8c02f19",
          kind: "aggregation",
          description: "Denied dollars by group+CARC — cohort c1, decline week (remit basis)",
          contract: { id: "denied_dollars", version: 2 },
          operators: [
            { name: "top_k", version: "1.0.0" },
            { name: "share_of_total", version: "1.1.0" },
          ],
          cacheHit: false,
          rowCount: 17,
          truncated: false,
          suppressedCells: 0,
          grade: "proxy",
        },
        {
          probeId: "p_t3_2",
          probeHash: "6b5d20e4",
          kind: "row_evidence",
          description: "Masked denial sample, cohort c1",
          operators: [{ name: "reservoir_sample", version: "1.0.0" }],
          cacheHit: false,
          rowCount: 3,
          truncated: false,
          suppressedCells: 0,
          grade: "direct",
        },
      ],
      reconciliation: {
        status: "passed",
        detail: "17 CARC rows sum to the cohort denial total $52,028.78 (tolerance 0¢).",
        parentCents: 5_202_878,
        childSumCents: 5_202_878,
      },
      sampleRows: {
        columns: ["denial_id", "claim_id", "patient", "payer", "group·CARC", "denied"],
        maskedColumns: ["patient"],
        purpose: "verify CARC coding of cohort denials (purpose recorded in trace)",
        rows: [
          ["DNL-220148", "CLM-004182…", "▒▒▒▒▒▒", "State Medicaid", "CO·27", "$5,120.33"],
          ["DNL-220305", "CLM-007751…", "▒▒▒▒▒▒", "Meridian Health", "CO·96", "$2,251.09"],
          ["DNL-220419", "CLM-002960…", "▒▒▒▒▒▒", "Atlas Commercial", "CO·197", "$197.58"],
        ],
      },
    },
  },
  { type: "stage", stage: "narrating", status: "started" },
  {
    type: "narrative_delta",
    text:
      "Across the pinned top-3 cohort, $52,028.78 in denials landed in the decline week over 39 events. [F18] CO·27 (coverage terminated) leads the mix at $12,439.92 — 23.9% of denied dollars — concentrated in State Medicaid. [F19] CO·197 (prior authorization) is the most frequent code at 5 of 39 events but only $2,282.70. Note: State Medicaid's denial amounts are proxy-grade — inferred from CO/OA adjustment groups, not a claim-level denial feed — so this answer carries proxy grade under the grade law.",
  },
  {
    type: "turn_complete",
    investigationId: "inv_t3",
    status: "complete",
    answerGrade: "proxy",
    metric: DENIED_DOLLARS_CONTRACT,
  },
];

const T4_EVENTS: TurnEvent[] = [
  { type: "stage", stage: "classified", status: "completed", detail: "REFINEMENT · 0.96" },
  {
    type: "interpretation",
    interpretation: {
      metric: { id: "denied_dollars", name: "Denied dollars", version: 2 },
      windowDescription: "kept: Jul 27 – Aug 2, 2026 (remit date)",
      comparisonDescription: "“Q1” → Jan 1 – Mar 31, 2026, re-anchored from stored concrete dates",
      filterDescriptions: ["cohort: top-3 payers (pinned, carried)"],
      synonymMappings: [{ from: "that", to: "the CARC mix from turn 3" }],
      planDiff: [
        "kept window, cohort, dimensions",
        "set comparison: CUSTOM → Q1 2026 (Jan 1 – Mar 31)",
        "primary-side frames unchanged → evidence cache",
      ],
      appliedOperators: ["SetComparison(CUSTOM, Q1 2026)"],
    },
  },
  { type: "stage", stage: "interpreted", status: "completed", detail: "SetComparison(CUSTOM, Q1)" },
  { type: "context_header", header: T4_HEADER, turnClass: "refinement" },
  { type: "stage", stage: "planned", status: "completed", detail: "1 new probe (comparison side only)" },
  { type: "stage", stage: "validated", status: "completed" },
  {
    type: "stage",
    stage: "executing",
    status: "started",
    probesDone: 0,
    probesTotal: 2,
    cacheHits: 1,
    detail: "primary side served from evidence cache",
  },
  { type: "stage", stage: "executing", status: "completed", probesDone: 2, probesTotal: 2, cacheHits: 1 },
  { type: "stage", stage: "calculating", status: "completed", detail: "share_of_total · compare" },
  {
    type: "stage",
    stage: "reconciled",
    status: "completed",
    detail: "shares sum to 100% on both sides — PASSED",
  },
  {
    type: "warning",
    code: "SMALL_DENOMINATOR",
    message:
      "This week has 39 denial events vs 607 in Q1 — shares move in large steps. Treat the mix shift as a lead, not a conclusion.",
    severity: "info",
  },
  { type: "finding", finding: F20 },
  { type: "finding", finding: F21 },
  {
    type: "chart_spec",
    spec: {
      id: "t4_carc_vs_q1",
      kind: "grouped_bar",
      title: "Share of denied dollars — decline week vs Q1 2026",
      unit: "percent",
      series: [
        { key: "week", label: "Jul 27–Aug 2", role: "current" },
        { key: "q1", label: "Q1 2026", role: "baseline" },
      ],
      rows: [
        { label: "CO·27", values: { week: 23.9, q1: 5.9 } },
        { label: "CO·97", values: { week: 16.1, q1: 3.7 } },
        { label: "CO·96", values: { week: 14.9, q1: 8.1 } },
        { label: "CO·204", values: { week: 8.6, q1: 5.9 } },
        { label: "CO·4", values: { week: 5.9, q1: 4.2 } },
        { label: "OA·109", values: { week: 5.1, q1: 3.6 } },
        { label: "CO·197", values: { week: 4.4, q1: 4.8 } },
        { label: "CO·16", values: { week: 2.9, q1: 13.1 } },
      ],
      truncation: { shown: 8, total: 17 },
    },
  },
  {
    type: "evidence",
    evidence: {
      zeroProbeTurn: false,
      probes: [
        {
          probeId: "p_t4_1",
          probeHash: "f19ab3c4",
          kind: "aggregation",
          description: "Denied dollars by group+CARC — cohort c1, Q1 2026 (comparison side)",
          contract: { id: "denied_dollars", version: 2 },
          operators: [{ name: "share_of_total", version: "1.1.0" }],
          cacheHit: false,
          rowCount: 19,
          truncated: false,
          suppressedCells: 0,
          grade: "direct",
        },
        {
          probeId: "p_t3_1",
          probeHash: "e8c02f19",
          kind: "aggregation",
          description: "Primary side (decline week) — evidence cache hit",
          contract: { id: "denied_dollars", version: 2 },
          operators: [{ name: "share_of_total", version: "1.1.0" }],
          cacheHit: true,
          rowCount: 17,
          truncated: false,
          suppressedCells: 0,
          grade: "proxy",
        },
      ],
      reconciliation: {
        status: "passed",
        detail: "Shares sum to 100% on both comparison sides (mod rounding).",
      },
    },
  },
  { type: "stage", stage: "narrating", status: "started" },
  {
    type: "narrative_delta",
    text:
      "Against the Q1 2026 baseline — 607 denial events, $1,270,788.86 denied — this week's mix is unusual. [F20] CO·27's share of denied dollars is 4.0× its Q1 baseline (23.9% vs 5.9%), and CO·97 runs 4.3× (16.1% vs 3.7%). [F21] The administrative codes went the other way: CO·16 is at 2.9% vs 13.1% and CO·18 at 2.7% vs 9.6% — the mix shifted toward eligibility and coverage codes. With only 39 events this week, shares move in large steps; treat this as a lead.",
  },
  {
    type: "turn_complete",
    investigationId: "inv_t4",
    status: "complete",
    answerGrade: "proxy",
    metric: DENIED_DOLLARS_CONTRACT,
  },
];

const T5_EVENTS: TurnEvent[] = [
  { type: "stage", stage: "classified", status: "completed", detail: "META · 0.99" },
  {
    type: "interpretation",
    interpretation: {
      metric: { id: "cash_posted", name: "Cash posted (payer)", version: 3 },
      windowDescription: "context of F2 (turn 1): Jul 27 – Aug 2, 2026 (post date)",
      filterDescriptions: [],
      synonymMappings: [{ from: "F2", to: "finding F2 — Atlas Commercial WoW decline (turn 1)" }],
      planDiff: ["zero-probe path: answered from the session trace"],
      appliedOperators: ["Explain(F2)"],
    },
  },
  { type: "stage", stage: "interpreted", status: "completed", detail: "Explain(F2) — trace lookup" },
  { type: "context_header", header: T1_HEADER, turnClass: "meta" },
  { type: "stage", stage: "planned", status: "skipped", detail: "zero-probe path" },
  { type: "stage", stage: "validated", status: "skipped" },
  { type: "stage", stage: "executing", status: "skipped", probesDone: 0, probesTotal: 0 },
  { type: "stage", stage: "calculating", status: "skipped" },
  { type: "stage", stage: "reconciled", status: "skipped" },
  {
    type: "chart_spec",
    spec: {
      id: "t5_atlas_submissions",
      kind: "bar",
      title: "Atlas Commercial claims submitted by week (from trace, probe 77b1e0aa)",
      unit: "count",
      series: [{ key: "count", label: "Claims submitted", role: "current" }],
      rows: ATLAS_SUBMISSIONS_BY_WEEK.map((w) => ({ label: w.week, values: { count: w.count } })),
      highlightLabel: "Jul 13",
    },
  },
  {
    type: "evidence",
    evidence: {
      zeroProbeTurn: true,
      probes: [],
      reconciliation: {
        status: "not_applicable",
        detail: "META turn — cites T1/T2 evidence; nothing new to reconcile.",
      },
      traceNote:
        "Answered from the session trace: probe a3f2c9d1 (cash_posted@3, compare@1.2.0), probe d4a91b77 (payer decomposition, reconciliation PASSED at 0¢ tolerance), probe 77b1e0aa (Atlas submission trend). Zero warehouse queries executed — asserted by the execution service.",
    },
  },
  { type: "stage", stage: "narrating", status: "started" },
  {
    type: "narrative_delta",
    text:
      "[F2] states that Atlas Commercial payer cash fell $48,940.41 (−15.7%) week-over-week. It rests on probe a3f2c9d1 — posted payer cash under governed contract cash_posted@3 (post-date basis, patient payments excluded) — compared across Jul 27 – Aug 2 vs Jul 20 – 26 by kernel operator compare@1.2.0, and on the payer decomposition in probe d4a91b77, whose 12 rows reconciled to the parent decline exactly (PASSED, 0¢ tolerance). The volume attribution comes from probe 77b1e0aa: Atlas submissions dropped from ~270/week to ~204/week beginning the week of Jul 13, and remits lag submissions, so the cash gap lands in the decline week. This explanation was answered entirely from the session trace — zero warehouse queries.",
  },
  {
    type: "turn_complete",
    investigationId: "inv_t5",
    status: "complete",
    answerGrade: "direct",
    metric: CASH_POSTED_CONTRACT,
  },
];

export const REFERENCE_TURNS: ScriptedTurn[] = [
  {
    id: "T1",
    question: "Why did cash decline last week?",
    trigger: /why.*cash.*decline|cash.*decline.*last\s+week/i,
    events: T1_EVENTS,
  },
  {
    id: "T2",
    question: "Break that down by payer",
    trigger: /break.*down.*payer|by\s+payer/i,
    events: T2_EVENTS,
  },
  {
    id: "T3",
    question: "Just the top three payers — what's the CARC mix on their denials?",
    trigger: /top\s+three\s+payers|carc\s+mix/i,
    events: T3_EVENTS,
  },
  {
    id: "T4",
    question: "Compare that to Q1",
    trigger: /compare.*q1/i,
    events: T4_EVENTS,
  },
  {
    id: "T5",
    question: "Why do you say F2?",
    trigger: /why.*f2|say\s+f2/i,
    events: T5_EVENTS,
  },
];
