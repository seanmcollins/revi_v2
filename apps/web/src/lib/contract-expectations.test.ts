/**
 * Contract-drift guard.
 *
 * This suite pins the REQUIRED field paths of the wire contract — the SSE
 * TurnEvent frames, the blocking/recovered TurnResponse, SessionResponse,
 * ErrorEnvelope, lineage and portfolio payloads — as parse functions run
 * against fixtures. The reference mock streams double as canonical fixtures,
 * so the mock and the API cannot drift apart silently.
 *
 * The reconciliation against `contracts/openapi.json` (via the generated
 * `lib/types.gen.ts`) lives in its own suite — `contract-openapi.test.ts` —
 * which binds these tables to the published wire types at both compile
 * time and run time, and records the known spec gaps. Hand-written types in
 * lib/types.ts stay authoritative for UI-only shapes.
 */

import { describe, expect, it } from "vitest";

import {
  mapAnswerWarning,
  newReceivedState,
  parseErrorEnvelope,
  parseInvestigationResponse,
  parsePortfolioSnapshot,
  parseSessionLineage,
  parseSessionResponse,
  parseTurnFrame,
  parseTurnResponse,
  refinementsToWire,
  REQUIRED_ANSWER_FIELDS,
  REQUIRED_CLARIFICATION_FIELDS,
  REQUIRED_ERROR_ENVELOPE_FIELDS,
  REQUIRED_FRAME_FIELDS,
  REQUIRED_INVESTIGATION_FIELDS,
  REQUIRED_SESSION_FIELDS,
  STABLE_ERROR_CODES,
  trackReceived,
  turnResponseToEvents,
  type TurnFrameKind,
  type WirePin,
} from "@/lib/contract";
import type { Refinement } from "@/lib/types";

/* eslint-disable @typescript-eslint/no-explicit-any */
import RAW_SAMPLES from "@/lib/__fixtures__/wire-samples.json";

/** Captured live server output — see the note above FRAME_FIXTURES. */
const SAMPLES = RAW_SAMPLES as any;

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

/** Deep-clone a value and delete one dotted path from it. */
function withoutPath<T>(value: T, path: string): T {
  const clone = structuredClone(value) as unknown;
  const keys = path.split(".");
  let cursor: unknown = clone;
  for (const key of keys.slice(0, -1)) {
    cursor = (cursor as Record<string, unknown>)[key];
  }
  delete (cursor as Record<string, unknown>)[keys[keys.length - 1]];
  return clone as T;
}

/* ------------------------------------------------------------------ */
/* SSE frames — parsed from REAL server output                         */
/* ------------------------------------------------------------------ */

/**
 * `__fixtures__/wire-samples.json` is not hand-written. It is captured
 * verbatim from a live `REVI_LLM_MOCK=1 uvicorn revi_api.main:app` run —
 * the T1 reference turn, a portfolio card and its cold-start drill, a
 * clarification, a definitional answer, and a `GET /v1/investigations/{id}`
 * body. That is deliberate: the previous fixtures were written in the UI's
 * own vocabulary, so they agreed with the parser and disagreed with the
 * server, and the suite was green while every live frame was drift.
 */
const PIN: WirePin = {
  watermark: {
    id: SAMPLES.session.watermark_id,
    loadedAt: "2026-08-03 04:10",
    newestDataDate: SAMPLES.session.newest_data_date,
  },
  pack: { packId: SAMPLES.session.pack_id, version: SAMPLES.session.pack_version },
};

/** One real payload per published frame kind (turn_complete has its own suite). */
const FRAME_FIXTURES: Record<Exclude<TurnFrameKind, "turn_complete">, Record<string, unknown>> = {
  stage: SAMPLES.stage,
  context_header: SAMPLES.context_header,
  finding: SAMPLES.finding,
  chart_spec: SAMPLES.chart_spec,
  narrative_delta: SAMPLES.narrative_delta,
  clarification: { question: "Which window did you mean?", options: [], reason: "ambiguous" },
  warning: { code: "WATERMARK_STALE", pinned: "wm_003", newest: "wm_004" },
  error: {
    code: "QUERY_BUDGET_EXCEEDED",
    message: "Query budget exhausted for this turn.",
    correlation_id: "corr_123",
  },
};

