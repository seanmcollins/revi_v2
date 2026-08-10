/**
 * The governed conversation→worklist bridge, against the LIVE payload.
 *
 * Every fixture below is trimmed from a real turn: `POST
 * /v1/sessions/{sid}/turns` with "what should my denial team work on first
 * this week", which routes to the governed concept `work_prioritization`
 * and comes back with `worklist` beside its findings (8 of 33 cards, 2
 * lanes, `anomaly_priority@3`) and a `WORKLIST_ATTACHED` warning.
 *
 * The gap this closes: that question used to return a clarification
 * offering four ranking bases, none of which was the 33-card list with its
 * lanes, recoverable estimates and reconciliation state. The portfolio was
 * never mentioned — two products in one shell.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { AnswerWorklist } from "@/components/worklist/AnswerWorklist";
import { TooltipProvider } from "@/components/ui/tooltip";
import { mapWorklist, type WorklistData } from "@/lib/contract";
import { useSessionStore } from "@/lib/store";

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  } as unknown as typeof ResizeObserver;
});

/** ANM-021 — live rank 1: not_comparable, ranked on the detector's figure. */
const CARD_NOT_COMPARABLE = {
  anomaly_id: "ANM-021",
  provenance: "external_detection",
  priority_formula_version: "anomaly_priority@3",
  source_watermark_id: "wm_003",
  title: "DNFB accumulation: Northgate general-surgery discharges",
  description: "22 unbilled discharges totaling $178,217.",
  category: "dnfb",
  metric_id: "dnfb_dollars",
  severity: "critical",
  lane: "value",
  impact_cents: 17_821_682,
  reconciled_impact_cents: 19_587_392,
  impact_agreement: "not_comparable",
  impact_delta_fraction: null,
  ranked_on: "not_comparable",
  ranked_impact_cents: 17_821_682,
  ranked_on_note:
    "ranked on the detection system's figure ($178,216.82): this platform's re-derivation is not a comparable quantity (an as-of balance against a windowed flow), so substituting it would change the claim rather than correct it.",
  recoverable_cents_estimate: 16_930_598,
  actionability_label: "highly recoverable",
  priority_score: 0.328589,
  drill_spec: { metric_ids: ["dnfb_dollars"] },
  drillable: true,
};

/** ANM-001 — live rank 2: diverged, and ordered on THIS platform's figure. */
const CARD_RANKED_ON_PLATFORM = {
  ...CARD_NOT_COMPARABLE,
  anomaly_id: "ANM-001",
  title: "Medical-necessity denial spike: Summit Peak MA Cardiology",
  metric_id: "denied_dollars",
  impact_cents: 17_064_300,
  reconciled_impact_cents: 17_720_287,
  impact_agreement: "diverged",
  impact_delta_fraction: 0.0384,
  ranked_on: "platform",
  ranked_impact_cents: 17_720_287,
  ranked_on_note:
    "ranked on this platform's re-derived figure ($177,202.87): the detection system's figure diverges from it.",
  recoverable_cents_estimate: 17_064_300,
};

/** ANM-015 — a card the platform refuses to open, with its own reason. */
const CARD_REFUSED = {
  ...CARD_NOT_COMPARABLE,
  anomaly_id: "ANM-015",
  title: "Gross collection rate dip: Meridian imaging",
  lane: "compliance",
  impact_cents: 6_355_160,
  ranked_on: "detector",
  ranked_impact_cents: 6_355_160,
  ranked_on_note: "",
  impact_agreement: "agreed",
  reconciled_impact_cents: 6_355_160,
  // Whole impact recoverable — so the row prints no separate recoverable
  // line, exactly as the rail's card does when the two numbers agree.
  recoverable_cents_estimate: 6_355_160,
  drill_spec: undefined,
  drillable: false,
  drill_unavailable_reason:
    "GRAIN_INCOMPATIBLE: dimension 'proc_group' is not a legal scope dimension for ratio metric 'gross_collection_rate'",
};

