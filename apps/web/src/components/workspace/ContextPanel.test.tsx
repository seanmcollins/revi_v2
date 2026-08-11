/**
 * The Evidence rail, ungated.
 *
 * In the calm layout the facts are not on the answer — they are here, and
 * a citation in the writing is one gesture from the row it cites. That
 * makes this panel load-bearing rather than supplementary, and it was
 * gating its ENTIRE body on `answer.evidence`: a turn whose bundle is
 * absent (the server's own restoration notes anticipate exactly that —
 * "its evidence and governed-provenance blocks are absent rather than
 * empty") had its facts nowhere at all, while the panel fell back to
 * showing a DIFFERENT turn's bundle under this turn's question.
 *
 * What is asserted here is the honest shape: the rail renders what the
 * selected turn has, and states what it does not.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { ContextPanel } from "@/components/workspace/ContextPanel";
import { TooltipProvider } from "@/components/ui/tooltip";
import { resetAnswerVariantCache, setAnswerVariant } from "@/lib/answerVariant";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import { emptyAnswer, useSessionStore, type TurnRecord } from "@/lib/store";
import type { Finding } from "@/lib/types";

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
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

function finding(value: string): Finding {
  return {
    referent: { value, kind: "finding" },
    title: `Ashvale HMO ${value}: 47.2% denial rate`,
    statement: `Ashvale HMO ${value}: 47.2% denial rate.`,
    metricRefs: ["denial_rate"],
    values: { denial_rate: 0.472 },
    grade: "direct",
    directionOfGood: "down_is_good",
    confidence: "high",
    suggestedRefinements: [],
    measured: { metricId: "denial_rate", value: 47.2, unit: "percent" },
  };
}

/** A turn the server restored WITHOUT an evidence bundle, with facts. */
function turnWithoutBundle(id: string): TurnRecord {
  return {
    id,
    index: 0,
    submission: { utterance: "Rank our plans by denial rate for July 2026." },
    answer: {
      ...emptyAnswer(),
      status: "complete",
      rehydrated: true,
      // The write-up is what sends the facts to the rail: with no prose
      // the answer keeps them inline and the rail stands its own section
      // down, which is a different (and correct) branch.
      narrative: "Denial rate is concentrated in three plans F1 and F2.",
      findings: [finding("F1"), finding("F2")],
    },
  };
}

/** An earlier turn that DOES carry a bundle — the wrong-turn fallback. */
function turnWithBundle(id: string): TurnRecord {
  return {
    id,
    index: 0,
    submission: { utterance: "How much cash posted last week?" },
    answer: {
      ...emptyAnswer(),
      status: "complete",
      narrative: "Cash posted fell 12.7% week over week.",
      findings: [],
      evidence: {
        probes: [
          {
            probeId: "main",
            probeHash: "h1",
            kind: "aggregation",
            description: "cash posted by week",
            metrics: [],
            cacheHit: false,
            truncated: false,
            suppressedCells: 0,
            durationMs: 12,
          },
        ],
        warehouseQueries: 1,
        cacheHits: 0,
        zeroProbeTurn: false,
      },
    },
  };
}

function renderPanel() {
  return render(
    <TooltipProvider>
      <ContextPanel />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  window.history.replaceState(null, "", "/");
  resetAnswerVariantCache();
  setAnswerVariant("b");
  useSessionStore.setState({ settings: DEFAULT_SETTINGS, drawerTurnId: null, turns: [] });
});

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  resetAnswerVariantCache();
  useSessionStore.setState({ settings: DEFAULT_SETTINGS, drawerTurnId: null, turns: [] });
});

describe("the Evidence rail is not gated on the evidence bundle", () => {
  it("shows the facts of a turn whose bundle is absent", () => {
    useSessionStore.setState({ turns: [turnWithoutBundle("turn_1")], drawerTurnId: "turn_1" });
    renderPanel();
    expect(screen.getByRole("heading", { name: /Facts \(2\)/ })).toBeInTheDocument();
    expect(screen.getByText("Ashvale HMO F1: 47.2% denial rate")).toBeInTheDocument();
  });

  it("says the bundle is missing rather than pretending it is empty", () => {
    useSessionStore.setState({ turns: [turnWithoutBundle("turn_1")], drawerTurnId: "turn_1" });
    renderPanel();
    expect(screen.getByText(/published no evidence/)).toBeInTheDocument();
  });

  it("never shows another turn's working under this turn's question", () => {
    useSessionStore.setState({
      turns: [turnWithBundle("turn_1"), turnWithoutBundle("turn_2")],
      drawerTurnId: "turn_2",
    });
    renderPanel();
    expect(screen.getByText(/Rank our plans by denial rate/)).toBeInTheDocument();
    expect(screen.queryByText("cash posted by week")).not.toBeInTheDocument();
  });

  it("still says there is nothing at all when there is nothing at all", () => {
    renderPanel();
    expect(screen.getByText(/No evidence yet/)).toBeInTheDocument();
  });
});
