/**
 * Two seams where the client's idea of a payload had drifted from the
 * payload — both found by reading the live wire rather than the fixtures:
 * the lineage edge (below) and the streamed-versus-composed write-up (at
 * the foot of this file).
 *
 * ONE OPERATOR VOCABULARY, TWO DIRECTIONS.
 *
 * A lineage edge's label can arrive two ways: built by this client (a
 * `Refinement`, rendered by `describeRefinement`) or sent by the server (a
 * snake_case DTO, rendered by `describeWireOperator`). Two renderers for
 * one vocabulary is how a product ends up saying `SetDimensions(payer)` in
 * one place and `set_dimensions` in another.
 *
 * So the two are pinned against each other here, through the wire mapping
 * the client already owns: for every operator `refinementToWire` can
 * produce, sending it and reading it back must yield the SAME label the
 * client would have drawn itself.
 *
 * This is also the test that would have caught the original defect. The
 * wire publishes operators as OBJECTS; the parser cast them to `string[]`
 * and the graph handed them to a `<code>` element, which renders an object
 * as "[object Object]" — and nobody saw it, because the edge join used an
 * investigation id where a turn id belonged and therefore never matched a
 * node at all. Both halves are asserted below.
 */

import { describe, expect, it } from "vitest";

import {
  describeWireOperator,
  newReceivedState,
  parseSessionLineage,
  refinementToWire,
  trackReceived,
  turnResponseToEvents,
} from "@/lib/contract";
import { describeRefinement } from "@/lib/format";
import type { Refinement } from "@/lib/types";

/** One of every operator the client can build and send. */
const OPERATORS: Refinement[] = [
  { op: "SetDimensions", dimensions: ["payer"] },
  {
    op: "AddFilter",
    filter: { dimension: "payer", dimensionLabel: "Payer", op: "eq", values: ["Aetna"] },
  },
  { op: "RemoveFilter", dimension: "payer" },
  {
    op: "SetWindow",
    window: { start: "2026-07-01", end: "2026-07-31", basis: "service" },
  },
  {
    op: "SetComparison",
    comparison: {
      kind: "same_period_last_year",
      window: { start: "2025-07-01", end: "2025-07-31", basis: "service" },
    },
  },
  { op: "SetComparison", comparison: null },
  { op: "SetGrain", grain: { entity: "claim" } },
  { op: "DrillInto", target: "F2" },
  { op: "Pivot", measures: ["denied_dollars"] },
  { op: "Explain", target: "F2" },
  { op: "RankBy", metric: "denial_rate", descending: true },
  { op: "RankBy", metric: "denial_rate", descending: false },
  { op: "Expand" },
  { op: "ResetContext", keepPins: true },
  { op: "ResetContext", keepPins: false },
];

describe("describeWireOperator — the same words as describeRefinement", () => {
  for (const refinement of OPERATORS) {
    const expected = describeRefinement(refinement);
    it(`round-trips ${expected}`, () => {
      const wire = refinementToWire(refinement);
      expect(wire).not.toBeNull();
      expect(describeWireOperator(wire)).toBe(expected);
    });
  }

  /*
   * The one deliberate divergence. A custom comparison's display LABEL
   * ("Q1 2026") is a client-side nicety that `refinementToWire` does not
   * send — the wire carries the two dates. So the edge names the dates,
   * which is the honest rendering of what the server actually said.
   */
  it("names a custom comparison by its dates, which is all the wire carries", () => {
    expect(
      describeWireOperator({
        op: "set_comparison",
        kind: null,
        custom: { start: "2026-01-01", end: "2026-03-31" },
      }),
    ).toBe("SetComparison(2026-01-01…2026-03-31)");
  });

  it("passes a display string through untouched — the local DAG builds those", () => {
    expect(describeWireOperator("DrillInto(F2)")).toBe("DrillInto(F2)");
  });

  it("degrades an unrecognised operator to its name rather than dropping it", () => {
    expect(describeWireOperator({ op: "some_future_operator" })).toBe("some_future_operator");
  });

  it("returns null for something that is not an operator at all", () => {
    expect(describeWireOperator({ dimensions: ["payer"] })).toBeNull();
    expect(describeWireOperator(42)).toBeNull();
  });
});

