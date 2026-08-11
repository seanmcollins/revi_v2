/**
 * BOTH SIDE PANES FOLD — asymmetrically, and on purpose.
 *
 * What is pinned here is the behaviour an owner asked for and the three
 * places it is easy to get subtly wrong:
 *
 *   ASYMMETRY. The left rail never vanishes; it narrows to an icon strip
 *     carrying New chat, Home, Monitors, the connection indicator and the
 *     way back. The right rail vanishes completely and leaves one thin
 *     edge tab. Collapsing them the same way would cost a reader their
 *     wayfinding to save 216 pixels.
 *   BORROWED, NOT CHOSEN. A referent chip opens the evidence rail over a
 *     preference that says collapsed — and closing it returns to
 *     collapsed. If a citation could flip the persisted preference, one
 *     click on F2 would silently rewrite how the workspace opens
 *     tomorrow.
 *   THE FREED WIDTH IS THE MIDDLE COLUMN'S, and it goes to the figures
 *     rather than to the prose. These assert the CONTAINER contract — the
 *     grid's data attributes and the classes the CSS derives widths from
 *     — never the chart's internals, which belong to the chart module and
 *     measure themselves.
 *
 * Nothing is mocked except the two things a jsdom render cannot have: the
 * network, and a layout engine (so the widths are asserted at their
 * source, `globals.css`, in the style of `decoration.test.ts`).
 */

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { MemoryRouter } from "react-router-dom";

import Workspace from "@/components/workspace/Workspace";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { TurnDriver } from "@/lib/driver";
import { PANE_STORAGE_KEYS, usePaneStore } from "@/lib/panes";
import { useSessionStore } from "@/lib/store";

/* ------------------------------------------------------------------ */
/* Harness                                                             */
/* ------------------------------------------------------------------ */

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
});

/** A driver with nothing real in it — no turn is submitted here. */
function quietDriver(): TurnDriver {
  return {
    submit: vi.fn().mockResolvedValue(undefined),
    newSession: vi.fn().mockResolvedValue(undefined),
    listSessions: vi.fn().mockResolvedValue({ sessions: [], total: 0 }),
  };
}

