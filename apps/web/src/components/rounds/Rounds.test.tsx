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
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BriefPanel } from "@/components/rounds/BriefPanel";
import { BriefEntryRow } from "@/components/rounds/BriefEntryRow";
import { LeadLifecyclePanel } from "@/components/rounds/LeadLifecycle";
import { LeadStatusControl } from "@/components/rounds/LeadStatus";
import { TimeToImpactLine } from "@/components/rounds/TimeToImpactLine";
import { WatchSensitivityForm } from "@/components/rounds/WatchSensitivity";
import { WatchThis } from "@/components/rounds/WatchThis";
import { WatchTile } from "@/components/rounds/WatchTile";
import { TooltipProvider } from "@/components/ui/tooltip";
import live from "@/lib/__fixtures__/live-rounds.json";
import { mapTimeToImpact, parsePortfolioSnapshot } from "@/lib/contract";
import { readableStatement } from "@/lib/prose";
import {
  mapLeadState,
  mapRoundsPin,
  orderTilesForGrid,
  parseBrief,
  parseRounds,
  tileCensus,
} from "@/lib/rounds";
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
    watchesError: null,
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
/* The mark a movement is allowed to paint                             */
/* ------------------------------------------------------------------ */

/**
 * EVERY DELTA CHIP PAINTED A MINUS SIGN — on the ones that went up as
 * well as the ones that went down.
 *
 * Measured live on all six `p[data-delta-direction]` elements of the
 * page: every one rendered lucide `Minus` (`path d="M5 12h14"`), because
 * every watch on the demo tenant is `sameWindow: true` and the same-window
 * branch drew `Minus` for "no arrow". At 10px beside a numeral that is not
 * the absence of a sign, it is a sign — so the exec's marquee tile read
 * "— 7.3 points from 22.2%" on a denial rate that got 7.3 points WORSE,
 * twelve pixels under brief prose saying "up 7.3 points".
 *
 * The refusal to draw an arrow on a re-measure is right and is kept. What
 * changed is that the neutral case is drawn UNSIGNED, and the direction
 * the payload does carry is stated in the word the brief beside it uses.
 *
 * Asserted as a property rather than as an icon: no mark may be shared
 * between a movement that went up and one that went down.
 */
