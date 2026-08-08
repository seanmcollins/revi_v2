import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiDriver,
  fetchHealth,
  fetchPortfolioLatest,
  fetchSessionLineage,
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

const COMPLETE_FRAMES = [
  sse("stage", { stage: "classified", status: "completed", detail: "NEW_INVESTIGATION (0.98)" }),
  sse("narrative_delta", { text: "Payer cash fell 12.7% week over week." }),
  sse("turn_complete", { investigationId: "inv_1", status: "complete" }),
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
  it("defaults to mock unless NEXT_PUBLIC_REVI_DRIVER is exactly 'api'", () => {
    // The env var is inlined at build time; in tests it is unset.
    expect(resolveDriverKind()).toBe("mock");
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
    expect(turn?.body?.refinements).toEqual([{ op: "DrillInto", target: "F2" }]);
    expect(turn?.body?.clarification_response).toBe("Last week");
    expect(turn?.body?.re_anchor).toBe(true);
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
    expect(events.map((e) => e.type)).toEqual(["stage", "narrative_delta", "turn_complete"]);
    expect(onConnectionState).toHaveBeenLastCalledWith("online");
  });

  it("drops malformed frames, console.errors the field path, and keeps streaming", async () => {
    const onContractDrift = vi.fn();
    const frames = [
      sse("finding", { finding: { referent: { value: "F1", kind: "finding" } } }), // missing title etc.
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
    expect(events.map((e) => e.type)).toEqual(["stage", "narrative_delta", "turn_complete"]);
    expect(onContractDrift).toHaveBeenCalledWith(
      expect.arrayContaining(["finding.title", "finding.statement"]),
      'sse frame "finding"',
    );
    expect(console.error).toHaveBeenCalledWith(
      expect.stringContaining('missing required field "finding.title"'),
    );
  });

  it("turns a malformed turn_complete into a visible CONTRACT_DRIFT error (no silent hang)", async () => {
    const frames = [
      sse("narrative_delta", { text: "…" }),
      sse("turn_complete", { status: "complete" }), // investigationId missing
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
  investigationId: "inv_7",
  status: "complete",
  turnClass: "new_investigation",
  findings: [
    {
      referent: { value: "F1", kind: "finding" },
      title: "State Medicaid cash down",
      statement: "Fell 52.9% WoW.",
      metricRefs: [],
      values: {},
      grade: "direct",
      directionOfGood: "up_is_good",
      confidence: "high",
      suggestedRefinements: [],
    },
    {
      referent: { value: "F2", kind: "finding" },
      title: "Atlas Commercial volume down",
      statement: "Fell 18.3% WoW.",
      metricRefs: [],
      values: {},
      grade: "direct",
      directionOfGood: "up_is_good",
      confidence: "high",
      suggestedRefinements: [],
    },
  ],
  narrative: "Payer cash fell. Two payers explain most of it.",
};

describe("ApiDriver reconnect tolerance", () => {
  it("recovers a dropped stream via same-key JSON replay, duplicate-free", async () => {
    const droppedFrames = [
      sse("finding", FULL_TURN_RESPONSE.findings[0] ? { finding: FULL_TURN_RESPONSE.findings[0] } : {}),
      sse("narrative_delta", { text: "Payer cash fell. " }),
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
      sse("stage", { stage: "classified", status: "completed", investigationId: "inv_9" }),
    ];
    const response = { ...structuredClone(FULL_TURN_RESPONSE), investigationId: "inv_9" };
    const { fetchImpl, calls } = scriptedFetch((call) => {
      if (call.url.endsWith("/v1/sessions")) return fakeResponse({ json: SESSION_WIRE });
      if (call.accept === "text/event-stream") {
        return fakeResponse({ stream: streamOf(droppedFrames, true) });
      }
      return fakeResponse({ json: response });
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
        return fakeResponse({ stream: streamOf([sse("narrative_delta", { text: "Half " })]) });
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
});
