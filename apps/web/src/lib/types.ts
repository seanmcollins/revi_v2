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

// Type-only, and therefore erased: `contract.ts` owns the wire mappers and
// the shapes they produce, and `WorklistData` is one of those rather than a
// hand-mirrored domain record like the rest of this file. Importing the
// type here (instead of re-declaring it) is what keeps the event union and
// the parser from drifting apart.
import type { MonitorDeclaration, MonitorRefusal, WorklistData } from "@/lib/contract";

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
  /**
   * `ContextHeaderPayload.as_of` — set when the turn's measure is a
   * SNAPSHOT contract (an as-of balance at the watermark) rather than a
   * flow over a window. The payload still carries `window_start`/
   * `window_end` on those turns, and they are not what was measured: an
   * A/R-aging answer computed as of 2026-08-02 was published under a
   * "2026-07-01..2026-07-31 (service)" range, and the header, the caution
   * and the prose all described a scope the calculation never applied.
   *
   * When this is present the window chip states the as-of date and says
   * that no start..end range was applied, rather than repeating a range
   * the number does not honour.
   */
  asOf?: string;
  /**
   * True when this header was rebuilt from a stored investigation rather
   * than monitored as the turn streamed. The chips are the same facts; the
   * marker says where they came from, because a restored turn's stage
   * timings and composed prose were never persisted and the reader is
   * entitled to know which surfaces survived.
   */
  restored?: boolean;
  /**
   * `InvestigationResponse.restoration_notes` — the SERVER's own account
   * of what was rebuilt and what the store does not hold, rendered in the
   * Restored popover.
   *
   * It states things this client cannot know and must not guess. Live it
   * is two sentences: that the window, scope, cohort and watermark were
   * rebuilt from the turn's stored investigation spec rather than
   * re-computed (so the figures are the ones the turn published when it
   * ran), and that the narrative trace keeps its template, redactions and
   * length but not its sentences — which is a far more precise statement
   * of the limit than "the write-up was not stored".
   *
   * Absent on a payload generation that publishes none, and the popover
   * then falls back to its own static explanation.
   */
  restorationNotes?: string[];
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
  /**
   * This clarification was REBUILT from the stored investigation, not
   * received live — and the store keeps neither the question the engine
   * asked nor the interpretations it offered.
   *
   * The distinction is load-bearing and it is the whole of B-01. An empty
   * `options` list on a LIVE turn is a substantive claim ("there was
   * nothing answerable to offer"); the same empty list on a restored turn
   * is an unrestored field. Rendering the second as the first told a
   * prospect re-opening the permalink — the demo path — three times over
   * that the platform had had nothing to say, on turns that each offered
   * four real interpretations on the wire.
   */
  restored?: boolean;
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

/**
 * `BenchmarkPayload` — one governed external range for a metric.
 *
 * A RANGE, never a point target, and never separable from its context.
 * `cohortLabel`, `period`, `authority`, `cautions` and `reviewStatus` are
 * required on the wire for a reason the pack states outright: a figure
 * quoted without them is a different claim from the one its source made.
 * Live, every shipped entry is `machine_researched` — gathered by an
 * automated search of public sources and reviewed by nobody — and one
 * finding can carry seven of them, spanning ACA-marketplace plan-reported
 * denial shares and provider-side clearinghouse rates in the same list.
 *
 * `valueLow`/`valueHigh` are DECIMAL STRINGS on the wire and stay strings
 * here: they are the source's own figures and rounding them through a
 * float would edit a quotation.
 */
export interface Benchmark {
  id: string;
  metricId: string;
  /** Who the range describes ("ACA marketplace (HealthCare.gov issuers)…"). */
  cohortLabel: string;
  valueLow: string;
  valueHigh: string;
  /** The source's own words for the unit ("percent of in-network claims denied"). */
  unit: string;
  /** The source's period ("2023-2024", "2020 (27%) to Q3 2023 (36%)"). */
  period: string;
  authority: string;
  /** `machine_researched` for every figure shipped so far. */
  reviewStatus: string;
  /** What the range does NOT say — published with it, never separable. */
  cautions: string[];
  sources: string[];
}

/**
 * The finding's own headline measure as a NUMBER in its display unit —
 * `values[metricId]` scaled the same way the chart rows are (a `ratio`
 * frame publishes 0.295082 and this carries 29.5). Kept beside the
 * formatted string so a benchmark range can be stated against the figure
 * it is being quoted next to, in points, instead of the reader doing the
 * arithmetic from two differently-scaled numbers.
 */
