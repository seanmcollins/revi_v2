/**
 * The two experiences an answer has: default (plain language, no engine
 * vocabulary) and debug (the same answer plus the decision trace).
 *
 * This is the regression test for the copy sweep. It renders a whole
 * answer and asserts that the internal words the design doc names —
 * probe, watermark, epoch, plan hash, schema — do not reach the screen
 * with debug off, while the domain words analysts actually use (denial,
 * CARC, payer, AR) are untouched.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AnswerCard } from "@/components/answer/AnswerCard";
import { TooltipProvider } from "@/components/ui/tooltip";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import { emptyAnswer, useSessionStore, type TurnRecord } from "@/lib/store";
import type { DebugTrace } from "@/lib/types";

const TRACE: DebugTrace = {
  traceId: "trace_1",
  sessionId: "sess_1",
  investigationId: "inv_1",
  turnId: "turn_1",
  settings: {
    modelTier: null,
    maxTurnCostUsd: null,
    narrativeDepth: "summary",
    evidenceDepth: "standard",
    debug: true,
  },
  turnClass: "new_investigation",
  classificationConfidence: 0.94,
  refinementOperators: [],
  referentResolutions: [],
  probes: [],
  grades: {},
  findingGrades: {},
  calculationOperators: [],
  warnings: [],
  llmCalls: [],
  templateHashes: {},
  timingsMs: {},
  watermarkId: "wm_003",
  watermarkStale: false,
  epoch: 1,
  reAnchored: false,
  packId: "base-rcm",
  packVersion: "1.0.0",
  packSnapshotId: "snap",
  redactions: [],
};

function turn(debugTrace?: DebugTrace): TurnRecord {
  return {
    id: "turn_1",
    index: 0,
    submission: { utterance: "Why did cash decline last week?" },
    answer: {
      ...emptyAnswer(),
      status: "complete",
      investigationId: "inv_1",
      // A denial-domain narrative: these words must SURVIVE the sweep.
      narrative: "Denials on Atlas Health rose, driven by CARC 16 on the payer's AR.",
      warnings: [
        {
          type: "warning",
          code: "EVIDENCE_TRUNCATED",
          message: "Only the largest 48 payers were compared.",
          severity: "caution",
        },
      ],
      ...(debugTrace ? { debug: debugTrace } : {}),
    },
  };
}

function renderCard(record: TurnRecord) {
  return render(
    <TooltipProvider>
      <AnswerCard turn={record} />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  useSessionStore.setState({ settings: DEFAULT_SETTINGS });
});

afterEach(() => {
  cleanup();
  useSessionStore.setState({ settings: DEFAULT_SETTINGS });
});

describe("AnswerCard — default mode", () => {
  it("shows a caution's sentence without its §12 code", () => {
    renderCard(turn());

    expect(screen.getByText("Only the largest 48 payers were compared.")).toBeInTheDocument();
    expect(screen.queryByText("EVIDENCE_TRUNCATED")).not.toBeInTheDocument();
  });

  it("renders no decision trace", () => {
    renderCard(turn(TRACE));

    expect(screen.queryByText(/Decision trace/)).not.toBeInTheDocument();
  });

  it("keeps the domain vocabulary analysts use", () => {
    const { container } = renderCard(turn());
    const text = container.textContent ?? "";

    for (const word of ["Denials", "CARC", "payer", "AR"]) {
      expect(text).toContain(word);
    }
  });

  it("carries no internal vocabulary anywhere on the answer", () => {
    const { container } = renderCard(turn(TRACE));
    const text = container.textContent ?? "";

    for (const pattern of [
      /\bprobes?\b/i,
      /\bwatermark\b/i,
      /\bepoch\b/i,
      /\bplan hash\b/i,
      /\bzero-probe\b/i,
      /\bstructured output\b/i,
      /\bschema\b/i,
    ]) {
      expect(text, `default mode must not say ${pattern}`).not.toMatch(pattern);
    }
  });
});

describe("AnswerCard — an answer with no findings (F2)", () => {
  function emptyTurn(overrides: Partial<TurnRecord["answer"]> = {}): TurnRecord {
    return {
      id: "turn_empty",
      index: 0,
      submission: { utterance: "Denials for Atlas Health in July" },
      answer: {
        ...emptyAnswer(),
        status: "complete",
        investigationId: "inv_empty",
        answerGrade: "direct",
        metric: {
          metrics: [{ id: "denial_rate", contractVersion: 2 }],
          pack: { packId: "base-rcm", version: "1.0.0" },
        },
        header: {
          window: { start: "2026-07-01", end: "2026-07-31", basis: "post" },
          filters: [],
          watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
          packVersion: { packId: "base-rcm", version: "1.0.0" },
        },
        ...overrides,
      },
    };
  }

  it("never renders a card that is blank apart from its badges", () => {
    // The whole failure mode: a completed turn with no findings, no
    // narrative and no clarification used to render a "Governed" badge
    // over empty space — which reads as a bug and, worse, certifies a
    // nothing.
    const { container } = renderCard(emptyTurn());
    expect(
      screen.getByText(/No findings for this question — here's what was checked/),
    ).toBeInTheDocument();
    expect((container.textContent ?? "").trim().length).toBeGreaterThan(60);
  });

  it("surfaces what the payload DOES carry — window, measure, checks", () => {
    renderCard(
      emptyTurn({
        evidence: {
          probes: [
            {
              probeId: "main",
              probeHash: "h",
              kind: "aggregation",
              description: "direct metric query",
              metrics: [],
              cacheHit: false,
              truncated: false,
              suppressedCells: 0,
              durationMs: 3,
            },
          ],
          warehouseQueries: 1,
          cacheHits: 0,
          zeroProbeTurn: false,
        },
      }),
    );
    expect(screen.getByText(/Jul 1 – Jul 31, 2026 \(post date\)/)).toBeInTheDocument();
    expect(screen.getByText(/Denial rate/)).toBeInTheDocument();
    expect(screen.getByText(/1 data check against this data load/)).toBeInTheDocument();
  });

  it("defers to the engine's own warnings when it published any", () => {
    renderCard(
      emptyTurn({
        warnings: [
          {
            type: "warning",
            code: "ANSWER_NOTE",
            message: "suppression: cells counting fewer than 11 entities are suppressed",
            severity: "info",
          },
        ],
      }),
    );
    expect(screen.getByText(/the engine's own account of why/)).toBeInTheDocument();
  });

  it("stays out of the way of an answer that DID find something", () => {
    renderCard(turn());
    expect(screen.queryByText(/No findings for this question/)).not.toBeInTheDocument();
  });
});

describe("AnswerCard — debug mode", () => {
  beforeEach(() => {
    useSessionStore.setState({ settings: { ...DEFAULT_SETTINGS, debug: true } });
  });

  it("restores the §12 code next to the caution", () => {
    renderCard(turn());

    expect(screen.getByText("EVIDENCE_TRUNCATED")).toBeInTheDocument();
  });

  it("renders the decision trace for the turn", () => {
    renderCard(turn(TRACE));

    expect(screen.getByText("Decision trace")).toBeInTheDocument();
    expect(screen.getByText(/new_investigation \(0\.94\)/)).toBeInTheDocument();
  });
});
