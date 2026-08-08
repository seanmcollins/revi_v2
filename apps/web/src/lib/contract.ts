/**
 * Wire-contract guard for the real API driver (M11).
 *
 * Typed-but-tolerant parsing:
 *   - unknown EXTRA fields are ignored (forward compatibility);
 *   - defaultable containers (empty lists/records) are filled in;
 *   - MISSING required fields are contract drift — the caller shows a
 *     visible banner and console.errors the exact field path. Never a
 *     silent blank UI.
 *
 * The REQUIRED_* path tables below are the single source of truth for
 * what this UI needs from the wire. `lib/contract-expectations.test.ts`
 * pins them against fixtures; when `contracts/openapi.json` lands at the
 * repo root, regenerate wire types (openapi-typescript) and reconcile —
 * any mismatch must fail those tests loudly.
 */

import type {
  ChartSpec,
  ClarificationData,
  ContextHeaderData,
  DataWatermark,
  DefinitionCardData,
  EvidenceBundle,
  EvidenceGrade,
  Finding,
  InterpretationData,
  LineageEdge,
  LineageNode,
  MetricContractSummary,
  PackVersionRef,
  SessionLineageData,
  TurnClass,
  TurnCompleteEvent,
  TurnEvent,
  WarningEvent,
} from "@/lib/types";
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
/* SSE frames — required paths per event kind                          */
/* ------------------------------------------------------------------ */

/**
 * The fields the UI cannot render without, per SSE event kind. Paths are
 * relative to the decoded event object (event name merged in as `type`).
 * Containers whose emptiness is valid (filters, suggestedRefinements,
 * probes…) are DEFAULTED instead — see `withDefaults`.
 */
export const REQUIRED_EVENT_FIELDS: Record<TurnEvent["type"], readonly string[]> = {
  stage: ["stage", "status"],
  context_header: [
    "turnClass",
    "header.window.start",
    "header.window.end",
    "header.window.basis",
    "header.grain.entity",
    "header.watermark.id",
    "header.watermark.loadedAt",
    "header.watermark.newestDataDate",
    "header.packVersion.packId",
    "header.packVersion.version",
  ],
  finding: [
    "finding.referent.value",
    "finding.referent.kind",
    "finding.title",
    "finding.statement",
    "finding.grade",
    "finding.directionOfGood",
    "finding.confidence",
  ],
  chart_spec: ["spec.id", "spec.kind", "spec.title", "spec.unit", "spec.series", "spec.rows"],
  narrative_delta: ["text"],
  clarification: ["clarification.question", "clarification.options"],
  warning: ["code", "message", "severity"],
  turn_complete: ["investigationId", "status"],
  error: ["code", "message"],
  interpretation: [
    "interpretation.metric.id",
    "interpretation.metric.name",
    "interpretation.metric.version",
    "interpretation.windowDescription",
  ],
  evidence: ["evidence.reconciliation.status"],
  definition_card: [
    "definition.term",
    "definition.normalizedTo",
    "definition.definition",
    "definition.packVersion.packId",
    "definition.packVersion.version",
  ],
};

const TURN_STATUSES: ReadonlySet<string> = new Set([
  "complete",
  "clarification_required",
  "failed",
]);

const CHART_SERIES_ITEM_FIELDS = ["key", "label", "role"] as const;
const CHART_ROW_ITEM_FIELDS = ["label", "values"] as const;

function checkArrayItems(
  root: unknown,
  arrayPath: string,
  itemFields: readonly string[],
  missing: string[],
): void {
  const array = getPath(root, arrayPath);
  if (!Array.isArray(array)) {
    // Presence was already checked by REQUIRED_EVENT_FIELDS; a non-array is drift.
    if (array !== undefined && array !== null) missing.push(arrayPath);
    return;
  }
  array.forEach((item, index) => {
    for (const field of itemFields) {
      const value = getPath(item, field);
      if (value === undefined || value === null) missing.push(`${arrayPath}[${index}].${field}`);
    }
  });
}

