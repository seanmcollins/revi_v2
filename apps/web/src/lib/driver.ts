/**
 * The driver seam. The UI talks to a TurnDriver; swapping the mock for the
 * real API is exchanging this implementation only — the store, the event
 * union, and every component are identical for both.
 *
 * The real driver (lib/apiDriver.ts) wraps `streamTurnEvents` from lib/sse.ts:
 *   POST /v1/sessions/{sid}/turns  with Accept: text/event-stream.
 */

import type { Refinement, TurnEvent } from "@/lib/types";

/** Which implementation sits behind the seam. */
export type DriverKind = "mock" | "api";

/** Connection state machine for the live API: connecting → online ⇄ offline. */
export type ConnectionState = "connecting" | "online" | "offline";

export interface TurnSubmission {
  utterance?: string;
  refinements?: Refinement[];
  /** Reply to a pending clarification — sent as `clarification_response`. */
  clarificationResponse?: string;
  /** Ask the server to re-anchor this session to the freshest watermark. */
  reAnchor?: boolean;
}

export interface TurnDriver {
  submit(
    submission: TurnSubmission,
    emit: (event: TurnEvent) => void,
    signal?: AbortSignal,
  ): Promise<void>;
}
