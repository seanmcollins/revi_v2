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
 * ── Known, deliberate gaps (each one is asserted below so it cannot rot) ──
 *
 * 1. ErrorEnvelope is NOT in the spec. The API documents only FastAPI's
 *    HTTPValidationError, but §12 mandates {code, message, correlation_id}
 *    and the driver decodes it on every non-2xx. Tracked as a spec gap,
 *    not a client bug.
 * 2. AnomalyCard carries no evidence `grade`. The UI still requires one —
 *    a dollar figure without the grade that earned it invents provenance —
 *    so a live portfolio response trips the visible drift banner by design.
 * 3. SessionLineageResponse names the node list `investigations` and omits
 *    a display `label`; the parser aliases the former and derives the
 *    latter (see LINEAGE_NODE_ALIASES).
 * 4. TurnEvent SSE frames are not modelled in the spec at all (they are a
 *    streaming media type, not a JSON schema). REQUIRED_EVENT_FIELDS in
 *    lib/contract.ts remains their only contract, pinned by fixtures in
 *    contract-expectations.test.ts.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import {
  LINEAGE_EDGE_ALIASES,
  LINEAGE_NODE_ALIASES,
  PORTFOLIO_ITEM_ALIASES,
  REQUIRED_LINEAGE_EDGE_FIELDS,
  REQUIRED_LINEAGE_NODE_FIELDS,
  REQUIRED_PORTFOLIO_ITEM_FIELDS,
  REQUIRED_SESSION_FIELDS,
  REQUIRED_TURN_RESPONSE_FIELDS,
} from "@/lib/contract";
import type { components } from "@/lib/types.gen";

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

/** `grade` is the documented gap — AnomalyCard has no such key. */
const PORTFOLIO_ITEM_BACKING = {
  referent: "anomaly_id",
  title: "title",
  impactCents: "impact_cents",
} satisfies Partial<
  Record<(typeof REQUIRED_PORTFOLIO_ITEM_FIELDS)[number], keyof Schemas["AnomalyCard"]>
>;

/** Every key the driver puts in a turn body must be a real TurnRequest field. */
const TURN_REQUEST_KEYS = [
  "idempotency_key",
  "correlation_id",
  "utterance",
  "refinements",
  "clarification_response",
  "re_anchor",
] as const satisfies readonly (keyof Schemas["TurnRequest"])[];

/** The endpoints the driver calls must all still exist as paths. */
const DRIVER_PATHS = [
  "/v1/health",
  "/v1/sessions",
  "/v1/sessions/{session_id}/turns",
  "/v1/sessions/{session_id}/lineage",
  "/v1/investigations/{investigation_id}",
  "/v1/portfolio/latest",
] as const satisfies readonly (keyof import("@/lib/types.gen").paths)[];

/* ------------------------------------------------------------------ */
/* Run-time binding against contracts/openapi.json                      */
/* ------------------------------------------------------------------ */

interface OpenApiSchema {
  required?: string[];
  properties?: Record<string, unknown>;
}
interface OpenApiDoc {
  paths: Record<string, unknown>;
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

describe("OpenAPI reconciliation — paths", () => {
  it("still publishes every endpoint the driver calls", () => {
    for (const route of DRIVER_PATHS) {
      expect(Object.keys(SPEC.paths)).toContain(route);
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
    // impact_cents is optional on AnomalyCard, so only presence is asserted.
    const properties = Object.keys(schema("AnomalyCard").properties ?? {});
    for (const key of Object.values(PORTFOLIO_ITEM_BACKING)) expect(properties).toContain(key);
    expect(schema("AnomalyCard").required).toContain("anomaly_id");
  });

  it("aliases the spec spelling for the backed paths", () => {
    for (const [uiPath, wireKey] of Object.entries(PORTFOLIO_ITEM_BACKING)) {
      if (uiPath === "title") continue; // same name on both sides
      expect(PORTFOLIO_ITEM_ALIASES[uiPath]).toBe(wireKey);
    }
  });

  it("documents that AnomalyCard still has no evidence grade", () => {
    // When the server adds one, this fails — add `grade` to
    // PORTFOLIO_ITEM_BACKING and delete this test.
    expect(Object.keys(schema("AnomalyCard").properties ?? {})).not.toContain("grade");
    expect(REQUIRED_PORTFOLIO_ITEM_FIELDS).toContain("grade");
  });
});

describe("OpenAPI reconciliation — known spec gaps", () => {
  it("still does not model the §12 error envelope", () => {
    // When ErrorEnvelope lands, bind REQUIRED_ERROR_ENVELOPE_FIELDS to it
    // the way SESSION_BACKING is bound, and delete this test.
    expect(Object.keys(SPEC.components.schemas)).not.toContain("ErrorEnvelope");
  });
});
