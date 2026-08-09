/**
 * Pre-materialized portfolio snapshot ("top five things today") — mocked
 * from the answer key's five planted scenarios at snap_003. Ranked by the
 * governed ranking policy (dollar impact within issue class), not by score.
 */

import type { Refinement } from "@/lib/types";

/**
 * How a portfolio card came to exist. Deliberately NOT an `EvidenceGrade`:
 * a grade certifies how *this platform* computed a number from certified
 * semantics, whereas an anomaly card is a record read out of an external
 * detection system as-of a watermark. `external_detection` is the only
 * value the wire publishes (AnomalyCard.provenance is a const) — drilling a
 * card starts an ordinary turn, and *that* answer carries a real grade.
 */
export type AnomalyProvenance = "external_detection";

export type AnomalySeverity = "critical" | "high" | "medium" | "low";

export interface PortfolioItem {
  rank: number;
  referent: string;
  title: string;
  issueClass: string;
  impactCents: number;
  impactLabel: string;
  detail: string;
  /** Where the card came from, in place of an evidence grade it cannot claim. */
  provenance: AnomalyProvenance;
  /** The governed priority formula that ranked this card (AnomalyCard.priority_formula_version). */
  priorityFormulaVersion: string;
  /** The watermark the detection was read at (AnomalyCard.source_watermark_id). */
  sourceWatermarkId: string;

  /* --- Published, and until now unread by this client (F8) --------- */

  /** `AnomalyCard.severity` — the detector's own grading, not a UI guess. */
  severity?: AnomalySeverity;
  /** `AnomalyCard.age_days` — days since detection at the pinned watermark. */
  ageDays?: number;
  /**
   * `AnomalyCard.recoverable_cents_estimate`. NOT the same number as the
   * impact and rendered separately for exactly that reason: a $493,266
   * detection with a $9,865 recoverable estimate is a small piece of work
   * wearing a large number, and showing only the large number is the
   * single most misleading thing this panel could do.
   */
  recoverableCentsEstimate?: number;
  /** `AnomalyCard.actionability_label` — "highly recoverable", "compliance-mandatory", … */
  actionabilityLabel?: string;
  /** `AnomalyCard.actionability_rationale` — why that label, in the engine's words. */
  actionabilityRationale?: string;
  /** `AnomalyCard.priority_score` / `compliance_floor_applied` — how it ranked. */
  priorityScore?: number;
  complianceFloorApplied?: boolean;
  /** `AnomalyCard.metric_id`, `status`, `confidence` — the detection's own identity. */
  metricId?: string;
  status?: string;
  confidence?: string;
  detectedAt?: string;
  windowStart?: string;
  windowEnd?: string;
  /** `AnomalyCard.dimensions` — the detected scope, as chips. */
  dimensions?: Array<{ dimension: string; value: string }>;

  /**
   * `AnomalyCard.drillable` and the platform's refusal when it is false.
   * The server refuses 4 of 33 live cards (36% of ranked impact) with a
   * GRAIN_INCOMPATIBLE sentence naming the dimension and the grain. A
   * client-side "Drill in" button over that refusal is a dead control that
   * claims the platform can do something it has just said it cannot.
   */
  drillable: boolean;
  drillUnavailableReason?: string;
  /**
   * `AnomalyCard.drill_repointed_from` / `drill_repoint_rationale`: the
   * card's drill probes a DIFFERENT metric than the one it reports, and
   * the server says why. Nine live cards are repointed off `denial_rate`.
   */
  drillRepointedFrom?: string;
  drillRepointRationale?: string;
  /**
   * The measure the drill ACTUALLY probes — `drill_spec.metric_ids[0]`,
   * not `metric_id`. On a repointed card those two differ and only this
   * one is the truth about what opening the card will measure: live
   * ANM-001 reports `denial_rate`, names `denial_rate` as what it was
   * repointed FROM, and probes `denied_dollars`.
   */
  drillMetricId?: string;