export interface MeasuredValue {
  metricId: string;
  value: number;
  unit: "cents" | "percent" | "count" | "days";
  /**
   * The figure is a CEILING, not a measurement: the engine withheld a
   * small numerator and published an upper bound over a publishable
   * population instead (`<metric>__is_bound` on `FindingPayload.values`).
   *
   * This flag is the difference between "Veritas denies 76.9% of claims"
   * and "Veritas denies at most 76.9% of claims, over thirteen of them" —
   * two sentences a payer meeting would act on very differently. The wire
   * has carried it since wave B; the client dropped it at the first
   * mapper, so the hero stat printed a ceiling in the same treatment as a
   * measurement three cards above it.
   */
  isBound?: boolean;
  /** The population the ceiling was taken over (`<metric>__bound_population`). */
  boundPopulation?: number;
}

export interface Finding {
  referent: ReferentId;
  title: string;
  statement: string;
  metricRefs: string[];
  /**
   * Named scalar values backing the statement (kernel-certified).
   *
   * BOOLEANS ARE CARRIED. They used to be filtered out one line into the
   * mapper — the docstring said so outright — and `denial_rate__is_bound`
   * is a boolean, so the client could not learn that a finding was a
   * ceiling even though the wire had said so since wave B.
   */
  values: Record<string, number | string | boolean>;
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
  /**
   * `FindingPayload.benchmarks` — the governed external ranges the pack
   * publishes for this finding's measure. Up to seven per finding, and
   * until now read by nothing in this client: the only path any of them
   * took to a reader was inside a narrative sentence that asserted the
   * comparison with full confidence and carried neither the review status
   * nor the cautions the entry travels with.
   */
  benchmarks?: Benchmark[];
  /**
   * This finding's headline measure as a number in its display unit, when
   * the turn published a unit for it. Present for the same reason
   * `benchmarks` is: a range is only readable beside the figure it is
   * quoted against.
   */
  measured?: MeasuredValue;
  /**
   * The MOVEMENT this shape finding states runs between cells that are
   * ceilings, so it is not a measured change.
   *
   * Live: `"denial rate by month, 2026-01-01..2026-08-02: 7.5% → 9.0% (up
   * 1.5 points)"`, whose 2026-01 endpoint is a ceiling over 133 records
   * and whose 2026-06 endpoint is a ceiling over 111. A difference between
   * two upper bounds has no sign and no size — "≤ 1.5 points" is not a
   * weaker version of the claim, it is unknowable — and the card printed
   * the engine's sentence with no qualification at all. Only the narrative
   * said it, and the narrative is the surface with the least warranty.
   *
   * Derived at the wire seam by matching the finding's own `first`/`last`
   * values against the rows of the turn's temporal chart for the same
   * measure, so it lands whether or not the kernel grows a
   * `<metric>__is_bound` for shape findings — and when the kernel does
   * publish one, `measured.isBound` carries it and this stays consistent
   * with it rather than competing.
   */
  boundedMovement?: {
    /** The opening endpoint of the stated movement is a ceiling. */
    first: boolean;
    /** The closing endpoint of the stated movement is a ceiling. */
    last: boolean;
    /** The populations those ceilings were taken over, when published. */
    firstPopulation?: number;
    lastPopulation?: number;
  };
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
  /**
   * This row's value is an upper BOUND, not a measurement — the engine
   * withheld a small numerator and published a ceiling over a publishable
   * population instead (see `BoundedCell` server-side, R3-01).
   *
   * Read defensively at the seam: the structured flag is landing on the
   * wire in a later batch, and a payload generation that does not publish
   * it simply leaves this undefined. Nothing renders a bound it was not
   * told about — but the moment it arrives, the CSV carries it, because a
   * spreadsheet of ceilings that does not say so is the export most likely
   * to reach a payer.
   */
  bounded?: boolean;
  /** The published ceiling, in the chart's display unit, when the wire carries it. */
  bound?: number;
  /** The population the bound was taken over (`n`), when the wire carries it. */
  denominator?: number;
  /**
   * The point is not a settled measurement yet — a terminal bucket that is
   * calendar-partial or still adjudicating. Also read defensively: a
   * spreadsheet that presents a 23%-adjudicated month as final reports the
   * claims run-out as deterioration.
   */
  provisional?: boolean;
  /**
   * The engine published this category with NO value — the cell was
   * withheld outright under the small-cell policy.
   *
   * Set only when the wire sent a row for the category and the value was
   * absent, so it is never confused with a category the frame never
   * mentioned. It exists because the figure could not tell the difference:
   * a withheld cell and a measured 0.0% both drew as nothing at all, and
   * on a 150-cell ranking the engine's own sentence said "85 were withheld
   * outright and 13 are measured" over a picture showing 85 and 3 as the
   * same blank. The CSV has always been honest about this (`""` with a
   * `withheld` note); the chart is the surface that gets screenshotted.
   */
  withheld?: boolean;
  /**
   * The same facts as the four fields above, but per SERIES CELL rather
   * than per category — keyed by the series key in `values`.
   *
   * A comparison chart draws two marks per category (this window and the
   * one before it) and the wire flags them independently: a payer whose
   * JUNE numerator was suppressed publishes `is_bound` on the prior row
   * only. Carried on the row alone, that ceiling either desaturated BOTH
   * bars — telling the reader July was unmeasured when it was measured —
   * or, for `denominator`, kept whichever side's `n` arrived last and
   * printed it under both. The row-level flags stay: they are "this
   * category contains a ceiling", which is what the axis tick and the
   * ordering rule need, and they are what a payload generation without
   * per-cell detail still gives us.
   */
  cells?: Record<string, ChartCell>;
}

