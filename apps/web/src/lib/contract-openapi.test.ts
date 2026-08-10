/**
 * OpenAPI reconciliation.
 *
 * `contracts/openapi.json` is the server's published truth; `types.gen.ts`
 * is its mechanical translation (`pnpm gen:types`). This file BINDS the
 * hand-written REQUIRED_* path tables in lib/contract.ts to it, in two
 * directions:
 *
 *   compile time — every canonical UI path is mapped to a key that must
 *     exist on the generated wire type. A server-side rename or removal
 *     stops being a runtime surprise and becomes a type error here.
 *   run time     — the tables are checked against the spec's own
 *     `required` arrays, so a field going from required to optional is
 *     caught even without regenerating.
 *
 * Division of authority: types.gen.ts owns WIRE shapes; lib/types.ts stays
 * authoritative for UI-only shapes (TurnEvent, StageStatus, LineageNode's
 * display `label`, PortfolioItem's `drill` action) which the API does not
 * model and must not dictate.
 *
 * ── What is bound, and what is deliberately not ──
 *
 * 1. The §12 error envelope IS published now. `ErrorEnvelope` is bound below
 *    exactly like SessionResponse, AND the routes the driver calls are walked
 *    to confirm 400/404/409/503 actually reference it — a published schema
 *    nobody declares would rot silently. Status 422 stays FastAPI's own
 *    `HTTPValidationError` (malformed request body only): one status, one
 *    model. Domain (§12) failures arrive as 400.
 * 2. `AnomalyCard` carries NO evidence `grade`, and that is a settled design
 *    decision rather than a gap. A grade certifies how *this platform*
 *    computed a number from certified semantics; an anomaly card is a record
 *    read out of an external detection system as-of a watermark, so grading
 *    it would invent provenance. The card instead publishes the three facts a
 *    grade would have implied — `provenance` (`external_detection`),
 *    `priority_formula_version`, `source_watermark_id` — all required, all
 *    asserted below. The UI shows a DETECTION chip, not a GradeBadge.
 * 3. STILL A GAP: `SessionLineageResponse` names the node list
 *    `investigations` and omits a display `label`; the parser aliases the
 *    former and derives the latter (see LINEAGE_NODE_ALIASES).
 * 4. SSE frames are modelled and their BODIES are bound. `TurnStreamEvent`
 *    publishes the nine frame kinds; `data` is an open object, but each
 *    frame body is itself a published model (`ContextHeaderPayload`,
 *    `FindingPayload`, `ChartSpec`, `ErrorEnvelope`), so those are bound
 *    below too. Three UI event kinds — `interpretation`, `evidence`,
 *    `definition_card` — are deliberately NOT frames, and that asymmetry is
 *    asserted rather than assumed.
 * 5. CLOSED (M14): the blocking 200 body is a discriminated
 *    `TurnAnswer | TurnClarification | TurnError` on `outcome`, and the
 *    parser now discriminates the same way instead of demanding a `status`
 *    field none of the variants carries. Each variant is bound to its own
 *    schema below, `GET /v1/investigations/{iid}` keeps its own parser and
 *    binding, and `TurnRequest.spec` (the typed first turn a portfolio
 *    card's `drill_spec` posts) is bound alongside them.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  LINEAGE_EDGE_ALIASES,
  LINEAGE_NODE_ALIASES,
  PORTFOLIO_ITEM_ALIASES,
  REQUIRED_ANSWER_FIELDS,
  REQUIRED_CLARIFICATION_FIELDS,
  REQUIRED_ERROR_ENVELOPE_FIELDS,
  REQUIRED_FRAME_FIELDS,
  REQUIRED_INVESTIGATION_FIELDS,
  REQUIRED_LINEAGE_EDGE_FIELDS,
  REQUIRED_LINEAGE_NODE_FIELDS,
  REQUIRED_PORTFOLIO_ITEM_FIELDS,
  REQUIRED_SESSION_FIELDS,
  REQUIRED_SESSION_SUMMARY_FIELDS,
  REQUIRED_TURN_ERROR_FIELDS,
} from "@/lib/contract";
import type { components, paths } from "@/lib/types.gen";

type Schemas = components["schemas"];

/* ------------------------------------------------------------------ */
/* Compile-time binding: canonical UI path → generated wire key         */
/* ------------------------------------------------------------------ */

/**
 * `satisfies Record<UiPath, keyof WireType>` is the whole trick: the KEYS
 * force every entry of the REQUIRED_* table to be accounted for, and the
 * VALUES must name a field that still exists on the generated type.
 */
const SESSION_BACKING = {
  session_id: "session_id",
  "watermark.id": "watermark_id",
  "watermark.loaded_at": "watermark_loaded_at",
  "watermark.newest_data_date": "newest_data_date",
  "pack.pack_id": "pack_id",
  "pack.version": "pack_version",
} satisfies Record<(typeof REQUIRED_SESSION_FIELDS)[number], keyof Schemas["SessionResponse"]>;

/** §12's envelope, published at last — bound the same way as the session. */
const ERROR_ENVELOPE_BACKING = {
  code: "code",
  message: "message",
  correlation_id: "correlation_id",
} satisfies Record<(typeof REQUIRED_ERROR_ENVELOPE_FIELDS)[number], keyof Schemas["ErrorEnvelope"]>;

