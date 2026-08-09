import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TurnDriver } from "@/lib/driver";
import { investigationLinkFor, sessionLinkFor } from "@/lib/links";
import { REFERENCE_QUESTIONS, REFERENCE_TURNS } from "@/lib/mock/reference";
import { chunkNarrative, MockDriver } from "@/lib/mockDriver";
import {
  applyEventToAnswer,
  emptyAnswer,
  useSessionStore,
  type AnswerState,
} from "@/lib/store";
import { minGrade, STAGE_ORDER, type TurnEvent } from "@/lib/types";

function reduce(events: TurnEvent[], from: AnswerState = emptyAnswer()): AnswerState {
  return events.reduce(applyEventToAnswer, from);
}

describe("applyEventToAnswer (pure reducer)", () => {
  it("starts with all stages pending", () => {
    const answer = emptyAnswer();
    expect(answer.stages).toHaveLength(STAGE_ORDER.length);
    expect(answer.stages.every((s) => s.state === "pending")).toBe(true);
  });

  it("marks earlier stages done when a later stage arrives", () => {
    const answer = reduce([
      { type: "stage", stage: "calculating", status: "started" },
    ]);
    const byId = new Map(answer.stages.map((s) => [s.stage, s.state]));
    expect(byId.get("classified")).toBe("done");
    expect(byId.get("executing")).toBe("done");
    expect(byId.get("calculating")).toBe("active");
    expect(byId.get("narrating")).toBe("pending");
  });

  it("tracks probe progress and cache hits on the executing stage", () => {
    const answer = reduce([
      { type: "stage", stage: "executing", status: "started", probesDone: 1, probesTotal: 2, cacheHits: 1 },
    ]);
    const executing = answer.stages.find((s) => s.stage === "executing");
    expect(executing).toMatchObject({ probesDone: 1, probesTotal: 2, cacheHits: 1 });
    expect(answer.cacheHits).toBe(1);
  });

  it("records skipped stages (zero-probe paths)", () => {
    const answer = reduce([
      { type: "stage", stage: "executing", status: "skipped" },
    ]);
    expect(answer.stages.find((s) => s.stage === "executing")?.state).toBe("skipped");
  });

  it("accumulates narrative deltas in order", () => {
    const answer = reduce([
      { type: "narrative_delta", text: "Payer cash fell" },
      { type: "narrative_delta", text: " −12.7%." },
    ]);
    expect(answer.narrative).toBe("Payer cash fell −12.7%.");
  });

  it("collects findings and charts in arrival order", () => {
    const t1 = REFERENCE_TURNS[0].events;
    const answer = reduce(t1);
    expect(answer.findings.map((f) => f.referent.value)).toEqual(["F1", "F2", "F3"]);
    expect(answer.charts).toHaveLength(1);
    expect(answer.findings[0].impactCents).toBe(-9_909_308);
  });

  it("replaces a finding the answer already has instead of drawing it twice", () => {
    // A referent identifies a finding, so a second event carrying one is
    // the SAME finding said again. The terminal frame DOES say some of them
    // again: the governed display names that correct their titles are only
    // known once the turn is finished, so a streamed frame reads "$195,873.92
    // dnfb dollars" and the completed answer reads "…discharged not final
    // billed". Appending would render the card twice.
    const t1 = REFERENCE_TURNS[0].events;
    const answer = reduce(t1);
    const corrected = {
      ...answer.findings[0],
      title: "Corrected by the pack's governed display name",
    };
    const after = applyEventToAnswer(answer, { type: "finding", finding: corrected });
    expect(after.findings).toHaveLength(answer.findings.length);
    expect(after.findings[0].title).toBe("Corrected by the pack's governed display name");
    expect(after.findings.map((f) => f.referent.value)).toEqual(["F1", "F2", "F3"]);
  });

  it("replaces a chart it already has, keyed by the spec id", () => {
    const answer = reduce(REFERENCE_TURNS[0].events);
    const corrected = { ...answer.charts[0], title: "Governed measure by payer" };
    const after = applyEventToAnswer(answer, { type: "chart_spec", spec: corrected });
    expect(after.charts).toHaveLength(1);
    expect(after.charts[0].title).toBe("Governed measure by payer");
  });

  it("clarification is a first-class successful state, not an error", () => {
    const answer = reduce([
      {
        type: "clarification",
        clarification: { question: "Which did you mean?", options: ["A", "B"] },
      },
      { type: "turn_complete", investigationId: "i", status: "clarification_required" },
    ]);
    expect(answer.status).toBe("clarification");
    expect(answer.error).toBeUndefined();
  });

  it("turn_complete finalizes stages and carries grade + governed provenance", () => {
    const answer = reduce(REFERENCE_TURNS[2].events);
    expect(answer.status).toBe("complete");
    expect(answer.answerGrade).toBe("proxy");
    // T3 pivots onto two governed contracts, so there is no single
    // headline metric — the block lists what ran and elects nothing.
    expect(answer.metric?.primary).toBeUndefined();
    expect(answer.metric?.metrics.map((m) => m.id)).toEqual([
      "denied_dollars",
      "denial_events",
    ]);
    expect(answer.stages.some((s) => s.state === "active")).toBe(false);
  });

  it("a META turn's provenance block is empty — nothing governed was measured", () => {
    // T5 answers from the trace: zero probes, so no contract stands behind
    // it. The block still arrives (the pack was still pinned); what must
    // not happen is a metric it never read appearing in it.
    const answer = reduce(REFERENCE_TURNS[4].events);
    expect(answer.metric?.metrics).toEqual([]);
    expect(answer.metric?.primary).toBeUndefined();
  });

  it("error events set error state", () => {
    const answer = reduce([
      { type: "error", code: "WATERMARK_STALE", message: "stale" },
    ]);
    expect(answer.status).toBe("error");
    expect(answer.error).toEqual({ code: "WATERMARK_STALE", message: "stale" });
  });

  it("does not mutate the previous answer (immutability)", () => {
    const before = emptyAnswer();
    const frozen = JSON.stringify(before);
    reduce([{ type: "narrative_delta", text: "x" }], before);
    expect(JSON.stringify(before)).toBe(frozen);
  });
});

