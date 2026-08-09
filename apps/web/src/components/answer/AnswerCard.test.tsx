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

/* ------------------------------------------------------------------ */
/* The round-2 wire fields, rendered                                   */
/* ------------------------------------------------------------------ */

function bareTurn(overrides: Partial<TurnRecord["answer"]>): TurnRecord {
  return {
    id: "turn_wire",
    index: 0,
    submission: { utterance: "…" },
    answer: { ...emptyAnswer(), status: "complete", investigationId: "inv_1", ...overrides },
  };
}

describe("AnswerCard — structured warnings (F14)", () => {
  it("titles a caution in the reader's words and keeps the engine's sentence", () => {
    renderCard(
      bareTurn({
        warnings: [
          {
            type: "warning",
            code: "POPULATION_CAVEAT",
            severity: "caution",
            message: "population_caveat: no deadline predicate is applied to this figure",
            structured: true,
          },
        ],
      }),
    );
    // The code prettified is still jargon; this is the same fact in the
    // words an analyst would use.
    expect(screen.getByText("How to read this number")).toBeInTheDocument();
    expect(
      screen.getByText("no deadline predicate is applied to this figure"),
    ).toBeInTheDocument();
    // …and not the machine prefix, which the title now carries.
    expect(screen.queryByText(/population_caveat:/)).not.toBeInTheDocument();
  });

  it("styles a caution and a note differently, from the server's severity", () => {
    const { container } = renderCard(
      bareTurn({
        warnings: [
          {
            type: "warning",
            code: "WINDOW_ASSUMED",
            severity: "caution",
            message: "window_assumed: no window was named, so the last 30 days were used",
            structured: true,
          },
          {
            type: "warning",
            code: "SUPPRESSION_APPLIED",
            severity: "info",
            message: "suppression: cells counting fewer than 11 entities are suppressed",
            structured: true,
          },
        ],
      }),
    );
    const caution = container.querySelector('[data-warning-code="WINDOW_ASSUMED"]');
    const note = container.querySelector('[data-warning-code="SUPPRESSION_APPLIED"]');
    expect(caution?.className).toContain("border-warning/40");
    expect(note?.className).not.toContain("border-warning/40");
    expect(screen.getByText("Window assumed")).toBeInTheDocument();
    expect(screen.getByText("Small cells were suppressed")).toBeInTheDocument();
  });

  it("puts cautions above notes — the ones that change the reading come first", () => {
    const { container } = renderCard(
      bareTurn({
        warnings: [
          {
            type: "warning",
            code: "SUPPRESSION_APPLIED",
            severity: "info",
            message: "suppression: small cells removed",
            structured: true,
          },
          {
            type: "warning",
            code: "EMPTY_RESULT",
            severity: "caution",
            message: "empty_result: no rows matched",
            structured: true,
          },
        ],
      }),
    );
    const codes = [...container.querySelectorAll("[data-warning-code]")].map((el) =>
      el.getAttribute("data-warning-code"),
    );
    expect(codes).toEqual(["EMPTY_RESULT", "SUPPRESSION_APPLIED"]);
  });

  it("collapses duplicates into one row with a count badge", () => {
    renderCard(
      bareTurn({
        warnings: [
          {
            type: "warning",
            code: "ALTERNATE_BASIS_USED",
            severity: "caution",
            message: "alternate_basis_used: remit is not bound, so service date was used",
            count: 4,
            structured: true,
          },
        ],
      }),
    );
    expect(screen.getByText("Computed on a different date basis")).toBeInTheDocument();
    expect(screen.getByText("×4")).toBeInTheDocument();
  });

  it("renders an unclassified warning as its sentence, with no invented heading", () => {
    // The server is saying "we have no handle for this one"; a confident
    // title over it would be the client making the call it just declined.
    renderCard(
      bareTurn({
        warnings: [
          {
            type: "warning",
            code: "UNCLASSIFIED",
            severity: "info",
            message: "something the classifier has never seen before",
            structured: true,
          },
        ],
      }),
    );
    expect(
      screen.getByText("something the classifier has never seen before"),
    ).toBeInTheDocument();
    expect(screen.queryByText("UNCLASSIFIED")).not.toBeInTheDocument();
  });
});