describe("parseSessionLineage — edges are readable by the graph that draws them", () => {
  /** The live payload shape, with the ids in their real namespaces. */
  const WIRE = {
    investigations: [
      {
        turn_id: "turn_a",
        investigation_id: "inv_a",
        turn_class: "new_investigation",
        question: "Why did cash decline last week?",
      },
      {
        turn_id: "turn_b",
        investigation_id: "inv_b",
        turn_class: "refinement",
        question: "Break that down by payer",
      },
    ],
    edges: [
      {
        parent_id: "inv_a",
        child_id: "inv_b",
        turn_id: "turn_b",
        operators: [{ op: "set_dimensions", dimensions: ["payer"] }],
      },
    ],
  };

  it("renders wire operator OBJECTS as labels, never as [object Object]", () => {
    const { value } = parseSessionLineage(WIRE);
    expect(value?.edges[0]?.operators).toEqual(["SetDimensions(payer)"]);
    for (const label of value?.edges[0]?.operators ?? []) {
      expect(label).not.toContain("[object");
      expect(typeof label).toBe("string");
    }
  });

  it("gives every edge a turn id that matches a node, so the join can succeed", () => {
    const { value } = parseSessionLineage(WIRE);
    const nodeTurnIds = new Set(value?.nodes.map((n) => n.turnId));
    for (const edge of value?.edges ?? []) {
      expect(nodeTurnIds.has(edge.turnId)).toBe(true);
      // The old join key. It is an investigation id and matches nothing.
      expect(nodeTurnIds.has(edge.childInvestigationId)).toBe(false);
    }
  });
});

/* ------------------------------------------------------------------ */
/* The stream is a draft; the terminal frame is the write-up           */
/* ------------------------------------------------------------------ */

/**
 * A LENGTH IS NOT A PREFIX.
 *
 * `turnResponseToEvents` reconciles the prose the live stream delivered
 * with the prose the terminal frame carries. It used to do that with
 * arithmetic alone — append `narrative.slice(receivedLength)` — which is a
 * correct CONTINUATION only when the streamed text is a byte-exact prefix
 * of the composed one. The server makes no such promise: measured on a
 * live turn against the demo tenant, the stream carried a 676-character
 * draft and the terminal frame a 1,141-character rewrite that shared no
 * suffix with it. Slicing the rewrite at 676 landed three characters into
 * the word "July", so the published answer ended with a sentence that
 * restarted mid-word and then repeated a paragraph the reader had just
 * read — on the flagship surface, on an ordinary question.
 *
 * The prefix is now checked. These pin both branches.
 */
describe("turnResponseToEvents — reconciling a streamed draft with the final prose", () => {
  const answerOf = (narrative: string) =>
    ({
      outcome: "answer",
      investigationId: "inv_1",
      sessionId: "sess_1",
      turnClass: "new_investigation",
      findings: [],
      charts: [],
      warnings: [],
      narrative,
      watermarkStale: false,
    }) as unknown as Parameters<typeof turnResponseToEvents>[0];

  it("sends only the tail when the stream really was a prefix", () => {
    const received = newReceivedState();
    trackReceived(received, { type: "narrative_delta", text: "Cash posted fell 12.7%. " });

    const events = turnResponseToEvents(
      answerOf("Cash posted fell 12.7%. Three payers account for most of it."),
      received,
    );
    const deltas = events.filter((e) => e.type === "narrative_delta");
    expect(deltas).toHaveLength(1);
    if (deltas[0]?.type === "narrative_delta") {
      expect(deltas[0].text).toBe("Three payers account for most of it.");
      expect(deltas[0].replace).toBeUndefined();
    }
  });

  it("REPLACES the draft when the composer rewrote it", () => {
    const received = newReceivedState();
    // A draft that is not a prefix of the final text — the real shape.
    trackReceived(received, {
      type: "narrative_delta",
      text: "Denial rate rose. The July 2026 reading is context only.",
    });

    const final =
      "Across the five settled months the denial rate moved from 7.2% to 9.1%. " +
      "The July 2026 reading of 12.8% is context only, not part of that movement.";
    const events = turnResponseToEvents(answerOf(final), received);
    const deltas = events.filter((e) => e.type === "narrative_delta");
    expect(deltas).toHaveLength(1);
    if (deltas[0]?.type === "narrative_delta") {
      expect(deltas[0].replace).toBe(true);
      expect(deltas[0].text).toBe(final);
      // The defect, stated as an assertion: no mid-word splice survives.
      expect(deltas[0].text).not.toMatch(/\by \d{4}/);
    }
  });

  it("emits nothing when the terminal prose is exactly what was streamed", () => {
    const received = newReceivedState();
    const prose = "Cash posted fell 12.7% against the prior week.";
    trackReceived(received, { type: "narrative_delta", text: prose });

    const events = turnResponseToEvents(answerOf(prose), received);
    expect(events.filter((e) => e.type === "narrative_delta")).toHaveLength(0);
  });

  it("sends the whole write-up when the stream delivered none of it", () => {
    const events = turnResponseToEvents(answerOf("The whole answer."), newReceivedState());
    const deltas = events.filter((e) => e.type === "narrative_delta");
    expect(deltas).toHaveLength(1);
    if (deltas[0]?.type === "narrative_delta") {
      expect(deltas[0].text).toBe("The whole answer.");
    }
  });
});
