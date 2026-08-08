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
  newReceivedState,
  parseErrorEnvelope,
  parsePortfolioSnapshot,
  parseSessionLineage,
  parseSessionResponse,
  parseTurnResponse,
  REQUIRED_ERROR_ENVELOPE_FIELDS,
  REQUIRED_EVENT_FIELDS,
  REQUIRED_SESSION_FIELDS,
  REQUIRED_TURN_RESPONSE_FIELDS,
  STABLE_ERROR_CODES,
  trackReceived,
  turnResponseToEvents,
  validateTurnEvent,
} from "@/lib/contract";
import { clarificationEvents, PR3_EVENTS } from "@/lib/mock/definitions";
import { REFERENCE_TURNS } from "@/lib/mock/reference";
import type { TurnEvent } from "@/lib/types";

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
/* SSE frames                                                          */
/* ------------------------------------------------------------------ */

/** One minimal valid fixture per SSE event kind. */
const EVENT_FIXTURES: Record<TurnEvent["type"], TurnEvent> = {
  stage: { type: "stage", stage: "executing", status: "started", probesDone: 1, probesTotal: 3 },
  context_header: {
    type: "context_header",
    turnClass: "new_investigation",
    header: {
      window: { start: "2026-07-27", end: "2026-08-02", basis: "post" },
      filters: [],
      grain: { entity: "transaction", timeBucket: "week" },
      watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
      packVersion: { packId: "base-rcm", version: "1.0.0" },
    },
  },
  finding: {
    type: "finding",
    finding: {
      referent: { value: "F1", kind: "finding" },
      title: "State Medicaid cash down",
      statement: "Posted cash fell 52.9% week over week.",
      metricRefs: ["cash_posted"],
      values: { delta_cents: -9_909_308 },
      grade: "direct",
      impactCents: -9_909_308,
      directionOfGood: "up_is_good",
      confidence: "high",
      suggestedRefinements: [],
    },
  },
  chart_spec: {
    type: "chart_spec",
    spec: {
      id: "c1",
      kind: "grouped_bar",
      title: "Cash by payer",
      unit: "cents",
      series: [{ key: "current", label: "This week", role: "current" }],
      rows: [{ label: "State Medicaid", referent: "F1", values: { current: 8_812_843 } }],
    },
  },
  narrative_delta: { type: "narrative_delta", text: "Payer cash fell " },
  clarification: {
    type: "clarification",
    clarification: { question: "Which window did you mean?", options: ["Last week", "July"] },
  },
  warning: { type: "warning", code: "PROXY_GRADE", message: "Proxy evidence only.", severity: "caution" },
  turn_complete: {
    type: "turn_complete",
    investigationId: "inv_001",
    status: "complete",
    answerGrade: "direct",
  },
  error: { type: "error", code: "QUERY_BUDGET_EXCEEDED", message: "Budget exhausted." },
  interpretation: {
    type: "interpretation",
    interpretation: {
      metric: { id: "cash_posted", name: "Cash posted (payer)", version: 3 },
      windowDescription: "last week → Jul 27–Aug 2 (post date)",
      filterDescriptions: [],
      synonymMappings: [],
    },
  },
  evidence: {
    type: "evidence",
    evidence: {
      probes: [],
      reconciliation: { status: "passed", detail: "children sum to parent" },
      zeroProbeTurn: false,
    },
  },
  definition_card: {
    type: "definition_card",
    definition: {
      term: "PR3",
      normalizedTo: "Group code PR · CARC 3",
      definition: "Amount applied as the member's copay.",
      sources: [],
      packVersion: { packId: "base-rcm", version: "1.0.0" },
      relatedConcepts: [],
    },
  },
};

