/**
 * THE ROUTE TABLE'S ONE LOAD-BEARING PROPERTY.
 *
 * The workspace rewrites its own address bar: `/` becomes `/s/{id}` when the
 * first turn mints a session, and goes back to `/` on New chat. Under Next
 * that was a raw `history.replaceState` — no route transition, no remount,
 * one component instance running throughout. Under react-router it is a
 * navigation, and a navigation swaps the matched route's element.
 *
 * Two decisions keep the lifecycle identical, and both are invisible in the
 * source unless you know to look for them:
 *
 *   1. `/`, `/s/:sessionId` and `/i/:investigationId` all render the SAME
 *      component type, so React reconciles instead of remounting. Give them
 *      three different components and every address rewrite becomes a fresh
 *      driver, a re-torn-down health poll, and a thread rebuilt underneath
 *      the analyst mid-conversation.
 *   2. `WorkspaceRoute` PINS the segment at mount. `useParams` is live, so an
 *      un-pinned read would hand the workspace an `initialSessionId` for the
 *      session it is already in the moment it published its own permalink —
 *      asking the store to re-join and rebuild a thread that is on screen.
 *
 * Both are asserted here rather than in prose, because both look like
 * arbitrary style choices to the next person who edits this file.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { act } from "react";
import { MemoryRouter, useNavigate } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const mounts = vi.fn();
const props = vi.fn();

vi.mock("@/components/workspace/Workspace", () => ({
  default: (p: { initialSessionId?: string; initialInvestigationId?: string }) => {
    props(p);
    // A mount counter that survives re-renders: the effect runs once per
    // MOUNT, which is exactly the event this file is about.
    return <WorkspaceStub {...p} />;
  },
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
});

describe("the route table — three routes, one workspace", () => {
  it("renders the workspace at / with no session pinned", () => {
    draw("/");
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "");
    expect(mounts).toHaveBeenCalledTimes(1);
  });

  it("passes the segment through at /s/{id}", () => {
    draw("/s/sess_6850e4aa2ccd");
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "sess_6850e4aa2ccd");
  });

  it("passes the segment through at /i/{id}, as an investigation", () => {
    draw("/i/inv_6455d1b5dbd7");
    const el = screen.getByTestId("workspace");
    expect(el).toHaveAttribute("data-investigation", "inv_6455d1b5dbd7");
    expect(el).toHaveAttribute("data-session", "");
  });

  it("renders Monitors at /monitors, and not the workspace", () => {
    draw("/monitors");
    expect(screen.getByTestId("monitors")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace")).not.toBeInTheDocument();
  });
});

describe("the address rewrite is not a remount", () => {
  /**
   * The regression this exists for: a `/` → `/s/{id}` rewrite that unmounts
   * the workspace mints a second driver over a live session. That is the
   * exact failure M31 fixed at the store level, and a route table with three
   * different components would reintroduce it above the store.
   */
  it("keeps ONE workspace instance across / -> /s/{id}", () => {
    draw("/", "/s/sess_new");
    expect(mounts).toHaveBeenCalledTimes(1);

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });

    expect(screen.getByTestId("workspace")).toBeInTheDocument();
    expect(mounts).toHaveBeenCalledTimes(1);
  });

  it("keeps ONE workspace instance across /s/{id} -> / (New chat)", () => {
    draw("/s/sess_old", "/");
    expect(mounts).toHaveBeenCalledTimes(1);

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });

    expect(mounts).toHaveBeenCalledTimes(1);
  });

  it("PINS the segment at mount, so a rewrite never feeds a session back in", () => {
    draw("/", "/s/sess_minted");
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "");

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });

    // The address bar now says /s/sess_minted — and the workspace still
    // holds no `initialSessionId`, so its "open this session" effect has
    // nothing new to act on. An un-pinned `useParams` would put
    // "sess_minted" here and re-join the session already on screen.
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "");
  });

  it("still pins on a REAL arrival — a permalink opened cold", () => {
    draw("/s/sess_linked", "/");
    expect(screen.getByTestId("workspace")).toHaveAttribute("data-session", "sess_linked");
  });

  /**
   * The counter counts, so an empty result above means something.
   *
   * A route change to a DIFFERENT surface must remount — that is not a
   * defect, it is what the Monitors arrival announcement and its focus move
   * are built on. If this passed at one mount as well, the assertions above
   * would be measuring a stub that never re-runs its effect rather than a
   * reconciliation that never remounts.
   */
  it("DOES remount when the surface genuinely changes (/ -> /monitors -> /)", () => {
    draw("/", "/monitors");
    expect(mounts).toHaveBeenCalledTimes(1);

    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });
    expect(screen.getByTestId("monitors")).toBeInTheDocument();
    expect(screen.queryByTestId("workspace")).not.toBeInTheDocument();

    cleanup();
    mounts.mockClear();
    draw("/monitors", "/");
    expect(mounts).toHaveBeenCalledTimes(0);
    act(() => {
      screen.getByRole("button", { name: "rewrite" }).click();
    });
    expect(mounts).toHaveBeenCalledTimes(1);
  });
});