/**
 * The 200 body is a discriminated union, so each variant is bound to its
 * own schema. Every required path is a wire key now — the parser reads the
 * published spelling directly instead of demanding a `status` field none of
 * the three variants has ever carried.
 */
const ANSWER_BACKING = {
  outcome: "outcome",
  session_id: "session_id",
  investigation_id: "investigation_id",
  turn_class: "turn_class",
} satisfies Record<(typeof REQUIRED_ANSWER_FIELDS)[number], keyof Schemas["TurnAnswer"]>;

const CLARIFICATION_BACKING = {
  outcome: "outcome",
  session_id: "session_id",
  investigation_id: "investigation_id",
  question: "question",
} satisfies Record<
  (typeof REQUIRED_CLARIFICATION_FIELDS)[number],
  keyof Schemas["TurnClarification"]
>;

const TURN_ERROR_BACKING = {
  outcome: "outcome",
  error: "error",
} satisfies Record<(typeof REQUIRED_TURN_ERROR_FIELDS)[number], keyof Schemas["TurnError"]>;

/** `GET /v1/investigations/{iid}` — the reconnect-recovery shape. */
const INVESTIGATION_BACKING = {
  investigation_id: "investigation_id",
  turn_id: "turn_id",
  turn_class: "turn_class",
  status: "status",
  created_at: "created_at",
} satisfies Record<
  (typeof REQUIRED_INVESTIGATION_FIELDS)[number],
  keyof Schemas["InvestigationResponse"]
>;

/**
 * Per-frame payload schemas. `TurnStreamEvent.data` is an open object, so
 * the spec pins the frame KINDS but not their contents — except that every
 * frame body IS a published model, and these bindings say which. A rename
 * inside `ContextHeaderPayload` or `FindingPayload` is now a type error
 * here rather than a blank panel at runtime.
 */
const FRAME_PAYLOAD_SCHEMA: Partial<Record<keyof typeof REQUIRED_FRAME_FIELDS, keyof Schemas>> = {
  context_header: "ContextHeaderPayload",
  finding: "FindingPayload",
  chart_spec: "ChartSpec",
  error: "ErrorEnvelope",
};

/**
 * The session list reads the wire spelling directly (no camelCase aliases
 * to keep in step), so this binding exists to make a server-side rename a
 * type error rather than a rail that silently drops every row.
 */
const SESSION_SUMMARY_BACKING = {
  session_id: "session_id",
  title: "title",
  created_at: "created_at",
  last_activity: "last_activity",
} satisfies Record<
  (typeof REQUIRED_SESSION_SUMMARY_FIELDS)[number],
  keyof Schemas["SessionSummary"]
>;

const LINEAGE_NODE_BACKING = {
  turnId: "turn_id",
  investigationId: "investigation_id",
  turnClass: "turn_class",
} satisfies Record<
  (typeof REQUIRED_LINEAGE_NODE_FIELDS)[number],
  keyof Schemas["InvestigationResponse"]
>;

const LINEAGE_EDGE_BACKING = {
  parentInvestigationId: "parent_id",
  childInvestigationId: "child_id",
  turnId: "turn_id",
} satisfies Record<
  (typeof REQUIRED_LINEAGE_EDGE_FIELDS)[number],
  keyof Schemas["LineageEdgePayload"]
>;

/**
 * Every required portfolio path is backed by a real AnomalyCard key now —
 * `provenance` replaced the `grade` the card cannot honestly claim, so this
 * no longer needs to be `Partial<>`.
 */
const PORTFOLIO_ITEM_BACKING = {
  referent: "anomaly_id",
  title: "title",
  impactCents: "impact_cents",
  provenance: "provenance",
} satisfies Record<
  (typeof REQUIRED_PORTFOLIO_ITEM_FIELDS)[number],
  keyof Schemas["AnomalyCard"]
>;

/**
 * Not *required* by the UI — a missing one degrades the DETECTION badge
 * tooltip rather than blanking the card — but published as required by the
 * spec, so bind them anyway: a server-side rename becomes a type error here
 * instead of a silently empty tooltip.
 */
const PORTFOLIO_PROVENANCE_ANNOTATIONS = {
  priorityFormulaVersion: "priority_formula_version",
  sourceWatermarkId: "source_watermark_id",
} satisfies Record<string, keyof Schemas["AnomalyCard"]>;

/** Every key the driver puts in a turn body must be a real TurnRequest field. */
const TURN_REQUEST_KEYS = [
  "idempotency_key",
  "correlation_id",
  "utterance",
  "spec",
  "refinements",
  "clarification_response",
  "re_anchor",
] as const satisfies readonly (keyof Schemas["TurnRequest"])[];

/**
 * The evidence bundle the drawer, the reconciliation banner and the "no
 * new queries" chip read. It is not a REQUIRED_* table — a missing bundle
 * degrades the drawer instead of blanking the answer — so it is bound by
 * UI concept rather than by required path: every key the mapper reads must
 * still be a real field on the published schema.
 */