describe("SSE frame contract (validateTurnEvent)", () => {
  const kinds = Object.keys(EVENT_FIXTURES) as TurnEvent["type"][];

  it("covers every event kind in the union", () => {
    expect(kinds.sort()).toEqual(Object.keys(REQUIRED_EVENT_FIELDS).sort());
  });

  for (const kind of kinds) {
    describe(kind, () => {
      it("accepts the canonical fixture", () => {
        const result = validateTurnEvent(EVENT_FIXTURES[kind]);
        expect(result.ok).toBe(true);
      });

      for (const path of REQUIRED_EVENT_FIELDS[kind]) {
        it(`fails loudly when required field "${path}" is missing`, () => {
          const broken = withoutPath(EVENT_FIXTURES[kind], path);
          const result = validateTurnEvent(broken);
          expect(result.ok).toBe(false);
          if (!result.ok) expect(result.missing).toContain(path);
        });
      }
    });
  }

  it("every reference mock event is a valid wire fixture", () => {
    const allEvents: TurnEvent[] = [
      ...REFERENCE_TURNS.flatMap((t) => t.events),
      ...PR3_EVENTS,
      ...clarificationEvents("unscripted question", "Why did cash decline last week?"),
    ];
    expect(allEvents.length).toBeGreaterThan(50);
    for (const event of allEvents) {
      const result = validateTurnEvent(event);
      expect(result.ok, `mock event ${event.type} must satisfy the contract`).toBe(true);
    }
  });

  it("reports indexed paths for broken chart series/rows", () => {
    const fixture = structuredClone(EVENT_FIXTURES.chart_spec);
    if (fixture.type !== "chart_spec") throw new Error("unreachable");
    delete (fixture.spec.series[0] as unknown as Record<string, unknown>).role;
    delete (fixture.spec.rows[0] as unknown as Record<string, unknown>).values;
    const result = validateTurnEvent(fixture);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.missing).toContain("spec.series[0].role");
      expect(result.missing).toContain("spec.rows[0].values");
    }
  });

  it("rejects an unknown turn_complete status (a fabricated 'complete' is worse than loud drift)", () => {
    const broken = structuredClone(EVENT_FIXTURES.turn_complete) as unknown as Record<
      string,
      unknown
    >;
    broken.status = "done";
    const result = validateTurnEvent(broken as unknown as TurnEvent);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.missing).toContain("status");
  });

  it("defaults omitted containers instead of flagging drift (tolerant reads)", () => {
    const finding = withoutPath(
      withoutPath(EVENT_FIXTURES.finding, "finding.suggestedRefinements"),
      "finding.metricRefs",
    );
    const result = validateTurnEvent(finding);
    expect(result.ok).toBe(true);
    if (result.ok && result.value.type === "finding") {
      expect(result.value.finding.suggestedRefinements).toEqual([]);
      expect(result.value.finding.metricRefs).toEqual([]);
    }
  });

  it("ignores unknown extra fields (forward compatibility)", () => {
    const extended = {
      ...EVENT_FIXTURES.turn_complete,
      serverOnlyField: { anything: true },
    } as unknown as TurnEvent;
    expect(validateTurnEvent(extended).ok).toBe(true);
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
/* TurnResponse (turn_complete payload / blocking JSON / GET recovery) */
/* ------------------------------------------------------------------ */

function turnResponseFixture(): Record<string, unknown> {
  const header = EVENT_FIXTURES.context_header;
  const finding = EVENT_FIXTURES.finding;
  const chart = EVENT_FIXTURES.chart_spec;
  const evidence = EVENT_FIXTURES.evidence;
  if (
    header.type !== "context_header" ||
    finding.type !== "finding" ||
    chart.type !== "chart_spec" ||
    evidence.type !== "evidence"
  ) {
    throw new Error("unreachable");
  }
  return structuredClone({
    investigationId: "inv_001",
    status: "complete",
    turnClass: "new_investigation",
    header: header.header,
    findings: [finding.finding],
    charts: [chart.spec],
    narrative: "Payer cash fell 12.7% week over week.",
    warnings: [{ code: "PROXY_GRADE", message: "Proxy evidence only.", severity: "caution" }],
    evidence: evidence.evidence,
    answerGrade: "direct",
  });
}

describe("TurnResponse contract (parseTurnResponse)", () => {
  it("parses the full response with zero drift", () => {
    const { value, drift } = parseTurnResponse(turnResponseFixture());
    expect(drift).toEqual([]);
    expect(value?.investigationId).toBe("inv_001");
    expect(value?.findings).toHaveLength(1);
    expect(value?.charts).toHaveLength(1);
  });

  for (const path of REQUIRED_TURN_RESPONSE_FIELDS) {
    it(`is fatal when core field "${path}" is missing`, () => {
      const { value, drift } = parseTurnResponse(withoutPath(turnResponseFixture(), path));
      expect(value).toBeNull();
      expect(drift).toContain(path);
    });
  }

  it("tolerates snake_case aliases for top-level fields", () => {
    const fixture = turnResponseFixture();
    fixture.investigation_id = fixture.investigationId;
    delete fixture.investigationId;
    fixture.answer_grade = fixture.answerGrade;
    delete fixture.answerGrade;
    const { value, drift } = parseTurnResponse(fixture);
    expect(drift).toEqual([]);
    expect(value?.investigationId).toBe("inv_001");
    expect(value?.answerGrade).toBe("direct");
  });

  it("drops a broken optional sub-shape with indexed drift, keeps the rest", () => {
    const fixture = withoutPath(turnResponseFixture(), "findings.0.title");
    const { value, drift } = parseTurnResponse(fixture);
    expect(value).not.toBeNull();
    expect(value?.findings).toEqual([]);
    expect(drift).toContain("findings[0].finding.title");
    expect(value?.charts).toHaveLength(1);
  });

  it("replays a fresh response as a full event stream ending in turn_complete", () => {
    const { value } = parseTurnResponse(turnResponseFixture());
    const events = turnResponseToEvents(value!);
    expect(events.map((e) => e.type)).toEqual([
      "context_header",
      "stage",
      "finding",
      "chart_spec",
      "warning",
      "narrative_delta",
      "evidence",
      "turn_complete",
    ]);
    for (const event of events) {
      expect(validateTurnEvent(event).ok).toBe(true);
    }
  });

  it("skips deltas already received on the live stream (duplicate-free recovery)", () => {
    const { value } = parseTurnResponse(turnResponseFixture());
    const received = newReceivedState();
    const finding = EVENT_FIXTURES.finding;
    if (finding.type !== "finding") throw new Error("unreachable");
    trackReceived(received, EVENT_FIXTURES.context_header);
    trackReceived(received, finding);
    trackReceived(received, { type: "narrative_delta", text: "Payer cash fell " });

    const events = turnResponseToEvents(value!, received);
    expect(events.some((e) => e.type === "context_header")).toBe(false);
    expect(events.some((e) => e.type === "finding")).toBe(false);
    const delta = events.find((e) => e.type === "narrative_delta");
    expect(delta && delta.type === "narrative_delta" ? delta.text : "").toBe(
      "12.7% week over week.",
    );
    expect(events[events.length - 1]?.type).toBe("turn_complete");
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
