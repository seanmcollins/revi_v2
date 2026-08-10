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
  impactWithheldReason,
  mapAnswerWarning,
  mapChartSpec,
  mapFinding,
  newReceivedState,
  parseErrorEnvelope,
  parseInvestigationResponse,
  parsePortfolioSnapshot,
  parseSessionLineage,
  parseSessionResponse,
  parseTurnFrame,
  parseTurnResponse,
  refinementsToWire,
  selectRenderableCharts,
  unitsFromChartSpecs,
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
import { formatMeasure } from "@/lib/format";
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
    expect(value.definition?.packVersion.packId).toBe("base-rcm");
    // "What is PR3?" answers with TWO code terms — a group code and a
    // CARC — and the pair IS the answer. They used to be flattened into
    // one prose blob, leaving the card's own group-code and CARC slots
    // permanently empty; `TermPayload.kind` says which is which.
    expect(value.definition?.groupCode).toMatchObject({ code: "PR" });
    expect(value.definition?.groupCode?.meaning).toContain("owed by the patient");
    expect(value.definition?.carc).toMatchObject({ code: 3, category: "Copay" });
    expect(value.definition?.normalizedTo).toBe("PR — Patient Responsibility · 3 — Copay");
    // A CARC number is not a "related concept"; it is half the answer.
    expect(value.definition?.relatedConcepts).toEqual([]);
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
    // The real T1 answer carries five honest-limitation sentences (a pruned
    // probe, TWO alternate-basis labels, suppression, a skipped transform),
    // and every one of them reaches the UI as a note rather than vanishing.
    //
    // Four banners, not five. The two alternate-basis sentences are one
    // fact spelled twice — they differ only by `probe
    // 'submission_volume_by_payer'` and `…__prior`, which is a plan node
    // the analyst has never seen — so they collapse onto the fact and the
    // count says it was raised twice. Nothing is dropped: the probe names
    // ride on `probes` for debug mode and the badge's tooltip.
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
      "narrative_delta",
      "evidence",
      "turn_complete",
    ]);
    expect(events.filter((e) => e.type === "warning").map((e) => e.code)).toEqual(
      Array(4).fill("ANSWER_NOTE"),
    );
    const alternate = events.find(
      (e) => e.type === "warning" && e.message.startsWith("alternate_basis_used"),
    );
    if (alternate?.type !== "warning") throw new Error("expected the alternate-basis warning");
    expect(alternate.count).toBe(2);
    expect(alternate.probes).toEqual([
      "submission_volume_by_payer",
      "submission_volume_by_payer__prior",
    ]);
    // The surviving sentence must not keep ONE arbitrary plan node in it —
    // that would read as a fact about `submission_volume_by_payer` when it
    // is a fact about both probes.
    expect(alternate.message).toBe(
      "alternate_basis_used: a probe reads 'claim_volume' on the 'submission' basis (primary is 'service')",
    );
    // A warning raised ONCE keeps the probe the engine named: there is no
    // ambiguity to remove and the name is the most specific thing in it.
    const omitted = events.find(
      (e) => e.type === "warning" && e.message.includes("lag_distribution_compare"),
    );
    if (omitted?.type !== "warning") throw new Error("expected the omitted-probe warning");
    expect(omitted.count).toBeUndefined();
  });

  it("reads the governed provenance the badge renders, and invents none of it", () => {
    // The captured T1 answer runs the `cash_decline` playbook, which names
    // no governing metric and reads several. The badge must therefore get
    // NO primary and the full list — electing one would assert a contract
    // the turn never designated.
    const { value } = parseTurnResponse(SAMPLES.turn_complete, PIN);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    const wire = SAMPLES.turn_complete.metric;
    expect(value.metric?.primary).toBeUndefined();
    expect(wire.primary).toBeNull();
    expect(value.metric?.metrics).toEqual(
      wire.metrics.map((m: any) => ({ id: m.id, contractVersion: m.contract_version })),
    );
    expect(value.metric?.playbookId).toBe(wire.playbook_id);
    // The pack the TURN recorded, not the session pin the header renders
    // against: those come apart across a pack promotion, and this is the
    // one field that can tell an analyst which definition they are reading.
    expect(value.metric?.pack).toEqual({
      packId: wire.pack_id,
      version: wire.pack_version,
    });
    expect(value.metric?.packSnapshotId).toBe(wire.pack_snapshot_id);
  });

  it("carries the provenance onto turn_complete, where the badge reads it", () => {
    const { value } = parseTurnResponse(SAMPLES.turn_complete, PIN);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    const complete = turnResponseToEvents(value).at(-1);
    expect(complete?.type).toBe("turn_complete");
    expect(complete?.type === "turn_complete" ? complete.metric : null).toEqual(value.metric);
  });

  it("publishes no badge data at all when the server published none", () => {
    // Not an empty block standing in for one: a server that sent nothing
    // must leave the badge silent rather than assert an unnamed pack.
    const body = withoutPath(SAMPLES.turn_complete, "metric");
    const { value } = parseTurnResponse(body, PIN);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    expect(value.metric).toBeUndefined();
  });

  it("replays a clarification as a clarification, never as a complete answer", () => {
    const { value } = parseTurnResponse(SAMPLES.clarification_body, PIN);
    if (value?.outcome !== "clarification_required") throw new Error("unreachable");
    const events = turnResponseToEvents(value);
    expect(events.map((e) => e.type)).toEqual(["stage", "clarification", "turn_complete"]);
    const complete = events[events.length - 1];
    expect(complete.type === "turn_complete" && complete.status).toBe("clarification_required");
  });

  it("carries the assembled clarification on the terminal frame, always", () => {
    // Even when the live stream already delivered a `clarification` frame.
    // That frame is progress: the options on it are whatever the funnel had
    // reached, and the reason on it predates nothing. The terminal payload
    // is the assembled one, and skipping it left a card rendered mid-flight
    // as the last word — options computed, validated, published, and never
    // rendered as chips.
    const { value } = parseTurnResponse(SAMPLES.clarification_body, PIN);
    if (value?.outcome !== "clarification_required") throw new Error("unreachable");
    const received = newReceivedState();
    trackReceived(received, { type: "clarification", clarification: value.clarification });

    const events = turnResponseToEvents(value, received);

    expect(events.map((e) => e.type)).toEqual(["stage", "turn_complete"]);
    const complete = events[events.length - 1];
    if (complete.type !== "turn_complete") throw new Error("unreachable");
    expect(complete.clarification).toEqual(value.clarification);
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
    // This route keeps what the server stored: findings, warnings, the
    // evidence bundle projected from the turn's trace, and charts rebuilt
    // from the frames it persisted. Not the header, and not the narrative
    // — nothing stores the composed prose, so nothing pretends to.
    expect(value.charts.length).toBeGreaterThan(0);
    expect(value.header).toBeUndefined();
    expect(value.narrative).toBeUndefined();
    expect(value.evidence?.probes.length).toBeGreaterThan(0);
  });

  it("carries the same evidence bundle a live answer publishes", () => {
    const { value } = parseInvestigationResponse(SAMPLES.investigation);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    const wire = SAMPLES.investigation.evidence;
    const probe = value.evidence!.probes[0];
    // Renamed, never re-derived: every field traces to the wire bundle,
    // which the server projected from the turn's recorded trace.
    expect(probe.probeId).toBe(wire.probes[0].id);
    expect(probe.probeHash).toBe(wire.probes[0].hash);
    expect(probe.description).toBe(wire.probes[0].purpose);
    expect(probe.rowCount).toBe(wire.probes[0].rows);
    expect(value.evidence!.warehouseQueries).toBe(wire.warehouse_queries);
    expect(value.evidence!.zeroProbeTurn).toBe(wire.zero_probe_turn);
    expect(value.evidence!.reconciliation?.summary).toBe(wire.reconciliation.summary);
  });

  it("keeps its governed badge when the turn is restored from history", () => {
    // A re-opened session rebuilds its thread from this route. Without the
    // block here every restored turn would read as ungoverned — the one
    // regression the by-id read has to be immune to.
    const { value } = parseInvestigationResponse(SAMPLES.investigation);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    const wire = SAMPLES.investigation.metric;
    // A typed first turn names its own governing metric, so there IS a
    // single contract to point at here.
    expect(value.metric?.primary).toEqual({
      id: wire.primary.id,
      contractVersion: wire.primary.contract_version,
    });
    expect(value.metric?.playbookId).toBeUndefined();
    expect(value.metric?.pack.packId).toBe(wire.pack_id);
    const complete = turnResponseToEvents(value).at(-1);
    expect(complete?.type === "turn_complete" ? complete.metric : null).toEqual(value.metric);
  });

  it("emits the restored bundle as an evidence event, so the drawer opens", () => {
    const { value } = parseInvestigationResponse(SAMPLES.investigation);
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    const events = turnResponseToEvents(value);
    const evidence = events.find((e) => e.type === "evidence");
    expect(evidence && evidence.type === "evidence" ? evidence.evidence : null).toEqual(
      value.evidence,
    );
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
    /*
     * THE REAL EDGE SHAPE. This fixture used to read
     * `{parentTurnId, childTurnId, operators: ["SetDimensions(payer)"]}`,
     * which the server has never sent: `parent_id` and `child_id` are
     * INVESTIGATION ids, the turn the edge belongs to is `turn_id`, and
     * the operators are DTO objects, not display strings. Because the
     * fixture agreed with the parser rather than with the wire, a join
     * that could not match anything and a cast of objects to `string[]`
     * both passed for a release.
     */
    edges: [
      {
        parent_id: "inv_001",
        child_id: "inv_002",
        turn_id: "t2",
        operators: [{ op: "set_dimensions", dimensions: ["payer"] }],
      },
    ],
  };

  it("parses a valid DAG", () => {
    const { value, drift } = parseSessionLineage(LINEAGE);
    expect(drift).toEqual([]);
    expect(value?.nodes).toHaveLength(2);
    expect(value?.edges[0]?.operators).toEqual(["SetDimensions(payer)"]);
  });

  it("joins the edge to a NODE's turn, not to an investigation id", () => {
    const { value } = parseSessionLineage(LINEAGE);
    const edge = value?.edges[0];
    expect(edge?.turnId).toBe("t2");
    expect(value?.nodes.map((n) => n.turnId)).toContain(edge?.turnId);
    // The investigation ids are carried, and are deliberately NOT the
    // join key — they belong to a different namespace from `turnId`.
    expect(edge?.parentInvestigationId).toBe("inv_001");
    expect(edge?.childInvestigationId).toBe("inv_002");
  });

  it("drops an edge with no turn id, because it can join to nothing", () => {
    const { value, drift } = parseSessionLineage(withoutPath(LINEAGE, "edges.0.turn_id"));
    expect(value?.edges).toHaveLength(0);
    expect(drift).toContain("edges[0].turnId");
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
      edges: [{ parent_id: "inv_001", child_id: "inv_002", turn_id: "t1" }],
    });
    expect(drift).toEqual([]);
    expect(value?.nodes[0]?.turnId).toBe("t1");
    expect(value?.nodes[0]?.label).toBe("Why did cash decline last week?");
    expect(value?.edges[0]).toEqual({
      parentInvestigationId: "inv_001",
      childInvestigationId: "inv_002",
      turnId: "t1",
      operators: [],
    });
  });
});