describe("reference fixtures — invariants the UI depends on", () => {
  it("T1 header matches §10.3 verbatim values", () => {
    const headerEvent = REFERENCE_TURNS[0].events.find((e) => e.type === "context_header");
    if (headerEvent?.type !== "context_header") throw new Error("missing header");
    expect(headerEvent.header.window).toMatchObject({
      start: "2026-07-27",
      end: "2026-08-02",
      basis: "post",
    });
    expect(headerEvent.header.watermark.loadedAt).toBe("2026-08-03 04:10");
  });

  it("T2 reconciliation: the split's children reconciled to the parent", () => {
    const evidence = REFERENCE_TURNS[1].events.find((e) => e.type === "evidence");
    if (evidence?.type !== "evidence") throw new Error("missing evidence");
    const reconciliation = evidence.evidence.reconciliation;
    // The verdict is a recorded status and its raw summary — the parent
    // total and the row sum are computed inside the reconcile operator
    // and never leave it, so the fixture no longer claims them either.
    expect(reconciliation?.status).toBe("passed");
    expect(reconciliation?.summary).toBe("status=passed");
    // ...and the split reused T1's frames rather than re-querying them.
    expect(evidence.evidence.cacheHits).toBeGreaterThan(0);
  });

  it("T3 carries proxy grade per the grade law (min-propagation)", () => {
    const complete = REFERENCE_TURNS[2].events.find((e) => e.type === "turn_complete");
    if (complete?.type !== "turn_complete") throw new Error("missing complete");
    expect(complete.answerGrade).toBe(minGrade("direct", "proxy", "direct"));
  });

  it("T4 evidence contains a cache hit (primary side reused)", () => {
    const evidence = REFERENCE_TURNS[3].events.find((e) => e.type === "evidence");
    if (evidence?.type !== "evidence") throw new Error("missing evidence");
    expect(evidence.evidence.probes.some((p) => p.cacheHit)).toBe(true);
  });

  it("T5 is a zero-probe META turn", () => {
    const evidence = REFERENCE_TURNS[4].events.find((e) => e.type === "evidence");
    if (evidence?.type !== "evidence") throw new Error("missing evidence");
    expect(evidence.evidence.zeroProbeTurn).toBe(true);
    expect(evidence.evidence.probes).toHaveLength(0);
  });
});

