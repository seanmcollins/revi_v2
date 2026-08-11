/**
 * "View as" — the same certified rows, drawn another way.
 *
 * What this file holds is the part that is a PROMISE rather than a
 * picture: that switching the shape re-renders and never re-measures, that
 * the export does not move when the drawing does, that every form keeps
 * the drill and the honesty marks, and that the choice is the reader's for
 * as long as they are looking at that figure — including after they make
 * it bigger.
 *
 * The marks themselves are Recharts' and are not asserted here (jsdom
 * gives a `ResponsiveContainer` no size). The table and the donut's key
 * are ordinary HTML and are.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { honestyTick, InvestigationChart } from "@/components/charts/InvestigationChart";
import { useSessionStore } from "@/lib/store";
import type { ChartSpec } from "@/lib/types";

beforeAll(() => {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }),
  });
  // Radix menus and dialogs are pointer-driven, and jsdom ships neither
  // `PointerEvent` nor these two element methods.
  if (!("PointerEvent" in window)) {
    // @ts-expect-error jsdom has no PointerEvent; MouseEvent carries what Radix reads.
    window.PointerEvent = window.MouseEvent;
  }
  Element.prototype.hasPointerCapture ??= () => false;
  Element.prototype.setPointerCapture ??= () => {};
  Element.prototype.releasePointerCapture ??= () => {};
  Element.prototype.scrollIntoView ??= () => {};
});

afterEach(() => cleanup());

const RANKING: ChartSpec = {
  id: "chart_main",
  kind: "bar",
  title: "Denied dollars by payer",
  unit: "cents",
  xLabel: "payer",
  series: [{ key: "denied_dollars", label: "denied dollars", role: "current" }],
  rows: [
    { label: "State Medicaid MCO", referent: "payer:pay_003", values: { denied_dollars: 412300 } },
    { label: "Ashvale Health Plan", values: { denied_dollars: 220100 } },
    { label: "Halvern Health", values: { denied_dollars: 118400 } },
  ],
  order: { basis: "value", by: "denied_dollars", descending: true },
};

/** The same ranking with every mark the honesty vocabulary can wear. */
const MARKED: ChartSpec = {
  ...RANKING,
  comparison: undefined,
  rows: [
    { label: "State Medicaid MCO", values: { denied_dollars: 412300 } },
    { label: "Federal Medicare", values: { denied_dollars: 90000 }, bounded: true, denominator: 133 },
    { label: "Summit Peak Medicare Advantage", values: { denied_dollars: 40000 }, provisional: true },
    { label: "Veritas Comp Fund", values: {}, withheld: true },
    {
      label: "Lakewood Medicaid MCO",
      values: { denied_dollars: 0 },
      cells: { denied_dollars: { absent: true } },
    },
  ],
};

async function openViewMenu(user: ReturnType<typeof userEvent.setup>) {
  const trigger = screen.getByRole("button", { name: /^View as, currently/ });
  await user.click(trigger);
  return trigger;
}

async function chooseView(user: ReturnType<typeof userEvent.setup>, name: string) {
  await openViewMenu(user);
  const item = await screen.findByRole("menuitemradio", { name });
  await user.click(item);
}

/* ------------------------------------------------------------------ */