describe("SSE frame contract (parseTurnFrame)", () => {
  const kinds = Object.keys(FRAME_FIXTURES) as (keyof typeof FRAME_FIXTURES)[];

  it("covers every published frame kind except turn_complete", () => {
    expect([...kinds, "turn_complete"].sort()).toEqual(Object.keys(REQUIRED_FRAME_FIELDS).sort());
  });

  for (const kind of kinds) {
    describe(kind, () => {
      it("accepts the real server payload", () => {
        const result = parseTurnFrame(kind, FRAME_FIXTURES[kind], PIN);
        expect(result, `${kind} must be a published kind`).not.toBeNull();
        expect(result?.ok).toBe(true);
      });

      for (const path of REQUIRED_FRAME_FIELDS[kind]) {
        it(`fails loudly when required field "${path}" is missing`, () => {
          const broken = withoutPath(FRAME_FIXTURES[kind], path);
          const result = parseTurnFrame(kind, broken, PIN);
          expect(result?.ok).toBe(false);
          if (result && !result.ok) expect(result.missing).toContain(path);
        });
      }
    });
  }

  it("skips an unpublished frame kind instead of failing (forward compatibility)", () => {
    expect(parseTurnFrame("something_new", { anything: true }, PIN)).toBeNull();
  });

  it("skips an engine stage the rail has no slot for, without calling it drift", () => {
    expect(parseTurnFrame("stage", { stage: "quantum_leap" }, PIN)).toBeNull();
  });

  it("maps every engine stage the server actually emits onto the rail", () => {
    // The eight the engine publishes today, plus the refinement pair.
    const emitted = [
      "classify",
      "interpret",
      "plan",
      "validate",
      "execute",
      "calculate",
      "findings",
      "resolve_referents",
      "emit_refinements",
    ];
    for (const stage of emitted) {
      const result = parseTurnFrame("stage", { stage }, PIN);
      expect(result, `engine stage "${stage}" must reach the rail`).not.toBeNull();
      expect(result?.ok).toBe(true);
    }
  });

  it("maps a finding payload into the UI shape without inventing anything", () => {
    const result = parseTurnFrame("finding", SAMPLES.finding, PIN);
    expect(result?.ok).toBe(true);
    if (!result?.ok || result.value.type !== "finding") throw new Error("unreachable");
    const finding = result.value.finding;
    expect(finding.referent).toEqual({ value: "F1", kind: "finding" });
    expect(finding.metricRefs).toEqual(["cash_posted"]);
    expect(finding.values.delta_cents).toBe(-9_909_308);
    expect(finding.impactCents).toBe(-9_909_308);
    expect(finding.impactKind).toBe("delta");
    // direction-of-good lives on the metric contract, not on the payload:
    // withheld, never guessed from the sign
    expect(finding.directionOfGood).toBe("neutral");
    expect(finding.suggestedRefinements).toEqual([
      { label: "drill into F1", refinement: { op: "DrillInto", target: "F1" } },
    ]);
  });

  it("folds long-format chart rows into keyed series rows", () => {
    const result = parseTurnFrame("chart_spec", SAMPLES.chart_spec, PIN);
    expect(result?.ok).toBe(true);
    if (!result?.ok || result.value.type !== "chart_spec") throw new Error("unreachable");
    const spec = result.value.spec;
    expect(spec.id).toBe(SAMPLES.chart_spec.id);
    expect(spec.unit).toBe("cents"); // money_cents → the formatter's unit
    expect(spec.series).toHaveLength(1);
    expect(spec.rows.length).toBeGreaterThan(0);
    expect(spec.rows[0].values[spec.series[0].key]).toBeTypeOf("number");
    expect(spec.rows[0].referent).toMatch(/^D\d+$/); // clicks compile to DrillInto
    // the published type is kept even where the UI reduces it to bars
    expect(spec.wireChartType).toBe(SAMPLES.chart_spec.chart_type);
  });

  it("completes the header from the session pin, keeping the header's own watermark id", () => {
    const result = parseTurnFrame("context_header", SAMPLES.context_header, PIN);
    expect(result?.ok).toBe(true);
    if (!result?.ok || result.value.type !== "context_header") throw new Error("unreachable");
    const header = result.value.header;
    expect(header.window).toEqual({ start: "2026-07-27", end: "2026-08-02", basis: "post" });
    expect(header.comparison?.kind).toBe("prior_period");
    expect(header.watermark).toEqual(PIN.watermark);
    expect(header.packVersion).toEqual(PIN.pack);
  });

  it("carries filter chips through with their origin turn", () => {
    const result = parseTurnFrame(
      "context_header",
      SAMPLES.drill_turn_complete.context_header as Record<string, unknown>,
      PIN,
    );
    expect(result?.ok).toBe(true);
    if (!result?.ok || result.value.type !== "context_header") throw new Error("unreachable");
    const filters = result.value.header.filters;
    expect(filters.map((f) => f.dimension).sort()).toEqual(["payer", "service_line"]);
    for (const filter of filters) {
      expect(filter.op).toBe("eq");
      expect(filter.values).toHaveLength(1);
      expect(filter.originTurn).toMatch(/^turn_/);
    }
  });

  it("writes a sentence for the warning codes that carry structured detail", () => {
    const stale = parseTurnFrame("warning", FRAME_FIXTURES.warning, PIN);
    if (!stale?.ok || stale.value.type !== "warning") throw new Error("unreachable");
    expect(stale.value.message).toContain("wm_004");
    expect(stale.value.message).toContain("wm_003");
    expect(stale.value.severity).toBe("caution");

    const recon = parseTurnFrame(
      "warning",
      { code: "RECONCILIATION_FAILED", detail: "children sum short by 4c" },
      PIN,
    );
    if (!recon?.ok || recon.value.type !== "warning") throw new Error("unreachable");
    expect(recon.value.message).toContain("children sum short by 4c");
  });

  it("labels a free-text answer warning as a note rather than faking a code", () => {
    expect(mapAnswerWarning("suppression: small cells are suppressed")).toEqual({
      code: "ANSWER_NOTE",
      message: "suppression: small cells are suppressed",
      severity: "info",
    });
    expect(mapAnswerWarning("RECONCILIATION_FAILED: children sum short")).toEqual({
      code: "RECONCILIATION_FAILED",
      message: "children sum short",
      severity: "caution",
    });
  });

  it("ignores unknown extra fields (forward compatibility)", () => {
    const extended = { ...SAMPLES.finding, serverOnlyField: { anything: true } };
    expect(parseTurnFrame("finding", extended, PIN)?.ok).toBe(true);
  });
});

