/**
 * The calm layout on turns that were actually ASKED.
 *
 * Every screenshot the A/B was judged on was a restored turn, read back
 * through `GET /v1/investigations/{iid}`, where the composed prose is not
 * among the things the server keeps. `factsInline` is therefore true on
 * all of them, and B degrades to a context line over a fact list — which
 * means the panel that chose this layout never once saw its actual
 * thesis: the writing as the answer, the facts in the rail, one figure,
 * one integrity line. All three reviewers made re-verifying on a live
 * turn a condition of the flip.
 *
 * So `src/lib/__fixtures__/live-turns.json` is two turns run against the
 * running API (`POST /v1/sessions/{sid}/turns`, watermark wm_003, LLM
 * mode claude-agent-sdk), captured as the authoritative `turn_complete`
 * frame — the same payload `apiDriver` parses:
 *
 *   narrative_premise  "Our denial rate doubled since January — why?"
 *                      sess_ffc7421e03b2 / inv_488e1e9406f5 —
 *                      PREMISE_PARTIAL, 1,826 characters of composed
 *                      prose, 2 findings, 2 frames, 3 probes.
 *   worklist_leads     "What should my denial team work on first this
 *                      week to recover the most cash?"
 *                      sess_22a282b55677 / inv_03dc9db507c3 —
 *                      WORKLIST_LEADS, 2,265 characters of prose, the
 *                      33-card ranked worklist, 1 finding, 4 frames,
 *                      9 probes, 13 warnings.
 *
 * These are the shapes the layout is defaulting to, and this file is the
 * screenshot: what renders, in what order, on the path the A/B never saw.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import live from "@/lib/__fixtures__/live-turns.json";
import { MemoryRouter } from "react-router-dom";

import { AnswerCard } from "@/components/answer/AnswerCard";
import { ContextPanel } from "@/components/workspace/ContextPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { resetAnswerVariantCache, setAnswerVariant } from "@/lib/answerVariant";
import { parseTurnResponse, turnResponseToEvents } from "@/lib/contract";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import {
  applyEventToAnswer,
  emptyAnswer,
  useSessionStore,
  type TurnRecord,
} from "@/lib/store";

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
  Object.defineProperty(Element.prototype, "scrollIntoView", {
    configurable: true,
    writable: true,
    value: () => {},
  });
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

const PIN = {
  watermark: { id: "wm_003", loadedAt: "2026-08-03T04:10:00", newestDataDate: "2026-08-02" },
  pack: { packId: "base-rcm", version: "1.0.0" },
};

interface Case {
  session_id: string;
  question: string;
  turn_complete: unknown;
}

/** A captured live turn, through the real seam, into a turn record. */
function turnFrom(name: "narrative_premise" | "worklist_leads"): TurnRecord {
  const record = (live as unknown as Record<string, Case>)[name];
  const parsed = parseTurnResponse(record.turn_complete, PIN);
  expect(parsed.drift, `${name}: the live payload must parse with no drift`).toEqual([]);
  expect(parsed.value).not.toBeNull();
  let answer = emptyAnswer();
  for (const event of turnResponseToEvents(parsed.value!)) {
    answer = applyEventToAnswer(answer, event);
  }
  return {
    id: `turn_${name}`,
    index: 0,
    submission: { utterance: record.question },
    answer,
  };
}

/** The referent registry the store builds as the turn streams. */
function seed(turn: TurnRecord): void {
  const referents = Object.fromEntries(
    turn.answer.findings.map((f) => [
      f.referent.value,
      {
        referent: f.referent,
        turnId: turn.id,
        label: f.title,
        ...(f.impactCents !== undefined ? { impactCents: f.impactCents } : {}),
        statement: f.statement,
      },
    ]),
  );
  useSessionStore.setState({ referents, turns: [turn] });
}

function renderAnswer(turn: TurnRecord) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <AnswerCard turn={turn} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

