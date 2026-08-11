/**
 * DEEP RESEARCH, END TO END — the offer, the minute, and the report.
 *
 * `src/lib/__fixtures__/deep-research-run.json` is captured verbatim from
 * a running deployment (`GET /v1/deep-research/dr_2707e2b4b25d4a64`, the
 * load through Aug 2, 2026): one finished run over every open denial, 34
 * priced populations, 20 that could not be priced, two contrasts, the
 * timeliness curve, both sides of the filing deadline, the censoring
 * disclosure and nine warnings. It is the same discipline the answer
 * fixtures follow — a real payload, not a hand-written one, because the
 * failures worth catching are the ones a real payload has and an invented
 * one does not (a `null` rate, a `0E-10` p-value, a decimal string where a
 * number was assumed).
 *
 * What is asserted is not how the report looks. It is that nothing on it
 * can lie: that a population with no publishable rate never renders a
 * number, that the interval never leaves the figure it belongs to, that
 * every caveat is titled and reachable, and that the figures keep the two
 * controls every other chart in this product has.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { AnswerCard } from "@/components/answer/AnswerCard";
import { PortfolioCard } from "@/components/portfolio/PortfolioPanel";
import { ResearchLaunchCard } from "@/components/research/ResearchLaunchCard";
import { ResearchProgress } from "@/components/research/ResearchProgress";
import { ResearchReportView } from "@/components/research/ResearchReport";
import { RunDeepResearchButton } from "@/components/research/ResearchOffer";
import { TooltipProvider } from "@/components/ui/tooltip";
import offerTurnFixture from "@/lib/__fixtures__/deep-research-offer-turn.json";
import runFixture from "@/lib/__fixtures__/deep-research-run.json";
import { newReceivedState, parseTurnResponse, turnResponseToEvents } from "@/lib/contract";
import {
  applyResearchFrame,
  initialWatchState,
  mapResearchOffer,
  type ResearchReport,
  type ResearchRun,
  type ResearchWatchState,
} from "@/lib/deepResearch";
import { researchReportToCsv } from "@/lib/export";
import type { PortfolioItem } from "@/lib/mock/portfolio";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import { applyEventToAnswer, emptyAnswer, useSessionStore } from "@/lib/store";

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
  if (!("PointerEvent" in window)) {
    // @ts-expect-error jsdom has no PointerEvent; MouseEvent carries what Radix reads.
    window.PointerEvent = window.MouseEvent;
  }
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => {};
  Element.prototype.releasePointerCapture ??= () => {};
  Element.prototype.scrollIntoView ??= () => {};
});

afterEach(() => cleanup());

const RUN = runFixture as unknown as ResearchRun;
const REPORT = RUN.report as ResearchReport;

function mount(node: React.ReactNode) {
  return render(
    <MemoryRouter>
      <TooltipProvider>{node}</TooltipProvider>
    </MemoryRouter>,
  );
}

/* ------------------------------------------------------------------ */
/* 1. The offer                                                        */
/* ------------------------------------------------------------------ */

const OFFER = {
  population: { kind: "payer" as const, values: ["Atlas Commercial"], label: "denials from Atlas Commercial" },
  label: "Run deep research",
  description:
    "Measure what is realistically recoverable out of denials from Atlas Commercial, on your own history, and write it up.",
};

const CARD: PortfolioItem = {
  rank: 1,
  referent: "ANM-001",
  title: "Medical-necessity denial spike",
  issueClass: "denial_spike",
  impactCents: 4_930_000,
  impactLabel: "denied",
  detail: "A run of clinical denials on one payer.",
  provenance: "external_detection",
  priorityFormulaVersion: "anomaly_priority@3",
  sourceWatermarkId: "wm_003",
  drillable: true,
  drillSpec: { metric_ids: ["denied_dollars"] },
};