describe("session store + mock driver (the real event pipeline)", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.getState().setDriver(new MockDriver(0));
    // The store now opens on the ENV driver kind ("api" by default) rather
    // than on a hardcoded "mock", so a live deployment stops flashing a
    // "Mock driver" pill and a degraded badge before its first health
    // poll. This block drives the mock fixture on purpose, so it says so.
    useSessionStore.setState({
      connection: { mode: "mock", state: "online", healthChecked: true },
    });
  });

  it("streams the reference T1 into a complete turn", async () => {
    await useSessionStore.getState().submit({ utterance: "Why did cash decline last week?" });
    const turn = useSessionStore.getState().turns[0];
    expect(turn.answer.status).toBe("complete");
    expect(turn.answer.findings).toHaveLength(3);
    expect(turn.answer.narrative).toContain("$193,525.79");
    expect(useSessionStore.getState().streamingTurnId).toBeNull();
  });

  it("mirrors referents into the registry", async () => {
    await useSessionStore.getState().submit({ utterance: "Why did cash decline last week?" });
    await useSessionStore.getState().submit({ utterance: "Break that down by payer" });
    const { referents } = useSessionStore.getState();
    expect(referents["F1"]).toMatchObject({ turnId: "turn_1" });
    expect(referents["F2"].impactCents).toBe(-4_894_041);
    // T2 chart rows register as table_row referents
    expect(referents["F6"].referent.kind).toBe("table_row");
    expect(referents["F6"].label).toBe("State Medicaid");
  });

  it("unscripted input yields a first-class clarification turn", async () => {
    await useSessionStore.getState().submit({ utterance: "forecast next year in detail" });
    const turn = useSessionStore.getState().turns[0];
    expect(turn.answer.status).toBe("clarification");
    expect(turn.answer.clarification?.options.length).toBeGreaterThan(0);
  });

  it("emitRefinement queues typed refinements (chart-click channel)", () => {
    useSessionStore.getState().emitRefinement(
      { op: "DrillInto", target: "F6" },
      { turnId: "turn_1", referent: "F6" },
    );
    const pending = useSessionStore.getState().pendingRefinements;
    expect(pending).toHaveLength(1);
    expect(pending[0].refinement).toEqual({ op: "DrillInto", target: "F6" });
  });

  it("persists per-answer feedback triage", () => {
    useSessionStore.getState().setFeedback("turn_1", "fix");
    expect(useSessionStore.getState().feedback["turn_1"]).toBe("fix");
  });

  it("watermark banner: stay pinned keeps the session watermark", () => {
    useSessionStore.getState().simulateWatermarkRefresh();
    expect(useSessionStore.getState().watermarkBanner.visible).toBe(true);
    useSessionStore.getState().resolveWatermarkBanner("stay_pinned");
    expect(useSessionStore.getState().watermark.id).toBe("wm_003");
  });

  it("watermark banner: re-anchor advances the epoch", () => {
    useSessionStore.getState().simulateWatermarkRefresh();
    useSessionStore.getState().resolveWatermarkBanner("re_anchor");
    expect(useSessionStore.getState().watermark.id).toBe("wm_004");
  });
});

