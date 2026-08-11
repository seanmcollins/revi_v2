/**
 * HOME — the demo's opening shot, against live-captured payloads.
 *
 * Five things are pinned here, and every one of them was a decision rather
 * than a layout:
 *
 *   WHAT CHANGED is the brief's own headline, one line, expandable in place
 *     to the brief itself — not a summary of it. A quiet load renders the
 *     proud sentence and offers nothing to expand.
 *   THE EVOLUTION RULE actually re-orders the page: zero monitors →
 *     invitation under the anomalies; a monitor that moved → digest above
 *     them; monitors that held still → digest below. `homeLayout.test.ts`
 *     asserts the arithmetic; this asserts the DOM order it produces.
 *   THE COMPOSER GOES SOMEWHERE. Home renders no thread, so a question
 *     asked here has to arrive at `/s/{id}` or it has not been asked.
 *   THE COLD START ANNOUNCES ITSELF instead of redirecting. A load nobody
 *     has been briefed on says its headline in the app's polite region and
 *     moves focus to the zone carrying it — the a11y half of the retired
 *     `/` → `/monitors` push, kept without the navigation.
 *   THE HONESTY MARKS TRAVEL. A bounded tile is still a ceiling in the
 *     digest, in words, beside the `≤`.
 *
 * Nothing is mocked except the two things a jsdom render cannot have: the
 * network, and the deployment wiring that owns a real health heartbeat.
 */

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";

import { TooltipProvider } from "@/components/ui/tooltip";
import live from "@/lib/__fixtures__/live-monitors.json";
import type { TurnDriver, TurnSubmission } from "@/lib/driver";
import { INITIAL_PACK, useSessionStore } from "@/lib/store";
import type { TurnEvent } from "@/lib/types";

// Home's own driver + health heartbeat. The store's driver is installed
// directly below so a submission can be watched end to end without a
// fake SSE stream.
vi.mock("@/lib/useDeployment", () => ({
  useDeployment: () => ({ driverKind: "api" as const, driver: null, live: true }),
}));

import { Home } from "@/components/home/Home";

/* ------------------------------------------------------------------ */
/* Payloads                                                            */
/* ------------------------------------------------------------------ */

type Json = Record<string, unknown>;

const BRIEF = live.brief as unknown as Json;
const MONITORS = live.monitors as unknown as Json;
const TILES = MONITORS.tiles as Json[];

/**
 * The brief's own headline, read out of the capture rather than
 * transcribed into the assertions.
 *
 * It used to be spelled out here as "4 thing(s) changed between wm_002 and
 * wm_003" — which is what the engine said before M43, parenthetical plural
 * and warehouse ids and all. Pinning that text meant the tests would have
 * gone on passing over copy the language contract now bans, and would have
 * failed the moment it was fixed. The headline is the SERVER's sentence;
 * what Home owes is to print it once, in the right zone, and to announce
 * it — which is what these assert.
 */
const HEADLINE = BRIEF.headline as string;

/**
 * How many monitors this capture says actually moved enough to brief —
 * counted the way `movedPinIds` counts them, from BOTH published sources:
 * a tile in the material band, or a brief entry of a movement kind. A tile
 * can be material with no brief line and a brief line can name a pin whose
 * tile did not band material, and the digest's own census is the union.
 */
const MOVED = new Set([
  ...TILES.filter((t) => (t.delta as Json | null)?.material === true).map((t) => t.pin_id),
  ...(BRIEF.entries as Json[])
    .filter((e) => e.kind === "pin_movement" || e.kind === "rank_flip")
    .map((e) => e.pin_id)
    .filter((id): id is string => typeof id === "string"),
]).size;

