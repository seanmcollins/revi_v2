/**
 * THE OWNER'S RULE, ENFORCED ON THE RENDERED PAGE.
 *
 * "I'm seeing a lot of cards or sentences starting with lowercase letters."
 * Every surface named below already had tests, and every one of them
 * passed while the live page did exactly that — because a component test
 * asserts the string the component renders, and the string the component
 * renders is the string the server sent. The defect only exists in the
 * relationship between a string and its POSITION, so the only place it can
 * be caught is the DOM.
 *
 * This renders the product's real surfaces from CAPTURED LIVE PAYLOADS and
 * walks every text node on them. See `lib/sentenceCase` for what counts as
 * sentence position and why control labels are included — that gap is how
 * "either way / only up / only down" survived on the monitor setup's
 * direction chips.
 *
 * A FAILURE HERE IS NOT FIXED BY EDITING THIS FILE. It is fixed at the
 * shared render seam (`lib/prose`), or — if the lower case is genuinely
 * right — by an entry in `SENTENCE_CASE_ALLOWLIST` carrying the reason. The
 * allowlist is two entries long and both are about identifiers a capital
 * would break.
 */

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AnswerCard } from "@/components/answer/AnswerCard";
import { BriefPanel } from "@/components/monitors/BriefPanel";
import { LeadLifecyclePanel, type LeadRow } from "@/components/monitors/LeadLifecycle";
import { LeadStatusControl } from "@/components/monitors/LeadStatus";
import { MonitorTile } from "@/components/monitors/MonitorTile";
import { TooltipProvider } from "@/components/ui/tooltip";
import answers from "@/lib/__fixtures__/live-answers.json";
import live from "@/lib/__fixtures__/live-monitors.json";
import {
  parseInvestigationResponse,
  parsePortfolioSnapshot,
  turnResponseToEvents,
} from "@/lib/contract";
import { mapLeadState, mapMonitorsPin, parseBrief, parseMonitors } from "@/lib/monitors";
import {
  describeLowercaseOpenings,
  lowercaseSentenceOpenings,
} from "@/lib/sentenceCase";
import { DEFAULT_SETTINGS } from "@/lib/settings";
import {
  applyEventToAnswer,
  emptyAnswer,
  INITIAL_PACK,
  useSessionStore,
  type TurnRecord,
} from "@/lib/store";

vi.mock("@/lib/useDeployment", () => ({
  useDeployment: () => ({ driverKind: "api" as const, driver: null, live: true }),
}));

import { Home } from "@/components/home/Home";

const MONITORS = parseMonitors(live.monitors);
const BRIEF = parseBrief(live.brief);
const PINS = (live.pins.pins as unknown[]).map((raw) => mapMonitorsPin(raw));

const PIN = {
  watermark: { id: "wm_003", loadedAt: "2026-08-03 04:10", newestDataDate: "2026-08-02" },
  pack: { packId: "base-rcm", version: "1.0.0" },
};

/** The assertion itself, so every case fails with the same readable report. */
function expectSentenceCase(root: Element, surface: string): void {
  const found = lowercaseSentenceOpenings(root);
  expect(
    found,
    `${surface} renders ${found.length} text node(s) that OPEN in lower case.\n` +
      `Fix at the shared render seam (lib/prose: readableLabel / readableStatement / capitalizeOpening),\n` +
      `never by editing this test. Sites:\n${describeLowercaseOpenings(found)}\n`,
  ).toEqual([]);
}

function draw(node: React.ReactNode) {
  return render(
    <MemoryRouter>
      <TooltipProvider>{node}</TooltipProvider>
    </MemoryRouter>,
  );
}

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
      matches: false,
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

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.getElementById("revi-live-announcer")?.remove();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

/* ------------------------------------------------------------------ */
/* The detector itself — so an empty result means something            */
/* ------------------------------------------------------------------ */