/* ------------------------------------------------------------------ */
/* Chart units, shapes and duplicates — all pinned to LIVE payloads    */
/* ------------------------------------------------------------------ */

/**
 * Captured from `GET /v1/investigations/inv_3b08e3a4a1fe` on a running
 * API: a denial-rate ranking, `unit: "ratio"`, twelve payers, and rows
 * whose values are 0–1 fractions. The finding card for the same rows
 * reads "12.1%"; the chart used to read "0.1%".
 */
const LIVE_RATIO_CHART = {
  id: "chart_main",
  chart_type: "line",
  title: "denial rate — main",
  frame_id: "main",
  x: "payer",
  series: null,
  value: "denial_rate",
  unit: "ratio",
  grade: "direct",
  rows: [
    { x: "Ashvale Health Plan", series: null, value: 0.079945, referent_id: "F3" },
    { x: "Silverline Medicare Advantage", series: null, value: 0.121361, referent_id: "F1" },
    { x: "Lakewood Medicaid MCO", series: null, value: 0.104623, referent_id: "F2" },
  ],
  annotations: [],
  recipe_id: "denial_rate_trend",
};

describe("chart units (F7 — a ratio frame is not a percent frame)", () => {
  it("scales a live 0.079945 ratio row so it formats as 8.0%", () => {
    const spec = mapChartSpec(LIVE_RATIO_CHART);
    expect(spec?.unit).toBe("percent");
    const row = spec?.rows.find((r) => r.label === "Ashvale Health Plan");
    expect(row?.values.denial_rate).toBeCloseTo(7.9945, 6);
    // What the renderer prints, through the shared measure formatter.
    expect(formatMeasure(row!.values.denial_rate, spec!.unit)).toBe("8.0%");
  });

  it("leaves a `percent` frame alone — it is already in percentage points", () => {
    const spec = mapChartSpec({
      ...LIVE_RATIO_CHART,
      unit: "percent",
      rows: [{ x: "A", series: null, value: 7.9945 }],
    });
    expect(spec?.rows[0]?.values.denial_rate).toBeCloseTo(7.9945, 6);
  });

  it("renders a `days` frame as days, not as a bare count", () => {
    const spec = mapChartSpec({
      ...LIVE_RATIO_CHART,
      value: "avg_days_to_pay",
      unit: "days",
      rows: [{ x: "Atlas Commercial", series: null, value: 5.34 }],
    });
    expect(spec?.unit).toBe("days");
    expect(formatMeasure(5.34, "days")).toBe("5.3 d");
  });

  it("reads measure units — and their scale — off the turn's own chart specs", () => {
    // The unit alone is the half-fact that produced "0.1%": a `ratio`
    // metric renders as a percent AND arrives as a 0–1 fraction, and a
    // consumer needs both or it will format one against the other.
    expect(unitsFromChartSpecs([LIVE_RATIO_CHART])).toEqual({
      denial_rate: { unit: "percent", scale: 100 },
    });
  });
});