/* ------------------------------------------------------------------ */
/* Typed gestures: UI refinement → published operator                  */
/* ------------------------------------------------------------------ */

describe("refinement translation (refinementsToWire)", () => {
  it("emits the published `op` spelling, not the UI's", () => {
    expect(
      refinementsToWire([
        { op: "SetDimensions", dimensions: ["payer"] },
        {
          op: "AddFilter",
          filter: {
            dimension: "payer",
            dimensionLabel: "Payer",
            op: "eq",
            values: ["Federal Medicare"],
          },
        },
        { op: "RemoveFilter", dimension: "service_line" },
        { op: "RankBy", metric: "denied_dollars", descending: true },
        { op: "ResetContext", keepPins: true },
      ]),
    ).toEqual([
      { op: "set_dimensions", dimensions: ["payer"] },
      {
        op: "add_filter",
        dimension: "payer",
        predicate_op: "eq",
        values: ["Federal Medicare"],
      },
      { op: "remove_filter", dimension: "service_line" },
      { op: "rank_by", by: "denied_dollars", descending: true },
      { op: "reset_context", keep_pins: true },
    ]);
  });

  it("expands a multi-target drill into one operator per target", () => {
    expect(refinementsToWire([{ op: "DrillInto", target: ["F1", "F2", "F3"] }])).toEqual([
      { op: "drill_into", target: "F1" },
      { op: "drill_into", target: "F2" },
      { op: "drill_into", target: "F3" },
    ]);
  });

  it("produces bodies the published TurnRequest accepts", () => {
    // Every emitted `op` must be one of the closed twelve the spec names.
    const published = new Set([
      "set_dimensions",
      "add_filter",
      "remove_filter",
      "set_window",
      "set_comparison",
      "set_grain",
      "drill_into",
      "pivot",
      "explain",
      "rank_by",
      "expand",
      "reset_context",
    ]);
    const every: Refinement[] = [
      { op: "SetDimensions", dimensions: [] },
      {
        op: "AddFilter",
        filter: { dimension: "payer", dimensionLabel: "Payer", op: "in", values: ["a"] },
      },
      { op: "RemoveFilter", dimension: "payer" },
      { op: "SetWindow", window: { start: "2026-01-01", end: "2026-03-31", basis: "service" } },
      { op: "SetComparison", comparison: null },
      { op: "SetGrain", grain: { entity: "denial" } },
      { op: "DrillInto", target: "F1" },
      { op: "Pivot", measures: ["denied_dollars"] },
      { op: "Explain", target: "F1" },
      { op: "RankBy", metric: "denied_dollars", descending: false },
      { op: "Expand" },
      { op: "ResetContext", keepPins: false },
    ];
    const wire = refinementsToWire(every);
    expect(wire).toHaveLength(every.length);
    for (const operator of wire) expect(published).toContain(operator.op);
  });
});

