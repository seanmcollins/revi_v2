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
  /**
   * A typed FIRST turn — an explicit `TypedInvestigationSpec` (metric ids,
   * dimensions, filters, window/basis) that opens a NEW investigation with
   * no parent and no model call. This is what a portfolio card's
   * `drill_spec` is, and what a chart click posts when the session has no
   * prior answer to refine. Passed to the wire verbatim: it is already the
   * published shape, so translating it would only be a chance to get it
   * wrong.
   */
  spec?: Record<string, unknown>;
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