/** The captured worklist, wrapped in the snapshot envelope the route sends. */
const PORTFOLIO: Json = {
  status: "ok",
  tenant: "demo",
  watermark_id: "wm_003",
  formula_version: "anomaly_priority@3",
  items: live.cards,
  lanes: [
    {
      id: "compliance",
      label: "Must do regardless of size",
      description: "Work the rule requires whatever it is worth.",
      kind: "governance",
      anomaly_ids: [(live.cards as Json[])[0].anomaly_id],
      item_count: 1,
      impact_cents: 17_821_682,
    },
    {
      id: "value",
      label: "Ranked by value recoverable",
      description: "Ordered by what is left to save.",
      kind: "governance",
      anomaly_ids: (live.cards as Json[]).slice(1).map((c) => c.anomaly_id),
      item_count: 3,
      impact_cents: 40_000_000,
    },
  ],
  cash_timing_lanes: [
    {
      id: "pre_cash",
      label: "Still catchable",
      description: "The cash effect has not landed yet.",
      kind: "cash_timing",
      anomaly_ids: [(live.cards as Json[])[0].anomaly_id],
      item_count: 1,
      impact_cents: 17_821_682,
      recoverable_cents_estimate: 16_930_598,
      soonest_deadline_date: "2026-08-19",
      soonest_deadline_days: 9,
      dated_item_count: 1,
    },
  ],
  warnings: [],
};

/** A brief with nothing in it — the proud, quiet morning. */
const QUIET_BRIEF: Json = {
  ...BRIEF,
  status: "nothing_material",
  headline: "Nothing crossed the threshold since the Aug 1 load.",
  entries: [],
  entries_total: 0,
};

/** Monitors that all held still: no material delta, and no brief movement. */
const STILL_MONITORS: Json = {
  ...MONITORS,
  tiles: TILES.map((tile) => ({
    ...tile,
    delta: tile.delta ? { ...(tile.delta as Json), material: false } : null,
  })),
};

const NO_MONITORS: Json = { ...MONITORS, tiles: [] };

/** A brief carrying no `pin_movement` / `rank_flip` line. */
const NO_MOVEMENT_BRIEF: Json = {
  ...BRIEF,
  entries: (BRIEF.entries as Json[]).filter(
    (e) => e.kind !== "pin_movement" && e.kind !== "rank_flip",
  ),
};

/** One monitor whose headline value is an upper bound, not a measurement. */
const BOUNDED_MONITORS: Json = {
  ...MONITORS,
  // Applied to a monitor the digest will actually SHOW. The digest caps at
  // four and puts the ones that moved first, so pinning this to a fixed
  // index made the test depend on how many monitors happened to move in
  // the capture — it stopped exercising anything the moment a fifth one
  // did.
  tiles: TILES.map((tile) =>
    (tile.delta as Json | null)?.material === true
      ? {
          ...tile,
          label: "monthly denial rate for Veritas Comp Fund",
          value_text: "≤ 76.9%",
          integrity: { ...(tile.integrity as Json), is_bound: true, provisional: true },
        }
      : tile,
  ),
};

/* ------------------------------------------------------------------ */
/* Harness                                                             */
/* ------------------------------------------------------------------ */

interface Served {
  brief?: Json;
  monitors?: Json;
  portfolio?: Json;
}

function serve({ brief = BRIEF, monitors = MONITORS, portfolio = PORTFOLIO }: Served = {}) {
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
      if (url.includes("/v1/monitors/brief")) return json(brief);
      if (url.includes("/v1/monitors/pins")) return json({ pins: live.pins.pins });
      if (url.includes("/v1/monitors")) return json(monitors);
      if (url.includes("/v1/portfolio")) return json(portfolio);
      return json({});
    }),
  );
}

/** Where a submitted turn is supposed to end up. */
function SessionProbe() {
  const { sessionId } = useParams<{ sessionId: string }>();
  return <div data-testid="session-route">{sessionId}</div>;
}

function draw() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/s/:sessionId" element={<SessionProbe />} />
          </Routes>
        </TooltipProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/**
 * A driver that mints a session the way the live one does — inside the
 * submission, before the answer — because that instant is exactly what
 * `useAsk` follows.
 */
