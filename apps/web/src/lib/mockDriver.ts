/**
 * Mock TurnDriver — replays the golden five-turn conversation through the
 * SAME typed TurnEvent pipeline the real POST-SSE driver will use (see
 * lib/sse.ts). Swapping in the real API later means replacing this class
 * with one that calls `streamTurnEvents` — nothing else changes.
 *
 * Pacing mimics the real system's tens-of-seconds latency at demo scale:
 * stage events arrive with visible gaps and the narrative streams in
 * word-sized deltas.
 */

import type { TurnDriver, TurnSubmission } from "@/lib/driver";
import { GOLDEN_TURNS } from "@/lib/mock/golden";
import { clarificationEvents, PR3_EVENTS } from "@/lib/mock/definitions";
import type { TurnEvent } from "@/lib/types";

const PR3_TRIGGER = /what\s*(?:is|'s)?\s*pr\s*-?\s*3\b|^pr\s*3\??$/i;

function delayFor(event: TurnEvent, pace: number): number {
  if (pace === 0) return 0;
  switch (event.type) {
    case "stage":
      return (event.stage === "executing" ? 420 : 260) * pace;
    case "narrative_delta":
      return 0; // narrative pacing happens per chunk below
    case "finding":
      return 240 * pace;
    case "chart_spec":
      return 200 * pace;
    default:
      return 140 * pace;
  }
}

const sleep = (ms: number): Promise<void> =>
  ms <= 0 ? Promise.resolve() : new Promise((resolve) => setTimeout(resolve, ms));

/** Split narrative text into small word-group deltas for streaming. */
export function chunkNarrative(text: string, wordsPerChunk = 4): string[] {
  const words = text.split(" ");
  const chunks: string[] = [];
  for (let i = 0; i < words.length; i += wordsPerChunk) {
    const slice = words.slice(i, i + wordsPerChunk).join(" ");
    chunks.push(i === 0 ? slice : ` ${slice}`);
  }
  return chunks;
}

export class MockDriver implements TurnDriver {
  /** How far through the golden conversation this session has advanced. */
  private goldenProgress = 0;
  /** Pace multiplier: 1 = demo speed, 0 = instant (tests). */
  constructor(private readonly pace: number = 1) {}

  nextGoldenQuestion(): string | undefined {
    return GOLDEN_TURNS[this.goldenProgress]?.question;
  }

  private resolveEvents(submission: TurnSubmission): TurnEvent[] {
    const text = submission.utterance?.trim() ?? "";
    if (PR3_TRIGGER.test(text)) return PR3_EVENTS;

    const next = GOLDEN_TURNS[this.goldenProgress];
    if (next && next.trigger.test(text)) {
      this.goldenProgress += 1;
      return next.events;
    }
    // A later golden turn asked out of order, or anything unscripted →
    // first-class clarification. The interpreter never guesses.
    return clarificationEvents(text, next?.question);
  }

  async submit(
    submission: TurnSubmission,
    emit: (event: TurnEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const events = this.resolveEvents(submission);
    // Pace against a wall-clock schedule with catch-up: if the browser
    // throttles timers (background tab), a late wake flushes everything
    // due instead of compounding the delay.
    let scheduledAt = Date.now();
    const pacedEmit = async (event: TurnEvent, delayMs: number): Promise<boolean> => {
      scheduledAt += delayMs;
      await sleep(scheduledAt - Date.now());
      if (signal?.aborted) return false;
      emit(event);
      return true;
    };
    for (const event of events) {
      if (signal?.aborted) return;
      if (event.type === "narrative_delta") {
        // Emit word-group deltas, but coalesce everything already due into
        // ONE store update per wake — a long throttle pause must not turn
        // into hundreds of nested React updates.
        const chunks = chunkNarrative(event.text);
        let i = 0;
        while (i < chunks.length) {
          let text = chunks[i];
          scheduledAt += 26 * this.pace;
          i += 1;
          while (i < chunks.length && scheduledAt <= Date.now()) {
            text += chunks[i];
            scheduledAt += 26 * this.pace;
            i += 1;
          }
          await sleep(scheduledAt - Date.now());
          if (signal?.aborted) return;
          emit({ type: "narrative_delta", text });
        }
      } else if (!(await pacedEmit(event, delayFor(event, this.pace)))) {
        return;
      }
    }
  }

  resetProgress(): void {
    this.goldenProgress = 0;
  }
}

export const GOLDEN_QUESTIONS: string[] = GOLDEN_TURNS.map((t) => t.question);
