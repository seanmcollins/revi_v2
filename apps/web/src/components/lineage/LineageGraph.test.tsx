/**
 * THE BADGE IS A POSITION, NOT A NAME.
 *
 * The lineage row draws a fixed 28px square badge — "T1", "T2" — beside
 * the question it stands for. In api mode it was drawing the whole
 * question inside that square, because two different ideas had collapsed
 * onto one field called `label`:
 *
 *   - `parseSessionLineage` publishes `label` as a display NAME. The wire
 *     has no such field (`InvestigationResponse` carries no label), so it
 *     is derived from the question — which is correct, and is what
 *     `ApiDriver.resumeSession` consumes as `node.question || node.label`.
 *   - `LineageGraph` read that same `label` as the badge's ORDINAL,
 *     falling back to `T${i+1}` only when it was empty. It never was.
 *
 * So every server-fed node put a full sentence in a 28px box: the badge
 * reflowed to four lines of wrapped monospace, pushed the row's title out
 * of place and left the connector spine hanging beside it. It survived
 * every test because mock mode builds its nodes locally, where the badge
 * really is `T${i+1}` — only a live API reproduced it.
 *
 * These tests pin the two halves TOGETHER — wire payload through
 * `parseSessionLineage` into `buildLineageNodes` — because the defect was
 * in neither half alone, it was in the handoff.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { buildLineageNodes, LineageGraph } from "@/components/lineage/LineageGraph";
import { parseSessionLineage } from "@/lib/contract";
import { emptyAnswer, useSessionStore, type TurnRecord } from "@/lib/store";
import type { SessionLineageData } from "@/lib/types";

// The render tests exercise the LOCAL branch, so the server query is
// stubbed to "no answer yet" rather than wired to a QueryClient.
vi.mock("@/lib/queries", () => ({
  useSessionLineageQuery: () => ({ data: undefined }),
}));

const QUESTION = "Give me my denial rate by month for the last 6 months";

/**
 * The shape the live API actually returns: snake_case, node list named
 * `investigations`, and NO `label` anywhere. Copied from
 * `GET /v1/sessions/{sid}/lineage` against the demo tenant.
 */
const WIRE = {
  investigations: [
    {
      turn_id: "turn_bf5d7fc58cb2",
      investigation_id: "inv_61fac8f3cd94",
      turn_class: "new_investigation",
      question: QUESTION,
    },
    {
      turn_id: "turn_9c1e4a77b210",
      investigation_id: "inv_77ab19d0c3e5",
      turn_class: "refinement",
      question: "Break that down by payer",
    },
  ],
  edges: [
    {
      // Investigation ids on both endpoints; `turn_id` is the join key.
      parent_id: "inv_61fac8f3cd94",
      child_id: "inv_77ab19d0c3e5",
      turn_id: "turn_9c1e4a77b210",
      operators: [{ op: "set_dimensions", dimensions: ["payer"] }],
    },
  ],
};

function serverData(): SessionLineageData {
  const { value, drift } = parseSessionLineage(WIRE);
  expect(drift).toEqual([]);
  expect(value).not.toBeNull();
  return value as SessionLineageData;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("buildLineageNodes — server-fed rows", () => {
  it("numbers the badge by position and never puts the question in it", () => {
    const nodes = buildLineageNodes({ turns: [], referents: {}, serverData: serverData() });

    expect(nodes.map((n) => n.ordinal)).toEqual(["T1", "T2"]);
    // The regression itself: the badge must not be handed a sentence.
    for (const node of nodes) {
      expect(node.ordinal).toMatch(/^T\d+$/);
      expect(node.ordinal).not.toContain(" ");
    }
  });

  it("keeps the question as the row's NAME, where it has room to render", () => {
    const nodes = buildLineageNodes({ turns: [], referents: {}, serverData: serverData() });
    expect(nodes[0].question).toBe(QUESTION);
    expect(nodes[1].question).toBe("Break that down by payer");
  });

  it("carries the edge operators onto the child they produced", () => {
    const nodes = buildLineageNodes({ turns: [], referents: {}, serverData: serverData() });
    expect(nodes[0].operators).toEqual([]);
    expect(nodes[1].operators).toEqual(["SetDimensions(payer)"]);
  });

  it("maps a node to a local turn by id when the ids share a namespace", () => {
    const turns = [
      { ...localTurn("turn_bf5d7fc58cb2") },
      { ...localTurn("turn_9c1e4a77b210") },
    ];
    const nodes = buildLineageNodes({ turns, referents: {}, serverData: serverData() });
    expect(nodes.map((n) => n.scrollTurnId)).toEqual([
      "turn_bf5d7fc58cb2",
      "turn_9c1e4a77b210",
    ]);
  });

  it("falls back to position when the store minted its own turn ids", () => {
    // The live case: the store streams `turn_1`, `turn_2`… and the server
    // knows nothing about those ids. Order is the only shared key, and it
    // is a sound one — the thread is rebuilt from this same list.
    const turns = [localTurn("turn_1"), localTurn("turn_2")];
    const nodes = buildLineageNodes({ turns, referents: {}, serverData: serverData() });
    expect(nodes.map((n) => n.scrollTurnId)).toEqual(["turn_1", "turn_2"]);
  });
});

describe("LineageGraph — rendered", () => {
  it("draws T1/T2 in the badges rather than the questions", () => {
    useSessionStore.setState({
      turns: [localTurn("turn_1", QUESTION), localTurn("turn_2", "Break that down by payer")],
      referents: {},
      sessionId: "sess_1",
      sessionLive: true,
    });

    render(<LineageGraph />);

    expect(screen.getByText("T1")).toBeInTheDocument();
    expect(screen.getByText("T2")).toBeInTheDocument();
    expect(screen.getByText(QUESTION)).toBeInTheDocument();
  });

  it("names the turn class in prose and never prints the wire enum", () => {
    useSessionStore.setState({
      turns: [
        localTurn("turn_1", QUESTION, "new_investigation"),
        localTurn("turn_2", "Show that as a share", "presentation_only"),
      ],
      referents: {},
      sessionId: "sess_1",
      sessionLive: true,
    });

    const { container } = render(<LineageGraph />);

    expect(screen.getByText("New investigation")).toBeInTheDocument();
    // Twice over: once on the edge that produced the turn, once on the
    // turn itself. Both used to be able to print `presentation_only`.
    expect(screen.getAllByText("Presentation")).toHaveLength(2);
    // No snake_case reaches the reader.
    expect(container.textContent).not.toMatch(/[a-z]+_[a-z]+/);
  });
});

function localTurn(
  id: string,
  utterance = QUESTION,
  turnClass: TurnRecord["answer"]["turnClass"] = "new_investigation",
): TurnRecord {
  return {
    id,
    index: 0,
    submission: { utterance },
    answer: { ...emptyAnswer(), turnClass },
  } as TurnRecord;
}