describe("chart shape and duplicates (F20)", () => {
  it("draws a categorical axis as bars even when the wire says line", () => {
    // Twelve unordered payers joined by a line assert a trend that does
    // not exist. The wire type is a hint; the axis is the fact.
    expect(mapChartSpec(LIVE_RATIO_CHART)?.kind).toBe("bar");
    expect(mapChartSpec(LIVE_RATIO_CHART)?.wireChartType).toBe("line");
  });

  it("keeps a line for a genuinely temporal axis", () => {
    const spec = mapChartSpec({
      ...LIVE_RATIO_CHART,
      x: "month",
      rows: [
        { x: "2026-05", series: null, value: 0.1 },
        { x: "2026-06", series: null, value: 0.11 },
        { x: "2026-07", series: null, value: 0.12 },
      ],
    });
    expect(spec?.kind).toBe("line");
  });

  /**
   * The `by month` grain, exactly as it arrives.
   *
   * Live capture (`POST /v1/sessions/{sid}/turns`, "show me denied dollars
   * by month for the last 6 months"): the six-month series is published as
   * `chart_type: "stacked_bar"` with `x: "month"` and ISO first-of-month
   * labels, alongside a trend finding titled "denied dollars by month,
   * 2026-02-01..2026-07-31: $885,721.50 → $1,193,126.92 (up $307,405.42)".
   *
   * The axis rule only ever fired on a DECLARED line, so an ordered time
   * axis could not earn one: a genuine six-month trend drew as six
   * disconnected bars directly beneath a finding whose own title has an
   * arrow in it. The wire type is a hint in both directions.
   */
  const LIVE_MONTHLY_TREND = {
    id: "chart_main",
    chart_type: "stacked_bar",
    title: "denied dollars — main",
    frame_id: "main",
    x: "month",
    series: null,
    value: "denied_dollars",
    unit: "money_cents",
    grade: "direct",
    rows: [
      { x: "2026-02-01", series: null, value: 88_572_150, referent_id: null },
      { x: "2026-03-01", series: null, value: 97_289_576, referent_id: null },
      { x: "2026-04-01", series: null, value: 82_252_958, referent_id: null },
      { x: "2026-05-01", series: null, value: 107_059_856, referent_id: null },
      { x: "2026-06-01", series: null, value: 99_090_358, referent_id: null },
      { x: "2026-07-01", series: null, value: 119_312_692, referent_id: null },
    ],
    annotations: [],
  };

  it("draws an ordered time axis as a line even when the wire says stacked_bar", () => {
    const spec = mapChartSpec(LIVE_MONTHLY_TREND);
    expect(spec?.kind).toBe("line");
    // Nothing is thrown away: the published type still travels.
    expect(spec?.wireChartType).toBe("stacked_bar");
    // Money arrives in cents and is NOT rescaled — only `ratio` is.
    expect(spec?.unit).toBe("cents");
    expect(spec?.rows[0]?.values.denied_dollars).toBe(88_572_150);
    expect(spec?.rows).toHaveLength(6);
    expect(spec?.title).toBe("Denied dollars by month");
  });

  it("leaves a genuinely multi-series stacked bar over time stacked", () => {
    // A composition over time is not a trend line per component: unstacking
    // it would change what the chart claims. Only the single-series case
    // is promoted.
    const spec = mapChartSpec({
      ...LIVE_MONTHLY_TREND,
      series: "payer",
      rows: [
        { x: "2026-06-01", series: "Atlas", value: 10 },
        { x: "2026-06-01", series: "Bluestone", value: 20 },
        { x: "2026-07-01", series: "Atlas", value: 12 },
        { x: "2026-07-01", series: "Bluestone", value: 22 },
      ],
    });
    expect(spec?.kind).toBe("grouped_bar");
  });

  it("composes a human title from the frame's columns", () => {
    const spec = mapChartSpec({
      ...LIVE_RATIO_CHART,
      value: "cash_posted",
      title: "cash posted — cash by payer  compare",
      frame_id: "cash_by_payer__compare",
    });
    expect(spec?.title).toBe("Cash posted by payer");
    expect(spec?.wireTitle).toBe("cash posted — cash by payer  compare");
  });

  it("suppresses the duplicate __compare frame a comparison turn publishes", () => {
    // Live fact: `GET /v1/investigations/inv_5f5f6771bb97` publishes
    // chart_main and chart_main__compare whose rows are byte-identical.
    const base = mapChartSpec(LIVE_RATIO_CHART)!;
    const twin = mapChartSpec({
      ...LIVE_RATIO_CHART,
      id: "chart_main__compare",
      frame_id: "main__compare",
      title: "denial rate — main  compare",
    })!;
    const rendered = selectRenderableCharts([twin, base]);
    expect(rendered).toHaveLength(1);
    expect(rendered[0]?.id).toBe("chart_main");
  });

  it("folds a genuinely different comparison frame in as a second series", () => {
    const base = mapChartSpec(LIVE_RATIO_CHART)!;
    const prior = mapChartSpec({
      ...LIVE_RATIO_CHART,
      id: "chart_main__prior",
      frame_id: "main__prior",
      rows: LIVE_RATIO_CHART.rows.map((r) => ({ ...r, value: r.value / 2 })),
    })!;
    const rendered = selectRenderableCharts([base, prior]);
    expect(rendered).toHaveLength(1);
    expect(rendered[0]?.series.map((s) => s.role)).toEqual(["current", "baseline"]);
    expect(rendered[0]?.rows[0]?.values.prior).toBeCloseTo(7.9945 / 2, 6);
  });

  it("folds an old-shape twin in as a comparison, drawn paired and never stacked", () => {
    const base = mapChartSpec({ ...LIVE_RATIO_CHART, chart_type: "stacked_bar" })!;
    const prior = mapChartSpec({
      ...LIVE_RATIO_CHART,
      chart_type: "stacked_bar",
      id: "chart_main__prior",
      frame_id: "main__prior",
      rows: LIVE_RATIO_CHART.rows.map((r) => ({ ...r, value: r.value / 2 })),
    })!;
    const [rendered] = selectRenderableCharts([base, prior]);
    // The stored shape reaches the same renderer as the live one: two
    // windows per category, side by side, with a delta in the tooltip.
    expect(rendered?.comparison).toEqual({ currentKey: "denial_rate", priorKey: "prior" });
    expect(rendered?.stacked).toBe(false);
  });

  it("drops a single-row frame — one point is a number, not a trend", () => {
    const scalar = mapChartSpec({
      ...LIVE_RATIO_CHART,
      id: "chart_scalar",
      frame_id: "scalar",
      x: "denial_rate",
      rows: [{ x: "denial_rate", series: null, value: 0.0812 }],
    })!;
    expect(selectRenderableCharts([scalar])).toEqual([]);
  });

  it("keeps a one-category COMPARISON — two windows is two marks, not one point", () => {
    // Live: `inv_509badf4a978/chart_main__compare` is the whole answer to
    // "why did our denial rate double in July versus June" — one category,
    // two marks. Counted in rows it was dropped, and the turn whose entire
    // subject is a movement between two windows rendered with no figure.
    const scalar = mapChartSpec({
      ...LIVE_RATIO_CHART,
      id: "chart_main__compare",
      frame_id: "main__compare",
      x: "denial_rate",
      series: "period",
      rows: [
        { x: "0.0812", series: "current", value: 0.0812 },
        { x: "0.0812", series: "prior", value: 0.0406 },
      ],
    })!;
    const rendered = selectRenderableCharts([scalar]);
    expect(rendered).toHaveLength(1);
    expect(rendered[0]?.comparison).toEqual({ currentKey: "current", priorKey: "prior" });
  });

  it("leaves a two-window chart whole rather than folding it into a base frame", () => {
    // The engine suppresses the superseded base at the source, so this
    // pairing does not arrive today. If it ever does, the comparison is
    // the chart with both windows in it and must not be reduced to the
    // current one.
    const base = mapChartSpec(LIVE_RATIO_CHART)!;
    const paired = mapChartSpec({
      ...LIVE_RATIO_CHART,
      id: "chart_main__compare",
      frame_id: "main__compare",
      series: "period",
      rows: LIVE_RATIO_CHART.rows.flatMap((r) => [
        { ...r, series: "current" },
        { ...r, series: "prior", value: r.value / 2 },
      ]),
    })!;
    const rendered = selectRenderableCharts([base, paired]);
    expect(rendered.map((c) => c.id)).toContain("chart_main__compare");
    const kept = rendered.find((c) => c.id === "chart_main__compare");
    expect(kept?.series.map((s) => s.key)).toEqual(["current", "prior"]);
    expect(kept?.rows[0]?.values).toEqual({
      current: expect.closeTo(7.9945, 6),
      prior: expect.closeTo(7.9945 / 2, 6),
    });
  });
});

