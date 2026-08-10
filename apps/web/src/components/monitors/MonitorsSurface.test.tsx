/**
 * MONITORS, AS A ROUTE — what it says while it is working, and what it does
 * to somebody it moved here without asking.
 *
 * This surface has two long waits (it re-runs every monitor and verifies
 * every claimed fix on request) and it is the one place in the product the
 * app navigates on the analyst's behalf. Both were silent: a grep for
 * `aria-live|role="status"` across the whole `components/monitors` directory
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

import { MonitorsSurface } from "@/components/monitors/MonitorsSurface";
import { TooltipProvider } from "@/components/ui/tooltip";
import live from "@/lib/__fixtures__/live-monitors.json";
import { noteMonitorsRedirect } from "@/lib/monitorsVisit";
import { useSessionStore } from "@/lib/store";

const push = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push, replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => "/monitors",
}));

function draw() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <MonitorsSurface />
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

describe("Monitors speaks while it works", () => {
  it("makes both long waits live regions, in the words that name the work", () => {
    draw();
    const statuses = screen
      .getAllByRole("status")
      .map((el) => ({ text: el.textContent ?? "", live: el.getAttribute("aria-live") }));

    const walking = statuses.find((s) => s.text.includes("Walking your Monitors at this load"));
    expect(walking, "the brief's pending state must be announced").toBeDefined();
    expect(walking?.live).toBe("polite");

    const monitors = statuses.find((s) => s.text.includes("Re-running your monitors"));
    expect(monitors, "the monitor zone's pending state must be announced").toBeDefined();
    expect(monitors?.live).toBe("polite");
  });

  it("gives the page's own heading somewhere for focus to land", () => {
    draw();
    const heading = screen.getByRole("heading", { level: 1, name: "Monitors" });
    expect(heading).toHaveAttribute("tabindex", "-1");
  });

  it("moves focus to the heading when the cold start sent the analyst here", async () => {
    // The redirect is real and it latches once; what it did not do was
    // tell anybody it had happened. A screen-reader user's focus stayed on
    // a composer that is no longer mounted.
    noteMonitorsRedirect();
    draw();
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("heading", { level: 1, name: "Monitors" }),
      ),
    );
    // And it says so, once, in the app's own polite region.
    const announcer = document.getElementById("revi-live-announcer");
    await waitFor(() => expect(announcer?.textContent).toContain("Opened Monitors"));
  });

  it("answers the redirect only once — a bookmark is not a redirect", () => {
    noteMonitorsRedirect();
    const first = draw();
    first.unmount();
    document.getElementById("revi-live-announcer")?.remove();
    draw();
    expect(document.activeElement).not.toBe(
      screen.getByRole("heading", { level: 1, name: "Monitors" }),
    );
  });

  it("offers a way past the brief for a keyboard reader", () => {
    // Twenty monitors is three screens below a brief somebody may have
    // already read.
    draw();
    expect(screen.getByRole("link", { name: /Skip to your monitors/ })).toBeInTheDocument();
  });

  /**
   * A SKIP LINK THAT IS NOT FIRST IS NOT A SKIP LINK.
   *
   * Measured on the live surface: "Skip to your monitors" was the 152nd of
   * 224 tab stops, because it sat in the main header and the session rail
   * — fifty rows, two controls each — is earlier in the document. The one
   * control whose entire purpose is to save those stops could only be
   * reached by taking them.
   *
   * Asserted as `focusable[0]` rather than "is in the document", which is
   * what the previous test asserted and what let this ship.
   */
  it("puts the skip links first in the document, ahead of the rail", () => {
    const { container } = draw();
    const focusable = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
      (el) => !el.hasAttribute("disabled") && el.getAttribute("tabindex") !== "-1",
    );
    expect(focusable.length).toBeGreaterThan(3);
    expect(focusable[0]).toHaveAccessibleName("Skip to this load's brief");
    expect(focusable[1]).toHaveAccessibleName("Skip to your monitors");
  });

  /**
   * NO TWO CONTROLS SHARE ONE NAME.
   *
   * This started as a theme-toggle count: two buttons on this route both
   * announced "Toggle theme" (the rail's and the page header's), so a
   * screen reader read the same control twice with nothing to tell them
   * apart. Light is now the only theme and both toggles are gone, so the
   * assertion is the general rule the specific one was standing in for —
   * every focusable control on the route has a distinct accessible name.
   */
  it("gives every control on the route a distinct accessible name", () => {
    const { container } = draw();
    const named = [...container.querySelectorAll<HTMLElement>(FOCUSABLE)]
      .filter((el) => !el.hasAttribute("disabled") && el.getAttribute("tabindex") !== "-1")
      .map((el) => el.getAttribute("aria-label") ?? el.textContent?.trim() ?? "")
      .filter(Boolean);
    const duplicated = named.filter((name, i) => named.indexOf(name) !== i);
    expect(duplicated).toEqual([]);
  });

  it("lands each skip link on something that can take focus", () => {
    draw();
    for (const [name, id] of [
      ["Skip to this load's brief", "brief-zone"],
      ["Skip to your monitors", "monitors-heading"],
    ] as const) {
      const link = screen.getByRole("link", { name });
      expect(link).toHaveAttribute("href", `#${id}`);
      // The target exists AND is focusable — a fragment that scrolls
      // without moving focus resumes tabbing from the link, i.e. from
      // behind the rail the reader just skipped.
      const target = document.getElementById(id);
      expect(target, `#${id} must exist for "${name}" to land on`).not.toBeNull();
      expect(target).toHaveAttribute("tabindex", "-1");
    }
  });
});