const EVIDENCE_BACKING = {
  probes: "probes",
  reconciliation: "reconciliation",
  warehouseQueries: "warehouse_queries",
  cacheHits: "cache_hits",
  zeroProbeTurn: "zero_probe_turn",
  answerGrade: "answer_grade",
} satisfies Record<string, keyof Schemas["EvidencePayload"]>;

const EVIDENCE_PROBE_BACKING = {
  probeId: "id",
  probeHash: "hash",
  description: "purpose",
  kind: "kind",
  metrics: "metrics",
  cacheHit: "cache_hit",
  rowCount: "rows",
  limit: "limit",
  truncated: "truncated",
  suppressedCells: "suppressed_cells",
  grade: "grade",
  durationMs: "duration_ms",
} satisfies Record<string, keyof Schemas["EvidenceProbePayload"]>;

/**
 * Cut when the drawer was pointed at the live API, and listed so they stay
 * cut: nothing on the wire backs them. Masked sample rows have no source
 * (the planner emits no row-evidence probe, so no stored frame holds
 * row-level content); the reconciliation totals are computed inside the
 * reconcile operator and never leave it; per-probe operator versions are
 * recorded for the turn, not per probe. If any of these appears on
 * `EvidencePayload` later, THEN the UI may show it.
 */
const EVIDENCE_FIELDS_WITH_NO_WIRE_SOURCE = [
  "sample_rows",
  "trace_note",
  "operators",
] as const;

/**
 * The "Governed" badge. Same binding discipline as the evidence bundle:
 * bound by UI concept, because a missing block degrades the badge to
 * silence rather than blanking the answer.
 */
const METRIC_PROVENANCE_BACKING = {
  primary: "primary",
  metrics: "metrics",
  playbookId: "playbook_id",
  packSnapshotId: "pack_snapshot_id",
} satisfies Record<string, keyof Schemas["MetricProvenancePayload"]>;

/**
 * Cut when the badge was pointed at the live API, and listed so they stay
 * cut. Every one of them is a real field of a governed `MetricContract` —
 * and none is recorded per turn, so rendering them would mean reading
 * TODAY's pack and captioning an OLDER answer with it. The badge shows the
 * id and the version the turn read; that pair is enough to look the rest
 * up, and unlike a name copied out of a live pack it cannot go stale.
 * If any of these ever appears on `MetricProvenancePayload`, THEN the UI
 * may show it.
 */
const METRIC_FIELDS_WITH_NO_WIRE_SOURCE = [
  "name",
  "kind",
  "numerator",
  "denominator",
  "primary_date_basis",
  "exclusions",
  "unit",
  "direction_of_good",
  "fingerprint",
] as const;

/** A portfolio card's drill handle IS a typed first turn (§18.1-10). */
const DRILL_SPEC_BACKING = {
  metric_ids: "metric_ids",
  dimensions: "dimensions",
  filters: "filters",
  window: "window",
} satisfies Record<string, keyof Schemas["TypedInvestigationSpec"]>;

/** The endpoints the driver calls, with the method it calls them by. */
const DRIVER_OPERATIONS = {
  "/v1/health": ["get"],
  // POST opens (or re-joins) a session; GET lists this tenant's sessions.
  "/v1/sessions": ["post", "get"],
  "/v1/sessions/{session_id}/turns": ["post"],
  "/v1/sessions/{session_id}/lineage": ["get"],
  "/v1/investigations/{investigation_id}": ["get"],
  "/v1/investigations/{investigation_id}/trace": ["get"],
  "/v1/capabilities": ["get"],
  "/v1/portfolio/latest": ["get"],
} as const satisfies Partial<Record<keyof paths, readonly ("get" | "post")[]>>;

/** Statuses §12 answers with the envelope; 422 is FastAPI's own, on purpose. */
const ENVELOPE_STATUSES = ["400", "404", "409", "503"] as const;

const ENVELOPE_REF = "#/components/schemas/ErrorEnvelope";

/**
 * UI event kinds the server does NOT publish as SSE frames — they are
 * sections of the `turn_complete` payload, replayed as synthetic events by
 * `turnResponseToEvents`. Asserted below so the asymmetry stays deliberate.
 */
const TURN_COMPLETE_ONLY_KINDS = ["interpretation", "evidence", "definition_card"] as const;

/* ------------------------------------------------------------------ */
/* Run-time binding against contracts/openapi.json                      */
/* ------------------------------------------------------------------ */

interface OpenApiSchema {
  required?: string[];
  properties?: Record<string, unknown>;
  description?: string;
  /** Closed vocabularies (NarrativeDepth, EvidenceDepth, frame kinds). */
  enum?: string[];
}
interface OpenApiRef {
  $ref?: string;
}
interface OpenApiMediaSchema extends OpenApiRef {
  oneOf?: OpenApiRef[];
  discriminator?: { propertyName?: string };
}
interface OpenApiResponse {
  content?: Record<string, { schema?: OpenApiMediaSchema }>;
}
interface OpenApiOperation {
  responses?: Record<string, OpenApiResponse>;
  parameters?: { name: string; in: string }[];
}
interface OpenApiDoc {
  paths: Record<string, Record<string, OpenApiOperation>>;
  components: { schemas: Record<string, OpenApiSchema> };
}