/**
 * Validate one decoded SSE event against the required-field table and
 * return it with defaultable containers filled. `ok: false` means the
 * frame must NOT be rendered — the caller reports contract drift.
 */
export function validateTurnEvent(event: TurnEvent): ParseResult<TurnEvent> {
  const missing: string[] = [];
  missingAt(event, REQUIRED_EVENT_FIELDS[event.type] ?? [], missing);

  switch (event.type) {
    case "chart_spec":
      checkArrayItems(event, "spec.series", CHART_SERIES_ITEM_FIELDS, missing);
      checkArrayItems(event, "spec.rows", CHART_ROW_ITEM_FIELDS, missing);
      break;
    case "clarification": {
      const options = getPath(event, "clarification.options");
      if (options !== undefined && options !== null && !Array.isArray(options)) {
        missing.push("clarification.options");
      }
      break;
    }
    case "turn_complete": {
      const status = getPath(event, "status");
      if (typeof status === "string" && !TURN_STATUSES.has(status)) missing.push("status");
      break;
    }
    default:
      break;
  }

  if (missing.length > 0) return { ok: false, missing };
  return { ok: true, value: withDefaults(event) };
}

/** Fill containers whose emptiness is valid, without mutating the input. */
function withDefaults(event: TurnEvent): TurnEvent {
  switch (event.type) {
    case "context_header":
      return { ...event, header: { ...event.header, filters: event.header.filters ?? [] } };
    case "finding": {
      const finding = event.finding;
      return {
        ...event,
        finding: {
          ...finding,
          values: finding.values ?? {},
          metricRefs: finding.metricRefs ?? [],
          suggestedRefinements: finding.suggestedRefinements ?? [],
        },
      };
    }
    case "interpretation": {
      const interpretation = event.interpretation;
      return {
        ...event,
        interpretation: {
          ...interpretation,
          filterDescriptions: interpretation.filterDescriptions ?? [],
          synonymMappings: interpretation.synonymMappings ?? [],
        },
      };
    }
    case "evidence": {
      const evidence = event.evidence;
      return {
        ...event,
        evidence: {
          ...evidence,
          probes: (evidence.probes ?? []).map((probe) => ({
            ...probe,
            operators: probe.operators ?? [],
          })),
          zeroProbeTurn: evidence.zeroProbeTurn ?? false,
        },
      };
    }
    case "definition_card": {
      const definition = event.definition;
      return {
        ...event,
        definition: {
          ...definition,
          sources: definition.sources ?? [],
          relatedConcepts: definition.relatedConcepts ?? [],
        },
      };
    }
    default:
      return event;
  }
}

/* ------------------------------------------------------------------ */
/* TurnResponse (blocking JSON + GET /v1/investigations/{iid})         */
/* ------------------------------------------------------------------ */

export const REQUIRED_TURN_RESPONSE_FIELDS = ["investigationId", "status"] as const;

export interface TurnResponseData {
  investigationId: string;
  status: TurnCompleteEvent["status"];
  turnClass?: TurnClass;
  header?: ContextHeaderData;
  interpretation?: InterpretationData;
  findings?: Finding[];
  charts?: ChartSpec[];
  narrative?: string;
  warnings?: Omit<WarningEvent, "type">[];
  clarification?: ClarificationData;
  evidence?: EvidenceBundle;
  definition?: DefinitionCardData;
  answerGrade?: EvidenceGrade;
  metric?: MetricContractSummary;
}

/** camelCase is canonical (mirrors types.ts); snake_case is tolerated. */
function alias(record: UnknownRecord, camel: string, snake: string): unknown {
  return record[camel] ?? record[snake];
}

export interface TurnResponseParse {
  /** Null when core required fields are missing (fatal drift). */
  value: TurnResponseData | null;
  /** Every missing required path found, fatal or not. */
  drift: string[];
}

