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
  values: string[];
  originTurn: string;
  /** A session pin (carryover law 5) rather than a turn-local filter. */
  pinned?: boolean;
}

export interface CohortSummary {
  id: string;
  /** Human-readable intensional definition ("top-3 payers by WoW cash decline"). */
  definition: string;
  pinned: boolean;
  originTurn: string;
  size: number;
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

export interface MetricContractSummary {
  id: string;
  version: number;
  name: string;
  kind: "FLOW" | "SNAPSHOT";
  numerator: string;
  denominator?: string;
  primaryDateBasis: DateBasis;
  exclusions: string[];
  unit: "cents" | "percent" | "count" | "days";
  directionOfGood: DirectionOfGood;
  /** Content-hash prefix of the governed contract. */
  fingerprint: string;
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
  deltaPct?: number;
  directionOfGood: DirectionOfGood;
  confidence: Confidence;
  comparison?: FindingComparisonStat;
  /** Typed follow-ups rendered as drill actions / chips. */
  suggestedRefinements: SuggestedRefinement[];
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
  title: string;
  unit: "cents" | "percent" | "count";
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

export interface OperatorApplication {
  name: string;
  version: string;
}

export interface ProbeEvidence {
  probeId: string;
  /** Content-hash prefix (mono in UI). */
  probeHash: string;
  kind: "aggregation" | "snapshot" | "row_evidence";
  description: string;
  contract?: { id: string; version: number };
  operators: OperatorApplication[];
  cacheHit: boolean;
  rowCount: number;
  truncated: boolean;
  suppressedCells: number;
  grade: EvidenceGrade;
}

export interface ReconciliationResult {
  status: "passed" | "failed" | "not_applicable";
  /** "12 payer rows sum to parent delta −$193,525.79 (tolerance 0¢)". */
  detail?: string;
  parentCents?: number;
  childSumCents?: number;
}

export interface MaskedSampleRows {
  columns: string[];
  rows: string[][];
  maskedColumns: string[];
  purpose: string;
}

export interface EvidenceBundle {
  probes: ProbeEvidence[];
  reconciliation: ReconciliationResult;
  sampleRows?: MaskedSampleRows;
  /** Turn answered entirely from trace/cache — zero warehouse queries. */
  zeroProbeTurn: boolean;
  traceNote?: string;
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

export interface WarningEvent {
  type: "warning";
  code: string;
  message: string;
  severity: "info" | "caution";
}

export interface TurnCompleteEvent {
  type: "turn_complete";
  investigationId: string;
  status: "complete" | "clarification_required" | "failed";
  answerGrade?: EvidenceGrade;
  metric?: MetricContractSummary;
}

export interface ErrorEvent {
  type: "error";
  code: string;
  message: string;
  correlationId?: string;
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
