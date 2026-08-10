/**
 * Pre-materialized portfolio snapshot — the shared shape, plus the mock
 * fixture behind it.
 *
 * Two things this docstring used to claim that stopped being true. It
 * called the worklist "top five things today": live it is 33 cards split
 * across two lanes, and five is only how many the rail shows before "Show
 * all" — a number about this panel's collapsing, not about the product.
 * And it said the ranking was "dollar impact within issue class"
 * (`dollar_impact@1`), which the fixture below still carries; live cards
 * publish `anomaly_priority@2`, a weighted score over normalized impact,
 * recency and a governed recoverable estimate, with a relative compliance
 * floor. The `PortfolioItem` interface here is the SHARED shape both
 * sources map into, so a stale sentence at the top of it mis-describes the
 * live worklist as well as the fixture.
 */

import type { LeadStatus, TimeToImpact } from "@/lib/rounds";
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

/**
 * `PriorityDecompositionPayload` — every term of `anomaly_priority`, so a
 * ranked list can show its working instead of asserting an order.
 */
export interface PriorityDecomposition {
  impactNorm: number;
  recency: number;
  recoverableNorm: number;
  impactTerm: number;
  recencyTerm: number;
  actionabilityTerm: number;
  weightSum: number;
  scoreBeforeFloor: number;
  floorApplied: boolean;
  floorValue: number;
  /** How the compliance floor was chosen ("relative_median"). */
  floorBasis: string;
  /**
   * WHICH figure this card's `impact_norm` was computed from, and the two
   * numbers that turn it back into arithmetic anyone can check:
   * `impactNorm === rankedImpactCents / impactNormalizerCents`.
   *
   * Without the normalizer the decomposition was checkable in every term
   * but the first: a reader could see `impact_norm: 0.361299` beside
   * `impact_cents: 17821682` and had no way to get from one to the other.
   * The normalizer is the largest ranked figure in the population
   * ($493,266.36 live), and it is the same for every card, which is what
   * makes the ranking a comparison rather than a list of unrelated scores.
   */
  rankedOn?: RankedOn;
  rankedImpactCents?: number;
  impactNormalizerCents?: number;
}

/**
 * `AnomalyCard.ranked_on` — WHICH of the two published figures ordered
 * this card. Three states, three different claims:
 *
 *   `detector` — the detection system's own `impact_cents` ranked it;
 *   `platform` — this platform's re-derivation did, because the two
 *     diverge and the governed contract is the one it stands behind;
 *   `not_comparable` — the detector's figure ranked it BECAUSE this
 *     platform's re-derivation is not the same kind of quantity (an as-of
 *     balance against a windowed flow), so substituting it would change
 *     the claim rather than correct it.
 *
 * The last two are the same outcome reached for opposite reasons, and the
 * card publishes a written note saying which, so neither is rendered as
 * the other.
 */
export type RankedOn = "detector" | "platform" | "not_comparable";

/**
 * `AnomalyCard.drill_dimension_repoints` — a governed SUBSTITUTION for the
 * detector's cut, published with its reasoning.
 *
 * Distinct from `drillRepointedFrom`, which is about the MEASURE. This is
 * about the CUT: the detection feed cuts procedures at `proc_group`, which
 * binds on `claim_line`, so a claim-grain contract has no legal procedure
 * cut at all and four cards — the largest on the worklist among them —
 * refused outright. The catalog certifies `primary_proc_group` (the
 * claim's dominant procedure group) at the claim grain and the drill is
 * pointed at that.
 *
 * A substitution, not a translation, and the card has to say so: the
 * detector counted LINES in the group, the drill counts CLAIMS whose
 * largest procedure group is that one. For a single-procedure claim those
 * are the same population; for a multi-procedure claim they are not.
 */
export interface DrillDimensionRepoint {
  fromDimension: string;
  toDimension: string;
  /** The server's own reasoning, verbatim — never summarized here. */
  rationale: string;
}

/**
 * One half of the worklist (`PortfolioLanePayload`). The lane carries its
 * own ordering (`anomalyIds`), its totals, and the sentence that says why
 * it exists — the rail renders the server's own explanation rather than
 * inventing a heading for a split it did not decide.
 */
