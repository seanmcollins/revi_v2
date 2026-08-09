/**
 * Revi web — domain + wire types.
 *
 * These mirror the Python domain shapes by hand for M11 pre-work:
 *   packages/kernel/src/revi_kernel/{frame,grades,refs,watermark}.py
 *   packages/investigation/src/revi_investigation/domain/{records,turns,context}.py
 *
 * NOTE: in M8 this file's wire-facing types will be REGENERATED from the
 * FastAPI OpenAPI spec via `openapi-typescript` (`make openapi`). Keep the
 * shapes conservative and JSON-serializable so the swap is mechanical.
 */

/* ------------------------------------------------------------------ */
/* Grades (revi_kernel.grades)                                         */
/* ------------------------------------------------------------------ */

/** Strength ordering: direct > derived > proxy > discovery > unavailable. */
export type EvidenceGrade =
  | "direct"
  | "derived"
  | "proxy"
  | "discovery"
  | "unavailable";

export const GRADE_STRENGTH: Record<EvidenceGrade, number> = {
  direct: 4,
  derived: 3,
  proxy: 2,
  discovery: 1,
  unavailable: 0,
};

/** The grade law: every transform output carries the weakest input grade. */
export function minGrade(first: EvidenceGrade, ...rest: EvidenceGrade[]): EvidenceGrade {
  let weakest = first;
  for (const g of rest) {
    if (GRADE_STRENGTH[g] < GRADE_STRENGTH[weakest]) weakest = g;
  }
  return weakest;
}

/* ------------------------------------------------------------------ */
/* Refs (revi_kernel.refs)                                             */
/* ------------------------------------------------------------------ */

export type DateBasis = "service" | "post" | "submission" | "remit" | "discharge";

export type EntityGrain =
  | "claim"
  | "line"
  | "encounter"
  | "transaction"
  | "remit"
  | "denial";

export type TimeBucket = "day" | "week" | "month";

export interface Grain {
  entity: EntityGrain;
  timeBucket?: TimeBucket;
}

export type ReferentKind =
  | "finding"
  | "cohort"
  | "chart_series"
  | "table_row"
  | "dimension_value";

/** A stable, analyst-visible handle (F1, F2, …) — design §7.6. */
export interface ReferentId {
  value: string;
  kind: ReferentKind;
}

/* ------------------------------------------------------------------ */
/* Watermark / windows / context (revi_kernel + domain/context)        */
/* ------------------------------------------------------------------ */

export interface DataWatermark {
  id: string;
  /** "2026-08-03 04:10" — rendered verbatim in the context header. */
  loadedAt: string;
  /** ISO date of the newest fact row visible at this watermark. */
  newestDataDate: string;
}

/** A window resolved to concrete dates at plan time (never re-resolved). */
export interface ResolvedWindow {
  /** ISO date, inclusive. */
  start: string;
  /** ISO date, inclusive. */
  end: string;
  basis: DateBasis;
  /** What the user asked for, e.g. "last week". */
  requested?: string;
}

export type ComparisonKind = "prior_period" | "same_period_last_year" | "custom";

export interface Comparison {
  kind: ComparisonKind;
  window: ResolvedWindow;
  /** Display label for custom comparisons, e.g. "Q1 2026". */
  label?: string;
}

/** One scope clause, tagged with the turn that introduced it. */
/**
 * One scope clause, tagged with the turn that introduced it. `op` mirrors
 * the published `PredicateOp` set verbatim (`FilterChip.op`) rather than a
 * UI-local subset — a chip that says "between" when the server said
 * "range" is a chip that lies about the population.
 */
export interface FilterClause {
  dimension: string;
  dimensionLabel: string;
  op: "eq" | "neq" | "in" | "not_in" | "range" | "is_null" | "contains";
  /**
   * The values the predicate ACTUALLY ran with — the corrected ones when
   * the engine matched "lakewood medicaid mco" to the payer that exists.
   * The chip states this and only this: a header whose entire job is to
   * say which population ran cannot show a value the warehouse has never
   * held, two rows above the caution saying so.
   */
  values: string[];
  /**
   * What the user typed, when the engine corrected it (`requested_values`
   * on the wire). Present ONLY when it differs from `values`, so the
   * presence of the field is itself the signal that a correction happened;
   * an older payload that publishes no such field leaves it undefined and
   * the chip renders exactly as before.
   */
  requestedValues?: string[];
  originTurn: string;
  /** A session pin (carryover law 5) rather than a turn-local filter. */
  pinned?: boolean;
}