/* ------------------------------------------------------------------ */
/* SessionResponse (snake_case wire)                                   */
/* ------------------------------------------------------------------ */

const SESSION_FIXTURE = {
  session_id: "sess_a1b2",
  watermark: {
    id: "wm_003",
    loaded_at: "2026-08-03 04:10",
    newest_data_date: "2026-08-02",
  },
  pack: { pack_id: "base-rcm", version: "1.0.0" },
};

describe("SessionResponse contract (parseSessionResponse)", () => {
  it("maps the snake_case wire to camelCase UI shapes", () => {
    const result = parseSessionResponse(SESSION_FIXTURE);
    expect(result).toEqual({
      ok: true,
      value: {
        sessionId: "sess_a1b2",
        watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
        pack: { packId: "base-rcm", version: "1.0.0" },
      },
    });
  });

  for (const path of REQUIRED_SESSION_FIELDS) {
    it(`fails loudly when required field "${path}" is missing`, () => {
      const result = parseSessionResponse(withoutPath(SESSION_FIXTURE, path));
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.missing).toContain(path);
    });
  }

  it("tolerates the live server's flat spelling and normalizes the ISO timestamp", () => {
    const result = parseSessionResponse({
      session_id: "sess_flat",
      tenant: "demo",
      pack_id: "base-rcm",
      pack_version: "1.0.0",
      watermark_id: "wm_003",
      watermark_loaded_at: "2026-08-03T04:10:00",
      newest_data_date: "2026-08-02",
      epoch: 0,
    });
    expect(result).toEqual({
      ok: true,
      value: {
        sessionId: "sess_flat",
        watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
        pack: { packId: "base-rcm", version: "1.0.0" },
      },
    });
  });
});

/* ------------------------------------------------------------------ */
/* TurnResponse — the discriminated 200 body                           */
/* ------------------------------------------------------------------ */

/**
 * The published 200 body is `TurnAnswer | TurnClarification | TurnError`
 * discriminated on `outcome`. M13 pinned the fact that the parser required
 * a `status` field none of the three carries; these fixtures are the real
 * bodies, so the parser is now tested against what the server sends rather
 * than against what an earlier milestone assumed it would.
 */
