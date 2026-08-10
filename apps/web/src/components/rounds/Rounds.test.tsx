/**
 * ROUNDS — against a captured live surface.
 *
 * Every payload read here came off a running deployment
 * (`scripts/capture-fixtures.mjs rounds`, wm_003, Postgres stores, real
 * warehouse): the brief with its four entries, four evaluated tiles, the
 * pins behind them, one lead with a real lifecycle, and the portfolio
 * cards that carry the two new fields. Nothing in this file is composed —
 * a Rounds test written against a hand-made tile would assert a shape the
 * product does not produce, on the one surface nobody is watching while it
 * renders.
 *
 * The centrepiece is the CONSERVATION DISCIPLINE. On an answer, the
 * honesty marks can lean on the conversation around them; on a tile there
 * is no asker in the room, and whatever number it shows is the number
 * somebody repeats in a huddle. So: no tile without its integrity atom —
 * asserted at the parser (a payload missing it is dropped and reported)
 * and at the renderer (every live tile draws its grade and its caveat
 * count), because either one alone leaves the other free to break.
 */

import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { BriefPanel } from "@/components/rounds/BriefPanel";
import { BriefEntryRow } from "@/components/rounds/BriefEntryRow";
import { LeadStatusControl } from "@/components/rounds/LeadStatus";
import { TimeToImpactLine } from "@/components/rounds/TimeToImpactLine";
import { WatchThis } from "@/components/rounds/WatchThis";
import { WatchTile } from "@/components/rounds/WatchTile";
import { TooltipProvider } from "@/components/ui/tooltip";
import live from "@/lib/__fixtures__/live-rounds.json";
import { mapTimeToImpact, parsePortfolioSnapshot } from "@/lib/contract";
import { mapLeadState, mapRoundsPin, parseBrief, parseRounds } from "@/lib/rounds";
import { useSessionStore } from "@/lib/store";

function draw(node: React.ReactNode) {
  return render(<TooltipProvider>{node}</TooltipProvider>);
}

const ROUNDS = parseRounds(live.rounds);
const BRIEF = parseBrief(live.brief);

beforeEach(() => {
  useSessionStore.setState({
    driver: null,
    watches: {},
    knownWatches: [],
    watchesLoaded: false,
    watchesLoading: false,
    watchPendingKey: null,
    watchError: null,
    leadStates: {},
    leadPendingId: null,
    leadError: null,
  });
});

afterEach(cleanup);

/* ------------------------------------------------------------------ */
/* The conservation rule                                               */
/* ------------------------------------------------------------------ */