function serve() {
  vi.stubGlobal(
    "fetch",
    vi.fn(() =>
      Promise.resolve(
        new Response(JSON.stringify({}), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    ),
  );
}

function draw() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <MemoryRouter initialEntries={["/s/sess_1"]}>
      <QueryClientProvider client={client}>
        <TooltipProvider>
          <Workspace />
        </TooltipProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

/** The element carrying the pane state — the grid the CSS reads. */
function grid(): HTMLElement {
  const node = document.querySelector(".workspace-grid");
  if (!(node instanceof HTMLElement)) throw new Error("the workspace grid is not on screen");
  return node;
}

/** The window width the auto-collapse thresholds are read from. */
function resizeTo(width: number) {
  Object.defineProperty(window, "innerWidth", { value: width, configurable: true });
  act(() => {
    window.dispatchEvent(new Event("resize"));
  });
}

beforeEach(() => {
  window.localStorage.clear();
  // The mock fixture: no network, no session bootstrap, no health poll.
  window.localStorage.setItem("revi-driver", "mock");
  Element.prototype.scrollIntoView = vi.fn();
  usePaneStore.getState().reset();
  useSessionStore.getState().reset();
  useSessionStore.setState({
    driver: quietDriver(),
    connection: { mode: "mock", state: "online", healthChecked: true },
    sessions: [],
    turns: [],
  });
  // Wide enough that nothing auto-collapses: every fold below is a choice.
  Object.defineProperty(window, "innerWidth", { value: 1600, configurable: true });
  serve();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  document.getElementById("revi-live-announcer")?.remove();
  window.localStorage.clear();
  usePaneStore.getState().reset();
});

/* ------------------------------------------------------------------ */
/* Asymmetric collapse                                                 */
/* ------------------------------------------------------------------ */

describe("both panes fold, and they do not fold alike", () => {
  it("opens with both rails expanded and nothing freed", () => {
    draw();
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "false");
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "false");
    expect(screen.getByRole("button", { name: "Collapse the sessions pane" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Collapse the evidence pane" })).toBeVisible();
  });

  it("the sessions rail narrows to an icon strip that keeps every way out", async () => {
    const user = userEvent.setup();
    draw();

    await user.click(screen.getByRole("button", { name: "Collapse the sessions pane" }));

    expect(grid()).toHaveAttribute("data-sessions-collapsed", "true");

    // A nav landmark, not an unlabelled column of glyphs.
    const strip = screen.getByRole("navigation", { name: "Main" });
    // The five things somebody would be stranded without.
    expect(within(strip).getByRole("button", { name: "New chat" })).toBeVisible();
    expect(within(strip).getByRole("link", { name: "Home" })).toBeVisible();
    expect(within(strip).getByRole("status", { name: "Mock fixture" })).toBeVisible();
    expect(
      within(strip).getByRole("button", { name: "Expand the sessions pane" }),
    ).toBeVisible();

    // And the lists are gone rather than rendered as five identical icons.
    expect(within(strip).queryByRole("heading", { name: /sessions/i })).toBeNull();
  });

  it("the strip offers Monitors on a live deployment, and not on the fixture", async () => {
    const user = userEvent.setup();
    draw();
    await user.click(screen.getByRole("button", { name: "Collapse the sessions pane" }));

    // Mock fixture: no deployment to walk, so no link to one.
    expect(screen.queryByRole("link", { name: /^Monitors/ })).toBeNull();

    act(() => {
      useSessionStore.setState({
        connection: { mode: "api", state: "online", healthChecked: true },
      });
    });
    expect(screen.getByRole("link", { name: /^Monitors/ })).toBeVisible();
  });

  it("the evidence rail goes entirely, leaving one edge tab", async () => {
    const user = userEvent.setup();
    draw();

    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));

    expect(grid()).toHaveAttribute("data-evidence-collapsed", "true");
    // The panel itself is off the page — not merely hidden.
    expect(document.getElementById("pane-evidence")).toBeNull();

    const tab = screen.getByRole("button", { name: "Expand the evidence pane" });
    expect(tab).toBeVisible();
    // It says the word the panel's own tab says — one rendering per thing.
    expect(tab).toHaveTextContent("Evidence");
    // Tabbable: a control only a pointer can find is half a control.
    expect(tab.tabIndex).toBeGreaterThanOrEqual(0);
  });

  it("announces each pane's state on its own toggle", async () => {
    const user = userEvent.setup();
    draw();

    expect(screen.getByRole("button", { name: "Collapse the sessions pane" })).toHaveAttribute(
      "aria-expanded",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Collapse the sessions pane" }));
    expect(screen.getByRole("button", { name: "Expand the sessions pane" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );

    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));
    expect(screen.getByRole("button", { name: "Expand the evidence pane" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("both fold at once, and both come back", async () => {
    const user = userEvent.setup();
    draw();
    await user.click(screen.getByRole("button", { name: "Collapse the sessions pane" }));
    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));

    expect(grid()).toHaveAttribute("data-sessions-collapsed", "true");
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "true");

    await user.click(screen.getByRole("button", { name: "Expand the sessions pane" }));
    await user.click(screen.getByRole("button", { name: "Expand the evidence pane" }));

    expect(grid()).toHaveAttribute("data-sessions-collapsed", "false");
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "false");
  });
});

/* ------------------------------------------------------------------ */
/* Reopen semantics                                                    */
/* ------------------------------------------------------------------ */

describe("an open-evidence gesture borrows the rail; it does not buy it", () => {
  it("opens a collapsed rail without touching what this device asked for", async () => {
    const user = userEvent.setup();
    draw();
    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));
    expect(window.localStorage.getItem(PANE_STORAGE_KEYS.evidence)).toBe("collapsed");

    // What a referent chip, the integrity line and the trust row all do.
    act(() => {
      useSessionStore.getState().openDrawer("turn_1");
    });

    expect(grid()).toHaveAttribute("data-evidence-collapsed", "false");
    expect(document.getElementById("pane-evidence")).not.toBeNull();
    // The persisted preference is exactly what it was.
    expect(window.localStorage.getItem(PANE_STORAGE_KEYS.evidence)).toBe("collapsed");
  });

  it("closing the borrowed rail returns to collapsed, and stays there", async () => {
    const user = userEvent.setup();
    draw();
    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));
    act(() => {
      useSessionStore.getState().openDrawer("turn_1");
    });

    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));

    expect(grid()).toHaveAttribute("data-evidence-collapsed", "true");
    expect(window.localStorage.getItem(PANE_STORAGE_KEYS.evidence)).toBe("collapsed");
    // A fresh visit opens collapsed, which is what was asked for.
    expect(usePaneStore.getState().preference.evidence).toBe(true);
  });

  it("and the next gesture opens it again", async () => {
    const user = userEvent.setup();
    draw();
    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));
    act(() => {
      useSessionStore.getState().openDrawer("turn_1");
    });
    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));

    act(() => {
      useSessionStore.getState().openDrawer("turn_1");
    });
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "false");
  });

  it("the toggle, unlike a gesture, DOES record the preference", async () => {
    const user = userEvent.setup();
    draw();
    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));
    await user.click(screen.getByRole("button", { name: "Expand the evidence pane" }));

    expect(window.localStorage.getItem(PANE_STORAGE_KEYS.evidence)).toBe("expanded");
  });
});

