/**
 * THE ROUTE TABLE'S LOAD-BEARING PROPERTIES.
 *
 * Two of them, and both look like arbitrary style choices to the next
 * person who edits `App.tsx`.
 *
 * ONE. The workspace rewrites its own address bar: `/i/{iid}` resolves to
 * the session that turn belongs to and becomes `/s/{sid}`. Under Next that
 * was a raw `history.replaceState` — no route transition, no remount, one
 * component instance running throughout. Under react-router it is a
 * navigation, and a navigation swaps the matched route's element. `/s/:id`
 * and `/i/:id` therefore render the SAME component type, so React
 * reconciles instead of remounting; three different components would make
 * every address rewrite a fresh driver, a re-torn-down health poll, and a
 * thread rebuilt underneath the analyst mid-conversation.
 *
 * TWO. `WorkspaceRoute` PINS the segment at mount. `useParams` is live, so
 * an un-pinned read would hand the workspace an `initialSessionId` for the
 * session it is already in the moment it published its own permalink —
 * asking the store to re-join and rebuild a thread that is on screen.
 *
 * AND ONE PROPERTY THAT CHANGED, DELIBERATELY. `/` is Home, not the
 * workspace. `/` → `/s/{id}` is a real mount now, and that is correct
 * rather than tolerated: Home is a different surface, the same way Monitors
 * is. The turn that causes the move is already streaming in the store when
 * the workspace arrives, and `switchSession` no-ops on a session that is
 * streaming or already on screen — so nothing is re-joined. The tests below
 * pin the new front door and keep every invariant that still applies.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { act } from "react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const mounts = vi.fn();
const props = vi.fn();
const homeMounts = vi.fn();

vi.mock("@/components/workspace/Workspace", () => ({
  default: (p: { initialSessionId?: string; initialInvestigationId?: string }) => {
    props(p);
    // A mount counter that survives re-renders: the effect runs once per
    // MOUNT, which is exactly the event this file is about.
    return <WorkspaceStub {...p} />;
  },
}));

// Home is three live reads and a composer; this file is about which
// element a path resolves to, so it is stubbed exactly as the workspace is.
// What Home DOES is asserted in `components/home/Home.test.tsx`.
vi.mock("@/components/home/Home", () => ({
  Home: () => <HomeStub />,
}));

vi.mock("@/components/monitors/MonitorsSurface", () => ({
  MonitorsSurface: () => <div data-testid="monitors">Monitors</div>,
}));

vi.mock("@/components/providers/QueryProvider", () => ({
  QueryProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { useEffect } from "react";

import { AppRoutes } from "@/App";

function WorkspaceStub(p: { initialSessionId?: string; initialInvestigationId?: string }) {
  useEffect(() => {
    mounts();
  }, []);
  return (
    <div data-testid="workspace" data-session={p.initialSessionId ?? ""} data-investigation={p.initialInvestigationId ?? ""}>
      workspace
    </div>
  );
}

function HomeStub() {
  useEffect(() => {
    homeMounts();
  }, []);
  return <div data-testid="home">home</div>;
}

/** A control the test can press to perform the address rewrite for real. */
function Rewriter({ to }: { to: string }) {
  const navigate = useNavigate();
  return (
    <button type="button" onClick={() => navigate(to, { replace: true })}>
      rewrite
    </button>
  );
}

function draw(initial: string, rewriteTo = "/s/sess_1") {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <Rewriter to={rewriteTo} />
      <AppRoutes />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  mounts.mockClear();
  props.mockClear();
  homeMounts.mockClear();
});

