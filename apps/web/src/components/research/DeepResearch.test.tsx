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
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { AnswerCard } from "@/components/answer/AnswerCard";
import { TurnInput } from "@/components/chat/TurnInput";
import { PortfolioCard } from "@/components/portfolio/PortfolioPanel";
import { ResearchLaunchCard } from "@/components/research/ResearchLaunchCard";
import { ResearchProgress } from "@/components/research/ResearchProgress";
import { ResearchReportView } from "@/components/research/ResearchReport";
import { ResearchStudyView } from "@/components/research/ResearchStudy";
import { RunDeepResearchButton } from "@/components/research/ResearchOffer";
import { TooltipProvider } from "@/components/ui/tooltip";
import offerTurnFixture from "@/lib/__fixtures__/deep-research-offer-turn.json";
import previewFixture from "@/lib/__fixtures__/deep-research-preview.json";
import runFixture from "@/lib/__fixtures__/deep-research-run.json";
import studyFixture from "@/lib/__fixtures__/deep-research-study.json";
import { newReceivedState, parseTurnResponse, turnResponseToEvents } from "@/lib/contract";
import {
  applyResearchFrame,
  initialWatchState,
  mapResearchOffer,
  mapResearchPreview,
  offerFromPreview,
  parseResearchPreview,
  type ResearchPreview,
  isResearchStudy,
  measuredFigures,
  parseResearchRun,
  type ResearchReport,
  type ResearchRun,
  type ResearchStudy,
  type ResearchWatchState,
} from "@/lib/deepResearch";
import { researchReportToCsv, researchStudyToCsv } from "@/lib/export";
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

/**
 * NOTHING IN THIS FILE TOUCHES A NETWORK.
 *
 * The dry run is a real POST (`plan_only: true`), so every surface that
 * offers deep research now reaches for `fetch`. The default stub refuses,
 * which is the honest baseline for a test that is not about the preview:
 * a suite that quietly opened a socket to localhost would pass or fail on
 * whether somebody had the API running.
 */
let calls: Array<{ url: string; body: Record<string, unknown> }> = [];

function stubFetch(reply: (body: Record<string, unknown>) => unknown | Promise<unknown>): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const body = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {};
      calls.push({ url: String(url), body });
      const payload = await reply(body);
      return {
        ok: true,
        status: 200,
        json: async () => payload,
        text: async () => JSON.stringify(payload),
      } as unknown as Response;
    }),
  );
}