/**
 * Parse a full TurnResponse. Core fields are fatal when missing; broken
 * optional sub-shapes are dropped and reported as drift so a partially
 * damaged response still renders what it can.
 */
export function parseTurnResponse(raw: unknown): TurnResponseParse {
  const drift: string[] = [];
  if (!isRecord(raw)) return { value: null, drift: [...REQUIRED_TURN_RESPONSE_FIELDS] };

  const investigationId = alias(raw, "investigationId", "investigation_id");
  const status = raw.status;
  if (typeof investigationId !== "string") drift.push("investigationId");
  if (typeof status !== "string" || !TURN_STATUSES.has(status)) drift.push("status");
  if (drift.length > 0) return { value: null, drift };

  const value: TurnResponseData = {
    investigationId: investigationId as string,
    status: status as TurnCompleteEvent["status"],
  };

  const turnClass = alias(raw, "turnClass", "turn_class");
  if (typeof turnClass === "string") value.turnClass = turnClass as TurnClass;

  const sub = <T extends TurnEvent>(event: T, prefix: string): T | null => {
    const result = validateTurnEvent(event);
    if (result.ok) return result.value as T;
    drift.push(...result.missing.map((path) => `${prefix}${path}`));
    return null;
  };

  if (raw.header !== undefined && raw.header !== null) {
    const event = sub(
      {
        type: "context_header",
        header: raw.header as ContextHeaderData,
        turnClass: (value.turnClass ?? "new_investigation") as TurnClass,
      },
      "",
    );
    if (event) value.header = event.header;
  }
  if (raw.interpretation !== undefined && raw.interpretation !== null) {
    const event = sub(
      { type: "interpretation", interpretation: raw.interpretation as InterpretationData },
      "",
    );
    if (event) value.interpretation = event.interpretation;
  }
  if (Array.isArray(raw.findings)) {
    value.findings = [];
    raw.findings.forEach((finding, index) => {
      const event = sub({ type: "finding", finding: finding as Finding }, `findings[${index}].`);
      if (event) value.findings?.push(event.finding);
    });
  }
  if (Array.isArray(raw.charts)) {
    value.charts = [];
    raw.charts.forEach((spec, index) => {
      const event = sub({ type: "chart_spec", spec: spec as ChartSpec }, `charts[${index}].`);
      if (event) value.charts?.push(event.spec);
    });
  }
  if (typeof raw.narrative === "string") value.narrative = raw.narrative;
  if (Array.isArray(raw.warnings)) {
    value.warnings = [];
    raw.warnings.forEach((warning, index) => {
      const event = sub(
        { type: "warning", ...(warning as Omit<WarningEvent, "type">) },
        `warnings[${index}].`,
      );
      if (event) {
        value.warnings?.push({ code: event.code, message: event.message, severity: event.severity });
      }
    });
  }
  if (raw.clarification !== undefined && raw.clarification !== null) {
    const event = sub(
      { type: "clarification", clarification: raw.clarification as ClarificationData },
      "",
    );
    if (event) value.clarification = event.clarification;
  }
  if (raw.evidence !== undefined && raw.evidence !== null) {
    const event = sub({ type: "evidence", evidence: raw.evidence as EvidenceBundle }, "");
    if (event) value.evidence = event.evidence;
  }
  const definition = alias(raw, "definition", "definition_card");
  if (definition !== undefined && definition !== null) {
    const event = sub(
      { type: "definition_card", definition: definition as DefinitionCardData },
      "",
    );
    if (event) value.definition = event.definition;
  }
  const answerGrade = alias(raw, "answerGrade", "answer_grade");
  if (typeof answerGrade === "string") value.answerGrade = answerGrade as EvidenceGrade;
  if (isRecord(raw.metric)) value.metric = raw.metric as unknown as MetricContractSummary;

  return { value, drift };
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
  hasInterpretation: boolean;
  hasEvidence: boolean;
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
    hasInterpretation: false,
    hasEvidence: false,
    hasDefinition: false,
    hasClarification: false,
    warningKeys: new Set(),
  };
}