const SPEC: OpenApiDoc = JSON.parse(
  readFileSync(path.resolve(import.meta.dirname, "../../../../contracts/openapi.json"), "utf8"),
) as OpenApiDoc;

const schema = (name: string): OpenApiSchema => {
  const found = SPEC.components.schemas[name];
  if (!found) throw new Error(`schema "${name}" vanished from contracts/openapi.json`);
  return found;
};

/** Assert each wire key is present AND listed as required by the spec. */
function expectGuaranteed(schemaName: string, wireKeys: readonly string[]): void {
  const { required = [], properties = {} } = schema(schemaName);
  for (const key of wireKeys) {
    expect(Object.keys(properties), `${schemaName}.${key} must exist`).toContain(key);
    expect(required, `${schemaName}.${key} must be required`).toContain(key);
  }
}

const operation = (route: string, method: string): OpenApiOperation => {
  const found = SPEC.paths[route]?.[method];
  if (!found) throw new Error(`${method.toUpperCase()} ${route} vanished from openapi.json`);
  return found;
};

const responseSchema = (
  op: OpenApiOperation,
  status: string,
  mediaType: string,
): OpenApiMediaSchema | undefined => op.responses?.[status]?.content?.[mediaType]?.schema;

const responseRef = (op: OpenApiOperation, status: string, mediaType: string): string | undefined =>
  responseSchema(op, status, mediaType)?.$ref;

/** The nine SSE frame kinds the server publishes on TurnStreamEvent. */
function publishedSseKinds(): string[] {
  const event = schema("TurnStreamEvent").properties?.event as { enum?: string[] } | undefined;
  const kinds = event?.enum;
  if (!Array.isArray(kinds)) throw new Error("TurnStreamEvent.event no longer publishes an enum");
  return kinds;
}

describe("OpenAPI reconciliation — paths", () => {
  it("still publishes every endpoint the driver calls, by the method it calls", () => {
    for (const [route, methods] of Object.entries(DRIVER_OPERATIONS)) {
      expect(Object.keys(SPEC.paths)).toContain(route);
      for (const method of methods) {
        expect(Object.keys(SPEC.paths[route] ?? {}), `${route} must accept ${method}`).toContain(
          method,
        );
      }
    }
  });
});

describe("OpenAPI reconciliation — the session list", () => {
  it("guarantees every field a session row is drawn from", () => {
    expectGuaranteed("SessionSummary", Object.values(SESSION_SUMMARY_BACKING));
  });

  it("maps each required row path to exactly one wire key", () => {
    expect(Object.keys(SESSION_SUMMARY_BACKING).sort()).toEqual(
      [...REQUIRED_SESSION_SUMMARY_FIELDS].sort(),
    );
  });

  it("publishes a total, so a capped page cannot pass as the whole history", () => {
    expect(Object.keys(schema("SessionListResponse").properties ?? {})).toEqual(
      expect.arrayContaining(["sessions", "total", "tenant", "limit"]),
    );
  });

  it("takes no tenant parameter — the token decides whose list this is", () => {
    const operation = SPEC.paths["/v1/sessions"]?.get as OpenApiOperation | undefined;
    const names = (operation?.parameters ?? []).map((p) => p.name);
    expect(names).not.toContain("tenant");
    expect(names).toContain("limit");
  });
});

describe("OpenAPI reconciliation — session bootstrap", () => {
  it("guarantees every field parseSessionResponse requires", () => {
    expectGuaranteed("SessionResponse", Object.values(SESSION_BACKING));
  });

  it("maps each canonical UI path to exactly one wire key", () => {
    expect(Object.keys(SESSION_BACKING).sort()).toEqual([...REQUIRED_SESSION_FIELDS].sort());
  });
});

describe("OpenAPI reconciliation — turn submission", () => {
  it("accepts every key the driver sends in a turn body", () => {
    const properties = Object.keys(schema("TurnRequest").properties ?? {});
    for (const key of TURN_REQUEST_KEYS) expect(properties).toContain(key);
  });

  it("guarantees every field each 200 variant is read for", () => {
    expectGuaranteed("TurnAnswer", Object.values(ANSWER_BACKING));
    expectGuaranteed("TurnClarification", Object.values(CLARIFICATION_BACKING));
    expectGuaranteed("TurnError", Object.values(TURN_ERROR_BACKING));
  });

  it("guarantees the fields a recovered turn is read for", () => {
    expectGuaranteed("InvestigationResponse", Object.values(INVESTIGATION_BACKING));
  });

  it("publishes the typed first turn a portfolio card posts", () => {
    expectGuaranteed("TypedInvestigationSpec", ["metric_ids", "window"]);
    const properties = Object.keys(schema("TypedInvestigationSpec").properties ?? {});
    for (const key of Object.values(DRILL_SPEC_BACKING)) expect(properties).toContain(key);
    // and every card carries one, so a cold-start drill is never guesswork
    expectGuaranteed("AnomalyCard", ["drill_spec"]);
  });
});

