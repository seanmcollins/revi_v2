/**
 * Wire-contract guard AND the wire → UI translation layer.
 *
 * Typed-but-tolerant parsing:
 *   - unknown EXTRA fields are ignored (forward compatibility);
 *   - defaultable containers (empty lists/records) are filled in;
 *   - MISSING required fields are contract drift — the caller shows a
 *     visible banner and console.errors the exact field path. Never a
 *     silent blank UI.
 *
 * The REQUIRED_* path tables below name **published wire paths**, and that
 * is the correction this file exists to record. Through M13 they named the
 * UI's own shapes instead (`finding.referent.value`, `charts`, `status`),
 * so they agreed with the hand-written fixtures and disagreed with the
 * server: the suite was green while a live turn produced nothing but
 * drift. `lib/contract-expectations.test.ts` now pins them against payloads
 * captured from a running API, and `lib/contract-openapi.test.ts` binds
 * them to `contracts/openapi.json` (via the generated `lib/types.gen.ts`,
 * refreshed with `pnpm gen:types`) at both compile time and run time — so a
 * server-side rename fails a test instead of blanking a panel.
 *
 * Translating rather than re-typing the UI is deliberate: `types.gen.ts`
 * owns the wire, `lib/types.ts` owns the screen, and the mapping between
 * them lives here, in one place, with every reduction named (see "The
 * wire → UI seam" below).
 */

import type {
  ChartRow,
  ChartSpec,
  ClarificationData,
  ComparisonKind,
  Confidence,
  ContextHeaderData,
  DataWatermark,
  DateBasis,
  DebugInterpretationTrace,
  DebugLlmCallTrace,
  DebugProbeTrace,
  DebugTrace,
  DefinitionCardData,
  EvidenceBundle,
  EvidenceGrade,
  FilterClause,
  Finding,
  LineageEdge,
  LineageNode,
  MetricContractRef,
  MetricProvenance,
  PackVersionRef,
  ProbeEvidence,
  ReconciliationResult,
  Refinement,
  SessionLineageData,
  SessionListData,
  SessionSummary,
  StageId,
  SuggestedRefinement,
  TurnClass,
  TurnEvent,
  WarningEvent,
} from "@/lib/types";
import { GRADE_STRENGTH } from "@/lib/types";
import type { PortfolioItem } from "@/lib/mock/portfolio";

export type ParseResult<T> = { ok: true; value: T } | { ok: false; missing: string[] };

/* ------------------------------------------------------------------ */
/* Path helpers                                                        */
/* ------------------------------------------------------------------ */

type UnknownRecord = Record<string, unknown>;

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function getPath(root: unknown, path: string): unknown {
  let current: unknown = root;
  for (const key of path.split(".")) {
    if (!isRecord(current)) return undefined;
    current = current[key];
  }
  return current;
}

function missingAt(root: unknown, paths: readonly string[], out: string[]): void {
  for (const path of paths) {
    const value = getPath(root, path);
    if (value === undefined || value === null) out.push(path);
  }
}

/* ------------------------------------------------------------------ */
/* Stable error codes (design §12) + ErrorEnvelope                     */
/* ------------------------------------------------------------------ */

export const STABLE_ERROR_CODES: ReadonlySet<string> = new Set([
  "BINDING_AMBIGUOUS",
  "INSUFFICIENT_EVIDENCE",
  "UNSUPPORTED_CONCEPT",
  "POLICY_DENIED",
  "SOURCE_UNAVAILABLE",
  "QUERY_BUDGET_EXCEEDED",
  "AMBIGUOUS_REFINEMENT",
  "REFERENT_NOT_FOUND",
  "CONTEXT_CONFLICT",
  "GRAIN_INCOMPATIBLE",
  "DATE_BASIS_INVALID",
  "WATERMARK_STALE",
  "DATA_LOADING",
  "RECONCILIATION_FAILED",
  "SOURCE_CAPABILITY_UNSUPPORTED",
]);

export const REQUIRED_ERROR_ENVELOPE_FIELDS = [
  "code",
  "message",
  "correlation_id",
] as const;

export interface ErrorEnvelopeData {
  code: string;
  message: string;
  correlationId: string;
}

export function parseErrorEnvelope(raw: unknown): ParseResult<ErrorEnvelopeData> {
  const missing: string[] = [];
  missingAt(raw, REQUIRED_ERROR_ENVELOPE_FIELDS, missing);
  if (missing.length > 0) return { ok: false, missing };
  const record = raw as UnknownRecord;
  return {
    ok: true,
    value: {
      code: String(record.code),
      message: String(record.message),
      correlationId: String(record.correlation_id),
    },
  };
}

/* ------------------------------------------------------------------ */
/* POST /v1/sessions → SessionResponse (snake_case wire)               */
/* ------------------------------------------------------------------ */

export const REQUIRED_SESSION_FIELDS = [
  "session_id",
  "watermark.id",
  "watermark.loaded_at",
  "watermark.newest_data_date",
  "pack.pack_id",
  "pack.version",
] as const;

export interface SessionBootstrap {
  sessionId: string;
  watermark: DataWatermark;
  pack: PackVersionRef;
}

/**
 * The nested shape ({watermark: {...}, pack: {...}}) is canonical; the
 * flat spelling the live server emits (watermark_id, watermark_loaded_at,
 * newest_data_date, pack_id, pack_version) is tolerated as an alias.
 * Drift = missing in BOTH spellings, reported against the canonical path.
 */
export function parseSessionResponse(raw: unknown): ParseResult<SessionBootstrap> {
  if (!isRecord(raw)) return { ok: false, missing: [...REQUIRED_SESSION_FIELDS] };
  const missing: string[] = [];
  const watermark = isRecord(raw.watermark) ? raw.watermark : undefined;
  const pack = isRecord(raw.pack) ? raw.pack : undefined;
  const pick = (nested: unknown, flat: unknown, path: string): string => {
    const value = typeof nested === "string" ? nested : typeof flat === "string" ? flat : null;
    if (value === null) {
      missing.push(path);
      return "";
    }
    return value;
  };

  const sessionId = pick(raw.session_id, undefined, "session_id");
  const watermarkId = pick(watermark?.id, raw.watermark_id, "watermark.id");
  const loadedAt = pick(watermark?.loaded_at, raw.watermark_loaded_at, "watermark.loaded_at");
  const newestDataDate = pick(
    watermark?.newest_data_date,
    raw.newest_data_date,
    "watermark.newest_data_date",
  );
  const packId = pick(pack?.pack_id, raw.pack_id, "pack.pack_id");
  const version = pick(pack?.version, raw.pack_version, "pack.version");
  if (missing.length > 0) return { ok: false, missing };

  return {
    ok: true,
    value: {
      sessionId,
      watermark: {
        id: watermarkId,
        loadedAt: normalizeLoadedAt(loadedAt),
        newestDataDate,
      },
      pack: { packId, version },
    },
  };
}

/** "2026-08-03T04:10:00" (ISO wire) → "2026-08-03 04:10" (header verbatim). */
function normalizeLoadedAt(value: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(value);
  return match ? `${match[1]} ${match[2]}` : value;
}

/* ------------------------------------------------------------------ */
/* The wire → UI seam                                                  */
/* ------------------------------------------------------------------ */

/**
 * Everything below translates the **published** payloads
 * (`contracts/openapi.json`, mirrored into `lib/types.gen.ts`) into the
 * UI's own `TurnEvent` union. The two vocabularies are deliberately
 * different and neither is wrong:
 *
 *   the wire speaks the *domain* — `FindingPayload.referent` is the string
 *     "F1", a `ChartSpec` is a frame plus column names, a context header is
 *     window bounds and filter chips;
 *   the UI speaks *rendering* — a referent is `{value, kind}`, a chart is
 *     series and rows keyed for a chart library, a header is chips with
 *     display labels.
 *
 * Before this seam existed the parser simply asserted the UI vocabulary
 * against the server and every live frame was contract drift. What changed
 * is the direction of authority: `types.gen.ts` owns the wire, `types.ts`
 * owns the screen, and the mapping between them is written down here, once,
 * where it can be tested against real server output.
 *
 * Three facts the UI renders are **not** on the wire and are sourced from
 * the session pin (`POST /v1/sessions`, whose fields are spec-required):
 * the watermark's `loaded_at`, its `newest_data_date`, and the pack
 * version. `ContextHeaderPayload` publishes the watermark *id* only; the
 * mapper carries the id through verbatim so a header from another epoch
 * stays visibly different from the pin rather than being silently
 * relabelled.
 */