/** Record a successfully validated event into the received-state tracker. */
export function trackReceived(received: ReceivedTurnState, event: TurnEvent): void {
  switch (event.type) {
    case "context_header":
      received.hasHeader = true;
      break;
    case "interpretation":
      received.hasInterpretation = true;
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
    case "evidence":
      received.hasEvidence = true;
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

  if (response.header && response.turnClass && !received.hasHeader) {
    events.push({ type: "context_header", header: response.header, turnClass: response.turnClass });
  }
  if (response.interpretation && !received.hasInterpretation) {
    events.push({ type: "interpretation", interpretation: response.interpretation });
  }
  // Close out the stage rail honestly: the pipeline finished server-side.
  if (response.status === "complete") {
    events.push({ type: "stage", stage: "narrating", status: "completed" });
  } else if (response.status === "clarification_required") {
    events.push({ type: "stage", stage: "interpreted", status: "completed" });
  }
  for (const finding of response.findings ?? []) {
    if (!received.findingReferents.has(finding.referent.value)) {
      events.push({ type: "finding", finding });
    }
  }
  for (const spec of response.charts ?? []) {
    if (!received.chartIds.has(spec.id)) events.push({ type: "chart_spec", spec });
  }
  for (const warning of response.warnings ?? []) {
    if (!received.warningKeys.has(`${warning.code}:${warning.message}`)) {
      events.push({ type: "warning", ...warning });
    }
  }
  if (response.narrative !== undefined && response.narrative.length > received.narrativeLength) {
    events.push({ type: "narrative_delta", text: response.narrative.slice(received.narrativeLength) });
  }
  if (response.evidence && !received.hasEvidence) {
    events.push({ type: "evidence", evidence: response.evidence });
  }
  if (response.definition && !received.hasDefinition) {
    events.push({ type: "definition_card", definition: response.definition });
  }
  if (response.clarification && !received.hasClarification) {
    events.push({ type: "clarification", clarification: response.clarification });
  }
  events.push({
    type: "turn_complete",
    investigationId: response.investigationId,
    status: response.status,
    ...(response.answerGrade !== undefined ? { answerGrade: response.answerGrade } : {}),
    ...(response.metric !== undefined ? { metric: response.metric } : {}),
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
/* GET /v1/portfolio/latest                                            */
/* ------------------------------------------------------------------ */

/**
 * What a portfolio card cannot be drawn without. The spec models these as
 * `AnomalyCard`, whose spelling is snake_case (`anomaly_id`, `impact_cents`)
 * — both are accepted, drift is reported against the canonical name.
 *
 * `grade` is deliberately still required: the GradeBadge is a trust
 * affordance, and rendering a dollar figure without the evidence grade that
 * earned it would invent provenance. AnomalyCard does not yet carry one, so
 * a real portfolio response trips the drift banner — which is the correct,
 * visible outcome, not a bug to paper over. See the reconciliation notes in
 * contract-expectations.test.ts.
 */
export const REQUIRED_PORTFOLIO_ITEM_FIELDS = [
  "referent",
  "title",
  "impactCents",
  "grade",
] as const;

/** Wire aliases: canonical camelCase path → the spec's AnomalyCard key. */
export const PORTFOLIO_ITEM_ALIASES: Record<string, string> = {
  referent: "anomaly_id",
  impactCents: "impact_cents",
  issueClass: "category",
  detail: "description",
  impactLabel: "impact_label",
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
    items.push({
      rank: typeof record.rank === "number" ? record.rank : index + 1,
      referent,
      title: record.title as string,
      issueClass: typeof issueClass === "string" ? issueClass : "",
      impactCents: (record.impactCents ?? record.impact_cents) as number,
      impactLabel: typeof impactLabel === "string" ? impactLabel : "",
      detail: typeof detail === "string" ? detail : "",
      grade: record.grade as PortfolioItem["grade"],
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
