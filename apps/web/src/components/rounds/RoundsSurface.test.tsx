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