/**
 * The pinned population, as `CohortPayload` publishes it.
 *
 * `definition` is the INTENSIONAL rule (§7.5) rendered in the same
 * `dimension op [values]` grammar the filter chips use — "payer in [State
 * Medicaid, Atlas Commercial]" — not a description of the pinned rows.
 * `entityGrain` says what one member IS (a claim, a line, a remit), which
 * is what makes the size figure readable: "86,415" alone is a number, and
 * "86,415 claims" is a population.
 *
 * `detailed` records which of the two sources filled this in. The context
 * header publishes only `cohort` (an id) and `cohort_size`; the richer
 * block rides on `TurnAnswer.cohort`. A turn restored from an older
 * store may have the first and not the second, and the chip must then say
 * what it has rather than dressing up a hash as a definition.
 */
export interface CohortSummary {
  id: string;
  /** Human-readable intensional definition ("payer in [State Medicaid, …]"). */
  definition: string;
  pinned: boolean;
  originTurn: string;
  size: number;
  /** `CohortPayload.entity_grain` — what one member of this population is. */
  entityGrain?: string;
  /** `origin_referent` — the handle (F2) the population was selected from. */
  originReferent?: string;
  originInvestigationId?: string;
  /** The cohort definition's own window, when it kept one. */
  windowStart?: string;
  windowEnd?: string;
  /** The data load the membership was frozen against. */
  pinnedWatermarkId?: string;
  /** True when this came from `TurnAnswer.cohort` rather than the header id. */
  detailed?: boolean;
}

export interface PackVersionRef {
  packId: string;
  version: string;
}

/** §7.2 context header — carried by EVERY answer, no exceptions. */
export interface ContextHeaderData {
  window: ResolvedWindow;
  comparison?: Comparison;
  filters: FilterClause[];
  cohort?: CohortSummary;
  /** Not published on `ContextHeaderPayload`; present only in mock data. */
  grain?: Grain;
  watermark: DataWatermark;
  packVersion: PackVersionRef;
}

/* ------------------------------------------------------------------ */
/* Metric contracts (governed provenance)                              */
/* ------------------------------------------------------------------ */

/**
 * Direction-of-good: which way is GOOD for this metric. A falling denial
 * rate is good; a falling cash total is bad. Drives delta coloring.
 */
export type DirectionOfGood = "up_is_good" | "down_is_good" | "neutral";

/**
 * One governed metric contract a turn read, as the turn recorded it.
 *
 * `contractVersion` is the version the connector stamped on the executed
 * frame — absent when the probe was planned and never ran, which is a
 * different fact from "version 1" and is shown as one.
 */
export interface MetricContractRef {
  id: string;
  contractVersion?: number;
}

/**
 * `TurnAnswer.metric` — whose definition produced this answer's numbers.
 *
 * Projected server-side from the turn's own recorded trace (the same
 * record the evidence bundle and the debug trace read), so the badge
 * cannot disagree with the drawer. It carries ids, versions, the playbook
 * and the pack — and deliberately nothing else: a contract's display
 * name, numerator, date basis, exclusions and fingerprint live in the
 * pack, are not recorded per turn, and captioning last week's answer with
 * today's pack would be exactly the overclaim the badge exists to
 * prevent.
 *
 * `primary` is set only when ONE contract stands behind the answer. A
 * playbook turn that ran several leaves it undefined and lists them all.
 */
export interface MetricProvenance {
  primary?: MetricContractRef;
  /** Every governed metric this turn's probes named, in plan order. */
  metrics: MetricContractRef[];
  /** The governed playbook recorded in the turn's plan context. */
  playbookId?: string;
  pack: PackVersionRef;
  /** Content hash of the pack as loaded — "1.0.0" vs "1.0.0, hot-edited". */
  packSnapshotId?: string;
}

/* ------------------------------------------------------------------ */
/* Turn taxonomy + refinements (domain/turns, domain/refinements)      */
/* ------------------------------------------------------------------ */

export type TurnClass =
  | "new_investigation"
  | "refinement"
  | "presentation_only"
  | "context_control"
  | "meta"
  | "clarification_response"
  | "definitional";