describe("OpenAPI reconciliation — lineage", () => {
  it("guarantees the investigation fields the DAG needs", () => {
    expectGuaranteed("InvestigationResponse", Object.values(LINEAGE_NODE_BACKING));
  });

  it("guarantees both edge endpoints", () => {
    expectGuaranteed("LineageEdgePayload", Object.values(LINEAGE_EDGE_BACKING));
  });

  it("aliases the spec spelling for every required node and edge path", () => {
    for (const field of REQUIRED_LINEAGE_NODE_FIELDS) {
      expect(LINEAGE_NODE_ALIASES[field]).toBe(LINEAGE_NODE_BACKING[field]);
    }
    for (const field of REQUIRED_LINEAGE_EDGE_FIELDS) {
      expect(LINEAGE_EDGE_ALIASES[field]).toBe(LINEAGE_EDGE_BACKING[field]);
    }
  });

  it("carries the node list under `investigations`, which the parser aliases", () => {
    expect(Object.keys(schema("SessionLineageResponse").properties ?? {})).toContain(
      "investigations",
    );
  });
});

describe("OpenAPI reconciliation — portfolio", () => {
  it("guarantees the anomaly fields that back a portfolio card", () => {
    // impact_cents is optional on AnomalyCard (it defaults to 0), so only
    // presence is asserted for it; the rest are spec-required.
    const properties = Object.keys(schema("AnomalyCard").properties ?? {});
    for (const key of Object.values(PORTFOLIO_ITEM_BACKING)) expect(properties).toContain(key);
    expectGuaranteed("AnomalyCard", ["anomaly_id", "title", "provenance"]);
  });

  it("maps each required portfolio path to exactly one wire key", () => {
    expect(Object.keys(PORTFOLIO_ITEM_BACKING).sort()).toEqual(
      [...REQUIRED_PORTFOLIO_ITEM_FIELDS].sort(),
    );
  });

  it("aliases the spec spelling for the backed paths", () => {
    for (const [uiPath, wireKey] of Object.entries(PORTFOLIO_ITEM_BACKING)) {
      if (uiPath === "title") continue; // same name on both sides
      expect(PORTFOLIO_ITEM_ALIASES[uiPath]).toBe(wireKey);
    }
    for (const [uiPath, wireKey] of Object.entries(PORTFOLIO_PROVENANCE_ANNOTATIONS)) {
      expect(PORTFOLIO_ITEM_ALIASES[uiPath]).toBe(wireKey);
    }
  });

  it("publishes NO evidence grade on AnomalyCard — by design, not as a gap", () => {
    // This is not a hole waiting to be filled. A grade certifies how THIS
    // platform computed a number from certified semantics; an anomaly card is
    // a record read from an external detection system as-of a watermark, so
    // stamping DIRECT/DERIVED/PROXY on it would invent provenance. If a
    // `grade` ever appears here, that is a server-side design regression to
    // argue with — not a signal to start requiring one in the UI.
    expect(Object.keys(schema("AnomalyCard").properties ?? {})).not.toContain("grade");
    expect(REQUIRED_PORTFOLIO_ITEM_FIELDS as readonly string[]).not.toContain("grade");
    expect(schema("AnomalyCard").description ?? "").toContain("No evidence grade, by construction");
  });

  it("guarantees the three provenance facts that stand in for a grade", () => {
    expectGuaranteed("AnomalyCard", [
      "provenance",
      ...Object.values(PORTFOLIO_PROVENANCE_ANNOTATIONS),
    ]);
    expect(schema("AnomalyCard").properties?.provenance).toMatchObject({
      const: "external_detection",
    });
  });
});