describe("the route table — Home at the front door, one workspace behind it", () => {
  it("renders HOME at /, and not the workspace", () => {
    draw("/");
    expect(screen.getByTestId("home")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace")).not.toBeInTheDocument();
    expect(mounts).toHaveBeenCalledTimes(0);
  });

  it("passes the segment through at /s/{id}", () => {
    draw("/s/sess_6850e4aa2ccd");
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "sess_6850e4aa2ccd");
    expect(screen.queryByTestId("home")).not.toBeInTheDocument();
  });

  it("passes the segment through at /i/{id}, as an investigation", () => {
    draw("/i/inv_6455d1b5dbd7");
    const el = screen.getByTestId("workspace");
    expect(el).toHaveAttribute("data-investigation", "inv_6455d1b5dbd7");
    expect(el).toHaveAttribute("data-session", "");
  });

  it("renders Monitors at /monitors, and neither of the other two", () => {
    draw("/monitors");
    expect(screen.getByTestId("monitors")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace")).not.toBeInTheDocument();
    expect(screen.queryByTestId("home")).not.toBeInTheDocument();
  });
});

describe("the address rewrite is not a remount", () => {
  /**
   * The regression this exists for: a rewrite that unmounts the workspace
   * mints a second driver over a live session. That is the exact failure
   * M31 fixed at the store level, and a route table naming `/s/:id` and
   * `/i/:id` with two different components would reintroduce it above the
   * store. `/i/{iid}` → `/s/{sid}` is the rewrite that still happens.
   */
  it("keeps ONE workspace instance across /i/{iid} -> /s/{sid}", () => {
    draw("/i/inv_1", "/s/sess_resolved");
    expect(mounts).toHaveBeenCalledTimes(1);

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });

    expect(screen.getByTestId("workspace")).toBeInTheDocument();
    expect(mounts).toHaveBeenCalledTimes(1);
  });

  it("keeps ONE workspace instance across /s/{id} -> /s/{id} (a permalink re-asserted)", () => {
    draw("/s/sess_old", "/s/sess_old");
    expect(mounts).toHaveBeenCalledTimes(1);

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });

    expect(mounts).toHaveBeenCalledTimes(1);
  });

  it("PINS the segment at mount, so a rewrite never feeds a session back in", () => {
    draw("/i/inv_pinned", "/s/sess_minted");
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "");

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });

    // The address bar now says /s/sess_minted — and the workspace still
    // holds no `initialSessionId`, so its "open this session" effect has
    // nothing new to act on. An un-pinned `useParams` would put
    // "sess_minted" here and re-join the session already on screen.
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "");
    expect(screen.getByTestId("workspace")).toHaveAttribute(
      "data-investigation",
      "inv_pinned",
    );
  });

  it("still pins on a REAL arrival — a permalink opened cold", () => {
    draw("/s/sess_linked", "/");
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "sess_linked");
  });

  /**
   * The counter counts, so an empty result above means something.
   *
   * A route change to a DIFFERENT surface must remount — that is not a
   * defect, it is what Monitors' skip-link focus targets and Home's
   * brief announcement are built on. If this passed at one mount as well,
   * the assertions above would be measuring a stub that never re-runs its
   * effect rather than a reconciliation that never remounts.
   */
  it("DOES remount when the surface genuinely changes (/monitors -> /s/{id})", () => {
    draw("/monitors", "/s/sess_1");
    expect(mounts).toHaveBeenCalledTimes(0);

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });
    expect(mounts).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("monitors")).not.toBeInTheDocument();
  });
});

describe("Home is the front door, and the workspace comes back to it", () => {
  /**
   * "NEW CHAT" GOES HOME. The store abandons the session and the address
   * goes back to `/` — which is Home, with its composer, rather than the
   * empty hero the workspace used to draw at that path. The workspace must
   * genuinely leave: a mounted workspace over a discarded session is what
   * kept putting the abandoned thread's question in the header.
   */
  it("swaps the workspace out for Home across /s/{id} -> / (New chat)", () => {
    draw("/s/sess_old", "/");
    expect(screen.getByTestId("workspace")).toBeInTheDocument();
    expect(mounts).toHaveBeenCalledTimes(1);

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });

    expect(screen.queryByTestId("workspace")).not.toBeInTheDocument();
    expect(screen.getByTestId("home")).toBeInTheDocument();
  });

  /**
   * AND THE FIRST TURN LEAVES IT. Home submits, the turn mints a session,
   * and the address becomes `/s/{id}` — a real mount of the workspace,
   * pinned to the session the turn is already streaming into.
   */
  it("mounts the workspace, pinned, across / -> /s/{id} (the first question)", () => {
    draw("/", "/s/sess_minted");
    expect(screen.getByTestId("home")).toBeInTheDocument();
    expect(mounts).toHaveBeenCalledTimes(0);

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });

    expect(mounts).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "sess_minted");
    expect(screen.queryByTestId("home")).not.toBeInTheDocument();
  });
});
