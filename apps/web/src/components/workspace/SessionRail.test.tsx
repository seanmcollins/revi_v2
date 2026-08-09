import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionRail } from "@/components/workspace/SessionRail";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { TurnDriver } from "@/lib/driver";
import { MockDriver } from "@/lib/mockDriver";
import { useSessionStore } from "@/lib/store";
import type { SessionListData, SessionSummary } from "@/lib/types";

/**
 * The rail used to render three hard-coded session titles behind three dead
 * buttons. These tests hold the replacement to the two things that made it
 * a lie: every row is a server row, and clicking one actually switches.
 */

function row(overrides: Partial<SessionSummary> = {}): SessionSummary {
  return {
    sessionId: "sess_a",
    title: "Why did cash decline last week?",
    createdAt: "2026-08-08T09:00:00Z",
    lastActivity: "2026-08-08T09:05:00Z",
    turnCount: 2,
    ...overrides,
  };
}

// jsdom implements neither of these; the rail's scroll area observes size
// and its portfolio cards carry tooltips (both provided by the real app
// shell in app/layout.tsx).
beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
});

function renderRail() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <SessionRail />
      </TooltipProvider>
    </QueryClientProvider>,
  );
}

/** A driver that lists sessions and resumes them, with nothing else real. */
function listingDriver(
  page: SessionListData,
  resume?: TurnDriver["resumeSession"],
): TurnDriver {
  return {
    submit: vi.fn().mockResolvedValue(undefined),
    newSession: vi.fn().mockResolvedValue(undefined),
    listSessions: vi.fn().mockResolvedValue(page),
    ...(resume ? { resumeSession: resume } : {}),
  };
}

describe("SessionRail — the session list is the server's, or nothing", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({
      driver: null,
      sessions: [],
      sessionsTotal: 0,
      sessionsState: "idle",
      sessionsError: null,
      switchingSessionId: null,
      switchError: null,
      connection: { mode: "api", state: "online" },
    });
  });

  afterEach(() => cleanup());

  it("renders the server's rows — titles and ages, not fixtures", async () => {
    useSessionStore.getState().setDriver(
      listingDriver({
        sessions: [
          row(),
          row({ sessionId: "sess_b", title: "COB investigation", turnCount: 0 }),
        ],
        total: 2,
      }),
    );

    renderRail();

    expect(await screen.findByText("Why did cash decline last week?")).toBeInTheDocument();
    expect(screen.getByText("COB investigation")).toBeInTheDocument();
    // The retired fixtures must not come back through any other door.
    expect(screen.queryByText(/Denial spike — Meridian Imaging/)).not.toBeInTheDocument();
  });

  it("marks the current session and switches to another one on click", async () => {
    const resumeSession = vi.fn().mockResolvedValue({
      sessionId: "sess_b",
      watermark: { id: "wm_9", loadedAt: "2026-08-08 04:00", newestDataDate: "2026-08-07" },
      pack: { packId: "base-rcm", version: "1.0.0" },
      turns: [],
    });
    useSessionStore
      .getState()
      .setDriver(
        listingDriver(
          { sessions: [row(), row({ sessionId: "sess_b", title: "COB investigation" })], total: 2 },
          resumeSession,
        ),
      );
    useSessionStore.setState({ sessionId: "sess_a" });

    renderRail();
    const current = await screen.findByRole("button", {
      name: /Why did cash decline last week\?/,
    });
    expect(current).toHaveAttribute("aria-current", "true");

    await userEvent.click(screen.getByRole("button", { name: /COB investigation/ }));

    await waitFor(() => expect(resumeSession).toHaveBeenCalledWith("sess_b"));
    expect(useSessionStore.getState().sessionId).toBe("sess_b");
  });

  it("draws the current row with a real indicator, not a 1.15:1 tint", async () => {
    useSessionStore.getState().setDriver(
      listingDriver({
        sessions: [row(), row({ sessionId: "sess_b", title: "COB investigation" })],
        total: 2,
      }),
    );
    useSessionStore.setState({ sessionId: "sess_a" });

    renderRail();
    const current = await screen.findByRole("button", {
      name: /Why did cash decline last week\?/,
    });
    const other = screen.getByRole("button", { name: /COB investigation/ });

    // `bg-accent` measures 1.15:1 against the translucent rail and its
    // hover variant 1.06:1 — the tint could not tell selected from hovered.
    // `--ring` on that surface is 3.61:1 light / 10.22:1 dark.
    expect(current.className).toContain("border-l-ring");
    expect(other.className).toContain("border-l-transparent");
    expect(other.className).not.toContain("border-l-ring");
  });

  it("paints the primary CTA from the stops white is legible on", async () => {
    useSessionStore.getState().setDriver(listingDriver({ sessions: [], total: 0 }));

    renderRail();

    // White measured 3.74:1 light / 2.49:1 dark on the display stops — the
    // most prominent button in the product, below AA in both themes. The
    // CTA stops carry it at 5.21:1 → 5.48:1.
    const cta = await screen.findByRole("button", { name: /New chat/ });
    expect(cta.className).toContain("accent-gradient-cta");
    expect(cta.className).not.toMatch(/(^|\s)accent-gradient(\s|$)/);
  });

  it("does not switch while a turn is streaming", async () => {
    const resumeSession = vi.fn();
    useSessionStore
      .getState()
      .setDriver(
        listingDriver(
          { sessions: [row(), row({ sessionId: "sess_b", title: "COB investigation" })], total: 2 },
          resumeSession,
        ),
      );
    useSessionStore.setState({ sessionId: "sess_a", streamingTurnId: "turn_1" });

    renderRail();
    const target = await screen.findByRole("button", { name: /COB investigation/ });
    expect(target).toBeDisabled();

    await userEvent.click(target);
    expect(resumeSession).not.toHaveBeenCalled();
  });

  it("says a capped page is capped instead of implying it is the whole history", async () => {
    useSessionStore.getState().setDriver(listingDriver({ sessions: [row()], total: 12 }));

    renderRail();

    expect(await screen.findByText("1 of 12")).toBeInTheDocument();
  });

  it("names the failure when the list cannot be read", async () => {
    useSessionStore.getState().setDriver({
      submit: vi.fn(),
      newSession: vi.fn(),
      listSessions: vi.fn().mockRejectedValue(new Error("HTTP 503")),
    });

    renderRail();

    expect(await screen.findByText("HTTP 503")).toBeInTheDocument();
  });
});