/** Everything the browser will stop on, in document order. */
const FOCUSABLE =
  'a[href], button, input, select, textarea, [tabindex]:not([tabindex="-1"])';

/**
 * THE WHOLE PATH, against the API driver: does a tile actually GET the
 * monitor it was drawn from?
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
describe("Monitors reaches the monitors its tiles are drawn from", () => {
  // The single-flight latch is store state, and the tests above leave a
  // pins read in flight forever (their network never answers). A fresh
  // surface is a fresh read.
  beforeEach(() => {
    useSessionStore.setState({
      knownMonitors: [],
      monitorsLoaded: false,
      monitorsLoading: false,
      monitorsError: null,
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
        if (url.includes("/v1/monitors/pins")) return json(pins, status);
        if (url.includes("/v1/monitors/brief")) return json(live.brief);
        if (url.includes("/v1/monitors")) return json(live.monitors);
        if (url.includes("/v1/portfolio")) return json({ ...live.cards });
        return json({}, 404);
      }),
    );
  }

  it("hands every tile the monitor it was drawn from", async () => {
    serve(live.pins);
    draw();
    // The tiles land…
    await waitFor(() =>
      expect(document.querySelectorAll("[data-tile-pin]").length).toBe(live.monitors.tiles.length),
    );
    // …and so do the pins behind them: every tile's settings menu offers
    // the editor rather than explaining an absence.
    await waitFor(() =>
      expect(useSessionStore.getState().knownMonitors.length).toBe(live.pins.pins.length),
    );
    const pinned = new Set(useSessionStore.getState().knownMonitors.map((p) => p.pinId));
    for (const tile of document.querySelectorAll("[data-tile-pin]")) {
      expect(pinned.has(tile.getAttribute("data-tile-pin") ?? "")).toBe(true);
    }
    expect(useSessionStore.getState().monitorsError).toBeNull();
  });

  it("says so on the page when that read 500s", async () => {
    serve({ detail: "monitor unit 'days' is not a MonitorUnit" }, 500);
    draw();
    await waitFor(() => expect(useSessionStore.getState().monitorsError).not.toBeNull());
    // The tiles are still drawn — the two reads are independent, and a
    // failed settings read must not blank the surface.
    await waitFor(() =>
      expect(document.querySelectorAll("[data-tile-pin]").length).toBe(live.monitors.tiles.length),
    );
  });
});
