import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiDriver,
  fetchCapabilities,
  fetchHealth,
  fetchHealthDetail,
  fetchPortfolioLatest,
  fetchSessionLineage,
  fetchSessionList,
  fetchTrace,
  resolveDriverKind,
} from "@/lib/apiDriver";
import type { TurnEvent } from "@/lib/types";

/* ------------------------------------------------------------------ */
/* Wire fakes                                                          */
/* ------------------------------------------------------------------ */

const SESSION_WIRE = {
  session_id: "sess_live_1",
  watermark: { id: "wm_010", loaded_at: "2026-08-06 04:11", newest_data_date: "2026-08-05" },
  pack: { pack_id: "base-rcm", version: "1.0.0" },
};

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function sse(event: string, payload: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`;
}

function streamOf(frames: string[], failAfter = false): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  let index = 0;
  // Pull-based so every frame is DELIVERED before the transport "drops" —
  // erroring from start() would discard queued chunks.
  return new ReadableStream<Uint8Array>({
    pull(controller) {
      if (index < frames.length) {
        controller.enqueue(encoder.encode(frames[index]));
        index += 1;
      } else if (failAfter) {
        controller.error(new TypeError("network dropped"));
      } else {
        controller.close();
      }
    },
  });
}

interface FakeResponseInit {
  status?: number;
  bodyText?: string;
  json?: unknown;
  stream?: ReadableStream<Uint8Array>;
}

function fakeResponse(init: FakeResponseInit): Response {
  const status = init.status ?? 200;
  const text = init.bodyText ?? (init.json !== undefined ? JSON.stringify(init.json) : "");
  const fake = {
    ok: status >= 200 && status < 300,
    status,
    body: init.stream ?? null,
    text: () => Promise.resolve(text),
    json: () => Promise.resolve(init.json as unknown),
  };
  return fake as unknown as Response;
}

interface RecordedCall {
  url: string;
  method: string;
  accept: string | undefined;
  body: Record<string, unknown> | null;
}

function scriptedFetch(
  script: (call: RecordedCall, index: number) => Response | Promise<Response>,
): { fetchImpl: typeof fetch; calls: RecordedCall[] } {
  const calls: RecordedCall[] = [];
  const fetchImpl = ((input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const headers = (init?.headers ?? {}) as Record<string, string>;
    const call: RecordedCall = {
      url: String(input),
      method: init?.method ?? "GET",
      accept: headers.Accept,
      body: typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null,
    };
    calls.push(call);
    return Promise.resolve(script(call, calls.length - 1));
  }) as typeof fetch;
  return { fetchImpl, calls };
}

/**
 * Wire frames, in the server's own spelling. Earlier milestones wrote these
 * in the UI's vocabulary, which is exactly how a green suite coexisted with
 * a live turn that produced nothing but drift.
 */
const ANSWER_BODY = {
  outcome: "answer",
  session_id: "sess_a1b2",
  investigation_id: "inv_1",
  turn_class: "new_investigation",
  findings: [],
  chart_specs: [],
  narrative: "Payer cash fell 12.7% week over week.",
  warnings: [],
};

const COMPLETE_FRAMES = [
  sse("stage", { stage: "classify" }),
  sse("narrative_delta", { delta: "Payer cash fell 12.7% week over week." }),
  sse("turn_complete", ANSWER_BODY),
];

function makeDriver(fetchImpl: typeof fetch, extra: ConstructorParameters<typeof ApiDriver>[0] = {}) {
  return new ApiDriver({
    baseUrl: "http://api.test",
    fetchImpl,
    recoveryAttempts: 2,
    recoveryDelayMs: 0,
    ...extra,
  });
}

async function collectSubmit(
  driver: ApiDriver,
  submission: Parameters<ApiDriver["submit"]>[0],
): Promise<TurnEvent[]> {
  const events: TurnEvent[] = [];
  await driver.submit(submission, (event) => events.push(event));
  return events;
}

beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
});

/* ------------------------------------------------------------------ */
/* Driver selection                                                    */
/* ------------------------------------------------------------------ */

describe("resolveDriverKind", () => {
  it("defaults to api unless NEXT_PUBLIC_REVI_DRIVER is exactly 'mock'", () => {
    // The env var is inlined at build time; in tests it is unset.
    expect(resolveDriverKind()).toBe("api");
  });
});

/* ------------------------------------------------------------------ */
/* Session bootstrap + turn body                                       */
/* ------------------------------------------------------------------ */

describe("ApiDriver session bootstrap", () => {
  it("creates the session on first turn and reuses it afterwards", async () => {
    const onSession = vi.fn();
    const { fetchImpl, calls } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(COMPLETE_FRAMES) }),
    );
    const driver = makeDriver(fetchImpl, { onSession });

    await collectSubmit(driver, { utterance: "Why did cash decline last week?" });
    await collectSubmit(driver, { utterance: "Break that down by payer" });

    const sessionCalls = calls.filter((c) => c.url.endsWith("/v1/sessions"));
    expect(sessionCalls).toHaveLength(1);
    expect(onSession).toHaveBeenCalledTimes(1);
    expect(onSession).toHaveBeenCalledWith({
      sessionId: "sess_live_1",
      watermark: { id: "wm_010", loadedAt: "2026-08-06 04:11", newestDataDate: "2026-08-05" },
      pack: { packId: "base-rcm", version: "1.0.0" },
    });
    const turnCalls = calls.filter((c) => c.url.includes("/turns"));
    expect(turnCalls[0]?.url).toBe("http://api.test/v1/sessions/sess_live_1/turns");
  });

  it("sends the agreed wire body with fresh idempotency + correlation UUIDs", async () => {
    const { fetchImpl, calls } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(COMPLETE_FRAMES) }),
    );
    const driver = makeDriver(fetchImpl);
    await collectSubmit(driver, { utterance: "Why did cash decline last week?" });

    const session = calls.find((c) => c.url.endsWith("/v1/sessions"));
    expect(session?.body).toEqual({ tenant: "demo" });

    const turn = calls.find((c) => c.url.includes("/turns"));
    expect(turn?.accept).toBe("text/event-stream");
    expect(turn?.body?.utterance).toBe("Why did cash decline last week?");
    expect(String(turn?.body?.idempotency_key)).toMatch(UUID_RE);
    expect(String(turn?.body?.correlation_id)).toMatch(UUID_RE);
    expect(turn?.body).not.toHaveProperty("refinements");
    expect(turn?.body).not.toHaveProperty("clarification_response");
    expect(turn?.body).not.toHaveProperty("re_anchor");
    // No settings key at all with the controls at their defaults — the
    // pre-settings body, byte for byte.
    expect(turn?.body).not.toHaveProperty("settings");
  });

  it("puts chosen session settings on the turn, in the published spelling", async () => {
    const { fetchImpl, calls } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(COMPLETE_FRAMES) }),
    );
    const driver = makeDriver(fetchImpl);
    await collectSubmit(driver, {
      utterance: "Why did cash decline last week?",
      settings: {
        modelTier: "claude-sonnet-5",
        // Above a 0.50 deployment ceiling on purpose: the client sends what
        // was chosen and the server refuses it by name. Clamping here would
        // hide the refusal the analyst needs to see.
        maxTurnCostUsd: "5.00",
        narrativeDepth: "analyst",
        evidenceDepth: "deep",
        debug: true,
      },
    });

    const turn = calls.find((c) => c.url.includes("/turns"));
    expect(turn?.body?.settings).toEqual({
      model_tier: "claude-sonnet-5",
      max_turn_cost_usd: "5.00",
      narrative_depth: "analyst",
      evidence_depth: "deep",
      debug: true,
    });
  });

  it("omits the settings key when the submission carries the defaults", async () => {
    const { fetchImpl, calls } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(COMPLETE_FRAMES) }),
    );
    const driver = makeDriver(fetchImpl);
    await collectSubmit(driver, {
      utterance: "Why did cash decline last week?",
      settings: {
        modelTier: null,
        maxTurnCostUsd: null,
        narrativeDepth: "summary",
        evidenceDepth: "standard",
        debug: false,
      },
    });

    const turn = calls.find((c) => c.url.includes("/turns"));
    expect(turn?.body).not.toHaveProperty("settings");
  });

  it("maps typed refinements, clarification responses, and re_anchor onto the wire", async () => {
    const { fetchImpl, calls } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(COMPLETE_FRAMES) }),
    );
    const driver = makeDriver(fetchImpl);
    await collectSubmit(driver, {
      refinements: [{ op: "DrillInto", target: "F2" }],
      clarificationResponse: "Last week",
      reAnchor: true,
    });

    const turn = calls.find((c) => c.url.includes("/turns"));
    // Translated to the published spelling — posting the UI's PascalCase
    // `op` is a 422 against a conforming server.
    expect(turn?.body?.refinements).toEqual([{ op: "drill_into", target: "F2" }]);
    expect(turn?.body?.clarification_response).toBe("Last week");
    expect(turn?.body?.re_anchor).toBe(true);
    expect(turn?.body).not.toHaveProperty("utterance");
  });

  it("posts a typed investigation spec as a first turn, verbatim", async () => {
    const { fetchImpl, calls } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(COMPLETE_FRAMES) }),
    );
    const driver = makeDriver(fetchImpl);
    // A portfolio card's drill_spec: already the published shape, so it
    // travels untouched — a NEW investigation, not a refinement.
    const spec = {
      metric_ids: ["denied_dollars"],
      dimensions: ["payer"],
      filters: [{ op: "add_filter", dimension: "payer", predicate_op: "eq", values: ["Atlas"] }],
      window: { start: "2026-06-25", end: "2026-07-25" },
    };
    await collectSubmit(driver, { spec });

    const turn = calls.find((c) => c.url.includes("/turns"));
    expect(turn?.body?.spec).toEqual(spec);
    expect(turn?.body).not.toHaveProperty("refinements");
    expect(turn?.body).not.toHaveProperty("utterance");
  });

  it("flags contract drift on a malformed session response without going offline", async () => {
    const onContractDrift = vi.fn();
    const onConnectionState = vi.fn();
    const broken = structuredClone(SESSION_WIRE) as Record<string, unknown>;
    delete broken.pack;
    const { fetchImpl } = scriptedFetch(() => fakeResponse({ json: broken }));
    const driver = makeDriver(fetchImpl, { onContractDrift, onConnectionState });

    const events = await collectSubmit(driver, { utterance: "hello" });
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ type: "error", code: "CONTRACT_DRIFT" });
    expect(onContractDrift).toHaveBeenCalledWith(
      expect.arrayContaining(["pack.pack_id", "pack.version"]),
      "POST /v1/sessions",
    );
    expect(onConnectionState).not.toHaveBeenCalledWith("offline", expect.anything());
  });

  it("reports offline with a visible error when the API is unreachable", async () => {
    const onConnectionState = vi.fn();
    const fetchImpl = (() => Promise.reject(new TypeError("fetch failed"))) as typeof fetch;
    const driver = makeDriver(fetchImpl, { onConnectionState });

    const events = await collectSubmit(driver, { utterance: "hello" });
    expect(events[0]).toMatchObject({ type: "error", code: "API_UNREACHABLE" });
    expect(onConnectionState).toHaveBeenCalledWith("offline", "session bootstrap failed");
  });
});

/* ------------------------------------------------------------------ */
/* "New chat" — newSession()                                           */
/* ------------------------------------------------------------------ */

describe("ApiDriver.newSession", () => {
  it("discards the cached session and eagerly mints a fresh one for the NEXT turn", async () => {
    const onSession = vi.fn();
    let sessionCalls = 0;
    const { fetchImpl, calls } = scriptedFetch((call) => {
      if (call.url.endsWith("/v1/sessions")) {
        sessionCalls += 1;
        return fakeResponse({
          json: { ...SESSION_WIRE, session_id: `sess_live_${sessionCalls}` },
        });
      }
      return fakeResponse({ stream: streamOf(COMPLETE_FRAMES) });
    });
    const driver = makeDriver(fetchImpl, { onSession });

    // First turn lazily bootstraps sess_live_1.
    await collectSubmit(driver, { utterance: "q1" });
    expect(onSession).toHaveBeenLastCalledWith(
      expect.objectContaining({ sessionId: "sess_live_1" }),
    );

    // newSession() eagerly mints sess_live_2 with no turn in flight.
    await driver.newSession();
    expect(onSession).toHaveBeenLastCalledWith(
      expect.objectContaining({ sessionId: "sess_live_2" }),
    );
    expect(calls.filter((c) => c.url.endsWith("/v1/sessions"))).toHaveLength(2);

    // The NEXT turn reuses the FRESH session, not the abandoned one.
    await collectSubmit(driver, { utterance: "q2" });
    const turnCalls = calls.filter((c) => c.url.includes("/turns"));
    expect(turnCalls[turnCalls.length - 1]?.url).toBe(
      "http://api.test/v1/sessions/sess_live_2/turns",
    );
    // Still only 2 session POSTs — the second turn did NOT bootstrap again.
    expect(calls.filter((c) => c.url.endsWith("/v1/sessions"))).toHaveLength(2);
  });

  it("tolerates an unreachable API — the cache stays cleared for lazy retry on the next turn", async () => {
    const onConnectionState = vi.fn();
    const onSession = vi.fn();
    let sessionAttempt = 0;
    const fetchImpl = ((input: RequestInfo | URL) => {
      if (String(input).endsWith("/v1/sessions")) {
        sessionAttempt += 1;
        if (sessionAttempt === 1) return Promise.reject(new TypeError("offline"));
        return Promise.resolve(fakeResponse({ json: SESSION_WIRE }));
      }
      return Promise.resolve(fakeResponse({ stream: streamOf(COMPLETE_FRAMES) }));
    }) as typeof fetch;
    const driver = makeDriver(fetchImpl, { onConnectionState, onSession });

    // Does not throw — offline is tolerated.
    await expect(driver.newSession()).resolves.toBeUndefined();
    expect(onConnectionState).toHaveBeenCalledWith("offline", "new session failed");
    expect(onSession).not.toHaveBeenCalled();

    // The next turn lazily retries from a clean slate and succeeds.
    const events = await collectSubmit(driver, { utterance: "q" });
    expect(onSession).toHaveBeenCalledWith(
      expect.objectContaining({ sessionId: "sess_live_1" }),
    );
    expect(events.some((e) => e.type === "error")).toBe(false);
  });

  it("does not flip offline when the fresh session response merely fails contract validation", async () => {
    const onConnectionState = vi.fn();
    const onContractDrift = vi.fn();
    const broken = structuredClone(SESSION_WIRE) as Record<string, unknown>;
    delete broken.pack;
    const { fetchImpl } = scriptedFetch(() => fakeResponse({ json: broken }));
    const driver = makeDriver(fetchImpl, { onConnectionState, onContractDrift });

    await driver.newSession();

    expect(onContractDrift).toHaveBeenCalledWith(
      expect.arrayContaining(["pack.pack_id", "pack.version"]),
      "POST /v1/sessions",
    );
    expect(onConnectionState).not.toHaveBeenCalledWith("offline", expect.anything());
  });
});

/* ------------------------------------------------------------------ */
/* Streaming + drift gating                                            */
/* ------------------------------------------------------------------ */

describe("ApiDriver streaming", () => {
  it("emits validated events in order and reports online on completion", async () => {
    const onConnectionState = vi.fn();
    const { fetchImpl } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(COMPLETE_FRAMES) }),
    );
    const driver = makeDriver(fetchImpl, { onConnectionState });

    const events = await collectSubmit(driver, { utterance: "q" });
    // The authoritative turn_complete body replays as its own events, which
    // is why the rail gets a closing stage before the terminal frame.
    expect(events.map((e) => e.type)).toEqual([
      "stage",
      "narrative_delta",
      "stage",
      "turn_complete",
    ]);
    expect(onConnectionState).toHaveBeenLastCalledWith("online");
  });

  it("drops malformed frames, console.errors the field path, and keeps streaming", async () => {
    const onContractDrift = vi.fn();
    const frames = [
      sse("finding", { referent: "F1" }), // missing title / statement
      ...COMPLETE_FRAMES,
    ];
    const { fetchImpl } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(frames) }),
    );
    const driver = makeDriver(fetchImpl, { onContractDrift });

    const events = await collectSubmit(driver, { utterance: "q" });
    expect(events.some((e) => e.type === "finding")).toBe(false);
    expect(events.map((e) => e.type)).toEqual([
      "stage",
      "narrative_delta",
      "stage",
      "turn_complete",
    ]);
    expect(onContractDrift).toHaveBeenCalledWith(
      expect.arrayContaining(["title", "statement"]),
      'sse frame "finding"',
    );
    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining('missing required field "title"'),
    );
  });

  it("turns a malformed turn_complete into a visible CONTRACT_DRIFT error (no silent hang)", async () => {
    const frames = [
      sse("narrative_delta", { delta: "…" }),
      sse("turn_complete", { outcome: "answer", session_id: "s" }), // no investigation_id
    ];
    const { fetchImpl, calls } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(frames) }),
    );
    const driver = makeDriver(fetchImpl);

    const events = await collectSubmit(driver, { utterance: "q" });
    expect(events[events.length - 1]).toMatchObject({ type: "error", code: "CONTRACT_DRIFT" });
    // Terminal frame arrived (however broken) — no recovery replay.
    expect(calls.filter((c) => c.url.includes("/turns"))).toHaveLength(1);
  });

  it("carries the debug block off the turn_complete frame — no new frame kind", async () => {
    // Verified against a live API: with `debug: true` in the turn's
    // settings the whole DebugTracePayload rides inside `turn_complete`,
    // so the stream parser learns no new ordering rules.
    const frames = [
      sse("stage", { stage: "classify" }),
      sse("turn_complete", {
        ...ANSWER_BODY,
        debug: {
          trace_id: "trace_9f2c",
          session_id: "sess_a1b2",
          investigation_id: "inv_1",
          turn_id: "turn_1",
          turn_class: "new_investigation",
          classification_confidence: 0.94,
          probes: [{ id: "cash_by_payer", hash: "8094abae", rows: 12 }],
          llm_calls: [{ template: "classify_turn", model: "claude-opus-5", cost_usd: "0.0182" }],
        },
      }),
    ];
    const { fetchImpl } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(frames) }),
    );
    const driver = makeDriver(fetchImpl);

    const events = await collectSubmit(driver, { utterance: "q" });
    const complete = events.find((e) => e.type === "turn_complete");
    expect(complete).toMatchObject({
      type: "turn_complete",
      debug: {
        traceId: "trace_9f2c",
        turnClass: "new_investigation",
        classificationConfidence: 0.94,
      },
    });
  });

  it("omits the debug block entirely when the turn ran without it", async () => {
    const { fetchImpl } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ stream: streamOf(COMPLETE_FRAMES) }),
    );
    const driver = makeDriver(fetchImpl);

    const events = await collectSubmit(driver, { utterance: "q" });
    const complete = events.find((e) => e.type === "turn_complete");
    expect(complete).not.toHaveProperty("debug");
  });

  it("surfaces an ErrorEnvelope on a rejected turn without flipping offline", async () => {
    const onConnectionState = vi.fn();
    const { fetchImpl } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({
            status: 429,
            bodyText: JSON.stringify({
              code: "QUERY_BUDGET_EXCEEDED",
              message: "Query budget exhausted.",
              correlation_id: "corr_9",
            }),
          }),
    );
    const driver = makeDriver(fetchImpl, { onConnectionState });

    const events = await collectSubmit(driver, { utterance: "q" });
    expect(events).toEqual([
      {
        type: "error",
        code: "QUERY_BUDGET_EXCEEDED",
        message: "Query budget exhausted.",
        correlationId: "corr_9",
      },
    ]);
    expect(onConnectionState).not.toHaveBeenCalledWith("offline", expect.anything());
  });

  it("decodes the envelope on a 400 — §12 domain errors moved off 422", async () => {
    // The server used to answer domain (§12) failures with 422 and now answers
    // with 400, reserving 422 for a malformed request body. The driver decodes
    // an envelope out of ANY non-2xx body, so no status list needed updating —
    // this pins that, because a status-gated decoder would regress silently.
    const { fetchImpl } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({
            status: 400,
            bodyText: JSON.stringify({
              code: "GRAIN_INCOMPATIBLE",
              message: "That comparison mixes grains.",
              correlation_id: "corr_400",
            }),
          }),
    );
    const driver = makeDriver(fetchImpl);

    const events = await collectSubmit(driver, { utterance: "q" });
    expect(events).toEqual([
      {
        type: "error",
        code: "GRAIN_INCOMPATIBLE",
        message: "That comparison mixes grains.",
        correlationId: "corr_400",
      },
    ]);
  });

  it("falls back to HTTP_<status> when the error body is not an envelope", async () => {
    const { fetchImpl } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ status: 500, bodyText: "Bad Gateway" }),
    );
    const driver = makeDriver(fetchImpl);

    const events = await collectSubmit(driver, { utterance: "q" });
    expect(events[0]).toMatchObject({ type: "error", code: "HTTP_500" });
  });
});

/* ------------------------------------------------------------------ */
/* Reconnect tolerance                                                 */
/* ------------------------------------------------------------------ */

const FULL_TURN_RESPONSE = {
  outcome: "answer",
  session_id: "sess_a1b2",
  investigation_id: "inv_7",
  turn_class: "new_investigation",
  findings: [
    {
      referent: "F1",
      title: "State Medicaid cash down",
      statement: "Fell 52.9% WoW.",
      metric_ids: [],
      values: [],
      grade: "direct",
      confidence: "high",
      suggested_refinements: [],
    },
    {
      referent: "F2",
      title: "Atlas Commercial volume down",
      statement: "Fell 18.3% WoW.",
      metric_ids: [],
      values: [],
      grade: "direct",
      confidence: "high",
      suggested_refinements: [],
    },
  ],
  chart_specs: [],
  warnings: [],
  narrative: "Payer cash fell. Two payers explain most of it.",
};

/** `GET /v1/investigations/{iid}` — a different shape from the turn body. */
const INVESTIGATION_BODY = {
  investigation_id: "inv_9",
  session_id: "sess_a1b2",
  turn_id: "turn_9",
  turn_class: "new_investigation",
  status: "complete",
  created_at: "2026-08-03T04:12:00Z",
  findings: FULL_TURN_RESPONSE.findings,
  warnings: [],
};

describe("ApiDriver reconnect tolerance", () => {
  it("recovers a dropped stream via same-key JSON replay, duplicate-free", async () => {
    const droppedFrames = [
      sse("finding", FULL_TURN_RESPONSE.findings[0]),
      sse("narrative_delta", { delta: "Payer cash fell. " }),
    ];
    const { fetchImpl, calls } = scriptedFetch((call) => {
      if (call.url.endsWith("/v1/sessions")) return fakeResponse({ json: SESSION_WIRE });
      if (call.accept === "text/event-stream") {
        return fakeResponse({ stream: streamOf(droppedFrames, true) });
      }
      return fakeResponse({ json: FULL_TURN_RESPONSE });
    });
    const driver = makeDriver(fetchImpl);

    const events = await collectSubmit(driver, { utterance: "q" });

    // Same idempotency key on the live attempt and the JSON replay.
    const turnCalls = calls.filter((c) => c.url.includes("/turns"));
    expect(turnCalls).toHaveLength(2);
    expect(turnCalls[1]?.accept).toBe("application/json");
    expect(turnCalls[1]?.body?.idempotency_key).toBe(turnCalls[0]?.body?.idempotency_key);

    // F1 arrived live; recovery must add only F2 + the narrative suffix.
    const findingTitles = events
      .filter((e): e is Extract<TurnEvent, { type: "finding" }> => e.type === "finding")
      .map((e) => e.finding.referent.value);
    expect(findingTitles).toEqual(["F1", "F2"]);
    const narrative = events
      .filter((e): e is Extract<TurnEvent, { type: "narrative_delta" }> => e.type === "narrative_delta")
      .map((e) => e.text)
      .join("");
    expect(narrative).toBe("Payer cash fell. Two payers explain most of it.");
    expect(events[events.length - 1]).toMatchObject({
      type: "turn_complete",
      investigationId: "inv_7",
      status: "complete",
    });
  });

  it("prefers GET /v1/investigations/{iid} when a frame carried the id", async () => {
    const droppedFrames = [
      // Extra fields are tolerated — and the driver harvests the id.
      sse("stage", { stage: "classify", investigation_id: "inv_9" }),
    ];
    const { fetchImpl, calls } = scriptedFetch((call) => {
      if (call.url.endsWith("/v1/sessions")) return fakeResponse({ json: SESSION_WIRE });
      if (call.accept === "text/event-stream") {
        return fakeResponse({ stream: streamOf(droppedFrames, true) });
      }
      // the by-id route answers with InvestigationResponse, not a turn body
      return fakeResponse({ json: INVESTIGATION_BODY });
    });
    const driver = makeDriver(fetchImpl);

    const events = await collectSubmit(driver, { utterance: "q" });
    expect(calls.map((c) => c.url)).toContain("http://api.test/v1/investigations/inv_9");
    expect(events[events.length - 1]).toMatchObject({ type: "turn_complete", investigationId: "inv_9" });
  });

  it("recovers a stream that closed cleanly without a terminal frame", async () => {
    const { fetchImpl, calls } = scriptedFetch((call) => {
      if (call.url.endsWith("/v1/sessions")) return fakeResponse({ json: SESSION_WIRE });
      if (call.accept === "text/event-stream") {
        return fakeResponse({ stream: streamOf([sse("narrative_delta", { delta: "Half " })]) });
      }
      return fakeResponse({ json: FULL_TURN_RESPONSE });
    });
    const driver = makeDriver(fetchImpl);

    const events = await collectSubmit(driver, { utterance: "q" });
    expect(calls.filter((c) => c.url.includes("/turns"))).toHaveLength(2);
    expect(events[events.length - 1]).toMatchObject({ type: "turn_complete" });
  });

  it("goes offline with a visible error when recovery is exhausted", async () => {
    const onConnectionState = vi.fn();
    const { fetchImpl, calls } = scriptedFetch((call) => {
      if (call.url.endsWith("/v1/sessions")) return fakeResponse({ json: SESSION_WIRE });
      if (call.accept === "text/event-stream") {
        return fakeResponse({ stream: streamOf([], true) });
      }
      throw new TypeError("still down");
    });
    const driver = makeDriver(fetchImpl, { onConnectionState });

    const events = await collectSubmit(driver, { utterance: "q" });
    expect(events[events.length - 1]).toMatchObject({ type: "error", code: "API_UNREACHABLE" });
    expect(onConnectionState).toHaveBeenCalledWith("offline", "lost connection mid-turn");
    // 1 sessions + 1 SSE attempt + recoveryAttempts JSON replays.
    expect(calls.filter((c) => c.accept === "application/json" && c.url.includes("/turns"))).toHaveLength(2);
  });
});

/* ------------------------------------------------------------------ */
/* GET endpoints                                                       */
/* ------------------------------------------------------------------ */

describe("GET endpoint fetchers", () => {
  it("fetchHealth returns true on 200 and false when unreachable", async () => {
    const { fetchImpl } = scriptedFetch(() => fakeResponse({ json: { status: "ok" } }));
    await expect(fetchHealth({ baseUrl: "http://api.test", fetchImpl })).resolves.toBe(true);

    const down = (() => Promise.reject(new TypeError("refused"))) as typeof fetch;
    await expect(fetchHealth({ baseUrl: "http://api.test", fetchImpl: down })).resolves.toBe(false);
  });

  it("fetchHealthDetail reads llm_mode off a healthy response", async () => {
    const { fetchImpl } = scriptedFetch(() =>
      fakeResponse({ json: { status: "ok", llm_mode: "scripted-demo" } }),
    );
    await expect(fetchHealthDetail({ baseUrl: "http://api.test", fetchImpl })).resolves.toEqual({
      ok: true,
      llmMode: "scripted-demo",
    });
  });

  it("fetchHealthDetail reports unreachable with no llmMode", async () => {
    const down = (() => Promise.reject(new TypeError("refused"))) as typeof fetch;
    await expect(
      fetchHealthDetail({ baseUrl: "http://api.test", fetchImpl: down }),
    ).resolves.toEqual({ ok: false });
  });

  it("fetchCapabilities reads the deployment's published settings bounds", async () => {
    const { fetchImpl, calls } = scriptedFetch(() =>
      fakeResponse({
        json: {
          llm: "claude-agent-sdk",
          pack_id: "base-rcm",
          pack_version: "1.0.0",
          newest_watermark_id: "wm_003",
          settings: {
            model_tiers: ["claude-opus-5", "claude-sonnet-5"],
            default_model_tier: "claude-opus-5",
            model_tier_effective: true,
            max_turn_cost_usd: "0.50",
            narrative_depths: ["summary", "analyst"],
            evidence_depths: ["standard", "deep"],
            evidence_depth_deep_multiplier: 4,
            debug_available: true,
          },
        },
      }),
    );
    const capabilities = await fetchCapabilities({ baseUrl: "http://api.test", fetchImpl });

    expect(calls[0]?.url).toBe("http://api.test/v1/capabilities");
    expect(capabilities.settings.modelTiers).toEqual(["claude-opus-5", "claude-sonnet-5"]);
    expect(capabilities.settings.evidenceDepthDeepMultiplier).toBe(4);
  });

  it("fetchTrace reads a recorded decision trace", async () => {
    const { fetchImpl, calls } = scriptedFetch(() =>
      fakeResponse({
        json: {
          trace_id: "trace_1",
          session_id: "sess_1",
          investigation_id: "inv_1",
          turn_id: "turn_1",
          turn_class: "new_investigation",
          probes: [{ id: "cash_by_payer", hash: "abc123", rows: 12 }],
        },
      }),
    );
    const trace = await fetchTrace("inv_1", { baseUrl: "http://api.test", fetchImpl });

    expect(calls[0]?.url).toBe("http://api.test/v1/investigations/inv_1/trace");
    expect(trace?.turnClass).toBe("new_investigation");
    expect(trace?.probes[0]).toMatchObject({ id: "cash_by_payer", rows: 12 });
  });

  it("fetchTrace propagates the deployment's refusal instead of returning null", async () => {
    const { fetchImpl } = scriptedFetch(() =>
      fakeResponse({
        status: 400,
        json: {
          code: "POLICY_DENIED",
          message: "debug traces are disabled on this deployment (REVI_DEBUG_TRACE=0)",
          correlation_id: "corr_1",
        },
      }),
    );
    await expect(fetchTrace("inv_1", { baseUrl: "http://api.test", fetchImpl })).rejects.toThrow(
      /REVI_DEBUG_TRACE=0/,
    );
  });

  it("fetchPortfolioLatest treats 501 as gracefully unavailable", async () => {
    const { fetchImpl } = scriptedFetch(() => fakeResponse({ status: 501, bodyText: "{}" }));
    await expect(
      fetchPortfolioLatest({ baseUrl: "http://api.test", fetchImpl }),
    ).resolves.toEqual({ kind: "unavailable" });
  });

  it("fetchPortfolioLatest parses a valid snapshot", async () => {
    const { fetchImpl } = scriptedFetch(() =>
      fakeResponse({
        json: {
          items: [
            {
              rank: 1,
              referent: "P1",
              title: "Timely filing risk",
              impactCents: 117_141_515,
              provenance: "external_detection",
              priorityFormulaVersion: "dollar_impact@1",
              sourceWatermarkId: "wm_003",
            },
          ],
          rankingPolicy: "dollar_impact@1",
        },
      }),
    );
    const result = await fetchPortfolioLatest({ baseUrl: "http://api.test", fetchImpl });
    expect(result.kind).toBe("ok");
    if (result.kind === "ok") {
      expect(result.snapshot.items[0]?.referent).toBe("P1");
      expect(result.snapshot.items[0]?.drill.refinement).toEqual({
        op: "DrillInto",
        target: "P1",
      });
    }
  });

  it("fetchSessionLineage parses the DAG and reports drift on broken nodes", async () => {
    const onDrift = vi.fn();
    const { fetchImpl } = scriptedFetch(() =>
      fakeResponse({
        json: {
          nodes: [
            {
              turnId: "t1",
              investigationId: "inv_1",
              turnClass: "new_investigation",
              label: "T1",
              question: "Why did cash decline last week?",
            },
            { turnId: "t2" }, // broken
          ],
          edges: [],
        },
      }),
    );
    const lineage = await fetchSessionLineage("sess_live_1", {
      baseUrl: "http://api.test",
      fetchImpl,
      onDrift,
    });
    expect(lineage.nodes).toHaveLength(1);
    expect(onDrift).toHaveBeenCalledWith(
      expect.arrayContaining(["nodes[1].investigationId"]),
      expect.stringContaining("lineage"),
    );
  });

  it("fetchSessionList reads the wire spelling and drops rows it cannot draw", async () => {
    const onDrift = vi.fn();
    const { fetchImpl, calls } = scriptedFetch(() =>
      fakeResponse({
        json: {
          tenant: "demo",
          total: 9,
          limit: 50,
          sessions: [
            {
              session_id: "sess_a",
              title: "Why did cash decline last week?",
              created_at: "2026-08-08T09:00:00Z",
              last_activity: "2026-08-08T09:05:00Z",
              turn_count: 3,
            },
            { session_id: "sess_b" }, // broken: no title, no timestamps
          ],
        },
      }),
    );

    const page = await fetchSessionList(25, {
      baseUrl: "http://api.test",
      fetchImpl,
      onDrift,
    });

    expect(calls[0]?.url).toBe("http://api.test/v1/sessions?limit=25");
    expect(page.sessions).toEqual([
      {
        sessionId: "sess_a",
        title: "Why did cash decline last week?",
        createdAt: "2026-08-08T09:00:00Z",
        lastActivity: "2026-08-08T09:05:00Z",
        turnCount: 3,
      },
    ]);
    // `total` is the server's, not the page length: a truncated list must
    // not be able to claim it is the whole history.
    expect(page.total).toBe(9);
    expect(onDrift).toHaveBeenCalledWith(
      expect.arrayContaining(["sessions[1].title"]),
      "GET /v1/sessions",
    );
  });
});

/* ------------------------------------------------------------------ */
/* Session switching (re-join + thread rebuild)                        */
/* ------------------------------------------------------------------ */

describe("ApiDriver.resumeSession", () => {
  const LINEAGE_WIRE = {
    investigations: [
      {
        turn_id: "t1",
        investigation_id: "inv_1",
        turn_class: "new_investigation",
        question: "Why did cash decline last week?",
      },
      {
        turn_id: "t2",
        investigation_id: "inv_2",
        turn_class: "refinement",
        question: "Break that down by payer",
      },
    ],
    edges: [],
  };

  const investigationWire = (id: string, question: string) => ({
    investigation_id: id,
    session_id: "sess_live_1",
    turn_id: "t",
    turn_class: "new_investigation",
    status: "complete",
    question,
    created_at: "2026-08-08T09:00:00Z",
    findings: [
      {
        referent: `F_${id}`,
        title: "State Medicaid cash down",
        statement: "Posted cash fell $48,940.41.",
        grade: "direct",
        confidence: "high",
        metric_ids: [],
        values: [],
        suggested_refinements: [],
      },
    ],
    warnings: [],
    // The server projects the bundle from the turn's recorded trace on
    // this route too, so a restored turn opens a drawer with real working
    // in it instead of an empty one.
    evidence: {
      probes: [
        {
          id: "cash_by_payer",
          hash: "d94855a5",
          purpose: "Decline week versus prior week by payer",
          kind: "aggregation",
          metrics: [{ id: "cash_posted", contract_version: 1 }],
          cache_hit: false,
          rows: 12,
          limit: 12,
          truncated: false,
          suppressed_cells: 0,
          grade: "direct",
          duration_ms: 31,
        },
      ],
      reconciliation: { status: "passed", detail: null, summary: "status=passed" },
      warehouse_queries: 1,
      cache_hits: 0,
      zero_probe_turn: false,
      answer_grade: "direct",
    },
    chart_specs: [],
  });

  function scriptResume() {
    return scriptedFetch((call) => {
      if (call.url.endsWith("/v1/sessions")) return fakeResponse({ json: SESSION_WIRE });
      if (call.url.includes("/lineage")) return fakeResponse({ json: LINEAGE_WIRE });
      if (call.url.includes("/v1/investigations/inv_1")) {
        return fakeResponse({ json: investigationWire("inv_1", "Why did cash decline last week?") });
      }
      if (call.url.includes("/v1/investigations/inv_2")) {
        return fakeResponse({ json: investigationWire("inv_2", "Break that down by payer") });
      }
      return fakeResponse({ status: 404, json: {} });
    });
  }

  it("re-joins the named session and rebuilds its thread from stored turns", async () => {
    const onSession = vi.fn();
    const { fetchImpl, calls } = scriptResume();
    const driver = makeDriver(fetchImpl, { onSession });

    const resumed = await driver.resumeSession("sess_live_1");

    // Re-join carries the session id — the server's re-join semantics keep
    // the session's ORIGINAL watermark pin instead of re-pinning it.
    const open = calls.find((c) => c.url.endsWith("/v1/sessions"));
    expect(open?.method).toBe("POST");
    expect(open?.body).toMatchObject({ session_id: "sess_live_1" });
    expect(resumed.sessionId).toBe("sess_live_1");
    expect(resumed.watermark.id).toBe("wm_010");
    expect(onSession).toHaveBeenCalledTimes(1);

    expect(resumed.turns.map((t) => t.question)).toEqual([
      "Why did cash decline last week?",
      "Break that down by payer",
    ]);
    const first = resumed.turns[0];
    expect(first.investigationId).toBe("inv_1");
    expect(first.events.some((e) => e.type === "finding")).toBe(true);
    expect(first.events.at(-1)).toMatchObject({ type: "turn_complete", status: "complete" });

    // A restored turn carries its working: the drawer, the reconciliation
    // banner and the "no new queries" chip all read this one event, and
    // before the bundle reached this route they had nothing to read.
    const evidence = first.events.find((e) => e.type === "evidence");
    if (evidence?.type !== "evidence") throw new Error("no evidence event");
    expect(evidence.evidence.probes[0]).toMatchObject({
      probeId: "cash_by_payer",
      description: "Decline week versus prior week by payer",
      rowCount: 12,
      cacheHit: false,
      grade: "direct",
    });
    expect(evidence.evidence.reconciliation?.status).toBe("passed");
    expect(evidence.evidence.zeroProbeTurn).toBe(false);
  });

  it("fails the switch visibly rather than serving a thread with a turn missing", async () => {
    const { fetchImpl } = scriptedFetch((call) => {
      if (call.url.endsWith("/v1/sessions")) return fakeResponse({ json: SESSION_WIRE });
      if (call.url.includes("/lineage")) return fakeResponse({ json: LINEAGE_WIRE });
      if (call.url.includes("/v1/investigations/inv_1")) {
        return fakeResponse({ json: investigationWire("inv_1", "Why did cash decline last week?") });
      }
      return fakeResponse({ status: 500, bodyText: "" }); // inv_2 unreadable
    });
    const driver = makeDriver(fetchImpl);

    await expect(driver.resumeSession("sess_live_1")).rejects.toBeInstanceOf(Error);
  });

  it("resumes an empty session as an empty thread", async () => {
    const { fetchImpl } = scriptedFetch((call) =>
      call.url.endsWith("/v1/sessions")
        ? fakeResponse({ json: SESSION_WIRE })
        : fakeResponse({ json: { investigations: [], edges: [] } }),
    );
    const driver = makeDriver(fetchImpl);

    const resumed = await driver.resumeSession("sess_live_1");

    expect(resumed.turns).toEqual([]);
  });
});