function precedes(a: Element | null, b: Element | null): boolean {
  if (!a || !b) return false;
  return (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
}

const line = (root: HTMLElement) => root.querySelector("[data-integrity-tone]");

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetAnswerVariantCache();
  setAnswerVariant("b");
  useSessionStore.setState({
    settings: DEFAULT_SETTINGS,
    drawerTurnId: null,
    focusedReferent: null,
    referents: {},
    turns: [],
  });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  resetAnswerVariantCache();
  useSessionStore.setState({ settings: DEFAULT_SETTINGS, drawerTurnId: null, turns: [] });
});

describe("live turn — the premise correction, prose-primary", () => {
  it("is the path the A/B never saw: prose, not facts inline", () => {
    const turn = turnFrom("narrative_premise");
    expect(turn.answer.narrative.length).toBeGreaterThan(1000);
    const { container } = renderAnswer(turn);
    // The tell: no fact list ON the answer. The facts are in the rail.
    expect(container.querySelector('section[aria-label="Findings"]')).toBeNull();
    expect(
      screen.getByRole("button", { name: /2 facts behind this answer/ }),
    ).toBeInTheDocument();
  });

  it("leads with the verdict, in prose, above the writing", () => {
    const turn = turnFrom("narrative_premise");
    const { container } = renderAnswer(turn);
    const verdict = container.querySelector('[data-warning-code="PREMISE_PARTIAL"]');
    expect(verdict).toHaveAttribute("data-verdict", "true");
    expect(verdict?.textContent).toContain("The premise holds in direction, not in size");
    const prose = screen.getByText(/What follows is the composition of the movement/);
    expect(precedes(verdict, prose)).toBe(true);
  });

  it("draws one figure and names whatever it did not draw", () => {
    const turn = turnFrom("narrative_premise");
    const { container } = renderAnswer(turn);
    // Two frames on the wire; `selectRenderableCharts` drops the
    // byte-identical comparison twin, and one is drawn either way.
    expect(container.querySelectorAll("figure")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: /2 facts behind this answer/ }),
    ).toBeInTheDocument();
  });

  it("closes with the integrity line, and the line states what it counts", () => {
    const turn = turnFrom("narrative_premise");
    const { container } = renderAnswer(turn);
    const signature = line(container);
    expect(signature).toHaveAttribute("data-integrity-tone", "verified");
    expect(screen.getByText("Verified against your data")).toBeInTheDocument();
    // Five warnings on this payload: PREMISE_PARTIAL leads as the
    // verdict, and of the four behind the line three are cautions.
    expect(screen.getByRole("button", { name: "4 things to know" })).toBeInTheDocument();
    expect(
      screen.getByText("3 change how a number here should be read"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "3 checks" })).toBeInTheDocument();
    expect(precedes(container.querySelector("figure"), signature)).toBe(true);
  });

  it("cites the facts as citations, not as a dozen green boxes", () => {
    const turn = turnFrom("narrative_premise");
    seed(turn);
    const { container } = renderAnswer(turn);
    // The composed prose cites F1 four times and F2 three times; every
    // repeat that was written as a bare parenthetical is dropped.
    const written = (turn.answer.narrative.match(/\(F\d(?:, F\d)*\)/g) ?? []).length;
    const rendered = container.querySelectorAll('p button[class*="align-super"]').length;
    expect(written).toBeGreaterThan(0);
    expect(rendered).toBeLessThan(written);
    // And what does render is a citation, not the row handle: superscript,
    // muted, no green fill.
    const citation = container.querySelector('p button[class*="align-super"]');
    expect(citation?.className).not.toContain("bg-verified");
  });

  it("hands the facts to the rail, whole, with no machine date literal", () => {
    const turn = turnFrom("narrative_premise");
    seed(turn);
    useSessionStore.setState({ drawerTurnId: turn.id });
    const { container } = render(
      <MemoryRouter>
        <TooltipProvider>
          <ContextPanel />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { name: /Facts \(2\)/ })).toBeInTheDocument();
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}/);
  });
});