/** The session pin a header is rendered against (see above). */
export interface WirePin {
  watermark: DataWatermark;
  pack: PackVersionRef;
}

/** The nine SSE frame kinds `TurnStreamEvent.event` publishes. */
export type TurnFrameKind =
  | "stage"
  | "warning"
  | "clarification"
  | "context_header"
  | "finding"
  | "chart_spec"
  | "narrative_delta"
  | "error"
  | "turn_complete";

export const TURN_FRAME_KINDS: ReadonlySet<string> = new Set<TurnFrameKind>([
  "stage",
  "warning",
  "clarification",
  "context_header",
  "finding",
  "chart_spec",
  "narrative_delta",
  "error",
  "turn_complete",
]);

/**
 * Wire fields the UI cannot render a frame without, per published kind.
 * Every path here is `required` in the spec — `contract-openapi.test.ts`
 * asserts exactly that, so a field demoted to optional server-side fails
 * a test instead of blanking a panel. Containers whose emptiness is valid
 * (`rows`, `filter_chips`, `metric_ids`, `values`, `options`) and fields
 * with server-side defaults (`grade`, `confidence`, `watermark_id`) are
 * read tolerantly instead.
 */
export const REQUIRED_FRAME_FIELDS: Record<TurnFrameKind, readonly string[]> = {
  stage: ["stage"],
  warning: ["code"],
  clarification: ["question"],
  context_header: ["window_start", "window_end", "basis"],
  finding: ["referent", "title", "statement"],
  chart_spec: ["id", "chart_type", "title", "frame_id", "x", "value"],
  narrative_delta: ["delta"],
  error: ["code", "message", "correlation_id"],
  // the frame body is the full discriminated TurnResponse; the variant
  // tables below carry the rest
  turn_complete: ["outcome"],
};

/** `TurnAnswer | TurnClarification | TurnError`, discriminated on `outcome`. */
export const REQUIRED_ANSWER_FIELDS = [
  "outcome",
  "session_id",
  "investigation_id",
  "turn_class",
] as const;

export const REQUIRED_CLARIFICATION_FIELDS = [
  "outcome",
  "session_id",
  "investigation_id",
  "question",
] as const;

export const REQUIRED_TURN_ERROR_FIELDS = ["outcome", "error"] as const;

/** `GET /v1/investigations/{iid}` — the reconnect-recovery shape. */
export const REQUIRED_INVESTIGATION_FIELDS = [
  "investigation_id",
  "turn_id",
  "turn_class",
  "status",
  "created_at",
] as const;

const TURN_STATUSES: ReadonlySet<string> = new Set([
  "complete",
  "clarification_required",
  "failed",
]);

/* ------------------------------------------------------------------ */
/* Vocabulary reductions (each one named, none silent)                 */
/* ------------------------------------------------------------------ */

/**
 * Engine stage → the rail's coarser slot. The engine reports ten stages;
 * the rail shows eight, so three interpretation-family stages share one
 * slot. `findings` maps to the `reconciled` slot because findings
 * evaluation and the parent reconciliation are the same step of
 * `_run_analysis` and that slot is what reports it.
 */
const STAGE_BY_WIRE: Record<string, StageId> = {
  classify: "classified",
  interpret: "interpreted",
  definitional: "interpreted",
  resolve_referents: "interpreted",
  emit_refinements: "interpreted",
  plan: "planned",
  validate: "validated",
  execute: "executing",
  calculate: "calculating",
  findings: "reconciled",
  present: "narrating",
  narrate: "narrating",
};

/**
 * `ChartSpec.chart_type` → the three shapes `InvestigationChart` draws.
 * The reduction is lossy and deliberate: a waterfall renders as bars and a
 * range band as a line rather than being dropped, because a chart the UI
 * cannot draw *exactly* is still better than a blank panel — and the
 * published type travels on `wireChartType` so nothing is thrown away.
 */
const CHART_KIND_BY_WIRE: Record<string, ChartSpec["kind"]> = {
  bar: "bar",
  grouped_bar: "grouped_bar",
  stacked_bar: "grouped_bar",
  line: "line",
  waterfall: "bar",
  table: "bar",
  range_band: "line",
};

/** Kernel measure unit → the unit the formatters understand. */
const CHART_UNIT_BY_WIRE: Record<string, ChartSpec["unit"]> = {
  money_cents: "cents",
  ratio: "percent",
  percent: "percent",
  count: "count",
  days: "count",
};

const COMPARISON_KIND_BY_WIRE: Record<string, ComparisonKind> = {
  prior_period: "prior_period",
  prior_year: "same_period_last_year",
  custom: "custom",
};

/* ------------------------------------------------------------------ */
/* Payload mappers                                                     */
/* ------------------------------------------------------------------ */

/** camelCase is canonical (mirrors types.ts); snake_case is tolerated. */
function alias(record: UnknownRecord, camel: string, snake: string): unknown {
  return record[camel] ?? record[snake];
}