function fakeDriver(): TurnDriver & { asked: TurnSubmission[] } {
  const asked: TurnSubmission[] = [];
  return {
    asked,
    async submit(submission: TurnSubmission, emit: (event: TurnEvent) => void) {
      asked.push(submission);
      useSessionStore.getState().adoptSession({
        sessionId: "sess_home_1",
        watermark: useSessionStore.getState().watermark,
        pack: INITIAL_PACK,
      });
      emit({
        type: "turn_complete",
        status: "complete",
        investigationId: "inv_1",
      } as TurnEvent);
    },
    async newSession() {},
  };
}

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
});

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  window.localStorage.setItem("revi-driver", "api");
  Element.prototype.scrollIntoView = vi.fn();
  useSessionStore.setState({
    connection: {
      mode: "api",
      state: "online",
      newestWatermarkId: "wm_003",
      healthChecked: true,
    },
    driver: fakeDriver(),
    turns: [],
    sessionLive: false,
    sessionId: "",
    streamingTurnId: null,
    sessions: [],
    leadStates: {},
  });
  serve();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.getElementById("revi-live-announcer")?.remove();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

/** Which of the two evolving zones comes first in the document. */
function firstZone(): "monitors" | "anomalies" {
  const monitors = document.getElementById("home-monitors");
  const anomalies = document.getElementById("home-anomalies");
  expect(monitors, "the monitors zone must be on the page").not.toBeNull();
  expect(anomalies, "the anomalies zone must be on the page").not.toBeNull();
  const relation = monitors!.compareDocumentPosition(anomalies!);
  return relation & Node.DOCUMENT_POSITION_FOLLOWING ? "monitors" : "anomalies";
}

/* ------------------------------------------------------------------ */

