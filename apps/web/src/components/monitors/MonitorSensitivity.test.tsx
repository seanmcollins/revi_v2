/**
 * THE SENSITIVITY FORM'S WORDS — pinned, because the words were the defect.
 *
 * Two owner reactions to the live control are the reason this file exists.
 * On the default option, which named the gate rather than stating it:
 * "what the fuck does that even mean? That sounds like nonsense to me" —
 * and on the rename that called it "the standard threshold", a second and
 * worse failure: "I would never trust that." On the direction chips
 * ("either way / only up / only down"): "lowercase and amateur hour."
 *
 * Copy regresses more quietly than logic does: nothing throws when a
 * sentence goes back to naming a threshold nobody can see, and nothing
 * fails when a wire value leaks into a chip. So the assertions here are
 * EXACT STRINGS for the eleven control labels a reader meets, plus a
 * scanner over the rendered text that fails on the vocabulary this surface
 * is not allowed to use. The last one is the point: it catches the words
 * nobody thought to test.
 *
 * The wire is asserted alongside the words, in the same file, because the
 * whole risk of a copy pass is that somebody "fixes" a label by changing
 * the value under it.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MemoryRouter } from "react-router-dom";

import {
  MonitorSensitivityForm,
  recommendedRuleLabel,
} from "@/components/monitors/MonitorSensitivity";
import { TooltipProvider } from "@/components/ui/tooltip";

function draw(node: React.ReactNode) {
  return render(
    <MemoryRouter>
      <TooltipProvider>{node}</TooltipProvider>
    </MemoryRouter>,
  );
}

afterEach(cleanup);

/** The form as both call sites mount it: no recommendation, no metric noun. */
function plainForm(props: Partial<React.ComponentProps<typeof MonitorSensitivityForm>> = {}) {
  return draw(
    <MonitorSensitivityForm
      submitLabel="Start monitoring"
      pending={false}
      onSubmit={() => {}}
      onCancel={() => {}}
      {...props}
    />,
  );
}

/**
 * Each option's visible label beside the wire value it submits — so a
 * reworded label that quietly moves onto a different mode fails here
 * rather than in somebody's morning.
 */
function optionRows(container: HTMLElement): Array<{ mode: string; label: string }> {
  return Array.from(container.querySelectorAll<HTMLInputElement>('input[name="monitor-mode"]')).map(
    (input) => ({
      mode: input.value,
      label: input.closest("label")?.querySelector("span > span")?.textContent ?? "",
    }),
  );
}

/* ------------------------------------------------------------------ */
/* The default option states the rule, or says less                     */
/* ------------------------------------------------------------------ */

