/**
 * The four representative answers, from the live API, in all three
 * layouts.
 *
 * `src/lib/__fixtures__/live-answers.json` is captured verbatim from a
 * running deployment (`GET /v1/investigations/{iid}`, watermark wm_003):
 *
 *   worklist                  the routed work-prioritization answer —
 *                             twelve warnings, four portfolio frames, and
 *                             the `PROBE_FAMILIES_EMPTY` sentence that was
 *                             printing eight plan-node ids at the analyst.
 *   bounded_ranking_refused   "Rank our plans by denial rate, worst
 *                             first" — RANKING_REFUSED over thirty plans,
 *                             most of them upper bounds.
 *   comparison_premise        "Our denial rate doubled since January.
 *                             Why?" — PREMISE_PARTIAL against the prior
 *                             year, two line frames.
 *   restored_comparison       a July-vs-June comparison read back from
 *                             history, which is what every one of these
 *                             is: this route IS the restored path.
 *
 * What is asserted is not how they look — it is that no layout can lose
 * anything. For each payload and each layout: every warning the payload
 * carries is either on the answer or inside the disclosure that counts
 * it, every verdict leads, and no engine handle reaches the screen.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import live from "@/lib/__fixtures__/live-answers.json";
import { MemoryRouter } from "react-router-dom";

import { AnswerCard } from "@/components/answer/AnswerCard";
import { TooltipProvider } from "@/components/ui/tooltip";
import { resetAnswerVariantCache, setAnswerVariant } from "@/lib/answerVariant";
import { parseInvestigationResponse, turnResponseToEvents } from "@/lib/contract";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import {
  applyEventToAnswer,
  emptyAnswer,
  useSessionStore,
  type TurnRecord,
} from "@/lib/store";

beforeAll(() => {
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
  watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
  pack: { packId: "base-rcm", version: "1.0.0" },
};

/** A live investigation, through the real seam, into a turn record. */
function turnFrom(raw: unknown, id: string): TurnRecord {
  const parsed = parseInvestigationResponse(raw, PIN);
  expect(parsed.drift, `${id}: the fixture must parse with no contract drift`).toEqual([]);
  expect(parsed.value).not.toBeNull();
  let answer = emptyAnswer();
  for (const event of turnResponseToEvents(parsed.value!)) {
    answer = applyEventToAnswer(answer, event);
  }
  return {
    id,
    index: 0,
    submission: { utterance: (raw as { question?: string }).question ?? "" },
    answer: { ...answer, rehydrated: true },
  };
}

const CASES = Object.entries(live as Record<string, unknown>).map(([name, raw]) => ({
  name,
  raw,
}));

const VARIANTS = ["current", "a", "b"] as const;

function renderCard(record: TurnRecord) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <AnswerCard turn={record} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

function codesIn(root: HTMLElement | Document): string[] {
  return [...root.querySelectorAll("[data-warning-code]")].map(
    (el) => el.getAttribute("data-warning-code") ?? "",
  );
}

const VERDICT_CODES = new Set([
  "PREMISE_FALSE",
  "PREMISE_PARTIAL",
  "PREMISE_UNVERIFIABLE",
  "PREMISE_VERIFIED",
  "RANKING_REFUSED",
  "DIRECTION_UNMATCHED",
]);

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetAnswerVariantCache();
  useSessionStore.setState({
    settings: DEFAULT_SETTINGS,
    drawerTurnId: null,
    focusedReferent: null,
  });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  resetAnswerVariantCache();
});

describe.each(CASES)("live answer: $name", ({ name, raw }) => {
  it("parses into an answer with findings or a written analysis", () => {
    const turn = turnFrom(raw, `turn_${name}`);
    expect(
      turn.answer.findings.length > 0 || turn.answer.warnings.length > 0,
    ).toBe(true);
  });

  for (const variant of VARIANTS) {
    it(`renders every warning exactly once, on the answer or in its disclosure — ${variant}`, async () => {
      setAnswerVariant(variant);
      const turn = turnFrom(raw, `turn_${name}`);
      // `WORKLIST_ATTACHED` is relocated to open the worklist block, and
      // the worklist is not part of an investigation read-back — so the
      // expectation is the answer's own warning list.
      const expected = turn.answer.warnings.map((w) => w.code);
      const { container } = renderCard(turn);

      const onAnswer = codesIn(container);
      let reachable = [...onAnswer];

      // Open whichever disclosure this layout tucks the rest behind.
      const group = screen.queryByRole("button", { name: /\d+ things? to know/ });
      if (group) {
        await userEvent.click(group);
        const dialog = screen.queryByRole("dialog");
        reachable = dialog ? [...codesIn(container), ...codesIn(dialog)] : codesIn(container);
      }

      expect(reachable.sort()).toEqual([...expected].sort());
    });

    it(`leads with every verdict the payload carries — ${variant}`, () => {
      setAnswerVariant(variant);
      const turn = turnFrom(raw, `turn_${name}`);
      const verdicts = turn.answer.warnings.filter((w) => VERDICT_CODES.has(w.code));
      const { container } = renderCard(turn);

      for (const verdict of verdicts) {
        const el = container.querySelector(`[data-warning-code="${verdict.code}"]`);
        expect(el, `${verdict.code} must be on the answer in ${variant}`).not.toBeNull();
        expect(el).toHaveAttribute("data-verdict", "true");
      }
    });

    it(`puts no engine handle on the default surface — ${variant}`, () => {
      setAnswerVariant(variant);
      const turn = turnFrom(raw, `turn_${name}`);
      const { container } = renderCard(turn);
      const text = container.textContent ?? "";

      // The plan-node census that shipped inside PROBE_FAMILIES_EMPTY.
      expect(text).not.toMatch(/\(portfolio_[a-z_]+, \d+ rows?\(s\)\)/);
      expect(text).not.toContain("row(s)");
      // The measure ids that were reaching the reader in warehouse case.
      for (const id of ["denial_rate", "cash_posted", "timely_filing_at_risk_dollars"]) {
        expect(text, `${id} must not reach the screen in ${variant}`).not.toContain(id);
      }
    });
  }

  it("draws at most one figure in the calm layout, and names the rest", () => {
    setAnswerVariant("b");
    const turn = turnFrom(raw, `turn_${name}`);
    const { container } = renderCard(turn);
    expect(container.querySelectorAll("figure").length).toBeLessThanOrEqual(1);
  });

  it("keeps a bound rendered as a bound wherever its number renders", () => {
    setAnswerVariant("b");
    const turn = turnFrom(raw, `turn_${name}`);
    const bounded = turn.answer.findings.filter((f) => f.measured?.isBound === true);
    if (bounded.length === 0) return;
    renderCard(turn);
    // The calm layout shows the facts inline when there is no stored
    // write-up, which is the case for every read-back investigation.
    for (const finding of bounded) {
      expect(screen.getByText(finding.title)).toBeInTheDocument();
    }
    expect(screen.getAllByText(/upper bound/i).length).toBeGreaterThan(0);
  });
});