describe("Home — what changed, first and in one line", () => {
  it("renders the brief's own headline, collapsed, with a way into the detail", async () => {
    draw();
    await screen.findByText(HEADLINE);
    // Collapsed: the sentence and nothing else. The brief's entries are
    // behind the toggle, which says how many there are. (Asserted on the
    // entry rows rather than on a title, because two of these leads are
    // also cards in the anomalies zone below — which is the point of
    // having both zones.)
    expect(document.querySelectorAll("[data-brief-entry]")).toHaveLength(0);
    expect(
      screen.getByRole("button", {
        name: new RegExp(`Show what changed — ${(BRIEF.entries as Json[]).length} lines`),
      }),
    ).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("expands IN PLACE to the brief itself — the same panel Monitors renders", async () => {
    draw();
    const toggle = await screen.findByRole("button", { name: /Show what changed/ });
    await userEvent.click(toggle);

    // The real entries, with the real component behind them: the eyebrow
    // kinds and the walk census are `BriefPanel`'s, not a second rendering
    // of the same payload.
    await waitFor(() =>
      expect(document.querySelectorAll("[data-brief-entry]")).toHaveLength(
        (BRIEF.entries as Json[]).length,
      ),
    );
    expect(screen.getByText(/New at this load: ANM-029/)).toBeInTheDocument();
    expect(screen.getByText(/Gone without being worked: ANM-032/)).toBeInTheDocument();
    expect(document.querySelector("[data-walk-census]")).not.toBeNull();
    expect(screen.getByRole("button", { name: /Hide what changed/ })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    // One headline on screen, not two: the collapsed line steps aside for
    // the panel's own lead. (Scoped to the zone — the polite live region
    // carries the same sentence, which is the point of it.)
    const zone = document.getElementById("home-what-changed")!;
    expect(within(zone).getAllByText(HEADLINE)).toHaveLength(1);
  });

  it("gives a quiet load the proud sentence, and nothing to expand", async () => {
    serve({ brief: QUIET_BRIEF });
    draw();
    expect(await screen.findByText("Nothing material changed.")).toBeInTheDocument();
    expect(
      screen.getByText("Nothing crossed the threshold since the Aug 1 load."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Show what changed/ })).not.toBeInTheDocument();
    // And it still publishes the work behind the claim — a quiet brief that
    // could not say how much it walked is indistinguishable from one that
    // did not run.
    expect(document.querySelector("[data-walk-census]")).not.toBeNull();
  });
});

describe("Home — the evolution rule, on the page", () => {
  it("MONITORS THAT MOVED: the digest is above the anomalies", async () => {
    draw();
    // Scoped to the digest: the drawn anchor below names the same monitor,
    // which is the point of it — one object, two places, one subject.
    await waitFor(() =>
      expect(
        within(document.getElementById("home-monitors")!).getByText(
          "Denial rate by payer, monthly",
        ),
      ).toBeInTheDocument(),
    );
    await waitFor(() => expect(firstZone()).toBe("monitors"));
    expect(
      screen.getByText(
        new RegExp(`${TILES.length} monitors re-run at this load, ${MOVED} moved enough to brief you`),
      ),
    ).toBeInTheDocument();
  });

  it("MONITORS, NOTHING MOVED: the digest stays below the anomalies", async () => {
    serve({ monitors: STILL_MONITORS, brief: NO_MOVEMENT_BRIEF });
    draw();
    // Waited on the CENSUS rather than on a particular monitor's name: the
    // digest shows four of nine and which four is the digest's own
    // ordering rule, not this test's subject. What is under test is that
    // the zone still renders, still says what it walked, and sits BELOW
    // the anomalies when nothing moved.
    const census = await screen.findByText(
      new RegExp(`${TILES.length} monitors re-run at this load, none moved enough to brief you`),
    );
    expect(document.getElementById("home-monitors")!.contains(census)).toBe(true);
    expect(
      within(document.getElementById("home-monitors")!).getAllByRole("listitem").length,
    ).toBeGreaterThan(0);
    await waitFor(() => expect(firstZone()).toBe("anomalies"));
  });

  it("NO MONITORS: an invitation, under the anomalies, naming a real affordance", async () => {
    serve({ monitors: NO_MONITORS });
    draw();
    const invitation = await waitFor(() => {
      const node = document.querySelector("[data-monitor-invitation]");
      expect(node).not.toBeNull();
      return node!;
    });
    expect(invitation.textContent).toContain("Nothing is being monitored yet");
    expect(invitation.textContent).toContain("Monitor this");
    expect(invitation.textContent).toContain("watch Halvern");
    expect(firstZone()).toBe("anomalies");
  });

  it("keeps the honesty marks on a bounded value in the digest", async () => {
    serve({ monitors: BOUNDED_MONITORS });
    draw();
    const [value] = await screen.findAllByText(/≤ 76.9%/);
    // The `≤` is the server's, and the words beside it say what it means.
    expect(value.textContent).toContain("a ceiling, not a measurement");
    expect(value.textContent).toContain("still settling");
  });
});

describe("Home — the anomalies zone", () => {
  it("renders the lanes the server split, and the dollars still catchable", async () => {
    draw();
    expect(await screen.findByText("Must do regardless of size")).toBeInTheDocument();
    expect(screen.getByText("Ranked by value recoverable")).toBeInTheDocument();
    // The still-catchable total is the BAND now, not three lines of 12px
    // type: same lane, same server figure, at a size somebody reads. The
    // rail keeps the sentence form (`CashTimingSummary`).
    const band = await waitFor(() => {
      const node = document.querySelector("[data-key-figures]");
      expect(node).not.toBeNull();
      return node!;
    });
    expect(within(band as HTMLElement).getByText("Still catchable")).toBeInTheDocument();
    expect(band.textContent).toContain("~$169,306");
  });

  /**
   * THE BAND IS COMPUTED, NOT WRITTEN.
   *
   * Every cell comes off the snapshot's own `cash_timing_lanes` and its own
   * cards — the fixture publishes one lane with a $169,306 recoverable
   * estimate over 1 lead and a dated limit 9 days out, and that is exactly
   * what has to be on screen. A band that renders the right shape from the
   * wrong payload is the failure this pins.
   */
  it("computes every figure in the band from the payload, marks and all", async () => {
    draw();
    const band = await waitFor(() => {
      const node = document.querySelector("[data-key-figures]");
      expect(node).not.toBeNull();
      return node!;
    });
    const cell = (label: string): HTMLElement => {
      const node = band.querySelector<HTMLElement>(`[data-key-figure="${label}"]`);
      expect(node, `the band must carry a "${label}" figure`).not.toBeNull();
      return node!;
    };
    // A RECOVERABLE ESTIMATE KEEPS ITS MARK AT DISPLAY SIZE. `~` is this
    // product's existing mark for a governed estimate and the word says
    // what kind of number it is; 30px is exactly where dropping either
    // would matter most.
    const catchable = cell("Still catchable");
    expect(catchable.textContent).toContain("~$169,306");
    expect(catchable.textContent).toContain("estimated");
    expect(catchable.textContent).toContain("Recoverable, across 1 lead");
    
    // The count reconciles to the list: 4 cards, none of them worked.
    expect(cell("Open leads").textContent).toContain("4");
    // A REAL DATE OR NOTHING. The server's own arithmetic, not today's
    // clock — 9 days, from `soonest_deadline_days`.
    const deadline = cell("Soonest deadline");
    expect(deadline.textContent).toContain("In 9 days");
    expect(deadline.textContent).toContain("Aug 19, 2026");
    // Amber is a verdict colour. A dashboard figure is not a verdict.
    expect(band.querySelector("[class*='text-warning']")).toBeNull();
  });

  /**
   * A LANE THE SERVER DID NOT PUBLISH DRAWS NO CELL. The band is allowed
   * to be short; it is not allowed to hold a blank box, which is the exact
   * "is this broken?" impression it exists to remove.
   */
  it("draws no cell for a lane the snapshot does not carry", async () => {
    serve({ portfolio: { ...PORTFOLIO, cash_timing_lanes: [] } });
    draw();
    await screen.findByText("Must do regardless of size");
    const band = document.querySelector("[data-key-figures]");
    expect(band).not.toBeNull();
    expect(band!.querySelector('[data-key-figure="Still catchable"]')).toBeNull();
    // The count survives — it is computed from the cards, not the lanes.
    expect(band!.querySelector('[data-key-figure="Open leads"]')).not.toBeNull();
  });

  it("offers a drill on a card the server published a handle for", async () => {
    draw();
    expect(
      await screen.findByLabelText(/Drill into DNFB accumulation/),
    ).toBeInTheDocument();
  });

  /**
   * NO PRIMARY ACTION ON HOME IS HOVER-REVEALED.
   *
   * Opening a card is what this page is for — for a tenant who has pinned
   * nothing it is the only thing on it — and the rail's card shipped its
   * "Drill in" control as `opacity-0 group-hover:opacity-100`. An
   * affordance that does not exist until a mouse crosses it does not exist
   * on a touch screen, in a screenshot, on a projector, or for anybody
   * deciding whether the card is worth touching. The same rule already
   * took the hover off the monitor tile's settings control, the lead
   * status control and "Monitor this"; this is the last of them, on the
   * surface where it costs the most.
   *
   * The rail keeps the tighter treatment on purpose — see
   * `DrillAffordance` — so this asserts Home's own copy, by the attribute
   * that records which treatment was chosen rather than by a colour.
   */
  it("draws the drill control without a hover — it is Home's primary action", async () => {
    draw();
    const drill = await screen.findByLabelText(/Drill into DNFB accumulation/);
    expect(drill).toHaveAttribute("data-drill-affordance", "persistent");
    expect(drill.className).not.toMatch(/\bopacity-0\b/);
    expect(drill.className).not.toMatch(/group-hover:/);
  });

  it("leaves no hover-revealed control in Home's main column at all", async () => {
    draw();
    await screen.findByLabelText(/Drill into DNFB accumulation/);
    // The rail is a different surface with a different rule; the main
    // column is the page, and nothing on it may be revealed by a pointer.
    const main = document.querySelector("main");
    expect(main).not.toBeNull();
    const hidden = Array.from(
      main!.querySelectorAll<HTMLElement>("button, a[href]"),
    ).filter((el) => /\bopacity-0\b/.test(el.className));
    expect(hidden.map((el) => el.getAttribute("aria-label") ?? el.textContent)).toEqual([]);
  });
});

/* ------------------------------------------------------------------ */
/* The readings a monitor has stored, drawn                            */
/* ------------------------------------------------------------------ */

/**
 * A monitor whose PRIOR reading was a ceiling. Only the historical
 * reading's own rendered text says so — that is the only per-reading
 * evidence on the wire — and the point drawn for it must be hollow, with
 * the segments touching it dashed, exactly as the big line charts treat a
 * mark the engine did not measure.
 */
const BOUNDED_HISTORY: Json = {
  ...MONITORS,
  tiles: TILES.map((tile, i) =>
    i === 0
      ? {
          ...tile,
          delta: {
            ...(tile.delta as Json),
            prior_value_text: "≤ 25.9%",
          },
        }
      : tile,
  ),
};

describe("Home — a monitor's stored readings, and the two dots that are not a trend", () => {
  const sparkline = (): SVGElement | null =>
    document.querySelector<SVGElement>("[data-sparkline-points]");

  it("draws the readings the payload carries, one point per stored load", async () => {
    draw();
    const line = await waitFor(() => {
      const node = sparkline();
      expect(node).not.toBeNull();
      return node!;
    });
    // Three: the creation baseline (wm_001), the prior load (wm_002) and
    // this one. Nothing between them and nothing beyond them.
    expect(line.getAttribute("data-sparkline-points")).toBe("3");
    expect(line.getAttribute("role")).toBe("img");
    // NOT DECORATION FOR A SCREEN READER EITHER: the baseline reading
    // appears nowhere else on the tile, so the figure names them all.
    expect(line.getAttribute("aria-label")).toContain("24.3%");
    expect(line.getAttribute("aria-label")).toContain("29.5%");
  });

  it("says in words what it draws as a hollow point — the current reading is still settling", async () => {
    draw();
    const line = await waitFor(() => {
      const node = sparkline();
      expect(node).not.toBeNull();
      return node!;
    });
    // The live tile's own `integrity.provisional`. One qualified reading,
    // and the words travel with it.
    expect(line.getAttribute("data-sparkline-qualified")).toBe("1");
    expect(line.getAttribute("aria-label")).toContain("still settling");
    // A qualified point is a hollow one — filled with the card, not the
    // series colour — and its segment is dashed.
    expect(line.querySelector('circle[fill="var(--card)"]')).not.toBeNull();
    expect(line.querySelector("line[stroke-dasharray]")).not.toBeNull();
  });

  it("carries a CEILING from a stored reading, not just from the newest one", async () => {
    serve({ monitors: BOUNDED_HISTORY });
    draw();
    const line = await waitFor(() => {
      const node = sparkline();
      expect(node).not.toBeNull();
      return node!;
    });
    // Two qualified now: the bounded prior reading and the provisional
    // current one.
    expect(line.getAttribute("data-sparkline-qualified")).toBe("2");
    expect(line.getAttribute("aria-label")).toContain("a ceiling, not a measurement");
  });

  it("DRAWS NOTHING at two readings — a line through two dots is a trend nobody measured", async () => {
    // Every tile reduced to one stored reading: no baseline, no prior.
    serve({
      monitors: {
        ...MONITORS,
        tiles: TILES.map((tile) => ({ ...tile, delta: null, baseline_delta: null })),
      },
    });
    draw();
    await waitFor(() =>
      expect(
        within(document.getElementById("home-monitors")!).getByText(
          "Denial rate by payer, monthly",
        ),
      ).toBeInTheDocument(),
    );
    expect(sparkline()).toBeNull();
    // …and the tile keeps its current form rather than a gap where a
    // picture would have been: the value is still there, and so is the
    // sentence that says there is nothing to compare it against.
    expect(
      within(document.getElementById("home-monitors")!).getAllByText(
        "Nothing to compare against at this load",
      ).length,
    ).toBeGreaterThan(0);
  });
});

describe("Home — the composer is one keystroke away, and it goes somewhere", () => {
  it("takes focus on arrival, so New chat lands on a cursor", async () => {
    draw();
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("Ask a question")),
    );
  });

  it("offers the four hero questions beside it", async () => {
    draw();
    for (const question of [
      "Where are denials rising, and which payers are driving it?",
      "Why did cash come in low last week?",
      "What should my team work on first today?",
      "Is anything about to miss a filing deadline?",
    ]) {
      expect(await screen.findByRole("button", { name: new RegExp(question) })).toBeInTheDocument();
    }
  });

  it("submitting a hero question opens the session the turn mints", async () => {
    draw();
    const chip = await screen.findByRole("button", {
      name: /What should my team work on first today\?/,
    });
    await userEvent.click(chip);

    const probe = await screen.findByTestId("session-route");
    expect(probe).toHaveTextContent("sess_home_1");
    // Through the store's own submit path — the turn is in the store, in
    // the session that was minted for it.
    const state = useSessionStore.getState();
    expect(state.turns).toHaveLength(1);
    expect(state.turns[0].submission.utterance).toBe(
      "What should my team work on first today?",
    );
  });

  it("submitting typed text opens the session the turn mints", async () => {
    draw();
    const composer = await screen.findByLabelText("Ask a question");
    await userEvent.type(composer, "Do I have a COB problem?{Enter}");

    const probe = await screen.findByTestId("session-route");
    expect(probe).toHaveTextContent("sess_home_1");
    expect(useSessionStore.getState().turns[0].submission.utterance).toBe(
      "Do I have a COB problem?",
    );
  });
});

