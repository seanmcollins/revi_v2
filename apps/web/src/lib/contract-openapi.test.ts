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
 * 4. SSE frames ARE modelled now, partially. `TurnStreamEvent` publishes the
 *    nine frame kinds the turn route emits, and they are bound below to
 *    REQUIRED_EVENT_FIELDS. Its `data` is an open object, so the per-kind
 *    payload shape remains the UI's own contract, pinned by fixtures in
 *    contract-expectations.test.ts. Three UI event kinds — `interpretation`,
 *    `evidence`, `definition_card` — are deliberately NOT SSE frames: they
 *    arrive inside the `turn_complete` payload, and that asymmetry is
 *    asserted rather than assumed.
 * 5. NEW GAP, opened by the same change: the turn route's blocking 200 JSON
 *    is now a discriminated `TurnAnswer | TurnClarification | TurnError` on
 *    `outcome`, and none of those carries the `status` `parseTurnResponse`
 *    requires. The GET-by-id recovery path is fine (InvestigationResponse
 *    guarantees both fields); the same-idempotency-key JSON replay is not,
 *    and trips the drift banner against a conforming server. Pinned at the
 *    bottom of this file rather than papered over — closing it means binding
 *    the TurnAnswer payload sections, not aliasing one field.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  LINEAGE_EDGE_ALIASES,
  LINEAGE_NODE_ALIASES,
  PORTFOLIO_ITEM_ALIASES,
  REQUIRED_ERROR_ENVELOPE_FIELDS,
  REQUIRED_EVENT_FIELDS,
  REQUIRED_LINEAGE_EDGE_FIELDS,
  REQUIRED_LINEAGE_NODE_FIELDS,
  REQUIRED_PORTFOLIO_ITEM_FIELDS,
  REQUIRED_SESSION_FIELDS,
  REQUIRED_TURN_RESPONSE_FIELDS,
} from "@/lib/contract";
import type { TurnEvent } from "@/lib/types";
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

const TURN_RESPONSE_BACKING = {
  investigationId: "investigation_id",
  status: "status",
} satisfies Record<
  (typeof REQUIRED_TURN_RESPONSE_FIELDS)[number],
  keyof Schemas["InvestigationResponse"]
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
  parentTurnId: "parent_id",
  childTurnId: "child_id",
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
  "refinements",
  "clarification_response",
  "re_anchor",
] as const satisfies readonly (keyof Schemas["TurnRequest"])[];

/** The endpoints the driver calls, with the method it calls them by. */
const DRIVER_OPERATIONS = {
  "/v1/health": "get",
  "/v1/sessions": "post",
  "/v1/sessions/{session_id}/turns": "post",
  "/v1/sessions/{session_id}/lineage": "get",
  "/v1/investigations/{investigation_id}": "get",
  "/v1/portfolio/latest": "get",
} as const satisfies Partial<Record<keyof paths, "get" | "post">>;

/** Statuses §12 answers with the envelope; 422 is FastAPI's own, on purpose. */
const ENVELOPE_STATUSES = ["400", "404", "409", "503"] as const;

const ENVELOPE_REF = "#/components/schemas/ErrorEnvelope";

/**
 * UI event kinds the server does NOT publish as SSE frames — they are
 * sections of the `turn_complete` payload, replayed as synthetic events by
 * `turnResponseToEvents`. Asserted below so the asymmetry stays deliberate.
 */
const TURN_COMPLETE_ONLY_KINDS = [
  "interpretation",
  "evidence",
  "definition_card",
] as const satisfies readonly TurnEvent["type"][];

/* ------------------------------------------------------------------ */
/* Run-time binding against contracts/openapi.json                      */
/* ------------------------------------------------------------------ */

interface OpenApiSchema {
  required?: string[];
  properties?: Record<string, unknown>;
  description?: string;
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
    for (const [route, method] of Object.entries(DRIVER_OPERATIONS)) {
      expect(Object.keys(SPEC.paths)).toContain(route);
      expect(Object.keys(SPEC.paths[route] ?? {}), `${route} must accept ${method}`).toContain(
        method,
      );
    }
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

  it("guarantees the fields a recovered TurnResponse is read for", () => {
    expectGuaranteed("InvestigationResponse", Object.values(TURN_RESPONSE_BACKING));
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
    for (const [route, method] of Object.entries(DRIVER_OPERATIONS)) {
      const op = operation(route, method);
      for (const status of ENVELOPE_STATUSES) {
        expect(
          responseRef(op, status, "application/json"),
          `${method.toUpperCase()} ${route} → ${status} must return ErrorEnvelope`,
        ).toBe(ENVELOPE_REF);
      }
    }
  });