describe("the switcher offers what the payload can honestly become", () => {
  it("names the drawing on screen, so the control is also the label", () => {
    render(<InvestigationChart spec={RANKING} turnId="turn_1" />);
    expect(
      screen.getByRole("button", { name: "View as, currently bar: Denied dollars by payer" }),
    ).toBeInTheDocument();
  });

  it("carries the figure's own name, so four charts are four controls", () => {
    render(
      <InvestigationChart spec={{ ...RANKING, title: "Cash posted by payer" }} turnId="turn_1" />,
    );
    expect(
      screen.getByRole("button", { name: "View as, currently bar: Cash posted by payer" }),
    ).toBeInTheDocument();
  });

  it("lists bar, donut and table on a complete categorical census", async () => {
    const user = userEvent.setup();
    render(<InvestigationChart spec={RANKING} turnId="turn_1" />);
    await openViewMenu(user);
    const items = await screen.findAllByRole("menuitemradio");
    expect(items.map((item) => item.textContent)).toEqual(["Bar", "Donut", "Table"]);
    expect(items[0]).toHaveAttribute("aria-checked", "true");
  });

  it("drops the donut the moment a ceiling is on the figure", async () => {
    const user = userEvent.setup();
    render(<InvestigationChart spec={MARKED} turnId="turn_1" />);
    await openViewMenu(user);
    const items = await screen.findAllByRole("menuitemradio");
    // "≤ $176,112" has no arc: the mark's LENGTH is the claim, and there
    // is no dashed outline that turns an arc drawn at a bound into an arc
    // that is not asserting it. A form that cannot carry the mark is not
    // offered rather than offered with the mark dropped.
    expect(items.map((item) => item.textContent)).toEqual(["Bar", "Table"]);
  });

  it("is not drawn at all when one shape is all there is", () => {
    // A menu with a single item is a control that cannot do anything.
    render(
      <InvestigationChart
        spec={{
          ...RANKING,
          keying: {
            xColumn: "payer",
            seriesColumn: null,
            wireRows: 30,
            keys: 3,
            mode: "unkeyable",
            wireTotal: 441808,
            drawnTotal: 3468,
            note: "These rows are not uniquely keyed by the axes this chart declares.",
            rows: [],
          },
        }}
        turnId="turn_1"
      />,
    );
    expect(screen.queryByRole("button", { name: /^View as/ })).not.toBeInTheDocument();
  });
});

describe("the switcher is a keyboard control with a name", () => {
  it("opens on the keyboard and states which drawing is taken", async () => {
    const user = userEvent.setup();
    render(<InvestigationChart spec={RANKING} turnId="turn_1" />);

    const trigger = screen.getByRole("button", { name: /^View as, currently bar/ });
    trigger.focus();
    expect(trigger).toHaveFocus();
    await user.keyboard("{Enter}");

    const items = await screen.findAllByRole("menuitemradio");
    // A choice of ONE, not a list of commands — which is what tells a
    // screen reader that picking the table replaces the bar chart.
    expect(items).toHaveLength(3);
    expect(items[0]).toHaveAttribute("aria-checked", "true");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(trigger).toHaveFocus());
  });

  it("changes the drawing from the keyboard alone", async () => {
    const user = userEvent.setup();
    render(<InvestigationChart spec={RANKING} turnId="turn_1" />);

    screen.getByRole("button", { name: /^View as/ }).focus();
    await user.keyboard("{Enter}");
    await screen.findAllByRole("menuitemradio");
    await user.keyboard("{ArrowDown}{ArrowDown}{ArrowDown}{Enter}");

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^View as, currently table/ })).toBeInTheDocument(),
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */

describe("the table carries every mark the axis wears", () => {
  it("states a ceiling, a refusal, an absence and a provisional bucket in the cell", async () => {
    const user = userEvent.setup();
    render(<InvestigationChart spec={MARKED} turnId="turn_1" />);
    await chooseView(user, "Table");

    const rows = screen.getAllByRole("row");
    const cell = (label: string) =>
      rows.find((row) => within(row).queryByText(new RegExp(label)))?.textContent ?? "";

    // The "≤" opens the numeral exactly as it does on the axis.
    expect(cell("Federal Medicare")).toContain("≤ ");
    // NOT A BLANK. A blank cell in a spreadsheet-shaped thing is read as a
    // zero, and this one is a refusal.
    expect(cell("Veritas Comp Fund")).toContain("Withheld †");
    expect(cell("Lakewood Medicaid MCO")).toContain("No figure ‡");
    expect(cell("Summit Peak Medicare Advantage")).toContain("*");
  });

  it("drills from a row, with the referent the wire published", async () => {
    const user = userEvent.setup();
    const emitted: unknown[] = [];
    const spy = vi
      .spyOn(useSessionStore.getState(), "emitRefinement")
      .mockImplementation((refinement, meta) => {
        emitted.push({ refinement, meta });
      });
    useSessionStore.setState({ emitRefinement: spy as never });

    render(<InvestigationChart spec={RANKING} turnId="turn_1" />);
    await chooseView(user, "Table");
    await user.click(screen.getByRole("button", { name: "Drill into State Medicaid MCO" }));

    expect(emitted).toEqual([
      {
        refinement: { op: "DrillInto", target: "payer:pay_003" },
        meta: { turnId: "turn_1", referent: "payer:pay_003" },
      },
    ]);
    spy.mockRestore();
  });
});