describe("the launch affordance — offered where the payload offers it", () => {
  it("draws a persistent 'Run deep research' on a lead card that carries the offer", () => {
    mount(<PortfolioCard item={{ ...CARD, deepResearch: OFFER }} onDrill={() => {}} />);
    const control = screen.getByRole("button", {
      name: "Deep research on denials from Atlas Commercial — see what it will analyze",
    });
    expect(control).toBeInTheDocument();
    // PERSISTENT, not hover-revealed: the class list must not hide it.
    expect(control.className).not.toMatch(/opacity-0/);
    // The selector it will post, on the element, so the offer and the
    // request cannot drift.
    expect(control).toHaveAttribute("data-research-offer", "payer");
  });

  it("confirms before it spends the minute, rather than launching on the press", async () => {
    mount(<PortfolioCard item={{ ...CARD, deepResearch: OFFER }} onDrill={() => {}} />);
    // Nothing has started, and nothing claims to have: the control's own
    // name says it opens the card.
    expect(
      screen.queryByRole("button", { name: /^Run deep research on/ }),
    ).not.toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("button", {
        name: "Deep research on denials from Atlas Commercial — see what it will analyze",
      }),
    );
    const card = await screen.findByRole("heading", {
      name: "Deep research on denials from Atlas Commercial",
    });
    expect(card).toBeInTheDocument();
    // ONE Run button, inside the confirmation, and the cost beside it.
    expect(
      screen.getByRole("button", { name: "Run deep research on denials from Atlas Commercial" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/About a minute/)).toBeInTheDocument();
  });

  it("draws nothing at all on a lead card the platform made no offer for", () => {
    mount(<PortfolioCard item={CARD} onDrill={() => {}} />);
    expect(screen.queryByRole("button", { name: /deep research/i })).not.toBeInTheDocument();
  });

  it("reads the offer off a live card payload rather than deriving one", () => {
    // The wire's own shape, dropped whole when the selector is unreadable:
    // an offer with no population behind it is a button that would launch
    // something nobody chose.
    expect(mapResearchOffer({ population: { kind: "payer", values: ["Atlas Commercial"] } })).toEqual(
      {
        population: { kind: "payer", values: ["Atlas Commercial"], label: "" },
        label: "Run deep research",
        description: "",
      },
    );
    expect(mapResearchOffer({ population: { kind: "provider" } })).toBeUndefined();
    expect(mapResearchOffer({ label: "Run deep research" })).toBeUndefined();
  });

  it("states what the run costs before the click, on the composer's launch card", () => {
    mount(<ResearchLaunchCard offer={OFFER} />);
    expect(
      screen.getByRole("heading", { name: "Deep research on denials from Atlas Commercial" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/About a minute/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run deep research on denials from Atlas Commercial" }),
    ).toBeInTheDocument();
  });

  it("names the population on the accessible name of the compact control", () => {
    mount(<RunDeepResearchButton offer={OFFER} />);
    expect(
      screen.getByRole("button", {
        name: "Deep research on denials from Atlas Commercial — see what it will analyze",
      }),
    ).toBeInTheDocument();
  });

  /* ---------------------------------------------------------------- */
  /* The plan preview — written against a payload that does not exist  */
  /* ---------------------------------------------------------------- */

  /**
   * `scope`, `plan` and `options` are the seam for the backend's dry run
   * (see `ResearchOffer`). These two tests pin BOTH halves of the
   * contract: the card grows the sections when the payload carries them,
   * and today's payload — which carries none of them — still produces a
   * complete, honest confirmation rather than an empty one.
   */
  const PLANNED = {
    ...OFFER,
    scope: { openDenials: 565, openDollarsCents: 115_330_217 },
    plan: [
      {
        title: "What the open inventory is worth",
        purpose: "Expected recoverable dollars, priced only where your own history supports a rate.",
      },
      { title: "Strongest and weakest payer", purpose: "" },
    ],
    options: [{ kind: "all_open" as const, values: [], label: "every open denial" }],
  };

  it("previews the population's size, the proposed angles and the alternatives", () => {
    mount(<ResearchLaunchCard offer={PLANNED} />);
    expect(screen.getByText(/565 open denials, worth \$1,153,302.17/)).toBeInTheDocument();
    expect(screen.getByText("What the open inventory is worth")).toBeInTheDocument();
    expect(
      screen.getByText(/priced only where your own history supports a rate/),
    ).toBeInTheDocument();
    // The mode's generic lines are REPLACED by the real plan, never shown
    // beside it — two lists describing one run teaches a reader to skip
    // both.
    expect(screen.queryByText(/nothing filled in from an industry average/)).not.toBeInTheDocument();
    // The alternatives are a real single choice, with the offer's own
    // population selected.
    const chosen = screen.getByRole("radio", { name: "denials from Atlas Commercial" });
    expect(chosen).toBeChecked();
    expect(screen.getByRole("radio", { name: "every open denial" })).not.toBeChecked();
  });

  it("posts the population the reader chose, not the one the payload led with", async () => {
    mount(<ResearchLaunchCard offer={PLANNED} />);
    await userEvent.click(screen.getByRole("radio", { name: "every open denial" }));
    // The heading and the Run button both follow the choice, so what the
    // reader is about to press says what it will do.
    expect(
      screen.getByRole("heading", { name: "Deep research on every open denial" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run deep research on every open denial" }),
    ).toBeInTheDocument();
  });

  it("degrades to what the mode does when the payload carries no plan", () => {
    mount(<ResearchLaunchCard offer={OFFER} />);
    expect(screen.getByText(/nothing filled in from an industry average/)).toBeInTheDocument();
    // No size is asserted about a population nobody measured, and no
    // chooser is drawn over a single population.
    expect(screen.queryByText(/open denials, worth/)).not.toBeInTheDocument();
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  });

  it("reads the seam's three fields off the wire, and drops what it cannot honour", () => {
    const read = mapResearchOffer({
      population: { kind: "payer", values: ["Atlas Commercial"], label: "denials from Atlas" },
      scope: { open_denials: 565, open_dollars_cents: 115_330_217 },
      plan: { angles: [{ title: "Speed and what it is worth", purpose: "The price of a slow queue." }] },
      options: [{ kind: "all_open", values: [] }, { kind: "provider", values: ["x"] }],
    });
    expect(read?.scope).toEqual({ openDenials: 565, openDollarsCents: 115_330_217 });
    expect(read?.plan).toEqual([
      { title: "Speed and what it is worth", purpose: "The price of a slow queue." },
    ]);
    // A selector kind this build cannot post is dropped rather than
    // coerced: an option that posts something other than its label is
    // worse than one option fewer.
    expect(read?.options).toHaveLength(1);
    expect(read?.options?.[0]?.kind).toBe("all_open");
  });
});

/* ------------------------------------------------------------------ */
/* 2. The minute                                                       */
/* ------------------------------------------------------------------ */

/** A run as it exists the instant the POST returns: no report, no plan. */
function startingRun(): ResearchRun {
  return {
    id: "dr_test",
    session_id: "sess_test",
    status: "planning",
    created_at: "2026-08-11T02:37:15Z",
    data_load_label: "the load through Aug 2, 2026",
    population: { kind: "all_open", values: [], label: "every open denial" },
    progress: { phase: "plan", angle_index: 0, angle_total: 0, message: "", elapsed_ms: 0 },
  };
}

/** The frames a real run emits, in the order the server emits them. */
const FRAMES: Array<{ kind: string; data: Record<string, unknown> }> = [
  {
    kind: "research_started",
    data: {
      id: "dr_test",
      session_id: "sess_test",
      data_load: "the load through Aug 2, 2026",
      population: { kind: "all_open", values: [], label: "every open denial" },
    },
  },
  {
    kind: "research_progress",
    data: {
      phase: "plan",
      angle_index: 0,
      angle_total: 0,
      message: "Choosing what to look at",
      elapsed_ms: 4,
    },
  },
  {
    kind: "research_progress",
    data: {
      phase: "execute",
      angle_index: 0,
      angle_total: 8,
      message: "Reading your denial history",
      elapsed_ms: 12,
    },
  },
  {
    kind: "research_progress",
    data: {
      phase: "execute",
      angle_index: 3,
      angle_total: 8,
      message: "Comparing payers",
      elapsed_ms: 40,
    },
  },
  {
    kind: "research_progress",
    data: {
      phase: "synthesize",
      angle_index: 8,
      angle_total: 8,
      message: "Writing it up",
      elapsed_ms: 14686,
    },
  },
];

function replay(frames: typeof FRAMES): ResearchWatchState {
  let state = initialWatchState(startingRun());
  for (const frame of frames) state = applyResearchFrame(state, frame);
  return state;
}

describe("the progress surface — the minute, accounted for", () => {
  it("names the three phases in plain language and marks where the run is", () => {
    const state = replay(FRAMES.slice(0, 4));
    const { container } = mount(<ResearchProgress state={state} />);

    expect(screen.getByText("Reading your data")).toBeInTheDocument();
    expect(screen.getByText("Running the analysis")).toBeInTheDocument();
    expect(screen.getByText("Writing it up")).toBeInTheDocument();

    expect(container.querySelector('[data-phase="plan"]')).toHaveAttribute(
      "data-phase-state",
      "done",
    );
    expect(container.querySelector('[data-phase="execute"]')).toHaveAttribute(
      "data-phase-state",
      "active",
    );
    expect(container.querySelector('[data-phase="synthesize"]')).toHaveAttribute(
      "data-phase-state",
      "pending",
    );
  });

  it("prints the server's own sentence under the phase it belongs to, and the angle count", () => {
    mount(<ResearchProgress state={replay(FRAMES.slice(0, 4))} />);
    expect(screen.getByText(/Comparing payers/)).toBeInTheDocument();
    expect(screen.getByText(/3 of 8/)).toBeInTheDocument();
  });

  it("says where the minute actually goes, once the write-up is reached", () => {
    mount(<ResearchProgress state={replay(FRAMES)} />);
    expect(screen.getByText(/Most of the minute goes here/)).toBeInTheDocument();
  });

  it("promises the report will be here rather than offering a stop it cannot honour", () => {
    mount(<ResearchProgress state={replay(FRAMES)} />);
    expect(screen.getByText(/You can leave this page/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /stop|cancel/i })).not.toBeInTheDocument();
  });

  it("names the angles only once the platform has named them", () => {
    // Before the plan frame: the COUNT, which is a real fact — never eight
    // invented titles that would be replaced a moment later.
    mount(<ResearchProgress state={replay(FRAMES)} />);
    expect(screen.getByText(/8 angles to measure/)).toBeInTheDocument();
    expect(screen.queryByText("What it is looking at")).not.toBeInTheDocument();
    cleanup();

    const named = applyResearchFrame(replay(FRAMES), {
      kind: "research_plan",
      data: REPORT.plan as unknown as Record<string, unknown>,
    });
    mount(<ResearchProgress state={named} />);
    expect(screen.getByText("What it is looking at")).toBeInTheDocument();
    expect(screen.getByText("What the open inventory is worth")).toBeInTheDocument();
  });

  it("never infers a finished run: only a frame settles the status", () => {
    const running = replay(FRAMES);
    expect(running.run.status).toBe("running");
    const failed = applyResearchFrame(running, {
      kind: "error",
      data: { message: "This run stopped before it could finish." },
    });
    expect(failed.run.status).toBe("failed");
    mount(<ResearchProgress state={failed} />);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "This run stopped before it could finish.",
    );
    // Nothing partial is published, so nothing partial is offered.
    expect(screen.queryByText(/You can leave this page/)).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* 3. The report                                                       */
/* ------------------------------------------------------------------ */

function mountReport() {
  return mount(<ResearchReportView report={REPORT} runId={RUN.id} />);
}

describe("the report — the headline determination", () => {
  it("renders the total and its interval as one figure, not a figure and a footnote", () => {
    const { container } = mountReport();
    const figure = container.querySelector('[data-key-figure="Expected recoverable"]');
    expect(figure).not.toBeNull();
    // The point estimate AND both bounds are inside the same cell.
    expect(figure).toHaveTextContent("$1,167,668.88");
    expect(figure).toHaveTextContent("Between $874,052.42 and $1,518,693.69");
    expect(figure).toHaveTextContent("95% interval");
  });

  it("says the range is a sum of ranges rather than a guarantee", () => {
    const { container } = mountReport();
    expect(container.querySelector('[data-key-figure="Expected recoverable"]')).toHaveTextContent(
      /sum of each population's own range/,
    );
  });

  it("carries the still-catchable / past-deadline / no-limit split as supporting figures", () => {
    const { container } = mountReport();
    const band = container.querySelector("[data-research-split]");
    expect(band).not.toBeNull();
    expect(band).toHaveTextContent("$2,831,002.86");
    expect(band).toHaveTextContent("$2,918,492.26");
    // The split is over OPEN DENIED DOLLARS, and every cell says so —
    // reading it as recoverable dollars is the one misreading available.
    expect(band).toHaveTextContent("Of the denied dollars still open");
  });

  it("states what was priced and what was left out of the figure above", () => {
    mountReport();
    expect(
      screen.getByText(/cannot price yet, and is not in the figure above/),
    ).toBeInTheDocument();
  });
});

describe("the report — evidence tier decides the row treatment", () => {
  it("gives a measured population its rate, its interval and its expected dollars", () => {
    const { container } = mountReport();
    const row = [...container.querySelectorAll('[data-evidence="measured"]')].find((node) =>
      node.textContent?.includes("Ashvale Health Plan / registration and eligibility"),
    );
    expect(row).toBeDefined();
    expect(row).toHaveTextContent("58.3%");
    expect(row).toHaveTextContent("42.2%–72.9%");
    expect(row).toHaveTextContent("$22,964.69");
    expect(row).not.toHaveTextContent("Not estimable");
  });

  it("gives a not-estimable population its size and its dollars and NO rate at all", () => {
    const { container } = mountReport();
    const rows = [...container.querySelectorAll('[data-evidence="not_estimable"]')];
    expect(rows.length).toBeGreaterThan(0);
    const row = rows.find((node) =>
      node.textContent?.includes("Ashvale Health Plan / clinical / medical necessity"),
    );
    expect(row).toBeDefined();
    // The population and the money are real and are shown.
    expect(row).toHaveTextContent("25");
    expect(row).toHaveTextContent("$69,129.64");
    // The rate is not. Not a zero, not a dash, not a dimmed number — the
    // words, twice: once where the rate would be and once where the
    // expected dollars would be.
    expect(within(row as HTMLElement).getAllByText("Not estimable")).toHaveLength(2);
    // And no percentage anywhere on the row.
    expect(row?.textContent ?? "").not.toMatch(/\d\.\d%/);
  });

  it("never renders a rate for a row the run refused to price, across the whole table", () => {
    const { container } = mountReport();
    for (const row of container.querySelectorAll('[data-evidence="not_estimable"]')) {
      expect(row.textContent ?? "").not.toMatch(/\d%/);
    }
  });

  it("counts the populations too small to name rather than dropping them", () => {
    mountReport();
    expect(screen.getByText(/A further 6 populations hold 41 open denials/)).toBeInTheDocument();
  });
});

describe("the report — the working, in the product's own vocabulary", () => {
  it("states each contrast's test in words, and never as a bare probability", () => {
    mountReport();
    // Both contrasts separate at the same strength, so both carry the same
    // opening clause — the sentence is the server's, said once per gap.
    expect(
      screen.getAllByText(/The chance of seeing a gap this size if the two were really the same/),
    ).toHaveLength(2);
    // The p-values the payload carries ("0.0000066425", "0E-10") never
    // reach the reader as numbers.
    expect(screen.queryByText(/0\.0000066425/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0E-10/)).not.toBeInTheDocument();
  });

  it("puts the timeliness implication next to the curve", () => {
    mountReport();
    expect(screen.getByText("Speed, and what it is worth")).toBeInTheDocument();
    expect(
      screen.getByText(
        /Denials resubmitted within 0-14 days come back 54.4% of the time/,
      ),
    ).toBeInTheDocument();
  });

  it("keeps the run's own context sentences rather than dropping them", () => {
    mountReport();
    expect(screen.getByText("What else is worth knowing")).toBeInTheDocument();
    // The bound under a measured zero — the sentence that stops "0.0%"
    // from being read as "never".
    expect(
      screen.getByText(/That is not the same as never: on this many denials/),
    ).toBeInTheDocument();
  });

  it("splits the deadline table by whether the filing limit is confirmed", () => {
    const { container } = mountReport();
    expect(container.querySelector('[data-deadline-rule="limit confirmed"]')).not.toBeNull();
    expect(
      container.querySelector('[data-deadline-rule="limit needs confirming"]'),
    ).not.toBeNull();
    expect(
      screen.getByText(/Resubmitted inside the filing deadline, 44.2% of denials come back/),
    ).toBeInTheDocument();
    // Both positions on both sides of the split, each with its own n.
    expect(container.textContent).toContain("past the filing deadline");
    expect(container.textContent).toContain("inside the filing deadline");
  });

  it("renders the censoring disclosure as a quiet note in words, with its counts", () => {
    const { container } = mountReport();
    expect(screen.getByText("What is counted, and what is not")).toBeInTheDocument();
    const zone = container.querySelector('[aria-labelledby="research-censoring-heading"]');
    expect(zone).toHaveTextContent(
      /2,533 denials the payer has already answered, out of 5,398/,
    );
    expect(zone).toHaveTextContent(/counted in neither the wins nor the losses/);
    // The edge date is a readable date, never an ISO literal — and it is
    // said ONCE. The server's own last statement carries it, so this
    // component adds nothing beside it.
    expect(zone).toHaveTextContent(/as the data stood on Aug 2, 2026/);
    expect(zone).not.toHaveTextContent(/stands as the data did on/);
    expect(zone?.textContent ?? "").not.toMatch(/\d{4}-\d{2}-\d{2}/);
  });

  it("drops a context sentence a figure on this screen already carries", () => {
    mountReport();
    // The deadline note is published BOTH as the figure's annotation and
    // as a context note; it is drawn once, under the figure it qualifies.
    expect(
      screen.getAllByText(/Treating every limit as confirmed overstates the cliff/),
    ).toHaveLength(1);
  });

  it("shows the per-angle evidence, including how many cells each angle refused", async () => {
    const { container } = mountReport();
    const rail = container.querySelector('[aria-labelledby="research-evidence-heading"]');
    expect(rail).not.toBeNull();
    const angle = within(rail as HTMLElement).getByRole("button", {
      name: /What the open inventory is worth/,
    });
    await userEvent.click(angle);
    await waitFor(() =>
      expect(rail).toHaveTextContent(/5,398 denials read · 2,533 with a final answer/),
    );
    expect(rail).toHaveTextContent(/25 too small to publish a rate for/);
  });
});

describe("the report — the caveats go through the existing fold", () => {
  it("counts them on one line and titles every one when it opens", async () => {
    mountReport();
    const fold = screen.getByRole("button", { name: /things to know/ });
    expect(fold).toHaveTextContent("9 things to know");
    await userEvent.click(fold);

    // Every title is the one `warnings.ts` publishes for the code —
    // nothing here renders an ALL_CAPS handle at a reader. The censoring
    // code is raised four times with four different sentences, so its
    // title appears once per sentence rather than being collapsed onto a
    // count that would hide three of them.
    for (const title of [
      "Only your own data was used",
      "How these ranges combine",
      "Small groups were set aside, not guessed",
      "Some dollars could not be estimated yet",
      "Read the best and worst with care",
    ]) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
    expect(screen.getAllByText("Still-open cases are not counted either way")).toHaveLength(4);
    expect(screen.queryByText(/DEEP_RESEARCH_/)).not.toBeInTheDocument();
  });
});

describe("the report — its figures are this product's figures", () => {
  it("gives every chart the full screen and 'view as' controls", () => {
    mountReport();
    const expand = screen.getAllByRole("button", { name: /^View full screen:/ });
    const viewAs = screen.getAllByRole("button", { name: /^View as, currently/ });
    expect(expand.length).toBeGreaterThanOrEqual(6);
    expect(viewAs.length).toBe(expand.length);
  });

  it("switches one figure to the table without touching its neighbours", async () => {
    mountReport();
    const trigger = screen.getAllByRole("button", {
      name: /^View as, currently bar: Expected recoverable by population/,
    })[0];
    await userEvent.click(trigger);
    await userEvent.click(await screen.findByRole("menuitemradio", { name: "Table" }));
    await waitFor(() =>
      expect(
        screen.getByRole("button", {
          name: /^View as, currently table: Expected recoverable by population/,
        }),
      ).toBeInTheDocument(),
    );
    // The other figures are untouched: the choice is per figure.
    expect(
      screen.getByRole("button", { name: /^View as, currently bar: Recovery rate by payer/ }),
    ).toBeInTheDocument();
  });

  it("opens a figure full screen and keeps the drawing the reader chose", async () => {
    mountReport();
    await userEvent.click(
      screen.getAllByRole("button", { name: /^View full screen: Recovery rate by payer/ })[0],
    );
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Recovery rate by payer")).toBeInTheDocument();
  });

  it("offers no 'Monitor this' on a report figure, because the server refuses one", () => {
    // A run's stored analysis carries no measures, so
    // `POST /v1/monitors/pins` refuses it. No dead button: the chart is
    // handed no investigation id, so the affordance is not drawn.
    mountReport();
    expect(screen.queryByRole("button", { name: /^Monitor / })).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* 4. The composer path, end to end                                    */
/* ------------------------------------------------------------------ */

/**
 * `src/lib/__fixtures__/deep-research-offer-turn.json` is the terminal
 * frame of a live turn asking "run deep research on what we can actually
 * recover from Atlas Commercial denials" — an ORDINARY answer (two
 * findings, four figures, its own narrative) carrying `deep_research`
 * beside them. The whole point of the offer is that it rides along: the
 * answer above it is a real answer, and the run is what is on offer next.
 */
describe("the composer path — the offer survives the seam", () => {
  const PIN = {
    watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
    pack: { packId: "base-rcm", version: "1.0.0" },
  };

  function answerFrom(raw: unknown) {
    const parsed = parseTurnResponse(raw, PIN);
    expect(parsed.drift, "the fixture must parse with no contract drift").toEqual([]);
    let answer = emptyAnswer();
    for (const event of turnResponseToEvents(parsed.value!, newReceivedState())) {
      answer = applyEventToAnswer(answer, event);
    }
    return answer;
  }

  it("carries the resolved population from the wire onto the answer", () => {
    const answer = answerFrom(offerTurnFixture);
    expect(answer.deepResearch).toBeDefined();
    expect(answer.deepResearch?.population).toEqual({
      kind: "payer",
      values: ["Atlas Commercial"],
      label: "denials from Atlas Commercial",
    });
  });

  it("renders it as a launch card under the answer, with the cost stated", () => {
    const answer = answerFrom(offerTurnFixture);
    useSessionStore.setState({ settings: DEFAULT_SETTINGS });
    mount(
      <AnswerCard
        turn={{
          id: "turn_1",
          index: 0,
          submission: {
            utterance:
              "run deep research on what we can actually recover from Atlas Commercial denials",
          },
          answer,
        }}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Deep research on denials from Atlas Commercial" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/About a minute/)).toBeInTheDocument();
    // The answer itself is untouched — this is an offer beside it, not a
    // refusal in place of it. Its own findings are still on screen.
    expect(screen.getAllByText(/Atlas Commercial/).length).toBeGreaterThan(1);
  });

  it("draws no launch card on an answer the server made no offer on", () => {
    const withoutOffer = { ...(offerTurnFixture as Record<string, unknown>), deep_research: null };
    const answer = answerFrom(withoutOffer);
    expect(answer.deepResearch).toBeUndefined();
    useSessionStore.setState({ settings: DEFAULT_SETTINGS });
    mount(
      <AnswerCard
        turn={{ id: "turn_1", index: 0, submission: { utterance: "why did cash fall?" }, answer }}
      />,
    );
    expect(screen.queryByRole("heading", { name: /^Deep research on/ })).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* 5. Taking it away                                                   */
/* ------------------------------------------------------------------ */

describe("the export — every row, under every caveat", () => {
  const csv = researchReportToCsv(REPORT, {
    runId: RUN.id,
    exportedAt: new Date("2026-08-11T03:00:00Z"),
  });

  it("opens with the determination, its interval and the censoring statements", () => {
    expect(csv).toContain("# Revi — deep research:");
    expect(csv).toContain("$1,167,668.88, between $874,052.42 and $1,518,693.69");
    expect(csv).toContain("counted in neither the wins nor the losses");
  });

  it("carries every caveat the screen carries", () => {
    for (const title of ["Some dollars could not be estimated yet", "How these ranges combine"]) {
      expect(csv).toContain(title);
    }
  });

  it("carries the strata, the rates, the bands, the deadline and the contrasts", () => {
    const body = csv
      .split("\r\n")
      .filter((line) => line !== "" && !/^"?#/.test(line));
    // The header row, then one row per published object.
    expect(body[0]).toMatch(/^section,population,/);
    const sections = new Set(body.slice(1).map((line) => line.split(",")[0]));
    expect(sections).toEqual(
      new Set(["headline", "stratum", "rate", "timeliness", "deadline", "contrast"]),
    );
    // Every population the report holds, priced or refused, is a row.
    const strata = body.slice(1).filter((line) => line.startsWith("stratum,"));
    expect(strata).toHaveLength(
      (REPORT.strata ?? []).length + (REPORT.not_estimable ?? []).length,
    );
  });

  it("exports a refused rate as an EMPTY cell beside its tier, never as a zero", () => {
    const lines = csv.split("\r\n");
    const header = lines.find((line) => line.startsWith("section,"))!.split(",");
    const rateAt = header.indexOf("rate");
    const evidenceAt = header.indexOf("evidence");
    const refused = lines.filter((line) => line.split(",")[evidenceAt] === "not_estimable");
    expect(refused.length).toBeGreaterThan(0);
    for (const line of refused) expect(line.split(",")[rateAt]).toBe("");
  });
});