function asString(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

/** `[{name, value}]` → `{name: value}`, dropping nulls and booleans. */
function mapFindingValues(raw: unknown): Record<string, number | string> {
  const out: Record<string, number | string> = {};
  for (const entry of asArray(raw)) {
    if (!isRecord(entry) || typeof entry.name !== "string") continue;
    const value = entry.value;
    if (typeof value === "number" || typeof value === "string") out[entry.name] = value;
  }
  return out;
}

const DRILL_SUGGESTION = /^drill into ([FD]\d+)$/i;

function mapSuggestedRefinements(raw: unknown): SuggestedRefinement[] {
  const out: SuggestedRefinement[] = [];
  for (const entry of asArray(raw)) {
    if (typeof entry !== "string") continue;
    const match = DRILL_SUGGESTION.exec(entry.trim());
    // Only suggestions that compile to a typed operator become actions;
    // free text stays out of the gesture loop by design (§7.4).
    if (match) out.push({ label: entry, refinement: { op: "DrillInto", target: match[1] } });
  }
  return out;
}

export function mapFinding(raw: unknown): Finding | null {
  if (!isRecord(raw)) return null;
  const values = mapFindingValues(raw.values);
  const impactCents = asNumber(raw.impact_cents);
  return {
    referent: { value: asString(raw.referent), kind: "finding" },
    title: asString(raw.title),
    statement: asString(raw.statement),
    metricRefs: asArray(raw.metric_ids).filter((m): m is string => typeof m === "string"),
    values,
    grade: asString(raw.grade, "direct") as EvidenceGrade,
    ...(impactCents !== undefined ? { impactCents } : {}),
    // A movement finding carries a signed delta; a concentration finding
    // carries a level. The wire says which by which value it published.
    impactKind: "delta_cents" in values ? "delta" : "level",
    // NOT published per finding: direction-of-good lives on the metric
    // contract, not on the payload, so tone colouring is withheld rather
    // than guessed from the sign of the number.
    directionOfGood: "neutral",
    confidence: asString(raw.confidence, "high") as Confidence,
    suggestedRefinements: mapSuggestedRefinements(raw.suggested_refinements),
  };
}

export function mapChartSpec(raw: unknown): ChartSpec | null {
  if (!isRecord(raw)) return null;
  const wireType = asString(raw.chart_type);
  const valueColumn = asString(raw.value);
  const seriesColumn = typeof raw.series === "string" ? raw.series : null;

  const seriesKeys: string[] = [];
  const rowsByX = new Map<string, ChartRow>();
  for (const entry of asArray(raw.rows)) {
    if (!isRecord(entry)) continue;
    const label = asString(entry.x);
    // A null `series` means one series named by the measure column; a
    // non-null one means the rows are already long-format by series.
    const key = typeof entry.series === "string" ? entry.series : valueColumn;
    if (!seriesKeys.includes(key)) seriesKeys.push(key);
    const row = rowsByX.get(label) ?? { label, values: {} };
    const value = asNumber(entry.value);
    if (value !== undefined) row.values[key] = value;
    if (typeof entry.referent_id === "string") row.referent = entry.referent_id;
    rowsByX.set(label, row);
  }
  if (seriesKeys.length === 0) seriesKeys.push(valueColumn);

  return {
    id: asString(raw.id),
    kind: CHART_KIND_BY_WIRE[wireType] ?? "bar",
    wireChartType: wireType,
    title: asString(raw.title),
    unit: CHART_UNIT_BY_WIRE[asString(raw.unit)] ?? "count",
    xLabel: asString(raw.x) || undefined,
    series: seriesKeys.map((key, index) => ({
      key,
      label: seriesColumn === null ? valueColumn : key,
      role: index === 0 ? "current" : "baseline",
    })),
    rows: [...rowsByX.values()],
    ...(asArray(raw.annotations).length > 0
      ? { highlightLabel: String(asArray(raw.annotations)[0]) }
      : {}),
  };
}

export function mapContextHeader(raw: unknown, pin: WirePin): ContextHeaderData | null {
  if (!isRecord(raw)) return null;
  const basis = asString(raw.basis) as DateBasis;
  const comparisonStart = raw.comparison_start;
  const comparisonEnd = raw.comparison_end;
  const cohortId = raw.cohort;
  const cohortSize = asNumber(raw.cohort_size);

  const filters: FilterClause[] = [];
  for (const chip of asArray(raw.filter_chips)) {
    if (!isRecord(chip)) continue;
    filters.push({
      dimension: asString(chip.dimension),
      // The wire publishes ids, not display labels; the id IS the label
      // until a labelling endpoint exists, and inventing one would be a
      // second vocabulary nobody governs.
      dimensionLabel: asString(chip.dimension),
      op: asString(chip.op, "eq") as FilterClause["op"],
      values: asArray(chip.values).map((v) => String(v)),
      originTurn: asString(chip.origin_turn),
      ...(chip.pinned === true ? { pinned: true } : {}),
    });
  }

  return {
    window: { start: asString(raw.window_start), end: asString(raw.window_end), basis },
    ...(typeof comparisonStart === "string" && typeof comparisonEnd === "string"
      ? {
          comparison: {
            kind: COMPARISON_KIND_BY_WIRE[asString(raw.comparison_kind)] ?? "custom",
            window: { start: comparisonStart, end: comparisonEnd, basis },
          },
        }
      : {}),
    filters,
    ...(typeof cohortId === "string"
      ? {
          cohort: {
            id: cohortId,
            definition: cohortId,
            pinned: true,
            originTurn: "",
            size: cohortSize ?? 0,
          },
        }
      : {}),
    // The watermark ID is the header's own; the load time and pack pin come
    // from the session (see the seam docstring).
    watermark: { ...pin.watermark, id: asString(raw.watermark_id, pin.watermark.id) },
    packVersion: pin.pack,
  };
}

export function mapDefinitional(raw: unknown, pin: WirePin): DefinitionCardData | null {
  if (!isRecord(raw)) return null;
  const terms = asArray(raw.terms).filter(isRecord);
  const primary = terms[0];
  if (!primary) return null;
  return {
    term: asString(raw.question) || asString(primary.term),
    normalizedTo: terms.map((t) => asString(t.title)).join(" · "),
    definition: terms.map((t) => asString(t.definition)).join(" "),
    sources: terms
      .filter((t) => typeof t.source === "string")
      .map((t) => ({ label: String(t.source), authority: "governed_pack" as const })),
    packVersion: {
      packId: asString(raw.pack_id, pin.pack.packId),
      version: asString(raw.pack_version, pin.pack.version),
    },
    relatedConcepts: terms.slice(1).map((t) => asString(t.term)),
  };
}

/* ------------------------------------------------------------------ */
/* DebugTracePayload → DebugTrace (internal debug mode only)           */
/* ------------------------------------------------------------------ */

/**
 * The four identity fields the payload marks `required`; everything else
 * is defaulted server-side and read tolerantly here. A trace with no
 * probes and no LLM calls is a real trace (a zero-probe, zero-model turn),
 * not drift.
 */
export const REQUIRED_DEBUG_TRACE_FIELDS = [
  "trace_id",
  "session_id",
  "investigation_id",
  "turn_id",
] as const;

function asRecordList(value: unknown): UnknownRecord[] {
  return asArray(value).filter(isRecord);
}

function asStringMap(value: unknown): Record<string, string> {
  if (!isRecord(value)) return {};
  const out: Record<string, string> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === "string") out[key] = entry;
  }
  return out;
}

function asNumberMap(value: unknown): Record<string, number> {
  if (!isRecord(value)) return {};
  const out: Record<string, number> = {};
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry === "number") out[key] = entry;
  }
  return out;
}