describe("Home — the cold start announces itself instead of redirecting", () => {
  it("says the headline once and moves focus to the zone carrying it", async () => {
    // Nothing recorded for wm_003: a load this browser has not been
    // briefed on, which is exactly what used to trigger the `/` →
    // `/monitors` push.
    draw();
    const announcer = await waitFor(() => {
      const node = document.getElementById("revi-live-announcer");
      expect(node?.textContent).toContain("What changed at this load");
      return node!;
    });
    expect(announcer.textContent).toContain(HEADLINE);
    expect(announcer.getAttribute("aria-live")).toBe("polite");
    await waitFor(() =>
      expect(document.activeElement).toBe(document.getElementById("home-what-changed")),
    );
  });

  it("records the load as briefed, so the rail's New load dot goes out", async () => {
    draw();
    await waitFor(() =>
      expect(window.localStorage.getItem("revi-monitors-seen-watermark")).toBe("wm_003"),
    );
  });

  it("says nothing, and takes no focus, on a load already read", async () => {
    window.localStorage.setItem("revi-monitors-seen-watermark", "wm_003");
    draw();
    await screen.findByText(HEADLINE);
    expect(document.getElementById("revi-live-announcer")?.textContent ?? "").toBe("");
    // Focus stays where Home puts it by default: on the composer.
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByLabelText("Ask a question")),
    );
  });

  it("navigates nowhere — Home IS the brief-first cold start now", async () => {
    draw();
    await screen.findByText(HEADLINE);
    expect(screen.queryByTestId("session-route")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1, name: "Where things stand" })).toBeInTheDocument();
  });
});

describe("Home — reachable without a mouse", () => {
  it("puts the skip links first in the document, ahead of the rail", () => {
    const { container } = draw();
    const focusable = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (el) => !el.hasAttribute("disabled") && el.getAttribute("tabindex") !== "-1",
    );
    expect(focusable.length).toBeGreaterThan(3);
    expect(focusable[0]).toHaveAccessibleName("Skip to what changed");
    expect(focusable[1]).toHaveAccessibleName("Skip to the composer");
  });

  it("lands each skip link on something that can take focus", () => {
    draw();
    for (const [name, id] of [
      ["Skip to what changed", "home-what-changed"],
      ["Skip to the composer", "turn-composer"],
    ] as const) {
      const link = screen.getByRole("link", { name });
      expect(link).toHaveAttribute("href", `#${id}`);
      expect(document.getElementById(id), `#${id} must exist`).not.toBeNull();
    }
  });

  it("names all three zones for a screen reader", async () => {
    draw();
    await screen.findByText(HEADLINE);
    for (const name of ["What changed", "Detected anomalies", "Your monitors"]) {
      expect(screen.getByRole("region", { name })).toBeInTheDocument();
    }
  });
});

/** Everything the browser will stop on, in document order. */
const FOCUSABLE =
  'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';