describe("newChat() — session lifecycle", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.getState().setDriver(new MockDriver(0));
  });

  it("clears the thread and asks the driver for a new session", async () => {
    await useSessionStore.getState().submit({ utterance: "Why did cash decline last week?" });
    expect(useSessionStore.getState().turns).toHaveLength(1);

    await useSessionStore.getState().newChat();

    expect(useSessionStore.getState().turns).toHaveLength(0);
    expect(useSessionStore.getState().referents).toEqual({});
    expect(useSessionStore.getState().newChatPending).toBe(false);
  });

  it("resets the mock driver's reference progress, so T1 can be re-asked", async () => {
    await useSessionStore.getState().submit({ utterance: "Why did cash decline last week?" });
    await useSessionStore.getState().submit({ utterance: "Break that down by payer" });
    expect(useSessionStore.getState().turns).toHaveLength(2);

    await useSessionStore.getState().newChat();
    await useSessionStore.getState().submit({ utterance: "Why did cash decline last week?" });

    // T1 matched again (not a clarification) — proof the mock driver's
    // local progress rewound along with the thread.
    expect(useSessionStore.getState().turns[0].answer.status).toBe("complete");
  });

  it("is a no-op while a turn is streaming (single-flight)", async () => {
    const driver = new MockDriver(0);
    const newSessionSpy = vi.spyOn(driver, "newSession");
    useSessionStore.getState().setDriver(driver);
    useSessionStore.setState({ streamingTurnId: "turn_mid_flight" });

    await useSessionStore.getState().newChat();

    expect(newSessionSpy).not.toHaveBeenCalled();
    expect(useSessionStore.getState().turns).toEqual([]); // reset() never ran
  });

  it("reset-routes-to-newChat in api mode: the driver is asked for a fresh session", async () => {
    const newSession = vi.fn().mockResolvedValue(undefined);
    const fakeDriver: TurnDriver = { submit: vi.fn().mockResolvedValue(undefined), newSession };
    useSessionStore.getState().setDriver(fakeDriver);
    useSessionStore.setState({
      connection: { mode: "api", state: "online" },
      turns: [{ id: "turn_1", index: 0, submission: {}, answer: emptyAnswer() }],
      referents: {
        F1: {
          referent: { value: "F1", kind: "finding" },
          turnId: "turn_1",
          label: "State Medicaid cash down",
        },
      },
    });

    await useSessionStore.getState().newChat();

    // The old backend session is never silently continued — the driver's
    // cache is dropped, not just the local thread.
    expect(newSession).toHaveBeenCalledTimes(1);
    expect(useSessionStore.getState().turns).toEqual([]);
    expect(useSessionStore.getState().referents).toEqual({});
  });

  /**
   * The gap between "New chat" and the first question.
   *
   * Nothing is minted by the button — the server creates a session when a
   * turn arrives — so for that whole interval there is no session. What the
   * store must NOT do is keep the abandoned session's identity sitting in
   * state, because the header reads its watermark straight out of there:
   * a specific data-load date and load time, in the analyst's most-trusted
   * line, pinned by a thread that was just discarded.
   */
  it("holds no session across the gap, and claims no pin it cannot stand behind", async () => {
    const newSession = vi.fn().mockResolvedValue(undefined);
    const fakeDriver: TurnDriver = { submit: vi.fn().mockResolvedValue(undefined), newSession };
    useSessionStore.getState().setDriver(fakeDriver);
    useSessionStore.getState().adoptSession({
      sessionId: "sess_live_1",
      watermark: { id: "wm_9", loadedAt: "2026-08-08 04:00", newestDataDate: "2026-08-07" },
      pack: { packId: "base-rcm", version: "1.0.0" },
    });
    useSessionStore.setState({ connection: { mode: "api", state: "online" } });
    expect(useSessionStore.getState().sessionLive).toBe(true);

    await useSessionStore.getState().newChat();

    // `sessionLive` is what every surface gates the pin on.
    expect(useSessionStore.getState().sessionLive).toBe(false);
    // The connection is untouched: no request was made, so "connecting" —
    // which is what this used to set — would be the same fabrication one
    // layer down. Pressing a button is not a network event.
    expect(useSessionStore.getState().connection.state).toBe("online");
  });

  it("takes a real pin again the moment the first turn brings one back", async () => {
    const newSession = vi.fn().mockResolvedValue(undefined);
    const fakeDriver: TurnDriver = { submit: vi.fn().mockResolvedValue(undefined), newSession };
    useSessionStore.getState().setDriver(fakeDriver);
    useSessionStore.setState({ connection: { mode: "api", state: "online" } });

    await useSessionStore.getState().newChat();
    expect(useSessionStore.getState().sessionLive).toBe(false);

    // What `ApiDriver.onSession` does once the first turn bootstraps.
    useSessionStore.getState().adoptSession({
      sessionId: "sess_live_2",
      watermark: { id: "wm_10", loadedAt: "2026-08-09 04:00", newestDataDate: "2026-08-08" },
      pack: { packId: "base-rcm", version: "1.0.0" },
    });

    expect(useSessionStore.getState().sessionLive).toBe(true);
    expect(useSessionStore.getState().watermark.id).toBe("wm_10");
  });
});