export interface PortfolioLane {
  id: string;
  label: string;
  description: string;
  anomalyIds: string[];
  itemCount: number;
  impactCents: number;
}

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
  /**
   * `AnomalyCard.ranked_on` — WHICH figure the ordering was computed from:
   * the detector's `impact_cents`, or this platform's re-derivation of the
   * same cell. Load-bearing because the two disagree on 9 of 33 live cards
   * (largest gap 100.0%), so a worklist ordered by one of them and read as
   * if ordered by the other allocates a Monday morning wrongly.
   *
   * On the wire now, and live it is not one answer: 19 cards ranked on the
   * detector, 9 on this platform, 5 `not_comparable`. Still read
   * defensively — absent means the server has not said, and nothing here
   * guesses.
   */
  rankedOn?: RankedOn;
  /**
   * `AnomalyCard.ranked_impact_cents` — the figure that ACTUALLY ordered
   * the card, which is `impactCents` or `reconciledImpactCents` according
   * to `rankedOn`. Published separately rather than left to be inferred,
   * because inferring it is exactly the step a reader gets wrong.
   */
  rankedImpactCents?: number;
  /**
   * `AnomalyCard.ranked_on_note` — the server's sentence explaining the
   * choice, per card. On a `not_comparable` card it is the difference
   * between "we used the detector's number" and "we used the detector's
   * number because ours measures something else".
   */
  rankedOnNote?: string;
  /**
   * `AnomalyCard.priority` — every term of the governed priority formula,
   * published so the ordering is not a black box: the normalized impact,
   * the recency, the governed recoverable estimate, each term's weighted
   * contribution, and the compliance floor with the basis it was taken
   * from. Rendered in the detection badge, where the formula version it
   * decomposes already lives.
   */
  priority?: PriorityDecomposition;
  /**
   * `AnomalyCard.lane` — which half of the worklist this belongs to.
   * `compliance` work is done because the rule says so ($824 and $84,000
   * carry the same obligation); `value` work is ranked by what is
   * recoverable. Mixing the two into one ordered list is what let a small
   * mandatory refund sink below discretionary work.
   */
  lane?: "compliance" | "value";

  /* --- The card's figure vs this platform's re-derivation (F1) ----- */

  /**
   * `AnomalyCard.reconciled_impact_cents` — the same named cell computed
   * from this platform's governed contract at the pinned watermark.
   * A DIFFERENT claim from `impactCents`, which is the detection system's
   * own assertion on its own window, population and valuation basis.
   */
  reconciledImpactCents?: number;
  /** The contract the re-derivation used — not always the card's `metricId`. */
  reconciledImpactMetricId?: string;
  /**
   * The verdict, in four states that are four different claims:
   *
   *   `agreed` — the two figures match within tolerance;
   *   `diverged` — they differ, and the note says on what basis;
   *   `not_comparable` — they are different KINDS of measurement (a
   *     snapshot balance against a windowed total, a ratio against
   *     money), so the gap is not attributable to either side and the
   *     card is excluded from the divergence count;
   *   `unavailable` — this platform could not re-derive the cell at all.
   *
   * `not_comparable` is the one a three-state client used to drop: it is
   * the state the live #1 card is in, and collapsing it into "unavailable"
   * would report an inability where the platform published a reason.
   */
  impactAgreement?: "agreed" | "diverged" | "not_comparable" | "unavailable";
  impactDeltaCents?: number;
  /** Signed fraction (0.099077 = +9.9%). */
  impactDeltaFraction?: number;
  /** The platform's own account of the comparison, verbatim. */
  impactReconciliationNote?: string;
  /** `AnomalyCard.metric_id`, `status`, `confidence` — the detection's own identity. */
  metricId?: string;
  /**
   * `AnomalyCard.metric_display_name` — the pack's governed name for
   * `metricId`, published per card because a worklist has no answer to
   * hang a warning on. Present only for the ids that overclaim; most
   * metrics say what they measure and get no entry.
   */
  metricDisplayName?: string;
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
   * `AnomalyCard.drill_dimension_repoints` — the same disclosure for the
   * card's CUT rather than its measure. Live, three cards (ANM-011,
   * ANM-012, ANM-013) substitute `primary_proc_group` for the detector's
   * `proc_group`, each carrying the server's written reasoning.
   */
  drillDimensionRepoints?: DrillDimensionRepoint[];
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

  /* --- Rounds: where this lead stands, and when it hits cash -------- */

  /**
   * `AnomalyCard.lead_status` — where this lead stands with the humans
   * working it.
   *
   * `open` is the default and also the honest reading of a lead nobody has
   * touched, which is why the card says nothing at all in that state.
   * `resolved_confirmed` and `regressed` are verdicts the PLATFORM reached
   * by re-running the lead's own drill across loads; a person can claim a
   * resolution and cannot assert one, and the card's menu offers exactly
   * the four a person may set.
   */
  leadStatus?: LeadStatus;
  /**
   * What the last verification measured, in the platform's own words — the
   * confirmation sentence, or why it could not verify. Rendered VERBATIM:
   * it is the difference between "somebody ticked a box" and "this
   * platform re-measured the cell and agrees", and a paraphrase would
   * quietly turn the second back into the first.
   */
  leadStatusNote?: string;
  leadUpdatedAt?: string;
  /**
   * `AnomalyCard.time_to_impact` — when this card's dollars hit cash, or
   * whether they already have.
   *
   * Published CONTEXT. The list is ordered by `anomaly_priority@3` and
   * nothing here re-sorts it: a rank change needs its own versioned
   * formula decision, and smuggling urgency into an existing version would
   * make two builds of the same data disagree with no version string to
   * explain it.
   */
  timeToImpact?: TimeToImpact;
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