  /**
   * The card's typed first turn (`AnomalyCard.drill_spec`): a complete
   * `TypedInvestigationSpec` that opens a NEW investigation with no parent
   * and no model call. Present on live cards; absent in this mock, which
   * has no server to have produced one.
   */
  drillSpec?: Record<string, unknown>;
  /**
   * The mock fixture's local drill handle. Live cards do NOT get one
   * synthesized: a fabricated `DrillInto(P1)` against a server that
   * published no `drill_spec` is a gesture the engine never offered.
   */
  drill?: { label: string; refinement: Refinement };
}

export const PORTFOLIO_ITEMS: PortfolioItem[] = [
  {
    rank: 1,
    referent: "P1",
    title: "Timely filing risk — State Medicaid HMO at Eastside",
    issueClass: "timely_filing_watch",
    impactCents: 117_141_515,
    impactLabel: "billed at risk",
    detail:
      "414 July claims unsubmitted, 58–88 days left against the 90-day service-basis limit. 15 CARC·29 denials already on file.",
    provenance: "external_detection",
    priorityFormulaVersion: "dollar_impact@1",
    sourceWatermarkId: "wm_003",
    drillable: true,
    drill: { label: "Drill in", refinement: { op: "DrillInto", target: "P1" } },
  },
  {
    rank: 2,
    referent: "P2",
    title: "COB mismatch — Silverline Medicare Advantage",
    issueClass: "cob_investigation",
    impactCents: 29_875_184,
    impactLabel: "denied via CARC·22",
    detail:
      "7.7% of Apr–Jul claims flagged primary-with-other-insurance; 115 OA·22 denials, rebills landing ~37 days later.",
    provenance: "external_detection",
    priorityFormulaVersion: "dollar_impact@1",
    sourceWatermarkId: "wm_003",
    drillable: true,
    drill: { label: "Drill in", refinement: { op: "DrillInto", target: "P2" } },
  },
  {
    rank: 3,
    referent: "P3",
    title: "Cash decline — week of Jul 27",
    issueClass: "cash_decline",
    impactCents: -19_352_579,
    impactLabel: "payer cash WoW",
    detail:
      "−12.7% week-over-week. State Medicaid timing (−$99.1K) plus Atlas Commercial volume (−$48.9K) explain 76%.",
    provenance: "external_detection",
    priorityFormulaVersion: "dollar_impact@1",
    sourceWatermarkId: "wm_003",
    drillable: true,
    drill: { label: "Drill in", refinement: { op: "DrillInto", target: "P3" } },
  },
  {
    rank: 4,
    referent: "P4",
    title: "Underpayment — Northbridge Commercial · ORTHO-SURG",
    issueClass: "underpayment_review",
    impactCents: 5_587_898,
    impactLabel: "variance since May",
    detail:
      "Paying ~92% of contract-expected on ORTHO-SURG lines since 2026-05. Underpayments never net against overpayments.",
    provenance: "external_detection",
    priorityFormulaVersion: "dollar_impact@1",
    sourceWatermarkId: "wm_003",
    drillable: true,
    drill: { label: "Drill in", refinement: { op: "DrillInto", target: "P4" } },
  },
  {
    rank: 5,
    referent: "P5",
    title: "Denial spike — Meridian Health × Imaging (CO·197)",
    issueClass: "denial_spike",
    impactCents: 2_510_182,
    impactLabel: "denied since Jun 15",
    detail:
      "Prior-auth denial rate 2.2% → 9.9% (4.6×) at the Jun 15 break. 12 denied claims so far on first remits.",
    provenance: "external_detection",
    priorityFormulaVersion: "dollar_impact@1",
    sourceWatermarkId: "wm_003",
    drillable: true,
    drill: { label: "Drill in", refinement: { op: "DrillInto", target: "P5" } },
  },
];

export const PORTFOLIO_META = {
  watermark: "2026-08-03 04:10",
  rankingPolicy: "dollar_impact@1",
  packVersion: "base-rcm@1.0.0",
};
