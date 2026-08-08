/**
 * POST-SSE reader. Native EventSource cannot POST, so turn submission
 * streams arrive via fetch + ReadableStream and are parsed here.
 *
 * Implements the SSE wire format subset the API emits:
 *   - `event:` / `data:` / `id:` fields; multi-line data joined with "\n"
 *   - comment lines (leading ":") are heartbeats — ignored
 *   - events are delimited by blank lines; CRLF and LF both accepted
 *   - incomplete trailing events at EOF are discarded (per spec)
 */

/** One decoded frame: the published kind plus its raw wire payload. */
export interface TurnFrame {
  kind: string;
  data: Record<string, unknown>;
}

export interface SseMessage {
  event: string;
  data: string;
  id?: string;
}

/**
 * Parse a byte stream into SSE messages. Handles chunk boundaries that
 * split lines, fields, or multi-byte UTF-8 sequences.
 */
export async function* parseSseStream(
  stream: ReadableStream<Uint8Array>,
): AsyncGenerator<SseMessage, void, undefined> {
  const reader = stream.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  let eventType = "";
  let dataLines: string[] = [];
  let id: string | undefined;

  const flush = (): SseMessage | null => {
    if (dataLines.length === 0) {
      // Per spec: no data → nothing dispatched; event type resets anyway.
      eventType = "";
      return null;
    }
    const message: SseMessage = {
      event: eventType || "message",
      data: dataLines.join("\n"),
      ...(id !== undefined ? { id } : {}),
    };
    eventType = "";
    dataLines = [];
    return message;
  };

  const handleLine = (rawLine: string): SseMessage | null => {
    // Strip a single trailing CR (CRLF normalization).
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line === "") return flush();
    if (line.startsWith(":")) return null; // comment / heartbeat
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        eventType = value;
        break;
      case "data":
        dataLines.push(value);
        break;
      case "id":
        if (!value.includes("\0")) id = value;
        break;
      default:
        // "retry" and unknown fields are ignored by this client.
        break;
    }
    return null;
  };

  try {
    let first = true;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      let text = decoder.decode(value, { stream: true });
      if (first) {
        // Strip a UTF-8 BOM if the server sent one.
        if (text.startsWith("﻿")) text = text.slice(1);
        first = false;
      }
      buffer += text;
      let newline: number;
      while ((newline = buffer.indexOf("\n")) !== -1) {
        const line = buffer.slice(0, newline);
        buffer = buffer.slice(newline + 1);
        const message = handleLine(line);
        if (message) yield message;
      }
    }
    // EOF: process a final unterminated line, but per spec an event that
    // was never followed by a blank line is NOT dispatched.
    if (buffer.length > 0) handleLine(buffer);
  } finally {
    reader.releaseLock();
  }
}

/**
 * Decode one SSE message into a raw wire frame. This layer does NOT map
 * to the UI event union — that is `parseTurnFrame`'s job in lib/contract.ts,
 * which needs the session pin to render a header. Returns null for a body
 * that is not a JSON object (a malformed frame is skipped, never crashed
 * on); unknown *kinds* are passed through, because deciding what is
 * published is the contract layer's call, not the transport's.
 */
export function decodeTurnFrame(message: SseMessage): TurnFrame | null {
  let payload: unknown;
  try {
    payload = JSON.parse(message.data);
  } catch {
    return null;
  }
  if (typeof payload !== "object" || payload === null || Array.isArray(payload)) return null;
  return { kind: message.event, data: payload as Record<string, unknown> };
}

export interface StreamTurnOptions {
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
  headers?: Record<string, string>;
}

/**
 * A non-2xx response to the turn POST. Carries the raw body text so the
 * caller can decode the API's ErrorEnvelope ({code, message, correlation_id}).
 */
export class SseHttpError extends Error {
  constructor(
    readonly status: number,
    readonly bodyText: string,
  ) {
    super(`turn submission failed: HTTP ${status}`);
    this.name = "SseHttpError";
  }
}

/**
 * POST a turn and stream typed events. This is the transport the real
 * HTTP driver will use in M8+ — the mock driver produces the same
 * TurnEvent union without the network hop.
 */
export async function streamTurnEvents(
  url: string,
  body: unknown,
  onFrame: (frame: TurnFrame) => void,
  options: StreamTurnOptions = {},
): Promise<void> {
  const doFetch = options.fetchImpl ?? fetch;
  const response = await doFetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      ...options.headers,
    },
    body: JSON.stringify(body),
    signal: options.signal,
  });
  if (!response.ok) {
    const bodyText = await response.text().catch(() => "");
    throw new SseHttpError(response.status, bodyText);
  }
  if (!response.body) {
    throw new Error("turn submission returned no body stream");
  }
  for await (const message of parseSseStream(response.body)) {
    const frame = decodeTurnFrame(message);
    if (frame) onFrame(frame);
  }
}