/* ------------------------------------------------------------------ */
/* Keyboard                                                            */
/* ------------------------------------------------------------------ */

describe("[ and ] fold the panes — except while somebody is typing", () => {
  it("[ toggles the sessions pane", async () => {
    const user = userEvent.setup();
    draw();

    // `[[` is user-event's escape for a literal bracket — a bare
    // `[` opens a key descriptor in its keyboard DSL.
    await user.keyboard("[[");
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "true");
    await user.keyboard("[[");
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "false");
  });

  it("] toggles the evidence pane", async () => {
    const user = userEvent.setup();
    draw();

    await user.keyboard("]");
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "true");
    await user.keyboard("]");
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "false");
  });

  it("the composer can type a bracket without folding the workspace", async () => {
    const user = userEvent.setup();
    draw();

    const composer = document.getElementById("turn-composer");
    expect(composer).not.toBeNull();
    await user.click(composer as HTMLElement);
    await user.keyboard("CARC 45 [[see note]");

    expect(grid()).toHaveAttribute("data-sessions-collapsed", "false");
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "false");
    expect(composer).toHaveValue("CARC 45 [see note]");
  });

  it("leaves ⌘[ to the browser", async () => {
    const user = userEvent.setup();
    draw();
    await user.keyboard("{Meta>}[[{/Meta}");
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "false");
  });
});

/* ------------------------------------------------------------------ */
/* Focus                                                               */
/* ------------------------------------------------------------------ */

describe("a pane that closes under somebody's focus hands it over", () => {
  it("the sessions toggle keeps the focus across the fold", async () => {
    const user = userEvent.setup();
    draw();

    await user.click(screen.getByRole("button", { name: "Collapse the sessions pane" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Expand the sessions pane" })).toHaveFocus(),
    );
  });

  it("the evidence rail hands its focus to the edge tab", async () => {
    const user = userEvent.setup();
    draw();

    await user.click(screen.getByRole("button", { name: "Collapse the evidence pane" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Expand the evidence pane" })).toHaveFocus(),
    );
  });

  it("a keyboard fold from outside the pane does not steal the focus", async () => {
    const user = userEvent.setup();
    draw();
    const composer = document.getElementById("turn-composer") as HTMLElement;
    composer.focus();

    // Not typed INTO the composer — the shortcut is suppressed there. This
    // is the case where focus is elsewhere on the page entirely.
    const heading = screen.getByRole("heading", { level: 1 });
    heading.setAttribute("tabindex", "-1");
    heading.focus();
    await user.keyboard("]");

    expect(grid()).toHaveAttribute("data-evidence-collapsed", "true");
    expect(heading).toHaveFocus();
  });
});

/* ------------------------------------------------------------------ */
/* Auto-collapse                                                       */
/* ------------------------------------------------------------------ */

describe("narrow viewports fold the panes, and a choice outranks the width", () => {
  it("folds the sessions rail first", () => {
    draw();
    resizeTo(1200);
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "true");
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "false");
  });

  it("folds the evidence rail too once the column would be narrower than the prose", () => {
    draw();
    resizeTo(900);
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "true");
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "true");
  });

  it("un-folds when the room comes back", () => {
    draw();
    resizeTo(900);
    resizeTo(1600);
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "false");
    expect(grid()).toHaveAttribute("data-evidence-collapsed", "false");
  });

  it("stops deciding once the reader has", async () => {
    const user = userEvent.setup();
    draw();
    resizeTo(1200);
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "true");

    await user.click(screen.getByRole("button", { name: "Expand the sessions pane" }));
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "false");

    // Narrower still — and the layout does not argue.
    resizeTo(1024);
    expect(grid()).toHaveAttribute("data-sessions-collapsed", "false");
  });
});

