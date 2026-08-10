/**
 * ROUNDS, AS A ROUTE — what it says while it is working, and what it does
 * to somebody it moved here without asking.
 *
 * This surface has two long waits (it re-runs every watch and verifies
 * every claimed fix on request) and it is the one place in the product the
 * app navigates on the analyst's behalf. Both were silent: a grep for
 * `aria-live|role="status"` across the whole `components/rounds` directory
 * returned exactly one hit, on an advisory that was inactive, while the
 * answer path announces every pipeline transition over a 26-60s wait.
 *
 * Nothing here is mocked except the two things a jsdom render cannot have:
 * the app router, and the network.
 */

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RoundsSurface } from "@/components/rounds/RoundsSurface";
import { TooltipProvider } from "@/components/ui/tooltip";
import live from "@/lib/__fixtures__/live-rounds.json";
import { noteRoundsRedirect } from "@/lib/roundsVisit";
import { useSessionStore } from "@/lib/store";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => "/rounds",
}));

function draw() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <RoundsSurface />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
  // The live driver, with a load to walk — the only state in which this
  // surface renders its zones at all.
  window.localStorage.setItem("revi-driver", "api");
  useSessionStore.setState({
    connection: {
      ...useSessionStore.getState().connection,
      mode: "api",
      state: "online",
      newestWatermarkId: "wm_003",
      healthChecked: true,
    },
  });
  // Every read hangs: this is the state the surface is being tested in —
  // the first open of a load, before anything has come back.
  vi.stubGlobal("fetch", vi.fn(() => new Promise(() => {})));
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("Rounds speaks while it works", () => {
  it("makes both long waits live regions, in the words that name the work", () => {
    draw();
    const statuses = screen
      .getAllByRole("status")
      .map((el) => ({ text: el.textContent ?? "", live: el.getAttribute("aria-live") }));

    const walking = statuses.find((s) => s.text.includes("Walking your Rounds at this load"));
    expect(walking, "the brief's pending state must be announced").toBeDefined();
    expect(walking?.live).toBe("polite");

    const watches = statuses.find((s) => s.text.includes("Re-running your watches"));
    expect(watches, "the watch zone's pending state must be announced").toBeDefined();
    expect(watches?.live).toBe("polite");
  });

  it("gives the page's own heading somewhere for focus to land", () => {
    draw();
    const heading = screen.getByRole("heading", { level: 1, name: "Rounds" });
    expect(heading).toHaveAttribute("tabindex", "-1");
  });

  it("moves focus to the heading when the cold start sent the analyst here", async () => {
    // The redirect is real and it latches once; what it did not do was
    // tell anybody it had happened. A screen-reader user's focus stayed on
    // a composer that is no longer mounted.
    noteRoundsRedirect();
    draw();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("heading", { level: 1, name: "Rounds" }),
      ),
    );
    // And it says so, once, in the app's own polite region.
    const announcer = document.getElementById("revi-live-announcer");
    await waitFor(() => expect(announcer?.textContent).toContain("Opened Rounds"));
  });

  it("answers the redirect only once — a bookmark is not a redirect", () => {
    noteRoundsRedirect();
    const first = draw();
    first.unmount();
    document.getElementById("revi-live-announcer")?.remove();
    draw();
    expect(document.activeElement).not.toBe(
      screen.getByRole("heading", { level: 1, name: "Rounds" }),
    );
  });

  it("offers a way past the brief for a keyboard reader", () => {
    // Twenty watches is three screens below a brief somebody may have
    // already read.
    draw();
    expect(screen.getByRole("link", { name: /Skip to your watches/ })).toBeInTheDocument();
  });
});

/**
 * THE WHOLE PATH, against the API driver: does a tile actually GET the
 * watch it was drawn from?
 *
 * The round-8 defect had two live faces behind one symptom — the pins read
 * 500ing, and the read returning 200 with thirty pins while `pinsById`
 * stayed empty — and both ended at the same disabled button. The test the
 * reviewer asked for by name is this one: mount the surface against the
 * driver the product runs, and assert the tiles and the pins meet.
 *
 * Everything here is the captured live payload; only the network is
 * replaced.
 */
describe("Rounds reaches the watches its tiles are drawn from", () => {
  // The single-flight latch is store state, and the tests above leave a
  // pins read in flight forever (their network never answers). A fresh
  // surface is a fresh read.
  beforeEach(() => {
    useSessionStore.setState({
      knownWatches: [],
      watchesLoaded: false,
      watchesLoading: false,
      watchesError: null,
    });
  });

  function serve(pins: unknown, status = 200) {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        const json = (body: unknown, code = 200) =>
          Promise.resolve(
            new Response(JSON.stringify(body), {
              status: code,
              headers: { "content-type": "application/json" },
            }),
          );
        if (url.includes("/v1/health")) return json({ watermark: "wm_003", store_mode: "postgres" });
        if (url.includes("/v1/rounds/pins")) return json(pins, status);
        if (url.includes("/v1/rounds/brief")) return json(live.brief);
        if (url.includes("/v1/rounds")) return json(live.rounds);
        if (url.includes("/v1/portfolio")) return json({ ...live.cards });
        return json({}, 404);
      }),
    );
  }

  it("hands every tile the watch it was drawn from", async () => {
    serve(live.pins);
    draw();
    // The tiles land…
    await waitFor(() =>
      expect(document.querySelectorAll("[data-tile-pin]").length).toBe(live.rounds.tiles.length),
    );
    // …and so do the pins behind them: every tile's settings menu offers
    // the editor rather than explaining an absence.
    await waitFor(() =>
      expect(useSessionStore.getState().knownWatches.length).toBe(live.pins.pins.length),
    );
    const pinned = new Set(useSessionStore.getState().knownWatches.map((p) => p.pinId));
    for (const tile of document.querySelectorAll("[data-tile-pin]")) {
      expect(pinned.has(tile.getAttribute("data-tile-pin") ?? "")).toBe(true);
    }
    expect(useSessionStore.getState().watchesError).toBeNull();
  });

  it("says so on the page when that read 500s", async () => {
    serve({ detail: "watch unit 'days' is not a RoundsWatchUnit" }, 500);
    draw();
    await waitFor(() => expect(useSessionStore.getState().watchesError).not.toBeNull());
    // The tiles are still drawn — the two reads are independent, and a
    // failed settings read must not blank the surface.
    await waitFor(() =>
      expect(document.querySelectorAll("[data-tile-pin]").length).toBe(live.rounds.tiles.length),
    );
  });
});
