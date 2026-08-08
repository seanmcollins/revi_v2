import { describe, expect, it } from "vitest";

import { decodeTurnEvent, parseSseStream, type SseMessage } from "@/lib/sse";

function streamOf(chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(typeof chunk === "string" ? encoder.encode(chunk) : chunk);
      }
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<SseMessage[]> {
  const out: SseMessage[] = [];
  for await (const message of parseSseStream(stream)) out.push(message);
  return out;
}

describe("parseSseStream", () => {
  it("parses a single complete event", async () => {
    const messages = await collect(
      streamOf(['event: stage\ndata: {"stage":"classified"}\n\n']),
    );
    expect(messages).toEqual([{ event: "stage", data: '{"stage":"classified"}' }]);
  });

  it("parses multiple events in one chunk", async () => {
    const messages = await collect(
      streamOf(['event: stage\ndata: {"n":1}\n\nevent: finding\ndata: {"n":2}\n\n']),
    );
    expect(messages.map((m) => m.event)).toEqual(["stage", "finding"]);
    expect(messages.map((m) => m.data)).toEqual(['{"n":1}', '{"n":2}']);
  });

  it("reassembles an event split across chunk boundaries mid-line", async () => {
    const messages = await collect(
      streamOf(["event: narrative_del", 'ta\ndata: {"text":"he', 'llo"}\n', "\n"]),
    );
    expect(messages).toEqual([{ event: "narrative_delta", data: '{"text":"hello"}' }]);
  });

  it("reassembles a multi-byte UTF-8 character split across chunks", async () => {
    const encoded = new TextEncoder().encode('data: {"text":"−12.7%"}\n\n');
    // Split inside the 3-byte U+2212 MINUS SIGN (starts at index 15).
    const cut = 16;
    const messages = await collect(streamOf([encoded.slice(0, cut), encoded.slice(cut)]));
    expect(messages[0]?.data).toBe('{"text":"−12.7%"}');
  });

  it("handles CRLF line endings", async () => {
    const messages = await collect(
      streamOf(['event: stage\r\ndata: {"a":1}\r\n\r\n']),
    );
    expect(messages).toEqual([{ event: "stage", data: '{"a":1}' }]);
  });

  it("ignores comment/heartbeat lines", async () => {
    const messages = await collect(
      streamOf([": keep-alive\n\n: ping\nevent: stage\ndata: {}\n\n"]),
    );
    expect(messages).toEqual([{ event: "stage", data: "{}" }]);
  });

  it("joins multi-line data fields with newlines", async () => {
    const messages = await collect(streamOf(["data: line1\ndata: line2\n\n"]));
    expect(messages).toEqual([{ event: "message", data: "line1\nline2" }]);
  });

  it("defaults the event type to message", async () => {
    const messages = await collect(streamOf(["data: x\n\n"]));
    expect(messages[0]?.event).toBe("message");
  });

  it("captures event ids", async () => {
    const messages = await collect(streamOf(["id: 7\nevent: stage\ndata: {}\n\n"]));
    expect(messages[0]?.id).toBe("7");
  });

  it("discards an unterminated trailing event at EOF (per spec)", async () => {
    const messages = await collect(streamOf(["event: stage\ndata: {}"]));
    expect(messages).toEqual([]);
  });

  it("dispatches nothing for an event with no data", async () => {
    const messages = await collect(streamOf(["event: stage\n\n"]));
    expect(messages).toEqual([]);
  });

  it("strips exactly one leading space from field values", async () => {
    const messages = await collect(streamOf(["data:  two spaces\n\n"]));
    expect(messages[0]?.data).toBe(" two spaces");
  });
});

describe("decodeTurnEvent", () => {
  it("decodes a known event into the typed union", () => {
    const event = decodeTurnEvent({
      event: "narrative_delta",
      data: '{"text":"hi"}',
    });
    expect(event).toEqual({ type: "narrative_delta", text: "hi" });
  });

  it("the SSE event name wins over a mismatched payload type", () => {
    const event = decodeTurnEvent({
      event: "warning",
      data: '{"type":"finding","code":"X","message":"m","severity":"info"}',
    });
    expect(event?.type).toBe("warning");
  });

  it("returns null for unknown event names", () => {
    expect(decodeTurnEvent({ event: "mystery", data: "{}" })).toBeNull();
  });

  it("returns null for malformed JSON", () => {
    expect(decodeTurnEvent({ event: "stage", data: "{not json" })).toBeNull();
  });

  it("returns null for non-object payloads", () => {
    expect(decodeTurnEvent({ event: "stage", data: '"str"' })).toBeNull();
  });
});