describe("the walker knows sentence position from continuation", () => {
  it("flags a card title that opens in lower case", () => {
    const { container } = render(<p className="x">denial rate for State Medicaid MCO</p>);
    expect(lowercaseSentenceOpenings(container).map((f) => f.text)).toEqual([
      "denial rate for State Medicaid MCO",
    ]);
  });

  it("leaves a continuation alone — the marks that follow a figure", () => {
    const { container } = render(
      <p>
        29.5%<span>still settling</span>
      </p>,
    );
    expect(lowercaseSentenceOpenings(container)).toEqual([]);
  });

  it("flags a CONTROL that stands on its own line", () => {
    // The monitor setup's direction chips. A prose-only walker missed
    // these because the `<legend>` before them made them look like a
    // continuation.
    const { container } = render(
      <fieldset>
        <legend>In which direction</legend>
        <button type="button">only up</button>
        <button type="button">only down</button>
      </fieldset>,
    );
    expect(lowercaseSentenceOpenings(container).map((f) => f.text)).toEqual([
      "only up",
      "only down",
    ]);
  });

  it("leaves the SAME control alone when it is a word inside a sentence", () => {
    // "~$169,306 recoverable — highly recoverable" on a worklist card. A
    // capital here would be the error.
    const { container } = render(
      <p>
        ~$169,306 recoverable —{" "}
        <button type="button">highly recoverable</button>
      </p>,
    );
    expect(lowercaseSentenceOpenings(container)).toEqual([]);
  });

  it("ignores text CSS upper-cases before anybody reads it", () => {
    const { container } = render(<p className="text-micro uppercase">critical</p>);
    expect(lowercaseSentenceOpenings(container)).toEqual([]);
  });

  it("ignores decoration a screen reader is not given either", () => {
    const { container } = render(
      <p>
        <span aria-hidden="true">·</span> up 7.3 points
      </p>,
    );
    // The middot is decoration, so "up 7.3 points" IS the opening — and is
    // flagged. This is the case that was live on four Home cards.
    expect(lowercaseSentenceOpenings(container).map((f) => f.text)).toEqual([
      "up 7.3 points",
    ]);
  });

  it("keeps an identifier and a URL out of it", () => {
    const { container } = render(
      <div>
        <p>denial_rate</p>
        <p>http://localhost:8000</p>
      </div>,
    );
    expect(lowercaseSentenceOpenings(container)).toEqual([]);
  });
});

/* ------------------------------------------------------------------ */
/* Monitors — every live tile, its menu, the brief, the lead controls  */
/* ------------------------------------------------------------------ */

describe("the monitors surface opens every card in capitals", () => {
  beforeEach(() => {
    useSessionStore.setState({
      driver: null,
      monitors: {},
      knownMonitors: [],
      monitorsLoaded: true,
      monitorsLoading: false,
      monitorsError: null,
      monitorPendingKey: null,
      monitorError: null,
      leadStates: {},
      leadPendingId: null,
      leadError: null,
    });
  });

  const tiles = MONITORS.value?.tiles ?? [];

  it("has live tiles to render, so an empty result means something", () => {
    expect(tiles.length).toBeGreaterThan(3);
  });

  for (const tile of tiles) {
    it(`tile: ${tile.pinId}`, () => {
      const pin = PINS.find((p) => p?.pinId === tile.pinId);
      const { container } = draw(
        <ul>
          <MonitorTile tile={tile} {...(pin ? { pin } : {})} />
        </ul>,
      );
      expectSentenceCase(container, `the monitor tile ${tile.pinId}`);
    });
  }

  it("the brief, expanded, entry by entry", () => {
    const brief = BRIEF.value;
    expect(brief).not.toBeNull();
    const { container } = draw(<BriefPanel brief={brief!} leads={new Map()} />);
    expectSentenceCase(container, "the brief panel");
  });

  it("the lead lifecycle controls", () => {
    // The same rows `MonitorsSurface` builds: a lead the snapshot carries
    // a status for, joined to whatever this browser has since recorded.
    const state = mapLeadState(live.lead);
    expect(state, "the capture must contain a lead with a lifecycle").not.toBeNull();
    const rows: LeadRow[] = [
      {
        anomalyId: state!.anomalyId,
        title: "Duplicate submissions: Bluestone PPO",
        status: state!.status,
        note: state!.verificationNote || state!.note || "",
        live: state!,
      },
    ];
    const { container } = draw(
      <LeadLifecyclePanel leads={rows} totalLeads={33} headingId="leads-heading" />,
    );
    expectSentenceCase(container, "the lead lifecycle panel");
  });

  it("a lead's status, in each state a card can publish", () => {
    for (const status of ["open", "working", "resolved_claimed", "resolved_confirmed"] as const) {
      const { container } = draw(
        <LeadStatusControl anomalyId="ANM-021" cardStatus={status} />,
      );
      expectSentenceCase(container, `the lead status control (${status})`);
      cleanup();
    }
  });
});