describe("a delta chip never paints a sign the payload did not carry", () => {
  const MINUS_PATH = 'path[d="M5 12h14"]';

  /** The live rate tile's delta, re-pointed at one direction. */
  function tileWith(over: Partial<import("@/lib/rounds").RoundsDelta>) {
    const tile = (ROUNDS.value?.tiles ?? []).find((t) => t.delta !== undefined)!;
    return { ...tile, delta: { ...tile.delta!, ...over } };
  }

  function markOf(container: HTMLElement): string | null {
    return container.querySelector("[data-delta-mark]")?.getAttribute("data-delta-mark") ?? null;
  }

  it("says UP in the word the server sent, on a movement that went up", () => {
    const { container } = draw(
      <WatchTile tile={tileWith({ direction: "up", sameWindow: false, delta: 0.035823 })} />,
    );
    expect(screen.getByText(/up 3\.6 points/)).toBeInTheDocument();
    expect(markOf(container)).toBe("up");
  });

  it("says DOWN on a movement that went down, and shares no mark with up", () => {
    const down = draw(
      <WatchTile tile={tileWith({ direction: "down", sameWindow: false, delta: -0.035823 })} />,
    );
    expect(screen.getByText(/down 3\.6 points/)).toBeInTheDocument();
    expect(markOf(down.container)).toBe("down");
    cleanup();
    const up = draw(
      <WatchTile tile={tileWith({ direction: "up", sameWindow: false, delta: 0.035823 })} />,
    );
    expect(markOf(up.container)).not.toBe("down");
  });

  it("draws the same-window re-measure UNSIGNED, and still says which way it went", () => {
    // The demo tenant's entire grid. The glyph claims nothing; the word
    // carries the direction, exactly as the brief prose beside it does.
    const { container } = draw(
      <WatchTile tile={tileWith({ direction: "up", sameWindow: true, delta: 0.035823 })} />,
    );
    expect(markOf(container)).toBe("neutral");
    expect(screen.getByText(/up 3\.6 points/)).toBeInTheDocument();
    expect(container.querySelector(MINUS_PATH)).toBeNull();
  });

  it("draws no change as no change — unsigned, and with no direction word", () => {
    const { container } = draw(
      <WatchTile tile={tileWith({ direction: "flat", delta: 0, deltaText: "$0.00" })} />,
    );
    expect(markOf(container)).toBe("neutral");
    // Scoped to the chip: the tile also carries a baseline movement, and
    // that one DID go somewhere.
    const chip = container.querySelector("[data-delta-mark]");
    expect(chip?.textContent).toMatch(/no change/);
    expect(chip?.textContent).not.toMatch(/\b(up|down) /);
  });

  it("names no direction when the server named none", () => {
    const { container } = draw(
      <WatchTile tile={tileWith({ direction: "unknown", sameWindow: false, delta: 0.01 })} />,
    );
    expect(markOf(container)).toBe("neutral");
    const chip = container.querySelector("[data-delta-mark]");
    expect(chip?.textContent).not.toMatch(/\b(up|down) /);
  });

  it("paints a minus sign on nothing, anywhere on the live grid", () => {
    // The measurement that opened this: 6 of 6 chips drew `M5 12h14`.
    const { container } = draw(
      <ul>
        {(ROUNDS.value?.tiles ?? []).map((tile) => (
          <WatchTile key={tile.pinId} tile={tile} />
        ))}
      </ul>,
    );
    expect(container.querySelectorAll(MINUS_PATH).length).toBe(0);
    // …and every chip that IS drawn carries a mark that can be read back.
    for (const chip of container.querySelectorAll("[data-delta-mark]")) {
      expect(["up", "down", "neutral", "none"]).toContain(chip.getAttribute("data-delta-mark"));
    }
  });

  it("keeps the refusal itself: a non-comparable delta paints no mark at all", () => {
    const { container } = draw(
      <WatchTile
        tile={tileWith({
          comparable: false,
          notComparableReason: "first reading — baseline set at this load.",
        })}
      />,
    );
    expect(markOf(container)).toBe("none");
    expect(container.querySelector(MINUS_PATH)).toBeNull();
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

  it("renders every entry's statement word for word", () => {
    // WORD for word, not byte for byte, and the difference is exactly two
    // mechanical repairs: a stop printed twice where two sentence
    // builders met, and a rank grammar over a set of one. Every figure,
    // every clause and every ordering is the server's — see `lib/prose`,
    // and the assertion below that nothing else moved.
    draw(<BriefPanel brief={BRIEF.value!} />);
    for (const entry of BRIEF.value?.entries ?? []) {
      expect(
        screen.getByText(readableStatement(entry.statement)),
        `the brief must print ${entry.kind} as the server wrote it`,
      ).toBeInTheDocument();
      // Nothing but punctuation and the rank clause may move: every
      // figure, every name and every date in the payload's own sentence
      // is still on the page, in order.
      const words = entry.statement
        .replace(/ranks #1 of 1 measured by/g, "")
        .match(/[\w$%.,–-]*\d[\w$%.,–-]*/g) ?? [];
      const rendered = readableStatement(entry.statement);
      for (const word of words) {
        expect(rendered, `${entry.kind} dropped "${word}"`).toContain(word);
      }
    }
  });

  it("prints no stacked stop and no rank over a set of one", () => {
    // The two residues, asserted on the rendered page rather than on the
    // helper: `.).` is live in the JOC account's brief line, where the
    // curated reviewer note is quoted inside a sentence that carries on.
    const { container } = draw(<BriefPanel brief={BRIEF.value!} />);
    const text = container.textContent ?? "";
    expect(text).not.toContain(".).");
    expect(text).not.toContain("#1 of 1");
    expect(text).toContain("worth my morning). Since you started watching");
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

/* ------------------------------------------------------------------ */
/* Absence is said, not drawn as flatness                              */
/* ------------------------------------------------------------------ */

describe("a watch with nothing to compare says so", () => {
  const silent = () => (ROUNDS.value?.tiles ?? []).find((t) => t.delta === undefined)!;

  it("renders a sentence where a tile has no published movement", () => {
    // Live, 9 of 12 tiles rendered empty space beside the ones that moved,
    // so "did not move" and "there was nothing to compare" were the same
    // pixel. This states a fact about the payload and invents no reason
    // for it.
    const tile = silent();
    expect(tile, "the live capture must contain a tile with no delta").toBeDefined();
    const { container } = draw(<WatchTile tile={tile} />);
    expect(container.querySelector("[data-delta-absent]")).not.toBeNull();
  });

  it("prefers the server's own reason when it publishes a non-comparable delta", () => {
    // The fix's other half: once the server sends a delta with
    // `comparable: false` and a sentence, that sentence renders instead of
    // the client's line, and the client says nothing of its own.
    const tile = silent();
    const reason = "first reading at this load: there is nothing to compare against yet.";
    const { container } = draw(
      <WatchTile
        tile={{
          ...tile,
          delta: {
            priorWatermarkId: "wm_002",
            priorValueText: "",
            valueText: tile.valueText,
            deltaText: "",
            direction: "unknown",
            comparable: false,
            notComparableReason: reason,
            reference: "prior_load",
            sameWindow: false,
            material: false,
            thresholdSource: "governed",
            belowGovernedGate: false,
            materialityRule: "",
            materialityNote: "",
          },
        }}
      />,
    );
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(container.querySelector("[data-delta-absent]")).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/* The grid at Rounds scale                                            */
/* ------------------------------------------------------------------ */

describe("the tile grid is ordered, and says how", () => {
  it("puts the watches that moved first and keeps the server's order inside each band", () => {
    const tiles = ROUNDS.value?.tiles ?? [];
    const ordered = orderTilesForGrid(tiles);
    // The live capture: one material movement, one flat, two with no
    // comparison at all. Creation order put the flat one second and the
    // silent pair last only by luck; this makes it a rule.
    expect(ordered[0].delta?.material).toBe(true);
    expect(ordered[1].delta?.delta).toBe(0);
    expect(ordered.slice(2).every((t) => t.delta === undefined)).toBe(true);
    // Nothing is lost and nothing is duplicated by the sort.
    expect(ordered.map((t) => t.pinId).sort()).toEqual(tiles.map((t) => t.pinId).sort());
  });

  it("counts the grid in the same bands it orders it by", () => {
    expect(tileCensus(ROUNDS.value?.tiles ?? [])).toEqual([
      "1 moved",
      "1 unchanged",
      "2 with nothing to compare",
    ]);
  });
});

describe("a tile is one tab stop, with its controls reachable inside it", () => {
  it("keeps a tile's own controls out of the tab order until it is entered", () => {
    // ~5 focusable controls per tile is ~100 tab stops across a 20-watch
    // surface. The tile is the stop; Enter enters it.
    const tile = (ROUNDS.value?.tiles ?? [])[0];
    const { container } = draw(<WatchTile tile={tile} />);
    const item = container.querySelector<HTMLElement>("[data-tile-pin]")!;
    expect(item.tabIndex).toBe(0);
    const inner = [...item.querySelectorAll<HTMLElement>("a[href], button")];
    expect(inner.length).toBeGreaterThan(1);
    expect(inner.every((el) => el.tabIndex === -1)).toBe(true);

    fireEvent.keyDown(item, { key: "Enter", target: item });
    expect(item).toHaveAttribute("data-tile-entered", "true");
    expect(
      [...item.querySelectorAll<HTMLElement>("a[href], button")].every((el) => el.tabIndex === 0),
    ).toBe(true);
  });

  it("names what the tile is, so the one stop is not an unlabelled box", () => {
    const tile = (ROUNDS.value?.tiles ?? [])[0];
    const { container } = draw(<WatchTile tile={tile} />);
    const item = container.querySelector("[data-tile-pin]")!;
    expect(item.getAttribute("aria-label")).toContain(tile.label);
    expect(item.getAttribute("aria-label")).toContain(tile.valueText);
    expect(item).toHaveAttribute("aria-describedby", "rounds-tile-hint");
  });
});

/* ------------------------------------------------------------------ */
/* A failed read is never a disabled control                           */
/* ------------------------------------------------------------------ */

/**
 * ROUND 8's LEAD UI DEFECT, closed at the client.
 *
 * Live, `GET /v1/rounds/pins` 500'd off one watch whose threshold unit was
 * `days`, the store swallowed it in an empty `catch {}`, and "Change what
 * it takes to brief you" rendered `disabled` on all thirty tiles with no
 * sentence anywhere on the page. Three reviewers reported it as a dead
 * button; nobody could tell it from a feature that was switched off.
 *
 * The server-side half is somebody else's fix and it does not close this:
 * a read that CAN fail must present as a failed read whenever it does.
 */
describe("a tile whose settings could not be read says so", () => {
  const tile = () => (ROUNDS.value?.tiles ?? [])[0];

  const openMenu = (label: string) =>
    fireEvent.click(screen.getByRole("button", { name: `Settings for the watch ${label}` }));

  it("mocks the 500: the sentence renders and the control offers the read again", async () => {
    const listRoundsPins = vi
      .fn()
      .mockRejectedValueOnce(new Error("GET /v1/rounds/pins failed: 500 Internal Server Error"))
      .mockResolvedValue([]);
    useSessionStore.setState({
      driver: { submit: async () => {}, listRoundsPins } as unknown as never,
    });
    await useSessionStore.getState().loadWatches();
    // The store keeps the server's own sentence rather than dropping it.
    expect(useSessionStore.getState().watchesError).toContain("500");
    expect(useSessionStore.getState().watchesLoaded).toBe(false);

    const t = tile();
    draw(<WatchTile tile={t} />);
    openMenu(t.label);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      /Could not read this watch's settings — reload to try again/,
    );
    // And the failure itself, verbatim, so the reason is diagnosable from
    // the screen the analyst is already looking at.
    expect(alert).toHaveTextContent(/500/);

    // Nothing on this menu is a disabled control: the whole defect class
    // was a control whose only account of itself was that it could not be
    // pressed.
    for (const button of screen.getAllByRole("button")) {
      expect(button, `${button.textContent} must not be dead`).toBeEnabled();
    }

    // THE CONTROL EXPLAINS ITSELF. Not "Change what it takes to brief you"
    // greyed out — a live control that re-runs the read that failed.
    const retry = screen.getByRole("button", { name: /Read this watch's settings again/ });
    fireEvent.click(retry);
    expect(listRoundsPins).toHaveBeenCalledTimes(2);
  });

  it("says so too when the read succeeded without this watch in it", async () => {
    // The other live failure mode behind the same symptom: a 200 whose
    // list did not contain the pin the tile was drawn from.
    useSessionStore.setState({
      driver: { submit: async () => {}, listRoundsPins: async () => [] } as unknown as never,
    });
    await useSessionStore.getState().loadWatches();
    expect(useSessionStore.getState().watchesLoaded).toBe(true);
    expect(useSessionStore.getState().watchesError).toBeNull();

    const t = tile();
    draw(<WatchTile tile={t} />);
    openMenu(t.label);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      /Could not read this watch's settings — reload to try again/,
    );
    expect(alert).toHaveTextContent(/came back without this watch in it/);
  });

  it("opens the editor the moment the settings ARE in hand", async () => {
    const pin = mapRoundsPin(live.pins.pins[0]);
    expect(pin).not.toBeNull();
    const t = { ...tile(), pinId: pin!.pinId };
    draw(<WatchTile tile={t} pin={pin!} />);
    openMenu(t.label);
    const change = await screen.findByRole("button", {
      name: /Change what it takes to brief you/,
    });
    expect(change).toBeEnabled();
    fireEvent.click(change);
    expect(
      await screen.findByRole("button", { name: /Save and restart this watch/ }),
    ).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* The controls that end a watch, and the ones that start one          */
/* ------------------------------------------------------------------ */

/**
 * "STOP WATCHING THIS" WAS ONE IRREVERSIBLE CLICK.
 *
 * The reassurance copy landed and is good — "Nothing is deleted. The loads
 * this watch has already been briefed on stay readable" — but it sat UNDER
 * the button, read on the way past rather than acknowledged. Filed rounds
 * 8, 9 and 10.
 *
 * There is no undo to offer instead: re-registering a watch resets the
 * baseline "since you started watching" measures from, so an undo would
 * quietly hand back a different watch. The two-step is the honest control.
 */
describe("ending a watch is armed before it fires", () => {
  const tile = () => (ROUNDS.value?.tiles ?? [])[0];
  const openMenu = (label: string) =>
    fireEvent.click(screen.getByRole("button", { name: `Settings for the watch ${label}` }));

  it("does not remove the watch on the first click", async () => {
    const deleteRoundsPin = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({
      driver: { submit: async () => {}, deleteRoundsPin } as unknown as never,
    });
    const t = tile();
    draw(<WatchTile tile={t} />);
    openMenu(t.label);
    fireEvent.click(await screen.findByRole("button", { name: /Stop watching this/ }));
    expect(deleteRoundsPin).not.toHaveBeenCalled();

    // The reassurance is now the thing being acknowledged, above the
    // confirming control rather than below the firing one.
    const armed = document.querySelector("[data-stop-watch-armed]");
    expect(armed).not.toBeNull();
    expect(armed).toHaveTextContent(/Nothing is deleted/);
    expect(armed).toHaveTextContent(new RegExp(escapeRe(t.label)));
  });

  it("removes it on the second, and the reversible choice is offered first", async () => {
    const deleteRoundsPin = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({
      driver: { submit: async () => {}, deleteRoundsPin } as unknown as never,
    });
    const t = tile();
    draw(<WatchTile tile={t} />);
    openMenu(t.label);
    fireEvent.click(await screen.findByRole("button", { name: /Stop watching this/ }));

    const keep = await screen.findByRole("button", { name: /Keep watching/ });
    const stop = screen.getByRole("button", { name: /Yes, stop watching/ });
    // Reversible first in the DOM, so it is first for a keyboard too.
    expect(keep.compareDocumentPosition(stop) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(keep);
    expect(document.querySelector("[data-stop-watch-armed]")).toBeNull();
    expect(deleteRoundsPin).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Stop watching this/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Yes, stop watching/ }));
    expect(deleteRoundsPin).toHaveBeenCalledWith(t.pinId);
  });
});

/**
 * THE WATCH AFFORDANCES ARE PRESENT, not hovered into existence.
 *
 * Filed rounds 7-10 by three reviewers: `WatchThis` defaults to the
 * compact size and rendered `opacity-0 group-hover:opacity-100`, and
 * `FactRow` passes no size — so on every finding card, the one gesture
 * that starts the proactive monitoring this product is sold on did not
 * exist for a touch user, in a screenshot, or on a projector. The tile's
 * own settings trigger had the same class.
 *
 * Quiet is the right volume and `text-muted-foreground` is quiet — it
 * measures 4.57–5.24:1 in light and 6.92–7.31 in dark. Invisible is not a
 * volume.
 */
describe("watch affordances are visible without a pointer", () => {
  it("draws the tile's settings trigger at full opacity", () => {
    const { container } = draw(<WatchTile tile={(ROUNDS.value?.tiles ?? [])[0]} />);
    const trigger = container.querySelector<HTMLElement>('[aria-label^="Settings for the watch"]')!;
    expect(trigger).not.toBeNull();
    expect(trigger.className).not.toMatch(/\bopacity-0\b/);
    expect(trigger.className).not.toMatch(/group-hover:opacity/);
  });

  it("draws the compact Watch this at full opacity", () => {
    useSessionStore.setState({
      driver: {
        submit: async () => {},
        createRoundsPin: async () => ({}),
        listRoundsPins: async () => [],
      } as unknown as never,
    });
    const { container } = draw(
      <WatchThis
        artifactKey="inv_1:F1"
        investigationId="inv_1"
        referent="F1"
        presentation="finding"
      />,
    );
    const button = container.querySelector<HTMLElement>("button")!;
    expect(button).not.toBeNull();
    expect(button.className).not.toMatch(/\bopacity-0\b/);
    expect(button.className).not.toMatch(/group-hover:opacity/);
  });
});

/* ------------------------------------------------------------------ */
/* An entry a reader cannot open is a notification                     */
/* ------------------------------------------------------------------ */

describe("every brief entry has somewhere to go", () => {
  const newLead = () => (BRIEF.value?.entries ?? []).find((e) => e.kind === "new_lead")!;

  it("offers the lead's own drill when the entry carries no investigation", () => {
    // Live, 3 of 4 brief rows carried `investigation_id: null` — including
    // both four-figure new leads. The drill exists; the row now hands it
    // over.
    const entry = newLead();
    expect(entry.investigationId).toBeUndefined();
    const open = vi.fn();
    draw(<BriefEntryRow entry={entry} lead={{ open }} />);
    fireEvent.click(screen.getByRole("button", { name: /Open this lead/ }));
    expect(open).toHaveBeenCalledOnce();
  });

  it("says why there is nothing to open rather than ending in silence", () => {
    const reason = "no governed contract covers this detector's cell at this pack version";
    draw(<BriefEntryRow entry={newLead()} lead={{ unavailableReason: reason }} />);
    expect(screen.getByText(new RegExp(escapeRe(reason)))).toBeInTheDocument();
  });

  it("says a four-figure sum with no grade is the detector's own figure", () => {
    const entry = newLead();
    expect(entry.integrity).toBeUndefined();
    expect(entry.impactCents).toBeGreaterThan(0);
    draw(<BriefEntryRow entry={entry} />);
    expect(screen.getByText(/not re-measured at this load/)).toBeInTheDocument();
  });

  it("says on the eyebrow when somebody has already claimed the lead", () => {
    // The compounding failure: PATCH ANM-029 → resolved_claimed, and the
    // brief still rendered it as an untouched "New lead". Two people work
    // the same $17k lead on the same morning.
    draw(<BriefEntryRow entry={newLead()} lead={{ status: "resolved_claimed" }} />);
    expect(screen.getByText(/already claimed fixed/)).toBeInTheDocument();
    expect(screen.getByText("Fixed — checking the data")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* A kind this build has never seen is still a change                  */
/* ------------------------------------------------------------------ */

describe("the brief's vocabulary is not a filter on the server's facts", () => {
  function withKind(kind: string) {
    return parseBrief({
      ...live.brief,
      entries: [{ ...live.brief.entries[0], kind }],
    });
  }

  it("renders a rank flip as the fact it is, not as a movement", () => {
    const parsed = withKind("rank_flip");
    expect(parsed.drift).toEqual([]);
    expect(parsed.value?.entries).toHaveLength(1);
    draw(<BriefEntryRow entry={parsed.value!.entries[0]} />);
    expect(screen.getByText(/A different cell now leads/)).toBeInTheDocument();
  });

  it("keeps an unknown kind, and reports the mismatch", () => {
    const parsed = withKind("something_new_entirely");
    expect(parsed.value?.entries).toHaveLength(1);
    expect(parsed.drift).toContain("entries[0].kind:something_new_entirely");
    draw(<BriefEntryRow entry={parsed.value!.entries[0]} />);
    // The server's sentence — the part that carries the fact — is intact.
    expect(screen.getByText(parsed.value!.entries[0].statement)).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* The walk's census closes, and speaks in dates                       */
/* ------------------------------------------------------------------ */

describe("the work behind the brief reconciles to its parts", () => {
  it("names the loads by data date, not by warehouse id", () => {
    const { container } = draw(
      <BriefPanel
        brief={{
          ...BRIEF.value!,
          newestDataDate: "2026-08-02",
          priorNewestDataDate: "2026-07-26",
        }}
      />,
    );
    const census = container.querySelector("[data-walk-census]")!;
    expect(census.textContent).toContain("Aug 2, 2026");
    expect(census.textContent).toContain("Jul 26, 2026");
    expect(census.textContent).not.toContain("wm_00");
  });

  it("splits the watches it walked, and says so when they do not add up", () => {
    // 4 evaluated: 1 briefed movement, 1 held back, and — before the
    // server published `not_yet_comparable` — two that were counted
    // nowhere at all.
    const brief = { ...BRIEF.value!, pinsEvaluated: 4 };
    const { container, unmount } = draw(<BriefPanel brief={brief} />);
    expect(container.querySelector("[data-walk-census]")?.textContent).toContain(
      "2 not accounted for",
    );
    unmount();

    const closed = {
      ...brief,
      immaterial: { ...brief.immaterial, notYetComparable: 2 },
    };
    const second = draw(<BriefPanel brief={closed} />);
    const census = second.container.querySelector("[data-walk-census]")!;
    expect(census.textContent).toContain("2 with nothing to compare against yet");
    expect(census.textContent).not.toContain("not accounted for");
  });
});

/* ------------------------------------------------------------------ */
/* What we claimed we fixed, and whether it stuck                      */
/* ------------------------------------------------------------------ */

describe("the lead lifecycle zone", () => {
  const rows = [
    {
      anomalyId: "ANM-001",
      title: "Timely-filing exposure",
      status: "resolved_claimed" as const,
      note: "resolution claimed; this platform re-runs the lead's own drill at each load.",
      impactCents: 17064300,
      live: {
        anomalyId: "ANM-001",
        status: "resolved_claimed" as const,
        note: "",
        baselineBasis: "",
        confirmingWatermarks: ["wm_003"],
        confirmationsRequired: 2,
        verificationNote: "",
      },
    },
    {
      anomalyId: "ANM-021",
      title: "Held general-surgery accounts",
      status: "working" as const,
      note: "Coding is clearing the 22 held accounts this week.",
      impactCents: 16930598,
    },
  ];

  it("groups leads by where they stand and leads with the ones that came back", () => {
    draw(<LeadLifecyclePanel leads={rows} totalLeads={33} headingId="leads-heading" />);
    expect(screen.getByText(/Claimed fixed/)).toBeInTheDocument();
    expect(screen.getByText(/Being worked/)).toBeInTheDocument();
    // The census says what is NOT here: the untouched leads are the
    // worklist, and a lifecycle view listing them would be the worklist.
    expect(screen.getByText("2 of 33 leads have somebody on them")).toBeInTheDocument();
  });

  it("shows how far along a claimed fix is, from the record that carries it", () => {
    draw(<LeadLifecyclePanel leads={rows} totalLeads={33} headingId="leads-heading" />);
    expect(
      screen.getByText("1 of the 2 consecutive loads the governed rule requires"),
    ).toBeInTheDocument();
  });

  it("says nothing has been picked up rather than rendering an empty box", () => {
    draw(<LeadLifecyclePanel leads={[]} totalLeads={33} headingId="leads-heading" />);
    expect(screen.getByText(/Nobody has picked up a lead yet/)).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* Reachable controls, legible ink                                     */
/* ------------------------------------------------------------------ */

describe("the sensitivity dialog's primary action cannot fall below the fold", () => {
  it("pins the submit row to the bottom of the scrolling panel", () => {
    // Measured before this: the popover was 662px tall at y=150 on a 772px
    // viewport, so "Save and restart this watch" sat 2px past the bottom
    // with no scroll container and nothing under it to scroll to.
    draw(
      <WatchSensitivityForm
        submitLabel="Save and restart this watch"
        pending={false}
        onSubmit={() => {}}
        onCancel={() => {}}
      />,
    );
    const submit = screen.getByRole("button", { name: "Save and restart this watch" });
    const row = submit.closest("div")?.parentElement;
    expect(row?.className).toContain("sticky");
    expect(row?.className).toContain("bottom-0");
  });

  it("keeps the server's refusal beside the control that produced it", () => {
    const refusal = "a threshold in 'cents' is only honest for a 'money_cents' contract";
    draw(
      <WatchSensitivityForm
        submitLabel="Start watching"
        pending={false}
        refusal={refusal}
        onSubmit={() => {}}
        onCancel={() => {}}
      />,
    );
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain(refusal);
    expect(alert.parentElement?.className).toContain("sticky");
  });
});

describe("the integrity atom reads as two counts, not one number", () => {
  it("separates the caveat count from its severity", () => {
    // Measured accessible string before this: "…·7 things to know6 change
    // how a number here should be read".
    const tile = (ROUNDS.value?.tiles ?? []).find((t) => t.warnings.length > 0)!;
    expect(tile, "the live capture must contain a tile with caveats").toBeDefined();
    const { container } = draw(<WatchTile tile={tile} />);
    const atom = container.querySelector("[data-integrity-atom]")!;
    expect(atom.textContent).not.toMatch(/things to know\d/);
  });

  it("draws the measured window in solid muted ink, not at 80%", () => {
    // `--muted-foreground` at 80% measures 3.48:1 on card, 3.27:1 on the
    // page and 3.16:1 on sunken — below the 4.5:1 floor at 12px. Solid:
    // 5.24 / 4.80 / 4.57.
    const tile = (ROUNDS.value?.tiles ?? []).find((t) => t.windowStart !== undefined)!;
    const { container } = draw(<WatchTile tile={tile} />);
    const html = container.innerHTML;
    expect(html).not.toContain("text-muted-foreground/80");
  });
});

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