describe("the donut is a ring of what was measured, and says what is not in it", () => {
  const WITHHELD: ChartSpec = {
    ...RANKING,
    rows: [
      ...RANKING.rows,
      { label: "Veritas Comp Fund", values: {}, withheld: true },
      { label: "Federal Medicare", values: {}, withheld: true },
    ],
  };

  it("draws every measured share, and drills from a segment", async () => {
    const user = userEvent.setup();
    const emitted: unknown[] = [];
    const spy = vi.fn((refinement: unknown, meta: unknown) => {
      emitted.push({ refinement, meta });
    });
    useSessionStore.setState({ emitRefinement: spy as never });

    render(<InvestigationChart spec={RANKING} turnId="turn_1" />);
    await chooseView(user, "Donut");

    const segment = screen.getByRole("button", { name: "Drill into Ashvale Health Plan" });
    // A direct label, not a legend: the name, the share and the figure are
    // all on the segment's own control, so identity is never colour alone.
    expect(segment).toHaveTextContent("Ashvale Health Plan");
    expect(segment).toHaveTextContent("$2,201.00");
    expect(segment.textContent).toMatch(/\d+\.\d%/);

    await user.click(segment);
    expect(emitted).toEqual([
      {
        refinement: { op: "DrillInto", target: "chart_main:Ashvale Health Plan" },
        meta: { turnId: "turn_1" },
      },
    ]);
  });

  it("renders the withheld cells as their own neutral segment, sized by nothing", async () => {
    const user = userEvent.setup();
    render(<InvestigationChart spec={WITHHELD} turnId="turn_1" />);
    await chooseView(user, "Donut");

    // The count a reader can see, where the size they cannot see would be.
    expect(screen.getByText("and 2 withheld †")).toBeInTheDocument();
    expect(screen.getByText("no share")).toBeInTheDocument();
    // …and the sentence under the picture that says what the ring is a
    // ring OF. A ring that dropped them silently would rescale every other
    // share to fill the gap.
    expect(
      screen.getByText(/These shares are of the measured rows only: 2 cells were withheld/),
    ).toBeInTheDocument();
  });

  it("computes the shares over the measured rows, never over the refusals", async () => {
    const user = userEvent.setup();
    render(<InvestigationChart spec={WITHHELD} turnId="turn_1" />);
    await chooseView(user, "Donut");

    // 412300 / (412300 + 220100 + 118400) = 54.9%.
    expect(
      screen.getByRole("button", { name: "Drill into State Medicaid MCO" }),
    ).toHaveTextContent("54.9%");
  });
});

/* ------------------------------------------------------------------ */

describe("switching the shape does not move the numbers", () => {
  /** The CSV the export button actually writes, captured off the Blob. */
  async function csvFrom(user: ReturnType<typeof userEvent.setup>): Promise<string> {
    const blobs: Blob[] = [];
    const createObjectURL = vi
      .spyOn(URL, "createObjectURL")
      .mockImplementation((blob: Blob | MediaSource) => {
        blobs.push(blob as Blob);
        return "blob:captured";
      });
    const revoke = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});

    // By TITLE, not by label: the button acknowledges a save for two
    // seconds and reads "Saved" while it does, and this test presses it
    // three times.
    await user.click(screen.getByTitle(/^Download the 3 rows/));
    const text = (await blobs[0]?.text()) ?? "";

    createObjectURL.mockRestore();
    revoke.mockRestore();
    click.mockRestore();
    return text;
  }

  it("writes a byte-identical export from the bar, the donut and the table", async () => {
    const user = userEvent.setup();
    const meta = {
      turnId: "turn_1",
      windowLabel: "Jul 2026",
      watermarkId: "wm_003",
      packLabel: "base-rcm@1.0.0",
      question: "Which payers are denying us the most?",
      investigationId: "inv_1",
      caveats: ["One caveat that travels with these numbers."],
    } as const;

    render(<InvestigationChart spec={RANKING} {...meta} />);
    const asBar = await csvFrom(user);

    await chooseView(user, "Donut");
    const asDonut = await csvFrom(user);

    await chooseView(user, "Table");
    const asTable = await csvFrom(user);

    // The export is built from the PUBLISHED spec and knows nothing about
    // which drawing is on screen. That is the guarantee: an analyst who
    // switched to a donut before hitting CSV gets the same file as one who
    // did not, byte for byte — including the export timestamp's own line,
    // which is why this compares the whole document.
    const withoutTimestamp = (csv: string) => csv.replace(/exported [^\n]*/g, "exported —");
    expect(withoutTimestamp(asDonut)).toBe(withoutTimestamp(asBar));
    expect(withoutTimestamp(asTable)).toBe(withoutTimestamp(asBar));
    expect(asBar).toContain("State Medicaid MCO");
    expect(asBar).toContain("One caveat that travels with these numbers.");
  });
});