const LIVE_WORKLIST = {
  matched_on: "concept",
  matched_id: "work_prioritization",
  label: "What to work first",
  description: "The ranked anomaly worklist at this watermark.",
  formula_version: "anomaly_priority@3",
  watermark_id: "wm_003",
  tenant: "demo",
  statement:
    "8 of 33 ranked cards at watermark wm_003, highest governed priority first. This is the detection feed's ranked work, not a measurement of the question asked above; the findings on this answer are that.",
  items: [CARD_NOT_COMPARABLE, CARD_RANKED_ON_PLATFORM, CARD_REFUSED],
  lanes: [
    {
      id: "compliance",
      label: "Must do regardless of size",
      description: "Compliance-mandatory categories, worked because the rule says so.",
      anomaly_ids: ["ANM-023", "ANM-024", "ANM-015"],
      item_count: 2,
      impact_cents: 4_976_298,
    },
    {
      id: "value",
      label: "Ranked by value recoverable",
      description: "Ordered by the governed priority formula.",
      anomaly_ids: ["ANM-021", "ANM-001"],
      item_count: 31,
      impact_cents: 169_985_085,
    },
  ],
  total_items: 33,
  limit: 8,
  total_recoverable_cents_estimate: 83_050_193,
  warnings_v2: [
    {
      code: "PORTFOLIO_RANKED_ON_PLATFORM",
      severity: "caution",
      message:
        "9 of 33 cards are ranked on this platform's re-derived figure rather than the detection system's, because the two diverge (anomaly_priority@3).",
      count: 1,
    },
  ],
};

const WORKLIST_ATTACHED = {
  code: "WORKLIST_ATTACHED",
  severity: "info" as const,
  message:
    "worklist_attached: this answer also carries the ranked anomaly worklist (8 of 33 cards), attached because the governed concept 'work_prioritization' routed it. The cards are the detection feed's, ordered by anomaly_priority@3; they are not findings this turn computed.",
  count: 1,
};

function parse(raw: unknown): WorklistData {
  const value = mapWorklist(raw);
  if (!value) throw new Error("live worklist failed contract validation");
  return value;
}

function renderWorklist(worklist: WorklistData, intro?: typeof WORKLIST_ATTACHED) {
  return render(
    <TooltipProvider>
      <AnswerWorklist worklist={worklist} {...(intro ? { intro } : {})} />
    </TooltipProvider>,
  );
}