describe("TurnResponse contract (parseTurnResponse)", () => {
  it("parses a real answer body with zero drift", () => {
    const { value, drift } = parseTurnResponse(SAMPLES.turn_complete, PIN);
    expect(drift).toEqual([]);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    expect(value.investigationId).toBe(SAMPLES.turn_complete.investigation_id);
    expect(value.turnClass).toBe("new_investigation");
    expect(value.findings.map((f) => f.referent.value)).toEqual(["F1", "F2", "F3"]);
    expect(value.charts.length).toBeGreaterThan(0);
    expect(value.header?.watermark.id).toBe("wm_003");
    expect(value.narrative).toContain("F1");
  });

  for (const path of REQUIRED_ANSWER_FIELDS) {
    it(`is fatal when the answer is missing "${path}"`, () => {
      const { value, drift } = parseTurnResponse(withoutPath(SAMPLES.turn_complete, path), PIN);
      expect(value).toBeNull();
      expect(drift).toContain(path);
    });
  }

  it("parses a clarification as its own outcome, not an empty answer", () => {
    const { value, drift } = parseTurnResponse(SAMPLES.clarification_body, PIN);
    expect(drift).toEqual([]);
    if (value?.outcome !== "clarification_required") throw new Error("expected a clarification");
    expect(value.clarification.question).toBe(SAMPLES.clarification_body.question);
    expect(value.investigationId).toBe(SAMPLES.clarification_body.investigation_id);
  });

  for (const path of REQUIRED_CLARIFICATION_FIELDS) {
    it(`is fatal when the clarification is missing "${path}"`, () => {
      const { value, drift } = parseTurnResponse(
        withoutPath(SAMPLES.clarification_body, path),
        PIN,
      );
      expect(value).toBeNull();
      expect(drift).toContain(path);
    });
  }

  it("parses a turn error, which carries an envelope and no investigation id", () => {
    const body = {
      outcome: "error",
      session_id: "sess_1",
      error: {
        code: "REFERENT_NOT_FOUND",
        message: "referent 'F9' is not in the live registry",
        correlation_id: "corr_9",
      },
    };
    const { value, drift } = parseTurnResponse(body, PIN);
    expect(drift).toEqual([]);
    if (value?.outcome !== "error") throw new Error("expected an error");
    expect(value.error.code).toBe("REFERENT_NOT_FOUND");
    expect(value.error.correlationId).toBe("corr_9");
  });

  it("reports the envelope's own missing paths when a turn error is malformed", () => {
    const { value, drift } = parseTurnResponse(
      { outcome: "error", error: { code: "X" } },
      PIN,
    );
    expect(value).toBeNull();
    expect(drift).toEqual(["error.message", "error.correlation_id"]);
  });

  it("refuses an unknown outcome rather than guessing which variant it is", () => {
    const { value, drift } = parseTurnResponse({ outcome: "maybe" }, PIN);
    expect(value).toBeNull();
    expect(drift).toContain("outcome");
  });

  it("carries a definitional answer through to the definition card", () => {
    const { value, drift } = parseTurnResponse(SAMPLES.definitional_body, PIN);
    expect(drift).toEqual([]);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    expect(value.turnClass).toBe("definitional");
    expect(value.definition?.definition).not.toBe("");
    expect(value.definition?.packVersion.packId).toBe("base-rcm");
  });

  it("drops a broken finding with indexed drift and keeps the rest of the answer", () => {
    const broken = structuredClone(SAMPLES.turn_complete);
    delete (broken.findings[0] as Record<string, unknown>).referent;
    const { value, drift } = parseTurnResponse(broken, PIN);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    expect(drift).toContain("findings[0].referent");
    expect(value.findings).toHaveLength(2);
    expect(value.charts.length).toBeGreaterThan(0);
  });

  it("replays a fresh answer as a full event stream ending in turn_complete", () => {
    const { value } = parseTurnResponse(SAMPLES.turn_complete, PIN);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    const events = turnResponseToEvents(value);
    // The real T1 answer carries five honest-limitation warnings (a pruned
    // probe, two alternate-basis labels, suppression, a skipped transform),
    // and every one of them reaches the UI as a note rather than vanishing.
    expect(events.map((e) => e.type)).toEqual([
      "context_header",
      "stage",
      "finding",
      "finding",
      "finding",
      "chart_spec",
      "warning",
      "warning",
      "warning",
      "warning",
      "warning",
      "narrative_delta",
      "turn_complete",
    ]);
    expect(events.filter((e) => e.type === "warning").map((e) => e.code)).toEqual(
      Array(5).fill("ANSWER_NOTE"),
    );
  });

  it("replays a clarification as a clarification, never as a complete answer", () => {
    const { value } = parseTurnResponse(SAMPLES.clarification_body, PIN);
    if (value?.outcome !== "clarification_required") throw new Error("unreachable");
    const events = turnResponseToEvents(value);
    expect(events.map((e) => e.type)).toEqual(["stage", "clarification", "turn_complete"]);
    const complete = events[events.length - 1];
    expect(complete.type === "turn_complete" && complete.status).toBe("clarification_required");
  });

  it("skips deltas already received on the live stream (duplicate-free recovery)", () => {
    const { value } = parseTurnResponse(SAMPLES.turn_complete, PIN);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    const received = newReceivedState();
    trackReceived(received, { type: "context_header", header: value.header!, turnClass: "refinement" });
    trackReceived(received, { type: "finding", finding: value.findings[0] });
    trackReceived(received, { type: "narrative_delta", text: value.narrative!.slice(0, 10) });

    const events = turnResponseToEvents(value, received);
    expect(events.some((e) => e.type === "context_header")).toBe(false);
    expect(events.filter((e) => e.type === "finding")).toHaveLength(2);
    const delta = events.find((e) => e.type === "narrative_delta");
    expect(delta && delta.type === "narrative_delta" ? delta.text : "").toBe(
      value.narrative!.slice(10),
    );
    expect(events[events.length - 1]?.type).toBe("turn_complete");
  });
});