/* ------------------------------------------------------------------ */
/* Finding anatomy — pinned to a live comparison turn (F16)            */
/* ------------------------------------------------------------------ */

/** Captured from `GET /v1/investigations/inv_5f5f6771bb97`. */
const LIVE_COMPARISON_FINDING = {
  referent: "F2",
  title: "Atlas Commercial denied dollars down $37,614.69 vs prior quarter",
  statement: "Atlas Commercial: denied dollars moved from $469,649.23 to $432,034.54.",
  metric_ids: ["denied_dollars"],
  values: [
    { name: "current_cents", value: 43_203_454 },
    { name: "prior_cents", value: 46_964_923 },
    { name: "delta_cents", value: -3_761_469 },
    { name: "pct_change", value: -0.080091 },
  ],
  grade: "direct",
  impact_cents: null,
  confidence: "qualified",
  suggested_refinements: ["drill into F2"],
  benchmarks: [],
};

/** Captured from `GET /v1/investigations/inv_3b08e3a4a1fe`. */
const LIVE_RANKING_FINDING = {
  referent: "F1",
  title: "Silverline Medicare Advantage: 12.1% denial rate",
  statement: "Silverline Medicare Advantage ranks #1 by denial rate: 12.1%.",
  metric_ids: ["denial_rate"],
  values: [
    { name: "denial_rate", value: 0.121361 },
    { name: "rank", value: 1 },
  ],
  grade: "direct",
  impact_cents: null,
  confidence: "high",
  suggested_refinements: ["drill into F1"],
  benchmarks: [],
};