function asStringList(value: unknown): string[] {
  return asArray(value).filter((entry): entry is string => typeof entry === "string");
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

function mapDebugInterpretation(raw: unknown): DebugInterpretationTrace | undefined {
  if (!isRecord(raw)) return undefined;
  return {
    intentSummary: asString(raw.intent_summary),
    metricIds: asStringList(raw.metric_ids),
    dimensionIds: asStringList(raw.dimension_ids),
    conceptIds: asStringList(raw.concept_ids),
    ...(optionalString(raw.playbook_id) ? { playbookId: String(raw.playbook_id) } : {}),
    ...(optionalString(raw.window_start) ? { windowStart: String(raw.window_start) } : {}),
    ...(optionalString(raw.window_end) ? { windowEnd: String(raw.window_end) } : {}),
    ...(optionalString(raw.basis) ? { basis: String(raw.basis) } : {}),
  };
}

function mapDebugProbe(raw: UnknownRecord): DebugProbeTrace {
  return {
    id: asString(raw.id),
    hash: asString(raw.hash),
    purpose: asString(raw.purpose),
    cacheHit: raw.cache_hit === true,
    rows: typeof raw.rows === "number" ? raw.rows : null,
    limit: typeof raw.limit === "number" ? raw.limit : null,
    truncated: raw.truncated === true,
    suppressedCells: asNumber(raw.suppressed_cells) ?? 0,
    ...(optionalString(raw.grade) ? { grade: String(raw.grade) } : {}),
    durationMs: asNumber(raw.duration_ms) ?? 0,
  };
}

function mapDebugLlmCall(raw: UnknownRecord): DebugLlmCallTrace {
  return {
    template: asString(raw.template),
    model: asString(raw.model),
    inputTokens: asNumber(raw.input_tokens) ?? 0,
    outputTokens: asNumber(raw.output_tokens) ?? 0,
    costUsd: asString(raw.cost_usd, "0"),
    schemaRetries: asNumber(raw.schema_retries) ?? 0,
    attempts: asNumber(raw.attempts) ?? 1,
    durationMs: asNumber(raw.duration_ms) ?? 0,
    ...(optionalString(raw.failure) ? { failure: String(raw.failure) } : {}),
  };
}

/**
 * `DebugTracePayload` → the UI's `DebugTrace`. Returns null when the
 * payload is absent or missing its identity fields: debug rendering is an
 * internal explanation surface, so a half-read trace is worse than none —
 * it would invite conclusions from fields that were never there.
 */
export function mapDebugTrace(raw: unknown): DebugTrace | null {
  if (!isRecord(raw)) return null;
  const missing: string[] = [];
  missingAt(raw, REQUIRED_DEBUG_TRACE_FIELDS, missing);
  if (missing.length > 0) return null;
  const settings = isRecord(raw.settings) ? raw.settings : {};
  return {
    traceId: asString(raw.trace_id),
    sessionId: asString(raw.session_id),
    investigationId: asString(raw.investigation_id),
    turnId: asString(raw.turn_id),
    settings: {
      modelTier: typeof settings.model_tier === "string" ? settings.model_tier : null,
      maxTurnCostUsd:
        typeof settings.max_turn_cost_usd === "string" ? settings.max_turn_cost_usd : null,
      narrativeDepth: asString(settings.narrative_depth, "summary"),
      evidenceDepth: asString(settings.evidence_depth, "standard"),
      debug: settings.debug === true,
    },
    ...(optionalString(raw.question) ? { question: String(raw.question) } : {}),
    ...(optionalString(raw.turn_class) ? { turnClass: String(raw.turn_class) } : {}),
    ...(typeof raw.classification_confidence === "number"
      ? { classificationConfidence: raw.classification_confidence }
      : {}),
    ...(mapDebugInterpretation(raw.interpretation) !== undefined
      ? { interpretation: mapDebugInterpretation(raw.interpretation) }
      : {}),
    refinementOperators: asRecordList(raw.refinement_operators),
    ...(optionalString(raw.refinement_rationale)
      ? { refinementRationale: String(raw.refinement_rationale) }
      : {}),
    referentResolutions: asRecordList(raw.referent_resolutions),
    ...(optionalString(raw.clarification_reason)
      ? { clarificationReason: String(raw.clarification_reason) }
      : {}),
    ...(optionalString(raw.plan_hash) ? { planHash: String(raw.plan_hash) } : {}),
    ...(optionalString(raw.playbook_id) ? { playbookId: String(raw.playbook_id) } : {}),
    probes: asRecordList(raw.probes).map(mapDebugProbe),
    grades: asStringMap(raw.grades),
    ...(optionalString(raw.weakest_grade) ? { weakestGrade: String(raw.weakest_grade) } : {}),
    findingGrades: asStringMap(raw.finding_grades),
    calculationOperators: asRecordList(raw.calculation_operators),
    ...(optionalString(raw.reconciliation) ? { reconciliation: String(raw.reconciliation) } : {}),
    warnings: asStringList(raw.warnings),
    llmCalls: asRecordList(raw.llm_calls).map(mapDebugLlmCall),
    templateHashes: asStringMap(raw.template_hashes),
    timingsMs: asNumberMap(raw.timings_ms),
    watermarkId: asString(raw.watermark_id),
    watermarkStale: raw.watermark_stale === true,
    epoch: asNumber(raw.epoch) ?? 0,
    reAnchored: raw.re_anchored === true,
    packId: asString(raw.pack_id),
    packVersion: asString(raw.pack_version),
    packSnapshotId: asString(raw.pack_snapshot_id),
    redactions: asStringList(raw.redactions),
  };
}

/**
 * A `warning` SSE frame: a stable §12 code plus code-specific detail. The
 * UI needs one sentence, so the known codes get a written one and anything
 * else falls back to the detail the server sent rather than to the bare
 * code.
 */
export function mapWarningFrame(raw: unknown): Omit<WarningEvent, "type"> | null {
  if (!isRecord(raw) || typeof raw.code !== "string") return null;
  const code = raw.code;
  if (code === "WATERMARK_STALE") {
    return {
      code,
      message: `The warehouse has loaded ${asString(raw.newest, "a newer watermark")} since this session pinned ${asString(raw.pinned, "its watermark")}. Results stay pinned until you re-anchor.`,
      severity: "caution",
    };
  }
  if (code === "RECONCILIATION_FAILED") {
    return {
      code,
      message: `These children do not reconcile to their parent: ${asString(raw.detail, "no detail supplied")}.`,
      severity: "caution",
    };
  }
  return {
    code,
    message: asString(raw.detail) || asString(raw.message) || code,
    severity: STABLE_ERROR_CODES.has(code) ? "caution" : "info",
  };
}

/**
 * `TurnAnswer.warnings` is a list of sentences, not structured codes. Ones
 * that begin with a stable §12 code keep it; the rest are notes, and are
 * labelled as notes rather than being dressed up as codes.
 */
export function mapAnswerWarning(sentence: string): Omit<WarningEvent, "type"> {
  const [head, ...rest] = sentence.split(": ");
  if (rest.length > 0 && STABLE_ERROR_CODES.has(head)) {
    return { code: head, message: rest.join(": "), severity: "caution" };
  }
  return { code: "ANSWER_NOTE", message: sentence, severity: "info" };
}

/* ------------------------------------------------------------------ */
/* EvidencePayload → EvidenceBundle (the drawer, banner, cache chip)    */
/* ------------------------------------------------------------------ */

/**
 * The bundle the server projects from the turn's recorded trace — the
 * same record `GET /v1/investigations/{iid}/trace` reads, so the drawer's
 * row counts and the debug panel's cannot disagree.
 *
 * This mapper renames and nothing else. Where the wire is silent the UI
 * is silent: a probe with no `rows` (planned, never executed) gets no row
 * count rather than a zero, an unparseable grade is dropped rather than
 * defaulted to `direct`, and a turn with no recorded verdict gets no
 * reconciliation object rather than a synthesized "not applicable". Every
 * one of those defaults would have been a claim the server never made.
 */
const EVIDENCE_GRADES: ReadonlySet<string> = new Set(Object.keys(GRADE_STRENGTH));

const RECONCILIATION_STATUSES: ReadonlySet<string> = new Set([
  "passed",
  "passed_with_suppression",
  "failed",
  "not_applicable",
]);

function asGrade(value: unknown): EvidenceGrade | undefined {
  return typeof value === "string" && EVIDENCE_GRADES.has(value)
    ? (value as EvidenceGrade)
    : undefined;
}

function mapProbeEvidence(raw: UnknownRecord): ProbeEvidence {
  const grade = asGrade(raw.grade);
  const rowCount = asNumber(raw.rows);
  const limit = asNumber(raw.limit);
  return {
    probeId: asString(raw.id),
    probeHash: asString(raw.hash),
    kind: asString(raw.kind),
    description: asString(raw.purpose),
    metrics: asRecordList(raw.metrics).map((metric) => {
      const version = asNumber(metric.contract_version);
      return {
        id: asString(metric.id),
        ...(version !== undefined ? { contractVersion: version } : {}),
      };
    }),
    cacheHit: raw.cache_hit === true,
    ...(rowCount !== undefined ? { rowCount } : {}),
    ...(limit !== undefined ? { limit } : {}),
    truncated: raw.truncated === true,
    suppressedCells: asNumber(raw.suppressed_cells) ?? 0,
    ...(grade !== undefined ? { grade } : {}),
    durationMs: asNumber(raw.duration_ms) ?? 0,
  };
}

function mapReconciliation(raw: unknown): ReconciliationResult | undefined {
  if (!isRecord(raw)) return undefined;
  const status = asString(raw.status);
  return {
    // A status this build does not know is reported as `unknown`, never
    // rounded toward the reassuring end of the scale.
    status: RECONCILIATION_STATUSES.has(status)
      ? (status as ReconciliationResult["status"])
      : "unknown",
    ...(optionalString(raw.detail) ? { detail: String(raw.detail) } : {}),
    summary: asString(raw.summary),
  };
}

export function mapEvidence(raw: unknown): EvidenceBundle | undefined {
  if (!isRecord(raw)) return undefined;
  const reconciliation = mapReconciliation(raw.reconciliation);
  const answerGrade = asGrade(raw.answer_grade);
  return {
    probes: asRecordList(raw.probes).map(mapProbeEvidence),
    ...(reconciliation !== undefined ? { reconciliation } : {}),
    warehouseQueries: asNumber(raw.warehouse_queries) ?? 0,
    cacheHits: asNumber(raw.cache_hits) ?? 0,
    zeroProbeTurn: raw.zero_probe_turn === true,
    ...(answerGrade !== undefined ? { answerGrade } : {}),
  };
}

/* ------------------------------------------------------------------ */
/* MetricProvenancePayload → MetricProvenance (the "Governed" badge)    */
/* ------------------------------------------------------------------ */

/**
 * `TurnAnswer.metric` — the governed provenance of the answer's numbers,
 * projected server-side from the same recorded trace as the evidence
 * bundle. This mapper renames and nothing else.
 *
 * `undefined` in, `undefined` out: a server that published no block gets
 * no badge, rather than a badge asserting an empty pack. A block whose
 * `metrics` list is empty is a different fact — the turn measured nothing
 * governed (a definitional answer, a META citation) — and is mapped
 * through so the badge can decline to render it for the right reason.
 */
function mapMetricRef(raw: UnknownRecord): MetricContractRef {
  const version = asNumber(raw.contract_version);
  return {
    id: asString(raw.id),
    ...(version !== undefined ? { contractVersion: version } : {}),
  };
}

export function mapMetricProvenance(raw: unknown): MetricProvenance | undefined {
  if (!isRecord(raw)) return undefined;
  const primary = isRecord(raw.primary) ? mapMetricRef(raw.primary) : undefined;
  return {
    ...(primary !== undefined ? { primary } : {}),
    metrics: asRecordList(raw.metrics).map(mapMetricRef),
    ...(optionalString(raw.playbook_id) ? { playbookId: String(raw.playbook_id) } : {}),
    // The pack the TURN recorded, not the session pin: an answer given
    // before a pack promotion must keep naming the version that defined
    // it, and this is the one place the UI can tell the difference.
    pack: { packId: asString(raw.pack_id), version: asString(raw.pack_version) },
    ...(optionalString(raw.pack_snapshot_id)
      ? { packSnapshotId: String(raw.pack_snapshot_id) }
      : {}),
  };
}

/* ------------------------------------------------------------------ */
/* SSE frame parsing                                                   */
/* ------------------------------------------------------------------ */

/**
 * Validate one decoded SSE frame against the required *wire* paths and map
 * it to the UI event union. `null` means an unpublished frame kind
 * (forward compatibility — skipped, never crashed on); `ok: false` means
 * the frame is drift and must NOT be rendered.
 *
 * `turn_complete` is not handled here: its body is the whole
 * `TurnResponse`, which `parseTurnResponse` owns.
 */
export function parseTurnFrame(
  kind: string,
  data: unknown,
  pin: WirePin,
): ParseResult<TurnEvent> | null {
  if (!TURN_FRAME_KINDS.has(kind)) return null;
  const frameKind = kind as TurnFrameKind;
  const missing: string[] = [];
  missingAt(data, REQUIRED_FRAME_FIELDS[frameKind], missing);
  if (missing.length > 0) return { ok: false, missing };
  const raw = data as UnknownRecord;

  switch (frameKind) {
    case "stage": {
      const stage = STAGE_BY_WIRE[asString(raw.stage)];
      // An engine stage the rail has no slot for is skipped, not drift:
      // progress frames are advisory and the answer does not depend on them.
      if (stage === undefined) return null;
      return { ok: true, value: { type: "stage", stage, status: "started" } };
    }
    case "warning": {
      const warning = mapWarningFrame(raw);
      if (warning === null) return { ok: false, missing: ["code"] };
      return { ok: true, value: { type: "warning", ...warning } };
    }
    case "clarification":
      return {
        ok: true,
        value: {
          type: "clarification",
          clarification: {
            question: asString(raw.question),
            options: asArray(raw.options).map((o) => String(o)),
            ...(typeof raw.reason === "string" ? { reason: raw.reason } : {}),
          },
        },
      };
    case "context_header": {
      const header = mapContextHeader(raw, pin);
      if (header === null) return { ok: false, missing: [...REQUIRED_FRAME_FIELDS.context_header] };
      // The frame does not carry the turn class; the header panel needs
      // one, and `new_investigation` is the only honest default before
      // `turn_complete` says otherwise (it overwrites this).
      return { ok: true, value: { type: "context_header", header, turnClass: "new_investigation" } };
    }
    case "finding": {
      const finding = mapFinding(raw);
      if (finding === null) return { ok: false, missing: [...REQUIRED_FRAME_FIELDS.finding] };
      return { ok: true, value: { type: "finding", finding } };
    }
    case "chart_spec": {
      const spec = mapChartSpec(raw);
      if (spec === null) return { ok: false, missing: [...REQUIRED_FRAME_FIELDS.chart_spec] };
      return { ok: true, value: { type: "chart_spec", spec } };
    }
    case "narrative_delta":
      return { ok: true, value: { type: "narrative_delta", text: asString(raw.delta) } };
    case "error":
      return {
        ok: true,
        value: {
          type: "error",
          code: asString(raw.code),
          message: asString(raw.message),
          correlationId: asString(raw.correlation_id),
        },
      };
    case "turn_complete":
      // Handled by the caller via parseTurnResponse — the frame body is the
      // authoritative TurnResponse, not a summary of it.
      return null;
  }
}

/* ------------------------------------------------------------------ */
/* TurnResponse — the discriminated 200 body                           */
/* ------------------------------------------------------------------ */

/**
 * The parsed 200 body, discriminated exactly as the wire is. Earlier
 * milestones flattened all three outcomes onto one `status` string; that
 * is what made a clarification indistinguishable from an answer with no
 * findings, and a `TurnError` (which carries no investigation id at all)
 * impossible to represent.
 */
export type TurnResponseData =
  | {
      outcome: "answer";
      investigationId: string;
      sessionId: string;
      turnClass: TurnClass;
      header?: ContextHeaderData;
      findings: Finding[];
      charts: ChartSpec[];
      narrative?: string;
      warnings: Omit<WarningEvent, "type">[];
      definition?: DefinitionCardData;
      reconciliation?: string;
      planHash?: string;
      watermarkStale: boolean;
      /**
       * The answer's own working — probes, verdict, cache reuse. Present
       * on every live answer and on a restored turn whose trace survived;
       * absent means the server published none, which the drawer says in
       * those words rather than drawing an empty-but-reassuring one.
       */
      evidence?: EvidenceBundle;
      /**
       * Whose definition the numbers are: the governed metric contract(s)
       * at the versions they were read at, the playbook that chose them,
       * and the pack the turn was pinned to. Absent means the server
       * published no block (no trace to project) — the badge then says
       * nothing rather than implying an ungoverned answer.
       */
      metric?: MetricProvenance;
      /** Published only when the settings in force had debug on. */
      debug?: DebugTrace;
    }
  | {
      outcome: "clarification_required";
      investigationId: string;
      sessionId: string;
      clarification: ClarificationData;
      watermarkStale: boolean;
      debug?: DebugTrace;
    }
  | { outcome: "error"; sessionId?: string; error: ErrorEnvelopeData };

export interface TurnResponseParse {
  /** Null when required fields are missing (fatal drift). */
  value: TurnResponseData | null;
  /** Every missing required path found. */
  drift: string[];
}

/** Parse `POST .../turns` (application/json) or a `turn_complete` frame. */
export function parseTurnResponse(raw: unknown, pin: WirePin): TurnResponseParse {
  if (!isRecord(raw)) return { value: null, drift: ["outcome"] };
  const outcome = raw.outcome;
  if (typeof outcome !== "string") return { value: null, drift: ["outcome"] };

  if (outcome === "error") {
    const drift: string[] = [];
    missingAt(raw, REQUIRED_TURN_ERROR_FIELDS, drift);
    if (drift.length > 0) return { value: null, drift };
    const envelope = parseErrorEnvelope(raw.error);
    if (!envelope.ok) {
      return { value: null, drift: envelope.missing.map((path) => `error.${path}`) };
    }
    return {
      value: {
        outcome: "error",
        ...(typeof raw.session_id === "string" ? { sessionId: raw.session_id } : {}),
        error: envelope.value,
      },
      drift: [],
    };
  }

  if (outcome === "clarification_required") {
    const drift: string[] = [];
    missingAt(raw, REQUIRED_CLARIFICATION_FIELDS, drift);
    if (drift.length > 0) return { value: null, drift };
    const clarificationTrace = mapDebugTrace(raw.debug);
    return {
      value: {
        outcome: "clarification_required",
        investigationId: asString(raw.investigation_id),
        sessionId: asString(raw.session_id),
        clarification: {
          question: asString(raw.question),
          options: asArray(raw.options).map((o) => String(o)),
          ...(typeof raw.reason === "string" ? { reason: raw.reason } : {}),
        },
        watermarkStale: raw.watermark_stale === true,
        ...(clarificationTrace !== null ? { debug: clarificationTrace } : {}),
      },
      drift: [],
    };
  }

  if (outcome !== "answer") return { value: null, drift: ["outcome"] };

  const drift: string[] = [];
  missingAt(raw, REQUIRED_ANSWER_FIELDS, drift);
  if (drift.length > 0) return { value: null, drift };

  const findings: Finding[] = [];
  asArray(raw.findings).forEach((entry, index) => {
    const finding = mapFinding(entry);
    if (finding === null || finding.referent.value === "") {
      drift.push(`findings[${index}].referent`);
      return;
    }
    findings.push(finding);
  });

  const charts: ChartSpec[] = [];
  asArray(raw.chart_specs).forEach((entry, index) => {
    const spec = mapChartSpec(entry);
    if (spec === null || spec.id === "") {
      drift.push(`chart_specs[${index}].id`);
      return;
    }
    charts.push(spec);
  });

  const header = raw.context_header != null ? mapContextHeader(raw.context_header, pin) : null;
  const definition = raw.definitional != null ? mapDefinitional(raw.definitional, pin) : null;
  const trace = mapDebugTrace(raw.debug);
  const evidence = mapEvidence(raw.evidence);
  const metric = mapMetricProvenance(raw.metric);

  return {
    value: {
      outcome: "answer",
      investigationId: asString(raw.investigation_id),
      sessionId: asString(raw.session_id),
      turnClass: asString(raw.turn_class) as TurnClass,
      ...(header !== null ? { header } : {}),
      findings,
      charts,
      ...(typeof raw.narrative === "string" && raw.narrative !== ""
        ? { narrative: raw.narrative }
        : {}),
      warnings: asArray(raw.warnings)
        .filter((w): w is string => typeof w === "string")
        .map(mapAnswerWarning),
      ...(definition !== null ? { definition } : {}),
      ...(typeof raw.reconciliation === "string" ? { reconciliation: raw.reconciliation } : {}),
      ...(typeof raw.plan_hash === "string" ? { planHash: raw.plan_hash } : {}),
      watermarkStale: raw.watermark_stale === true,
      ...(evidence !== undefined ? { evidence } : {}),
      ...(metric !== undefined ? { metric } : {}),
      ...(trace !== null ? { debug: trace } : {}),
    },
    drift,
  };
}

/**
 * Parse `GET /v1/investigations/{iid}` — a different shape from the turn
 * body and the only recovery path when the stream dropped after the
 * investigation id was known. It carries findings and warnings but no
 * header, charts or narrative, so a recovered turn renders what the server
 * actually kept rather than pretending the rest arrived.
 */
export function parseInvestigationResponse(raw: unknown): TurnResponseParse {
  const drift: string[] = [];
  if (!isRecord(raw)) return { value: null, drift: [...REQUIRED_INVESTIGATION_FIELDS] };
  missingAt(raw, REQUIRED_INVESTIGATION_FIELDS, drift);
  const status = asString(raw.status);
  if (!TURN_STATUSES.has(status)) drift.push("status");
  if (drift.length > 0) return { value: null, drift };

  const investigationId = asString(raw.investigation_id);
  if (status === "clarification_required") {
    return {
      value: {
        outcome: "clarification_required",
        investigationId,
        sessionId: asString(raw.session_id),
        clarification: {
          question: "This turn ended with a question — resend it to see the options again.",
          options: [],
        },
        watermarkStale: false,
      },
      drift,
    };
  }
  if (status === "failed") {
    return {
      value: {
        outcome: "error",
        sessionId: asString(raw.session_id),
        error: {
          code: "TURN_FAILED",
          message: asArray(raw.warnings).map(String).join(" ") || "This turn failed server-side.",
          correlationId: investigationId,
        },
      },
      drift,
    };
  }

  const findings: Finding[] = [];
  asArray(raw.findings).forEach((entry, index) => {
    const finding = mapFinding(entry);
    if (finding === null || finding.referent.value === "") {
      drift.push(`findings[${index}].referent`);
      return;
    }
    findings.push(finding);
  });

  // What the server kept, and only that: the charts are rebuilt from the
  // frames this turn persisted and the evidence bundle is projected from
  // its recorded trace, so both are the same objects the live answer
  // published. The narrative is not among them — nothing stores the
  // composed prose — and a restored turn says so rather than filling in.
  const charts: ChartSpec[] = [];
  asArray(raw.chart_specs).forEach((entry, index) => {
    const spec = mapChartSpec(entry);
    if (spec === null || spec.id === "") {
      drift.push(`chart_specs[${index}].id`);
      return;
    }
    charts.push(spec);
  });
  const evidence = mapEvidence(raw.evidence);
  const metric = mapMetricProvenance(raw.metric);

  return {
    value: {
      outcome: "answer",
      investigationId,
      sessionId: asString(raw.session_id),
      turnClass: asString(raw.turn_class) as TurnClass,
      findings,
      charts,
      warnings: asArray(raw.warnings)
        .filter((w): w is string => typeof w === "string")
        .map(mapAnswerWarning),
      ...(typeof raw.plan_hash === "string" ? { planHash: raw.plan_hash } : {}),
      ...(evidence !== undefined ? { evidence } : {}),
      ...(metric !== undefined ? { metric } : {}),
      watermarkStale: false,
    },
    drift,
  };
}

/* ------------------------------------------------------------------ */
/* Typed gestures: UI refinement → published operator                  */
/* ------------------------------------------------------------------ */

/**
 * The UI names operators in PascalCase and the wire in snake_case with an
 * `op` discriminator. Chart clicks and drill chips were being posted in the
 * UI's spelling, which a conforming server rejects as a malformed body, so
 * the translation is written down here next to everything else.
 * Unmappable operators return null rather than being sent as something
 * they are not.
 */
export function refinementToWire(refinement: Refinement): UnknownRecord | null {
  switch (refinement.op) {
    case "SetDimensions":
      return { op: "set_dimensions", dimensions: refinement.dimensions };
    case "AddFilter":
      return {
        op: "add_filter",
        dimension: refinement.filter.dimension,
        predicate_op: refinement.filter.op,
        values: refinement.filter.values,
      };
    case "RemoveFilter":
      return { op: "remove_filter", dimension: refinement.dimension };
    case "SetWindow":
      return {
        op: "set_window",
        window: { start: refinement.window.start, end: refinement.window.end },
        basis: refinement.window.basis,
      };
    case "SetComparison":
      return refinement.comparison === null
        ? { op: "set_comparison", kind: null, custom: null }
        : {
            op: "set_comparison",
            kind: refinement.comparison.kind === "same_period_last_year" ? "prior_year" : null,
            custom:
              refinement.comparison.kind === "custom"
                ? {
                    start: refinement.comparison.window.start,
                    end: refinement.comparison.window.end,
                  }
                : null,
          };
    case "SetGrain":
      return {
        op: "set_grain",
        entity: refinement.grain.entity,
        time_bucket: refinement.grain.timeBucket ?? null,
      };
    case "DrillInto":
      // The wire takes one target per operator; a multi-target UI gesture
      // becomes several operators (see `refinementsToWire`).
      return Array.isArray(refinement.target)
        ? null
        : { op: "drill_into", target: refinement.target };
    case "Pivot":
      return { op: "pivot", measures: refinement.measures };
    case "Explain":
      return { op: "explain", target: refinement.target };
    case "RankBy":
      return { op: "rank_by", by: refinement.metric, descending: refinement.descending };
    case "Expand":
      // `limit` is required and positive on the wire; the UI gesture has no
      // number attached, so it asks for the engine's default page.
      return { op: "expand", limit: 50 };
    case "ResetContext":
      return { op: "reset_context", keep_pins: refinement.keepPins };
  }
}

export function refinementsToWire(refinements: readonly Refinement[]): UnknownRecord[] {
  const out: UnknownRecord[] = [];
  for (const refinement of refinements) {
    if (refinement.op === "DrillInto" && Array.isArray(refinement.target)) {
      for (const target of refinement.target) out.push({ op: "drill_into", target });
      continue;
    }
    const mapped = refinementToWire(refinement);
    if (mapped !== null) out.push(mapped);
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* TurnResponse → TurnEvent replay (reconnect / refresh recovery)      */
/* ------------------------------------------------------------------ */

/** What the driver already received on the live stream before it dropped. */
export interface ReceivedTurnState {
  findingReferents: Set<string>;
  chartIds: Set<string>;
  narrativeLength: number;
  hasHeader: boolean;
  hasDefinition: boolean;
  hasClarification: boolean;
  warningKeys: Set<string>;
}

export function newReceivedState(): ReceivedTurnState {
  return {
    findingReferents: new Set(),
    chartIds: new Set(),
    narrativeLength: 0,
    hasHeader: false,
    hasDefinition: false,
    hasClarification: false,
    warningKeys: new Set(),
  };
}

/** Record a successfully parsed event into the received-state tracker. */
export function trackReceived(received: ReceivedTurnState, event: TurnEvent): void {
  switch (event.type) {
    case "context_header":
      received.hasHeader = true;
      break;
    case "finding":
      received.findingReferents.add(event.finding.referent.value);
      break;
    case "chart_spec":
      received.chartIds.add(event.spec.id);
      break;
    case "narrative_delta":
      received.narrativeLength += event.text.length;
      break;
    case "warning":
      received.warningKeys.add(`${event.code}:${event.message}`);
      break;
    case "definition_card":
      received.hasDefinition = true;
      break;
    case "clarification":
      received.hasClarification = true;
      break;
    default:
      break;
  }
}

/**
 * Convert a completed TurnResponse into the event stream the store already
 * understands, skipping everything the live stream delivered before it
 * dropped (findings by referent, charts by id, the narrative prefix).
 * Append-only answer state stays duplicate-free.
 */
export function turnResponseToEvents(
  response: TurnResponseData,
  received: ReceivedTurnState = newReceivedState(),
): TurnEvent[] {
  const events: TurnEvent[] = [];

  if (response.outcome === "error") {
    events.push({
      type: "error",
      code: response.error.code,
      message: response.error.message,
      correlationId: response.error.correlationId,
    });
    return events;
  }

  if (response.outcome === "clarification_required") {
    events.push({ type: "stage", stage: "interpreted", status: "completed" });
    if (!received.hasClarification) {
      events.push({ type: "clarification", clarification: response.clarification });
    }
    events.push({
      type: "turn_complete",
      investigationId: response.investigationId,
      status: "clarification_required",
      ...(response.debug ? { debug: response.debug } : {}),
    });
    return events;
  }

  if (response.header && !received.hasHeader) {
    events.push({
      type: "context_header",
      header: response.header,
      turnClass: response.turnClass,
    });
  }
  // Close out the stage rail honestly: the pipeline finished server-side.
  events.push({ type: "stage", stage: "narrating", status: "completed" });
  for (const finding of response.findings) {
    if (!received.findingReferents.has(finding.referent.value)) {
      events.push({ type: "finding", finding });
    }
  }
  for (const spec of response.charts) {
    if (!received.chartIds.has(spec.id)) events.push({ type: "chart_spec", spec });
  }
  for (const warning of response.warnings) {
    if (!received.warningKeys.has(`${warning.code}:${warning.message}`)) {
      events.push({ type: "warning", ...warning });
    }
  }
  if (response.narrative !== undefined && response.narrative.length > received.narrativeLength) {
    events.push({
      type: "narrative_delta",
      text: response.narrative.slice(received.narrativeLength),
    });
  }
  if (response.definition && !received.hasDefinition) {
    events.push({ type: "definition_card", definition: response.definition });
  }
  // The bundle rides on the completed response rather than on its own SSE
  // frame: it is only whole once the turn is (a probe's row count is not
  // known while it runs), and the server publishes no `evidence` frame.
  if (response.evidence) {
    events.push({ type: "evidence", evidence: response.evidence });
  }
  events.push({
    type: "turn_complete",
    investigationId: response.investigationId,
    status: "complete",
    // The answer's grade is the grade law over the turn's recorded
    // finding grades — computed server-side and read off the bundle,
    // never re-derived here from the findings on screen.
    ...(response.evidence?.answerGrade ? { answerGrade: response.evidence.answerGrade } : {}),
    // Same discipline for the badge: the governed provenance rides on the
    // completed response (there is no `metric` SSE frame — which contract
    // a probe read is only settled once it has run) and is carried
    // through as the server projected it.
    ...(response.metric ? { metric: response.metric } : {}),
    ...(response.debug ? { debug: response.debug } : {}),
  });
  return events;
}

/* ------------------------------------------------------------------ */
/* GET /v1/sessions/{sid}/lineage                                      */
/* ------------------------------------------------------------------ */

/**
 * Canonical (camelCase) paths the lineage graph cannot draw without. The
 * OpenAPI spelling is snake_case and names the node list `investigations`
 * (SessionLineageResponse.investigations: InvestigationResponse[]), so both
 * spellings are accepted and drift is reported against the canonical name.
 *
 * `label` is a UI-only display shape — InvestigationResponse has no such
 * field, so it is DERIVED from the question (falling back to the turn
 * class) rather than demanded from the wire.
 */
export const REQUIRED_LINEAGE_NODE_FIELDS = [
  "turnId",
  "investigationId",
  "turnClass",
] as const;

export const REQUIRED_LINEAGE_EDGE_FIELDS = ["parentTurnId", "childTurnId"] as const;

/** Wire aliases: canonical camelCase path → the spec's snake_case key. */
export const LINEAGE_NODE_ALIASES: Record<string, string> = {
  turnId: "turn_id",
  investigationId: "investigation_id",
  turnClass: "turn_class",
};

export const LINEAGE_EDGE_ALIASES: Record<string, string> = {
  parentTurnId: "parent_id",
  childTurnId: "child_id",
};

/** Read `path`, falling back to its snake_case alias; records misses. */
function pickAliased(
  record: unknown,
  path: string,
  aliases: Record<string, string>,
  missing: string[],
): unknown {
  if (!isRecord(record)) {
    missing.push(path);
    return undefined;
  }
  const alt = aliases[path];
  const value = record[path] ?? (alt !== undefined ? record[alt] : undefined);
  if (value === undefined || value === null) missing.push(path);
  return value;
}

export interface LineageParse {
  value: SessionLineageData | null;
  drift: string[];
}

export function parseSessionLineage(raw: unknown): LineageParse {
  const drift: string[] = [];
  if (!isRecord(raw)) return { value: null, drift: ["nodes", "edges"] };
  const rawNodes = raw.nodes ?? raw.investigations;
  const rawEdges = raw.edges;
  if (!Array.isArray(rawNodes) || !Array.isArray(rawEdges)) {
    return { value: null, drift: ["nodes", "edges"] };
  }

  const nodes: LineageNode[] = [];
  rawNodes.forEach((node, index) => {
    const missing: string[] = [];
    const turnId = pickAliased(node, "turnId", LINEAGE_NODE_ALIASES, missing);
    const investigationId = pickAliased(node, "investigationId", LINEAGE_NODE_ALIASES, missing);
    const turnClass = pickAliased(node, "turnClass", LINEAGE_NODE_ALIASES, missing);
    if (missing.length > 0) {
      drift.push(...missing.map((path) => `nodes[${index}].${path}`));
      return;
    }
    const record = node as UnknownRecord;
    const question = typeof record.question === "string" ? record.question : "";
    nodes.push({
      turnId: turnId as string,
      investigationId: investigationId as string,
      turnClass: turnClass as LineageNode["turnClass"],
      // UI-only: prefer an explicit label, else the question, else the class.
      label:
        typeof record.label === "string" && record.label !== ""
          ? record.label
          : question !== ""
            ? question
            : String(turnClass),
      question,
    });
  });

  const edges: LineageEdge[] = [];
  rawEdges.forEach((edge, index) => {
    const missing: string[] = [];
    const parentTurnId = pickAliased(edge, "parentTurnId", LINEAGE_EDGE_ALIASES, missing);
    const childTurnId = pickAliased(edge, "childTurnId", LINEAGE_EDGE_ALIASES, missing);
    if (missing.length > 0) {
      drift.push(...missing.map((path) => `edges[${index}].${path}`));
      return;
    }
    const record = edge as UnknownRecord;
    edges.push({
      parentTurnId: parentTurnId as string,
      childTurnId: childTurnId as string,
      operators: Array.isArray(record.operators) ? (record.operators as string[]) : [],
    });
  });
  return { value: { nodes, edges }, drift };
}

/* ------------------------------------------------------------------ */
/* GET /v1/sessions                                                    */
/* ------------------------------------------------------------------ */

/**
 * What a session row cannot be drawn without. All four are `required` on
 * the wire (`SessionSummary`), and all four are load-bearing: an id to
 * switch to, a title to read, a timestamp to order by, and a turn count
 * that distinguishes an empty session from a worked one. A row missing any
 * of them is dropped and reported rather than rendered with a blank or an
 * invented label.
 */
export const REQUIRED_SESSION_SUMMARY_FIELDS = [
  "session_id",
  "title",
  "created_at",
  "last_activity",
] as const;

export interface SessionListParse {
  value: SessionListData | null;
  drift: string[];
}

export function parseSessionList(raw: unknown): SessionListParse {
  const drift: string[] = [];
  if (!isRecord(raw) || !Array.isArray(raw.sessions)) {
    return { value: null, drift: ["sessions"] };
  }
  const sessions: SessionSummary[] = [];
  raw.sessions.forEach((entry, index) => {
    const missing: string[] = [];
    missingAt(entry, REQUIRED_SESSION_SUMMARY_FIELDS, missing);
    if (missing.length > 0) {
      drift.push(...missing.map((path) => `sessions[${index}].${path}`));
      return;
    }
    const record = entry as UnknownRecord;
    sessions.push({
      sessionId: asString(record.session_id),
      title: asString(record.title),
      createdAt: asString(record.created_at),
      lastActivity: asString(record.last_activity),
      // Tolerated rather than demanded: a missing count degrades the row's
      // subtitle, it does not make the session unopenable.
      turnCount: asNumber(record.turn_count) ?? 0,
    });
  });
  return {
    value: { sessions, total: asNumber(raw.total) ?? sessions.length },
    drift,
  };
}

/* ------------------------------------------------------------------ */
/* GET /v1/portfolio/latest                                            */
/* ------------------------------------------------------------------ */

/**
 * What a portfolio card cannot be drawn without. The spec models these as
 * `AnomalyCard`, whose spelling is snake_case (`anomaly_id`, `impact_cents`)
 * — both are accepted, drift is reported against the canonical name.
 *
 * `provenance` is required in place of an evidence `grade`, and that is the
 * resolved answer to an old objection rather than a concession. The earlier
 * position was that a dollar figure without the grade that earned it invents
 * provenance, so the UI demanded a `grade` AnomalyCard did not carry and a
 * live portfolio tripped the drift banner on purpose. The server answered
 * honestly instead of inventing a grade: a grade certifies how *this
 * platform* computed a number from certified semantics (§5.3), whereas an
 * anomaly card is a record read out of an external detection system as-of a
 * watermark — stamping DIRECT/DERIVED/PROXY on it would fabricate exactly
 * the provenance the rule exists to protect. So the card now declares the
 * three facts a grade would otherwise have implied: its `provenance`
 * (`external_detection`), the `priority_formula_version` that ranked it, and
 * the `source_watermark_id` it was read at. The UI renders a DETECTION
 * provenance badge, deliberately distinct from the GradeBadge, so an analyst
 * never reads "externally detected" as "certified evidence". Drilling a card
 * starts an ordinary investigation turn, and *that* answer carries a real
 * grade. AnomalyCard has no `grade` property and, by design, never will.
 *
 * `priorityFormulaVersion` / `sourceWatermarkId` are `required` on the wire
 * too but are read tolerantly (defaulted to "") rather than demanded: they
 * annotate the badge, so a server that omits them should degrade the tooltip,
 * not blank the card. The spec-level guarantee is pinned in
 * contract-openapi.test.ts.
 */
export const REQUIRED_PORTFOLIO_ITEM_FIELDS = [
  "referent",
  "title",
  "impactCents",
  "provenance",
] as const;

/** Wire aliases: canonical camelCase path → the spec's AnomalyCard key. */
export const PORTFOLIO_ITEM_ALIASES: Record<string, string> = {
  referent: "anomaly_id",
  impactCents: "impact_cents",
  issueClass: "category",
  detail: "description",
  impactLabel: "impact_label",
  // Same name on both sides, but listed so the backing table in
  // contract-openapi.test.ts can assert every required path uniformly.
  provenance: "provenance",
  priorityFormulaVersion: "priority_formula_version",
  sourceWatermarkId: "source_watermark_id",
};

export interface PortfolioSnapshotData {
  items: PortfolioItem[];
  watermark?: string;
  rankingPolicy?: string;
}

export interface PortfolioParse {
  value: PortfolioSnapshotData | null;
  drift: string[];
}

export function parsePortfolioSnapshot(raw: unknown): PortfolioParse {
  const drift: string[] = [];
  if (!isRecord(raw) || !Array.isArray(raw.items)) return { value: null, drift: ["items"] };
  const items: PortfolioItem[] = [];
  raw.items.forEach((item, index) => {
    const missing: string[] = [];
    for (const path of REQUIRED_PORTFOLIO_ITEM_FIELDS) {
      pickAliased(item, path, PORTFOLIO_ITEM_ALIASES, missing);
    }
    if (missing.length > 0) {
      drift.push(...missing.map((path) => `items[${index}].${path}`));
      return;
    }
    const record = item as UnknownRecord;
    const referent = (record.referent ?? record.anomaly_id) as string;
    const optional = (path: string): unknown =>
      record[path] ?? record[PORTFOLIO_ITEM_ALIASES[path] ?? path];
    const issueClass = optional("issueClass");
    const impactLabel = optional("impactLabel");
    const detail = optional("detail");
    const formulaVersion = optional("priorityFormulaVersion");
    const sourceWatermarkId = optional("sourceWatermarkId");
    items.push({
      rank: typeof record.rank === "number" ? record.rank : index + 1,
      referent,
      title: record.title as string,
      issueClass: typeof issueClass === "string" ? issueClass : "",
      impactCents: (record.impactCents ?? record.impact_cents) as number,
      impactLabel: typeof impactLabel === "string" ? impactLabel : "",
      detail: typeof detail === "string" ? detail : "",
      provenance: record.provenance as PortfolioItem["provenance"],
      priorityFormulaVersion: typeof formulaVersion === "string" ? formulaVersion : "",
      sourceWatermarkId: typeof sourceWatermarkId === "string" ? sourceWatermarkId : "",
      // The card's typed first turn, carried through verbatim: it is
      // already the published shape, so translating it would only be an
      // opportunity to get it wrong.
      ...(isRecord(record.drill_spec) ? { drillSpec: record.drill_spec } : {}),
      drill: isRecord(record.drill)
        ? (record.drill as unknown as PortfolioItem["drill"])
        : { label: "Drill in", refinement: { op: "DrillInto", target: referent } },
    });
  });
  const value: PortfolioSnapshotData = { items };
  // PortfolioResponse spells these `watermark_id` / `formula_version`.
  const watermark =
    alias(raw, "watermark", "watermark_loaded_at") ?? raw.watermark_id;
  if (typeof watermark === "string") value.watermark = watermark;
  const rankingPolicy =
    alias(raw, "rankingPolicy", "ranking_policy") ?? raw.formula_version;
  if (typeof rankingPolicy === "string") value.rankingPolicy = rankingPolicy;
  return { value, drift };
}
