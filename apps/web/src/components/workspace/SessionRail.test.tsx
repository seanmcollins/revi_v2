import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryRouter } from "react-router-dom";

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
    // The rail reads `useLocation().pathname` to decide whether the "New
    // load" dot is pointing at the page the reader is already on, so the
    // route it renders under is part of what these assert: `/` is the
    // workspace, which is where every one of these renders it.
    <MemoryRouter initialEntries={["/"]}>
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <SessionRail />
        </TooltipProvider>
      </QueryClientProvider>
    </MemoryRouter>,
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
          row({ sessionId: "sess_b", title: "COB investigation", turnCount: 4 }),
        ],
        total: 2,
      }),
    );

    renderRail();

    expect(await screen.findByText("Why did cash decline last week?")).toBeInTheDocument();
    expect(screen.getByText("COB investigation")).toBeInTheDocument();
    // The retired fixtures must not come back through any other door.
    expect(screen.queryByText(/Denial spike — Halvern Imaging/)).not.toBeInTheDocument();
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
    useSessionStore.setState({ sessionId: "sess_a", sessionLive: true });

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
    useSessionStore.setState({ sessionId: "sess_a", sessionLive: true });

    renderRail();
    const current = await screen.findByRole("button", {
      name: /Why did cash decline last week\?/,
    });
    const other = screen.getByRole("button", { name: /COB investigation/ });

    // `bg-accent` measures 1.15:1 against the translucent rail and its
    // hover variant 1.06:1 — the tint could not tell selected from hovered.
    // `--ring` on that surface is 3.61:1.
    expect(current.className).toContain("border-l-ring");
    expect(other.className).toContain("border-l-transparent");
    expect(other.className).not.toContain("border-l-ring");
  });

  it("paints the primary CTA from the stops white is legible on", async () => {
    useSessionStore.getState().setDriver(listingDriver({ sessions: [], total: 0 }));

    renderRail();

    // White measured 3.74:1 on the display stops — the most prominent
    // button in the product, below AA. The CTA stops carry it at
    // 5.21:1 → 5.48:1.
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

    // This belongs under the list, where it explains why the list ends —
    // not on the section heading, where "1 of 12" sat as a number beside
    // a title. Same fact, said where it is read.
    expect(
      await screen.findByText("Showing the 1 most recent of 12."),
    ).toBeInTheDocument();
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

  /**
   * A session nobody asked anything in.
   *
   * The live tenant opens with fourteen consecutive "New session — 0
   * turns" rows: one is written whenever a session is created, so every
   * abandoned New chat and every reviewer's probe leaves one behind. They
   * sort to the top, they push real work off the fifty-row page, and not
   * one of them opens onto anything.
   */
  it("lists no session that has no question in it, and says how many it held back", async () => {
    useSessionStore.getState().setDriver(
      listingDriver({
        sessions: [
          row({ sessionId: "sess_empty_1", title: "New session", turnCount: 0 }),
          row(),
          row({ sessionId: "sess_empty_2", title: "New session", turnCount: 0 }),
        ],
        total: 40,
      }),
    );

    renderRail();

    expect(await screen.findByText("Why did cash decline last week?")).toBeInTheDocument();
    expect(screen.queryByText("New session")).not.toBeInTheDocument();
    // Hidden, never silently: the rail and `GET /v1/sessions` still
    // reconcile on screen.
    expect(
      screen.getByText(
        /Showing the 1 most recent of 40 — 2 of the 3 read had no question in them\./,
      ),
    ).toBeInTheDocument();
  });

  /**
   * "New chat" abandons the session and the server mints the next one on
   * the first question, so between the two there is no session at all —
   * and no row in this list is the one you are in.
   */
  it("selects no row once New chat has left the browser without a session", async () => {
    useSessionStore
      .getState()
      .setDriver(
        listingDriver({
          sessions: [row(), row({ sessionId: "sess_b", title: "COB investigation" })],
          total: 2,
        }),
      );
    useSessionStore.setState({ sessionId: "sess_a", sessionLive: true });

    renderRail();
    const current = await screen.findByRole("button", {
      name: /Why did cash decline last week\?/,
    });
    expect(current).toHaveAttribute("aria-current", "true");

    // What `newChat()` sets, and what every session-scoped surface gates
    // on. The abandoned id stays in the store until a turn replaces it.
    useSessionStore.setState({ sessionLive: false });

    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: /Why did cash decline last week\?/ }),
      ).not.toHaveAttribute("aria-current"),
    );
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
    expect(body).toMatch(/Its answers are kept and it stays reachable at its link/);
    // The word "delete" must not appear over an operation that deletes
    // nothing; the row is what goes.
    expect(body).not.toMatch(/delete/i);
    expect(body).not.toMatch(/permanent/i);
  });

  /*
   * The dialog promised a link for a release in which no per-session URL
   * existed anywhere in the product. Now `/s/{session_id}` does, and the
   * promise is not asked to be taken on faith: the confirm hands the link
   * over, before the row it belongs to leaves the list.
   */
  it("hands over the link it promises, before the row goes", async () => {
    const archiveSession = vi.fn().mockResolvedValue(undefined);
    useSessionStore.getState().setDriver(archivingDriver(archiveSession));
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText },
      configurable: true,
    });
    renderRail();
    await screen.findByText("COB investigation");

    await userEvent.click(
      screen.getByRole("button", { name: "Archive Why did cash decline last week?" }),
    );
    await userEvent.click(screen.getByRole("button", { name: /Copy this session's link/ }));

    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("/s/sess_a"));
    expect(archiveSession).not.toHaveBeenCalled();
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

/**
 * The rail at real scale.
 *
 * The live tenant has 219 sessions and this list reads 50 of them, with no
 * search, no archived view and no date grouping — so finding last
 * Tuesday's investigation meant scrolling a wall of one-line titles. This
 * is the minimum viable slice: a filter over the rows already loaded,
 * which is honest about being exactly that.
 */
describe("SessionRail — finding a session in a long list", () => {
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

  const manyRows = Array.from({ length: 9 }, (_, i) =>
    row({ sessionId: `sess_${i}`, title: i === 4 ? "COB investigation" : `Cash question ${i}` }),
  );

  it("offers no filter box while the list is short enough to read", async () => {
    useSessionStore
      .getState()
      .setDriver(listingDriver({ sessions: [row()], total: 1 }));
    renderRail();
    await screen.findByText("Why did cash decline last week?");

    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
  });

  it("filters the loaded rows by title", async () => {
    useSessionStore
      .getState()
      .setDriver(listingDriver({ sessions: manyRows, total: 219 }));
    renderRail();
    await screen.findByText("COB investigation");

    await userEvent.type(screen.getByRole("searchbox"), "cob");

    expect(screen.getByText("COB investigation")).toBeInTheDocument();
    expect(screen.queryByText("Cash question 3")).not.toBeInTheDocument();
  });

  it("says which population it searched when nothing matches", async () => {
    useSessionStore
      .getState()
      .setDriver(listingDriver({ sessions: manyRows, total: 219 }));
    renderRail();
    await screen.findByText("COB investigation");

    await userEvent.type(screen.getByRole("searchbox"), "zzz");

    // Never "no sessions match": this read 9 of the 219 that exist. And
    // never "tenant" — that is the platform's word for the account, not
    // one an analyst uses.
    const body = document.body.textContent ?? "";
    expect(body).toMatch(/Nothing in the 9 sessions loaded here matches/);
    expect(body).toMatch(/there are 219 in all/);
    expect(body).not.toMatch(/tenant/i);
  });
});

/**
 * THE RAIL AT 48 PIXELS.
 *
 * The left pane is wayfinding, so its fold is a NARROWING, not a
 * disappearance: what survives is what somebody would be stranded without.
 * The rail is also mounted by Home and by Monitors, whose grids have no
 * folded width to give back — so the collapse is a prop those two never
 * pass, and a preference set in the workspace cannot leave a 48px strip
 * sitting in a 264px column on a page that never offered to fold it.
 */
describe("SessionRail — the collapsed icon strip", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({
      driver: null,
      sessions: [],
      sessionsTotal: 0,
      sessionsState: "ready",
      sessionsError: null,
      connection: { mode: "api", state: "online", healthChecked: true },
    });
  });

  afterEach(() => cleanup());

  function renderStrip(onToggle?: () => void) {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <MemoryRouter initialEntries={["/"]}>
        <QueryClientProvider client={client}>
          <TooltipProvider>
            <SessionRail collapsed {...(onToggle ? { onToggle } : {})} />
          </TooltipProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  }

  it("is a labelled nav landmark, not an unlabelled column of glyphs", () => {
    renderStrip();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
  });

  it("keeps New chat, Home, Monitors and the connection indicator", () => {
    renderStrip();
    const strip = screen.getByRole("navigation", { name: "Main" });

    expect(within(strip).getByRole("button", { name: "New chat" })).toBeInTheDocument();
    expect(within(strip).getByRole("link", { name: "Home" })).toBeInTheDocument();
    expect(within(strip).getByRole("link", { name: "Monitors" })).toBeInTheDocument();
    expect(within(strip).getByRole("status", { name: "API online" })).toBeInTheDocument();
  });

  it("every icon carries a name — the tooltip is for the pointer, not the record", () => {
    renderStrip();
    for (const control of [
      ...screen.getAllByRole("button"),
      ...screen.getAllByRole("link"),
    ]) {
      expect(control).toHaveAccessibleName();
    }
  });

  it("drops the lists rather than drawing them as identical icons", () => {
    useSessionStore.setState({
      sessions: [row({ title: "Why did cash decline last week?" })],
      sessionsTotal: 1,
    });
    renderStrip();

    expect(screen.queryByText("Why did cash decline last week?")).not.toBeInTheDocument();
    expect(screen.queryByRole("searchbox")).not.toBeInTheDocument();
    expect(screen.queryByText("Replay reference demo")).not.toBeInTheDocument();
  });

  it("says out loud that there is a load nobody has read", () => {
    window.localStorage.removeItem("revi-monitors-seen-watermark");
    useSessionStore.setState({
      connection: {
        mode: "api",
        state: "online",
        healthChecked: true,
        newestWatermarkId: "wm_004",
      },
    });
    renderStrip();

    // A dot on an icon is invisible to a reader who cannot see it, so the
    // fact is on the name. NOT a count — the brief has not been walked.
    expect(
      screen.getByRole("link", { name: "Monitors — there is a data load you have not read" }),
    ).toBeInTheDocument();
  });

  it("carries the expand control, and announces the state on it", async () => {
    const onToggle = vi.fn();
    renderStrip(onToggle);

    const toggle = screen.getByRole("button", { name: "Expand the sessions pane" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(toggle).toHaveAttribute("id", "pane-toggle-sessions");

    await userEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });

  it("offers no fold where no grid can give the column back", () => {
    // Home and Monitors mount this with no handler — and get no dead
    // button rather than one that would leave a strip in a 264px column.
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter initialEntries={["/"]}>
        <QueryClientProvider client={client}>
          <TooltipProvider>
            <SessionRail />
          </TooltipProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );

    expect(screen.queryByRole("button", { name: /the sessions pane/ })).not.toBeInTheDocument();
    // …and it is the full rail, exactly as it always was.
    expect(screen.getByText("Revi")).toBeInTheDocument();
  });

  it("the fold control at the inner edge announces an expanded pane", async () => {
    const onToggle = vi.fn();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter initialEntries={["/"]}>
        <QueryClientProvider client={client}>
          <TooltipProvider>
            <SessionRail onToggle={onToggle} />
          </TooltipProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );

    const toggle = screen.getByRole("button", { name: "Collapse the sessions pane" });
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveAttribute("aria-controls", "pane-sessions");

    await userEvent.click(toggle);
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});