describe("the recommended option states the rule instead of naming a threshold", () => {
  it("renders the number and its unit when a recommended level is published", () => {
    expect(recommendedRuleLabel({ value: 0.5, unit: "points" })).toBe(
      "Tell me when it moves more than 0.5 points",
    );
    const { container } = plainForm({ recommended: { value: 0.5, unit: "points" } });
    expect(screen.getByText("Tell me when it moves more than 0.5 points")).toBeInTheDocument();
    expect(optionRows(container)[0]).toEqual({
      mode: "governed_default",
      label: "Tell me when it moves more than 0.5 points",
    });
  });

  it("says one point rather than 1 points", () => {
    // A plural seam in the one sentence that carries the number is enough
    // to make a reader stop trusting the number.
    expect(recommendedRuleLabel({ value: 1, unit: "points" })).toBe(
      "Tell me when it moves more than 1 point",
    );
    plainForm({ recommended: { value: 1, unit: "points" } });
    expect(screen.getByText("Tell me when it moves more than 1 point")).toBeInTheDocument();
  });

  it("reads each unit the way that measure is actually kept", () => {
    // `points`, `cents` and `days` are three different questions about the
    // same-looking number. A level rendered in the wrong one is a monitor
    // that fires on the wrong thing.
    expect(recommendedRuleLabel({ value: 100000, unit: "cents" })).toBe(
      "Tell me when it moves more than $1,000",
    );
    expect(recommendedRuleLabel({ value: 2, unit: "days" })).toBe(
      "Tell me when it moves more than 2 days",
    );
    expect(recommendedRuleLabel({ value: 1, unit: "days" })).toBe(
      "Tell me when it moves more than 1 day",
    );
    expect(recommendedRuleLabel({ value: 5, unit: "relative_pct" })).toBe(
      "Tell me when it moves more than 5% of the current value",
    );
  });

  it("invents no number when the wire publishes none", () => {
    // Every metric renders this branch today: no endpoint carries a
    // structured recommended value, and the gate that exists lives inside
    // a caption this client does not parse.
    expect(recommendedRuleLabel()).toBe("Tell me about meaningful changes");
    const { container } = plainForm();
    expect(container.textContent).not.toMatch(/\d/);
  });

  it("names whose recommendation it is, and that it is not binding", () => {
    plainForm({ recommended: { value: 0.5, unit: "points" }, metricLabel: "denial rate" });
    expect(
      screen.getByText("Revi's recommended level for denial rates. You can change it anytime."),
    ).toBeInTheDocument();
  });

  it("falls back to the vaguer, truer noun with no metric supplied", () => {
    plainForm();
    expect(
      screen.getByText("Revi's recommended level for this metric. You can change it anytime."),
    ).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* The four options, exactly                                            */
/* ------------------------------------------------------------------ */

describe("the four options are sentences a reader says, bound to their wire modes", () => {
  it("renders the four labels exactly, in order, on the modes they submit", () => {
    const { container } = plainForm();
    expect(optionRows(container)).toEqual([
      { mode: "governed_default", label: "Tell me about meaningful changes" },
      { mode: "any_movement", label: "Tell me about any movement" },
      { mode: "delta_gte", label: "Set my own level…" },
      { mode: "crosses", label: "Tell me when it crosses a level…" },
    ]);
  });

  it("says in plain words that a crossing is measured against a level", () => {
    // It is the one mode whose reference is not the previous reading, so
    // the same movement can brief one day and not the next.
    plainForm();
    expect(
      screen.getByText("Measured against the level you set, not against what it read last time."),
    ).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* Direction, without a judgement attached                              */
/* ------------------------------------------------------------------ */

describe("the direction chips read as directions, in the reader's words", () => {
  it("gives all three the accessible names an owner would read aloud", () => {
    plainForm();
    for (const name of ["Any direction", "Only when it rises", "Only when it falls"]) {
      expect(screen.getByRole("button", { name })).toBeInTheDocument();
    }
  });

  it("says only what is watched, never whether it would be bad news", () => {
    // A rising denial rate is bad; rising collections are good; this form
    // does not know which measure it sits on. Any good/bad framing here is
    // wrong on half the product.
    const { container } = plainForm();
    const chips = Array.from(container.querySelectorAll("button")).map((b) => b.textContent ?? "");
    expect(chips.join(" ")).not.toMatch(/\b(worse|worsen|better|improve|bad|good|rises? above)\b/i);
  });
});

/* ------------------------------------------------------------------ */
/* The jargon guard                                                     */
/* ------------------------------------------------------------------ */

/**
 * Platform vocabulary. Every one of these is a word a first-time reader
 * has to have been TOLD, and every one of them has been on this surface.
 */
const JARGON = ["pack", "governed", "materiality", "watch mode", "threshold_source", "delta"];

/**
 * Wire values, which must never reach a reader as themselves.
 *
 * Checked per ELEMENT rather than across the prose, because half of them
 * are ordinary English in a sentence — "Any direction", "0.5 points", "it
 * crosses a level" are all fine, and all three would trip a word-boundary
 * scan. What is banned is the token standing alone as a label, which is
 * exactly what "either way / only up / only down" was.
 */
const WIRE_TOKENS = [
  "both",
  "any",
  "up",
  "down",
  "governed_default",
  "any_movement",
  "delta_gte",
  "crosses",
  "points",
  "relative_pct",
  "cents",
  "days",
];

function vocabularyHits(container: HTMLElement): string[] {
  const text = container.textContent ?? "";
  const hits = JARGON.filter((word) => new RegExp(`\\b${word}\\b`, "i").test(text));
  for (const node of Array.from(container.querySelectorAll("*"))) {
    const own = (node.textContent ?? "").trim().toLowerCase();
    if (WIRE_TOKENS.includes(own)) hits.push(own);
  }
  return hits;
}

describe("no platform vocabulary reaches the reader", () => {
  it("renders none of it in the state every monitor opens in", () => {
    const { container } = plainForm();
    expect(vocabularyHits(container)).toEqual([]);
  });

  it("renders none of it once the number and unit controls open", () => {
    // The unit picker is the densest place for a machine token to survive:
    // its four options were lowercase wire values until this pass.
    const { container } = plainForm();
    fireEvent.click(screen.getByRole("radio", { name: /Set my own level/ }));
    expect(screen.getByLabelText("How far it has to move")).toBeInTheDocument();
    expect(vocabularyHits(container)).toEqual([]);
  });

  it("opens every control label with a capital letter", () => {
    // An owner mandate, and control labels are the part that keeps being
    // exempted: option text, chip labels and legends are sentences too.
    const { container } = plainForm();
    fireEvent.click(screen.getByRole("radio", { name: /Set my own level/ }));
    const labels = Array.from(container.querySelectorAll("option, legend, button")).map(
      (node) => (node.textContent ?? "").trim(),
    );
    expect(labels.length).toBeGreaterThan(0);
    for (const label of labels) {
      expect(label, `"${label}" opens in lower case`).not.toMatch(/^[a-z]/);
    }
  });
});

/* ------------------------------------------------------------------ */
/* The wire, untouched by the rewording                                 */
/* ------------------------------------------------------------------ */

describe("the rewording changed no value this form submits", () => {
  it("still emits the mode, the number and the unit the analyst chose", () => {
    const onSubmit = vi.fn();
    draw(
      <MonitorSensitivityForm
        submitLabel="Start monitoring"
        pending={false}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );

    // Hidden until the mode that needs it is chosen — the calm default is
    // the common case and it asks for nothing.
    expect(screen.queryByLabelText("How far it has to move")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("radio", { name: /Set my own level/ }));
    fireEvent.change(screen.getByLabelText("How far it has to move"), {
      target: { value: "0.75" },
    });
    fireEvent.change(screen.getByLabelText("The unit that number is stated in"), {
      target: { value: "cents" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start monitoring" }));

    expect(onSubmit).toHaveBeenCalledWith({
      mode: "delta_gte",
      value: 0.75,
      unit: "cents",
      direction: "any",
      note: "",
    });
  });

  it("still submits the quiet default with no threshold attached to it", () => {
    const onSubmit = vi.fn();
    draw(
      <MonitorSensitivityForm
        submitLabel="Start monitoring"
        pending={false}
        recommended={{ value: 0.5, unit: "points" }}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Start monitoring" }));
    // A recommendation the reader can SEE is still not a threshold the
    // client sends: the number rides in the label, never onto the wire.
    expect(onSubmit).toHaveBeenCalledWith({
      mode: "governed_default",
      direction: "any",
      note: "",
    });
  });

  it("keeps the direction chips on their wire values", () => {
    const onSubmit = vi.fn();
    draw(
      <MonitorSensitivityForm
        submitLabel="Start monitoring"
        pending={false}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Only when it falls" }));
    fireEvent.click(screen.getByRole("button", { name: "Start monitoring" }));
    expect(onSubmit).toHaveBeenCalledWith({
      mode: "governed_default",
      direction: "down",
      note: "",
    });
  });
});