/**
 * A driver whose `submit` resolves each turn on a real (macro)task tick, so
 * a test can prove calls are serialized — never in flight at the same
 * time — rather than merely appearing ordered because nothing async
 * separates them.
 */
function makeSequencedFakeDriver(outcomes: Array<"complete" | "clarification">): {
  driver: TurnDriver;
  calls: string[];
  newSession: ReturnType<typeof vi.fn>;
  maxConcurrent: () => number;
} {
  const calls: string[] = [];
  let inFlight = 0;
  let maxConcurrent = 0;
  let next = 0;
  const newSession = vi.fn().mockResolvedValue(undefined);
  const driver: TurnDriver = {
    newSession,
    submit: async (submission, emit) => {
      inFlight += 1;
      maxConcurrent = Math.max(maxConcurrent, inFlight);
      calls.push(submission.utterance ?? "");
      await new Promise((resolve) => setTimeout(resolve, 0));
      const outcome = outcomes[next];
      next += 1;
      if (outcome === "clarification") {
        emit({
          type: "clarification",
          clarification: { question: "Which did you mean?", options: ["A", "B"] },
        });
        emit({ type: "turn_complete", investigationId: "inv_c", status: "clarification_required" });
      } else {
        emit({ type: "turn_complete", investigationId: "inv_ok", status: "complete" });
      }
      inFlight -= 1;
    },
  };
  return { driver, calls, newSession, maxConcurrent: () => maxConcurrent };
}

describe("replayReference() — sequential reference-demo replay", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
  });

  it("starts a fresh session, then submits all five reference turns in order, one at a time", async () => {
    const { driver, calls, newSession, maxConcurrent } = makeSequencedFakeDriver([
      "complete",
      "complete",
      "complete",
      "complete",
      "complete",
    ]);
    useSessionStore.getState().setDriver(driver);

    await useSessionStore.getState().replayReference();

    expect(newSession).toHaveBeenCalledTimes(1); // newChat() ran first
    expect(calls).toEqual([...REFERENCE_QUESTIONS]); // exact order, no skips/dupes
    expect(maxConcurrent()).toBe(1); // each turn awaited before the next submits
    expect(useSessionStore.getState().turns).toHaveLength(REFERENCE_QUESTIONS.length);
    expect(useSessionStore.getState().turns.every((t) => t.answer.status === "complete")).toBe(
      true,
    );
    expect(useSessionStore.getState().replaying).toBe(false);
    expect(useSessionStore.getState().replayProgress).toBeNull();
  });

  it("stops at the first clarification instead of faking a continuation", async () => {
    const { driver, calls } = makeSequencedFakeDriver(["complete", "clarification", "complete"]);
    useSessionStore.getState().setDriver(driver);

    await useSessionStore.getState().replayReference();

    // Only the first two turns ran; the third (and 4th, 5th) never submitted.
    expect(calls).toEqual([REFERENCE_QUESTIONS[0], REFERENCE_QUESTIONS[1]]);
    expect(useSessionStore.getState().turns).toHaveLength(2);
    expect(useSessionStore.getState().turns[0].answer.status).toBe("complete");
    expect(useSessionStore.getState().turns[1].answer.status).toBe("clarification");
    expect(useSessionStore.getState().replaying).toBe(false);
    expect(useSessionStore.getState().replayProgress).toBeNull();
  });

  it("reports live progress while running (e.g. 2/5) via replayProgress", async () => {
    const seen: Array<{ index: number; total: number } | null> = [];
    const unsubscribe = useSessionStore.subscribe((state) => {
      seen.push(state.replayProgress);
    });
    const { driver } = makeSequencedFakeDriver(Array(5).fill("complete"));
    useSessionStore.getState().setDriver(driver);

    await useSessionStore.getState().replayReference();
    unsubscribe();

    expect(seen).toContainEqual({ index: 1, total: 5 });
    expect(seen).toContainEqual({ index: 5, total: 5 });
  });

  it("is a no-op while a turn is already streaming (single-flight)", async () => {
    const { driver, newSession } = makeSequencedFakeDriver(["complete"]);
    useSessionStore.getState().setDriver(driver);
    useSessionStore.setState({ streamingTurnId: "turn_mid_flight" });

    await useSessionStore.getState().replayReference();

    expect(newSession).not.toHaveBeenCalled();
    expect(useSessionStore.getState().turns).toEqual([]);
  });

  it("real reference fixtures replay clean end to end through the MockDriver", async () => {
    useSessionStore.getState().setDriver(new MockDriver(0));

    await useSessionStore.getState().replayReference();

    const turns = useSessionStore.getState().turns;
    expect(turns).toHaveLength(5);
    expect(turns.map((t) => t.submission.utterance)).toEqual([...REFERENCE_QUESTIONS]);
    expect(turns.every((t) => t.answer.status === "complete")).toBe(true);
  });
});