describe("OpenAPI reconciliation — the evidence bundle", () => {
  it("publishes the bundle on every answer, not only in debug mode", () => {
    expect(Object.keys(schema("TurnAnswer").properties ?? {})).toContain("evidence");
    // ...and on the by-id read, so a restored turn shows its working.
    expect(Object.keys(schema("InvestigationResponse").properties ?? {})).toContain("evidence");
  });

  it("backs every field the drawer reads with a published wire key", () => {
    const bundle = Object.keys(schema("EvidencePayload").properties ?? {});
    for (const key of Object.values(EVIDENCE_BACKING)) expect(bundle).toContain(key);
    const probe = Object.keys(schema("EvidenceProbePayload").properties ?? {});
    for (const key of Object.values(EVIDENCE_PROBE_BACKING)) expect(probe).toContain(key);
  });

  it("keeps the cut affordances cut — they have no wire source", () => {
    const bundle = Object.keys(schema("EvidencePayload").properties ?? {});
    const probe = Object.keys(schema("EvidenceProbePayload").properties ?? {});
    for (const field of EVIDENCE_FIELDS_WITH_NO_WIRE_SOURCE) {
      expect(bundle, `EvidencePayload.${field}`).not.toContain(field);
      expect(probe, `EvidenceProbePayload.${field}`).not.toContain(field);
    }
    // The verdict is a status plus the recorded summary — not two totals.
    const reconciliation = Object.keys(schema("EvidenceReconciliation").properties ?? {});
    expect(reconciliation.sort()).toEqual(["detail", "status", "summary"]);
  });

  it("publishes the governed provenance on both doors onto a turn", () => {
    // On the live answer AND on the by-id read, so a restored turn keeps
    // its badge instead of reading as ungoverned.
    expect(Object.keys(schema("TurnAnswer").properties ?? {})).toContain("metric");
    expect(Object.keys(schema("InvestigationResponse").properties ?? {})).toContain("metric");
  });

  it("backs every field the badge reads with a published wire key", () => {
    const block = Object.keys(schema("MetricProvenancePayload").properties ?? {});
    for (const key of Object.values(METRIC_PROVENANCE_BACKING)) expect(block).toContain(key);
    // The pack the TURN recorded — not the session pin the header carries.
    expect(block).toContain("pack_id");
    expect(block).toContain("pack_version");
  });

  it("keeps the contract-detail fields cut — nothing per turn records them", () => {
    const block = Object.keys(schema("MetricProvenancePayload").properties ?? {});
    for (const field of METRIC_FIELDS_WITH_NO_WIRE_SOURCE) {
      expect(block, `MetricProvenancePayload.${field}`).not.toContain(field);
    }
    // A metric reference is an id and the version it was READ at, and the
    // badge and the drawer share one shape for it rather than two that
    // could disagree about the same contract.
    expect(Object.keys(schema("EvidenceMetricRef").properties ?? {}).sort()).toEqual([
      "contract_version",
      "id",
    ]);
  });

  it("keeps the drawer and the debug trace reading one probe shape", () => {
    // Two projections of one recorded trace: if their probe fields drift
    // apart, the drawer and the trace panel can disagree about the same
    // turn, which is the failure this whole design exists to prevent.
    const shown = Object.keys(schema("EvidenceProbePayload").properties ?? {});
    const traced = Object.keys(schema("DebugProbe").properties ?? {});
    for (const field of traced) expect(shown).toContain(field);
  });
});

describe("OpenAPI reconciliation — §12 error envelope", () => {
  it("guarantees every field parseErrorEnvelope requires", () => {
    expectGuaranteed("ErrorEnvelope", Object.values(ERROR_ENVELOPE_BACKING));
  });

  it("maps each canonical UI path to exactly one wire key", () => {
    expect(Object.keys(ERROR_ENVELOPE_BACKING).sort()).toEqual(
      [...REQUIRED_ERROR_ENVELOPE_FIELDS].sort(),
    );
  });

  it("declares ErrorEnvelope on 400/404/409/503 for every route the driver calls", () => {
    // The schema existing is not enough — the driver decodes an envelope out
    // of ANY non-2xx body, so each route must actually declare it. This is
    // the half that would rot silently.
    for (const [route, methods] of Object.entries(DRIVER_OPERATIONS)) {
      for (const method of methods) {
        const op = operation(route, method);
        for (const status of ENVELOPE_STATUSES) {
          expect(
            responseRef(op, status, "application/json"),
            `${method.toUpperCase()} ${route} → ${status} must return ErrorEnvelope`,
          ).toBe(ENVELOPE_REF);
        }
      }
    }
  });

  it("leaves 422 to FastAPI's HTTPValidationError — one status, one model", () => {
    // Domain (§12) errors are 400 now; 422 means a malformed request body and
    // nothing else, so it must NOT be re-pointed at ErrorEnvelope.
    for (const [route, methods] of Object.entries(DRIVER_OPERATIONS)) {
      for (const method of methods) {
        const ref = responseRef(operation(route, method), "422", "application/json");
        if (ref === undefined) continue; // routes with no parameters declare no 422
        expect(ref, `${method.toUpperCase()} ${route} → 422`).toBe(
          "#/components/schemas/HTTPValidationError",
        );
      }
    }
  });
});

describe("OpenAPI reconciliation — SSE frames", () => {
  it("models the frame envelope itself", () => {
    expectGuaranteed("TurnStreamEvent", ["event", "data"]);
    expect(
      responseRef(
        operation("/v1/sessions/{session_id}/turns", "post"),
        "200",
        "text/event-stream",
      ),
    ).toBe("#/components/schemas/TurnStreamEvent");
  });

  it("binds every published frame kind to a required-field table", () => {
    const published = publishedSseKinds();
    expect(published).toHaveLength(9);
    expect(published.sort()).toEqual(Object.keys(REQUIRED_FRAME_FIELDS).sort());
  });

  it("guarantees every wire field the frame parser requires", () => {
    // `TurnStreamEvent.data` is an open object, but each frame body IS a
    // published model. Binding them here is what turns a server-side rename
    // into a failing test instead of a blank panel.
    for (const [kind, schemaName] of Object.entries(FRAME_PAYLOAD_SCHEMA)) {
      expectGuaranteed(
        schemaName as string,
        REQUIRED_FRAME_FIELDS[kind as keyof typeof REQUIRED_FRAME_FIELDS],
      );
    }
  });

  it("keeps the three turn_complete-only kinds out of the SSE enum", () => {
    // interpretation / evidence / definition_card are UI event kinds the
    // server does not stream. The definition card and the evidence bundle
    // ride inside the turn_complete payload — the bundle is only whole
    // once the turn is, so there is nothing to stream mid-flight — and
    // `interpretation` is UI-only. They must not be "fixed" by adding
    // them to TurnStreamEvent.
    const published = publishedSseKinds();
    for (const kind of TURN_COMPLETE_ONLY_KINDS) {
      expect(published, `"${kind}" is not an SSE frame`).not.toContain(kind);
      expect(Object.keys(REQUIRED_FRAME_FIELDS), `"${kind}" is not a wire frame`).not.toContain(
        kind,
      );
    }
  });

  it("leaves the per-kind envelope open while the bodies stay typed", () => {
    expect(schema("TurnStreamEvent").properties?.data).toMatchObject({
      type: "object",
      additionalProperties: true,
    });
  });
});