/** The closed 12-operator refinement algebra (§7.4). No NL in the loop. */
export type Refinement =
  | { op: "SetDimensions"; dimensions: string[] }
  | { op: "AddFilter"; filter: Omit<FilterClause, "originTurn"> }
  | { op: "RemoveFilter"; dimension: string }
  | { op: "SetWindow"; window: ResolvedWindow }
  | { op: "SetComparison"; comparison: Comparison | null }
  | { op: "SetGrain"; grain: Grain }
  | { op: "DrillInto"; target: string | string[] }
  | { op: "Pivot"; measures: string[] }
  | { op: "Explain"; target: string }
  | { op: "RankBy"; metric: string; descending: boolean }
  | { op: "Expand" }
  | { op: "ResetContext"; keepPins: boolean };

export interface ClarificationData {
  question: string;
  options: string[];
  reason?: string;
}

/* ------------------------------------------------------------------ */
/* Findings (domain/records.Finding + display fields)                  */
/* ------------------------------------------------------------------ */

/**
 * `FindingPayload.confidence`. "qualified" is the engine's own word for a
 * finding whose evidence is weaker than its playbook's conclusion policy
 * demands — it is published, so it is spelled here rather than being
 * flattened into "low", which would say something different.
 */
export type Confidence = "high" | "medium" | "low" | "qualified";

export interface FindingComparisonStat {
  currentCents: number;
  priorCents: number;
  currentLabel: string;
  priorLabel: string;
}

/**
 * `MetricDisplayPayload` — a governed correction to a metric id that
 * overclaims. `timely_filing_at_risk_dollars` applies no deadline
 * predicate at all, so every surface that spells the id out ("timely
 * filing at risk dollars") promises filing exposure over a number that
 * measures unbilled inventory. The pack publishes the honest name, the
 * caveat that bounds it, and the rationale that argued for it.
 */
export interface MetricDisplay {
  metricId: string;
  displayName: string;
  /** What the number does NOT say — published alongside the name. */
  caveat?: string;
  /** Why the correction was authored, in the pack's own words. */
  rationale?: string;
}

export interface Finding {
  referent: ReferentId;
  title: string;
  statement: string;
  metricRefs: string[];
  /** Named scalar values backing the statement (kernel-certified). */
  values: Record<string, number | string>;
  grade: EvidenceGrade;
  impactCents?: number;
  /** "delta" impacts are signed and tone-colored; "level" impacts are neutral. */
  impactKind?: "delta" | "level";
  /** Caption under the impact stat, e.g. "WoW change" / "denied this week". */
  impactLabel?: string;
  /** Non-money headline stat, e.g. "4.0×" (used when impactCents is absent). */
  impactDisplay?: string;
  /**
   * Why the stat slot is empty when the server deliberately withheld the
   * impact figure (e.g. COMPARISON_WINDOW_MISMATCH suppresses it). The
   * card renders this instead of a blank — an absent number that nobody
   * explains reads as a rendering bug, not as a refusal.
   */
  impactWithheldReason?: string;
  deltaPct?: number;
  directionOfGood: DirectionOfGood;
  confidence: Confidence;
  comparison?: FindingComparisonStat;
  /** Typed follow-ups rendered as drill actions / chips. */
  suggestedRefinements: SuggestedRefinement[];
  /**
   * The governed display-name correction applied to this finding's title
   * and value label, when the turn published one for its measure. Carried
   * so the card can also show the caveat that travels with the name — the
   * correction and its bound are one governed entry, and shipping the
   * flattering half alone would be worse than shipping neither.
   */
  metricDisplay?: MetricDisplay;
}

export interface SuggestedRefinement {
  label: string;
  refinement: Refinement;
}

/* ------------------------------------------------------------------ */
/* Charts                                                              */
/* ------------------------------------------------------------------ */

export interface ChartRow {
  /** Category label (payer name, CARC code, week start…). */
  label: string;
  /** Referent for chart-click → DrillInto. */
  referent?: string;
  values: Record<string, number>;
}

export interface ChartSeries {
  key: string;
  label: string;
  /** Which validated series slot to use. */
  role: "current" | "baseline";
}

