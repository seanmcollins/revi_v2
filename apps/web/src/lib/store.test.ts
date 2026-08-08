import { beforeEach, describe, expect, it } from "vitest";

import { REFERENCE_TURNS } from "@/lib/mock/reference";
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

  it("turn_complete finalizes stages and carries grade + metric", () => {
    const answer = reduce(REFERENCE_TURNS[2].events);
    expect(answer.status).toBe("complete");
    expect(answer.answerGrade).toBe("proxy");
    expect(answer.metric?.id).toBe("denied_dollars");
    expect(answer.stages.some((s) => s.state === "active")).toBe(false);
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

  it("T2 reconciliation: payer children sum exactly to the parent delta", () => {
    const evidence = REFERENCE_TURNS[1].events.find((e) => e.type === "evidence");
    if (evidence?.type !== "evidence") throw new Error("missing evidence");
    expect(evidence.evidence.reconciliation.status).toBe("passed");
    expect(evidence.evidence.reconciliation.childSumCents).toBe(
      evidence.evidence.reconciliation.parentCents,
    );
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

describe("chunkNarrative", () => {
  it("round-trips the original text", () => {
    const text = "Payer cash posted fell $193,525.79 (−12.7%) week-over-week.";
    expect(chunkNarrative(text).join("")).toBe(text);
  });
});
