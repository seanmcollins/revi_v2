/**
 * The A/B, judged by its invariants rather than by its looks.
 *
 * Two refined layouts move a great deal of an answer somewhere else, and
 * the whole risk of that is a caveat quietly not arriving. So the tests
 * that matter here are conservation tests: count what the payload says,
 * count what the reader can reach, and require the two to be equal.
 *
 * The payload below is the live worklist answer's warning set (session
 * `sess_a4610c1892f5`, twelve warnings) plus a refused ranking, which is
 * the pair of shapes the layouts have to handle at once: a verdict that
 * must lead, and eleven cautions that must not.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { AnswerCard } from "@/components/answer/AnswerCard";
import { TooltipProvider } from "@/components/ui/tooltip";
import typedTurns from "@/lib/__fixtures__/live-typed-turns.json";
import RAW_SAMPLES from "@/lib/__fixtures__/wire-samples.json";
import { resetAnswerVariantCache, setAnswerVariant } from "@/lib/answerVariant";
import { mapWorklist, parseTurnResponse, turnResponseToEvents } from "@/lib/contract";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import {
  applyEventToAnswer,
  emptyAnswer,
  useSessionStore,
  type TurnRecord,
} from "@/lib/store";
import type { ChartSpec, Finding, WarningEvent } from "@/lib/types";

/* eslint-disable-next-line @typescript-eslint/no-explicit-any */
const SAMPLES = RAW_SAMPLES as any;

beforeAll(() => {
  // jsdom implements no layout, so it ships no `scrollIntoView` — and a
  // referent chip's whole job is to scroll to what it cites. Stubbed
  // rather than guarded in the product: a browser always has it, and a
  // guard would quietly turn a real breakage into a no-op.
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    writable: true,
    value: () => {},
  });
  // jsdom has no matchMedia; the impact stat's count-up and the charts
  // both ask it whether motion is reduced.
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

/**
 * THE REFUSAL, IN THE ENGINE'S CURRENT WORDS — read from a fixture, not
 * retyped into this file.
 *
 * It used to be a literal: "52 of the 52 publishable denial rate cells
 * carry a suppressed numerator, so no order was published." The engine
 * rewrote that sentence in round 6 for the reader who has to act on it —
 * meaning first, the count stated once in words, "too small to measure
 * exactly" in place of three words for two ideas — and this file went on
 * asserting the old one for months, which is the failure mode of every
 * hardcoded engine string: the test keeps passing and stops describing
 * the product.
 *
 * `live-typed-turns.json` is captured by `scripts/capture-fixtures.mjs`
 * from a TYPED turn, so re-capturing it costs no model call. Reading the
 * sentence from there means the next rewrite updates this test by being
 * captured, rather than by somebody noticing.
 */
const RANKING_REFUSED_SENTENCE: string =
  (typedTurns.bounded_ranking.warnings_v2 as Array<{ code: string; message: string }>).find(
    (w) => w.code === "RANKING_REFUSED",
  )?.message ?? "";

const WARNINGS: WarningEvent[] = [
  {
    type: "warning",
    code: "RANKING_REFUSED",
    severity: "caution",
    message: RANKING_REFUSED_SENTENCE,
    structured: true,
  },
  {
    type: "warning",
    code: "PREMISE_PARTIAL",
    severity: "caution",
    message:
      "premise_partial: You asked about a doubling in denial rate. It did not double — denial rate rose 11.5%.",
    structured: true,
  },
  {
    type: "warning",
    code: "WINDOW_ASSUMED",
    severity: "caution",
    message:
      "window_assumed: the question named no period, so I used 2026-07-01..2026-07-31 on the service basis.",
    structured: true,
  },
  {
    type: "warning",
    code: "FINDINGS_TRUNCATED",
    severity: "caution",
    message: "findings_truncated: 3 of 12 computed cells are published as findings.",
    structured: true,
  },
  {
    type: "warning",
    code: "ALTERNATE_BASIS_USED",
    severity: "caution",
    message: "alternate_basis_used: 'denial_rate' is computed on the 'service' basis.",
    count: 3,
    structured: true,
  },
  {
    type: "warning",
    code: "POPULATION_CAVEAT",
    severity: "caution",
    message: "population_caveat: claims still awaiting their first remittance are excluded.",
    structured: true,
  },
  {
    type: "warning",
    code: "SUPPRESSION_APPLIED",
    severity: "info",
    message: "suppression: cells counting fewer than 11 entities are withheld entirely.",
    structured: true,
  },
  {
    type: "warning",
    code: "TRANSFORM_SKIPPED",
    severity: "info",
    message: "transform 'compare' skipped: the question carries no comparison window",
    structured: true,
  },
  {
    type: "warning",
    code: "PROBE_FAMILIES_EMPTY",
    severity: "caution",
    message:
      "probe_families_empty: 8 metric famil(ies) on this plan were read and produced no published finding, so nothing above speaks for them: denial_rate (portfolio_denial_trend, 1 row(s)); cash_posted (portfolio_cash_trend, 1 row(s)).",
    structured: true,
  },
];

function finding(value: string, over: Partial<Finding> = {}): Finding {
  return {
    referent: { value, kind: "finding" },
    title: `Pinnacle HMO ${value}: 47.2% denial rate`,
    statement: `Pinnacle HMO ${value}: 47.2% denial rate over 2026-07-01..2026-07-31. No position is claimed for it.`,
    metricRefs: ["denial_rate"],
    values: { denial_rate: 0.472 },
    grade: "direct",
    directionOfGood: "down_is_good",
    confidence: "high",
    suggestedRefinements: [],
    measured: { metricId: "denial_rate", value: 47.2, unit: "percent" },
    ...over,
  };
}