export interface ChartSpec {
  id: string;
  /** The shape this UI draws — a reduction of `chart_type` (see contract.ts). */
  kind: "bar" | "grouped_bar" | "line";
  /** The published `ChartSpec.chart_type`, kept verbatim so the reduction
   *  is visible and reversible rather than lossy. */
  wireChartType?: string;
  /**
   * The published `ChartSpec.frame_id`. Load-bearing, not decoration: a
   * comparison turn publishes `main` AND `main__compare` (and, when the
   * engine grows one, `main__prior`) over the same measure, so the frame
   * id is the only thing that says which charts are the same question
   * asked twice. See `selectRenderableCharts`.
   */
  frameId?: string;
  title: string;
  /** The server's own `ChartSpec.title` ("denied dollars — main 2  compare"),
   *  kept so the composed human title can be checked against it. */
  wireTitle?: string;
  unit: "cents" | "percent" | "count" | "days";
  series: ChartSeries[];
  rows: ChartRow[];
  /** Truncation is surfaced, never silent. */
  truncation?: { shown: number; total: number };
  /** Annotation, e.g. "decline week". */
  highlightLabel?: string;
  xLabel?: string;
}

/* ------------------------------------------------------------------ */
/* Evidence (revi_kernel.frame provenance → drawer lineage)            */
/* ------------------------------------------------------------------ */

/**
 * These mirror `EvidencePayload` on the wire (see the server's
 * `revi_investigation_contracts.evidence`), which is projected from the
 * turn's recorded trace — the same record `GET .../trace` reads. Every
 * field below therefore has a counterpart the server actually stored;
 * nothing here is derived in the browser.
 *
 * Two things this deliberately no longer models, both cut in the pass
 * that pointed the drawer at the live API rather than at fixtures:
 *
 *   `sampleRows` — the planner emits only aggregation and snapshot
 *     probes, so no frame the platform stores holds row-level content and
 *     a masked-sample table had nothing behind it. The probe `kind` field
 *     is where `row_evidence` will announce itself when that lands.
 *   per-probe operator versions, and reconciliation `parentCents` /
 *     `childSumCents` — the trace records operator applications for the
 *     turn, not per probe, and the §7.8 verdict as a status string
 *     without the totals. Showing either meant inventing the attribution.
 */
export interface ProbeMetricRef {
  id: string;
  /** The contract version the executed frame was stamped with. */
  contractVersion?: number;
}

export interface ProbeEvidence {
  probeId: string;
  /** Content hash of the probe — the evidence-cache key component. */
  probeHash: string;
  /** The §6.2 probe union member; "" on traces predating the field. */
  kind: string;
  /** The plan's own statement of what this probe was for. */
  description: string;
  metrics: ProbeMetricRef[];
  cacheHit: boolean;
  /** Undefined for a probe planned but never executed — not zero rows. */
  rowCount?: number;
  /** The top-N cutoff applied, when one was. */
  limit?: number;
  truncated: boolean;
  suppressedCells: number;
  grade?: EvidenceGrade;
  durationMs: number;
}

export interface ReconciliationResult {
  /**
   * The recorded verdict. `passed_with_suppression` is its own state: the
   * parts agree within the allowance small-cell suppression leaves, which
   * is not the same claim as an exact match. `unknown` means the server
   * stored a summary in a grammar this build cannot parse — reported, not
   * rounded up to "passed".
   */
  status: "passed" | "passed_with_suppression" | "failed" | "not_applicable" | "unknown";
  /** The reason half of the verdict, e.g. "this is a first turn; ...". */
  detail?: string;
  /** The recorded string verbatim, for the debug disclosure. */
  summary: string;
}

export interface EvidenceBundle {
  probes: ProbeEvidence[];
  /**
   * Absent when the turn recorded no verdict at all (a META citation, a
   * kernel-only refinement). Distinct from a recorded `not_applicable`,
   * which means the check was reached, declined, and said why.
   */
  reconciliation?: ReconciliationResult;
  /** Probes that went to the warehouse; the rest came from the cache. */
  warehouseQueries: number;
  cacheHits: number;
  /** `warehouseQueries === 0` — this answer cost the warehouse nothing. */
  zeroProbeTurn: boolean;
  /** The grade law over the turn's recorded finding grades (§5.3). */
  answerGrade?: EvidenceGrade;
}

/* ------------------------------------------------------------------ */
/* Anomaly drill reconciliation (AnomalyReconciliationPayload)         */
/* ------------------------------------------------------------------ */