/* ------------------------------------------------------------------ */
/* GET /v1/investigations/{iid} — the recovery shape                   */
/* ------------------------------------------------------------------ */

describe("InvestigationResponse contract (parseInvestigationResponse)", () => {
  it("recovers a completed turn's findings from the by-id route", () => {
    const { value, drift } = parseInvestigationResponse(SAMPLES.investigation);
    expect(drift).toEqual([]);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    expect(value.investigationId).toBe(SAMPLES.investigation.investigation_id);
    expect(value.findings.map((f) => f.referent.value)).toEqual(["F1"]);
    // This route keeps findings and warnings, not charts or a header — a
    // recovered turn renders what the server actually stored.
    expect(value.charts).toEqual([]);
    expect(value.header).toBeUndefined();
  });

  for (const path of REQUIRED_INVESTIGATION_FIELDS) {
    it(`is fatal when "${path}" is missing`, () => {
      const { value, drift } = parseInvestigationResponse(
        withoutPath(SAMPLES.investigation, path),
      );
      expect(value).toBeNull();
      expect(drift).toContain(path);
    });
  }

  it("rejects an unknown status (a fabricated 'complete' is worse than loud drift)", () => {
    const { value, drift } = parseInvestigationResponse({
      ...SAMPLES.investigation,
      status: "done",
    });
    expect(value).toBeNull();
    expect(drift).toContain("status");
  });

  it("recovers a failed turn as an error outcome", () => {
    const { value } = parseInvestigationResponse({
      ...SAMPLES.investigation,
      status: "failed",
      warnings: ["the source went away"],
    });
    if (value?.outcome !== "error") throw new Error("expected an error");
    expect(value.error.message).toContain("the source went away");
  });
});

/* ------------------------------------------------------------------ */
/* ErrorEnvelope + stable codes                                        */
/* ------------------------------------------------------------------ */