/**
 * Archiving a row.
 *
 * `DELETE /v1/sessions/{sid}` is a SOFT archive server-side: 204, nothing
 * deleted, the session keeps its investigations and traces and stays
 * fetchable by id — it stops appearing in `GET /v1/sessions` and nothing
 * more. Three things this control has to get right, and all three are the
 * difference between tidying a list and appearing to destroy somebody's
 * work: it says archive rather than delete, it confirms before acting, and
 * a refusal puts the row back exactly where it was.
 */
describe("SessionRail — archiving a session off the list", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({
      driver: null,
      sessions: [],
      sessionsTotal: 0,
      sessionsState: "idle",
      sessionsError: null,
      switchingSessionId: null,
      switchError: null,
      connection: { mode: "api", state: "online" },
    });
  });

  afterEach(() => cleanup());

  function archivingDriver(archiveSession: TurnDriver["archiveSession"]): TurnDriver {
    return {
      ...listingDriver({
        sessions: [row(), row({ sessionId: "sess_b", title: "COB investigation" })],
        total: 2,
      }),
      ...(archiveSession ? { archiveSession } : {}),
    };
  }

  it("confirms first, in the language of archiving rather than deleting", async () => {
    const archiveSession = vi.fn().mockResolvedValue(undefined);
    useSessionStore.getState().setDriver(archivingDriver(archiveSession));
    renderRail();
    await screen.findByText("COB investigation");

    await userEvent.click(
      screen.getByRole("button", { name: "Archive Why did cash decline last week?" }),
    );

    // Nothing has happened yet — the confirm is a real gate.
    expect(archiveSession).not.toHaveBeenCalled();
    const body = document.body.textContent ?? "";
    expect(body).toMatch(/Its answers are kept and it stays reachable by link/);
    // The word "delete" must not appear over an operation that deletes
    // nothing; the row is what goes.
    expect(body).not.toMatch(/delete/i);
    expect(body).not.toMatch(/permanent/i);
  });

  it("keeps the row when the confirm is declined", async () => {
    const archiveSession = vi.fn().mockResolvedValue(undefined);
    useSessionStore.getState().setDriver(archivingDriver(archiveSession));
    renderRail();
    await screen.findByText("COB investigation");

    await userEvent.click(
      screen.getByRole("button", { name: "Archive Why did cash decline last week?" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Keep it" }));

    expect(archiveSession).not.toHaveBeenCalled();
    expect(screen.getByText("Why did cash decline last week?")).toBeInTheDocument();
    expect(useSessionStore.getState().sessions).toHaveLength(2);
  });

  it("removes the row optimistically and archives it server-side", async () => {
    const archiveSession = vi.fn().mockResolvedValue(undefined);
    useSessionStore.getState().setDriver(archivingDriver(archiveSession));
    renderRail();
    await screen.findByText("COB investigation");

    await userEvent.click(
      screen.getByRole("button", { name: "Archive Why did cash decline last week?" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Archive" }));

    expect(archiveSession).toHaveBeenCalledWith("sess_a");
    await waitFor(() =>
      expect(screen.queryByText("Why did cash decline last week?")).not.toBeInTheDocument(),
    );
    // The other row is untouched, and the total tracks the removal.
    expect(screen.getByText("COB investigation")).toBeInTheDocument();
    expect(useSessionStore.getState().sessionsTotal).toBe(1);
  });

  it("puts the row back, and says why, when the server refuses", async () => {
    const archiveSession = vi.fn().mockRejectedValue(new Error("HTTP 403 — not your tenant"));
    useSessionStore.getState().setDriver(archivingDriver(archiveSession));
    renderRail();
    await screen.findByText("COB investigation");

    await userEvent.click(
      screen.getByRole("button", { name: "Archive Why did cash decline last week?" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Archive" }));

    // A row that left the screen while surviving on the server is a list
    // quietly lying about what exists.
    expect(await screen.findByText(/HTTP 403 — not your tenant/)).toBeInTheDocument();
    expect(screen.getByText("Why did cash decline last week?")).toBeInTheDocument();
    expect(useSessionStore.getState().sessions).toHaveLength(2);
    expect(useSessionStore.getState().sessionsTotal).toBe(2);
  });

  it("offers no archive control on a driver that cannot archive", async () => {
    // The mock fixture has no deployment behind it. A control that would
    // silently do nothing is worse than one that is not there.
    useSessionStore.getState().setDriver(archivingDriver(undefined));
    renderRail();
    await screen.findByText("COB investigation");

    expect(screen.queryByRole("button", { name: /^Archive / })).not.toBeInTheDocument();
  });
});

describe("SessionRail — the mock fixture has no sessions and says so", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({
      sessions: [],
      sessionsState: "idle",
      sessionsError: null,
      connection: { mode: "mock", state: "online" },
    });
    useSessionStore.getState().setDriver(new MockDriver(0));
  });

  afterEach(() => cleanup());

  it("shows why the list is empty rather than inventing rows", async () => {
    renderRail();

    expect(
      await screen.findByText(/no deployment to list sessions from/i),
    ).toBeInTheDocument();
    expect(useSessionStore.getState().sessions).toEqual([]);
  });

  it("keeps the fabricating fixture previews to mock mode only", async () => {
    renderRail();

    expect(await screen.findByText("Fixture previews")).toBeInTheDocument();
    expect(screen.getByText("Simulate a newer data load")).toBeInTheDocument();

    cleanup();
    useSessionStore.setState({ connection: { mode: "api", state: "online" } });
    useSessionStore.getState().setDriver({
      submit: vi.fn(),
      newSession: vi.fn(),
      listSessions: vi.fn().mockResolvedValue({ sessions: [], total: 0 }),
    });
    renderRail();

    // A simulated watermark is a watermark that does not exist. Useful
    // against a fixture; a lie against a live deployment.
    await waitFor(() =>
      expect(screen.queryByText("Simulate a newer data load")).not.toBeInTheDocument(),
    );
  });
});