/** One (category, series) mark, and what the engine said about it. */
export interface ChartCell {
  /** This MARK is a ceiling over a suppressed numerator, not a measurement. */
  bounded?: boolean;
  /** The published ceiling, in the chart's display unit. */
  bound?: number;
  /** The population the ceiling was taken over (`n`). */
  denominator?: number;
  /**
   * There is NO figure here, and the zero on the wire is an artifact.
   *
   * The compare operator outer-joins the two windows and zero-fills
   * additive units, so a payer that appears only in the prior window
   * arrives with a current value of 0 — and the engine says so in its own
   * annotation ("their current mark is an absence, not a measured zero").
   * Drawn as a zero it reads as a collapse to nothing, which is the
   * opposite of what happened: nothing was measured at all.
   */
  absent?: boolean;
}

/**
 * One row exactly as the wire sent it, in the chart's display unit.
 *
 * Kept ONLY when the wire's rows collided under the axes the spec declares
 * — the export has to be able to carry what actually arrived, and a CSV
 * built from the collapsed cells would be the same understatement the
 * picture is, with a provenance block on top of it.
 */
export interface ChartWireRow {
  x: string;
  /** The series key this row was written to (the measure when `series` is null). */
  series: string;
  value?: number;
  referent?: string;
  bounded?: boolean;
  bound?: number;
  denominator?: number;
  provisional?: boolean;
}

/**
 * The wire sent more rows than its declared axes can tell apart.
 *
 * Live: a chart declaring `x=month, series=payer` published 30 rows over
 * 3 months × 1 payer. Keyed by `x` alone and written with `values[series]`,
 * 27 of them overwrote the other three and the figure drew $3,468 of
 * $441,808 — under a caveat block that said nothing about it.
 *
 * Two honest outcomes, and the unit decides which:
 *
 *   `summed` — dollars and counts add up, so the colliding rows are added
 *     and the census is stated. Nothing is lost and the total is checkable.
 *   `unkeyable` — percentages and day counts do not add up. There is no
 *     figure to draw, so none is drawn: the chart states what arrived and
 *     hands over the rows.
 */
export interface ChartKeying {
  /** The x column the spec declared. */
  xColumn: string;
  /** The series column the spec declared, or null for a single measure. */
  seriesColumn: string | null;
  /** Rows the wire sent. */
  wireRows: number;
  /** Distinct `(x, series)` keys among them. */
  keys: number;
  mode: "summed" | "unkeyable";
  /** Total of every wire value, in display unit. */
  wireTotal: number;
  /**
   * Total the DRAWN cells carry. Equal to `wireTotal` under `summed`; under
   * `unkeyable` this is what last-write-wins would have kept, and the gap
   * between the two is the money the old mapper dropped in silence.
   */
  drawnTotal: number;
  /** The sentence the figure and the CSV both print. */
  note: string;
  /** The wire's own rows, long format. */
  rows: ChartWireRow[];
}

export interface ChartSeries {
  key: string;
  label: string;
  /** Which validated series slot to use. */
  role: "current" | "baseline";
  /**
   * This series survives a series cap no matter how small it is. Set on
   * both halves of a period comparison (and on the baseline an old-shape
   * `__compare` frame is folded in as): the prior window is half of what a
   * comparison chart claims, and folding it into "+N others" because it
   * happens to be the smallest column would delete the comparison while
   * leaving the title that promises one.
   */
  pinned?: boolean;
}