describe("ErrorEnvelope contract (parseErrorEnvelope)", () => {
  const ENVELOPE = {
    code: "QUERY_BUDGET_EXCEEDED",
    message: "Query budget exhausted for this turn.",
    correlation_id: "corr_123",
  };

  it("pins exactly the 15 stable error codes", () => {
    expect(STABLE_ERROR_CODES.size).toBe(15);
    for (const code of [
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
    ]) {
      expect(STABLE_ERROR_CODES.has(code), code).toBe(true);
    }
  });

  it("parses a complete envelope", () => {
    expect(parseErrorEnvelope(ENVELOPE)).toEqual({
      ok: true,
      value: {
        code: "QUERY_BUDGET_EXCEEDED",
        message: "Query budget exhausted for this turn.",
        correlationId: "corr_123",
      },
    });
  });

  for (const path of REQUIRED_ERROR_ENVELOPE_FIELDS) {
    it(`fails loudly when "${path}" is missing`, () => {
      const result = parseErrorEnvelope(withoutPath(ENVELOPE, path));
      expect(result.ok).toBe(false);
      if (!result.ok) expect(result.missing).toContain(path);
    });
  }
});

/* ------------------------------------------------------------------ */
/* Lineage + portfolio                                                 */
/* ------------------------------------------------------------------ */

describe("SessionLineage contract (parseSessionLineage)", () => {
  const LINEAGE = {
    nodes: [
      {
        turnId: "t1",
        investigationId: "inv_001",
        turnClass: "new_investigation",
        label: "T1",
        question: "Why did cash decline last week?",
      },
      {
        turnId: "t2",
        investigationId: "inv_002",
        turnClass: "refinement",
        label: "T2",
        question: "Break that down by payer",
      },
    ],
    edges: [{ parentTurnId: "t1", childTurnId: "t2", operators: ["SetDimensions(payer)"] }],
  };

  it("parses a valid DAG", () => {
    const { value, drift } = parseSessionLineage(LINEAGE);
    expect(drift).toEqual([]);
    expect(value?.nodes).toHaveLength(2);
    expect(value?.edges[0]?.operators).toEqual(["SetDimensions(payer)"]);
  });

  it("drops a broken node with an indexed drift path", () => {
    const { value, drift } = parseSessionLineage(withoutPath(LINEAGE, "nodes.1.investigationId"));
    expect(value?.nodes).toHaveLength(1);
    expect(drift).toContain("nodes[1].investigationId");
  });

  it("defaults missing edge operators to []", () => {
    const { value } = parseSessionLineage(withoutPath(LINEAGE, "edges.0.operators"));
    expect(value?.edges[0]?.operators).toEqual([]);
  });

  /* Spec alignment — see contract-openapi.test.ts for the full reconciliation. */

  it("keeps a node whose question is absent — gesture turns have none", () => {
    const { value, drift } = parseSessionLineage(withoutPath(LINEAGE, "nodes.1.question"));
    expect(drift).toEqual([]);
    expect(value?.nodes).toHaveLength(2);
    expect(value?.nodes[1]?.question).toBe("");
  });

  it("derives the display label from the question, then the turn class", () => {
    const noLabel = withoutPath(LINEAGE, "nodes.1.label");
    expect(parseSessionLineage(noLabel).value?.nodes[1]?.label).toBe("Break that down by payer");

    const bare = withoutPath(noLabel, "nodes.1.question");
    expect(parseSessionLineage(bare).value?.nodes[1]?.label).toBe("refinement");
  });

  it("accepts the OpenAPI spelling: investigations[] with snake_case ids", () => {
    const { value, drift } = parseSessionLineage({
      investigations: [
        {
          turn_id: "t1",
          investigation_id: "inv_001",
          turn_class: "new_investigation",
          question: "Why did cash decline last week?",
        },
      ],
      edges: [{ parent_id: "t1", child_id: "t2" }],
    });
    expect(drift).toEqual([]);
    expect(value?.nodes[0]?.turnId).toBe("t1");
    expect(value?.nodes[0]?.label).toBe("Why did cash decline last week?");
    expect(value?.edges[0]).toEqual({
      parentTurnId: "t1",
      childTurnId: "t2",
      operators: [],
    });
  });
});