describe("no tile without its integrity atom", () => {
  it("parses every live tile, atom and all", () => {
    expect(ROUNDS.drift).toEqual([]);
    expect(ROUNDS.value?.tiles.length).toBe(live.rounds.tiles.length);
    for (const tile of ROUNDS.value?.tiles ?? []) {
      expect(tile.integrity.grade).toBeTruthy();
      expect(tile.integrity.caveatCodes.length).toBe(tile.integrity.thingsToKnow);
    }
  });

  it("DROPS a tile whose payload lost its integrity block, and says so", () => {
    // Not "renders it without marks". A value drawn with no grade and no
    // caveat count is the one output this surface may not produce, so
    // there is no code path that produces it: the tile does not exist and
    // the missing field is reported as drift like any other.
    const stripped = {
      ...live.rounds,
      tiles: live.rounds.tiles.map((tile, index) =>
        index === 0 ? Object.fromEntries(Object.entries(tile).filter(([k]) => k !== "integrity")) : tile,
      ),
    };
    const parsed = parseRounds(stripped);
    expect(parsed.drift).toContain("tiles[0].integrity");
    expect(parsed.value?.tiles.length).toBe(live.rounds.tiles.length - 1);
  });

  it("drops a tile whose grade is not a grade this client knows", () => {
    const bogus = {
      ...live.rounds,
      tiles: live.rounds.tiles.map((tile, index) =>
        index === 0 ? { ...tile, integrity: { ...tile.integrity, grade: "excellent" } } : tile,
      ),
    };
    expect(parseRounds(bogus).drift).toContain("tiles[0].integrity");
  });

  it("draws the grade and the caveat count on EVERY live tile", () => {
    for (const tile of ROUNDS.value?.tiles ?? []) {
      const { container, unmount } = draw(<WatchTile tile={tile} />);
      const atom = container.querySelector("[data-integrity-atom]");
      expect(atom, `${tile.label} must carry its integrity atom`).not.toBeNull();
      expect(atom).toHaveAttribute("data-answer-grade", tile.integrity.grade);
      expect(atom?.textContent).toContain(`${tile.integrity.thingsToKnow} things to know`);
      unmount();
    }
  });

  it("says a still-settling figure is still settling, in words", () => {
    const provisional = (ROUNDS.value?.tiles ?? []).find((t) => t.integrity.provisional);
    expect(provisional, "the live capture must contain a provisional tile").toBeDefined();
    draw(<WatchTile tile={provisional!} />);
    // A word, not a shade: a provisional figure that carried its
    // uncertainty only in a lighter grey is a figure somebody quotes.
    expect(screen.getByText("still settling")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* Deltas keep the metric's own unit                                   */
/* ------------------------------------------------------------------ */

describe("a movement is stated in the metric's own unit", () => {
  const rateTile = () =>
    (ROUNDS.value?.tiles ?? []).find((t) => t.delta?.unit === "ratio" && t.delta.delta !== 0)!;

  it("renders a rate's movement in POINTS, as the server rendered it", () => {
    const tile = rateTile();
    expect(tile).toBeDefined();
    draw(<WatchTile tile={tile} />);
    expect(screen.getAllByText(/3\.6 points/).length).toBeGreaterThan(0);
    // Never a percentage. 0.035823 rendered as "+3.6%" is a number a
    // reader cannot tell from a relative change, which is the single most
    // common way a denial-rate figure lies.
    expect(screen.queryByText(/3\.6%/)).not.toBeInTheDocument();
  });

  it("says a same-window change is the data catching up, not a movement", () => {
    const tile = rateTile();
    const { container } = draw(<WatchTile tile={tile} />);
    expect(screen.getAllByText("same period, re-measured").length).toBeGreaterThan(0);
    // And it is not drawn with a direction arrow: an arrow is a claim
    // about the world, and this is a claim about adjudication run-out.
    const line = container.querySelector('[data-same-window="true"]');
    expect(line).not.toBeNull();
  });

  it("shows the baseline movement when it says something the prior load does not", () => {
    const tile = rateTile();
    draw(<WatchTile tile={tile} />);
    expect(screen.getByText(/since you started watching/)).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* The brief                                                            */
/* ------------------------------------------------------------------ */

describe("the brief is a list of sentences, not of metrics", () => {
  it("parses the live brief with no drift", () => {
    expect(BRIEF.drift).toEqual([]);
    expect(BRIEF.value?.status).toBe("material_changes");
    expect(BRIEF.value?.entries.length).toBe(live.brief.entries.length);
  });

  it("renders every entry's statement verbatim", () => {
    draw(<BriefPanel brief={BRIEF.value!} />);
    for (const entry of BRIEF.value?.entries ?? []) {
      expect(
        screen.getByText(entry.statement),
        `the brief must print ${entry.kind} verbatim`,
      ).toBeInTheDocument();
    }
  });

  it("renders the entries in the SERVER's order and re-sorts nothing", () => {
    // Time-to-impact is published CONTEXT, never a ranking input:
    // `anomaly_priority@3` orders this list and no surface may reorder it.
    // Two of these entries carry a cash lane and the first has none, so a
    // client that sorted by urgency would visibly move them.
    const { container } = draw(<BriefPanel brief={BRIEF.value!} />);
    const rendered = [...container.querySelectorAll("[data-brief-entry]")].map((el) =>
      el.getAttribute("data-brief-entry"),
    );
    expect(rendered).toEqual((BRIEF.value?.entries ?? []).map((e) => e.kind));
  });

  it("counts what the gate held back rather than hiding it", () => {
    // On its own line, not only inside the headline (the server puts the
    // same clause in both). Suppressing a movement silently and
    // suppressing it visibly are different products.
    const { container } = draw(<BriefPanel brief={BRIEF.value!} />);
    const held = container.querySelector("[data-immaterial-summary]");
    expect(held).not.toBeNull();
    expect(held?.textContent).toContain(BRIEF.value!.immaterial.note);
  });

  it("publishes the work behind the brief", () => {
    draw(<BriefPanel brief={BRIEF.value!} />);
    expect(screen.getByText(/watches re-run/)).toBeInTheDocument();
  });

  it("makes 'nothing material changed' the loudest thing on the surface", () => {
    // The proud state: a quiet morning is the best outcome this product
    // has, and it is set at the size otherwise reserved for one headline
    // figure. A pale grey empty state would say the opposite.
    const quiet = {
      ...BRIEF.value!,
      status: "nothing_material" as const,
      entries: [],
      headline: "Revi walked 4 watches and 33 leads at wm_003. Nothing cleared the gate.",
    };
    const { container } = draw(<BriefPanel brief={quiet} />);
    const proud = screen.getByText("Nothing material changed.");
    expect(proud).toBeInTheDocument();
    expect(proud.className).toContain("text-figure");
    // With the counts that back it — otherwise it is indistinguishable
    // from a brief that did not run.
    expect(within(container).getByText(new RegExp(escapeRe(quiet.headline)))).toBeInTheDocument();
  });

  it("says a first load is a first load rather than briefing 33 'new' leads", () => {
    draw(
      <BriefPanel
        brief={{ ...BRIEF.value!, status: "first_load", entries: [], headline: "This is the first load." }}
      />,
    );
    expect(screen.getByText("First walk of your Rounds.")).toBeInTheDocument();
  });

  it("carries the honesty marks onto a briefed movement", () => {
    const movement = (BRIEF.value?.entries ?? []).find((e) => e.integrity !== undefined);
    expect(movement, "a pin movement must carry its integrity block").toBeDefined();
    const { container } = draw(<BriefEntryRow entry={movement!} />);
    expect(container.querySelector("[data-integrity-atom]")).not.toBeNull();
  });

  it("names which threshold briefed an entry", () => {
    const movement = (BRIEF.value?.entries ?? []).find((e) => e.delta?.thresholdSource === "watch");
    expect(movement).toBeDefined();
    draw(<BriefEntryRow entry={movement!} />);
    expect(screen.getByText("your threshold")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* Time to impact                                                       */
/* ------------------------------------------------------------------ */

describe("the cash lane is context, and says which kind it is", () => {
  const cards = parsePortfolioSnapshot({ items: live.cards }).value?.items ?? [];

  it("marks a projection provisional, in a word", () => {
    const projected = cards.find((c) => c.timeToImpact?.kind === "projected");
    expect(projected, "the live capture must contain a projected card").toBeDefined();
    draw(<TimeToImpactLine timeToImpact={projected!.timeToImpact!} />);
    expect(screen.getByText(/hits cash in ~\d+ days/)).toBeInTheDocument();
    // An estimate rendered like a filing limit is indistinguishable from
    // one on the screen beside it.
    expect(screen.getByText("provisional")).toBeInTheDocument();
  });

  it("says an already-landed card has landed, and names the window still open", () => {
    const hit = cards.find(
      (c) => c.timeToImpact?.kind === "already_hit" && c.timeToImpact.recoveryDays !== undefined,
    );
    expect(hit).toBeDefined();
    draw(<TimeToImpactLine timeToImpact={hit!.timeToImpact!} />);
    expect(screen.getByText(/already hit cash — appeal window closes in \d+ days/)).toBeInTheDocument();
    expect(screen.queryByText("provisional")).not.toBeInTheDocument();
  });

  it("renders an unknown lane with its reason rather than a blank", () => {
    const unknown = mapTimeToImpact({
      kind: "unknown",
      lane: "unknown",
      method: "Contractual allowance is the negotiated discount working as designed.",
      method_id: "unknown",
      provisional: false,
      reason: "Contractual allowance is the negotiated discount working as designed.",
      recovery_label: "",
    });
    draw(<TimeToImpactLine timeToImpact={unknown!} />);
    expect(screen.getByText("no cash date for this kind of card")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* The lead lifecycle                                                  */
/* ------------------------------------------------------------------ */

describe("a lead's status, and the two verdicts a person may not set", () => {
  it("reads the live lead record", () => {
    const lead = mapLeadState(live.lead);
    expect(lead?.anomalyId).toBe("ANM-021");
    expect(lead?.status).toBe("working");
  });

  it("says nothing at all about an untouched lead", () => {
    // `open` is the default AND the honest reading of a lead nobody has
    // picked up; a chip on 33 of 33 cards would bury the four somebody is
    // actually working.
    const { container } = draw(<LeadStatusControl anomalyId="ANM-999" />);
    expect(container.querySelector("[data-lead-status]")).toBeNull();
  });

  it("shows the platform's own verification sentence, verbatim", () => {
    const note =
      "ANM-031 is no longer in the detection feed at wm_003: the detector's own rule has stopped firing for this cell.";
    draw(
      <LeadStatusControl anomalyId="ANM-031" cardStatus="resolved_confirmed" cardNote={note} />,
    );
    expect(screen.getByText("Fix confirmed in the data")).toBeInTheDocument();
    expect(screen.getByText(note)).toBeInTheDocument();
  });

  it("offers no control at all without a deployment to record on", () => {
    // The store has no driver here. A menu that silently did nothing
    // would be worse than one that is not there.
    draw(<LeadStatusControl anomalyId="ANM-021" cardStatus="working" />);
    expect(screen.queryByRole("button", { name: /Change where/ })).not.toBeInTheDocument();
    // What the card DID say still stands: it is a published fact.
    expect(screen.getByText("Working it")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* Watching                                                            */
/* ------------------------------------------------------------------ */

describe("Watch this", () => {
  it("is not offered by a driver with nowhere to register a watch", () => {
    const { container } = draw(
      <WatchThis artifactKey="k" investigationId="inv_1" presentation="chart" />,
    );
    expect(container.textContent).toBe("");
  });

  it("reports an existing watch rather than offering to start a second one", () => {
    // The failure this closes: an analyst opens a permalink the next
    // morning, the button says "Watch this" over a watch that has been
    // running all week, and clicking it puts two tiles over one measure.
    const pin = mapRoundsPin(
      live.pins.pins.find((p: { created_from_kind: string }) => p.created_from_kind === "artifact"),
    );
    expect(pin).not.toBeNull();
    useSessionStore.setState({
      driver: { submit: async () => {}, newSession: async () => {}, createRoundsPin: async () => pin! },
      knownWatches: [pin!],
      watchesLoaded: true,
    });
    draw(
      <WatchThis
        artifactKey="k"
        investigationId={pin!.createdFromInvestigationId!}
        {...(pin!.createdFromReferent ? { referent: pin!.createdFromReferent } : {})}
        presentation="chart"
      />,
    );
    expect(screen.getByText("Watching")).toBeInTheDocument();
    expect(screen.queryByText("Watch this")).not.toBeInTheDocument();
  });
});

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