describe("AnswerWorklist — the ranked list, inside a conversation", () => {
  beforeEach(() => {
    useSessionStore.getState().reset();
    useSessionStore.setState({ streamingTurnId: null });
  });

  afterEach(() => cleanup());

  it("parses the live payload without losing the page/population distinction", () => {
    const worklist = parse(LIVE_WORKLIST);
    expect(worklist.matchedOn).toBe("concept");
    expect(worklist.matchedId).toBe("work_prioritization");
    // `items` is a PAGE; `lanes` describe the WHOLE population. A renderer
    // that trusted `lane.itemCount` as its row count would invent rows.
    expect(worklist.items).toHaveLength(3);
    expect(worklist.totalItems).toBe(33);
    expect(worklist.lanes.find((l) => l.id === "value")?.itemCount).toBe(31);
  });

  it("leads with the sentence saying these are not this turn's findings", () => {
    renderWorklist(parse(LIVE_WORKLIST), WORKLIST_ATTACHED);
    // The machine prefix is stripped (it travels in `code`), the sentence
    // is not.
    expect(screen.getByText(/they are not findings this turn computed/)).toBeInTheDocument();
    expect(screen.queryByText(/^worklist_attached:/)).not.toBeInTheDocument();
  });

  it("renders each card with the figure that actually ranked it", () => {
    renderWorklist(parse(LIVE_WORKLIST));
    const text = document.body.textContent ?? "";
    // ANM-001 is ordered on this platform's $177,202.87, NOT the
    // detector's $170,643 printed on the rail's version of the same card.
    expect(text).toContain("$177,203");
    expect(text).toContain("Ranked on this platform's figure");
    // ANM-021's is the detector's, and the reason is not a divergence.
    expect(text).toContain("$178,217");
    expect(text).toContain("not comparable");
  });

  it("shows what of each card is recoverable, not just the headline", () => {
    renderWorklist(parse(LIVE_WORKLIST));
    // $169,306 of ANM-021's $178,217 — the number that decides whether
    // this is worth a morning.
    expect(screen.getByText(/\$169,306 recoverable/)).toBeInTheDocument();
  });

  it("refuses to open a card the platform refused, in the platform's own words", async () => {
    renderWorklist(parse(LIVE_WORKLIST));
    const refused = screen.getByRole("button", {
      name: /Cannot drill into Gross collection rate dip/,
    });
    // `aria-disabled`, not `disabled`: the refusal is the control's whole
    // content and must stay reachable from a keyboard.
    expect(refused).toHaveAttribute("aria-disabled", "true");
    expect(refused).not.toBeDisabled();
    expect(refused.getAttribute("aria-label")).toContain("GRAIN_INCOMPATIBLE");
  });

  it("opens a drillable card as a typed FIRST turn carrying its anomaly ref", async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ submit });
    renderWorklist(parse(LIVE_WORKLIST));

    await userEvent.click(
      screen.getByRole("button", { name: /Drill into DNFB accumulation/ }),
    );

    // A card is not a refinement of whatever this answer was about: it
    // opens its own investigation, and `anomalyRef` is what makes the
    // server reconcile the card's figure against the answer's.
    expect(submit).toHaveBeenCalledWith({
      spec: { metric_ids: ["dnfb_dollars"] },
      anomalyRef: "ANM-021",
    });
  });

  it("re-queries one lane through the typed worklist handle — no natural language", async () => {
    const submit = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({ submit });
    renderWorklist(parse(LIVE_WORKLIST));

    await userEvent.click(screen.getByRole("button", { name: "Ranked by value recoverable" }));

    // `TurnRequest.worklist` is additive by contract: the turn runs as it
    // would have and the list rides alongside, so this is a re-query of
    // the LIST rather than a refinement of the answer above it.
    expect(submit).toHaveBeenCalledWith({ worklist: { lane: "value", limit: 8 } });
  });

  it("states the page it is showing rather than implying it is the whole list", () => {
    renderWorklist(parse(LIVE_WORKLIST));
    const footer = screen.getByText(/3 of 33 ranked cards/);
    expect(footer).toBeInTheDocument();
    expect(screen.getByText(/\$830,502 estimated recoverable/)).toBeInTheDocument();
    // And it names the data load by DATE or not at all. This line used to
    // read "…ranked cards at data load wm_003" — a log token in a sentence
    // a director reads. The id stays on the title for whoever has to
    // reproduce the query.
    expect(footer.textContent).not.toMatch(/wm_\d/);
    expect(footer).toHaveAttribute("title", "Data load wm_003");
  });

  it("carries the list's own caveats about the population", () => {
    renderWorklist(parse(LIVE_WORKLIST));
    expect(
      screen.getByText(/9 of 33 cards are ranked on this platform's re-derived figure/),
    ).toBeInTheDocument();
  });

  it("offers no lane chips when the page shows only one lane", () => {
    const single = parse({
      ...LIVE_WORKLIST,
      items: [CARD_NOT_COMPARABLE, CARD_RANKED_ON_PLATFORM],
    });
    renderWorklist(single);
    // A chip for a lane with nothing on screen filters to an empty list
    // and reads as a bug; a single-lane page needs no filter at all.
    expect(
      screen.queryByRole("button", { name: "Must do regardless of size" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Ranked by value recoverable" }),
    ).not.toBeInTheDocument();
  });

  it("stays silent about the ranking basis on an ordinary card", () => {
    // No worklist-level warnings either, so the only "ranked on" that
    // could appear would be the per-row basis line.
    const ordinary = parse({ ...LIVE_WORKLIST, items: [CARD_REFUSED], warnings_v2: [] });
    renderWorklist(ordinary);
    // `detector` + `agreed` is the default reading, already carried by the
    // figure printed beside it. A line restating it on the majority of
    // rows would bury the ones where the basis actually differs.
    expect(document.body.textContent ?? "").not.toMatch(/ranked on/);
    expect(document.body.textContent ?? "").not.toMatch(/re-derived/);
  });

  it("refuses a payload with no governed routing rather than rendering a guess", () => {
    expect(mapWorklist({ ...LIVE_WORKLIST, matched_on: "vibes" })).toBeUndefined();
    expect(mapWorklist(null)).toBeUndefined();
  });
});