/* ------------------------------------------------------------------ */
/* Where the freed width goes                                          */
/* ------------------------------------------------------------------ */

const CSS = readFileSync(
  resolve(dirname(fileURLToPath(import.meta.url)), "../../globals.css"),
  "utf8",
);

describe("the freed width reaches the figures, not the prose", () => {
  it("the answer column is the size container the figures measure against", () => {
    draw();
    const column = document.querySelector(".answer-column");
    expect(column).not.toBeNull();
    expect(CSS).toMatch(/\.answer-column\s*\{[^}]*container-type:\s*inline-size/);
  });

  it("the grid derives the freed width from BOTH panes", () => {
    // The whole contract with the chart lane: the container widens, and
    // their `ResizeObserver` does the rest. If `--pane-freed` ever stops
    // being the difference between the expanded widths and the drawn
    // ones, a collapsed rail would free room the figures never see.
    const block = CSS.match(/\.workspace-grid\s*\{[\s\S]*?\n\}/)?.[0] ?? "";
    expect(block).toContain("--pane-freed");
    expect(block).toContain("var(--pane-sessions) - var(--pane-sessions-width)");
    expect(block).toContain("var(--pane-evidence) - var(--pane-evidence-width)");
    expect(block).toContain("grid-template-columns");
  });

  it("collapsing narrows the drawn columns, which is what frees the room", () => {
    expect(CSS).toMatch(
      /\.workspace-grid\[data-sessions-collapsed="true"\]\s*\{\s*--pane-sessions-width:\s*var\(--pane-sessions-collapsed\)/,
    );
    expect(CSS).toMatch(
      /\.workspace-grid\[data-evidence-collapsed="true"\]\s*\{\s*--pane-evidence-width:\s*0rem/,
    );
  });

  it("the breakout spends the freed width and is capped by its own column", () => {
    const block = CSS.match(/\.data-breakout\s*\{[\s\S]*?\n\}/)?.[0] ?? "";
    // Prose measure + what the rails gave back…
    expect(CSS).toMatch(/--data-measure:\s*calc\(var\(--answer-measure\) \+ var\(--pane-freed/);
    // …and never wider than the column it sits in.
    expect(block).toContain("100cqi");
    // Symmetric: a block child left-aligns, so growth needs both margins.
    expect(block).toContain("margin-inline");
  });

  it("the answer's figures and fact tables carry the breakout; the prose does not", () => {
    const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
    for (const file of [
      "answer/AnswerBodyCurrent.tsx",
      "answer/AnswerBodyDetailed.tsx",
      "answer/AnswerBodyCalm.tsx",
    ]) {
      const source = readFileSync(resolve(root, file), "utf8");
      const charted = source
        .split("\n")
        .findIndex((line) => line.includes("<AnswerChart"));
      expect(charted).toBeGreaterThan(-1);
      expect(source).toContain("data-breakout");
      // The narrative keeps the measure it has always been set on.
      expect(source).not.toMatch(/data-breakout[^\n]*NarrativeText/);
    }
  });

  it("respects a reader who asked for less motion", () => {
    expect(CSS).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.workspace-grid,\s*\.data-breakout\s*\{\s*transition:\s*none/,
    );
  });
});
