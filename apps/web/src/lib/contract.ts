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
  AnomalyReconciliation,
  Benchmark,
  ChartKeying,
  ChartRow,
  ChartSpec,
  ChartWireRow,
  ClarificationData,
  CohortSummary,
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
  MeasuredValue,
  MetricContractRef,
  MetricDisplay,
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
  TurnUsage,
  WarningEvent,
} from "@/lib/types";
import { GRADE_STRENGTH } from "@/lib/types";
import { formatMeasure, type MeasureUnit } from "@/lib/format";
import type {
  PortfolioItem,
  PortfolioLane,
  PriorityDecomposition,
} from "@/lib/mock/portfolio";

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
  /**
   * `ErrorEnvelope.subcode` — which of two failures wearing one code this
   * was. Only `QUERY_BUDGET_EXCEEDED` publishes one today
   * (`WAREHOUSE_READ_BUDGET` | `MODEL_SPEND_BUDGET`); read tolerantly, so
   * a code that grows one later needs no change here.
   */
  subcode?: string;
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
      ...(typeof record.subcode === "string" && record.subcode !== ""
        ? { subcode: record.subcode }
        : {}),
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

/**
 * Kernel measure unit → the unit the formatters understand.
 *
 * `ratio` and `percent` both render as a percentage but do NOT carry the
 * same numbers: a `ratio` frame publishes 0.079945 where a `percent` frame
 * would publish 7.9945. Collapsing them here (which is what this table used
 * to do) is exactly how a chart came to say "0.1%" beside a finding card
 * saying "12.1%" — see `CHART_VALUE_SCALE_BY_WIRE`.
 */
const CHART_UNIT_BY_WIRE: Record<string, ChartSpec["unit"]> = {
  money_cents: "cents",
  ratio: "percent",
  percent: "percent",
  count: "count",
  days: "days",
};

/**
 * The factor that takes a published value into the unit the UI formats in.
 * Only `ratio` needs one: it is a 0–1 fraction and the UI renders percent.
 * Everything else is already in its display unit and is left alone.
 */
const CHART_VALUE_SCALE_BY_WIRE: Record<string, number> = {
  ratio: 100,
};

/**
 * How to render one governed measure: the display unit AND the factor that
 * gets a published value into it. The two travel together on purpose —
 * knowing a `ratio` metric "is a percent" without also knowing it arrives
 * as 0.121361 is precisely the half-fact that rendered 12.1% as 0.1%.
 */
export interface MeasureDisplay {
  unit: MeasureUnit;
  scale: number;
}

export function measureDisplay(wireUnit: string): MeasureDisplay | undefined {
  const unit = CHART_UNIT_BY_WIRE[wireUnit];
  if (unit === undefined) return undefined;
  return { unit, scale: CHART_VALUE_SCALE_BY_WIRE[wireUnit] ?? 1 };
}

/**
 * Is this x column temporal? The wire does not say, and the chart type it
 * publishes is not a reliable proxy — the engine sends `line` for a
 * payer-by-payer ranking, which is a bar chart drawn as a line. So the
 * shape of the axis decides: labels that are all dates/periods get a line,
 * anything categorical gets bars.
 */
const TEMPORAL_LABEL =
  /^(\d{4}-\d{2}(-\d{2})?|\d{4}-W\d{1,2}|\d{4}Q[1-4]|\d{4})$/i;

const MONTH_LABEL =
  /^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(\s+\d{1,2})?(,?\s*\d{4})?$/i;

function isTemporalAxis(labels: readonly string[]): boolean {
  if (labels.length === 0) return false;
  return labels.every((label) => TEMPORAL_LABEL.test(label) || MONTH_LABEL.test(label));
}

/**
 * Is this x column an ORDINAL BUCKET axis — and if so, where does a label
 * sit on it?
 *
 * The sibling problem to `isTemporalAxis`, and the same root cause: the
 * wire does not say. A bucketed axis arrives as strings, so anything that
 * sorts it sorts it lexicographically — which is how the unbilled-aging
 * chart drew `120+` ($14.1M) in SECOND position, between `0-30` and
 * `31-60`, and why the filing-runway profile was correct only by luck of
 * emission order.
 *
 * Deliberately a SHAPE recognizer, not a list of known buckets: a pack that
 * adds `0-15/16-45/46+` tomorrow gets the same ordering without a code
 * change, and a hard-coded list would silently mis-order the first bucket
 * nobody thought of. What is recognized is the shape of a numeric range or
 * an open-ended edge:
 *
 *   `0-30`, `31–60`, `0 to 30`, `61-90 days`   → the range's LOWER bound
 *   `90+`, `>90`, `over 90`, `120 or more`     → the number itself
 *   `<30`, `under 30`, `up to 30`              → just below the number
 *
 * A label carrying no number at all (`expired`, `unbilled`, `no deadline`)
 * is NOT given a position: inventing one would be this module deciding
 * that "expired" means "less than zero days", which is a semantic the pack
 * owns and the wire has not published. Such labels keep the slot the engine
 * emitted them in — see `orderRowsByOrdinalBucket`.
 */
const BUCKET_RANGE = /^\D*?(-?\d+(?:\.\d+)?)\s*(?:-|–|—|\.\.|to)\s*(-?\d+(?:\.\d+)?)\b/i;
const BUCKET_OPEN_UPPER =
  /^\D*?(-?\d+(?:\.\d+)?)\s*\+|^(?:>=?|over|above|more than|at least|older than)\s*(-?\d+(?:\.\d+)?)\b|^(-?\d+(?:\.\d+)?)\s*(?:or more|and over|and above|plus)\b/i;
const BUCKET_OPEN_LOWER =
  /^(?:<=?|under|below|less than|up to|within|fewer than)\s*(-?\d+(?:\.\d+)?)\b/i;

export function ordinalBucketKey(label: string): number | undefined {
  const text = label.trim();
  if (text === "") return undefined;
  const lower = BUCKET_OPEN_LOWER.exec(text);
  // "under 30" sorts just below the range that starts at 30, so a
  // `<30 / 30-60` pair cannot tie.
  if (lower?.[1] !== undefined) return Number(lower[1]) - 0.5;
  const range = BUCKET_RANGE.exec(text);
  if (range?.[1] !== undefined) return Number(range[1]);
  const upper = BUCKET_OPEN_UPPER.exec(text);
  if (upper !== null) {
    const captured = upper[1] ?? upper[2] ?? upper[3];
    if (captured !== undefined) return Number(captured);
  }
  return undefined;
}

/**
 * True when the axis is bucketed rather than nominal: at least two labels
 * carry a bucket position and no label is temporal (a month axis is
 * already ordered, and `2026-02` would otherwise parse as a range).
 */
export function isOrdinalBucketAxis(labels: readonly string[]): boolean {
  if (labels.length < 2) return false;
  if (labels.some((label) => TEMPORAL_LABEL.test(label) || MONTH_LABEL.test(label))) return false;
  return labels.filter((label) => ordinalBucketKey(label) !== undefined).length >= 2;
}

/**
 * Order bucketed rows by their position on the axis, leaving un-positioned
 * labels exactly where the engine put them.
 *
 * The positioned rows are sorted among themselves and re-seated into the
 * slots they already occupied; a label with no number (`expired`) keeps its
 * own slot. So the chart stops drawing `120+` in second place, and nothing
 * here has to decide what "expired" means.
 */
function orderRowsByOrdinalBucket(rows: readonly ChartRow[], descending = false): ChartRow[] {
  const slots: number[] = [];
  const positioned: { row: ChartRow; key: number }[] = [];
  rows.forEach((row, index) => {
    const key = ordinalBucketKey(row.label);
    if (key === undefined) return;
    slots.push(index);
    positioned.push({ row, key });
  });
  if (positioned.length < 2) return [...rows];
  positioned.sort((a, b) => (descending ? b.key - a.key : a.key - b.key));
  const out = [...rows];
  slots.forEach((slot, i) => {
    const entry = positioned[i];
    if (entry !== undefined) out[slot] = entry.row;
  });
  return out;
}

/**
 * Seat the rows in the order the CATALOG declared for this axis.
 *
 * `ChartSpec.axis_order` is the pack's own list for an ordinal dimension
 * (`["0-30","31-60","61-90","91-120","120+"]`), and the server emits its
 * rows in it — so this is normally a no-op that re-states the fact rather
 * than performing it. It is applied anyway, and applied STABLY, because a
 * client that only labels an order it never checks is trusting a claim it
 * could have verified for free.
 *
 * A label the catalog does not declare keeps its wire slot, after the
 * declared ones — the same rule the server states. A bucket the pack has
 * not heard of is a fact about the data, not a licence to reorder the ones
 * it has, and it is emphatically not something this module gets to place by
 * guessing a number out of it.
 */
function orderRowsByAxisOrder(
  rows: readonly ChartRow[],
  axisOrder: readonly string[],
): ChartRow[] {
  const rank = new Map(axisOrder.map((label, index) => [label, index] as const));
  return rows
    .map((row, index) => ({ row, index, rank: rank.get(row.label) ?? axisOrder.length }))
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.row);
}

/**
 * The resolved ordering the payload carries, in whatever spelling it
 * carries it.
 *
 * The engine's own vocabulary for this is `Ordering{by, descending}` on the
 * probe (`revi_kernel.probes`), and the chart-spec half that publishes it is
 * landing in a later batch — so this reads the shapes that half could
 * plausibly take rather than one it has not committed to yet: a nested
 * object, a bare column name with a sibling direction, a `-column` /
 * `column_desc` string, or an array of orderings (first wins).
 *
 * Returns undefined when the payload says nothing about order — which is
 * the case today, and which leaves the wire's own row order alone. This
 * never invents a ranking; it only honours one that was published.
 */
export interface ChartSortHint {
  by: string;
  descending: boolean;
}

function directionOf(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const text = value.trim().toLowerCase();
    if (text === "desc" || text === "descending" || text === "down") return true;
    if (text === "asc" || text === "ascending" || text === "up") return false;
  }
  return fallback;
}

export function readChartSort(raw: UnknownRecord): ChartSortHint | undefined {
  const candidates = [raw.sort, raw.order, raw.ordering, raw.order_by, raw.sort_by, raw.ranked_by];
  for (const candidate of candidates) {
    const entry = Array.isArray(candidate) ? candidate[0] : candidate;
    if (isRecord(entry)) {
      const by = asString(entry.by ?? entry.column ?? entry.field ?? entry.key ?? entry.measure);
      if (by === "") continue;
      return {
        by,
        descending: directionOf(
          entry.descending ?? entry.desc ?? entry.direction ?? entry.order,
          true,
        ),
      };
    }
    if (typeof entry === "string" && entry.trim() !== "") {
      const text = entry.trim();
      // "-denied_dollars" / "denied_dollars desc" / "denied_dollars_desc".
      const leadingMinus = text.startsWith("-");
      const stripped = leadingMinus ? text.slice(1) : text;
      const suffix = /[\s_](asc|ascending|desc|descending)$/i.exec(stripped);
      const by = suffix ? stripped.slice(0, suffix.index) : stripped;
      if (by === "") continue;
      return {
        by,
        descending: leadingMinus
          ? true
          : directionOf(
              suffix?.[1] ?? raw.descending ?? raw.sort_direction ?? raw.direction,
              true,
            ),
      };
    }
  }
  return undefined;
}

/** "denied_dollars" → "Denied dollars"; "carc" → "CARC". */
const COLUMN_ACRONYMS: Record<string, string> = {
  carc: "CARC",
  rarc: "RARC",
  ar: "AR",
  dnfb: "DNFB",
  cpt: "CPT",
  drg: "DRG",
  msdrg: "MS-DRG",
  npi: "NPI",
  pct: "%",
};

export function humanizeColumn(column: string): string {
  const words = column.split(/[_\s]+/).filter(Boolean);
  if (words.length === 0) return column;
  const spelled = words.map((word) => COLUMN_ACRONYMS[word.toLowerCase()] ?? word);
  const [head, ...tail] = spelled;
  const lead = COLUMN_ACRONYMS[words[0].toLowerCase()] ? head : head[0].toUpperCase() + head.slice(1);
  return [lead, ...tail].join(" ");
}

/**
 * A human title from the frame's own columns: "Cash posted by payer".
 * The server's title names the internal frame ("cash posted — cash by
 * payer  compare"), which is engine bookkeeping — the frame id is already
 * carried verbatim on `frameId` and the original on `wireTitle`, so
 * nothing is lost by putting the analyst's words on the chart itself.
 * The window is appended at render time, where the header is in scope.
 */