describe("session list + switching", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({
      driver: null,
      sessions: [],
      sessionsTotal: 0,
      sessionsState: "idle",
      sessionsError: null,
      switchingSessionId: null,
      switchError: null,
      sessionId: "sess_current",
      connection: { mode: "api", state: "online" },
    });
  });

  it("a driver with no deployment reports an empty list honestly", async () => {
    // The mock fixture implements no listSessions — the same idiom the
    // settings panel uses for capabilities it cannot read.
    useSessionStore.getState().setDriver(new MockDriver(0));

    await useSessionStore.getState().loadSessions();

    const state = useSessionStore.getState();
    expect(state.sessions).toEqual([]);
    expect(state.sessionsState).toBe("unavailable");
    expect(state.sessionsError).toMatch(/no deployment/i);
  });

  it("does not blame the deployment before a driver is wired", async () => {
    // page.tsx sets the driver in an effect, after the rail's first paint.
    useSessionStore.setState({ driver: null });

    await useSessionStore.getState().loadSessions();

    expect(useSessionStore.getState().sessionsState).toBe("idle");
    expect(useSessionStore.getState().sessionsError).toBeNull();
  });

  it("keeps the server's rows verbatim", async () => {
    const page = {
      sessions: [
        {
          sessionId: "sess_a",
          title: "Why did cash decline last week?",
          createdAt: "2026-08-08T09:00:00Z",
          lastActivity: "2026-08-08T09:05:00Z",
          turnCount: 3,
        },
      ],
      total: 7,
    };
    useSessionStore.getState().setDriver({
      submit: vi.fn(),
      newSession: vi.fn(),
      listSessions: vi.fn().mockResolvedValue(page),
    });

    await useSessionStore.getState().loadSessions();

    expect(useSessionStore.getState().sessions).toEqual(page.sessions);
    expect(useSessionStore.getState().sessionsTotal).toBe(7);
    expect(useSessionStore.getState().sessionsState).toBe("ready");
  });

  it("rehydrates a switched-to session's thread from server events", async () => {
    const resumeSession = vi.fn().mockResolvedValue({
      sessionId: "sess_other",
      watermark: { id: "wm_9", loadedAt: "2026-08-08 04:00", newestDataDate: "2026-08-07" },
      pack: { packId: "base-rcm", version: "1.0.0" },
      turns: [
        {
          investigationId: "inv_1",
          question: "Why did cash decline last week?",
          events: [
            {
              type: "finding",
              finding: {
                referent: { value: "F1", kind: "finding" },
                title: "State Medicaid cash down",
                statement: "Posted cash fell $48,940.41.",
                grade: "direct",
                confidence: "high",
                metricRefs: [],
                values: {},
                directionOfGood: "up_is_good",
                suggestedRefinements: [],
              },
            },
            { type: "turn_complete", investigationId: "inv_1", status: "complete" },
          ] satisfies TurnEvent[],
        },
      ],
    });
    useSessionStore.getState().setDriver({
      submit: vi.fn(),
      newSession: vi.fn(),
      listSessions: vi.fn().mockResolvedValue({ sessions: [], total: 0 }),
      resumeSession,
    });

    await useSessionStore.getState().switchSession("sess_other");

    const state = useSessionStore.getState();
    expect(resumeSession).toHaveBeenCalledWith("sess_other");
    expect(state.sessionId).toBe("sess_other");
    expect(state.watermark.id).toBe("wm_9");
    expect(state.turns).toHaveLength(1);
    const turn = state.turns[0];
    expect(turn.submission.utterance).toBe("Why did cash decline last week?");
    expect(turn.answer.status).toBe("complete");
    expect(turn.answer.findings).toHaveLength(1);
    expect(turn.answer.investigationId).toBe("inv_1");
    // Rebuilt, not watched: the answer card must not draw a stage timeline
    // for a pipeline nobody observed.
    expect(turn.answer.rehydrated).toBe(true);
    // Findings from a restored turn are addressable exactly like live ones.
    expect(state.referents["F1"].turnId).toBe(turn.id);
    expect(state.switchingSessionId).toBeNull();
  });

  it("clears the old thread and names the failure when a switch fails", async () => {
    useSessionStore.getState().setDriver({
      submit: vi.fn(),
      newSession: vi.fn(),
      listSessions: vi.fn().mockResolvedValue({ sessions: [], total: 0 }),
      resumeSession: vi.fn().mockRejectedValue(new Error("HTTP 404")),
    });
    useSessionStore.setState({
      turns: [{ id: "turn_1", index: 0, submission: {}, answer: emptyAnswer() }],
    });

    await useSessionStore.getState().switchSession("sess_gone");

    expect(useSessionStore.getState().turns).toEqual([]);
    expect(useSessionStore.getState().switchError).toBe("HTTP 404");
    expect(useSessionStore.getState().switchingSessionId).toBeNull();
  });

  it("is a no-op mid-stream, mid-replay, and for the session already on screen", async () => {
    const resumeSession = vi.fn();
    useSessionStore.getState().setDriver({
      submit: vi.fn(),
      newSession: vi.fn(),
      listSessions: vi.fn().mockResolvedValue({ sessions: [], total: 0 }),
      resumeSession,
    });

    useSessionStore.setState({ streamingTurnId: "turn_1" });
    await useSessionStore.getState().switchSession("sess_other");
    useSessionStore.setState({ streamingTurnId: null, replaying: true });
    await useSessionStore.getState().switchSession("sess_other");
    useSessionStore.setState({
      replaying: false,
      // Already open AND its thread is rebuilt — the only case where a
      // click on the current row has nothing left to do.
      turns: [{ id: "turn_1", index: 0, submission: {}, answer: emptyAnswer() }],
    });
    await useSessionStore.getState().switchSession("sess_current");

    expect(resumeSession).not.toHaveBeenCalled();
  });

  it("re-opens the current session when the page holds its id but none of its turns", async () => {
    // The first rail click after a page load: the store can hold a session
    // id with an empty thread (a reload, a drill-through, a turn submitted
    // before the rail was read), and the old guard compared ids only — so
    // the click no-oped into the cold-start hero with its guide chips live
    // over a session the analyst believed they had opened.
    const resumeSession = vi.fn().mockResolvedValue({
      sessionId: "sess_current",
      watermark: { id: "wm_9", loadedAt: "2026-08-08 04:00", newestDataDate: "2026-08-07" },
      pack: { packId: "base-rcm", version: "1.0.0" },
      turns: [
        {
          investigationId: "inv_1",
          question: "Why did cash decline last week?",
          events: [
            { type: "turn_complete", investigationId: "inv_1", status: "complete" },
          ] satisfies TurnEvent[],
        },
      ],
    });
    useSessionStore.getState().setDriver({
      submit: vi.fn(),
      newSession: vi.fn(),
      listSessions: vi.fn().mockResolvedValue({ sessions: [], total: 0 }),
      resumeSession,
    });
    useSessionStore.setState({ sessionId: "sess_current", turns: [] });

    await useSessionStore.getState().switchSession("sess_current");

    expect(resumeSession).toHaveBeenCalledWith("sess_current");
    expect(useSessionStore.getState().turns).toHaveLength(1);
  });

  it("refuses to switch through a driver that cannot re-open sessions", async () => {
    useSessionStore.getState().setDriver(new MockDriver(0));

    await useSessionStore.getState().switchSession("sess_other");

    expect(useSessionStore.getState().switchError).toMatch(/cannot re-open/i);
    expect(useSessionStore.getState().sessionId).toBe("sess_current");
  });

  /*
   * `/i/{investigation_id}`. A turn read outside its conversation has lost
   * the filters, the cohort and the referents that made its numbers mean
   * what they mean, so the link resolves to the session that produced it
   * (`InvestigationResponse.session_id`) and opens that.
   */
  it("opens the session an investigation link names", async () => {
    const resumeSession = vi.fn().mockResolvedValue({
      sessionId: "sess_other",
      watermark: { id: "wm_9", loadedAt: "2026-08-08 04:00", newestDataDate: "2026-08-07" },
      pack: { packId: "base-rcm", version: "1.0.0" },
      turns: [],
    });
    const sessionForInvestigation = vi.fn().mockResolvedValue("sess_other");
    useSessionStore.getState().setDriver({
      submit: vi.fn(),
      newSession: vi.fn(),
      listSessions: vi.fn().mockResolvedValue({ sessions: [], total: 0 }),
      resumeSession,
      sessionForInvestigation,
    });

    await useSessionStore.getState().openInvestigation("inv_b8267d9b585a");

    expect(sessionForInvestigation).toHaveBeenCalledWith("inv_b8267d9b585a");
    expect(resumeSession).toHaveBeenCalledWith("sess_other");
    expect(useSessionStore.getState().sessionId).toBe("sess_other");
  });

  it("names the failure when an investigation link cannot be resolved", async () => {
    useSessionStore.getState().setDriver({
      submit: vi.fn(),
      newSession: vi.fn(),
      resumeSession: vi.fn(),
      sessionForInvestigation: vi.fn().mockRejectedValue(new Error("HTTP 404")),
    });

    await useSessionStore.getState().openInvestigation("inv_gone");

    expect(useSessionStore.getState().switchError).toBe("HTTP 404");
  });

  it("says so when the driver cannot resolve investigation links at all", async () => {
    useSessionStore.getState().setDriver(new MockDriver(0));

    await useSessionStore.getState().openInvestigation("inv_1");

    expect(useSessionStore.getState().switchError).toMatch(/cannot open an investigation link/i);
  });
});

/**
 * The permalinks themselves. There was no per-session URL anywhere in the
 * product while the archive dialog promised in writing that a session
 * "stays reachable by link" — these are the strings that make the promise
 * true.
 */
describe("permalinks", () => {
  it("builds a session link an id with URL-unsafe characters cannot break", () => {
    expect(sessionLinkFor("sess_a", "https://revi.example.com")).toBe(
      "https://revi.example.com/s/sess_a",
    );
    // A trailing slash on the origin must not double up.
    expect(sessionLinkFor("sess_a", "https://revi.example.com/")).toBe(
      "https://revi.example.com/s/sess_a",
    );
    expect(sessionLinkFor("a b/c", "http://localhost:3000")).toBe(
      "http://localhost:3000/s/a%20b%2Fc",
    );
  });

  it("builds an investigation link the same way", () => {
    expect(investigationLinkFor("inv_1", "https://revi.example.com")).toBe(
      "https://revi.example.com/i/inv_1",
    );
  });
});

describe("chunkNarrative", () => {
  it("round-trips the original text", () => {
    const text = "Payer cash posted fell $193,525.79 (−12.7%) week-over-week.";
    expect(chunkNarrative(text).join("")).toBe(text);
  });
});