/**
 * The card's figure against this platform's re-derivation of it, stated.
 *
 * A card published $178,217; drilling it answered $195,873.92; the turn's
 * own §7.8 verdict said "not applicable — this is a first turn", which is
 * true about the investigation lineage and silent about the two numbers
 * the reader had just compared. Both figures are honest and they are
 * different claims: `cardImpactCents` is the external detection system's
 * assertion on its own window, population and valuation basis;
 * `answerImpactCents` is the governed metric contract re-derived at the
 * pinned watermark. They diverge when those differ, which is normal, is
 * not an error, and must be *said*.
 */
export interface AnomalyReconciliation {
  anomalyId: string;
  status: "agreed" | "diverged" | "unavailable";
  cardImpactCents: number;
  answerImpactCents?: number;
  deltaCents?: number;
  /** Signed fraction (0.099077 = +9.9%). */
  deltaFraction?: number;
  cardMetricId?: string;
  answerMetricId?: string;
  cardWindowStart?: string;
  cardWindowEnd?: string;
  /** The platform's own account of why the two differ. */
  detail?: string;
  /** The one-line verdict ("status=diverged; card=…; answer=…; delta=…"). */
  summary?: string;
}

/* ------------------------------------------------------------------ */
/* Interpretation ("show the interpretation" panel)                    */
/* ------------------------------------------------------------------ */

export interface SynonymMapping {
  from: string;
  to: string;
  note?: string;
}

export interface InterpretationData {
  metric: { id: string; name: string; version: number };
  windowDescription: string;
  comparisonDescription?: string;
  filterDescriptions: string[];
  synonymMappings: SynonymMapping[];
  playbook?: string;
  /** Plan diff vs the parent investigation ("kept window, added payer dim"). */
  planDiff?: string[];
  /** The typed operators this turn applied (lineage edge labels). */
  appliedOperators?: string[];
}

/* ------------------------------------------------------------------ */
/* Definitional cards (DEFINITIONAL turn class)                        */
/* ------------------------------------------------------------------ */

export interface DefinitionSource {
  label: string;
  authority: "governed_pack" | "standard_paraphrase" | "concept_dictionary";
}

export interface DefinitionCardData {
  term: string;
  normalizedTo: string;
  definition: string;
  groupCode?: { code: string; meaning: string };
  carc?: { code: number; paraphrase: string; category: string };
  sources: DefinitionSource[];
  packVersion: PackVersionRef;
  relatedConcepts: string[];
}

/* ------------------------------------------------------------------ */
/* Pipeline stages (SSE `stage` events)                                */
/* ------------------------------------------------------------------ */

export type StageId =
  | "classified"
  | "interpreted"
  | "planned"
  | "validated"
  | "executing"
  | "calculating"
  | "reconciled"
  | "narrating";

export const STAGE_ORDER: StageId[] = [
  "classified",
  "interpreted",
  "planned",
  "validated",
  "executing",
  "calculating",
  "reconciled",
  "narrating",
];

export const STAGE_LABELS: Record<StageId, string> = {
  classified: "Classified",
  interpreted: "Interpreted",
  planned: "Planned",
  validated: "Validated",
  executing: "Executing probes",
  calculating: "Calculating",
  reconciled: "Reconciled",
  narrating: "Narrating",
};

/**
 * The default (non-debug) rail: the same eight engine stages, grouped into
 * four steps an analyst can read without knowing the pipeline. This is a
 * relabelling and a grouping — no stage is hidden, and a group's state is
 * derived from the stages inside it, so a skipped step still reads as
 * skipped. The precise eight-stage rail stays one debug toggle away.
 */
export interface PlainStageGroup {
  id: string;
  label: string;
  stages: StageId[];
}

export const PLAIN_STAGE_GROUPS: PlainStageGroup[] = [
  { id: "reading", label: "Reading your question", stages: ["classified", "interpreted"] },
  { id: "deciding", label: "Deciding what to check", stages: ["planned", "validated"] },
  {
    id: "checking",
    label: "Checking the numbers",
    stages: ["executing", "calculating", "reconciled"],
  },
  { id: "writing", label: "Writing it up", stages: ["narrating"] },
];

/* ------------------------------------------------------------------ */
/* Debug trace (DebugTracePayload — internal debug mode only)          */
/* ------------------------------------------------------------------ */