describe("OpenAPI reconciliation — the discriminated 200 turn body", () => {
  /**
   * M13 pinned this as an open gap: the 200 body became
   * `TurnAnswer | TurnClarification | TurnError` discriminated on
   * `outcome`, while `parseTurnResponse` still required a `status` field
   * none of the three carries — so a conforming server's JSON replay
   * tripped the drift banner on every turn.
   *
   * It is closed, and not by aliasing `outcome` to `status`. The parser
   * discriminates the same way the wire does and each variant maps to its
   * own outcome: an answer, a clarification (a *successful* outcome, §2.8),
   * or a turn error that carries an envelope and no investigation id at
   * all. These assertions hold the shape of what was reconciled.
   */
  const TURN_OUTCOME_VARIANTS = ["TurnAnswer", "TurnClarification", "TurnError"] as const;

  it("publishes the 200 JSON body as a oneOf discriminated on `outcome`", () => {
    const body = responseSchema(
      operation("/v1/sessions/{session_id}/turns", "post"),
      "200",
      "application/json",
    );
    expect(body?.discriminator?.propertyName).toBe("outcome");
    expect(body?.oneOf?.map((variant) => variant.$ref)).toEqual(
      TURN_OUTCOME_VARIANTS.map((name) => `#/components/schemas/${name}`),
    );
  });

  it("discriminates on `outcome`, which every variant guarantees", () => {
    for (const name of TURN_OUTCOME_VARIANTS) {
      expectGuaranteed(name, ["outcome"]);
      // and none of them has a `status` — the field the old parser demanded
      expect(Object.keys(schema(name).properties ?? {}), `${name}.status`).not.toContain("status");
    }
  });

  it("only the answer/clarification variants carry an investigation id", () => {
    expectGuaranteed("TurnAnswer", ["investigation_id"]);
    expectGuaranteed("TurnClarification", ["investigation_id"]);
    // TurnError has none, which is why the parsed union has none either
    expect(Object.keys(schema("TurnError").properties ?? {})).not.toContain("investigation_id");
    expect(REQUIRED_TURN_ERROR_FIELDS as readonly string[]).not.toContain("investigation_id");
  });

  it("names the answer's payload sections the way the parser reads them", () => {
    const properties = Object.keys(schema("TurnAnswer").properties ?? {});
    for (const key of ["context_header", "chart_specs", "findings", "definitional", "warnings"]) {
      expect(properties, `TurnAnswer.${key}`).toContain(key);
    }
  });

  it("keeps recovery-by-id honest — InvestigationResponse guarantees its own shape", () => {
    expectGuaranteed("InvestigationResponse", Object.values(INVESTIGATION_BACKING));
  });
});

/* ------------------------------------------------------------------ */
/* Session settings + debug traces                                     */
/* ------------------------------------------------------------------ */

/**
 * The settings panel and debug mode are rendered from published shapes,
 * so the same discipline applies: every key the UI reads is bound to a key
 * the generated wire type still has, and the facts the panel depends on to
 * decide what to OFFER are asserted here rather than assumed.
 */
const SETTINGS_BACKING = {
  modelTier: "model_tier",
  maxTurnCostUsd: "max_turn_cost_usd",
  narrativeDepth: "narrative_depth",
  evidenceDepth: "evidence_depth",
  debug: "debug",
} satisfies Record<string, keyof Schemas["SessionSettingsModel"]>;

const BOUNDS_BACKING = {
  modelTiers: "model_tiers",
  defaultModelTier: "default_model_tier",
  modelTierEffective: "model_tier_effective",
  maxTurnCostUsd: "max_turn_cost_usd",
  narrativeDepths: "narrative_depths",
  evidenceDepths: "evidence_depths",
  evidenceDepthDeepMultiplier: "evidence_depth_deep_multiplier",
  debugAvailable: "debug_available",
} satisfies Record<string, keyof Schemas["SettingsBoundsPayload"]>;

const DEBUG_TRACE_BACKING = {
  traceId: "trace_id",
  sessionId: "session_id",
  investigationId: "investigation_id",
  turnId: "turn_id",
  turnClass: "turn_class",
  classificationConfidence: "classification_confidence",
  interpretation: "interpretation",
  refinementOperators: "refinement_operators",
  planHash: "plan_hash",
  probes: "probes",
  grades: "grades",
  weakestGrade: "weakest_grade",
  findingGrades: "finding_grades",
  llmCalls: "llm_calls",
  timingsMs: "timings_ms",
  watermarkId: "watermark_id",
  epoch: "epoch",
  packVersion: "pack_version",
  redactions: "redactions",
} satisfies Record<string, keyof Schemas["DebugTracePayload"]>;