describe("Portfolio contract (parsePortfolioSnapshot)", () => {
  const SNAPSHOT = {
    items: [
      {
        rank: 1,
        referent: "P1",
        title: "Timely filing risk",
        issueClass: "timely_filing_watch",
        impactCents: 117_141_515,
        impactLabel: "billed at risk",
        detail: "414 July claims unsubmitted.",
        provenance: "external_detection",
        priorityFormulaVersion: "dollar_impact@1",
        sourceWatermarkId: "wm_003",
        drill: { label: "Drill in", refinement: { op: "DrillInto", target: "P1" } },
      },
    ],
    watermark: "2026-08-03 04:10",
    rankingPolicy: "dollar_impact@1",
  };

  it("parses a valid snapshot", () => {
    const { value, drift } = parsePortfolioSnapshot(SNAPSHOT);
    expect(drift).toEqual([]);
    expect(value?.items[0]?.referent).toBe("P1");
    expect(value?.rankingPolicy).toBe("dollar_impact@1");
  });

  it("drops an item missing its impact with an indexed drift path", () => {
    const { value, drift } = parsePortfolioSnapshot(withoutPath(SNAPSHOT, "items.0.impactCents"));
    expect(value?.items).toEqual([]);
    expect(drift).toContain("items[0].impactCents");
  });

  it("synthesizes a DrillInto when the drill action is omitted", () => {
    const { value } = parsePortfolioSnapshot(withoutPath(SNAPSHOT, "items.0.drill"));
    expect(value?.items[0]?.drill.refinement).toEqual({ op: "DrillInto", target: "P1" });
  });

  it("accepts the OpenAPI AnomalyCard spelling", () => {
    const { value, drift } = parsePortfolioSnapshot({
      items: [
        {
          anomaly_id: "A1",
          title: "Timely filing risk",
          category: "timely_filing_watch",
          impact_cents: 117_141_515,
          description: "414 July claims unsubmitted.",
          provenance: "external_detection",
          priority_formula_version: "dollar_impact@1",
          source_watermark_id: "wm_003",
        },
      ],
      watermark_id: "wm_003",
      formula_version: "dollar_impact@1",
    });
    expect(drift).toEqual([]);
    expect(value?.items[0]).toMatchObject({
      rank: 1,
      referent: "A1",
      issueClass: "timely_filing_watch",
      impactCents: 117_141_515,
      detail: "414 July claims unsubmitted.",
      provenance: "external_detection",
      priorityFormulaVersion: "dollar_impact@1",
      sourceWatermarkId: "wm_003",
    });
    expect(value?.watermark).toBe("wm_003");
    expect(value?.rankingPolicy).toBe("dollar_impact@1");
  });

  it("reports a card that declares no provenance rather than inventing one", () => {
    // BEHAVIOUR CHANGE: this used to assert drift on a missing `grade`. The
    // server settled that argument — AnomalyCard publishes no grade by design
    // (grading an external detection would invent provenance) and now carries
    // `provenance` instead, which IS required. A card that declares neither
    // still trips the visible drift banner rather than being drawn unlabelled.
    // See contract-openapi.test.ts.
    const { value, drift } = parsePortfolioSnapshot({
      items: [{ anomaly_id: "A1", title: "T", impact_cents: 1 }],
    });
    expect(value?.items).toEqual([]);
    expect(drift).toContain("items[0].provenance");
  });

  it("degrades the badge annotations to empty instead of dropping the card", () => {
    // priority_formula_version / source_watermark_id are spec-required but
    // only annotate the DETECTION badge tooltip — a server that omits them
    // should cost the tooltip, not the card.
    const { value, drift } = parsePortfolioSnapshot({
      items: [
        { anomaly_id: "A1", title: "T", impact_cents: 1, provenance: "external_detection" },
      ],
    });
    expect(drift).toEqual([]);
    expect(value?.items[0]).toMatchObject({
      referent: "A1",
      provenance: "external_detection",
      priorityFormulaVersion: "",
      sourceWatermarkId: "",
    });
  });
});
