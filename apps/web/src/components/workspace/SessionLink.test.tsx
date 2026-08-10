/**
 * "COPY LINK" DISCLOSES BEFORE IT COPIES.
 *
 * The live sequence: the operator answers the CFO's question, presses
 * Copy link, the CFO opens it cold, and the two thousand words everyone
 * had just read are replaced by "The written analysis was not stored for
 * this turn". The page was honest. The button said nothing, and the
 * tooltip that did say something needed a hover — so on a touch screen
 * and for a keyboard reader the disclosure did not exist at all.
 *
 * These assert the property, not the copy: the disclosure is reachable
 * without a pointer, it is on screen BEFORE anything reaches the
 * clipboard, and what it says tracks what this browser has watched come
 * back from the server.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SessionLink } from "@/components/workspace/Workspace";
import { TooltipProvider } from "@/components/ui/tooltip";
import { emptyAnswer, useSessionStore, type TurnRecord } from "@/lib/store";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn(), back: vi.fn() }),
  usePathname: () => "/",
}));

const copied: string[] = [];

function turn(id: string, over: Partial<TurnRecord["answer"]>): TurnRecord {
  return {
    id,
    index: 0,
    submission: { utterance: "Why did our denial rate go up in July 2026?" } as never,
    answer: { ...emptyAnswer(), status: "complete", ...over },
  };
}

function draw() {
  return render(
    <TooltipProvider>
      <SessionLink sessionId="sess_6850e4aa2ccd" />
    </TooltipProvider>,
  );
}

beforeEach(() => {
  copied.length = 0;
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: {
      writeText: (text: string) => {
        copied.push(text);
        return Promise.resolve();
      },
    },
  });
  useSessionStore.setState({ turns: [] });
});

afterEach(cleanup);

describe("the disclosure is in front of the copy, not behind a hover", () => {
  it("copies nothing on the first click — it opens what the link will contain", async () => {
    useSessionStore.setState({ turns: [turn("t1", { narrative: "It rose 3.6 points." })] });
    draw();
    fireEvent.click(screen.getByRole("button", { name: /Copy link/ }));
    expect(copied).toEqual([]);
    expect(await screen.findByText(/What this link opens/)).toBeInTheDocument();
    expect(screen.getByText(/It carries/)).toBeInTheDocument();
    expect(screen.getByText(/It does not carry/)).toBeInTheDocument();
  });

  it("copies the permalink from inside the disclosure", async () => {
    useSessionStore.setState({ turns: [turn("t1", { narrative: "It rose 3.6 points." })] });
    draw();
    fireEvent.click(screen.getByRole("button", { name: /Copy link/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Copy the link/ }));
    await screen.findByText(/Link copied/);
    expect(copied).toHaveLength(1);
    expect(copied[0]).toContain("/s/sess_6850e4aa2ccd");
  });
});

describe("what it promises tracks what the server actually gave back", () => {
  it("on a live session it says it has not measured, and names the check", async () => {
    // The demo case. Every turn here was watched streaming, so the client
    // store holds prose the server may never have kept — which is exactly
    // how the round-9 pass concluded that answers restore with their
    // narrative. They do not.
    useSessionStore.setState({ turns: [turn("t1", { narrative: "It rose 3.6 points." })] });
    draw();
    fireEvent.click(screen.getByRole("button", { name: /Copy link/ }));
    const basis = await screen.findByText(/Not yet measured on this session/);
    expect(basis).toHaveAttribute("data-disclosure-basis", "unobserved");
    expect(screen.getByText(/open the link once yourself/)).toBeInTheDocument();
  });

  it("on a restored session with no prose it says the analysis isn't stored", async () => {
    useSessionStore.setState({
      turns: [turn("t1", { narrative: "", rehydrated: true, charts: [] })],
    });
    draw();
    fireEvent.click(screen.getByRole("button", { name: /Copy link/ }));
    expect(
      await screen.findByText(/The written analysis isn't stored/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Measured:/)).toHaveAttribute(
      "data-disclosure-basis",
      "observed",
    );
  });

  it("says the analysis IS carried the moment a restore comes back with it", async () => {
    // Coded for the backend lane's fix rather than against it: nothing
    // here needs editing the morning the narrative starts persisting.
    useSessionStore.setState({
      turns: [turn("t1", { narrative: "It rose 3.6 points.", rehydrated: true })],
    });
    draw();
    fireEvent.click(screen.getByRole("button", { name: /Copy link/ }));
    expect(
      await screen.findByText(/The written analysis, as it was composed/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/isn't stored/)).not.toBeInTheDocument();
  });
});
