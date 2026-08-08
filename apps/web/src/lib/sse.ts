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

import { TURN_EVENT_TYPES, type TurnEvent } from "@/lib/types";

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
 * Decode one SSE message into a typed TurnEvent. Returns null for event
 * names outside the typed union (forward compatibility: unknown events
 * are skipped, never crashed on).
 */
export function decodeTurnEvent(message: SseMessage): TurnEvent | null {
  if (!TURN_EVENT_TYPES.has(message.event)) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(message.data);
  } catch {
    return null;
  }
  if (typeof payload !== "object" || payload === null) return null;
  // The server emits the event type both as the SSE event name and inside
  // the payload; the name wins so a mismatched body is detectable.
  return { ...(payload as Record<string, unknown>), type: message.event } as TurnEvent;
}

export interface StreamTurnOptions {
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
  headers?: Record<string, string>;
}

/**
 * POST a turn and stream typed events. This is the transport the real
 * HTTP driver will use in M8+ — the mock driver produces the same
 * TurnEvent union without the network hop.
 */
export async function streamTurnEvents(
  url: string,
  body: unknown,
  onEvent: (event: TurnEvent) => void,
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
    throw new Error(`turn submission failed: HTTP ${response.status}`);
  }
  if (!response.body) {
    throw new Error("turn submission returned no body stream");
  }
  for await (const message of parseSseStream(response.body)) {
    const event = decodeTurnEvent(message);
    if (event) onEvent(event);
  }
}