beforeEach(() => {
  calls = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      throw new TypeError("no network in this test");
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const RUN = runFixture as unknown as ResearchRun;
const REPORT = RUN.report as ResearchReport;
const PREVIEW = mapResearchPreview(
  (previewFixture as Record<string, unknown>).preview,
) as ResearchPreview;

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
/* 1b. The dry run — what a run WOULD do                               */
/* ------------------------------------------------------------------ */

/**
 * `src/lib/__fixtures__/deep-research-preview.json` is the whole 200 a
 * `plan_only: true` request answers with: a run envelope carrying an empty
 * id and the status `preview`, and inside it the resolved dry run for the
 * research question "which payers are slowing our cash the most".
 *
 * What is asserted here is the same thing the report's tests assert about
 * numbers, one level up: that no sentence on the confirmation is composed
 * by this client. The path choices, the reasons and the rationale arrive
 * written, beside the coverage figures they quote, and the card's job is
 * to print them.
 */
describe("the dry run — read at the seam", () => {
  it("accepts the preview status, which is the whole point of the response", () => {
    // The regression this pins: `preview` was missing from the accepted
    // statuses, so the ONE response the confirmation card exists to
    // render was rejected as contract drift and read as null.
    const { value, drift } = parseResearchPreview(previewFixture);
    expect(drift).toEqual([]);
    expect(value).not.toBeNull();
    expect(value?.population.kind).toBe("all_open");
  });

  it("carries the question, the period, the path choices and the readings", () => {
    const general = PREVIEW.generalized;
    expect(general?.researchQuestion).toBe("which payers are slowing our cash the most");
    // What the RESEARCH reads, which is not the review's population — the
    // whole reason the two are stated separately on the card.
    expect(general?.populationLabel).toBe("everything in your data");
    expect(general?.windowLabel).toBe("May 5, 2026 through Aug 2, 2026");
    expect(general?.pathChoices).toHaveLength(3);
    expect(general?.knowledgeConsulted.map((n) => n.title)).toEqual([
      "Timely filing limits vary by payer contract",
      "Commercial payers slow down at quarter end",
    ]);
    expect(general?.readings).toHaveLength(4);
    // Every reading arrives with the reason it is in the run — the field
    // the card is load-bearing about.
    for (const reading of general?.readings ?? []) expect(reading.reason).not.toBe("");
    expect(general?.authoredBy).toBe("model");
    expect(general?.roundsPlanned).toBe(3);
    expect(general?.refusal).toBe("");
  });

  it("keeps a reading whose family this build does not know", () => {
    // The opposite verdict to `mapResearchSelector`'s, and deliberately:
    // a selector's kind is POSTED back, so an unknown one makes the whole
    // selector unusable. A reading's shape is never rendered and never
    // sent anywhere, so the title and the reason still reach the reader.
    const read = mapResearchPreview({
      population: { kind: "all_open", values: [] },
      generalized: {
        research_question: "what is going on with COB",
        readings: [
          { shape: "seasonality", title: "COB denials by month", reason: "Because of the pattern." },
          { shape: "trend", title: "COB denials over time", reason: "Because it may be new." },
        ],
      },
    });
    expect(read?.generalized?.readings).toHaveLength(2);
    expect(read?.generalized?.readings[0]?.shape).toBeUndefined();
    expect(read?.generalized?.readings[0]?.title).toBe("COB denials by month");
    expect(read?.generalized?.readings[1]?.shape).toBe("trend");
  });

  it("drops a preview with no population behind it, and a block with no question", () => {
    expect(mapResearchPreview({ scope: { open_denials: 1, open_dollars_cents: 1 } })).toBeUndefined();
    expect(
      mapResearchPreview({ population: { kind: "all_open" }, generalized: { window_label: "July" } })
        ?.generalized,
    ).toBeUndefined();
  });
});

/**
 * THE ONE THING THIS CARD MUST NOT DO: promise a run other than the one
 * the button starts.
 *
 * The platform executes two kinds of run. A question the definitions
 * library can research is executed as a STUDY, so the readings resolved
 * for it are the readings that will be taken and belong in "what it will
 * look at". A question it cannot research falls back to the recoverability
 * review — the server branches on exactly that, and so does this card.
 * Everything below is about the two staying in step: what the zones
 * promise is what confirming buys, in both directions.
 */
describe("the launch card — a research question, previewed", () => {
  const ASKED = "which payers are slowing our cash the most";
  const previewed = () => offerFromPreview(PREVIEW, ASKED);

  it("describes the STUDY, because that is what confirming starts", () => {
    const { container } = mount(<ResearchLaunchCard offer={previewed()} />);
    // The card is about the QUESTION, and the question is printed rather
    // than a population count that answers something else.
    expect(
      screen.getByRole("heading", { name: "Deep research on your question" }),
    ).toBeInTheDocument();
    expect(screen.getByText(ASKED)).toBeInTheDocument();
    expect(
      screen.getByText("Reading everything in your data, over May 5, 2026 through Aug 2, 2026."),
    ).toBeInTheDocument();
    expect(screen.getByText(/About a minute/)).toBeInTheDocument();
    // The button still names WHAT IS POSTED — the population, which the
    // radio group below can still change.
    expect(
      screen.getByRole("button", { name: "Run deep research on every open denial" }),
    ).toBeInTheDocument();
    // "What it will look at" holds the readings the study will take, and
    // NOT the recoverability review's angles, which it will not.
    const runZone = container.querySelector("ul");
    expect(runZone).toHaveTextContent("Days in A/R by payer");
    expect(runZone).not.toHaveTextContent("What the open inventory is worth");
  });

  it("no longer says the button does something else, because it does not", () => {
    mount(<ResearchLaunchCard offer={previewed()} />);
    // The M55 card carried this sentence and it was correct then: the run
    // on offer was the review and the readings were resolved for a preview
    // nothing executed. Executing them makes the sentence false, and a
    // stale honesty note is the most expensive kind of copy to leave in.
    expect(screen.queryByText(/It does not take the readings below/)).not.toBeInTheDocument();
    expect(
      screen.queryByText(/Measure what is realistically recoverable/),
    ).not.toBeInTheDocument();
  });

  it("prints what the question reaches in the data, verbatim and unabridged", () => {
    mount(<ResearchLaunchCard offer={previewed()} />);
    expect(screen.getByText("What this question reaches in your data")).toBeInTheDocument();
    for (const choice of PREVIEW.generalized?.pathChoices ?? []) {
      // Verbatim: each statement arrives beside the coverage figure it
      // quotes, and a re-wording here is the phrasing that loses it.
      expect(screen.getByText(choice.statement)).toBeInTheDocument();
    }
    expect(screen.getByText(/99.8% of the claims in this period/)).toBeInTheDocument();
  });

  it("names the background notes it read, by title, beside its own sentence", () => {
    mount(<ResearchLaunchCard offer={previewed()} />);
    expect(screen.getByText("Background notes it read")).toBeInTheDocument();
    expect(
      screen.getByText(/Two background notes bear on this question/),
    ).toBeInTheDocument();
    expect(screen.getByText("Timely filing limits vary by payer contract")).toBeInTheDocument();
    expect(screen.getByText("Commercial payers slow down at quarter end")).toBeInTheDocument();
  });

  it("gives every reading its own reason, not just a list of titles", () => {
    const { container } = mount(<ResearchLaunchCard offer={previewed()} />);
    const runZone = container.querySelector("ul");
    for (const reading of PREVIEW.generalized?.readings ?? []) {
      expect(runZone).toHaveTextContent(reading.title);
      expect(runZone).toHaveTextContent(reading.reason);
    }
    // A confirmation that lists what will be read without saying why is a
    // progress bar in advance, and the reasons are the one thing on this
    // surface a reader can actually correct before spending the minute.
    expect(runZone?.querySelectorAll("li")).toHaveLength(
      PREVIEW.generalized?.readings.length ?? 0,
    );
  });

  it("says who chose the readings, and says it differently when nobody did", () => {
    mount(<ResearchLaunchCard offer={previewed()} />);
    expect(screen.getByText(/I read the level first, then cut it by payer/)).toBeInTheDocument();
    expect(screen.queryByText(/its own standing set/)).not.toBeInTheDocument();
    cleanup();

    const standing = offerFromPreview(
      {
        ...PREVIEW,
        generalized: { ...PREVIEW.generalized!, authoredBy: "revi" as const },
      },
      ASKED,
    );
    mount(<ResearchLaunchCard offer={standing} />);
    // A fallback presented as a decision is the small dishonesty that
    // makes every other claim on the card worth less.
    expect(
      screen.getByText(/Revi picked these readings from its own standing set/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/I read the level first, then cut it by payer/),
    ).not.toBeInTheDocument();
  });

  it("says in plain language that it would go back for more", () => {
    mount(<ResearchLaunchCard offer={previewed()} />);
    expect(
      screen.getByText(/It reads, then decides what to go after next/),
    ).toBeInTheDocument();
    expect(screen.getByText(/up to 3\s*rounds of that/)).toBeInTheDocument();
    cleanup();

    const single = offerFromPreview(
      { ...PREVIEW, generalized: { ...PREVIEW.generalized!, roundsPlanned: 1 } },
      ASKED,
    );
    mount(<ResearchLaunchCard offer={single} />);
    expect(
      screen.queryByText(/It reads, then decides what to go after next/),
    ).not.toBeInTheDocument();
  });

  it("stands a refusal where the readings would be, and leaves the run alone", () => {
    const refusal =
      "Nothing loaded here measures how long a payer takes to answer, so this question cannot be answered from your data.";
    const refused = offerFromPreview(
      {
        ...PREVIEW,
        generalized: { ...PREVIEW.generalized!, readings: [], refusal },
      },
      ASKED,
    );
    const { container } = mount(<ResearchLaunchCard offer={refused} />);
    expect(screen.getByText(refusal)).toBeInTheDocument();
    // The card falls back to the review exactly as the server does with
    // the same POST — and SAYS SO, because a button whose meaning changed
    // silently is worse than no button.
    expect(
      screen.getByRole("heading", { name: "Deep research on every open denial" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/measures what is recoverable out of every open denial instead/),
    ).toBeInTheDocument();
    expect(container.querySelector("[data-research-reasoning]")).toHaveAttribute(
      "data-research-reasoning",
      "review",
    );
    // The run on offer measures something the refusal is not about, so
    // refusing it here would refuse a run the platform can do.
    expect(
      screen.getByRole("button", { name: "Run deep research on every open denial" }),
    ).toBeInTheDocument();
    expect(screen.getByText(/About a minute/)).toBeInTheDocument();
  });

  it("keeps the population chooser working underneath the question", async () => {
    mount(<ResearchLaunchCard offer={previewed()} />);
    expect(screen.getByRole("radio", { name: "every open denial" })).toBeChecked();
    await userEvent.click(screen.getByRole("radio", { name: "denials from Atlas Commercial" }));
    expect(
      screen.getByRole("button", { name: "Run deep research on denials from Atlas Commercial" }),
    ).toBeInTheDocument();
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
    research_question: "",
    population: { kind: "all_open", values: [], label: "every open denial" },
    progress: {
      phase: "plan",
      angle_index: 0,
      angle_total: 0,
      message: "",
      elapsed_ms: 0,
      round_index: 0,
      round_total: 0,
    },
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

  it("promises the report will be here, and offers the stop that is real", async () => {
    // Two different promises and the surface owes both: leaving does not
    // stop the run, stopping does. Until the wire had a cancel this file
    // argued for the first alone, because a "Stop" that abandoned the
    // watcher while the server kept spending would have lied about itself.
    const onStop = vi.fn();
    mount(<ResearchProgress state={replay(FRAMES)} onStop={onStop} />);
    expect(screen.getByText(/You can leave this page/)).toBeInTheDocument();
    expect(screen.getByText(/the run ends where it is and nothing is published/)).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Stop this run" }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("says the stop is in flight rather than taking a second press", () => {
    const onStop = vi.fn();
    mount(<ResearchProgress state={replay(FRAMES)} onStop={onStop} stopping />);
    const button = screen.getByRole("button", { name: "Stopping…" });
    expect(button).toBeDisabled();
  });

  it("offers no stop where there is nothing left to stop", () => {
    // A finished run, a failed one and one already stopped have no work
    // left to end, and a control that did nothing would be the same lie in
    // the other direction.
    const finished = applyResearchFrame(replay(FRAMES), {
      kind: "research_complete",
      data: REPORT as unknown as Record<string, unknown>,
    });
    mount(<ResearchProgress state={finished} onStop={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
    cleanup();

    const failed = applyResearchFrame(replay(FRAMES), {
      kind: "error",
      data: { message: "This run stopped before it could finish." },
    });
    mount(<ResearchProgress state={failed} onStop={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
    cleanup();

    mount(<ResearchProgress state={stoppedState()} onStop={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /stop/i })).not.toBeInTheDocument();
  });

  /* ---------------------------------------------------------------- */
  /* The stop — a run somebody ended is not a run that went wrong      */
  /* ---------------------------------------------------------------- */

  /** The run as it comes back from `POST .../cancel`, mid-measurement. */
  function stoppedState(): ResearchWatchState {
    return applyResearchFrame(replay(FRAMES.slice(0, 4)), {
      kind: "research_cancelled",
      data: {
        id: "dr_test",
        message:
          "This run was stopped on request, so nothing was published. What it had got through is kept.",
      },
    });
  }

  it("settles on the stop frame without calling it a failure", () => {
    const stopped = stoppedState();
    expect(stopped.run.status).toBe("cancelled");
    // The `error` frame is the other thing entirely, and the two must not
    // collapse: one is the platform failing, the other is the reader
    // deciding.
    expect(stopped.run.status).not.toBe("failed");
  });

  it("renders a stopped run in the calm register, saying plainly what happened", () => {
    const { container } = mount(<ResearchProgress state={stoppedState()} />);
    expect(container.querySelector("[data-research-progress]")).toHaveAttribute(
      "data-research-progress",
      "cancelled",
    );
    expect(screen.getByText(/Stopped working through/)).toBeInTheDocument();
    expect(
      screen.getByText(/stopped on request, so nothing was published/),
    ).toBeInTheDocument();
    // NOT the warning register. Reporting somebody's own decision back to
    // them in red is how you teach them never to press it again.
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    // Nothing partial is published, so nothing partial is promised.
    expect(screen.queryByText(/You can leave this page/)).not.toBeInTheDocument();
  });

  it("says how far the stopped run got, without claiming the angle it stopped on", () => {
    mount(<ResearchProgress state={stoppedState()} />);
    expect(screen.getByText(/It had reached angle 3 of 8/)).toBeInTheDocument();
  });

  it("keeps a refused stop off the run it failed to stop", () => {
    // The run is still going and still drawn; the only thing that failed
    // is the request to end it, so that is the only thing this says.
    mount(
      <ResearchProgress
        state={replay(FRAMES)}
        onStop={vi.fn()}
        stopError="this run could not be stopped: HTTP 503"
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("HTTP 503");
    expect(screen.getByText("Running the analysis")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Stop this run" })).toBeInTheDocument();
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

  /* ---------------------------------------------------------------- */
  /* The rounds — a run that read something and went after it          */
  /* ---------------------------------------------------------------- */

  /** The frame a run emits when it has decided to chase what it found. */
  const CHASING = {
    kind: "research_progress",
    data: {
      phase: "round",
      angle_index: 4,
      angle_total: 4,
      message:
        "Round 1 — chasing it: the payer spread was decisive — cutting inside Veritas Comp Fund next",
      elapsed_ms: 9_100,
      round_index: 1,
      round_total: 3,
    },
  };

  it("draws no iteration row for a run that took one pass", () => {
    const { container } = mount(<ResearchProgress state={replay(FRAMES)} />);
    // A permanently pending "going after what it found" would promise a
    // second round this question never earned.
    expect(container.querySelector('[data-phase="round"]')).toBeNull();
  });

  it("says the run read something and went after it, in the server's own sentence", () => {
    const { container } = mount(
      <ResearchProgress state={replay([...FRAMES.slice(0, 4), CHASING])} />,
    );
    const row = container.querySelector('[data-phase="round"]');
    expect(row).not.toBeNull();
    expect(row).toHaveAttribute("data-phase-state", "active");
    expect(screen.getByText("Going after what it found")).toBeInTheDocument();
    // VERBATIM. The reason a round exists was written once, by whatever
    // decided it; a second wording here would be the one that goes stale.
    expect(
      screen.getByText(
        /the payer spread was decisive — cutting inside Veritas Comp Fund next/,
      ),
    ).toBeInTheDocument();
  });

  it("keeps the round on the surface once the run is back to measuring", () => {
    const measuring = {
      kind: "research_progress",
      data: {
        phase: "execute",
        angle_index: 1,
        angle_total: 3,
        message: "Chasing what round 1 turned up: payer (1 of 3)",
        elapsed_ms: 11_400,
        round_index: 1,
        round_total: 3,
      },
    };
    const { container } = mount(
      <ResearchProgress state={replay([...FRAMES.slice(0, 4), CHASING, measuring])} />,
    );
    const row = container.querySelector('[data-phase="round"]');
    // Ordering alone would call the finished round "not started": the
    // counter is a record of something that happened.
    expect(row).toHaveAttribute("data-phase-state", "done");
    expect(row).toHaveTextContent("round 1 of 3");
    expect(screen.getByText(/Chasing what round 1 turned up/)).toBeInTheDocument();
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
    expect(figure).toHaveTextContent("$309,129.94");
    expect(figure).toHaveTextContent("Between $224,079.89 and $436,678.86");
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
    expect(row).toHaveTextContent("$5,974.52");
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
    expect(fold).toHaveTextContent("13 things to know");
    await userEvent.click(fold);

    // Every title is the one `warnings.ts` publishes for the code —
    // nothing here renders an ALL_CAPS handle at a reader. The censoring
    // code is raised once per sentence, so its title appears once per
    // sentence rather than being collapsed onto a count that would hide
    // the rest of them. Seven, since the review: the four the denominator
    // costs, plus the waiting periods, where they do and do not apply, and
    // what the answered-only denominator left behind.
    for (const title of [
      "Only your own data was used",
      "How these ranges combine",
      // Two limits on one figure, and two titles: how the ranges add up is
      // a different fact from what the range does not move with.
      "What the range does not move with",
      "Small groups were set aside, not guessed",
      "Some dollars could not be estimated yet",
      "Read the best and worst with care",
    ]) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
    expect(screen.getAllByText("Still-open cases are not counted either way")).toHaveLength(7);
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

  it("resolves the SAME preview the composer's control does, for the same question", async () => {
    stubFetch(() => previewFixture);
    const answer = answerFrom(offerTurnFixture);
    useSessionStore.setState({ settings: DEFAULT_SETTINGS });
    const utterance =
      "run deep research on what we can actually recover from Atlas Commercial denials";
    mount(
      <AnswerCard turn={{ id: "turn_1", index: 0, submission: { utterance }, answer }} />,
    );

    await screen.findByText("What Revi checked before choosing");
    // The analyst's own utterance IS the research question, and the
    // population posted is the server's own selector, byte for byte.
    expect(calls).toHaveLength(1);
    expect(calls[0]?.body.plan_only).toBe(true);
    expect(calls[0]?.body.question).toBe(utterance);
    expect(calls[0]?.body.population).toEqual({
      kind: "payer",
      values: ["Atlas Commercial"],
      label: "denials from Atlas Commercial",
    });
    // Same content as the composer route: both describe one question.
    expect(screen.getByText("Days in A/R by payer")).toBeInTheDocument();
    expect(screen.getByText("What this question reaches in your data")).toBeInTheDocument();
    // The card is about the question, and the button still names the
    // server's own population — which is what a launch would post.
    expect(
      screen.getByRole("heading", { name: "Deep research on your question" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Run deep research on denials from Atlas Commercial" }),
    ).toBeInTheDocument();
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
/* 4b. The composer's own control                                      */
/* ------------------------------------------------------------------ */

/**
 * TWO WAYS TO SPEND ONE SENTENCE.
 *
 * Enter asks the question and gets an answer in seconds; "Deep research"
 * takes the same text and spends a minute on it. What these assert is
 * that the second one costs the first one nothing — the box, Enter,
 * Shift+Enter and the quick answer path are untouched — and that the
 * press PREVIEWS rather than launches, because a control that spent a
 * minute on a press would be the one thing this whole surface is built to
 * avoid.
 */
describe("the composer's deep research control", () => {
  const NAME = "Deep research on this question — see what it will look at";
  const ASKED = "which payers are slowing our cash the most";

  /** A started run, as the launch POST answers it. */
  const STARTED = {
    id: "dr_new",
    session_id: "sess_new",
    status: "planning",
    created_at: "2026-08-11T02:40:00Z",
    data_load_label: "the load through Aug 2, 2026",
    population: { kind: "all_open", values: [], label: "every open denial" },
    progress: { phase: "plan", angle_index: 0, angle_total: 0, message: "", elapsed_ms: 0 },
  };

  beforeEach(() => {
    useSessionStore.getState().reset();
    // `reset()` deliberately leaves `replaying` alone (the replay owns its
    // own lifecycle), so the idle composer is asserted explicitly here
    // rather than inherited from whichever test ran last.
    useSessionStore.setState({
      submit: vi.fn().mockResolvedValue(undefined),
      replaying: false,
      streamingTurnId: null,
      switchingSessionId: null,
      newChatPending: false,
    });
  });

  it("is a real button beside Send, and refuses an empty box exactly as Send does", () => {
    mount(<TurnInput suggestions={[]} />);
    const control = screen.getByRole("button", { name: NAME });
    // A real button, with native semantics: Enter and Space are the
    // browser's job and are not re-implemented here.
    expect(control.tagName).toBe("BUTTON");
    expect(control).toBeDisabled();
    expect(screen.getByLabelText("Send")).toBeDisabled();
    // Persistent, never hover-revealed.
    expect(control.className).not.toMatch(/opacity-0/);
  });

  it("is reachable from the box by keyboard alone", async () => {
    mount(<TurnInput suggestions={[]} />);
    const box = screen.getByRole("textbox");
    await userEvent.type(box, ASKED);
    expect(box).toHaveFocus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: NAME })).toHaveFocus();
    expect(screen.getByRole("button", { name: NAME })).toBeEnabled();
  });

  it("is disabled for the whole of a reference-demo replay, exactly as Send is", () => {
    useSessionStore.setState({ replaying: true });
    mount(<TurnInput suggestions={[]} />);
    expect(screen.getByRole("button", { name: NAME })).toBeDisabled();
  });

  it("previews what a run would look at, and starts nothing", async () => {
    stubFetch(() => previewFixture);
    mount(<TurnInput suggestions={[]} />);
    await userEvent.type(screen.getByRole("textbox"), ASKED);
    await userEvent.click(screen.getByRole("button", { name: NAME }));

    await screen.findByRole("heading", { name: "Deep research on your question" });
    // ONE request, and it is the dry run.
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toMatch(/\/v1\/deep-research$/);
    expect(calls[0]?.body.plan_only).toBe(true);
    expect(calls[0]?.body.question).toBe(ASKED);
    // The composer is NOT cleared: nothing was spent, and a box emptied
    // by a preview would have thrown the question away.
    expect(screen.getByRole("textbox")).toHaveValue(ASKED);
  });

  it("shows the same card the answer path shows — readings, reasons and all", async () => {
    stubFetch(() => previewFixture);
    mount(<TurnInput suggestions={[]} />);
    await userEvent.type(screen.getByRole("textbox"), ASKED);
    await userEvent.click(screen.getByRole("button", { name: NAME }));

    await screen.findByText("What Revi checked before choosing");
    expect(screen.getByText("Days in A/R by payer")).toBeInTheDocument();
    expect(
      screen.getByText(/the same measure is cut by payer rather than read as a total/),
    ).toBeInTheDocument();
    expect(screen.getByText("Timely filing limits vary by payer contract")).toBeInTheDocument();
  });

  it("confirming posts the same question the card described", async () => {
    stubFetch((body) => (body.plan_only === true ? previewFixture : STARTED));
    mount(<TurnInput suggestions={[]} />);
    await userEvent.type(screen.getByRole("textbox"), ASKED);
    await userEvent.click(screen.getByRole("button", { name: NAME }));
    await userEvent.click(
      await screen.findByRole("button", { name: "Run deep research on every open denial" }),
    );

    await waitFor(() => expect(calls).toHaveLength(2));
    // The SAME question, and this time it is a launch rather than a
    // preview: the run a reader confirmed has to be the run they read
    // about.
    expect(calls[1]?.body.question).toBe(ASKED);
    expect(calls[1]?.body.plan_only).toBeUndefined();
  });

  it("shows the server's own refusal and keeps the question in the box", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 400,
        text: async () =>
          JSON.stringify({
            code: "population_empty",
            message: "There are no open denials in this load to research.",
            correlation_id: "cor_1",
          }),
      })),
    );
    mount(<TurnInput suggestions={[]} />);
    await userEvent.type(screen.getByRole("textbox"), ASKED);
    await userEvent.click(screen.getByRole("button", { name: NAME }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("There are no open denials in this load to research.");
    // No card was opened over a preview that does not exist, and the
    // question survives so it can be sent the ordinary way.
    expect(screen.queryByRole("heading", { name: /^Deep research on/ })).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveValue(ASKED);
  });

  it("leaves the ordinary question path exactly as it was", async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ submit });
    mount(<TurnInput suggestions={[]} />);
    const box = screen.getByRole("textbox");

    // Shift+Enter still breaks the line and sends nothing.
    await userEvent.type(box, "line one{Shift>}{Enter}{/Shift}line two");
    expect(box).toHaveValue("line one\nline two");
    expect(submit).not.toHaveBeenCalled();

    await userEvent.clear(box);
    await userEvent.type(box, "why did cash fall?{Enter}");
    expect(submit).toHaveBeenCalledWith({ utterance: "why did cash fall?" });
    expect(box).toHaveValue("");
    // And nothing about an ordinary question touches deep research.
    expect(calls).toEqual([]);
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
    expect(csv).toContain("$309,129.94, between $224,079.89 and $436,678.86");
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

/* ------------------------------------------------------------------ */
/* 5. The study — the other artifact a run can produce                 */
/* ------------------------------------------------------------------ */

/**
 * `src/lib/__fixtures__/deep-research-study.json` is captured verbatim from
 * a live run (`GET /v1/deep-research/dr_7d4e191cbf5644b1`, the load through
 * Aug 2, 2026): the acceptance question, planned by the real control plane,
 * four read-and-decide rounds, fifteen readings with the reasons the
 * planner wrote for them, a determination composed under the grounding
 * validator, and the walk that produced all of it. Same discipline the
 * review's fixture follows and for the same reason: the failures worth
 * catching are the ones a real payload has.
 *
 * What is asserted is not how it looks. It is that a study cannot lie: that
 * the answer is the server's and never composed here, that a reading's
 * reason and verdict travel together, that a ceiling never renders as a
 * measurement, and that the walk is present and quiet rather than absent.
 */
describe("the study report", () => {
  const study = (studyFixture as { research_report: ResearchStudy }).research_report;
  // Every list on a study payload is optional on the wire (a study that
  // read nothing publishes none of them) and present on a real one. Named
  // once so the assertions below read as the properties they are.
  const readings = study.readings ?? [];
  const rounds = study.walk.rounds ?? [];
  const pathChoices = study.path_choices ?? [];
  const notes = study.knowledge_consulted ?? [];
  const mount = (node: React.ReactElement) =>
    render(<MemoryRouter><TooltipProvider>{node}</TooltipProvider></MemoryRouter>);

  it("is discriminated by the payload's own kind, not by its shape", () => {
    expect(isResearchStudy(study)).toBe(true);
    expect(isResearchStudy(runFixture.report)).toBe(false);
    // …and the run envelope says which, so a client branches on a field.
    const parsed = parseResearchRun(studyFixture);
    expect(parsed.drift).toEqual([]);
    expect(parsed.value?.report_kind).toBe("generalized");
    expect(parsed.value?.research_report).toBeDefined();
    expect(parsed.value?.report).toBeUndefined();
  });

  it("leads with the determination, in the server's own words", () => {
    mount(<ResearchStudyView study={study} runId="dr_test" />);
    expect(screen.getByText("What Revi determined")).toBeInTheDocument();
    // The FIRST sentence a reader sees is the platform's, verbatim. Nothing
    // on this surface composes a claim about the data.
    const opening = study.determination.statement.split(". ")[0]!;
    expect(screen.getByText(new RegExp(opening.slice(0, 60)))).toBeInTheDocument();
    expect(study.determination.composed).toBe(true);
  });

  it("answers both halves of the question it was asked", () => {
    // The acceptance bar, asserted on the artifact rather than by eye: a
    // composite question owes an answer to each half, and the determination
    // either gives one or says which half it could not give.
    const text = study.determination.statement.toLowerCase();
    expect(study.research_question).toContain("why");
    expect(study.research_question).toContain("what it will take");
    expect(text).toMatch(/bringing it down|what it (will )?take|to bring it down|unanswered/);
  });

  it("gives every reading the reason it was taken and what it settled", () => {
    const { container } = mount(<ResearchStudyView study={study} runId="dr_test" />);
    const blocks = [...container.querySelectorAll("[data-research-reading]")];
    expect(blocks).toHaveLength(readings.length);
    for (const [index, reading] of readings.entries()) {
      expect(blocks[index]).toHaveTextContent(reading.title);
      expect(reading.reason).not.toBe("");
      expect(blocks[index]).toHaveTextContent(reading.reason.slice(0, 40));
      if (reading.refusal === "") {
        expect(reading.settled).not.toBe("");
        expect(blocks[index]).toHaveTextContent(reading.settled.slice(0, 40));
      }
    }
  });

  it("never renders a withheld or bounded figure as a measurement", () => {
    mount(<ResearchStudyView study={study} runId="dr_test" />);
    for (const reading of readings) {
      for (const figure of reading.figures ?? []) {
        if (figure.evidence === "measured") continue;
        // A ceiling reads as a ceiling and a withheld row says so — never a
        // dimmed zero where the measurement would have been.
        expect(figure.display === "too small to publish" || figure.display.startsWith("≤")).toBe(
          true,
        );
        expect(figure.value === null || figure.bounded).toBe(true);
      }
      // And the measured set the charts draw excludes both.
      for (const figure of measuredFigures(reading)) {
        expect(figure.bounded).toBe(false);
        expect(figure.withheld).toBe(false);
      }
    }
  });

  it("draws each reading's figure through the same chart seam an answer uses", () => {
    const { container } = mount(<ResearchStudyView study={study} runId="dr_test" />);
    // Full screen and the CSV are the controls every chart in this product
    // has; a bespoke widget here would drift from its cousin. Asserted
    // through the accessible names, which is how a reader reaches them.
    const drawn = readings.filter((reading) => reading.chart_id !== "");
    expect(drawn.length).toBeGreaterThan(0);
    expect(
      screen.getAllByRole("button", { name: /^View full screen:/ }).length,
    ).toBe(drawn.length);
    expect(container.querySelectorAll("figure").length).toBeGreaterThanOrEqual(drawn.length);
  });

  it("keeps the walk present, complete and closed", async () => {
    const { container } = mount(<ResearchStudyView study={study} runId="dr_test" />);
    const fold = container.querySelector("[data-research-walk]");
    expect(fold).toHaveAttribute("data-research-walk", "closed");
    expect(screen.getByText("How Revi got here")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /How Revi got here/ }));
    expect(container.querySelector("[data-research-walk]")).toHaveAttribute(
      "data-research-walk",
      "open",
    );
    // Every round the run took, with the reason it exists.
    expect(container.querySelectorAll("[data-research-round]")).toHaveLength(rounds.length);
    for (const round of rounds) {
      if (round.index === 0) continue;
      expect(round.reason).not.toBe("");
      expect(screen.getByText(round.reason)).toBeInTheDocument();
    }
    // …and it says who chose the readings, because a fallback presented as
    // a choice is a small lie about how the analysis was decided.
    expect(study.walk.authored_by).toBe("model");
    expect(screen.getByText(/readings chosen for this question/)).toBeInTheDocument();
  });

  it("prints the path choices and the background notes verbatim", () => {
    mount(<ResearchStudyView study={study} runId="dr_test" />);
    expect(screen.getByText("What Revi checked before it chose")).toBeInTheDocument();
    for (const choice of pathChoices) {
      expect(screen.getByText(choice.statement)).toBeInTheDocument();
    }
    // Titles only: a note's content shapes which reading ran and can never
    // shape what a number says.
    for (const note of notes) {
      expect(screen.getByText(note.title)).toBeInTheDocument();
      expect(note).not.toHaveProperty("key_points");
    }
  });

  it("publishes a censoring note only where outcome-like data was read", () => {
    mount(<ResearchStudyView study={study} runId="dr_test" />);
    if (study.censoring === null || study.censoring === undefined) {
      expect(screen.queryByText("What is counted, and what is not")).not.toBeInTheDocument();
      return;
    }
    expect(screen.getByText("What is counted, and what is not")).toBeInTheDocument();
    for (const statement of study.censoring.statements ?? []) {
      expect(screen.getByText(statement)).toBeInTheDocument();
    }
  });

  it("exports every group, including the ones no figure was published for", () => {
    const csv = researchStudyToCsv(study, { runId: "dr_test", exportedAt: new Date(0) });
    const lines = csv.split("\n");
    expect(csv).toContain(study.research_question);
    // The header row names the provenance columns the screen keeps quiet.
    const header = lines.find((line) => line.startsWith("reading,"));
    expect(header).toContain("read_fingerprint");
    expect(header).toContain("evidence");
    expect(header).toContain("is_ceiling");
    const rows = lines.filter((line) => line.includes("not_estimable"));
    const withheld = readings.flatMap((r) =>
      (r.figures ?? []).filter((f) => f.evidence !== "measured"),
    );
    expect(rows.length).toBeGreaterThanOrEqual(withheld.length);
    // A withheld figure exports EMPTY, never zero: a zero in a spreadsheet
    // column is a measurement the run never made. Checked by COLUMN rather
    // than by scanning the line, because a legitimate zero lives on every
    // row — the opening round is round 0 — and a regex over the whole row
    // reads that as a published figure.
    // A quoted-aware split: a reason or a settled sentence carries commas,
    // and a naive one would read the wrong column and pass for the wrong
    // reason.
    const fields = (line: string) => {
      const out: string[] = [];
      let cell = "";
      let quoted = false;
      for (let i = 0; i < line.length; i += 1) {
        const ch = line[i];
        if (quoted) {
          if (ch === '"' && line[i + 1] === '"') {
            cell += '"';
            i += 1;
          } else if (ch === '"') quoted = false;
          else cell += ch;
        } else if (ch === '"') quoted = true;
        else if (ch === ",") {
          out.push(cell);
          cell = "";
        } else cell += ch;
      }
      out.push(cell);
      return out;
    };
    const columns = fields(header!);
    const at = (row: string, name: string) => fields(row)[columns.indexOf(name)];
    for (const row of rows) {
      expect(at(row, "value")).toBe("");
      // And a ceiling never ships the two counts that reconstruct it.
      if (at(row, "is_ceiling") === "yes") {
        expect(at(row, "population")).toBe("");
        expect(at(row, "successes")).toBe("");
      }
    }
  });
});

/**
 * THE WATCHER, ON A STUDY.
 *
 * The frames are the same stream the review uses, plus one: `research_
 * readings` names what is being read WHILE it is read, and the progress
 * frames finally carry which round they belong to. Both were on the wire
 * and both were empty before this milestone.
 */
describe("watching a study", () => {
  const study = (studyFixture as { research_report: ResearchStudy }).research_report;

  function watching(): ResearchWatchState {
    return initialWatchState({
      id: "dr_test",
      session_id: "sess_test",
      status: "running",
      created_at: "2026-08-11T09:39:20Z",
      data_load_label: "the load through Aug 2, 2026",
      research_question: study.research_question,
      population: { kind: "all_open", values: [], label: "every open denial" },
      progress: {
        phase: "plan",
        angle_index: 0,
        angle_total: 0,
        message: "",
        elapsed_ms: 0,
        round_index: 0,
        round_total: 4,
      },
    });
  }

  it("names the question it is working through, not a population it never opens", () => {
    render(<ResearchProgress state={watching()} />);
    expect(
      screen.getByRole("heading", { name: new RegExp(study.research_question.slice(0, 40)) }),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Working through every open denial/)).not.toBeInTheDocument();
  });

  it("lists the readings as they are taken, each with its reason and round", () => {
    const readings = (study.readings ?? []).slice(0, 8).map((reading) => ({
      title: reading.title,
      reason: reading.reason,
      round: reading.round,
      chases: reading.chases,
    }));
    const state = applyResearchFrame(watching(), {
      kind: "research_readings",
      data: { readings },
    });
    const { container } = render(<ResearchProgress state={state} />);
    for (const reading of readings) {
      expect(screen.getByText(reading.title)).toBeInTheDocument();
    }
    // A later round says which round it is — "still going" and "chasing
    // what the last round found" are different states.
    const chased = readings.find((r) => r.round > 0);
    if (chased) {
      expect(container.querySelector(`[data-angle-round="${chased.round}"]`)).not.toBeNull();
      expect(screen.getAllByText(/Round \d/).length).toBeGreaterThan(0);
    }
  });

  it("lights the iteration row from real round counters", () => {
    let state = watching();
    state = applyResearchFrame(state, {
      kind: "research_progress",
      data: {
        phase: "execute",
        angle_index: 2,
        angle_total: 3,
        message: "Chasing what round 1 turned up: bill lag days (2 of 3)",
        elapsed_ms: 61_000,
        round_index: 1,
        round_total: 4,
      },
    });
    const { container } = render(<ResearchProgress state={state} />);
    // The row exists only because the counter says the run has been here.
    const row = container.querySelector('[data-phase="round"]');
    expect(row).not.toBeNull();
    expect(row).toHaveAttribute("data-round", "1");
  });

  it("switches to the study when the completion frame is a study", () => {
    const state = applyResearchFrame(watching(), {
      kind: "research_complete",
      data: study as unknown as Record<string, unknown>,
    });
    expect(state.run.status).toBe("complete");
    expect(state.run.report_kind).toBe("generalized");
    expect(state.run.research_report?.id).toBe(study.id);
    expect(state.run.report).toBeUndefined();
  });
});