const LIVE_MISMATCH_WARNING =
  "COMPARISON_WINDOW_MISMATCH: the comparison window (2026-01-01..2026-03-31, 90d) is not " +
  "the same length as the analysis window (91d). Differences and percentage changes between " +
  "them are dominated by the length difference and are not normalized; no impact figure is " +
  "published for this turn and its findings are qualified.";

describe("finding anatomy (F16 — mappers fitted to the live wire)", () => {
  it("maps current/prior cents into the comparison the card draws", () => {
    const finding = mapFinding(LIVE_COMPARISON_FINDING);
    expect(finding?.comparison).toEqual({
      currentCents: 43_203_454,
      priorCents: 46_964_923,
      currentLabel: "current",
      priorLabel: "prior",
    });
    expect(finding?.impactKind).toBe("delta");
  });

  it("maps pct_change onto deltaPct", () => {
    expect(mapFinding(LIVE_COMPARISON_FINDING)?.deltaPct).toBeCloseTo(-0.080091, 6);
  });

  it("renders a ranking finding's own measure in the unit its chart declares", () => {
    const finding = mapFinding(LIVE_RANKING_FINDING, {
      unitByMetric: unitsFromChartSpecs([LIVE_RATIO_CHART]),
    });
    expect(finding?.impactDisplay).toBe("12.1%");
    expect(finding?.impactLabel).toBe("denial rate");
  });

  it("withholds the stat rather than inventing one when the unit is unknown", () => {
    const finding = mapFinding(LIVE_RANKING_FINDING);
    expect(finding?.impactDisplay).toBeUndefined();
  });

  it("names the reason the server published no impact figure", () => {
    expect(impactWithheldReason([LIVE_MISMATCH_WARNING])).toBe(
      "not published — 90d vs 91d comparison window",
    );
    const finding = mapFinding(LIVE_COMPARISON_FINDING, {
      impactWithheldReason: impactWithheldReason([LIVE_MISMATCH_WARNING]),
    });
    expect(finding?.impactWithheldReason).toBe("not published — 90d vs 91d comparison window");
  });

  it("says nothing about a withheld impact when nothing withheld it", () => {
    expect(impactWithheldReason(["suppression: cells counting fewer than 11 entities"])).toBeUndefined();
  });

  it("wires both facts through parseTurnResponse end to end", () => {
    const { value } = parseTurnResponse(
      {
        outcome: "answer",
        session_id: "s",
        investigation_id: "i",
        turn_class: "new_investigation",
        findings: [LIVE_COMPARISON_FINDING, LIVE_RANKING_FINDING],
        chart_specs: [LIVE_RATIO_CHART],
        warnings: [LIVE_MISMATCH_WARNING],
      },
      PIN,
    );
    if (value?.outcome !== "answer") throw new Error("expected an answer");
    expect(value.findings[0]?.impactWithheldReason).toBe(
      "not published — 90d vs 91d comparison window",
    );
    expect(value.findings[0]?.deltaPct).toBeCloseTo(-0.080091, 6);
    expect(value.findings[1]?.impactDisplay).toBe("12.1%");
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

  it("never synthesizes a drill handle the server did not publish", () => {
    // The old behaviour invented `DrillInto(P1)` for any card without a
    // handle — including the four live cards the server marks
    // `drillable: false` with a written GRAIN_INCOMPATIBLE refusal. A
    // client-side gesture over a server-side refusal is the UI claiming a
    // capability the platform has just declined.
    const { value } = parsePortfolioSnapshot(withoutPath(SNAPSHOT, "items.0.drill"));
    expect(value?.items[0]?.drill).toBeUndefined();
    expect(value?.items[0]?.drillable).toBe(false);
  });

  it("carries the server's refusal for an undrillable card", () => {
    const { value } = parsePortfolioSnapshot({
      items: [
        {
          anomaly_id: "ANM-013",
          title: "Gross collection rate dip",
          impact_cents: 49_326_600,
          provenance: "external_detection",
          drillable: false,
          drill_unavailable_reason:
            "GRAIN_INCOMPATIBLE: dimension 'proc_group' is not a legal scope dimension for ratio metric 'gross_collection_rate'",
          recoverable_cents_estimate: 986_500,
          actionability_label: "marginally recoverable",
          severity: "high",
          age_days: 44,
        },
      ],
      warnings: ["4 of 33 detected anomalies (36% of ranked impact) are not investigable"],
    });
    const item = value?.items[0];
    expect(item?.drillable).toBe(false);
    expect(item?.drillUnavailableReason).toContain("GRAIN_INCOMPATIBLE");
    expect(item?.recoverableCentsEstimate).toBe(986_500);
    expect(item?.actionabilityLabel).toBe("marginally recoverable");
    expect(item?.severity).toBe("high");
    expect(item?.ageDays).toBe(44);
    expect(value?.warnings).toHaveLength(1);
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