  it("leaves 422 to FastAPI's HTTPValidationError — one status, one model", () => {
    // Domain (§12) errors are 400 now; 422 means a malformed request body and
    // nothing else, so it must NOT be re-pointed at ErrorEnvelope.
    for (const [route, method] of Object.entries(DRIVER_OPERATIONS)) {
      const ref = responseRef(operation(route, method), "422", "application/json");
      if (ref === undefined) continue; // routes with no parameters declare no 422
      expect(ref, `${method.toUpperCase()} ${route} → 422`).toBe(
        "#/components/schemas/HTTPValidationError",
      );
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

  it("binds every published frame kind to a REQUIRED_EVENT_FIELDS entry", () => {
    const published = publishedSseKinds();
    expect(published).toHaveLength(9);
    for (const kind of published) {
      expect(
        Object.keys(REQUIRED_EVENT_FIELDS),
        `SSE frame "${kind}" is published but the UI has no required-field table for it`,
      ).toContain(kind);
    }
  });

  it("keeps the three turn_complete-only kinds out of the SSE enum", () => {
    // interpretation / evidence / definition_card are sections of the
    // turn_complete payload, replayed as synthetic events by
    // turnResponseToEvents. They are NOT frames, and must not be "fixed" by
    // adding them to TurnStreamEvent.
    const published = publishedSseKinds();
    for (const kind of TURN_COMPLETE_ONLY_KINDS) {
      expect(Object.keys(REQUIRED_EVENT_FIELDS)).toContain(kind);
      expect(published, `"${kind}" rides inside turn_complete, it is not an SSE frame`).not.toContain(
        kind,
      );
    }
  });

  it("accounts for every UI event kind as either published or turn_complete-only", () => {
    expect([...publishedSseKinds(), ...TURN_COMPLETE_ONLY_KINDS].sort()).toEqual(
      Object.keys(REQUIRED_EVENT_FIELDS).sort(),
    );
  });

  it("leaves the per-kind payload shape to the UI — `data` is an open object", () => {
    // TurnStreamEvent.data is `{type: object, additionalProperties: true}`, so
    // the spec pins the frame KINDS but not their contents. REQUIRED_EVENT_FIELDS
    // remains the only contract for those, pinned by contract-expectations.test.ts.
    expect(schema("TurnStreamEvent").properties?.data).toMatchObject({
      type: "object",
      additionalProperties: true,
    });
  });
});

describe("OpenAPI reconciliation — OPEN GAP: the blocking turn body", () => {
  /**
   * `parseTurnResponse` has two callers and the spec now models them
   * differently:
   *
   *   GET /v1/investigations/{iid}  → InvestigationResponse — bound above
   *     (TURN_RESPONSE_BACKING); `investigation_id` and `status` are both
   *     guaranteed, so recovery-by-id is honest.
   *   POST .../turns, Accept: application/json (`replayTurnJson`, the
   *     same-idempotency-key recovery) → now a discriminated
   *     `TurnAnswer | TurnClarification | TurnError` on `outcome`. Those
   *     carry NO `status`, TurnError carries no `investigation_id`, and the
   *     payload sections are spelled differently again (`context_header`,
   *     `chart_specs`, `definitional`).
   *
   * So a conforming server's JSON replay trips the drift banner on `status`.
   * That is the correct visible failure mode, not a silent one — but it is a
   * real gap, and closing it means mapping `outcome` → status AND binding the
   * TurnAnswer payload sections, not aliasing one field. These assertions pin
   * the mismatch so it cannot rot into a silent assumption either way.
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

  it("none of the three variants carries the `status` parseTurnResponse requires", () => {
    expect(REQUIRED_TURN_RESPONSE_FIELDS as readonly string[]).toContain("status");
    for (const name of TURN_OUTCOME_VARIANTS) {
      expect(Object.keys(schema(name).properties ?? {}), `${name}.status`).not.toContain("status");
      expect(schema(name).required, `${name}.outcome`).toContain("outcome");
    }
  });

  it("only the answer/clarification variants carry an investigation id", () => {
    expectGuaranteed("TurnAnswer", ["investigation_id"]);
    expectGuaranteed("TurnClarification", ["investigation_id"]);
    expect(Object.keys(schema("TurnError").properties ?? {})).not.toContain("investigation_id");
  });

  it("keeps recovery-by-id honest — InvestigationResponse still guarantees both", () => {
    expectGuaranteed("InvestigationResponse", ["investigation_id", "status"]);
  });
});