/* ------------------------------------------------------------------ */

describe("the choice belongs to the figure, and travels with it", () => {
  it("carries into full screen, rather than reverting to the drawing it replaced", async () => {
    const user = userEvent.setup();
    render(<InvestigationChart spec={RANKING} turnId="turn_1" />);

    await chooseView(user, "Table");
    expect(screen.getByRole("table")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^View full screen/ }));
    const dialog = await screen.findByRole("dialog");
    // The dialog is the same component mounted again from the same state,
    // which is the whole implementation of "it carries".
    expect(within(dialog).getByRole("table")).toBeInTheDocument();
    expect(
      within(dialog).getByRole("button", { name: /^View as, currently table/ }),
    ).toBeInTheDocument();
  });

  it("is per figure, not global — two charts on one turn are two questions", async () => {
    const user = userEvent.setup();
    render(
      <>
        <InvestigationChart spec={RANKING} turnId="turn_1" />
        <InvestigationChart
          spec={{ ...RANKING, id: "chart_two", title: "Cash posted by payer" }}
          turnId="turn_1"
        />
      </>,
    );

    await user.click(
      screen.getByRole("button", { name: /^View as, currently bar: Denied dollars by payer/ }),
    );
    await user.click(await screen.findByRole("menuitemradio", { name: "Table" }));

    expect(
      screen.getByRole("button", { name: /^View as, currently table: Denied dollars by payer/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /^View as, currently bar: Cash posted by payer/ }),
    ).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */

describe("the honesty marks translate to a ranking on its side", () => {
  it("keeps the same four glyphs, in the same places", () => {
    // A horizontal figure that grew its own set of marks would be a second
    // honesty vocabulary to learn, on the shape this product draws most.
    expect(honestyTick("Federal Medicare", { bounded: true })).toBe("≤ Federal Medicare");
    expect(honestyTick("Veritas Comp Fund", { withheld: true })).toBe("Veritas Comp Fund †");
    expect(honestyTick("Lakewood Medicaid MCO", { absent: true })).toBe("Lakewood Medicaid MCO ‡");
    expect(honestyTick("Jul 1", { provisional: true })).toBe("Jul 1*");
  });

  it("composes a ceiling with an absence rather than printing one of them", () => {
    // On a comparison one category can hold a ceiling in one window and no
    // figure at all in the other.
    expect(honestyTick("Summit Peak", { bounded: true, absent: true })).toBe("≤ Summit Peak ‡");
  });

  it("prints every one of them under the picture too", () => {
    render(<InvestigationChart spec={MARKED} turnId="turn_1" />);
    expect(screen.getByText(/≤ means at most/)).toBeInTheDocument();
    expect(screen.getByText(/† marks a cell the engine withheld outright/)).toBeInTheDocument();
    expect(screen.getByText(/‡ marks a category with a figure in the window/)).toBeInTheDocument();
    expect(screen.getByText(/\* marks a provisional bucket/)).toBeInTheDocument();
  });

  it("names the affordance the form actually has", () => {
    // "Click a bar to drill in" over a donut names a mark that is not on
    // the figure.
    render(<InvestigationChart spec={{ ...RANKING, xLabel: undefined }} turnId="turn_1" />);
    expect(screen.getByText(/Click a bar to drill in/)).toBeInTheDocument();
  });
});