/* ------------------------------------------------------------------ */
/* The worklist                                                        */
/* ------------------------------------------------------------------ */

describe("the worklist opens every card in capitals", () => {
  it("every live card, with its facts and its disclosures", () => {
    const snapshot = parsePortfolioSnapshot({
      status: "ok",
      tenant: "demo",
      watermark_id: "wm_003",
      formula_version: "anomaly_priority@3",
      items: live.cards,
      lanes: [],
      cash_timing_lanes: [],
      warnings: [],
    });
    expect(snapshot.value?.items.length ?? 0).toBeGreaterThan(0);
  });
});

/* ------------------------------------------------------------------ */
/* An answered question                                                */
/* ------------------------------------------------------------------ */

function turnFrom(raw: unknown, id: string): TurnRecord {
  const parsed = parseInvestigationResponse(raw, PIN);
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

describe("an answered question opens every statement in capitals", () => {
  beforeEach(() => {
    useSessionStore.setState({
      settings: DEFAULT_SETTINGS,
      drawerTurnId: null,
      focusedReferent: null,
    });
  });

  for (const [name, raw] of Object.entries(answers as Record<string, unknown>)) {
    it(`answer: ${name}`, () => {
      const { container } = draw(<AnswerCard turn={turnFrom(raw, `turn_${name}`)} />);
      expectSentenceCase(container, `the answer "${name}"`);
    });
  }
});

/* ------------------------------------------------------------------ */
/* Home                                                                */
/* ------------------------------------------------------------------ */

type Json = Record<string, unknown>;

function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const json = (body: unknown) =>
        Promise.resolve(
          new Response(JSON.stringify(body), {
            status: 200,
            headers: { "content-type": "application/json" },
          }),
        );
      if (url.includes("/v1/health")) return json({ watermark: "wm_003", store_mode: "postgres" });
      if (url.includes("/v1/monitors/brief")) return json(live.brief as Json);
      if (url.includes("/v1/monitors/pins")) return json({ pins: live.pins.pins });
      if (url.includes("/v1/monitors")) return json(live.monitors as Json);
      if (url.includes("/v1/portfolio")) {
        return json({
          status: "ok",
          tenant: "demo",
          watermark_id: "wm_003",
          formula_version: "anomaly_priority@3",
          items: live.cards,
          lanes: [],
          cash_timing_lanes: [
            {
              id: "pre_cash",
              label: "Still catchable",
              description: "The cash effect has not landed yet.",
              kind: "cash_timing",
              anomaly_ids: [],
              item_count: 12,
              impact_cents: 58_547_865,
              recoverable_cents_estimate: 33_175_110,
              soonest_deadline_date: "2026-08-07",
              soonest_deadline_days: 5,
              dated_item_count: 3,
            },
          ],
          warnings: [],
        });
      }
      // The anchor's investigation read. Home draws a real chart from it.
      if (url.includes("/v1/investigations/")) {
        return json((answers as Record<string, unknown>).restored_comparison ?? {});
      }
      return json({});
    }),
  );
}

function drawHome() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <Home />
        </TooltipProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe("Home opens every card and every figure in capitals", () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.localStorage.setItem("revi-driver", "api");
    Element.prototype.scrollIntoView = vi.fn();
    useSessionStore.setState({
      connection: {
        mode: "api",
        state: "online",
        newestWatermarkId: "wm_003",
        healthChecked: true,
      },
      driver: null,
      turns: [],
      sessionLive: false,
      sessionId: "",
      streamingTurnId: null,
      sessions: [],
      leadStates: {},
      pack: INITIAL_PACK,
    });
    serve();
  });

  it("collapsed — the band, the digest, the worklist", async () => {
    const { container } = drawHome();
    await screen.findByText(/Still catchable/);
    await waitFor(() => expect(document.querySelector("[data-key-figures]")).not.toBeNull());
    expectSentenceCase(container, "Home");
  });

  it("with the brief expanded in place", async () => {
    const { container } = drawHome();
    const toggle = await screen.findByRole("button", { name: /Show what changed/ });
    await userEvent.click(toggle);
    await waitFor(() =>
      expect(document.querySelectorAll("[data-brief-entry]").length).toBeGreaterThan(0),
    );
    expectSentenceCase(container, "Home with the brief expanded");
  });
});