function chart(id: string, frameId: string): ChartSpec {
  return {
    id,
    kind: "bar",
    frameId,
    title: `denial rate — ${frameId}`,
    unit: "percent",
    series: [{ key: "denial_rate", label: "denial rate", role: "current" }],
    rows: [
      { label: "Atlas Commercial", values: { denial_rate: 12.1 } },
      { label: "Meridian Health", values: { denial_rate: 9.4 } },
      { label: "State Medicaid MCO", values: { denial_rate: 8.2 } },
    ],
  };
}

function turn(over: Partial<TurnRecord["answer"]> = {}): TurnRecord {
  return {
    id: "turn_1",
    index: 0,
    submission: { utterance: "Rank our plans by denial rate for July 2026 — worst first." },
    answer: {
      ...emptyAnswer(),
      status: "complete",
      investigationId: "inv_1",
      answerGrade: "direct",
      metric: {
        metrics: [{ id: "denial_rate", contractVersion: 2 }],
        pack: { packId: "base-rcm", version: "1.0.0" },
      },
      header: {
        window: { start: "2026-07-01", end: "2026-07-31", basis: "service" },
        filters: [],
        watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
        packVersion: { packId: "base-rcm", version: "1.0.0" },
      },
      narrative:
        "Denial rate is concentrated in three plans F1, F2 and F3, and the tail carries suppressed numerators.",
      findings: [finding("F1"), finding("F2"), finding("F3")],
      charts: [chart("chart_main", "main"), chart("chart_premise", "premise")],
      warnings: WARNINGS,
      evidence: {
        probes: [
          {
            probeId: "main",
            probeHash: "h1",
            kind: "aggregation",
            description: "denial rate by plan",
            metrics: [],
            cacheHit: false,
            truncated: false,
            suppressedCells: 0,
            durationMs: 12,
          },
          {
            probeId: "premise",
            probeHash: "h2",
            kind: "aggregation",
            description: "denial rate over the window",
            metrics: [],
            cacheHit: true,
            truncated: false,
            suppressedCells: 0,
            durationMs: 8,
          },
        ],
        warehouseQueries: 1,
        cacheHits: 1,
        zeroProbeTurn: false,
      },
      ...over,
    },
  };
}

function renderCard(record: TurnRecord = turn()) {
  return render(
    <TooltipProvider>
      <AnswerCard turn={record} />
    </TooltipProvider>,
  );
}

function codesIn(root: HTMLElement | Document): string[] {
  return [...root.querySelectorAll("[data-warning-code]")].map(
    (el) => el.getAttribute("data-warning-code") ?? "",
  );
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetAnswerVariantCache();
  useSessionStore.setState({
    settings: DEFAULT_SETTINGS,
    drawerTurnId: null,
    focusedReferent: null,
    referents: {
      F1: {
        referent: { value: "F1", kind: "finding" },
        turnId: "turn_1",
        label: "Pinnacle HMO F1: 47.2% denial rate",
      },
    },
  });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  resetAnswerVariantCache();
  useSessionStore.setState({ settings: DEFAULT_SETTINGS, drawerTurnId: null });
});

/* ------------------------------------------------------------------ */
/* Which layout renders                                                */
/* ------------------------------------------------------------------ */

describe("AnswerCard — the toggle picks the layout", () => {
  it("renders the CALM layout when nothing has been chosen — it is the default", () => {
    const { container } = renderCard();
    expect(container.querySelector("[data-answer-variant]")).toHaveAttribute(
      "data-answer-variant",
      "b",
    );
    // And the default surface is the one the A/B chose: the verdicts on
    // the answer, everything else behind the line that counts it.
    expect(codesIn(container)).toEqual(["RANKING_REFUSED", "PREMISE_PARTIAL"]);
  });

  it("still renders the retired layout from a ?variant=current link", () => {
    window.history.replaceState(null, "", "/?variant=current");
    const { container } = renderCard();
    expect(container.querySelector("[data-answer-variant]")).toHaveAttribute(
      "data-answer-variant",
      "current",
    );
    // The control it always was: every warning its own banner.
    expect(codesIn(container)).toHaveLength(WARNINGS.length);
  });

  it("renders the calm layout from a ?variant=b link", () => {
    window.history.replaceState(null, "", "/?variant=b");
    const { container } = renderCard();
    expect(container.querySelector("[data-answer-variant]")).toHaveAttribute(
      "data-answer-variant",
      "b",
    );
  });

  it("renders the detailed layout from the stored choice", () => {
    setAnswerVariant("a");
    const { container } = renderCard();
    expect(container.querySelector("[data-answer-variant]")).toHaveAttribute(
      "data-answer-variant",
      "a",
    );
  });
});

/* ------------------------------------------------------------------ */
/* Nothing is deleted, only relocated                                  */
/* ------------------------------------------------------------------ */