const DEBUG_PROBE_BACKING = {
  id: "id",
  hash: "hash",
  purpose: "purpose",
  cacheHit: "cache_hit",
  rows: "rows",
  limit: "limit",
  truncated: "truncated",
  suppressedCells: "suppressed_cells",
  grade: "grade",
  durationMs: "duration_ms",
} satisfies Record<string, keyof Schemas["DebugProbe"]>;

const DEBUG_LLM_CALL_BACKING = {
  template: "template",
  model: "model",
  inputTokens: "input_tokens",
  outputTokens: "output_tokens",
  costUsd: "cost_usd",
  schemaRetries: "schema_retries",
  attempts: "attempts",
  durationMs: "duration_ms",
  failure: "failure",
} satisfies Record<string, keyof Schemas["DebugLlmCall"]>;

describe("OpenAPI reconciliation — session settings", () => {
  it("binds every control the panel renders to a published field", () => {
    const properties = Object.keys(schema("SessionSettingsModel").properties ?? {});
    for (const key of Object.values(SETTINGS_BACKING)) {
      expect(properties, `SessionSettingsModel.${key}`).toContain(key);
    }
  });

  it("carries the per-turn ceiling as a decimal STRING, like every other money field", () => {
    const budget = schema("SessionSettingsModel").properties?.max_turn_cost_usd as
      | { anyOf?: { type?: string }[] }
      | undefined;
    expect(budget?.anyOf?.map((variant) => variant.type)).toEqual(["string", "null"]);
    expect(schema("SettingsBoundsPayload").properties?.max_turn_cost_usd).toMatchObject({
      type: "string",
    });
  });

  it("publishes the bounds the panel needs to decide what to offer", () => {
    const properties = Object.keys(schema("SettingsBoundsPayload").properties ?? {});
    for (const key of Object.values(BOUNDS_BACKING)) {
      expect(properties, `SettingsBoundsPayload.${key}`).toContain(key);
    }
  });

  it("hangs the bounds off /v1/capabilities, and settings off both request shapes", () => {
    expect(Object.keys(schema("CapabilitiesResponse").properties ?? {})).toContain("settings");
    // Turn-scoped application is the one the UI uses; session-open exists too.
    expect(Object.keys(schema("TurnRequest").properties ?? {})).toContain("settings");
    expect(Object.keys(schema("OpenSessionRequest").properties ?? {})).toContain("settings");
    // …and the resolved values come back on the session, not the requested ones.
    expect(Object.keys(schema("SessionResponse").properties ?? {})).toContain("settings");
  });

  it("keeps the depth vocabularies closed — the panel renders exactly these", () => {
    expect(schema("NarrativeDepth").enum).toEqual(["summary", "analyst"]);
    expect(schema("EvidenceDepth").enum).toEqual(["standard", "deep"]);
  });
});

describe("OpenAPI reconciliation — debug traces", () => {
  it("binds every field the debug panel renders", () => {
    const properties = Object.keys(schema("DebugTracePayload").properties ?? {});
    for (const key of Object.values(DEBUG_TRACE_BACKING)) {
      expect(properties, `DebugTracePayload.${key}`).toContain(key);
    }
  });

  it("guarantees the four identity fields the mapper refuses to invent", () => {
    expectGuaranteed("DebugTracePayload", [
      "trace_id",
      "session_id",
      "investigation_id",
      "turn_id",
    ]);
  });

  it("binds the probe and LLM-call rows", () => {
    const probeProperties = Object.keys(schema("DebugProbe").properties ?? {});
    for (const key of Object.values(DEBUG_PROBE_BACKING)) {
      expect(probeProperties, `DebugProbe.${key}`).toContain(key);
    }
    const callProperties = Object.keys(schema("DebugLlmCall").properties ?? {});
    for (const key of Object.values(DEBUG_LLM_CALL_BACKING)) {
      expect(callProperties, `DebugLlmCall.${key}`).toContain(key);
    }
  });

  it("keeps `rows` nullable — planned-but-not-executed is not zero rows", () => {
    const rows = schema("DebugProbe").properties?.rows as { anyOf?: { type?: string }[] } | undefined;
    expect(rows?.anyOf?.map((variant) => variant.type)).toEqual(["integer", "null"]);
  });

  it("rides the debug block on both successful outcomes, and on no new SSE frame kind", () => {
    expect(Object.keys(schema("TurnAnswer").properties ?? {})).toContain("debug");
    expect(Object.keys(schema("TurnClarification").properties ?? {})).toContain("debug");
    // The `turn_complete` frame carries the whole TurnResponse, so debug
    // reaches a streaming client without a new frame kind to parse.
    expect(publishedSseKinds()).not.toContain("debug");
  });

  it("still serves the after-the-fact trace route the UI reads", () => {
    const op = operation("/v1/investigations/{investigation_id}/trace", "get");
    expect(responseRef(op, "200", "application/json")).toBe(
      "#/components/schemas/DebugTracePayload",
    );
    // Refusals travel in the §12 envelope, so they can be shown verbatim.
    expect(responseRef(op, "400", "application/json")).toBe(ENVELOPE_REF);
  });
});