/**
 * One turn's decision breakdown, as published by `TurnAnswer.debug` /
 * `TurnClarification.debug` and by `GET /v1/investigations/{iid}/trace`.
 *
 * This is the ONE place in the UI where precise internal vocabulary is
 * correct: it exists to explain how the engine reached an answer, to an
 * engineer, and blurring "probe" into "check" here would defeat it.
 * Everything in this shape is recorded on every turn regardless of the
 * debug setting — the setting only decides whether it is published — so a
 * turn nobody thought to debug is still explainable afterwards.
 */
export interface DebugInterpretationTrace {
  intentSummary: string;
  metricIds: string[];
  dimensionIds: string[];
  conceptIds: string[];
  playbookId?: string;
  windowStart?: string;
  windowEnd?: string;
  basis?: string;
}

export interface DebugProbeTrace {
  id: string;
  hash: string;
  purpose: string;
  cacheHit: boolean;
  /** null = planned but never executed. */
  rows: number | null;
  limit: number | null;
  truncated: boolean;
  suppressedCells: number;
  grade?: string;
  durationMs: number;
}

/** `failure` is the LlmFailureKind: declined / schema / off_script. */
export interface DebugLlmCallTrace {
  template: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  /** Decimal string — never rounded through a float. */
  costUsd: string;
  schemaRetries: number;
  attempts: number;
  durationMs: number;
  failure?: string;
}

export interface DebugTrace {
  traceId: string;
  sessionId: string;
  investigationId: string;
  turnId: string;
  /** The settings actually in force for the turn, as the server resolved them. */
  settings: {
    modelTier: string | null;
    maxTurnCostUsd: string | null;
    narrativeDepth: string;
    evidenceDepth: string;
    debug: boolean;
  };
  question?: string;
  turnClass?: string;
  classificationConfidence?: number;
  interpretation?: DebugInterpretationTrace;
  refinementOperators: Record<string, unknown>[];
  refinementRationale?: string;
  referentResolutions: Record<string, unknown>[];
  clarificationReason?: string;
  planHash?: string;
  playbookId?: string;
  probes: DebugProbeTrace[];
  grades: Record<string, string>;
  weakestGrade?: string;
  findingGrades: Record<string, string>;
  calculationOperators: Record<string, unknown>[];
  reconciliation?: string;
  warnings: string[];
  llmCalls: DebugLlmCallTrace[];
  templateHashes: Record<string, string>;
  timingsMs: Record<string, number>;
  watermarkId: string;
  watermarkStale: boolean;
  epoch: number;
  reAnchored: boolean;
  packId: string;
  packVersion: string;
  packSnapshotId: string;
  /** Free text the outbound-payload guard withheld — named, never silent. */
  redactions: string[];
}

/* ------------------------------------------------------------------ */
/* SSE turn events (plan §6: the POST-SSE stream)                      */
/* ------------------------------------------------------------------ */

export interface StageEvent {
  type: "stage";
  stage: StageId;
  status: "started" | "completed" | "skipped";
  /** e.g. "NEW_INVESTIGATION (0.98)" or "probe 2/3". */
  detail?: string;
  probesDone?: number;
  probesTotal?: number;
  cacheHits?: number;
}

export interface ContextHeaderEvent {
  type: "context_header";
  header: ContextHeaderData;
  turnClass: TurnClass;
}

export interface FindingEvent {
  type: "finding";
  finding: Finding;
}

export interface ChartSpecEvent {
  type: "chart_spec";
  spec: ChartSpec;
}

export interface NarrativeDeltaEvent {
  type: "narrative_delta";
  text: string;
}

export interface ClarificationEvent {
  type: "clarification";
  clarification: ClarificationData;
}

/**
 * One warning, with a handle a client can branch on (`WarningPayload`).
 *
 * `message` is the platform's own sentence verbatim; `code` is a handle
 * added beside it, never a replacement for it. `count` is how many
 * identical warnings collapsed into this entry — a four-probe plan
 * emitting one population caveat four times is one caveat seen four
 * times, and the rail renders one row that says so.
 *
 * `structured` records where the code came from. `true` means the server
 * classified it (`warnings_v2`) and the code is safe to branch on; absent
 * means it was inferred from the prose of a legacy `warnings` string, and
 * the UI treats it as a sentence with a label rather than as a contract.
 */
export interface WarningEvent {
  type: "warning";
  code: string;
  message: string;
  severity: "info" | "caution";
  /** `WarningPayload.count` — identical warnings collapsed into this one. */
  count?: number;
  /** True when the code came from `warnings_v2`, not from parsing prose. */
  structured?: boolean;
}