function composeChartTitle(
  valueColumn: string,
  xColumn: string,
  /** `valueColumn` is already a governed display name — do not re-spell it. */
  governedName = false,
): string {
  const measure = governedName ? valueColumn : humanizeColumn(valueColumn);
  if (!xColumn || xColumn === valueColumn) return measure;
  return `${measure} by ${humanizeColumn(xColumn).toLowerCase()}`;
}

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

/**
 * `[{name, value}]` → `{name: value}`, dropping nulls only.
 *
 * BOOLEANS ARE CARRIED, and that is the whole of R4-01. This filter used to
 * read `typeof value === "number" || typeof value === "string"`, and its
 * docstring said "dropping nulls and booleans" as though that were a
 * tidiness measure. `denial_rate__is_bound: true` is a boolean. So the one
 * fact that separates "Veritas denies 76.9% of claims" from "Veritas
 * denies at most 76.9%, over thirteen of them" was discarded three lines
 * into the client, and the hero stat rendered a suppression ceiling in the
 * same 1.55rem numeral as the measured card three above it.
 *
 * A null is still dropped: the wire publishes `impact_cents: null` to mean
 * "withheld", and a null in this map would read as a value of nothing.
 */
function mapFindingValues(raw: unknown): Record<string, number | string | boolean> {
  const out: Record<string, number | string | boolean> = {};
  for (const entry of asArray(raw)) {
    if (!isRecord(entry) || typeof entry.name !== "string") continue;
    const value = entry.value;
    if (typeof value === "number" || typeof value === "string" || typeof value === "boolean") {
      out[entry.name] = value;
    }
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

/**
 * What the rest of the turn tells a finding about itself. Neither fact is
 * on `FindingPayload`, and both are needed to render its stat honestly:
 *
 *   `unitByMetric` — the measure's display unit, read off the turn's own
 *     `chart_specs` (`value` column → `unit`). A ranking finding publishes
 *     `values: {denial_rate: 0.121361}` and no `impact_cents`, so without a
 *     unit the headline number cannot be rendered at all — and guessing one
 *     is how "12.1%" becomes "0.1".
 *   `impactWithheldReason` — the turn-level sentence explaining an absent
 *     `impact_cents` (COMPARISON_WINDOW_MISMATCH suppresses it for every
 *     finding at once), so the card can say why rather than show nothing.
 */
export interface FindingContext {
  unitByMetric?: Record<string, MeasureDisplay>;
  impactWithheldReason?: string;
  /**
   * `metric_display` — the pack's governed corrections, by metric id. The
   * engine composes a finding title from the raw id (`metric_label()` is
   * `id.replace("_", " ")`), so "timely filing at risk dollars: $22.4M"
   * reaches this client naming a filing exposure the formula never
   * measures. The narrative is already corrected server-side; titles are
   * corrected here, from the same governed entries, so the two surfaces of
   * one answer cannot call the same number two different things.
   */
  metricDisplay?: Record<string, MetricDisplay>;
  /**
   * `TurnAnswer.benchmarks` — the turn-level list. A finding publishes its
   * own `benchmarks` array today; this is the fallback for a payload
   * generation that publishes them only once, at the answer level, and it
   * is filtered per finding by metric id so a range is never quoted under
   * a number it does not describe.
   */
  benchmarks?: Benchmark[];
}

/**
 * The suffixes the wire hangs off a measure name to describe a suppression
 * ceiling: `denial_rate__is_bound`, `denial_rate__bound`,
 * `denial_rate__bound_population`. They are anatomy of one measure, never a
 * measure of their own.
 */
const BOUND_VALUE_SUFFIX = /__(is_bound|bound|bound_population)$/;

/** Values that are comparison anatomy, not standalone measures. */
const COMPARISON_VALUE_NAMES = new Set([
  "current_cents",
  "prior_cents",
  "delta_cents",
  "pct_change",
  "rank",
]);

/**
 * `FindingPayload.benchmarks` / `TurnAnswer.benchmarks` → the governed
 * external ranges, whole.
 *
 * Nothing is dropped and nothing is rounded: `value_low`/`value_high` stay
 * the decimal strings the source published, and an entry missing its
 * cohort label, period, authority or review status is still mapped — the
 * card renders what is there and says nothing about what is not, which is
 * the only honest reading of a partial quotation.
 */
export function mapBenchmarks(raw: unknown): Benchmark[] {
  const out: Benchmark[] = [];
  for (const entry of asArray(raw)) {
    if (!isRecord(entry)) continue;
    const low = asString(alias(entry, "valueLow", "value_low"));
    const high = asString(alias(entry, "valueHigh", "value_high"));
    // A range with neither end is not a range; there is nothing to show.
    if (low === "" && high === "") continue;
    out.push({
      id: asString(entry.id),
      metricId: asString(alias(entry, "metricId", "metric_id")),
      cohortLabel: asString(alias(entry, "cohortLabel", "cohort_label")),
      valueLow: low,
      valueHigh: high,
      unit: asString(entry.unit),
      period: asString(entry.period),
      authority: asString(entry.authority),
      reviewStatus: asString(alias(entry, "reviewStatus", "review_status")),
      cautions: asStringList(entry.cautions),
      sources: asStringList(entry.sources),
    });
  }
  return out;
}

export function mapFinding(raw: unknown, context: FindingContext = {}): Finding | null {
  if (!isRecord(raw)) return null;
  const values = mapFindingValues(raw.values);
  const impactCents = asNumber(raw.impact_cents);
  const metricRefs = asArray(raw.metric_ids).filter((m): m is string => typeof m === "string");

  // Comparison anatomy: the wire publishes current/prior/delta in cents and
  // the change as a fraction. Every one of these was being dropped, which
  // is why the mini-bars and the % chip never appeared on a live answer.
  const currentCents = asNumber(values.current_cents);
  const priorCents = asNumber(values.prior_cents);
  const deltaCents = asNumber(values.delta_cents);
  const pctChange = asNumber(values.pct_change);
  const hasComparison = currentCents !== undefined && priorCents !== undefined;

  // The finding's own measure as a NUMBER in its display unit, scaled the
  // same way the chart rows are (a `ratio` metric publishes 0.121361 and
  // reads 12.1%). Resolved whether or not a dollar impact was published,
  // because a benchmark range is quoted against this figure and a money
  // finding can carry one too. Nothing is invented — an unknown unit
  // yields nothing rather than a bare float.
  //
  // A measure that is a BOUND says so here, from the sibling values the
  // wire publishes beside it (`<metric>__is_bound`, `__bound`,
  // `__bound_population`). The exact `__bound` wins over the rounded
  // headline `value` when both are present — they are the same ceiling and
  // only one of them is the number the engine actually computed.
  const boundOf = (metricId: string): Pick<MeasuredValue, "isBound" | "boundPopulation"> =>
    values[`${metricId}__is_bound`] === true
      ? {
          isBound: true,
          ...(asNumber(values[`${metricId}__bound_population`]) !== undefined
            ? { boundPopulation: asNumber(values[`${metricId}__bound_population`]) }
            : {}),
        }
      : {};
  const figureOf = (metricId: string): number | undefined =>
    values[`${metricId}__is_bound`] === true
      ? (asNumber(values[`${metricId}__bound`]) ?? asNumber(values[metricId]))
      : asNumber(values[metricId]);

  let measured: MeasuredValue | undefined;
  for (const metricId of metricRefs) {
    const value = figureOf(metricId);
    const display = context.unitByMetric?.[metricId];
    if (value === undefined || display === undefined) continue;
    measured = {
      metricId,
      value: value * display.scale,
      unit: display.unit,
      ...boundOf(metricId),
    };
    break;
  }
  if (measured === undefined) {
    // Fall back to the sole non-anatomy value only when the payload
    // leaves no ambiguity about which number the finding is about. The
    // `__is_bound` / `__bound` / `__bound_population` siblings are not
    // candidates — they are anatomy of the measure, not a second measure,
    // and counting them would make every bounded finding "ambiguous".
    const named = Object.entries(values).filter(
      ([name, value]) =>
        typeof value === "number" &&
        !COMPARISON_VALUE_NAMES.has(name) &&
        !BOUND_VALUE_SUFFIX.test(name),
    );
    if (named.length === 1) {
      const [name] = named[0];
      const display = context.unitByMetric?.[name];
      const value = figureOf(name);
      if (display !== undefined && value !== undefined) {
        measured = {
          metricId: name,
          value: value * display.scale,
          unit: display.unit,
          ...boundOf(name),
        };
      }
    }
  }

  // The headline number when no dollar impact was published — rendered as
  // the ceiling it is when the wire said it is one. "≤ 76.9%" and "76.9%"
  // are different claims and this is the only place the difference can be
  // made, because every surface below reads `impactDisplay`.
  const headline = impactCents === undefined && !hasComparison ? measured : undefined;
  const impactDisplay = headline
    ? `${headline.isBound ? "≤ " : ""}${formatMeasure(headline.value, headline.unit)}`
    : undefined;
  const impactLabel = headline
    ? measureLabel(headline.metricId, context.metricDisplay)
    : undefined;

  const withheld =
    impactCents === undefined && hasComparison ? context.impactWithheldReason : undefined;

  // The governed name for whichever of this finding's measures has one.
  // Only the FIRST match is carried on the finding: the caveat that
  // travels with a display name is about one measure, and hanging two of
  // them off one card would attribute each to the other.
  const correction = context.metricDisplay
    ? metricRefs.map((id) => context.metricDisplay?.[id]).find((entry) => entry !== undefined)
    : undefined;

  return {
    referent: { value: asString(raw.referent), kind: "finding" },
    // The title and statement as the engine composed them, with raw metric
    // ids replaced by the pack's governed display names. Both, not just
    // the title: they are composed by the same server-side formatter from
    // the same `metric_label()` spelling and they sit two lines apart, so
    // correcting one leaves a card whose heading and whose sentence call
    // the same number different things. The narrative above them is
    // already corrected server-side from these very entries. Nothing else
    // in either string is touched — see `applyMetricDisplayNames`.
    title: context.metricDisplay
      ? applyMetricDisplayNames(asString(raw.title), context.metricDisplay)
      : asString(raw.title),
    statement: context.metricDisplay
      ? applyMetricDisplayNames(asString(raw.statement), context.metricDisplay)
      : asString(raw.statement),
    metricRefs,
    values,
    grade: asString(raw.grade, "direct") as EvidenceGrade,
    ...(impactCents !== undefined ? { impactCents } : {}),
    ...(impactDisplay !== undefined ? { impactDisplay } : {}),
    ...(impactLabel !== undefined ? { impactLabel } : {}),
    ...(withheld !== undefined ? { impactWithheldReason: withheld } : {}),
    ...(pctChange !== undefined ? { deltaPct: pctChange } : {}),
    ...(hasComparison
      ? {
          comparison: {
            currentCents,
            priorCents,
            currentLabel: "current",
            priorLabel: "prior",
          },
        }
      : {}),
    // A movement finding carries a signed delta; a concentration finding
    // carries a level. The wire says which by which value it published.
    impactKind: deltaCents !== undefined || hasComparison ? "delta" : "level",
    // NOT published per finding: direction-of-good lives on the metric
    // contract, not on the payload, so tone colouring is withheld rather
    // than guessed from the sign of the number.
    directionOfGood: "neutral",
    confidence: asString(raw.confidence, "high") as Confidence,
    suggestedRefinements: mapSuggestedRefinements(raw.suggested_refinements),
    ...(correction !== undefined ? { metricDisplay: correction } : {}),
    // The governed external ranges, from the finding's own list when it
    // carries one and from the turn-level list otherwise (both are
    // published; a restored investigation carries neither). Filtered to
    // this finding's measures so a two-metric answer never quotes one
    // metric's peer range under the other's number.
    ...(() => {
      const own = mapBenchmarks(raw.benchmarks);
      const pool = own.length > 0 ? own : (context.benchmarks ?? []);
      const mine =
        metricRefs.length > 0
          ? pool.filter((b) => b.metricId === "" || metricRefs.includes(b.metricId))
          : pool;
      return mine.length > 0 ? { benchmarks: mine } : {};
    })(),
    ...(measured !== undefined ? { measured } : {}),
  };
}

/**
 * A measure's caption under the impact stat: the pack's governed display
 * name when there is one, and the humanized id otherwise. Lower-cased only
 * in the fallback — a display name is an authored phrase ("Discharged not
 * final billed (unbilled discharges)") and lower-casing it would be this
 * client editing governed content.
 */
function measureLabel(
  metricId: string,
  display: Record<string, MetricDisplay> | undefined,
): string {
  const entry = display?.[metricId];
  return entry ? entry.displayName : humanizeColumn(metricId).toLowerCase();
}

/**
 * `chart_specs` → measure id → display unit, so a finding can render its
 * own number. This is the only published place a metric's unit appears on
 * a turn payload; reading it here beats hard-coding a unit table the pack
 * would immediately outgrow.
 */
export function unitsFromChartSpecs(raw: unknown): Record<string, MeasureDisplay> {
  const out: Record<string, MeasureDisplay> = {};
  for (const entry of asArray(raw)) {
    if (!isRecord(entry)) continue;
    const measure = asString(entry.value);
    const display = measureDisplay(asString(entry.unit));
    if (measure !== "" && display !== undefined) out[measure] = display;
  }
  return out;
}

/**
 * The turn-level reason a dollar impact is absent from every finding. Only
 * one warning suppresses it today (§7 comparison-window mismatch) and it
 * names both window lengths, so the card can be specific: "not published —
 * 90d vs 91d comparison window" rather than an unexplained blank.
 */
export function impactWithheldReason(warnings: readonly string[]): string | undefined {
  const mismatch = warnings.find((w) => w.startsWith("COMPARISON_WINDOW_MISMATCH"));
  if (mismatch === undefined) return undefined;
  const lengths = [...mismatch.matchAll(/\((?:[^()]*?,\s*)?(\d+)d\)/g)].map((m) => m[1]);
  return lengths.length >= 2
    ? `not published — ${lengths[0]}d vs ${lengths[1]}d comparison window`
    : "not published — the comparison window is not the same length as the analysis window";
}

/**
 * What the turn said about its own ordering and its own cells, handed to
 * the chart mapper so a figure cannot present a rank the answer refused.
 */
export interface ChartTurnContext {
  metricDisplay?: Record<string, MetricDisplay>;
  /** `warnings_v2` codes published on this turn. */
  warningCodes?: readonly string[];
}

/** Units whose cells may be added together without inventing a number. */
function additiveUnit(unit: ChartSpec["unit"]): boolean {
  return unit === "cents" || unit === "count";
}

export function mapChartSpec(
  raw: unknown,
  metricDisplay?: Record<string, MetricDisplay>,
  turn?: ChartTurnContext,
): ChartSpec | null {
  if (!isRecord(raw)) return null;
  const wireType = asString(raw.chart_type);
  const wireUnit = asString(raw.unit);
  const valueColumn = asString(raw.value);
  const xColumn = asString(raw.x);
  const seriesColumn = typeof raw.series === "string" ? raw.series : null;
  const unit = CHART_UNIT_BY_WIRE[wireUnit] ?? "count";
  // The one place a wire value is rescaled. `ratio` frames publish
  // fractions (0.079945) and the UI renders percent, so without this the
  // axis reads "0.1%" beside a finding card reading "8.0%".
  const scale = CHART_VALUE_SCALE_BY_WIRE[wireUnit] ?? 1;
  const codes = new Set(turn?.warningCodes ?? []);
  // The engine refused to publish a ranking on this answer. The chart is
  // 400px below that refusal and used to sort by value anyway.
  const rankingRefused = codes.has("RANKING_REFUSED");

  /*
   * KEYING. A row is identified by the axes the spec DECLARES — `(x,
   * series)` — and the wire is not obliged to send one row per key. Live it
   * does not: a chart declaring `x=month, series=payer` published 30 rows
   * over 3 months × 1 payer, and keying by `x` alone with a
   * `values[series] = …` write meant twenty-seven of them silently
   * overwrote the other three. The figure drew $3,468 of $441,808 and the
   * CSV published the same understatement under a full caveats block.
   *
   * So collisions are COUNTED, not overwritten. Dollars and counts are
   * summed (nothing is lost and the total is stated); percentages and day
   * counts cannot be added, so the chart refuses to draw and hands over
   * what arrived. Either way the wire's own rows are kept for the export.
   */
  const seriesKeys: string[] = [];
  const rowsByX = new Map<string, ChartRow>();
  const cellCount = new Map<string, number>();
  const wireRows: ChartWireRow[] = [];
  /** Categories whose colliding rows named different drill targets. */
  const referentClash = new Set<string>();
  const additive = additiveUnit(unit);
  let wireTotal = 0;
  let lastWriteWinsTotal = 0;

  for (const entry of asArray(raw.rows)) {
    if (!isRecord(entry)) continue;
    const label = asString(entry.x);
    // A null `series` means one series named by the measure column; a
    // non-null one means the rows are already long-format by series.
    const key = typeof entry.series === "string" ? entry.series : valueColumn;
    if (!seriesKeys.includes(key)) seriesKeys.push(key);
    const row = rowsByX.get(label) ?? { label, values: {} };
    const value = asNumber(entry.value);
    const cellKey = `${label}\u0000${key}`;
    const collision = (cellCount.get(cellKey) ?? 0) > 0;
    cellCount.set(cellKey, (cellCount.get(cellKey) ?? 0) + 1);

    // The suppression ceiling, read in whichever spelling arrives.
    // `bound_population` / `is_bound` is the spelling wave B landed; the
    // others are earlier drafts of the same fact, kept because a payload
    // generation that publishes neither must not have one invented for it.
    const boundValue = asNumber(entry.bound ?? entry.upper_bound);
    const bounded =
      entry.bounded === true ||
      entry.is_bound === true ||
      entry.suppressed === true ||
      entry.bound === true ||
      boundValue !== undefined;
    const denominator = asNumber(
      entry.denominator ?? entry.bound_population ?? entry.population ?? entry.n,
    );
    const referent = typeof entry.referent_id === "string" ? entry.referent_id : undefined;

    wireRows.push({
      x: label,
      series: key,
      ...(value !== undefined ? { value: value * scale } : {}),
      ...(referent !== undefined ? { referent } : {}),
      ...(bounded ? { bounded: true } : {}),
      ...(boundValue !== undefined ? { bound: boundValue * scale } : {}),
      ...(denominator !== undefined ? { denominator } : {}),
      ...(entry.provisional === true ? { provisional: true } : {}),
    });
    if (value !== undefined) wireTotal += value * scale;

    if (value !== undefined) {
      const prior = row.values[key];
      if (!collision || prior === undefined) {
        if (prior === undefined) lastWriteWinsTotal += value * scale;
        row.values[key] = value * scale;
      } else if (additive) {
        row.values[key] = prior + value * scale;
      }
      // Non-additive collision: the first cell stands and the census below
      // says the figure cannot be drawn. Overwriting would pick a winner.
    }

    // A collided cell has no single drill target. Keeping the last
    // collider's referent is how a bar's drill-through came to open a
    // different cell from the one it draws — and once a cell's referents
    // have disagreed, no later row may restore one.
    if (!referentClash.has(label) && referent !== undefined) {
      if (row.referent !== undefined && row.referent !== referent) {
        referentClash.add(label);
        delete row.referent;
      } else {
        row.referent = referent;
      }
    }
    // A cell holding any ceiling is a ceiling: a sum that includes a
    // suppressed numerator is itself an upper bound, never a measurement.
    if (bounded) row.bounded = true;
    if (boundValue !== undefined && !collision) row.bound = boundValue * scale;
    if (collision) delete row.bound;
    if (denominator !== undefined && !collision) row.denominator = denominator;
    if (collision) delete row.denominator;
    // A terminal bucket that is calendar-partial or still adjudicating is
    // not a settled measurement, and a spreadsheet that presents it as one
    // reports the claims run-out as deterioration.
    if (entry.provisional === true) row.provisional = true;
    rowsByX.set(label, row);
  }
  if (seriesKeys.length === 0) seriesKeys.push(valueColumn);
  const rows = [...rowsByX.values()];

  const distinctKeys = cellCount.size;
  const collided = wireRows.length > distinctKeys;
  const keying: ChartKeying | undefined = collided
    ? {
        xColumn,
        seriesColumn,
        wireRows: wireRows.length,
        keys: distinctKeys,
        mode: additive ? "summed" : "unkeyable",
        wireTotal,
        drawnTotal: additive ? wireTotal : lastWriteWinsTotal,
        note: additive
          ? `The server sent ${wireRows.length} rows over ${distinctKeys} distinct (${xColumn || "x"}${seriesColumn ? `, ${seriesColumn}` : ""}) key${distinctKeys === 1 ? "" : "s"} — more rows than these axes can tell apart. The colliding rows are ADDED here, so the figure carries all of them; the CSV carries every row as it arrived.`
          : `The server sent ${wireRows.length} rows over ${distinctKeys} distinct (${xColumn || "x"}${seriesColumn ? `, ${seriesColumn}` : ""}) key${distinctKeys === 1 ? "" : "s"} — more rows than these axes can tell apart — and this measure cannot be added up, so there is no figure to draw. The rows are in the CSV exactly as they arrived.`,
        rows: wireRows,
      }
    : undefined;

  // The published `chart_type` is a hint, not a fact about the axis, and
  // it is unreliable in BOTH directions — so the axis decides, both ways.
  //
  // Downwards: the engine sends `line` for a payer-by-payer ranking, and a
  // line between twelve unordered payers asserts a trend that does not
  // exist. Categorical x draws bars.
  //
  // Upwards: the `by month` grain publishes its six-month series as
  // `stacked_bar` (live: `chart_main`, x `month`, rows 2026-02-01 …
  // 2026-07-01), which drew a genuine trend as six disconnected bars
  // beside a finding whose own title reads "$885,721.50 → $1,193,126.92".
  // The rule only ever fired on a declared `line`, so an ordered time axis
  // could never earn one. It can now — but only for a SINGLE series: a
  // real multi-series stacked bar over time is a composition, and
  // unstacking it into lines would change what it claims. A comparison
  // frame folded in later by `selectRenderableCharts` adds its baseline to
  // an already-decided line, which is current-vs-prior over time and
  // exactly right.
  const declared = CHART_KIND_BY_WIRE[wireType] ?? "bar";
  const labels = rows.map((row) => row.label);
  const temporal = isTemporalAxis(labels);
  const kind: ChartSpec["kind"] =
    declared === "line"
      ? temporal
        ? "line"
        : "bar"
      : temporal && seriesKeys.length === 1
        ? "line"
        : declared;

  /*
   * ORDER. Six rules, in this order, and a stated basis for each — the
   * chart sits directly under findings that read "best to worst", and an
   * axis that disagrees with the sentence above it is the answer arguing
   * with itself.
   *
   *   0. A DECLARED axis order wins outright. `axis_order` is the catalog's
   *      own list for an ordinal dimension, published by the server (which
   *      also emits the rows in it and clears `sort` when it does). It
   *      outranks the value sort AND the shape recognizer in rule 3,
   *      because it is the only ordering on this payload that anybody
   *      DECLARED: the recognizer reads numbers out of labels and honestly
   *      refuses to place `expired` or `filed`, while the pack knows
   *      `expired` sits before `0-30` and `filed` after `90+`. Rule 3
   *      stays as the fallback for payloads that carry no declared order.
   *   1. A temporal axis is already ordered; nothing here touches it.
   *      Sorting a month axis by value would delete the trend it draws.
   *   2. A REFUSED ranking is not re-created here. When the turn published
   *      `RANKING_REFUSED` — "ordering ceilings against measurements sorts
   *      by population size, not by the measure that was asked about" —
   *      the figure keeps emission order and says the ranking was refused.
   *      This is the defect a buyer screenshotted: a value sort running
   *      400px below the banner that declined to run it.
   *   3. A BUCKETED axis is ordered by its own scale, ahead of any value
   *      sort. `0-30 · 31-60 · 61-90 · 90+ · expired` is the axis's scale,
   *      not a ranking basis, and a value sort over it produced `expired,
   *      90+, 0-30, 61-90, 31-60` captioned "ordered by Atlas HMO
   *      Complete, high to low" — one arbitrary plan's dollars presented
   *      as the axis.
   *   4. A resolved ordering published on the payload wins. `by` must name
   *      a column this chart actually draws; an ordering naming something
   *      else is UNUSABLE and the wire's own order stands. It used to fall
   *      back to `seriesKeys[0]`, which is how a by-plan ranking came to be
   *      captioned "ordered by Central Physicians Plaza".
   *   5. Failing that, a bucketed axis is still ordered by its buckets.
   *
   * With none of those, the engine's emission order stands. Nominal
   * categories are NOT sorted by value on a hunch: the wire has not said
   * the question was a ranking, and a chart that re-ranks itself would
   * contradict a findings list ordered by something else.
   *
   * And whatever the basis, BOUNDED cells are held out of it. A ceiling has
   * no position in an order it was never measured for; it is seated after
   * the measured rows, in wire order, and the count is published so the
   * caption can say so.
   */
  const sort = readChartSort(raw);
  const axisOrder = asArray(raw.axis_order).filter(
    (value): value is string => typeof value === "string",
  );
  const ordinal = isOrdinalBucketAxis(labels);
  const boundedRows = rows.filter((row) => row.bounded === true).length;
  /** Sort the MEASURED rows only; ceilings keep wire order, at the end. */
  const holdingBounds = (
    compare: (a: ChartRow, b: ChartRow) => number,
  ): ChartRow[] => {
    if (boundedRows === 0) return [...rows].sort(compare);
    const measured = rows.filter((row) => row.bounded !== true);
    const ceilings = rows.filter((row) => row.bounded === true);
    return [...[...measured].sort(compare), ...ceilings];
  };

  let ordered = rows;
  let order: ChartSpec["order"] = { basis: "wire" };
  if (axisOrder.length > 0) {
    // Rule 0. Not conditioned on `temporal` or on `rankingRefused`: a
    // declared scale is not a ranking, so a turn that refused to rank is
    // still entitled to its aging axis — it keeps the `refused` flag so
    // the figure's banner still says no ranking was published.
    ordered = orderRowsByAxisOrder(rows, axisOrder);
    order = {
      basis: "axis-order",
      ...(xColumn !== "" ? { by: xColumn } : {}),
      ...(rankingRefused ? { refused: true } : {}),
    };
  } else if (!temporal && rankingRefused) {
    order = { basis: "wire", refused: true };
  } else if (!temporal) {
    const sortsOnValue =
      sort !== undefined &&
      (sort.by === valueColumn || sort.by === "value" || seriesKeys.includes(sort.by));
    const sortsOnLabel =
      sort !== undefined && (sort.by === xColumn || sort.by === "x" || sort.by === "label");
    // A sort naming the measure on a chart whose series are entities names
    // no drawn column at all. Guessing one is how the axis came to be
    // ordered by whichever plan happened to be first.
    const valueKey =
      sort !== undefined && seriesKeys.includes(sort.by)
        ? sort.by
        : seriesKeys.length === 1
          ? seriesKeys[0]
          : undefined;
    if (ordinal && !sortsOnLabel) {
      // Rule 3: the axis's own scale outranks any value sort over it.
      ordered = orderRowsByOrdinalBucket(rows);
      order = { basis: "ordinal-bucket" };
    } else if (sortsOnLabel && ordinal) {
      // "Order by the bucket column, ascending" means up the SCALE, not up
      // the alphabet — which is the whole of this bug on the runway chart.
      ordered = orderRowsByOrdinalBucket(rows, sort.descending);
      order = { basis: "ordinal-bucket", by: sort.by, descending: sort.descending };
    } else if (sortsOnValue && valueKey !== undefined) {
      const at = (row: ChartRow): number => row.values[valueKey] ?? Number.NEGATIVE_INFINITY;
      ordered = holdingBounds((a, b) => (sort.descending ? at(b) - at(a) : at(a) - at(b)));
      order = {
        basis: "value",
        by: valueKey,
        descending: sort.descending,
        ...(boundedRows > 0 ? { boundedExcluded: boundedRows } : {}),
      };
    } else if (sortsOnLabel) {
      ordered = holdingBounds((a, b) =>
        sort.descending ? b.label.localeCompare(a.label) : a.label.localeCompare(b.label),
      );
      order = {
        basis: "label",
        by: sort.by,
        descending: sort.descending,
        ...(boundedRows > 0 ? { boundedExcluded: boundedRows } : {}),
      };
    }
  }

  // `annotations[0]` is a SENTENCE about the figure ("upper bounds: 4 of 12
  // marks are ceilings, not measurements — their numerator was suppressed
  // and they cannot be ranked against the measured marks"). It was being
  // handed to a `ReferenceLine` as an x value, where it matched no category
  // and drew nothing, so a census the engine wrote reached no reader. One
  // that DOES name a drawn category is a highlight and still draws as one.
  const annotation = asArray(raw.annotations)[0];
  const annotationText = typeof annotation === "string" ? annotation : undefined;
  const annotationIsCategory =
    annotationText !== undefined && labels.includes(annotationText);

  return {
    id: asString(raw.id),
    kind,
    // A declared composition draws as one. `stacked_bar` was mapped to
    // `grouped_bar` and the renderer never set a stackId, so six plan
    // shares of one runway bucket drew as six competing bars — a
    // comparison the frame never claimed.
    ...(wireType === "stacked_bar" && kind !== "line" ? { stacked: true } : {}),
    order,
    wireChartType: wireType,
    frameId: asString(raw.frame_id) || undefined,
    // The chart names the same measure the finding above it does, so it
    // takes the same governed correction: a card reading "Discharged not
    // final billed" over an axis reading "Dnfb dollars" is one answer
    // calling one number two things.
    title: composeChartTitle(
      metricDisplay?.[valueColumn]?.displayName ?? valueColumn,
      xColumn,
      metricDisplay?.[valueColumn] !== undefined,
    ),
    wireTitle: asString(raw.title) || undefined,
    unit,
    xLabel: xColumn || undefined,
    series: seriesKeys.map((key, index) => ({
      key,
      label: seriesColumn === null ? valueColumn : key,
      role: index === 0 ? "current" : "baseline",
    })),
    rows: ordered,
    ...(boundedRows > 0 ? { boundedRows } : {}),
    ...(keying !== undefined ? { keying } : {}),
    ...(annotationText !== undefined && annotationIsCategory
      ? { highlightLabel: annotationText }
      : {}),
    ...(annotationText !== undefined && !annotationIsCategory ? { note: annotationText } : {}),
  };
}

/* ------------------------------------------------------------------ */
/* Which of a turn's charts are worth drawing (F20)                    */
/* ------------------------------------------------------------------ */

const COMPARE_SUFFIXES = ["__compare", "__prior"] as const;

function splitFrameSuffix(frameId: string): { base: string; suffix: string } | null {
  for (const suffix of COMPARE_SUFFIXES) {
    if (frameId.endsWith(suffix) && frameId.length > suffix.length) {
      return { base: frameId.slice(0, -suffix.length), suffix };
    }
  }
  return null;
}

function sameRows(a: ChartSpec, b: ChartSpec): boolean {
  if (a.rows.length !== b.rows.length) return false;
  return a.rows.every((row, index) => {
    const other = b.rows[index];
    if (other === undefined || other.label !== row.label) return false;
    const keys = new Set([...Object.keys(row.values), ...Object.keys(other.values)]);
    for (const key of keys) if (row.values[key] !== other.values[key]) return false;
    return true;
  });
}

/**
 * The charts a turn should actually draw.
 *
 * A comparison turn publishes the same measure twice — `main` and
 * `main__compare` — and (verified against a live `GET
 * /v1/investigations/{iid}`) their rows are byte-identical, because the
 * compare frame carries the CURRENT window's values with a comparison
 * annotation rather than the prior window's. Drawing both is two identical
 * charts stacked, which reads as a rendering bug and buries the real
 * answer. So:
 *
 *   - a `__compare`/`__prior` frame whose rows match its base frame is a
 *     duplicate and is dropped;
 *   - one whose rows DIFFER is the prior period, and is folded into the
 *     base chart as a second (baseline) series — current vs prior in one
 *     chart, which is what the analyst asked for;
 *   - a compare frame with no base frame stands on its own;
 *   - anything with fewer than two rows is not a chart. A single point
 *     plotted on an axis is a number that has been made to look like a
 *     trend; the finding card already says it, better.
 */
export function selectRenderableCharts(specs: readonly ChartSpec[]): ChartSpec[] {
  const byFrame = new Map<string, ChartSpec>();
  for (const spec of specs) {
    if (spec.frameId !== undefined) byFrame.set(spec.frameId, spec);
  }

  const merged = new Map<string, ChartSpec>();
  const dropped = new Set<string>();

  for (const spec of specs) {
    const split = spec.frameId !== undefined ? splitFrameSuffix(spec.frameId) : null;
    if (split === null) continue;
    const base = byFrame.get(split.base);
    if (base === undefined) continue;
    dropped.add(spec.id);
    if (sameRows(base, spec)) continue; // identical twin — the base one stands
    // A genuinely different comparison frame: same measure, other window.
    const baselineKey = `${split.suffix.replace("__", "")}`;
    const rows = base.rows.map((row) => {
      const other = spec.rows.find((r) => r.label === row.label);
      const carried = other ? Object.values(other.values)[0] : undefined;
      return carried === undefined
        ? row
        : { ...row, values: { ...row.values, [baselineKey]: carried } };
    });
    merged.set(base.id, {
      ...base,
      rows,
      series: [
        ...base.series,
        {
          key: baselineKey,
          label: baselineKey === "prior" ? "prior" : "comparison",
          role: "baseline",
          // Never folded into a rollup: it is the other half of the
          // comparison this chart's title promises.
          pinned: true,
        },
      ],
    });
  }

  const out: ChartSpec[] = [];
  for (const spec of specs) {
    if (dropped.has(spec.id)) continue;
    const resolved = merged.get(spec.id) ?? spec;
    if (resolved.rows.length < 2) continue;
    out.push(resolved);
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* How many series one chart can actually say (R3-13)                  */
/* ------------------------------------------------------------------ */

/**
 * The categorical palette has eight fixed slots, so eight is the number of
 * series a chart may DRAW. Seven entities plus the rollup when there are
 * more — a ninth hue is never generated (see `globals.css`).
 */
export const MAX_DRAWN_SERIES = 8;

/** Key and label of the folded-together tail. */
export const OTHERS_SERIES_KEY = "__others";

export interface CappedChart {
  /** The spec to draw. Identical to the input when nothing was capped. */
  spec: ChartSpec;
  /** How many series are not drawn under their own name. */
  hiddenSeries: number;
  /** True when the hidden series were SUMMED into the rollup mark. */
  rolledUp: boolean;
  /** The sentence the figure has to print when either of the above is true. */
  note?: string;
}

/**
 * Cap a chart's series, and say so.
 *
 * Twenty CARC codes and thirty plan names were being drawn in two colours,
 * which is not a legend problem — it is a chart that cannot be read at all.
 * The fix is not more hues (the palette's slot order IS its CVD safety):
 * it is to draw the largest few and fold the rest into one honest mark.
 *
 * The tail is SUMMED only when the measure is additive. Dollars and counts
 * add up; a percentage or a day-count does not, and a bar labelled "+13
 * others" holding the sum of thirteen rates would be a number nobody
 * computed. On those units the tail is dropped and named instead.
 *
 * Survivors keep their WIRE ORDER, not their rank, so the colour a series
 * wears does not change when the chart gains or loses a row.
 *
 * The returned spec is for DRAWING only. Every caller that exports keeps
 * the original — the CSV carries all thirty series, because an export that
 * silently matched the picture would drop rows the analyst came for.
 */
export function capChartSeries(spec: ChartSpec, max: number = MAX_DRAWN_SERIES): CappedChart {
  if (spec.series.length <= max) return { spec, hiddenSeries: 0, rolledUp: false };

  const total = (key: string): number =>
    spec.rows.reduce((sum, row) => sum + Math.abs(row.values[key] ?? 0), 0);
  // Pinned series are kept whatever their size — a comparison chart that
  // dropped its prior period would still be titled as a comparison.
  const pinned = spec.series.filter((s) => s.pinned === true);
  const ranked = [...spec.series]
    .filter((s) => s.pinned !== true)
    .sort((a, b) => total(b.key) - total(a.key));
  const keptKeys = new Set([
    ...pinned.map((s) => s.key),
    ...ranked.slice(0, Math.max(0, max - 1 - pinned.length)).map((s) => s.key),
  ]);
  const kept = spec.series.filter((s) => keptKeys.has(s.key));
  const hiddenKeys = spec.series.filter((s) => !keptKeys.has(s.key)).map((s) => s.key);
  const hiddenSeries = hiddenKeys.length;
  const additive = spec.unit === "cents" || spec.unit === "count";

  if (!additive) {
    return {
      spec: { ...spec, series: kept, rows: spec.rows.map((row) => ({ ...row })) },
      hiddenSeries,
      rolledUp: false,
      note: `showing the ${kept.length} largest of ${spec.series.length} series — ${hiddenSeries} more are in the CSV (they are not summed here: ${spec.unit === "percent" ? "percentages" : "day counts"} do not add up)`,
    };
  }

  const rows: ChartRow[] = spec.rows.map((row) => {
    let sum: number | undefined;
    for (const key of hiddenKeys) {
      const value = row.values[key];
      if (value === undefined) continue;
      sum = (sum ?? 0) + value;
    }
    const values: Record<string, number> = {};
    for (const series of kept) {
      const value = row.values[series.key];
      if (value !== undefined) values[series.key] = value;
    }
    // A row with nothing in the tail gets NO rollup value — never a zero
    // the engine did not publish.
    if (sum !== undefined) values[OTHERS_SERIES_KEY] = sum;
    return { ...row, values };
  });

  return {
    spec: {
      ...spec,
      rows,
      series: [
        ...kept,
        { key: OTHERS_SERIES_KEY, label: `+${hiddenSeries} others`, role: "baseline" },
      ],
    },
    hiddenSeries,
    rolledUp: true,
    note: `showing the ${kept.length} largest of ${spec.series.length} series; the other ${hiddenSeries} are combined into “+${hiddenSeries} others” — each one is a separate column in the CSV`,
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
    const values = asArray(chip.values).map((v) => String(v));
    // `requested_values` is what the user typed before the engine matched
    // it to a value this warehouse holds. Two payload generations are in
    // flight: one that publishes it and one that does not, so the field is
    // read defensively and kept only when it actually says something the
    // corrected values do not. Identical lists carry no information — a
    // "you typed" line under a value nobody corrected is noise.
    const requested = asArray(chip.requested_values).map((v) => String(v));
    const corrected =
      requested.length > 0 &&
      (requested.length !== values.length || requested.some((v, i) => v !== values[i]));
    filters.push({
      dimension: asString(chip.dimension),
      // The wire publishes ids, not display labels; the id IS the label
      // until a labelling endpoint exists, and inventing one would be a
      // second vocabulary nobody governs.
      dimensionLabel: asString(chip.dimension),
      op: asString(chip.op, "eq") as FilterClause["op"],
      values,
      ...(corrected ? { requestedValues: requested } : {}),
      originTurn: asString(chip.origin_turn),
      ...(chip.pinned === true ? { pinned: true } : {}),
    });
  }

  // `as_of` is published (and non-null) exactly when the turn's measure is
  // a SNAPSHOT contract — an as-of balance at the watermark, to which no
  // start..end range is applied. The payload still carries a window on
  // those turns and it is NOT what was measured: an A/R-aging answer
  // computed as of 2026-08-02 shipped under "2026-07-01..2026-07-31
  // (service)", and header, caution and prose all named a scope the
  // calculation never used. Carried through so the chip can state the
  // as-of date instead of a range the number does not honour. A payload
  // generation that publishes no such field leaves it undefined and the
  // header renders exactly as before.
  const asOf = optionalString(raw.as_of);

  return {
    window: { start: asString(raw.window_start), end: asString(raw.window_end), basis },
    ...(asOf !== undefined ? { asOf } : {}),
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

  // `TermPayload.kind` is the pack's own taxonomy — "concept", "metric",
  // "code:carc", "code:group_code", "code:rarc". A live "What is PR3?"
  // answers with TWO code terms, and flattening them into one prose blob
  // (which is what this mapper used to do) loses the thing that makes the
  // answer correct: PR is a group code and 3 is a CARC, and "PR3" is the
  // pair. The card has slots for exactly that — they were never filled.
  const groupTerm = terms.find((t) => asString(t.kind) === "code:group_code");
  const carcTerm = terms.find((t) => asString(t.kind) === "code:carc");
  const isCode = (t: UnknownRecord): boolean => asString(t.kind).startsWith("code:");
  const proseTerms = terms.filter((t) => !isCode(t));

  return {
    term: asString(raw.question) || asString(primary.term),
    normalizedTo: terms
      .map((t) => (isCode(t) ? `${asString(t.term)} — ${asString(t.title)}` : asString(t.title)))
      .filter((label) => label !== "" && label !== " — ")
      .join(" · "),
    // Only the non-code terms: the code terms render in their own slots
    // below, and printing them twice reads as a stutter.
    definition: proseTerms.map((t) => asString(t.definition)).join(" "),
    ...(groupTerm
      ? {
          groupCode: {
            code: asString(groupTerm.term),
            meaning: asString(groupTerm.definition) || asString(groupTerm.title),
          },
        }
      : {}),
    ...(carcTerm
      ? {
          carc: {
            code: Number(asString(carcTerm.term)),
            paraphrase: asString(carcTerm.definition),
            category: asString(carcTerm.title),
          },
        }
      : {}),
    sources: terms
      .filter((t) => typeof t.source === "string" && t.source !== "")
      .map((t) => ({ label: String(t.source), authority: "governed_pack" as const })),
    packVersion: {
      packId: asString(raw.pack_id, pin.pack.packId),
      version: asString(raw.pack_version, pin.pack.version),
    },
    // Related concepts are the OTHER prose terms — a CARC number ("3") in
    // this list is not a related concept, it is half of the answer.
    relatedConcepts: proseTerms.slice(1).map((t) => asString(t.term)),
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

/**
 * `warnings_v2` — the same warnings, classified server-side into
 * `{code, severity, message, count}` so a client can group, count, filter
 * and title them without matching substrings.
 *
 * Entries missing a `code` or a `message` are skipped rather than
 * defaulted: a warning with no sentence has nothing to say, and one with
 * no code is exactly what `UNCLASSIFIED` exists to be. `severity` is the
 * server's two-value ladder and is never re-derived here — anything
 * outside it reads as `caution`, because the failure mode of guessing
 * "info" over a caution is the one that costs a reader money.
 */
export function mapStructuredWarning(raw: unknown): Omit<WarningEvent, "type"> | null {
  if (!isRecord(raw)) return null;
  const code = asString(raw.code);
  const message = asString(raw.message);
  if (code === "" || message === "") return null;
  const count = asNumber(raw.count);
  return {
    code,
    message,
    severity: raw.severity === "info" ? "info" : "caution",
    ...(count !== undefined && count > 1 ? { count } : {}),
    structured: true,
  };
}

/**
 * The turn's warnings, preferring the structured list.
 *
 * `warnings_v2` wins whenever the server published one; the prose strings
 * stay the fallback for turns stored before it existed, which is the whole
 * reason both still travel. Note the asymmetry in the guard: an EMPTY
 * `warnings_v2` beside non-empty `warnings` means this response predates
 * the classifier (the server never drops a warning during classification),
 * so the strings are read rather than the emptiness being believed.
 */
export function readTurnWarnings(
  structured: unknown,
  sentences: readonly string[],
): Omit<WarningEvent, "type">[] {
  if (Array.isArray(structured) && structured.length > 0) {
    const mapped: Omit<WarningEvent, "type">[] = [];
    for (const entry of structured) {
      const warning = mapStructuredWarning(entry);
      if (warning !== null) mapped.push(warning);
    }
    if (mapped.length > 0) return mapped;
  }
  return sentences.map(mapAnswerWarning);
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
/* CohortPayload → CohortSummary (the context header's cohort chip)     */
/* ------------------------------------------------------------------ */

/**
 * `TurnAnswer.cohort` — the pinned population said in words.
 *
 * The context header publishes only the cohort's id and size, which is
 * what made the chip read `coh_9f2a11… (312)`: an analyst who had just
 * drilled "the top three payers" was handed their own selection back in a
 * vocabulary nobody speaks. Everything the chip needs was already on the
 * pinned cohort and none of it was on the wire; this block is that
 * projection, and `detailed: true` marks the chip as safe to render as a
 * definition rather than as a handle.
 *
 * Read tolerantly on purpose: the origin lookups are best-effort
 * server-side (a registry entry can age out), so an incomplete provenance
 * costs the reader the provenance line and nothing else. A payload with
 * no `id` is not a cohort and is dropped.
 */
export function mapCohort(raw: unknown): CohortSummary | undefined {
  if (!isRecord(raw)) return undefined;
  const id = asString(raw.id);
  if (id === "") return undefined;
  const definition = asString(raw.definition);
  return {
    id,
    // The intensional rule when there is one; the handle otherwise, so
    // the chip never renders an empty label that reads as no population.
    definition: definition !== "" ? definition : id,
    pinned: raw.pinned === true,
    // `origin_turn_id` is the turn the population was selected in — the
    // same fact the legacy shape called `originTurn`.
    originTurn: asString(raw.origin_turn_id),
    size: asNumber(raw.size) ?? 0,
    ...(optionalString(raw.entity_grain) ? { entityGrain: String(raw.entity_grain) } : {}),
    ...(optionalString(raw.origin_referent)
      ? { originReferent: String(raw.origin_referent) }
      : {}),
    ...(optionalString(raw.origin_investigation_id)
      ? { originInvestigationId: String(raw.origin_investigation_id) }
      : {}),
    ...(optionalString(raw.window_start) ? { windowStart: String(raw.window_start) } : {}),
    ...(optionalString(raw.window_end) ? { windowEnd: String(raw.window_end) } : {}),
    ...(optionalString(raw.pinned_watermark_id)
      ? { pinnedWatermarkId: String(raw.pinned_watermark_id) }
      : {}),
    detailed: true,
  };
}

/* ------------------------------------------------------------------ */
/* AnomalyReconciliationPayload → AnomalyReconciliation                */
/* ------------------------------------------------------------------ */

const ANOMALY_AGREEMENTS: ReadonlySet<string> = new Set([
  "agreed",
  "diverged",
  "unavailable",
]);

/**
 * `TurnAnswer.anomaly_reconciliation` — the drilled card's figure beside
 * this platform's re-derivation of it. Published only when the turn
 * carried an `anomaly_ref`, which is why the UI has to send one.
 *
 * A payload whose `status` is not one of the three published values is
 * dropped rather than defaulted: "unavailable" and "agreed" are opposite
 * claims about the same pair of numbers, and guessing between them is
 * exactly the silence this strip exists to end.
 */
export function mapAnomalyReconciliation(raw: unknown): AnomalyReconciliation | undefined {
  if (!isRecord(raw)) return undefined;
  const status = asString(raw.status);
  if (!ANOMALY_AGREEMENTS.has(status)) return undefined;
  const cardImpact = asNumber(raw.card_impact_cents);
  if (cardImpact === undefined) return undefined;
  const answerImpact = asNumber(raw.answer_impact_cents);
  const deltaCents = asNumber(raw.delta_cents);
  const deltaFraction = asNumber(raw.delta_fraction);
  return {
    anomalyId: asString(raw.anomaly_id),
    status: status as AnomalyReconciliation["status"],
    cardImpactCents: cardImpact,
    ...(answerImpact !== undefined ? { answerImpactCents: answerImpact } : {}),
    ...(deltaCents !== undefined ? { deltaCents } : {}),
    ...(deltaFraction !== undefined ? { deltaFraction } : {}),
    ...(optionalString(raw.card_metric_id) ? { cardMetricId: String(raw.card_metric_id) } : {}),
    ...(optionalString(raw.answer_metric_id)
      ? { answerMetricId: String(raw.answer_metric_id) }
      : {}),
    ...(optionalString(raw.card_window_start)
      ? { cardWindowStart: String(raw.card_window_start) }
      : {}),
    ...(optionalString(raw.card_window_end)
      ? { cardWindowEnd: String(raw.card_window_end) }
      : {}),
    ...(optionalString(raw.detail) ? { detail: String(raw.detail) } : {}),
    ...(optionalString(raw.summary) ? { summary: String(raw.summary) } : {}),
  };
}

/* ------------------------------------------------------------------ */
/* MetricDisplayPayload → governed display names                       */
/* ------------------------------------------------------------------ */

/**
 * `TurnAnswer.metric_display` / `CapabilitiesResponse.metric_display` —
 * the pack's corrections for metric ids that overclaim.
 *
 * Note what this payload does NOT carry: a unit. `unitsFromChartSpecs`
 * therefore stays exactly where it is — the turn's `chart_specs` are still
 * the only published place a measure's unit appears, and the `ratio`
 * rescaling that depends on it (0.121361 → "12.1%") is untouched by
 * anything here. Display names correct what a number is CALLED; they say
 * nothing about how it is rendered.
 */
export function mapMetricDisplay(raw: unknown): MetricDisplay[] {
  const out: MetricDisplay[] = [];
  for (const entry of asArray(raw)) {
    if (!isRecord(entry)) continue;
    const metricId = asString(entry.metric_id);
    const displayName = asString(entry.display_name);
    if (metricId === "" || displayName === "") continue;
    out.push({
      metricId,
      displayName,
      ...(optionalString(entry.caveat) ? { caveat: String(entry.caveat) } : {}),
      ...(optionalString(entry.rationale) ? { rationale: String(entry.rationale) } : {}),
    });
  }
  return out;
}

export function metricDisplayIndex(
  entries: readonly MetricDisplay[],
): Record<string, MetricDisplay> {
  const index: Record<string, MetricDisplay> = {};
  for (const entry of entries) index[entry.metricId] = entry;
  return index;
}

/**
 * The engine's own spelling of a metric id in prose: `metric_label()` on
 * the server is `metric_id.replace("_", " ")`, and finding titles and
 * statements are built from it. Matching that exact form is what lets a
 * governed display name be substituted into a title the server already
 * composed, without touching anything else in the sentence.
 */
function metricPhrase(metricId: string): string {
  return metricId.replace(/_/g, " ");
}

/**
 * Apply governed display names to one server-composed string.
 *
 * A finding title reads `… : $22.4M timely filing at risk dollars`; the
 * pack says that measure is "Unbilled open inventory on a running filing
 * clock". The substitution is whole-phrase and case-insensitive, and
 * touches nothing it was not asked to: a metric with no correction, or a
 * title that never names its measure, comes back byte-identical.
 *
 * ONE pass over the text, with the alternatives ordered longest-first.
 * Both properties are load-bearing and both were learned the hard way:
 *
 *   longest-first, so `denial_rate` cannot claim the "denial rate" inside
 *     an `initial_denial_rate` phrase that has its own entry;
 *   one pass, so a name this function has just INSERTED is never matched
 *     again by a shorter entry — replacing sequentially turned "initial
 *     denial rate" into "First-pass Denial rate (all payers)", which is
 *     the correction eating its own output.
 */
export function applyMetricDisplayNames(
  text: string,
  display: Record<string, MetricDisplay>,
): string {
  if (text === "") return text;
  const haystack = text.toLowerCase();
  // Both spellings a server-composed string can carry: the prose form the
  // engine writes (`metric_label()` → "dnfb dollars") and the raw id.
  const alternatives: { spelling: string; displayName: string }[] = [];
  for (const entry of Object.values(display)) {
    // IDEMPOTENCE, and the third property this function had to learn.
    // Title composition is moving server-side, so the same string now
    // arrives already carrying its governed name — and governed names
    // routinely CONTAIN the phrase they replace ("First-pass denial rate
    // (all payers)" contains "denial rate"), which is exactly the shape
    // that makes a second pass splice a name into the middle of itself.
    // A text that already says the display name has nothing left to
    // correct for that metric, in either payload generation: f(f(x)) is
    // f(x), and a server-composed title is left byte-identical.
    if (haystack.includes(entry.displayName.toLowerCase())) continue;
    alternatives.push({ spelling: metricPhrase(entry.metricId), displayName: entry.displayName });
    if (entry.metricId !== metricPhrase(entry.metricId)) {
      alternatives.push({ spelling: entry.metricId, displayName: entry.displayName });
    }
  }
  if (alternatives.length === 0) return text;
  alternatives.sort((a, b) => b.spelling.length - a.spelling.length);
  const bySpelling = new Map(
    alternatives.map((a) => [a.spelling.toLowerCase(), a.displayName]),
  );
  const pattern = new RegExp(
    `(^|[^\\w])(${alternatives
      .map((a) => a.spelling.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .join("|")})(?![\\w])`,
    "gi",
  );
  return text.replace(
    pattern,
    (match, lead: string, spelling: string) =>
      `${lead}${bySpelling.get(spelling.toLowerCase()) ?? match}`,
  );
}

/* ------------------------------------------------------------------ */
/* UsageSummary → TurnUsage                                            */
/* ------------------------------------------------------------------ */

/**
 * What a turn spent. `cost_usd` stays a STRING all the way to the screen:
 * the server sends a decimal and a price rounded through a float is not
 * the price that was charged.
 *
 * `undefined` when the server published no block, and when it published
 * one recording nothing — a turn that made no model call has no cost to
 * report, and "$0.000000" on a card is noise dressed as disclosure.
 */
export function mapUsage(raw: unknown): TurnUsage | undefined {
  if (!isRecord(raw)) return undefined;
  const usage: TurnUsage = {
    llmCalls: asNumber(raw.llm_calls) ?? 0,
    costUsd: asString(raw.cost_usd, "0"),
    inputTokens: asNumber(raw.input_tokens) ?? 0,
    outputTokens: asNumber(raw.output_tokens) ?? 0,
    cacheReadTokens: asNumber(raw.cache_read_tokens) ?? 0,
    cacheCreationTokens: asNumber(raw.cache_creation_tokens) ?? 0,
    schemaRetries: asNumber(raw.schema_retries) ?? 0,
  };
  if (usage.llmCalls === 0 && Number(usage.costUsd) === 0) return undefined;
  return usage;
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
          // Which of the two budgets stopped the turn, when the envelope
          // says. The two want opposite responses from the reader.
          ...(optionalString(raw.subcode) ? { subcode: String(raw.subcode) } : {}),
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
      /**
       * The pinned population as words rather than as a hash
       * (`TurnAnswer.cohort`). Merged onto the header before it reaches
       * the store, because the chip lives in the context header and the
       * header payload publishes only the id and the size.
       */
      cohort?: CohortSummary;
      /**
       * The drilled card's figure beside this platform's re-derivation of
       * it. Present only when the turn carried an `anomaly_ref`.
       */
      anomalyReconciliation?: AnomalyReconciliation;
      /** The pack's governed display-name corrections for this turn's measures. */
      metricDisplay?: MetricDisplay[];
      /**
       * The ranked worklist, when this turn's interpretation resolved the
       * pack's governed worklist routing (or the caller asked outright).
       * Rides alongside the findings and never replaces them.
       */
      worklist?: WorklistData;
      /** Published only when the settings in force had debug on. */
      debug?: DebugTrace;
    }
  | {
      outcome: "clarification_required";
      investigationId: string;
      sessionId: string;
      clarification: ClarificationData;
      watermarkStale: boolean;
      /**
       * A clarification carries it too. That is the whole point of the
       * bridge: the question that USED to end here — "what should my
       * denial team work first" answered with four ranking bases and no
       * mention of the 33-card list — can now hand over the list while it
       * asks.
       */
      worklist?: WorklistData;
      debug?: DebugTrace;
    }
  | {
      outcome: "error";
      sessionId?: string;
      error: ErrorEnvelopeData;
      /** What the failed turn still spent (`TurnError.usage`). */
      usage?: TurnUsage;
    };

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
    const errorUsage = mapUsage(raw.usage);
    return {
      value: {
        outcome: "error",
        ...(typeof raw.session_id === "string" ? { sessionId: raw.session_id } : {}),
        error: envelope.value,
        ...(errorUsage !== undefined ? { usage: errorUsage } : {}),
      },
      drift: [],
    };
  }

  if (outcome === "clarification_required") {
    const drift: string[] = [];
    missingAt(raw, REQUIRED_CLARIFICATION_FIELDS, drift);
    if (drift.length > 0) return { value: null, drift };
    const clarificationTrace = mapDebugTrace(raw.debug);
    const clarificationWorklist = mapWorklist(raw.worklist, drift);
    return {
      value: {
        outcome: "clarification_required",
        investigationId: asString(raw.investigation_id),
        sessionId: asString(raw.session_id),
        ...(clarificationWorklist !== undefined ? { worklist: clarificationWorklist } : {}),
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

  const warningSentences = asArray(raw.warnings).filter((w): w is string => typeof w === "string");
  const metricDisplay = mapMetricDisplay(raw.metric_display);
  const displayIndex = metricDisplayIndex(metricDisplay);
  const turnBenchmarks = mapBenchmarks(raw.benchmarks);
  const findingContext: FindingContext = {
    unitByMetric: unitsFromChartSpecs(raw.chart_specs),
    ...(impactWithheldReason(warningSentences) !== undefined
      ? { impactWithheldReason: impactWithheldReason(warningSentences) }
      : {}),
    ...(metricDisplay.length > 0 ? { metricDisplay: displayIndex } : {}),
    ...(turnBenchmarks.length > 0 ? { benchmarks: turnBenchmarks } : {}),
  };

  const findings: Finding[] = [];
  asArray(raw.findings).forEach((entry, index) => {
    const finding = mapFinding(entry, findingContext);
    if (finding === null || finding.referent.value === "") {
      drift.push(`findings[${index}].referent`);
      return;
    }
    findings.push(finding);
  });

  // The turn's own codes reach the chart mapper, so a figure cannot rank
  // what the answer refused to rank. `readTurnWarnings` is used rather than
  // `warnings_v2` directly, because a turn stored before the classifier
  // existed carries the same facts as prose and deserves the same restraint.
  const turnWarnings = readTurnWarnings(raw.warnings_v2, warningSentences);
  const warningCodes = turnWarnings.map((w) => w.code);

  const charts: ChartSpec[] = [];
  asArray(raw.chart_specs).forEach((entry, index) => {
    const spec = mapChartSpec(entry, displayIndex, { warningCodes });
    if (spec === null || spec.id === "") {
      drift.push(`chart_specs[${index}].id`);
      return;
    }
    charts.push(spec);
  });

  const cohort = mapCohort(raw.cohort);
  const anomalyReconciliation = mapAnomalyReconciliation(raw.anomaly_reconciliation);
  const worklist = mapWorklist(raw.worklist, drift);
  const headerOnly = raw.context_header != null ? mapContextHeader(raw.context_header, pin) : null;
  // The header's own cohort block is an id and a size; the full definition
  // travels beside it on the answer. Merged here, once, so no component
  // has to know there were ever two sources for one chip.
  const header =
    headerOnly !== null && cohort !== undefined ? { ...headerOnly, cohort } : headerOnly;
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
      warnings: turnWarnings,
      ...(definition !== null ? { definition } : {}),
      ...(typeof raw.reconciliation === "string" ? { reconciliation: raw.reconciliation } : {}),
      ...(typeof raw.plan_hash === "string" ? { planHash: raw.plan_hash } : {}),
      watermarkStale: raw.watermark_stale === true,
      ...(evidence !== undefined ? { evidence } : {}),
      ...(metric !== undefined ? { metric } : {}),
      ...(cohort !== undefined ? { cohort } : {}),
      ...(anomalyReconciliation !== undefined ? { anomalyReconciliation } : {}),
      ...(metricDisplay.length > 0 ? { metricDisplay } : {}),
      ...(worklist !== undefined ? { worklist } : {}),
      ...(trace !== null ? { debug: trace } : {}),
    },
    drift,
  };
}

/**
 * Parse `GET /v1/investigations/{iid}` — a different shape from the turn
 * body and the only recovery path when the stream dropped after the
 * investigation id was known. A recovered turn renders what the server
 * actually kept rather than pretending the rest arrived.
 *
 * `pin` is optional because this response carries no watermark load time
 * or pack version of its own: those are the SESSION's, and a caller with
 * no session to hand (a test, a bare recovery read) gets the same parse
 * minus the context header rather than a header pinned to invented dates.
 */
export function parseInvestigationResponse(
  raw: unknown,
  pin?: WirePin,
): TurnResponseParse {
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

  const restoredWarnings = asArray(raw.warnings).filter((w): w is string => typeof w === "string");
  // `metric_display` IS published on `InvestigationResponse` now, so a
  // restored turn's titles get the same governed correction the live turn
  // got — read from the stored record rather than re-derived from today's
  // pack, which is the distinction that makes it safe: this is the block
  // the turn itself carried, not a re-captioning of an older answer with
  // names it never used. A payload generation that publishes none leaves
  // the list empty and the titles keep the engine's raw spelling.
  const restoredDisplay = mapMetricDisplay(raw.metric_display);
  const restoredBenchmarks = mapBenchmarks(raw.benchmarks);
  const restoredContext: FindingContext = {
    unitByMetric: unitsFromChartSpecs(raw.chart_specs),
    ...(impactWithheldReason(restoredWarnings) !== undefined
      ? { impactWithheldReason: impactWithheldReason(restoredWarnings) }
      : {}),
    ...(restoredDisplay.length > 0 ? { metricDisplay: metricDisplayIndex(restoredDisplay) } : {}),
    ...(restoredBenchmarks.length > 0 ? { benchmarks: restoredBenchmarks } : {}),
  };

  const findings: Finding[] = [];
  asArray(raw.findings).forEach((entry, index) => {
    const finding = mapFinding(entry, restoredContext);
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
  const restoredTurnWarnings = readTurnWarnings(raw.warnings_v2, restoredWarnings);
  const charts: ChartSpec[] = [];
  asArray(raw.chart_specs).forEach((entry, index) => {
    // A restored turn gets the same restraint the live one got: the stored
    // record carries the codes, so a rebuilt chart cannot re-create a
    // ranking the original answer refused.
    const spec = mapChartSpec(entry, undefined, {
      warningCodes: restoredTurnWarnings.map((w) => w.code),
    });
    if (spec === null || spec.id === "") {
      drift.push(`chart_specs[${index}].id`);
      return;
    }
    charts.push(spec);
  });
  const evidence = mapEvidence(raw.evidence);
  const metric = mapMetricProvenance(raw.metric);
  const cohort = mapCohort(raw.cohort);
  // The stated context of a restored answer, when the store kept one.
  //
  // Re-opening a session used to rebuild a thread of dollar figures with
  // NO window, NO scope, NO cohort and NO data-load chip — because
  // `AnswerCard` gates the whole header on its presence and this parser
  // published none. What survived a restore was the caveats and the bare
  // numbers, so yesterday's answers came back disproportionately
  // caveat-heavy with the context they were bounded by missing, which
  // falsifies the one promise the landing page makes in writing.
  //
  // Read defensively on purpose: two payload generations are in flight
  // and only one publishes `context_header` on `InvestigationResponse`.
  // When it is absent the header stays absent and the card says the
  // context was not stored — a stated absence, not a silent one.
  const headerOnly =
    raw.context_header != null && pin !== undefined
      ? mapContextHeader(raw.context_header, pin)
      : null;
  // The server's OWN account of the restore, published per investigation
  // (`restoration_notes`). It names what was rebuilt from the stored spec
  // rather than re-computed, and states the store's limits precisely —
  // "the narrative trace keeps its template, redactions and length, not
  // its sentences" is a fact this client cannot derive and would only
  // approximate. Rendered verbatim in the Restored popover.
  const restorationNotes = asArray(raw.restoration_notes).filter(
    (note): note is string => typeof note === "string" && note !== "",
  );
  // `context_header_restored` is the server saying so, rather than this
  // parser asserting it from the fact that it is the one doing the
  // reading. Defaulted to true when the field is absent, which preserves
  // the previous behaviour for the payload generation that does not
  // publish it — a header reached through THIS function was restored by
  // construction, so the default is the honest one either way.
  const restored = raw.context_header_restored !== false;
  const header =
    headerOnly !== null
      ? {
          ...headerOnly,
          ...(cohort !== undefined ? { cohort } : {}),
          restored,
          ...(restorationNotes.length > 0 ? { restorationNotes } : {}),
        }
      : null;

  return {
    value: {
      outcome: "answer",
      investigationId,
      sessionId: asString(raw.session_id),
      turnClass: asString(raw.turn_class) as TurnClass,
      ...(header !== null ? { header } : {}),
      findings,
      charts,
      // The composed prose, when the store kept it. Nothing is
      // reconstructed: a payload that publishes no narrative leaves this
      // undefined and the card says the write-up was not stored.
      ...(typeof raw.narrative === "string" && raw.narrative !== ""
        ? { narrative: raw.narrative }
        : {}),
      // Structured when the stored investigation has them; the prose
      // strings when it does not — which is exactly the case this
      // fallback exists for, since investigations stored before
      // `warnings_v2` shipped carry only sentences.
      warnings: restoredTurnWarnings,
      ...(typeof raw.plan_hash === "string" ? { planHash: raw.plan_hash } : {}),
      ...(evidence !== undefined ? { evidence } : {}),
      ...(metric !== undefined ? { metric } : {}),
      ...(cohort !== undefined ? { cohort } : {}),
      ...(restoredDisplay.length > 0 ? { metricDisplay: restoredDisplay } : {}),
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
 * dropped (findings by referent, charts by id, the narrative prefix), so
 * the answer state stays duplicate-free.
 *
 * Two things are re-emitted on purpose rather than skipped, and both are
 * the same fact: some of an answer's own vocabulary is not settled until
 * the turn is. The context header's cohort is a hash on the streamed frame
 * and a definition on the terminal one; findings and charts name their
 * measure by its raw id on the streamed frame and by the pack's governed
 * display name on the terminal one. The store replaces the header and
 * upserts findings/charts by identity, so re-emitting corrects rather than
 * duplicates.
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
      ...(response.error.subcode ? { subcode: response.error.subcode } : {}),
      ...(response.usage ? { usage: response.usage } : {}),
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
      ...(response.worklist ? { worklist: response.worklist } : {}),
      ...(response.debug ? { debug: response.debug } : {}),
    });
    return events;
  }

  // The header is re-emitted when this response carries a cohort even
  // though the stream already delivered one: the streamed `context_header`
  // frame publishes the cohort as an id and a size, and the definition,
  // grain and origin ride only on the terminal payload. Re-emitting is
  // safe because the store REPLACES the header (it is not append-only
  // state like findings), so the authoritative version simply wins.
  if (response.header && (!received.hasHeader || response.cohort !== undefined)) {
    events.push({
      type: "context_header",
      header: response.header,
      turnClass: response.turnClass,
    });
  }
  // Close out the stage rail honestly: the pipeline finished server-side.
  events.push({ type: "stage", stage: "narrating", status: "completed" });
  // Findings and charts already delivered on the live stream are re-emitted
  // when this turn published display-name corrections, for the same reason
  // the header is: `metric_display` rides on the TERMINAL payload, so a
  // streamed `finding` frame necessarily carries the engine's raw spelling
  // ("… $195,873.92 dnfb dollars") while the completed answer's narrative
  // reads "discharged not final billed". One answer must not call one
  // number two things. The store upserts findings by referent and charts
  // by id, so re-emitting replaces rather than duplicates.
  const corrected = response.metricDisplay !== undefined && response.metricDisplay.length > 0;
  for (const finding of response.findings) {
    if (corrected || !received.findingReferents.has(finding.referent.value)) {
      events.push({ type: "finding", finding });
    }
  }
  for (const spec of response.charts) {
    if (corrected || !received.chartIds.has(spec.id)) events.push({ type: "chart_spec", spec });
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
    // Both ride on the terminal frame for the same reason the grade does:
    // neither is settled until the turn is. The reconciliation needs the
    // answer's own figure, and the display list names the metrics the
    // finished answer actually cited.
    ...(response.anomalyReconciliation
      ? { anomalyReconciliation: response.anomalyReconciliation }
      : {}),
    ...(response.metricDisplay ? { metricDisplay: response.metricDisplay } : {}),
    // Rides on the terminal frame like the reconciliation and the grade:
    // it is only whole once the turn is, and the server publishes no
    // `worklist` SSE frame.
    ...(response.worklist ? { worklist: response.worklist } : {}),
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
  /**
   * `PortfolioResponse.status` — the snapshot's own word for itself,
   * `"ok"` or `"empty"`. The endpoint is unconditional and always answers
   * 200, so "there is no worklist here" is a fact about the DATA LOAD
   * (nothing was detected at this watermark), never about the deployment.
   * The client used to infer emptiness from `items.length === 0`, which
   * cannot tell a quiet load apart from a snapshot whose cards all failed
   * to parse; the server says which, so it is read rather than guessed.
   */
  status?: "ok" | "empty";
  /**
   * `PortfolioResponse.warnings_v2` (falling back to the prose
   * `warnings`) — snapshot-level truth about the list itself ("4 of 33
   * detected anomalies (36% of ranked impact) are not investigable at
   * this catalog and pack version…"). Published since the endpoint
   * shipped and, until recently, read by nothing: the panel showed 33
   * confident-looking rows and said nothing about the 36% it could not
   * open. Now classified, so the rail can style a caution apart from a
   * note without matching substrings.
   */
  warnings: Omit<WarningEvent, "type">[];
  /**
   * `PortfolioResponse.lanes` — the compliance/value split, in the
   * server's own order. Empty when the deployment publishes no lanes, and
   * the rail then draws one list rather than inventing a division.
   */
  lanes: PortfolioLane[];
}

export interface PortfolioParse {
  value: PortfolioSnapshotData | null;
  drift: string[];
}

/**
 * `TurnAnswer.worklist` — the ranked worklist, answered into a conversation.
 *
 * The bridge that closes the "two products in one shell" gap: the platform
 * computed a prioritised, reconciled worklist that the rail rendered, and
 * asking for it in words ("what should my denial team work on first this
 * week") reached a clarification offering four ranking bases, none of
 * which was the list. Live, that question now routes to the governed
 * concept `work_prioritization` and the turn carries the worklist beside
 * its findings.
 *
 * `items` are the SAME `AnomalyCard` objects the rail draws, from the same
 * build, parsed by the same `mapAnomalyCard` — a chat answer and the rail
 * cannot disagree about the order or the money because there is one
 * computation and one parser behind both.
 *
 * Two facts the rendering must not lose. `items` is a PAGE (live: 8 of 33,
 * with `limit`), while `lanes` carry the membership of the WHOLE
 * population — so a lane naming 31 cards over a page holding 8 is normal
 * and a renderer that trusts `lane.itemCount` as its row count would
 * invent 23 rows. And these cards are the detection feed's ranked work,
 * not findings this turn computed, which is what the WORKLIST_ATTACHED
 * warning exists to say.
 */
export interface WorklistData {
  /** Which governed artifact routed this: playbook, concept, or an explicit ask. */
  matchedOn: "playbook" | "concept" | "typed_query";
  matchedId: string;
  /** The server's own prose summary of the list. Rendered, never re-derived. */
  statement: string;
  label: string;
  description: string;
  formulaVersion: string;
  watermarkId: string;
  items: PortfolioItem[];
  /** Lane membership over the WHOLE population, not just this page. */
  lanes: PortfolioLane[];
  /** How many cards exist (33) versus how many this page carries (8). */
  totalItems: number;
  limit: number;
  totalRecoverableCentsEstimate: number;
  warnings: Omit<WarningEvent, "type">[];
}

/**
 * `WorklistPayload` → `WorklistData`, or undefined when the turn carried
 * none. `matched_on` is the one required field; everything else defaults,
 * because a worklist that arrives thin should render thin rather than
 * vanish.
 */
export function mapWorklist(raw: unknown, drift: string[] = []): WorklistData | undefined {
  if (!isRecord(raw)) return undefined;
  const matchedOn = raw.matched_on;
  if (matchedOn !== "playbook" && matchedOn !== "concept" && matchedOn !== "typed_query") {
    drift.push("worklist.matched_on");
    return undefined;
  }
  const items: PortfolioItem[] = [];
  asArray(raw.items).forEach((item, index) => {
    const card = mapAnomalyCard(item, index, drift, "worklist.items");
    if (card !== null) items.push(card);
  });
  return {
    matchedOn,
    matchedId: asString(raw.matched_id),
    statement: asString(raw.statement),
    label: asString(raw.label),
    description: asString(raw.description),
    formulaVersion: asString(raw.formula_version),
    watermarkId: asString(raw.watermark_id),
    items,
    lanes: mapPortfolioLanes(raw.lanes),
    totalItems: asNumber(raw.total_items) ?? items.length,
    limit: asNumber(raw.limit) ?? items.length,
    totalRecoverableCentsEstimate: asNumber(raw.total_recoverable_cents_estimate) ?? 0,
    warnings: readTurnWarnings(
      raw.warnings_v2,
      asArray(raw.warnings).filter((w): w is string => typeof w === "string"),
    ),
  };
}

/**
 * `AnomalyCard.priority` — the formula's terms, or `undefined` when the
 * server published none. Read tolerantly: the decomposition annotates the
 * ranking, so a partial one should degrade the badge, not blank the card.
 */
function mapPriorityDecomposition(raw: unknown): PriorityDecomposition | undefined {
  if (!isRecord(raw)) return undefined;
  return {
    impactNorm: asNumber(raw.impact_norm) ?? 0,
    recency: asNumber(raw.recency) ?? 0,
    recoverableNorm: asNumber(raw.recoverable_norm) ?? 0,
    impactTerm: asNumber(raw.impact_term) ?? 0,
    recencyTerm: asNumber(raw.recency_term) ?? 0,
    actionabilityTerm: asNumber(raw.actionability_term) ?? 0,
    weightSum: asNumber(raw.weight_sum) ?? 1,
    scoreBeforeFloor: asNumber(raw.score_before_floor) ?? 0,
    floorApplied: raw.floor_applied === true,
    floorValue: asNumber(raw.floor_value) ?? 0,
    floorBasis: asString(raw.floor_basis),
    // The two numbers that make the FIRST term checkable, plus which
    // figure they came from. `impact_norm = ranked_impact_cents /
    // impact_normalizer_cents`, and without the normalizer the reader
    // could verify every term of the formula except the one carrying the
    // money. Optional on purpose: a payload generation that publishes none
    // leaves the badge showing exactly what it showed before.
    ...(raw.ranked_on === "detector" ||
    raw.ranked_on === "platform" ||
    raw.ranked_on === "not_comparable"
      ? { rankedOn: raw.ranked_on }
      : {}),
    ...(asNumber(raw.ranked_impact_cents) !== undefined
      ? { rankedImpactCents: asNumber(raw.ranked_impact_cents) }
      : {}),
    ...(asNumber(raw.impact_normalizer_cents) !== undefined
      ? { impactNormalizerCents: asNumber(raw.impact_normalizer_cents) }
      : {}),
  };
}

/**
 * `PortfolioResponse.lanes`. A lane with no id or no label is dropped: the
 * id is what binds cards to it and the label is the heading, and a lane
 * missing either would render as an unnamed bucket of work.
 */
function mapPortfolioLanes(raw: unknown): PortfolioLane[] {
  const lanes: PortfolioLane[] = [];
  for (const entry of asArray(raw)) {
    if (!isRecord(entry)) continue;
    const id = asString(entry.id);
    const label = asString(entry.label);
    if (id === "" || label === "") continue;
    const anomalyIds = asArray(entry.anomaly_ids).filter(
      (value): value is string => typeof value === "string" && value !== "",
    );
    lanes.push({
      id,
      label,
      description: asString(entry.description),
      anomalyIds,
      itemCount: asNumber(entry.item_count) ?? anomalyIds.length,
      impactCents: asNumber(entry.impact_cents) ?? 0,
    });
  }
  return lanes;
}

/**
 * One `AnomalyCard` → the shape both surfaces render.
 *
 * Extracted from `parsePortfolioSnapshot` because the SAME cards now
 * arrive by a second route: `TurnAnswer.worklist.items` carries the very
 * objects the rail draws, from the same build — same `anomaly_priority`
 * version, same decomposition, same `ranked_on`, same reconciliation
 * state. Two parsers over one payload is how a chat answer and a rail come
 * to disagree about the order or the money, so there is one.
 *
 * Returns null and appends to `drift` when a card is missing a field
 * without which it cannot be drawn at all; every other absence is
 * tolerated as "the server did not say".
 */
function mapAnomalyCard(
  item: unknown,
  index: number,
  drift: string[],
  driftPrefix: string,
): PortfolioItem | null {
  {
    const missing: string[] = [];
    for (const path of REQUIRED_PORTFOLIO_ITEM_FIELDS) {
      pickAliased(item, path, PORTFOLIO_ITEM_ALIASES, missing);
    }
    if (missing.length > 0) {
      drift.push(...missing.map((path) => `${driftPrefix}[${index}].${path}`));
      return null;
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
    const str = (key: string): string | undefined => {
      const value = record[key];
      return typeof value === "string" && value !== "" ? value : undefined;
    };
    const num = (key: string): number | undefined => asNumber(record[key]);
    const severity = str("severity");
    const dimensions = asRecordList(record.dimensions)
      .map((d) => ({ dimension: asString(d.dimension), value: asString(d.value) }))
      .filter((d) => d.dimension !== "" && d.value !== "");
    // `drillable` is a published boolean with no default worth guessing:
    // absent, the honest read is "the server did not say it refused", and
    // the drill handle it published (or did not) settles it.
    const drillable =
      typeof record.drillable === "boolean"
        ? record.drillable
        : isRecord(record.drill_spec) || isRecord(record.drill);
    // What the drill will actually measure. On a repointed card this is
    // NOT `metric_id`: live ANM-001 reports denial_rate, is repointed
    // FROM denial_rate, and probes denied_dollars — so reading the
    // reported metric here would render "drills denial_rate, not
    // denial_rate", which is both wrong and obviously wrong.
    const drillMetricId = isRecord(record.drill_spec)
      ? asArray(record.drill_spec.metric_ids).find(
          (id): id is string => typeof id === "string" && id !== "",
        )
      : undefined;
    // A governed substitution for the detector's CUT. All three fields are
    // required together: a repoint with no rationale is a column swap the
    // card cannot justify, and showing it without the reasoning is worse
    // than not showing it — so it is dropped rather than half-rendered.
    const dimensionRepoints = asRecordList(record.drill_dimension_repoints)
      .map((entry) => ({
        fromDimension: asString(entry.from_dimension),
        toDimension: asString(entry.to_dimension),
        rationale: asString(entry.rationale),
      }))
      .filter((r) => r.fromDimension !== "" && r.toDimension !== "" && r.rationale !== "");

    return {
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

      // Everything below is published on AnomalyCard and was being dropped.
      ...(severity !== undefined ? { severity: severity as PortfolioItem["severity"] } : {}),
      ...(num("age_days") !== undefined ? { ageDays: num("age_days") } : {}),
      ...(num("recoverable_cents_estimate") !== undefined
        ? { recoverableCentsEstimate: num("recoverable_cents_estimate") }
        : {}),
      ...(str("actionability_label") !== undefined
        ? { actionabilityLabel: str("actionability_label") }
        : {}),
      ...(str("actionability_rationale") !== undefined
        ? { actionabilityRationale: str("actionability_rationale") }
        : {}),
      ...(num("priority_score") !== undefined ? { priorityScore: num("priority_score") } : {}),
      ...(record.compliance_floor_applied === true ? { complianceFloorApplied: true } : {}),
      // WHICH figure the ordering was computed from — the detector's or
      // this platform's re-derivation — the figure itself, and the
      // server's sentence explaining the choice. Live this is not one
      // answer across the list (19 detector / 9 platform / 5
      // not_comparable), so a rail that shows only the number is showing
      // a ranking whose basis varies card by card without saying so.
      // Absent still means the server has not said, never a guess.
      ...(record.ranked_on === "detector" ||
      record.ranked_on === "platform" ||
      record.ranked_on === "not_comparable"
        ? { rankedOn: record.ranked_on }
        : {}),
      ...(num("ranked_impact_cents") !== undefined
        ? { rankedImpactCents: num("ranked_impact_cents") }
        : {}),
      ...(str("ranked_on_note") !== undefined
        ? { rankedOnNote: str("ranked_on_note") }
        : {}),
      ...(mapPriorityDecomposition(record.priority) !== undefined
        ? { priority: mapPriorityDecomposition(record.priority) }
        : {}),
      ...(record.lane === "compliance" || record.lane === "value"
        ? { lane: record.lane }
        : {}),

      // The card's figure and this platform's re-derivation of it. Two
      // different claims, both published, and the card renders both when
      // they disagree rather than picking the flattering one.
      ...(num("reconciled_impact_cents") !== undefined
        ? { reconciledImpactCents: num("reconciled_impact_cents") }
        : {}),
      ...(str("reconciled_impact_metric_id") !== undefined
        ? { reconciledImpactMetricId: str("reconciled_impact_metric_id") }
        : {}),
      // FOUR states, not three. `not_comparable` was added when the
      // reconciler learned to refuse a snapshot-vs-window or a
      // ratio-vs-money comparison instead of coercing one, and this
      // filter still listed three — so the top live card (ANM-021,
      // `not_comparable`, with a written explanation of exactly why the
      // two figures are different KINDS of measurement) published its
      // agreement to a client that dropped it on the floor, and the rail
      // and the export both went silent about the most interesting
      // verdict the reconciler makes.
      ...(record.impact_agreement === "agreed" ||
      record.impact_agreement === "diverged" ||
      record.impact_agreement === "not_comparable" ||
      record.impact_agreement === "unavailable"
        ? { impactAgreement: record.impact_agreement }
        : {}),
      ...(num("impact_delta_cents") !== undefined
        ? { impactDeltaCents: num("impact_delta_cents") }
        : {}),
      ...(num("impact_delta_fraction") !== undefined
        ? { impactDeltaFraction: num("impact_delta_fraction") }
        : {}),
      ...(str("impact_reconciliation_note") !== undefined
        ? { impactReconciliationNote: str("impact_reconciliation_note") }
        : {}),
      ...(str("metric_id") !== undefined ? { metricId: str("metric_id") } : {}),
      // The pack's governed name for the reported measure. A worklist has
      // no answer to hang a §6.6 population caveat on, so the correction
      // travels on the card itself.
      ...(str("metric_display_name") !== undefined
        ? { metricDisplayName: str("metric_display_name") }
        : {}),
      ...(str("status") !== undefined ? { status: str("status") } : {}),
      ...(str("confidence") !== undefined ? { confidence: str("confidence") } : {}),
      ...(str("detected_at") !== undefined ? { detectedAt: str("detected_at") } : {}),
      ...(str("window_start") !== undefined ? { windowStart: str("window_start") } : {}),
      ...(str("window_end") !== undefined ? { windowEnd: str("window_end") } : {}),
      ...(dimensions.length > 0 ? { dimensions } : {}),

      drillable,
      ...(str("drill_unavailable_reason") !== undefined
        ? { drillUnavailableReason: str("drill_unavailable_reason") }
        : {}),
      ...(str("drill_repointed_from") !== undefined
        ? { drillRepointedFrom: str("drill_repointed_from") }
        : {}),
      ...(str("drill_repoint_rationale") !== undefined
        ? { drillRepointRationale: str("drill_repoint_rationale") }
        : {}),
      // The card's CUT was substituted, not just its measure. Kept as a
      // list because the payload publishes one, and dropped entirely when
      // a repoint arrives without its reasoning: the rationale IS the
      // disclosure — "drills primary_proc_group, not proc_group" without
      // the sentence explaining that one counts claims and the other
      // counts lines is a raw column swap shown to an analyst.
      ...(dimensionRepoints.length > 0 ? { drillDimensionRepoints: dimensionRepoints } : {}),
      ...(drillMetricId !== undefined ? { drillMetricId } : {}),

      // The card's typed first turn, carried through verbatim: it is
      // already the published shape, so translating it would only be an
      // opportunity to get it wrong.
      ...(isRecord(record.drill_spec) ? { drillSpec: record.drill_spec } : {}),
      // NO fallback handle. A synthesized `DrillInto(ANM-013)` against a
      // card the server marked `drillable: false` — with a written reason —
      // was the client claiming a capability the platform had just refused.
      ...(isRecord(record.drill)
        ? { drill: record.drill as unknown as PortfolioItem["drill"] }
        : {}),
    };
  }
}

export function parsePortfolioSnapshot(raw: unknown): PortfolioParse {
  const drift: string[] = [];
  if (!isRecord(raw) || !Array.isArray(raw.items)) return { value: null, drift: ["items"] };
  const items: PortfolioItem[] = [];
  raw.items.forEach((item, index) => {
    const card = mapAnomalyCard(item, index, drift, "items");
    if (card !== null) items.push(card);
  });
  const value: PortfolioSnapshotData = {
    items,
    warnings: readTurnWarnings(
      raw.warnings_v2,
      asArray(raw.warnings).filter((w): w is string => typeof w === "string"),
    ),
    lanes: mapPortfolioLanes(raw.lanes),
  };
  // PortfolioResponse spells these `watermark_id` / `formula_version`.
  const watermark =
    alias(raw, "watermark", "watermark_loaded_at") ?? raw.watermark_id;
  if (typeof watermark === "string") value.watermark = watermark;
  const rankingPolicy =
    alias(raw, "rankingPolicy", "ranking_policy") ?? raw.formula_version;
  if (typeof rankingPolicy === "string") value.rankingPolicy = rankingPolicy;
  if (raw.status === "ok" || raw.status === "empty") value.status = raw.status;
  return { value, drift };
}