describe("every warning survives the move — calm layout", () => {
  it("renders the verdicts and hides nothing else: the counts add up", async () => {
    setAnswerVariant("b");
    const { container } = renderCard();

    // On the answer: the verdicts, and only the verdicts.
    const onAnswer = codesIn(container);
    expect(onAnswer).toEqual(["RANKING_REFUSED", "PREMISE_PARTIAL"]);

    // Behind the integrity line: everything else, and exactly everything
    // else. This is the conservation test — payload in, reader out.
    await userEvent.click(screen.getByRole("button", { name: "7 things to know" }));
    const dialog = await screen.findByRole("dialog");
    const inSheet = codesIn(dialog);

    expect(onAnswer.length + inSheet.length).toBe(WARNINGS.length);
    expect([...onAnswer, ...inSheet].sort()).toEqual(WARNINGS.map((w) => w.code).sort());
  });

  it("prints each caveat's own sentence in the sheet", async () => {
    setAnswerVariant("b");
    renderCard();
    await userEvent.click(screen.getByRole("button", { name: "7 things to know" }));
    const dialog = await screen.findByRole("dialog");

    expect(within(dialog).getByText("Window assumed")).toBeInTheDocument();
    expect(
      within(dialog).getByText(/the question named no period, so I used/),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("Small cells were suppressed")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* The monitor path — the one warning that was landing nowhere            */
/* ------------------------------------------------------------------ */

/**
 * A REFUSED MONITOR DECLARATION REACHES THE READER.
 *
 * Live, "Monitor Pinnacle Health Plan denial rate and alert me if it moves
 * more than $5,000" came back with `outcome: answer`, `monitor: null`, six
 * prose `warnings` and five `warnings_v2`. The missing sixth was the
 * refusal — a threshold in cents over a metric measured as a ratio — and
 * it was appended to `warnings` AFTER `warnings_v2` had been built, so
 * `readTurnWarnings` (which prefers the structured list whenever it is
 * non-empty) dropped it. On screen: an ordinary answer, no confirmation,
 * no warning, and an analyst who walks away believing they are being
 * monitored.
 *
 * The conservation rule this file already enforces is what would have
 * caught it, so it is extended here to the shape that broke it: whatever
 * the payload classified, the reader can reach — on the answer, in the
 * sheet, or in the refusal note that renders where the confirmation would
 * have gone.
 */
describe("a refused monitor declaration is not silently dropped", () => {
  const REFUSAL =
    "this turn read as a monitor declaration, and the monitor was NOT created: a threshold in " +
    "'cents' is only honest for a 'money_cents' contract, and this monitor measures 'ratio'. " +
    "State it in percentage points or as a relative percentage.";

  const refusalWarning: WarningEvent = {
    type: "warning",
    code: "MONITOR_NOT_CREATED",
    severity: "caution",
    message: REFUSAL,
    structured: true,
  };

  const refusedTurn = () =>
    turn({
      warnings: [...WARNINGS, refusalWarning],
      monitorRefused: { reason: REFUSAL, legalAlternatives: ["percentage points", "relative %"] },
    });

  it("renders the refusal where the confirmation would have gone", () => {
    setAnswerVariant("b");
    const { container } = renderCard(refusedTurn());

    const note = container.querySelector("[data-monitor-refused]");
    expect(note, "a refused declaration must render its own note").not.toBeNull();
    // The server's sentence, verbatim — it names the units this contract
    // WOULD take, which is the only part that tells the analyst how to ask
    // again.
    expect(note?.textContent).toContain(REFUSAL);
    // And the fact that decides tomorrow morning, in words.
    expect(screen.getByText(/Nothing is being monitored/)).toBeInTheDocument();
    // It is an alert, not a footnote: a state the reader was expecting did
    // not happen.
    expect(note).toHaveAttribute("role", "alert");
  });

  it("conserves every classified warning — rendered + sheet ≥ the payload", async () => {
    setAnswerVariant("b");
    const record = refusedTurn();
    const payload = record.answer.warnings;
    const { container } = renderCard(record);

    const onAnswer = codesIn(container);
    await userEvent.click(screen.getByRole("button", { name: /things to know/ }));
    const dialog = await screen.findByRole("dialog");
    const inSheet = codesIn(dialog);

    // THE ASSERTION THE DROP WOULD HAVE FAILED. Not equality — the refusal
    // is deliberately said twice, once as a caveat and once as the note
    // above the answer — but nothing the payload classified may reach the
    // reader zero times.
    expect(onAnswer.length + inSheet.length).toBeGreaterThanOrEqual(payload.length);
    const reachable = new Set([...onAnswer, ...inSheet]);
    for (const warning of payload) {
      expect(reachable.has(warning.code), `${warning.code} must reach the reader`).toBe(true);
    }
    expect(reachable.has("MONITOR_NOT_CREATED")).toBe(true);
  });

  it("says nothing about monitoring on an ordinary answer", () => {
    setAnswerVariant("b");
    const { container } = renderCard();
    expect(container.querySelector("[data-monitor-refused]")).toBeNull();
    expect(container.querySelector("[data-monitor-declaration]")).toBeNull();
  });

  /**
   * THE LIVE PAYLOAD, THROUGH THE REAL SEAM.
   *
   * `wire-samples.json#monitor_refused_turn` is the exec's own repro — "Monitor
   * Pinnacle Health Plan denial rate and alert me if it moves more than
   * $5,000" — captured verbatim from a running deployment: `outcome:
   * answer`, `monitor: null`, six warnings, six classified warnings, and the
   * `monitor_refused` payload naming the four phrasings that would work.
   */
  it("renders the live refusal end to end, from the wire", async () => {
    setAnswerVariant("b");
    const parsed = parseTurnResponse(SAMPLES.monitor_refused_turn, {
      watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
      pack: { packId: "base-rcm", version: "1.0.0" },
    });
    expect(parsed.drift).toEqual([]);
    if (parsed.value?.outcome !== "answer") throw new Error("not an answer");

    let answer = emptyAnswer();
    for (const event of turnResponseToEvents(parsed.value)) {
      answer = applyEventToAnswer(answer, event);
    }
    const { container } = renderCard({
      id: "turn_live",
      index: 0,
      submission: {
        utterance: "Monitor Pinnacle Health Plan denial rate and alert me if it moves more than $5,000.",
      },
      answer,
    });

    // The note, where the confirmation would have been.
    const note = container.querySelector("[data-monitor-refused]");
    expect(note).not.toBeNull();
    expect(note?.textContent).toContain("Pinnacle Health Plan denial rate");
    expect(note?.textContent).toContain("only honest for a 'money_cents' contract");
    // The phrasings that WOULD work — a refusal with no way forward is a
    // wall, and these are the server's own words for the way through it.
    expect(note?.textContent).toContain("more than half a point");

    // And the conservation rule holds on the live payload: every code the
    // server classified is reachable.
    const payload = SAMPLES.monitor_refused_turn.warnings_v2 as Array<{ code: string }>;
    const onAnswer = codesIn(container);
    await userEvent.click(screen.getByRole("button", { name: /things to know/ }));
    const dialog = await screen.findByRole("dialog");
    const reachable = new Set([...onAnswer, ...codesIn(dialog)]);
    expect(onAnswer.length + codesIn(dialog).length).toBeGreaterThanOrEqual(payload.length);
    for (const warning of payload) expect(reachable.has(warning.code)).toBe(true);
  });
});

describe("every warning survives the move — detailed layout", () => {
  it("groups the cautions without dropping one", async () => {
    setAnswerVariant("a");
    const { container } = renderCard();

    // Closed, the group shows only the verdicts as warnings.
    expect(codesIn(container)).toEqual(["RANKING_REFUSED", "PREMISE_PARTIAL"]);

    await userEvent.click(screen.getByRole("button", { name: /7 things to know/ }));
    const all = codesIn(container);
    expect(all.length).toBe(WARNINGS.length);
    expect([...all].sort()).toEqual(WARNINGS.map((w) => w.code).sort());
  });
});

/* ------------------------------------------------------------------ */
/* The verdict is never tucked away                                    */
/* ------------------------------------------------------------------ */

describe("verdict-class codes lead, in every layout", () => {
  for (const variant of ["current", "a", "b"] as const) {
    it(`keeps RANKING_REFUSED and PREMISE_PARTIAL on the answer — ${variant}`, () => {
      setAnswerVariant(variant);
      const { container } = renderCard();

      for (const code of ["RANKING_REFUSED", "PREMISE_PARTIAL"]) {
        const el = container.querySelector(`[data-warning-code="${code}"]`);
        expect(el, `${code} must be on the answer in ${variant}`).not.toBeNull();
        expect(el).toHaveAttribute("data-verdict", "true");
      }
    });
  }

  it("says the refusal in the engine's own words, not in a summary", () => {
    setAnswerVariant("b");
    renderCard();
    // The engine's CURRENT sentence, captured rather than retyped — the
    // whole clause, so a layout that truncated or paraphrased it fails.
    expect(RANKING_REFUSED_SENTENCE).toMatch(/^ranking_refused: /);
    expect(
      screen.getByText(RANKING_REFUSED_SENTENCE.replace(/^ranking_refused: /, "")),
    ).toBeInTheDocument();
    expect(screen.getByText(/No ranking was published/)).toBeInTheDocument();
  });

  it("never counts a verdict among the things to know", () => {
    setAnswerVariant("b");
    renderCard();
    // Nine warnings, two of them verdicts.
    expect(screen.getByRole("button", { name: "7 things to know" })).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* The integrity line                                                  */
/* ------------------------------------------------------------------ */

describe("the integrity line counts what it opens", () => {
  it("states the verification, the caveats and the checks, all from the payload", () => {
    setAnswerVariant("b");
    renderCard();
    expect(screen.getByText("Verified against your data")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "7 things to know" })).toBeInTheDocument();
    // Two probes on the evidence bundle, said as two.
    expect(screen.getByRole("button", { name: "2 checks" })).toBeInTheDocument();
  });

  it("the caveat count equals the rows the sheet actually lists", async () => {
    setAnswerVariant("b");
    renderCard();
    await userEvent.click(screen.getByRole("button", { name: "7 things to know" }));
    const dialog = await screen.findByRole("dialog");
    expect(codesIn(dialog)).toHaveLength(7);
  });

  it("refuses to claim verification over a turn that read nothing", () => {
    setAnswerVariant("b");
    renderCard(
      turn({
        evidence: { probes: [], warehouseQueries: 0, cacheHits: 0, zeroProbeTurn: true },
      }),
    );
    expect(screen.getByText("Answered without reading your data")).toBeInTheDocument();
    expect(screen.queryByText("Verified against your data")).not.toBeInTheDocument();
  });

  it("says where the answer came from when it came from the session's cache", () => {
    setAnswerVariant("b");
    renderCard(
      turn({
        evidence: { probes: [], warehouseQueries: 0, cacheHits: 4, zeroProbeTurn: true },
      }),
    );
    expect(
      screen.getByText("Answered from checks already run in this session"),
    ).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* The facts, and the way back to them                                 */
/* ------------------------------------------------------------------ */

describe("a referent chip opens the fact it cites", () => {
  it("opens the Evidence rail on this fact's own turn — calm layout", async () => {
    setAnswerVariant("b");
    renderCard();

    // The citation in the narrative, not a card heading.
    const chip = screen.getByRole("button", { name: /^F1: .* — open in Evidence$/ });
    await userEvent.click(chip);

    expect(useSessionStore.getState().drawerTurnId).toBe("turn_1");
    expect(useSessionStore.getState().focusedReferent).toBe("F1");
  });

  it("scrolls to the card instead, in the layouts that keep cards", async () => {
    setAnswerVariant("current");
    renderCard();
    const chip = screen.getAllByRole("button", { name: /^F1: .* — go to this finding$/ })[0];
    await userEvent.click(chip);

    expect(useSessionStore.getState().focusedReferent).toBe("F1");
    expect(useSessionStore.getState().drawerTurnId).toBeNull();
  });

  it("always offers a named way into the facts, chips or no chips", async () => {
    setAnswerVariant("b");
    renderCard();
    const link = screen.getByRole("button", { name: /3 facts behind this answer/ });
    await userEvent.click(link);
    expect(useSessionStore.getState().drawerTurnId).toBe("turn_1");
  });
});

/* ------------------------------------------------------------------ */
/* One chart                                                           */
/* ------------------------------------------------------------------ */

describe("the calm layout draws one figure", () => {
  it("draws the engine's primary frame and no other", () => {
    setAnswerVariant("b");
    const { container } = renderCard();
    const figures = container.querySelectorAll("figure");
    expect(figures).toHaveLength(1);
    expect(figures[0].textContent).toContain("denial rate — main");
    // The one it did not draw is named and reachable, not dropped.
    expect(screen.getByRole("button", { name: /1 more chart/ })).toBeInTheDocument();
  });

  it("draws every chart in the layouts that lead with them", () => {
    setAnswerVariant("current");
    const { container } = renderCard();
    expect(container.querySelectorAll("figure")).toHaveLength(2);
  });
});

/* ------------------------------------------------------------------ */
/* A restored turn with no write-up                                    */
/* ------------------------------------------------------------------ */

/* ------------------------------------------------------------------ */
/* The conditions the A/B shipped under                                */
/* ------------------------------------------------------------------ */

/** Is `a` before `b` in the document? The fold test, without a viewport. */
function precedes(a: Element | null, b: Element | null): boolean {
  if (!a || !b) return false;
  return (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
}

const line = (root: HTMLElement) => root.querySelector("[data-integrity-tone]");

describe("CONDITION 1 — the caveat count is on the first screen, on every path", () => {
  /**
   * jsdom has no layout, so "above the fold" is asserted as the thing
   * that makes it true: the integrity line comes BEFORE the block that
   * would otherwise push it down. On the restored path that block is the
   * fact list, which is three rows on a worklist turn and thirty on
   * `inv_534567aee34a`.
   */
  it("hoists the line above the facts when the facts are the answer", () => {
    setAnswerVariant("b");
    const { container } = renderCard(
      turn({ rehydrated: true, narrative: "", charts: [] }),
    );
    const facts = container.querySelector('section[aria-label="Findings"]');
    expect(facts, "the restored path renders its facts inline").not.toBeNull();
    expect(precedes(line(container), facts)).toBe(true);
  });

  it("keeps the line as the closing signature when there is writing", () => {
    setAnswerVariant("b");
    const { container } = renderCard();
    const prose = screen.getByText(/Denial rate is concentrated in three plans/);
    expect(precedes(prose, line(container))).toBe(true);
  });

  it("says it exactly once, wherever it sits", () => {
    setAnswerVariant("b");
    const restored = renderCard(turn({ rehydrated: true, narrative: "", charts: [] }));
    expect(restored.container.querySelectorAll("[data-integrity-tone]")).toHaveLength(1);
    cleanup();
    const live = renderCard();
    expect(live.container.querySelectorAll("[data-integrity-tone]")).toHaveLength(1);
  });

  it("states how many of the caveats change how a number reads", () => {
    setAnswerVariant("b");
    renderCard();
    // Seven things to know, five of them cautions.
    expect(
      screen.getByText("5 change how a number here should be read"),
    ).toBeInTheDocument();
  });

  it("says so plainly when every caveat changes a reading, and when none does", () => {
    setAnswerVariant("b");
    renderCard(
      turn({
        warnings: [
          {
            type: "warning",
            code: "POPULATION_CAVEAT",
            severity: "caution",
            message: "population_caveat: claims awaiting a first remittance are excluded.",
            structured: true,
          },
        ],
      }),
    );
    expect(
      screen.getByText("each one changes how a number here should be read"),
    ).toBeInTheDocument();
    cleanup();
    renderCard(
      turn({
        warnings: [
          {
            type: "warning",
            code: "TRANSFORM_SKIPPED",
            severity: "info",
            message: "transform 'compare' skipped: the question carries no comparison window",
            structured: true,
          },
        ],
      }),
    );
    expect(
      screen.getByText("notes about how this answer was produced"),
    ).toBeInTheDocument();
  });

  it("marks an assumed window on the context line itself", () => {
    setAnswerVariant("b");
    renderCard();
    // WINDOW_ASSUMED is on this payload: the question named no period.
    expect(screen.getByText(/Jul 2026 \(assumed\)/)).toBeInTheDocument();
  });
});

describe("CONDITION 2 — the answer grade has a home in the calm layout", () => {
  it("says a proxy grade in words, on the line", () => {
    setAnswerVariant("b");
    const { container } = renderCard(turn({ answerGrade: "proxy" }));
    expect(
      screen.getByText("Indicative — computed from a stand-in measure"),
    ).toBeInTheDocument();
    expect(container.querySelector('[data-answer-grade="proxy"]')).not.toBeNull();
  });

  it("says an uncertified grade in the badge's own words", () => {
    setAnswerVariant("b");
    renderCard(turn({ answerGrade: "discovery" }));
    expect(
      screen.getByText("Uncertified — fields nobody has certified for this purpose"),
    ).toBeInTheDocument();
  });

  it("adds nothing when the grade is direct — the expected case", () => {
    setAnswerVariant("b");
    const { container } = renderCard();
    expect(container.querySelector("[data-answer-grade]")).toBeNull();
    expect(screen.getByText("Verified against your data")).toBeInTheDocument();
  });

  it("never presents a proxy answer as a verified one", () => {
    setAnswerVariant("b");
    const { container } = renderCard(turn({ answerGrade: "proxy" }));
    // The clause is still true — it read the warehouse under a governed
    // measure — and the MARK no longer says the evidence is certified.
    expect(line(container)).toHaveAttribute("data-integrity-tone", "qualified");
  });
});

describe("CONDITION 3 — the dot means what the clause says", () => {
  it("is verified only over a governed read of the warehouse", () => {
    setAnswerVariant("b");
    const { container } = renderCard();
    expect(line(container)).toHaveAttribute("data-integrity-tone", "verified");
    expect(line(container)?.className).toContain("bg-verified");
  });

  it("is not verified over a turn that read nothing", () => {
    setAnswerVariant("b");
    const { container } = renderCard(
      turn({ evidence: { probes: [], warehouseQueries: 0, cacheHits: 0, zeroProbeTurn: true } }),
    );
    expect(screen.getByText("Answered without reading your data")).toBeInTheDocument();
    expect(line(container)).toHaveAttribute("data-integrity-tone", "unread");
    // The green dot and its halo, both of which shipped under this clause.
    expect(line(container)?.className).not.toContain("bg-verified");
    expect(line(container)?.className).not.toContain("integrity-dot");
  });

  it("is not verified over a read that no governed measure covered", () => {
    setAnswerVariant("b");
    const { container } = renderCard(turn({ metric: undefined }));
    expect(screen.getByText("Computed from your data")).toBeInTheDocument();
    expect(line(container)).toHaveAttribute("data-integrity-tone", "measured");
  });

  it("names the mark for a reader who cannot see it", () => {
    setAnswerVariant("b");
    const { container } = renderCard(
      turn({ evidence: { probes: [], warehouseQueries: 0, cacheHits: 4, zeroProbeTurn: true } }),
    );
    expect(line(container)?.textContent).toContain("No data was read");
  });
});

describe("CONDITION 8 — there is always a way into Evidence", () => {
  it("offers Evidence on a turn with no probes and no findings", async () => {
    setAnswerVariant("b");
    renderCard(
      turn({
        findings: [],
        narrative: "A definition, not a measurement.",
        charts: [],
        evidence: { probes: [], warehouseQueries: 0, cacheHits: 0, zeroProbeTurn: true },
      }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Evidence" }));
    expect(useSessionStore.getState().drawerTurnId).toBe("turn_1");
  });

  it("still counts the checks when there are checks to count", () => {
    setAnswerVariant("b");
    renderCard();
    expect(screen.getByRole("button", { name: "2 checks" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Evidence" })).not.toBeInTheDocument();
  });
});

describe("CONDITION 6 — the signature closes the answer", () => {
  /**
   * The flagship proactive question ("what should my team work on
   * first") carries thirty-three ranked cards. Rendered after the body,
   * they left the integrity line — this layout's closing signature —
   * mid-page with the largest block on the screen below it. A signature
   * followed by the whole document is not a signature.
   */
  const withWorklist = () => {
    const worklist = mapWorklist({
      matched_on: "concept",
      matched_id: "work_prioritization",
      label: "What to work first",
      statement: "8 of 33 ranked cards at watermark wm_003.",
      formula_version: "anomaly_priority@3",
      watermark_id: "wm_003",
      total_items: 33,
      limit: 8,
      items: [
        {
          anomaly_id: "ANM-021",
          provenance: "external_detection",
          priority_formula_version: "anomaly_priority@3",
          source_watermark_id: "wm_003",
          title: "DNFB accumulation: Northgate general-surgery discharges",
          description: "22 unbilled discharges totaling $178,217.",
          category: "dnfb",
          metric_id: "dnfb_dollars",
          severity: "critical",
          lane: "value",
          impact_cents: 17_821_682,
          ranked_on: "detector",
          ranked_impact_cents: 17_821_682,
          priority_score: 0.3286,
          drillable: false,
        },
      ],
      lanes: [],
      warnings_v2: [],
    });
    expect(worklist, "the worklist fixture must pass contract validation").toBeDefined();
    return turn({ worklist: worklist! });
  };

  it("renders the worklist inside the calm body, above the line", () => {
    setAnswerVariant("b");
    const { container } = renderCard(withWorklist());
    const block = screen.getByText("What to work first").closest("section");
    expect(precedes(block, line(container))).toBe(true);
  });

  it("leaves the other layouts' worklist exactly where it was", () => {
    setAnswerVariant("a");
    const { container } = renderCard(withWorklist());
    const block = screen.getByText("What to work first").closest("section");
    const charts = container.querySelectorAll("figure");
    expect(precedes(charts[charts.length - 1], block)).toBe(true);
  });
});

describe("CONDITION 5 — citations read as citations", () => {
  it("collapses a consecutive run into one group and drops a repeat of it", () => {
    setAnswerVariant("b");
    useSessionStore.setState({
      referents: {
        F1: { referent: { value: "F1", kind: "finding" }, turnId: "turn_1", label: "F1" },
        F2: { referent: { value: "F2", kind: "finding" }, turnId: "turn_1", label: "F2" },
        F3: { referent: { value: "F3", kind: "finding" }, turnId: "turn_1", label: "F3" },
      },
    });
    renderCard(
      turn({
        narrative:
          "Three plans carry the movement (F1, F2, F3). The tail is bounded (F1, F2, F3).",
      }),
    );
    // Three tap targets for three facts, not six for two identical
    // parentheticals.
    for (const value of ["F1", "F2", "F3"]) {
      expect(
        screen.getAllByRole("button", { name: new RegExp(`^${value}:`) }),
      ).toHaveLength(1);
    }
    // And the repeat took its parentheses with it.
    expect(screen.getByText(/The tail is bounded\./)).toBeInTheDocument();
  });

  it("never drops a citation the first time it is made", () => {
    setAnswerVariant("b");
    renderCard(turn({ narrative: "The movement is in Atlas (F1) and Meridian (F2)." }));
    // Two separate parentheticals, two citations — the collapse is for a
    // RUN, and these are not consecutive.
    expect(screen.getAllByRole("button", { name: /^F1:/ })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: "F2" })).toHaveLength(1);
  });

  it("moves focus to the fact it cites, not only the viewport", async () => {
    setAnswerVariant("b");
    const { container } = renderCard();
    // The rail is not mounted in this render, so the row the chip targets
    // is the one on the answer; what is asserted is that the jump FOCUSES
    // rather than merely scrolling.
    const row = document.createElement("li");
    row.id = "evidence-fact-F1";
    row.tabIndex = -1;
    container.appendChild(row);

    await userEvent.click(screen.getByRole("button", { name: /^F1: .* — open in Evidence$/ }));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(document.activeElement).toBe(row);
  });
});

describe("CONDITION 7 — the verdict is not printed twice", () => {
  const premiseEcho = () =>
    turn({
      rehydrated: true,
      narrative: "",
      charts: [],
      warnings: [
        {
          type: "warning",
          code: "PREMISE_PARTIAL",
          severity: "caution",
          message:
            "premise_partial: You asked about a doubling in denial rate. It did not double — denial rate rose 11.5%, short of the 100.0% a doubling assumes.",
          structured: true,
        },
      ],
      findings: [
        finding("F1", {
          title:
            "Premise partly supported: You asked about a doubling in denial rate. It did not double — denial rate rose 11.5%, short of the 100.0% a doubling assumes.",
          statement:
            "You asked about a doubling in denial rate. It did not double — denial rate rose 11.5%, short of the 100.0% a doubling assumes.",
        }),
      ],
    });

  it("says the verdict's own sentence once, and the row points at it", () => {
    setAnswerVariant("b");
    renderCard(premiseEcho());
    expect(screen.getAllByText(/short of the 100.0% a doubling assumes/)).toHaveLength(1);
    expect(
      screen.getByText("Stated in full as the verdict at the top of this answer."),
    ).toBeInTheDocument();
    // The fact is still there, still named, still countable.
    expect(screen.getByText("Premise partly supported")).toBeInTheDocument();
  });

  it("still recognizes the echo once the dates have been spelled", () => {
    setAnswerVariant("b");
    renderCard(
      turn({
        rehydrated: true,
        narrative: "",
        charts: [],
        warnings: [
          {
            type: "warning",
            code: "PREMISE_PARTIAL",
            severity: "caution",
            message:
              "premise_partial: You asked about a doubling in denial rate. It did not double — 7.1% → 7.9% vs prior year (2025-01-01..2025-08-02), rose 0.8 points, a 11.5% relative change.",
            structured: true,
          },
        ],
        findings: [
          finding("F1", {
            title: "Premise partly supported: You asked about a doubling in denial rate.",
            statement:
              "You asked about a doubling in denial rate. It did not double — 7.1% → 7.9% vs prior year (2025-01-01..2025-08-02), rose 0.8 points, a 11.5% relative change.",
          }),
        ],
      }),
    );
    // The verdict surface spells the ISO range; the row's raw statement
    // does not. Compared as printed, they are one sentence.
    expect(screen.getAllByText(/rose 0.8 points/)).toHaveLength(1);
    expect(
      screen.getByText("Stated in full as the verdict at the top of this answer."),
    ).toBeInTheDocument();
  });

  it("leaves a finding that says anything of its own untouched", () => {
    setAnswerVariant("b");
    renderCard(turn({ rehydrated: true, narrative: "", charts: [] }));
    expect(
      screen.queryByText("Stated in full as the verdict at the top of this answer."),
    ).not.toBeInTheDocument();
  });
});

describe("CONDITION 9 — no machine date literals on the fact rows", () => {
  it("spells the window the way a reader says it", () => {
    setAnswerVariant("b");
    const { container } = renderCard(
      turn({
        rehydrated: true,
        narrative: "",
        charts: [],
        findings: [
          finding("F1", {
            title: "Atlas Commercial: $33,954.90 denied dollars",
            statement:
              "Atlas Commercial ranks #1 of 12 measured by denied dollars over 2026-07-01..2026-07-31.",
          }),
        ],
      }),
    );
    expect(container.textContent).toContain("Jul 1–31, 2026");
    expect(container.textContent).not.toContain("2026-07-01..2026-07-31");
  });
});

describe("a restored turn keeps its answer in the calm layout", () => {
  const restored = () =>
    turn({ rehydrated: true, narrative: "", header: undefined, charts: [] });

  it("brings the facts back inline when there is no writing to be the answer", () => {
    setAnswerVariant("b");
    renderCard(restored());
    expect(
      screen.getByText(/The written analysis was not stored for this turn/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Pinnacle HMO F1: 47.2% denial rate/)).toBeInTheDocument();
  });

  /**
   * THE NOTE COUNTS WHAT SURVIVED.
   *
   * It named the findings and the context and stopped there — on a turn
   * whose two charts and whose evidence bundle had restored three inches
   * below it. The one sentence a reader has for judging how much of the
   * answer is left was understating the answer, on exactly the page a
   * buyer forwards to a CFO. (The server's own restoration note commits
   * the mirror of this and claims charts while shipping `chart_specs:
   * []`; that half is the API's.)
   */
  it("names the charts and the evidence when they DID come back", () => {
    setAnswerVariant("b");
    const { container } = renderCard(
      turn({ rehydrated: true, narrative: "", header: undefined }),
    );
    const note = container.querySelector("[data-restored-without-prose]");
    expect(note).not.toBeNull();
    expect(note).toHaveTextContent(/The written analysis was not stored for this turn/);
    expect(note).toHaveTextContent(/2 charts/);
    expect(note).toHaveTextContent(/the evidence behind them/);
  });

  it("claims neither when neither came back", () => {
    setAnswerVariant("b");
    const { container } = renderCard(
      turn({ rehydrated: true, narrative: "", header: undefined, charts: [], evidence: undefined }),
    );
    const note = container.querySelector("[data-restored-without-prose]");
    expect(note).toHaveTextContent(/the findings/);
    expect(note).not.toHaveTextContent(/chart/);
    expect(note).not.toHaveTextContent(/evidence/);
  });

  it("says it was restored exactly once", () => {
    setAnswerVariant("current");
    const { container } = renderCard(
      turn({
        rehydrated: true,
        header: {
          window: { start: "2026-07-01", end: "2026-07-31", basis: "service" },
          filters: [],
          watermark: {
            id: "wm_003",
            loadedAt: "2026-08-03 04:10",
            newestDataDate: "2026-08-02",
          },
          packVersion: { packId: "base-rcm", version: "1.0.0" },
          restored: true,
        },
      }),
    );
    // BUG 3: the line and the chip were both saying it.
    const said = (container.textContent ?? "").match(/Restored/g) ?? [];
    expect(said).toHaveLength(1);
  });
});

/* ------------------------------------------------------------------ */
/* CONDITION — the fold may never delete the answer                     */
/* ------------------------------------------------------------------ */

/**
 * ROUND-9 P0, on the default layout and the demo's opening question.
 *
 * On a provisional window the composer opens with the SETTLED reading and
 * the engine publishes the same paragraph as the body of
 * `ADJUDICATION_INCOMPLETE`. Byte-identical, so `foldComposedDisclosures`
 * deleted it — and `ADJUDICATION_INCOMPLETE` is not a verdict code, so the
 * only surviving copy sat inside "things to know", collapsed by default.
 * The first screen then read 12.8% three times and 9.1% not at all.
 *
 * Asserted end to end through the card the analyst actually gets, not on
 * `answer.narrative`, which is the string BEFORE the fold and was never
 * the thing at fault.
 */
describe("a provisional answer still says the settled figure out loud", () => {
  const SETTLED =
    "Through June 2026 — the last period that has finished settling — denial rate reads 9.1%.";
  const PROVISIONAL =
    "July 2026 is 26.3% settled, so the 12.8% it reports is provisional and will move.";

  function provisionalTurn(): TurnRecord {
    return turn({
      narrative: `${SETTLED} ${PROVISIONAL} Summit Peak and Lakewood carry most of it.`,
      warnings: [
        {
          type: "warning",
          code: "ADJUDICATION_INCOMPLETE",
          severity: "caution",
          message: `adjudication_incomplete: ${SETTLED} ${PROVISIONAL}`,
          structured: true,
        },
      ],
    });
  }

  it("keeps the settled sentence in the prose, uncollapsed, on the default layout", () => {
    setAnswerVariant("b");
    const { container } = renderCard(provisionalTurn());
    const prose = container.querySelector("[data-answer-prose]") ?? container;
    expect(prose.textContent).toContain("9.1%");
    expect(prose.textContent).toContain(SETTLED);
  });

  it("keeps it on every layout, not only the default one", () => {
    for (const variant of ["a", "b", "current"] as const) {
      cleanup();
      setAnswerVariant(variant);
      const { container } = renderCard(provisionalTurn());
      expect(container.textContent, `variant ${variant}`).toContain(SETTLED);
    }
  });
});