describe("live turn — the worklist question, the flagship proactive shape", () => {
  it("renders the ranked work inside the answer, above its signature", () => {
    const turn = turnFrom("worklist_leads");
    expect(turn.answer.worklist).toBeDefined();
    const { container } = renderAnswer(turn);
    const block = screen.getByRole("heading", { name: /What to work first/ }).closest("section");
    expect(precedes(block, line(container))).toBe(true);
    // And the 33 ranked cards no longer sit under the signature.
    expect(precedes(line(container), block)).toBe(false);
  });

  it("keeps the prose the answer and the facts in the rail", () => {
    const turn = turnFrom("worklist_leads");
    const { container } = renderAnswer(turn);
    expect(container.querySelector('section[aria-label="Findings"]')).toBeNull();
    expect(screen.getByText(/Beside the ranked worklist/)).toBeInTheDocument();
    expect(container.querySelectorAll("figure")).toHaveLength(1);
  });

  /**
   * OBSERVED, AND OUT OF THIS LANE'S SCOPE.
   *
   * `WORKLIST_LEADS` is not in `VERDICT_CODES`, so the sentence that says
   * the ranked list IS the answer — and names the first card, "Start with
   * ANM-021…" — is a caution rather than the verdict, and the composer
   * wrote it into the prose verbatim, so `foldComposedDisclosures`
   * correctly folds it out of the writing and leaves it in the sheet. The
   * fresh-eyes director filed exactly this (his item 7: "promote
   * WORKLIST_LEADS to a verdict, or lift the worklist above the payer
   * measurements"); the consolidated condition list took the SECOND
   * remedy, which is what shipped above. This pins the state the second
   * remedy leaves behind, so a promotion later is a deliberate change
   * rather than a surprise.
   */
  it("still files 'the worklist is the answer' as a caution, not a verdict", async () => {
    const turn = turnFrom("worklist_leads");
    const { container } = renderAnswer(turn);
    expect(container.querySelector('[data-warning-code="WORKLIST_LEADS"]')).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: /\d+ things to know/ }));
    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText(/the ranked worklist below IS the answer/),
    ).toBeInTheDocument();
  });

  it("counts thirteen warnings and loses none of them", async () => {
    const turn = turnFrom("worklist_leads");
    const payloadCodes = turn.answer.warnings.map((w) => w.code);
    const { container } = renderAnswer(turn);
    const onAnswer = [...container.querySelectorAll("[data-warning-code]")].map(
      (el) => el.getAttribute("data-warning-code") ?? "",
    );
    await userEvent.click(screen.getByRole("button", { name: /\d+ things to know/ }));
    const dialog = await screen.findByRole("dialog");
    const inSheet = [...dialog.querySelectorAll("[data-warning-code]")].map(
      (el) => el.getAttribute("data-warning-code") ?? "",
    );
    // WORKLIST_ATTACHED opens the worklist block instead of the list.
    const intro = payloadCodes.filter((c) => c === "WORKLIST_ATTACHED").length;
    expect(onAnswer.length + inSheet.length + intro).toBe(payloadCodes.length);
  });

  it("says how many of the caveats change how a number reads", () => {
    const turn = turnFrom("worklist_leads");
    renderAnswer(turn);
    expect(
      screen.getByText(/change how a number here should be read/),
    ).toBeInTheDocument();
  });

  it("suppresses the repeated single-fact citations the composer emits", () => {
    const turn = turnFrom("worklist_leads");
    seed(turn);
    const { container } = renderAnswer(turn);
    // The live prose cites (F1) six times in one paragraph.
    const written = (turn.answer.narrative.match(/\(F1\)/g) ?? []).length;
    expect(written).toBeGreaterThanOrEqual(5);
    const rendered = within(container).getAllByRole("button", { name: /^F1\b/ });
    expect(rendered).toHaveLength(1);
  });

  it("puts no engine handle on the answer", () => {
    const turn = turnFrom("worklist_leads");
    const { container } = renderAnswer(turn);
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/\(portfolio_[a-z_]+, \d+ rows?\(s\)\)/);
    expect(text).not.toContain("denial_rate");
  });
});