export interface TurnCompleteEvent {
  type: "turn_complete";
  investigationId: string;
  status: "complete" | "clarification_required" | "failed";
  answerGrade?: EvidenceGrade;
  /** The governed provenance of this turn's numbers (`TurnAnswer.metric`). */
  metric?: MetricProvenance;
  /**
   * Card figure vs re-derived figure, when this turn drilled an anomaly.
   * It rides on the terminal frame for the same reason the grade does:
   * the answer's own figure is not known until the turn is finished.
   */
  anomalyReconciliation?: AnomalyReconciliation;
  /** The governed display-name corrections this turn's measures carry. */
  metricDisplay?: MetricDisplay[];
  /** Present only when the settings in force for the turn had debug on. */
  debug?: DebugTrace;
}

/**
 * What one turn spent (`UsageSummary`). Cost is a DECIMAL STRING on the
 * wire and stays one here for the same reason the settings budget does: a
 * price rounded through a float is not the price that was charged.
 */
export interface TurnUsage {
  llmCalls: number;
  costUsd: string;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  schemaRetries: number;
}

export interface ErrorEvent {
  type: "error";
  code: string;
  message: string;
  correlationId?: string;
  /**
   * `ErrorEnvelope.subcode`. `QUERY_BUDGET_EXCEEDED` is two failures
   * wearing one code: `WAREHOUSE_READ_BUDGET` (the question reads too much
   * — narrow it) and `MODEL_SPEND_BUDGET` (the question was fine, the
   * wallet was the constraint — raise the ceiling or pick a cheaper tier).
   * They want opposite responses, so they get different copy.
   */
  subcode?: string;
  /**
   * What the failed turn still spent (`TurnError.usage`). A turn that
   * errored after two model calls cost real money, and a card that shows
   * only the refusal is quietly under-reporting the bill.
   */
  usage?: TurnUsage;
}

/*
 * The two events below are frontend-anticipated extensions beyond the plan's
 * core nine — the interpretation panel and evidence drawer need structured
 * payloads. They will be reconciled against the real OpenAPI spec in M8.
 */
export interface InterpretationEvent {
  type: "interpretation";
  interpretation: InterpretationData;
}

export interface EvidenceEvent {
  type: "evidence";
  evidence: EvidenceBundle;
}

export interface DefinitionEvent {
  type: "definition_card";
  definition: DefinitionCardData;
}

export type TurnEvent =
  | StageEvent
  | ContextHeaderEvent
  | FindingEvent
  | ChartSpecEvent
  | NarrativeDeltaEvent
  | ClarificationEvent
  | WarningEvent
  | TurnCompleteEvent
  | ErrorEvent
  | InterpretationEvent
  | EvidenceEvent
  | DefinitionEvent;

export const TURN_EVENT_TYPES: ReadonlySet<string> = new Set([
  "stage",
  "context_header",
  "finding",
  "chart_spec",
  "narrative_delta",
  "clarification",
  "warning",
  "turn_complete",
  "error",
  "interpretation",
  "evidence",
  "definition_card",
]);

/* ------------------------------------------------------------------ */
/* Session lineage (GET /v1/sessions/{sid}/lineage)                    */
/* ------------------------------------------------------------------ */

export interface LineageNode {
  turnId: string;
  investigationId: string;
  turnClass: TurnClass;
  label: string;
  question: string;
}

export interface LineageEdge {
  parentTurnId: string;
  childTurnId: string;
  operators: string[];
}

export interface SessionLineageData {
  nodes: LineageNode[];
  edges: LineageEdge[];
}

/* ------------------------------------------------------------------ */
/* Session list (GET /v1/sessions)                                     */
/* ------------------------------------------------------------------ */

/**
 * One row of the tenant's session list. Every field is server-derived:
 * `title` is the session's first question verbatim ("New session" when it
 * has none), `lastActivity` is its newest investigation. The rail renders
 * these and nothing else — there is no client-side naming of a session.
 */
export interface SessionSummary {
  sessionId: string;
  title: string;
  /** ISO timestamp — when the session was opened. */
  createdAt: string;
  /** ISO timestamp — when a turn was last answered in it. */
  lastActivity: string;
  turnCount: number;
}

export interface SessionListData {
  sessions: SessionSummary[];
  /** Every session the tenant owns, so a truncated page can say so. */
  total: number;
}