describe("AnswerCard — anomaly drill reconciliation (F1)", () => {
  const diverged: TurnRecord = bareTurn({
    anomalyReconciliation: {
      anomalyId: "ANM-021",
      status: "diverged",
      cardImpactCents: 17_821_682,
      answerImpactCents: 19_587_392,
      deltaCents: 1_765_710,
      deltaFraction: 0.099077,
      cardMetricId: "dnfb_dollars",
      answerMetricId: "dnfb_dollars",
      detail: "The detector's window, population or valuation basis is not the contract's.",
    },
  });

  it("shows BOTH figures and the gap", () => {
    renderCard(diverged);
    expect(screen.getByText("This answer differs from the card that opened it")).toBeInTheDocument();
    expect(screen.getByText("$178,216.82")).toBeInTheDocument();
    expect(screen.getByText("$195,873.92")).toBeInTheDocument();
    expect(screen.getByText("+9.9%")).toBeInTheDocument();
    expect(screen.getByText("ANM-021")).toBeInTheDocument();
  });

  it("repeats the platform's own explanation rather than summarizing it", () => {
    renderCard(diverged);
    expect(
      screen.getByText(/valuation basis is not the contract's/),
    ).toBeInTheDocument();
  });

  it("still shows both numbers when the two AGREE", () => {
    const { container } = renderCard(
      bareTurn({
        anomalyReconciliation: {
          anomalyId: "ANM-023",
          status: "agreed",
          cardImpactCents: 4_893_861,
          answerImpactCents: 4_893_861,
          deltaFraction: 0,
        },
      }),
    );
    expect(container.querySelector('[data-anomaly-status="agreed"]')).not.toBeNull();
    expect(screen.getAllByText("$48,938.61")).toHaveLength(2);
    // No delta column on an agreement — "+0.0%" is a fact nobody needs.
    expect(screen.queryByText("Difference")).not.toBeInTheDocument();
  });

  it("says so when the card could not be re-derived at all", () => {
    renderCard(
      bareTurn({
        anomalyReconciliation: {
          anomalyId: "ANM-015",
          status: "unavailable",
          cardImpactCents: 1_000,
        },
      }),
    );
    expect(screen.getByText("The card's figure could not be re-derived here")).toBeInTheDocument();
    expect(screen.getByText("not re-derived")).toBeInTheDocument();
  });

  it("renders nothing on a turn that drilled no card", () => {
    const { container } = renderCard(bareTurn({}));
    expect(container.querySelector("[data-anomaly-status]")).toBeNull();
  });
});

describe("AnswerCard — cache chip copy (residual)", () => {
  function zeroProbe(cacheHits: number): TurnRecord {
    return bareTurn({
      evidence: {
        probes: [],
        warehouseQueries: 0,
        cacheHits,
        zeroProbeTurn: true,
      },
    });
  }

  it("claims cache reuse only when probes actually came from the cache", () => {
    renderCard(zeroProbe(3));
    expect(screen.getByText(/Answered from cached results/)).toBeInTheDocument();
  });

  it("says nothing was needed when nothing was cached either", () => {
    // `zeroProbeTurn` is `warehouseQueries === 0`, which is also true of a
    // META or definitional turn that never had a probe to cache. "Answered
    // from cached results" over one of those claims a reuse that never
    // happened.
    renderCard(zeroProbe(0));
    expect(screen.getByText("No warehouse query was needed for this answer")).toBeInTheDocument();
    expect(screen.queryByText(/cached results/)).not.toBeInTheDocument();
  });
});

describe("AnswerCard — budget refusals (F19 residual)", () => {
  it("separates a model-spend stop from a read stop, and offers the fix", () => {
    renderCard(
      bareTurn({
        status: "error",
        error: {
          code: "QUERY_BUDGET_EXCEEDED",
          subcode: "MODEL_SPEND_BUDGET",
          message: "This turn reached its model-spend ceiling before it could finish.",
          usage: {
            llmCalls: 2,
            costUsd: "0.021321",
            inputTokens: 1033,
            outputTokens: 398,
            cacheReadTokens: 0,
            cacheCreationTokens: 1031,
            schemaRetries: 0,
          },
        },
      }),
    );
    expect(screen.getByText("This turn hit its model-spend ceiling")).toBeInTheDocument();
    // The recovery is a setting, not a rewrite — and it is one click away.
    expect(screen.getByRole("button", { name: "Adjust cost ceiling" })).toBeInTheDocument();
    // "Failures are free" is a claim, and it is false.
    expect(screen.getByText(/Spent \$0\.021321 on 2 model calls before stopping/)).toBeInTheDocument();
  });

  it("sends a read stop to narrow the question instead", () => {
    renderCard(
      bareTurn({
        status: "error",
        error: {
          code: "QUERY_BUDGET_EXCEEDED",
          subcode: "WAREHOUSE_READ_BUDGET",
          message: "That question would read more of the warehouse than one turn is allowed to.",
        },
      }),
    );
    expect(
      screen.getByText("This question reads more of the warehouse than one turn allows"),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Adjust cost ceiling" })).not.toBeInTheDocument();
  });

  it("leaves an ordinary refusal exactly as it was", () => {
    renderCard(
      bareTurn({
        status: "error",
        error: { code: "GRAIN_INCOMPATIBLE", message: "That metric can't be cut that way." },
      }),
    );
    expect(screen.getByText("GRAIN_INCOMPATIBLE")).toBeInTheDocument();
    expect(screen.getByText(/That metric can't be cut that way\./)).toBeInTheDocument();
    expect(screen.queryByText(/before stopping/)).not.toBeInTheDocument();
  });
});

describe("AnswerCard — a governed name's caveat travels with it (FN-5)", () => {
  // jsdom has no matchMedia; the impact stat's count-up asks it whether
  // motion is reduced. Answering "yes" also skips the animation, so the
  // assertions below read the final figure rather than a frame of it.
  beforeEach(() => {
    Object.defineProperty(window, "matchMedia", {
      configurable: true,
      writable: true,
      value: (query: string) => ({
        matches: true,
        media: query,
        onchange: null,
        addEventListener: () => {},
        removeEventListener: () => {},
        addListener: () => {},
        removeListener: () => {},
        dispatchEvent: () => false,
      }),
    });
  });

  const FINDING = {
    referent: { value: "F1", kind: "finding" as const },
    title: "Unbilled open inventory on a running filing clock: $22,426,000.28",
    statement: "Unbilled open inventory is $22,426,000.28 over the window.",
    metricRefs: ["timely_filing_at_risk_dollars"],
    values: {},
    grade: "direct" as const,
    directionOfGood: "down_is_good" as const,
    confidence: "high" as const,
    suggestedRefinements: [],
    impactCents: 2_242_600_028,
    impactLabel: "at this data load",
    // The pack's live entry. The "watch proxy" label came off when
    // `timely_filing_at_risk_dollars` grew its claim → plan → filing-rule
    // join: the runway is real and measurable now, so the number is no
    // longer standing in for a measurement nobody could make. What is left
    // is a population statement, which is a different and permanent kind
    // of caveat — the total still counts every unbilled open claim
    // regardless of runway. The figure itself did not move.
    metricDisplay: {
      metricId: "timely_filing_at_risk_dollars",
      displayName: "Unbilled open inventory on a running filing clock",
      caveat:
        "Counts every unbilled open claim regardless of runway, so claims a year from their deadline and claims already past it are both in the total; cut by filing_runway_bucket to separate them.",
    },
  };

  it("prints the caveat under the title, with nothing to hover", () => {
    // These cards travel as screenshots. A caveat behind a tooltip ships
    // the label and leaves its bound behind.
    renderCard(bareTurn({ findings: [FINDING] }));

    expect(screen.getByText(/regardless of runway/)).toBeVisible();
  });

  it("does not re-substitute a title the server already composed", () => {
    renderCard(bareTurn({ findings: [FINDING] }));

    // The governed name contains no raw id and appears exactly once — the
    // double-substitution shape is "…running filing clock on a running…".
    expect(
      screen.getByText("Unbilled open inventory on a running filing clock: $22,426,000.28"),
    ).toBeInTheDocument();
    expect(screen.queryByText(/timely filing at risk dollars/)).not.toBeInTheDocument();
  });
});

/**
 * The live DATE_BASIS_INVALID envelope, verbatim. Two halves in one
 * string: a sentence for the reader and a bracketed tail for whoever has
 * to fix it — and the card was printing both, under a code chip carrying
 * the code a third time.
 */
const DATE_BASIS_MESSAGE =
  "That metric can't be dated the way this question needs in this warehouse. " +
  "Asking on a different date basis will answer it. " +
  "[DATE_BASIS_INVALID: date basis 'remit' is not allowed for metric 'ar_balance' " +
  "(allowed: ['service', 'submission'])]";

describe("AnswerCard — error copy is the server's, and only the server's", () => {
  it("prints the sentence once and keeps the machine tail off the card", () => {
    renderCard(
      bareTurn({
        status: "error",
        error: { code: "DATE_BASIS_INVALID", message: DATE_BASIS_MESSAGE },
      }),
    );

    expect(
      screen.getByText(/That metric can't be dated the way this question needs/),
    ).toBeInTheDocument();
    // The code chip carries the code; the tail repeated it beside a raw
    // metric id and a Python list literal.
    expect(screen.getAllByText("DATE_BASIS_INVALID")).toHaveLength(1);
    expect(screen.queryByText(/ar_balance/)).not.toBeInTheDocument();
    expect(screen.queryByText(/\['service', 'submission'\]/)).not.toBeInTheDocument();
  });

  it("recommends nothing of its own", () => {
    renderCard(
      bareTurn({
        status: "error",
        error: { code: "DATE_BASIS_INVALID", message: DATE_BASIS_MESSAGE },
      }),
    );

    // The client composes no recovery sentence: naming bases here is how a
    // card came to recommend one the same message declares illegal. Only
    // what the server wrote is on screen.
    expect(screen.queryByText(/posting date/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/service, submission/i)).not.toBeInTheDocument();
  });

  it("keeps a tail whose code is not this error's own", () => {
    renderCard(
      bareTurn({
        status: "error",
        error: {
          code: "PLAN_INVALID",
          message: "That plan could not be built. [DATE_BASIS_INVALID: from the inner failure]",
        },
      }),
    );

    // Not provably a duplicate of the chip, so it is not removed — this
    // function does not guess about content it did not put there.
    expect(screen.getByText(/from the inner failure/)).toBeInTheDocument();
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

/**
 * Sellable analysts take numbers to meetings.
 *
 * The copy action is client-side only and composed from payload already on
 * screen — and the one thing it must never do is hand over the findings
 * without the caveats that bound them. `answerToText` is unit-tested in
 * `lib/export.test.ts`; what is asserted here is that the card offers it
 * on a finished answer, does not offer it on a turn with nothing to take
 * away, and tells the reader what it is going to include.
 */
describe("AnswerCard — taking the answer out of the browser", () => {
  it("offers a copy action on a finished answer, and says the caveats travel", () => {
    renderCard(turn());
    const button = screen.getByRole("button", { name: /Copy answer/ });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute("title", expect.stringContaining("every caveat"));
    expect(button).toHaveAttribute("title", expect.stringContaining("Nothing leaves this browser"));
  });

  it("offers nothing to copy while the turn is still streaming", () => {
    const streaming: TurnRecord = {
      ...turn(),
      answer: { ...turn().answer, status: "streaming" },
    };
    renderCard(streaming);
    expect(screen.queryByRole("button", { name: /Copy answer/ })).not.toBeInTheDocument();
  });

  it("offers nothing to copy on a turn with no findings and no prose", () => {
    const bare: TurnRecord = {
      ...turn(),
      answer: { ...turn().answer, narrative: "", findings: [] },
    };
    renderCard(bare);
    expect(screen.queryByRole("button", { name: /Copy answer/ })).not.toBeInTheDocument();
  });
});

/**
 * What a restored turn is allowed to leave silent.
 *
 * Re-opening a session replays what the server kept. On the payload
 * generations that do not persist the composed prose, the write-up is
 * simply absent — and because what DOES survive a restore is the caveats,
 * a silent gap leaves yesterday's answer looking like caveats with the
 * answer removed. The absence is stated instead.
 */
describe("AnswerCard — a turn rebuilt from history", () => {
  function restored(overrides: Partial<TurnRecord["answer"]> = {}): TurnRecord {
    return {
      id: "turn_restored",
      index: 0,
      submission: { utterance: "What is our denial rate by payer for July 2026?" },
      answer: {
        ...emptyAnswer(),
        status: "complete",
        rehydrated: true,
        narrative: "",
        findings: [
          {
            referent: { value: "F1", kind: "finding" },
            title: "State Medicaid MCO: 29.5% denial rate",
            statement: "State Medicaid MCO ranks #1 by denial rate.",
            metricRefs: ["denial_rate"],
            values: { denial_rate: 0.295082 },
            grade: "direct",
            directionOfGood: "neutral",
            confidence: "high",
            suggestedRefinements: [],
          },
        ],
        ...overrides,
      },
    };
  }

  it("says the write-up was not stored rather than leaving a gap", () => {
    renderCard(restored());
    expect(
      screen.getByText(/The written analysis was not stored for this turn/),
    ).toBeInTheDocument();
  });

  it("says nothing of the sort when the store did keep the prose", () => {
    renderCard(restored({ narrative: "Among the payers that cleared reporting…" }));
    expect(
      screen.queryByText(/The written analysis was not stored for this turn/),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/Among the payers that cleared reporting/)).toBeInTheDocument();
  });
});

/**
 * The governed conversation→worklist bridge, on the answer card.
 *
 * `WORKLIST_ATTACHED` is the sentence that says the ranked cards are the
 * detection feed's work and NOT findings this turn computed. Left in the
 * turn's general warning list it renders above the findings and far from
 * the cards it is about — so a reader meets eight ranked dollar figures
 * and finds the disclaimer somewhere else entirely. It is MOVED to open
 * the worklist block, and moved rather than duplicated.
 */
describe("AnswerCard — a turn carrying the ranked worklist", () => {
  const WORKLIST = {
    matchedOn: "concept" as const,
    matchedId: "work_prioritization",
    statement: "8 of 33 ranked cards at watermark wm_003, highest governed priority first.",
    label: "What to work first",
    description: "The ranked anomaly worklist at this watermark.",
    formulaVersion: "anomaly_priority@3",
    watermarkId: "wm_003",
    items: [],
    lanes: [],
    totalItems: 33,
    limit: 8,
    totalRecoverableCentsEstimate: 83_050_193,
    warnings: [],
  };

  const ATTACHED = {
    type: "warning" as const,
    code: "WORKLIST_ATTACHED",
    severity: "info" as const,
    message:
      "worklist_attached: this answer also carries the ranked anomaly worklist (8 of 33 cards). The cards are the detection feed's; they are not findings this turn computed.",
    structured: true,
  };

  it("opens the worklist with the attachment sentence, exactly once", () => {
    renderCard(bareTurn({ worklist: WORKLIST, warnings: [ATTACHED] }));

    const matches = screen.getAllByText(/they are not findings this turn computed/);
    expect(matches).toHaveLength(1);
    expect(screen.getByText(/8 of 33 ranked cards at watermark wm_003/)).toBeInTheDocument();
  });

  it("leaves the turn's other warnings in the warning list", () => {
    renderCard(
      bareTurn({
        worklist: WORKLIST,
        warnings: [
          ATTACHED,
          {
            type: "warning",
            code: "POPULATION_CAVEAT",
            severity: "caution",
            message: "population_caveat: this total counts every unbilled open claim",
            structured: true,
          },
        ],
      }),
    );

    expect(screen.getByText("How to read this number")).toBeInTheDocument();
    expect(
      screen.getByText(/this total counts every unbilled open claim/),
    ).toBeInTheDocument();
  });

  it("keeps the sentence in the warning list when there is no worklist to move it to", () => {
    renderCard(bareTurn({ warnings: [ATTACHED] }));

    // Nothing is ever silently dropped: with no worklist block to open,
    // the warning renders where every other warning does.
    expect(screen.getByText("Worklist attached")).toBeInTheDocument();
    expect(
      screen.getByText(/they are not findings this turn computed/),
    ).toBeInTheDocument();
  });
});