export interface ChartSpec {
  id: string;
  /**
   * `ChartSpec.value` — the WAREHOUSE id of the measure this frame draws,
   * kept beside the composed title.
   *
   * The title is the reader's phrase ("Denied A/R dollars by payer") and
   * is deliberately not an identifier; a finding's `metricRefs` are
   * identifiers. The two have to be matched to answer "which of these four
   * figures is the one the write-up is about", and matching them on prose
   * would be a client parsing its own captions.
   */
  measureId?: string;
  /** The shape this UI draws — a reduction of `chart_type` (see contract.ts). */
  kind: "bar" | "grouped_bar" | "line";
  /**
   * The frame declared a COMPOSITION (`stacked_bar`): the series sum to the
   * category's total and the marks must share a stack. Drawn grouped, the
   * same payload asserts a comparison the frame never made.
   */
  stacked?: boolean;
  /**
   * How the rows were ordered, and by what. `wire` means "the order the
   * engine emitted", which is what a chart falls back to when nothing on
   * the payload resolves an order — never a silent alphabetical sort.
   *
   * `axis-order` is the one basis this client did not decide: the payload
   * published `axis_order`, the catalog's own declared order for an ordinal
   * dimension, and it outranks everything else including the shape
   * recognizer below it. `ordinal-bucket` is that recognizer — a fallback
   * for payloads that carry no declared order, and an INFERENCE, which is
   * why the two are named apart rather than collapsed into one caption.
   */
  order?: {
    basis: "wire" | "axis-order" | "ordinal-bucket" | "value" | "label";
    /** The column the order was taken on, when the wire named one. */
    by?: string;
    descending?: boolean;
    /**
     * How many bounded cells were held OUT of this order and seated after
     * it. A ceiling has no position in an order it was never measured
     * for — sorting the two together ranks by population size, which is
     * the sentence the engine's own refusal warning uses.
     */
    boundedExcluded?: number;
    /**
     * The turn REFUSED to publish a ranking (`RANKING_REFUSED`), so these
     * rows are the engine's emission order and the figure must not
     * present them as a rank. Set even though `basis` is `wire`, because
     * "nothing published an order" and "an order was refused on purpose"
     * are different facts and the caption says which.
     */
    refused?: boolean;
  };
  /**
   * How many of these rows are ceilings rather than measurements, when any
   * are. The figure draws them apart and says so under the picture.
   */
  boundedRows?: number;
  /**
   * A sentence the wire published ABOUT the figure rather than a category
   * on it ("upper bounds: 4 of 12 marks are ceilings, not measurements…").
   * `annotations[0]` was being fed to a `ReferenceLine` as an x value, so
   * a census the engine wrote reached no reader at all.
   *
   * The FIRST such sentence. See `notes` for the rest — a comparison chart
   * publishes its comparison sentence at index 0, which used to push the
   * upper-bound census off the figure entirely.
   */
  note?: string;
  /**
   * Every sentence the wire published about this figure, in wire order.
   *
   * Reading `annotations[0]` alone was survivable while a chart carried
   * one annotation. A two-series comparison carries up to five — the
   * comparison itself, the prior-only census, truncation, the upper-bound
   * census and the withheld census — and the comparison sentence is
   * always first, so every other fact the engine wrote about the picture
   * was dropped on exactly the charts that carry the most of them.
   */
  notes?: string[];
  /**
   * This chart draws TWO WINDOWS of one measure, one mark each per
   * category — the engine's `series: "period"` frame, whose rows are
   * labelled `current` and `prior`.
   *
   * Period is a presentation of one measure, not a second dimension of it,
   * and the two halves are NOT summed (the engine's own annotation says
   * so). Which is why this is a named field rather than "two series that
   * happen to be called current and prior": it is what forbids the stack a
   * `stacked_bar` chart_type would otherwise earn, and what tells the
   * tooltip there is a delta worth computing.
   */
  comparison?: {
    /** Series key holding this window's marks. */
    currentKey: string;
    /** Series key holding the window it is compared against. */
    priorKey: string;
  };
  /**
   * The rows the wire sent were NOT uniquely keyed by the axes this spec
   * declares. Present only when that happened — see `ChartKeying`.
   */
  keying?: ChartKeying;
  /** The published `ChartSpec.chart_type`, kept verbatim so the reduction
   *  is visible and reversible rather than lossy. */
  wireChartType?: string;
  /**
   * The published `ChartSpec.frame_id`.
   *
   * It used to be load-bearing: a comparison turn published `main` AND a
   * byte-identical `main__compare`, and the frame id was the only thing
   * that said which charts were the same question asked twice. The engine
   * now emits ONE chart per comparison (`series: "period"`, both windows
   * in it) and suppresses the superseded twin at the source, so on current
   * payloads this is provenance. `selectRenderableCharts` still reads it,
   * defensively, because restored investigations stored under the old
   * shape replay exactly as they were recorded.
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
  /**
   * The categories on this axis are WINDOWS, not members of a dimension.
   *
   * A frame with no dimension and no time bucket has nothing to key its
   * marks by, and both payload generations say so differently: the older
   * one fell back to the measure column (an axis reading `0.127591`), the
   * current one keys on the synthetic `period` column. Either way what the
   * axis distinguishes is one window from another, which changes three
   * things downstream — the ticks are dates, the marks are never re-sorted
   * by value, and ONE mark is a whole figure (a labelled column of a
   * measure over its window), not a lone point pretending to be a trend.
   */
  windowAxis?: true;
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
  /**
   * Replace the write-up rather than extend it.
   *
   * The live stream publishes a DRAFT of the prose and the terminal frame
   * publishes the composed one, and the two are not required to share a
   * prefix — the composer may rewrite what it streamed. The recovery path
   * used to append `narrative.slice(receivedLength)` unconditionally,
   * which is only a continuation when the stream really is a prefix; when
   * it is not, it welds the tail of one text onto the whole of another and
   * the reader gets a sentence restarting mid-word. Set when the terminal
   * narrative supersedes what was streamed.
   */
  replace?: boolean;
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
  /**
   * The internal probe names the collapsed entries differed by.
   *
   * One fact spelled six ways is still one fact. The server's dedupe keys
   * on `(code, message)`, so six `ALTERNATE_BASIS_USED` warnings differing
   * only by `probe 'main'` / `'premise'` / `'main__window'` … survive it
   * as six amber boxes above the verdict they bury. `dedupeWarnings`
   * collapses them on the fact and parks the probe names here: they are
   * operator material, recoverable in debug mode and in the count badge's
   * tooltip, and they are not a reason to print the same sentence six
   * times over an analyst's answer.
   */
  probes?: string[];
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
  /**
   * The ranked worklist this turn carried (`TurnAnswer.worklist`), when
   * its interpretation resolved the pack's governed worklist routing.
   * Rides on the terminal frame for the same reason the grade does: the
   * server publishes no `worklist` SSE frame, and it is only whole once
   * the turn is. Carried on a CLARIFICATION too — that is the whole point
   * of the bridge, since "what should my team work first" is exactly the
   * question that used to end in a clarification with the 33-card list
   * never mentioned.
   */
  worklist?: WorklistData;
  /**
   * `TurnAnswer.monitor` — this turn was a MONITOR DECLARATION ("monitor
   * Silverline's denial rate"), the platform compiled it, answered it, and
   * registered the monitor. The payload carries the confirmation sentence,
   * the compiled threshold and the baseline the monitor starts from.
   *
   * Absent on every ordinary turn, which is what makes it safe for a card
   * to render as a state change: a turn carrying this has a real pin
   * server-side.
   */
  monitor?: MonitorDeclaration;
  /**
   * `TurnAnswer.monitor_refused` — the same turn class, refused. The monitor
   * was NOT created and nothing is being monitored; the answer stands on its
   * own. Rendered where the confirmation would have been.
   */
  monitorRefused?: MonitorRefusal;
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

/**
 * One refinement edge: the operators that turned a parent investigation
 * into a child one.
 *
 * THREE IDS, TWO NAMESPACES — and the reason this interface names them so
 * explicitly. `parent_id` and `child_id` on the wire are INVESTIGATION
 * ids; `turn_id` is the TURN the edge belongs to. These were read as
 * `parentTurnId` / `childTurnId` and then joined against a node's
 * `turnId`, which is an id from the other namespace: the join never
 * matched, so no lineage edge in the live product ever showed the
 * operators that produced it. `turnId` is the join key.
 */
export interface LineageEdge {
  /** The investigation this refinement came FROM. */
  parentInvestigationId: string;
  /** The investigation this refinement PRODUCED. */
  childInvestigationId: string;
  /** The turn the edge belongs to — joins to `LineageNode.turnId`. */
  turnId: string;
  /**
   * The typed operators, already in display form ("SetDimensions(payer)").
   * The wire publishes them as objects; `describeWireOperator` renders
   * them in the same vocabulary `describeRefinement` uses for the ones
   * this client builds itself.
   */
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
