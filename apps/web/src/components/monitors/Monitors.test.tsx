/**
 * MONITORS — against a captured live surface.
 *
 * Every payload read here came off a running deployment
 * (`scripts/capture-fixtures.mjs monitors`, wm_003, Postgres stores, real
 * warehouse): the brief with its four entries, four evaluated tiles, the
 * pins behind them, one lead with a real lifecycle, and the portfolio
 * cards that carry the two new fields. Nothing in this file is composed —
 * a Monitors test written against a hand-made tile would assert a shape the
 * product does not produce, on the one surface nobody is monitoring while it
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

import { MemoryRouter } from "react-router-dom";

import { BriefPanel } from "@/components/monitors/BriefPanel";
import { BriefEntryRow } from "@/components/monitors/BriefEntryRow";
import { LeadLifecyclePanel } from "@/components/monitors/LeadLifecycle";
import { LeadStatusControl } from "@/components/monitors/LeadStatus";
import { TimeToImpactLine } from "@/components/monitors/TimeToImpactLine";
import { MonitorSensitivityForm } from "@/components/monitors/MonitorSensitivity";
import { MonitorThis } from "@/components/monitors/MonitorThis";
import { DigestTile } from "@/components/home/MonitorDigest";
import { MonitorManagement } from "@/components/monitors/MonitorManagement";
import { TooltipProvider } from "@/components/ui/tooltip";
import live from "@/lib/__fixtures__/live-monitors.json";
import { mapTimeToImpact, parsePortfolioSnapshot } from "@/lib/contract";
import { capitalizeOpening, readableLabel, readableStatement } from "@/lib/prose";
import {
  mapLeadState,
  mapMonitorsPin,
  orderTilesForGrid,
  parseBrief,
  parseMonitors,
  tileCensus,
  type MonitorsPin,
  type MonitorsTile,
} from "@/lib/monitors";
import { useSessionStore } from "@/lib/store";

function draw(node: React.ReactNode) {
  return render(
    <MemoryRouter>
      <TooltipProvider>{node}</TooltipProvider>
    </MemoryRouter>,
  );
}

/**
 * ONE MONITOR, OPENED — which is where a monitor is now read and managed.
 *
 * These assertions were written against `MonitorTile`, the card the retired
 * `/monitors` grid drew. That surface is gone (Home renders the better
 * version of all three of its zones) and the card went with it: the digest
 * tile on Home expands in place into the same content plus the things a
 * grid of twenty cards could not afford. So the tests follow the behaviour
 * rather than the component — every property below is still a property of
 * what a reader sees, at the place they now see it.
 *
 * `expanded` is a prop rather than a click because `DigestTile` is
 * controlled by the digest's accordion: what is under test here is what the
 * detail SAYS, and the disclosure itself is asserted on its own below.
 */
function drawTile(tile: MonitorsTile, pin?: MonitorsPin) {
  return draw(
    <ul>
      <DigestTile
        tile={tile}
        {...(pin ? { pin } : {})}
        moved={false}
        expanded
        onToggle={() => {}}
      />
    </ul>,
  );
}

const MONITORS = parseMonitors(live.monitors);
const BRIEF = parseBrief(live.brief);

beforeEach(() => {
  useSessionStore.setState({
    driver: null,
    monitors: {},
    knownMonitors: [],
    monitorsLoaded: false,
    monitorsLoading: false,
    monitorsError: null,
    monitorPendingKey: null,
    monitorError: null,
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
    expect(MONITORS.drift).toEqual([]);
    expect(MONITORS.value?.tiles.length).toBe(live.monitors.tiles.length);
    for (const tile of MONITORS.value?.tiles ?? []) {
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
      ...live.monitors,
      tiles: live.monitors.tiles.map((tile, index) =>
        index === 0 ? Object.fromEntries(Object.entries(tile).filter(([k]) => k !== "integrity")) : tile,
      ),
    };
    const parsed = parseMonitors(stripped);
    expect(parsed.drift).toContain("tiles[0].integrity");
    expect(parsed.value?.tiles.length).toBe(live.monitors.tiles.length - 1);
  });

  it("drops a tile whose grade is not a grade this client knows", () => {
    const bogus = {
      ...live.monitors,
      tiles: live.monitors.tiles.map((tile, index) =>
        index === 0 ? { ...tile, integrity: { ...tile.integrity, grade: "excellent" } } : tile,
      ),
    };
    expect(parseMonitors(bogus).drift).toContain("tiles[0].integrity");
  });

  it("draws the grade and the caveat count on EVERY live tile", () => {
    for (const tile of MONITORS.value?.tiles ?? []) {
      const { container, unmount } = drawTile(tile);
      const atom = container.querySelector("[data-integrity-atom]");
      expect(atom, `${tile.label} must carry its integrity atom`).not.toBeNull();
      expect(atom).toHaveAttribute("data-answer-grade", tile.integrity.grade);
      expect(atom?.textContent).toContain(`${tile.integrity.thingsToKnow} things to know`);
      unmount();
    }
  });

  it("says a still-settling figure is still settling, in words", () => {
    const provisional = (MONITORS.value?.tiles ?? []).find((t) => t.integrity.provisional);
    expect(provisional, "the live capture must contain a provisional tile").toBeDefined();
    drawTile(provisional!);
    // A word, not a shade: a provisional figure that carried its
    // uncertainty only in a lighter grey is a figure somebody quotes.
    expect(screen.getByText("still settling")).toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* Deltas keep the metric's own unit                                   */
/* ------------------------------------------------------------------ */

describe("a movement is stated in the metric's own unit", () => {
  /**
   * A rate monitor that actually MOVED, off the capture.
   *
   * `comparable` is part of the predicate rather than an afterthought: a
   * tile whose two loads measured different subjects publishes a delta
   * object with no delta in it, and picking one of those would leave every
   * assertion below testing the not-comparable sentence instead of a
   * movement.
   */
  const rateTile = () =>
    (MONITORS.value?.tiles ?? []).find(
      (t) => t.delta?.unit === "ratio" && t.delta.comparable && (t.delta.delta ?? 0) !== 0,
    )!;

  it("renders a rate's movement in POINTS, as the server rendered it", () => {
    const tile = rateTile();
    expect(tile).toBeDefined();
    // Derived from the capture rather than transcribed from it: the
    // deployment's leading rate monitor moves between captures and the
    // claim under test is the UNIT, not the figure.
    const points = ((tile.delta!.delta ?? 0) * 100).toFixed(1);
    drawTile(tile);
    expect(screen.getAllByText(new RegExp(`${points} points`)).length).toBeGreaterThan(0);
    // Never a percentage. 0.07286 rendered as "+7.3%" is a number a
    // reader cannot tell from a relative change, which is the single most
    // common way a denial-rate figure lies.
    expect(screen.queryByText(new RegExp(`${points}%`))).not.toBeInTheDocument();
  });

  it("says a same-window change is the data catching up, not a movement", () => {
    const tile = rateTile();
    const { container } = drawTile(tile);
    // Sentence case, and in the line's own ink: the data catching up is
    // context about the reading, not an alarm about it.
    const marks = screen.getAllByText("Same period, re-measured");
    expect(marks.length).toBeGreaterThan(0);
    expect(marks[0].className).not.toContain("text-warning");
    // And it is not drawn with a direction arrow: an arrow is a claim
    // about the world, and this is a claim about adjudication run-out.
    const line = container.querySelector('[data-same-window="true"]');
    expect(line).not.toBeNull();
  });

  it("shows the baseline movement when it says something the prior load does not", () => {
    // The tile shows its baseline movement only when that movement says
    // something the prior-load one does not — the same magnitude twice is
    // one sentence printed twice. So the capture supplies the shape and
    // the two magnitudes are made to differ, which is the condition under
    // test.
    const found = (MONITORS.value?.tiles ?? []).find(
      (t) => t.baselineDelta !== undefined && t.delta !== undefined,
    );
    expect(found, "the live capture must contain a tile carrying both deltas").toBeDefined();
    const tile = {
      ...found!,
      baselineDelta: { ...found!.baselineDelta!, comparable: true, deltaText: "5.1 points" },
    };
    drawTile(tile);
    expect(screen.getByText(/since you started monitoring/)).toBeInTheDocument();
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
 * every monitor on the demo tenant is `sameWindow: true` and the same-window
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
  // COMPARABLE, or every assertion below tests the wrong branch: a tile
  // whose two loads measured different subjects renders the reason
  // instead of a chip, and re-pointing its direction does not change that.
  const baseTile = () =>
    (MONITORS.value?.tiles ?? []).find(
      (t) => t.delta !== undefined && t.delta.comparable && t.delta.unit === "ratio",
    )!;

  /**
   * The magnitude the SERVER rendered for that tile, read out of the
   * capture rather than transcribed into the assertion. The claim under
   * test is that the word beside it matches the direction the payload
   * carried; which rate moved how far is the deployment's business and
   * changes between captures.
   */
  const magnitude = () => `${((baseTile().delta!.delta ?? 0) * 100).toFixed(1)} points`;

  function tileWith(over: Partial<import("@/lib/monitors").MonitorsDelta>) {
    const tile = baseTile();
    return { ...tile, delta: { ...tile.delta!, ...over } };
  }

  function markOf(container: HTMLElement): string | null {
    return container.querySelector("[data-delta-mark]")?.getAttribute("data-delta-mark") ?? null;
  }

  /*
   * THE WORD IS THE SERVER'S; ONLY ITS FIRST CHARACTER IS THIS CLIENT'S.
   * The magnitude opens the delta line — the arrow beside it is
   * `aria-hidden` decoration — so it opens in capitals like every other
   * card opening in the product (`capitalizeOpening`, applied in
   * `DeltaLine`). "Up 3.6 points" is the same claim, in the same unit,
   * from the same `deltaText`.
   */
  it("says UP in the word the server sent, on a movement that went up", () => {
    const { container } = drawTile(tileWith({ direction: "up", sameWindow: false }));
    expect(screen.getByText(new RegExp(`Up ${magnitude()}`))).toBeInTheDocument();
    expect(markOf(container)).toBe("up");
  });

  it("says DOWN on a movement that went down, and shares no mark with up", () => {
    const down = drawTile(tileWith({ direction: "down", sameWindow: false }));
    expect(screen.getByText(new RegExp(`Down ${magnitude()}`))).toBeInTheDocument();
    expect(markOf(down.container)).toBe("down");
    cleanup();
    const up = drawTile(tileWith({ direction: "up", sameWindow: false }));
    expect(markOf(up.container)).not.toBe("down");
  });

  it("draws the same-window re-measure UNSIGNED, and still says which way it went", () => {
    // The demo tenant's entire grid. The glyph claims nothing; the word
    // carries the direction, exactly as the brief prose beside it does.
    const { container } = drawTile(tileWith({ direction: "up", sameWindow: true }));
    expect(markOf(container)).toBe("neutral");
    expect(screen.getByText(new RegExp(`Up ${magnitude()}`))).toBeInTheDocument();
    expect(container.querySelector(MINUS_PATH)).toBeNull();
  });

  it("draws no change as no change — unsigned, and with no direction word", () => {
    const { container } = drawTile(tileWith({ direction: "flat", delta: 0, deltaText: "$0.00" }));
    expect(markOf(container)).toBe("neutral");
    // Scoped to the chip: the tile also carries a baseline movement, and
    // that one DID go somewhere.
    const chip = container.querySelector("[data-delta-mark]");
    expect(chip?.textContent).toMatch(/No change/);
    // Case-insensitive on purpose: without the flag this assertion would
    // silently stop testing anything the moment the word took a capital.
    expect(chip?.textContent).not.toMatch(/\b(up|down) /i);
  });

  it("names no direction when the server named none", () => {
    const { container } = drawTile(tileWith({ direction: "unknown", sameWindow: false, delta: 0.01 }));
    expect(markOf(container)).toBe("neutral");
    const chip = container.querySelector("[data-delta-mark]");
    // Case-insensitive on purpose: without the flag this assertion would
    // silently stop testing anything the moment the word took a capital.
    expect(chip?.textContent).not.toMatch(/\b(up|down) /i);
  });

  it("paints a minus sign on nothing, anywhere on the live grid", () => {
    // The measurement that opened this: 6 of 6 chips drew `M5 12h14`.
    const { container } = draw(
      <ul>
        {(MONITORS.value?.tiles ?? []).map((tile) => (
          // Every one of them opened: the chip a collapsed tile draws is
          // the digest's own, and the line under test is the one the
          // detail states in full.
          <DigestTile
            key={tile.pinId}
            tile={tile}
            moved={false}
            expanded
            onToggle={() => {}}
          />
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
    const { container } = drawTile(
      tileWith({
        comparable: false,
        notComparableReason: "first reading — baseline set at this load.",
      }),
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
    expect(text).toContain("anything over a point). Against the previous load");
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
    expect(screen.getByText(/monitors re-run/)).toBeInTheDocument();
  });

  it("makes 'nothing material changed' the loudest thing on the surface", () => {
    // The proud state: a quiet morning is the best outcome this product
    // has, and it is set at the size otherwise reserved for one headline
    // figure. A pale grey empty state would say the opposite.
    const quiet = {
      ...BRIEF.value!,
      status: "nothing_material" as const,
      entries: [],
      headline: "Revi walked 4 monitors and 33 leads at wm_003. Nothing cleared the gate.",
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
    expect(screen.getByText("First walk of your Monitors.")).toBeInTheDocument();
  });

  it("carries the honesty marks onto a briefed movement", () => {
    const movement = (BRIEF.value?.entries ?? []).find((e) => e.integrity !== undefined);
    expect(movement, "a pin movement must carry its integrity block").toBeDefined();
    const { container } = draw(<BriefEntryRow entry={movement!} />);
    expect(container.querySelector("[data-integrity-atom]")).not.toBeNull();
  });

  it("names which threshold briefed an entry", () => {
    const movement = (BRIEF.value?.entries ?? []).find((e) => e.delta?.thresholdSource === "monitor");
    expect(movement).toBeDefined();
    draw(<BriefEntryRow entry={movement!} />);
    // Sentence case, quiet ink: which gate briefed this is a note, and
    // the words carry the distinction the colour used to.
    // "The governed threshold" / "your threshold" was engine vocabulary on
    // the line explaining why somebody was interrupted. The FACT is
    // unchanged — whose level briefed this, and whether it is looser than
    // the recommended one — and the rule itself is still verbatim in the
    // hover.
    const note = screen.getByText("Your level");
    expect(note).toBeInTheDocument();
    expect(note.className).not.toContain("text-warning");
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
    expect(screen.getByText(/Hits cash in ~\d+ days/)).toBeInTheDocument();
    // An estimate rendered like a filing limit is indistinguishable from
    // one on the screen beside it. The WORD is the mark; the ink is quiet.
    const provisional = screen.getByText("Provisional");
    expect(provisional).toBeInTheDocument();
    expect(provisional.className).not.toContain("text-warning");
  });

  it("says an already-landed card has landed, and names the window still open", () => {
    const hit = cards.find(
      (c) => c.timeToImpact?.kind === "already_hit" && c.timeToImpact.recoveryDays !== undefined,
    );
    expect(hit).toBeDefined();
    draw(<TimeToImpactLine timeToImpact={hit!.timeToImpact!} />);
    expect(screen.getByText(/Already hit cash — appeal window closes in \d+ days/)).toBeInTheDocument();
    expect(screen.queryByText("Provisional")).not.toBeInTheDocument();
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
    expect(screen.getByText("No cash date for this kind of card")).toBeInTheDocument();
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
/* Monitoring                                                            */
/* ------------------------------------------------------------------ */

describe("Monitor this", () => {
  it("is not offered by a driver with nowhere to register a monitor", () => {
    const { container } = draw(
      <MonitorThis artifactKey="k" investigationId="inv_1" presentation="chart" />,
    );
    expect(container.textContent).toBe("");
  });

  it("reports an existing monitor rather than offering to start a second one", () => {
    // The failure this closes: an analyst opens a permalink the next
    // morning, the button says "Monitor this" over a monitor that has been
    // running all week, and clicking it puts two tiles over one measure.
    const pin = mapMonitorsPin(
      live.pins.pins.find((p: { created_from_kind: string }) => p.created_from_kind === "artifact"),
    );
    expect(pin).not.toBeNull();
    useSessionStore.setState({
      driver: { submit: async () => {}, newSession: async () => {}, createMonitorsPin: async () => pin! },
      knownMonitors: [pin!],
      monitorsLoaded: true,
    });
    draw(
      <MonitorThis
        artifactKey="k"
        investigationId={pin!.createdFromInvestigationId!}
        {...(pin!.createdFromReferent ? { referent: pin!.createdFromReferent } : {})}
        presentation="chart"
      />,
    );
    expect(screen.getByText("Monitoring")).toBeInTheDocument();
    expect(screen.queryByText("Monitor this")).not.toBeInTheDocument();
  });
});

/* ------------------------------------------------------------------ */
/* Absence is said, not drawn as flatness                              */
/* ------------------------------------------------------------------ */

describe("a monitor with nothing to compare says so", () => {
  /**
   * A tile the server published NO delta object for.
   *
   * Composed rather than found: the engine now publishes a delta on every
   * tile, carrying `comparable: false` and a sentence where it has nothing
   * to compare — which is the better payload and is covered by the test
   * below. This branch is the older shape, still reachable from a stored
   * evaluation, and dropping the test with the fixture would leave the
   * client's own fallback line untested.
   */
  const silent = () => {
    const tile = (MONITORS.value?.tiles ?? [])[0]!;
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    const { delta: _delta, ...rest } = tile;
    return rest as typeof tile;
  };

  it("renders a sentence where a tile has no published movement", () => {
    // Live, 9 of 12 tiles rendered empty space beside the ones that moved,
    // so "did not move" and "there was nothing to compare" were the same
    // pixel. This states a fact about the payload and invents no reason
    // for it.
    const tile = silent();
    expect(tile.delta, "this branch is the no-delta shape").toBeUndefined();
    const { container } = drawTile(tile);
    expect(container.querySelector("[data-delta-absent]")).not.toBeNull();
  });

  it("prefers the server's own reason when it publishes a non-comparable delta", () => {
    // The fix's other half: once the server sends a delta with
    // `comparable: false` and a sentence, that sentence renders instead of
    // the client's line, and the client says nothing of its own.
    const tile = silent();
    const reason = "first reading at this load: there is nothing to compare against yet.";
    const { container } = drawTile(
      {
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
      },
    );
    // Rendered verbatim apart from the opening capital: the composer
    // hands over a bare clause ("first reading at this load: …") and a
    // tile of lower-case sentences reads as unfinished. Only the first
    // character moves — see `capitalizeOpening`.
    expect(screen.getByText(capitalizeOpening(reason))).toBeInTheDocument();
    expect(screen.getByText(new RegExp("there is nothing to compare against yet"))).toBeInTheDocument();
    expect(container.querySelector("[data-delta-absent]")).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/* The grid at Monitors scale                                            */
/* ------------------------------------------------------------------ */

describe("the tile grid is ordered, and says how", () => {
  it("puts the monitors that moved first and keeps the server's order inside each band", () => {
    const tiles = MONITORS.value?.tiles ?? [];
    const ordered = orderTilesForGrid(tiles);
    // The RULE, not one capture's census: monitors that moved come first,
    // then the ones that held still, then the ones with nothing to
    // compare against. Written as a band index so it survives a capture
    // where a different number of monitors moved — which is the ordinary
    // case, and which used to rewrite this test every load.
    const band = (t: (typeof tiles)[number]) =>
      t.delta?.material === true ? 0 : t.delta?.comparable === true ? 1 : 2;
    const bands = ordered.map(band);
    expect(bands).toEqual([...bands].sort((a, b) => a - b));
    expect(bands[0]).toBe(0);
    expect(bands.at(-1)).toBe(2);
    // Nothing is lost and nothing is duplicated by the sort.
    expect(ordered.map((t) => t.pinId).sort()).toEqual(tiles.map((t) => t.pinId).sort());
  });

  it("counts the grid in the same bands it orders it by", () => {
    const tiles = MONITORS.value?.tiles ?? [];
    const bandOf = (t: (typeof tiles)[number]) =>
      t.status !== "ok"
        ? "unavailable"
        : t.delta === undefined || !t.delta.comparable
          ? "silent"
          : t.delta.material || (t.delta.direction !== "flat" && t.delta.delta !== 0)
            ? "moved"
            : "unchanged";
    const moved = tiles.filter((t) => bandOf(t) === "moved").length;
    const unchanged = tiles.filter((t) => bandOf(t) === "unchanged").length;
    const silent = tiles.filter((t) => bandOf(t) === "silent").length;
    // Counted off the same predicates the sort uses, so the census cannot
    // disagree with the order it captions — which is the whole claim.
    expect(tileCensus(tiles)).toEqual([
      `${moved} moved`,
      `${unchanged} unchanged`,
      `${silent} with nothing to compare`,
    ]);
  });
});

/**
 * A MONITOR IS ONE TAB STOP, AND OPENING IT IS THE ORDINARY GESTURE.
 *
 * The retired grid got that property with a bespoke roving-tabindex
 * pattern: the card took `tabIndex={0}`, its five controls were forced to
 * `-1`, Enter "entered" it and Escape left. That existed because ~5
 * controls per card is ~100 tab stops across twenty monitors.
 *
 * It is a disclosure now and the browser's own semantics do the work.
 * Collapsed, a monitor has exactly one control — the tile itself. Opened,
 * its controls are ordinary and in reading order. Escape still closes, and
 * still hands focus back to the control that opened it.
 */
describe("a monitor is one tab stop, and opens in place", () => {
  it("is a single disclosure control when it is closed", () => {
    const tile = (MONITORS.value?.tiles ?? [])[0];
    const { container } = draw(
      <ul>
        <DigestTile tile={tile} moved={false} expanded={false} onToggle={() => {}} />
      </ul>,
    );
    const controls = [...container.querySelectorAll<HTMLElement>("a[href], button")];
    expect(controls).toHaveLength(1);
    expect(controls[0]).toHaveAttribute("aria-expanded", "false");
  });

  it("names the monitor and its value on the control that opens it", () => {
    const tile = (MONITORS.value?.tiles ?? [])[0];
    draw(
      <ul>
        <DigestTile tile={tile} moved={false} expanded={false} onToggle={() => {}} />
      </ul>,
    );
    // Named by its own content rather than by an `aria-label`: the label,
    // the figure with its marks and the movement are all things a reader
    // who cannot see the card is owed, and an `aria-label` would replace
    // every one of them with a shorter sentence.
    const control = screen.getByRole("button", { expanded: false });
    expect(control).toHaveAccessibleName(new RegExp(escapeRe(readableLabel(tile.label))));
    expect(control).toHaveAccessibleName(new RegExp(escapeRe(tile.valueText)));
    expect(control).toHaveAccessibleName(/Show this monitor's detail/);
    expect(control).toHaveAttribute("aria-describedby", "home-monitor-hint");
  });

  it("puts its controls in the tab order once it is open, in reading order", () => {
    const tile = (MONITORS.value?.tiles ?? [])[0];
    const { container } = drawTile(tile);
    const controls = [...container.querySelectorAll<HTMLElement>("a[href], button")];
    // The disclosure, then the detail's own — none of them hidden behind a
    // second gesture, and none of them at a negative tabindex.
    expect(controls.length).toBeGreaterThan(2);
    expect(controls.every((el) => el.tabIndex >= 0)).toBe(true);
    expect(controls[0]).toHaveAttribute("aria-expanded", "true");
  });

  it("closes on Escape from anywhere inside it", () => {
    const tile = (MONITORS.value?.tiles ?? [])[0];
    const onToggle = vi.fn();
    const { container } = draw(
      <ul>
        <DigestTile tile={tile} moved={false} expanded onToggle={onToggle} />
      </ul>,
    );
    // Fired from the LAST control in the panel, which is the one furthest
    // from the disclosure: the key is handled on the monitor, not on the
    // button that opened it.
    const controls = container.querySelectorAll<HTMLElement>("a[href], button");
    fireEvent.keyDown(controls[controls.length - 1], { key: "Escape" });
    expect(onToggle).toHaveBeenCalledTimes(1);
  });
});

/* ------------------------------------------------------------------ */
/* A failed read is never a disabled control                           */
/* ------------------------------------------------------------------ */

/**
 * ROUND 8's LEAD UI DEFECT, closed at the client.
 *
 * Live, `GET /v1/monitors/pins` 500'd off one monitor whose threshold unit was
 * `days`, the store swallowed it in an empty `catch {}`, and "Change what
 * it takes to brief you" rendered `disabled` on all thirty tiles with no
 * sentence anywhere on the page. Three reviewers reported it as a dead
 * button; nobody could tell it from a feature that was switched off.
 *
 * The server-side half is somebody else's fix and it does not close this:
 * a read that CAN fail must present as a failed read whenever it does.
 */
describe("a monitor whose settings could not be read says so", () => {
  const tile = () => (MONITORS.value?.tiles ?? [])[0];

  /**
   * THE PANEL, DIRECTLY. It used to be a popover behind a `…` trigger on a
   * card in the retired grid; it is rendered inline inside the monitor's
   * own expanded detail now — the reader has already opened this monitor,
   * so a second click into a floating panel bought nothing. Same content,
   * same three states, one less step.
   */
  const drawSettings = (t: MonitorsTile, pin?: MonitorsPin) =>
    draw(<MonitorManagement tile={t} {...(pin ? { pin } : {})} />);

  it("mocks the 500: the sentence renders and the control offers the read again", async () => {
    const listMonitorsPins = vi
      .fn()
      .mockRejectedValueOnce(new Error("GET /v1/monitors/pins failed: 500 Internal Server Error"))
      .mockResolvedValue([]);
    useSessionStore.setState({
      driver: { submit: async () => {}, listMonitorsPins } as unknown as never,
    });
    await useSessionStore.getState().loadMonitors();
    // The store keeps the server's own sentence rather than dropping it.
    expect(useSessionStore.getState().monitorsError).toContain("500");
    expect(useSessionStore.getState().monitorsLoaded).toBe(false);

    const t = tile();
    drawSettings(t);

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      /Could not read this monitor's settings — reload to try again/,
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
    const retry = screen.getByRole("button", { name: /Read this monitor's settings again/ });
    fireEvent.click(retry);
    expect(listMonitorsPins).toHaveBeenCalledTimes(2);
  });

  it("says so too when the read succeeded without this monitor in it", async () => {
    // The other live failure mode behind the same symptom: a 200 whose
    // list did not contain the pin the tile was drawn from.
    useSessionStore.setState({
      driver: { submit: async () => {}, listMonitorsPins: async () => [] } as unknown as never,
    });
    await useSessionStore.getState().loadMonitors();
    expect(useSessionStore.getState().monitorsLoaded).toBe(true);
    expect(useSessionStore.getState().monitorsError).toBeNull();

    const t = tile();
    drawSettings(t);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      /Could not read this monitor's settings — reload to try again/,
    );
    expect(alert).toHaveTextContent(/came back without this monitor in it/);
  });

  it("opens the editor the moment the settings ARE in hand", async () => {
    const pin = mapMonitorsPin(live.pins.pins[0]);
    expect(pin).not.toBeNull();
    const t = { ...tile(), pinId: pin!.pinId };
    drawSettings(t, pin!);
    const change = await screen.findByRole("button", {
      name: /Change what it takes to brief you/,
    });
    expect(change).toBeEnabled();
    fireEvent.click(change);
    expect(
      await screen.findByRole("button", { name: /Save and restart this monitor/ }),
    ).toBeInTheDocument();
  });

  it("states the recommended level as the number the wire published", async () => {
    // THE WHOLE POINT OF `recommended_threshold`. Before it existed the
    // default option read "Tell me about meaningful changes" on every
    // monitor in the product — honest, and vaguer than the rule the
    // monitor actually applies, because the gate reached this client only
    // inside a caption (docs/client-language.md §2.1).
    const raw = (live.pins.pins as { recommended_threshold?: { text: string } }[]).find(
      (entry) => entry.recommended_threshold?.text,
    );
    expect(raw, "the capture must carry a published recommendation").toBeDefined();
    const pin = mapMonitorsPin(raw)!;
    expect(pin.recommendedThreshold?.text).toBe(raw!.recommended_threshold!.text);

    const t = { ...tile(), pinId: pin.pinId };
    drawSettings(t, pin);
    fireEvent.click(
      await screen.findByRole("button", { name: /Change what it takes to brief you/ }),
    );
    expect(
      await screen.findByText(
        `Tell me when it moves more than ${raw!.recommended_threshold!.text}`,
      ),
    ).toBeInTheDocument();
    // And whose recommendation it is, and that it is not binding.
    expect(screen.getByText(/Revi's recommended level for .*\. You can change it anytime\./))
      .toBeInTheDocument();
    // Never the adjective the number replaced.
    expect(screen.queryByText(/governed threshold|standard threshold|the pack's/i)).toBeNull();
  });
});

/* ------------------------------------------------------------------ */
/* The controls that end a monitor, and the ones that start one          */
/* ------------------------------------------------------------------ */

/**
 * "STOP MONITORING THIS" WAS ONE IRREVERSIBLE CLICK.
 *
 * The reassurance copy landed and is good — "Nothing is deleted. The loads
 * this monitor has already been briefed on stay readable" — but it sat UNDER
 * the button, read on the way past rather than acknowledged. Filed monitors
 * 8, 9 and 10.
 *
 * There is no undo to offer instead: re-registering a monitor resets the
 * baseline "since you started monitoring" measures from, so an undo would
 * quietly hand back a different monitor. The two-step is the honest control.
 */
describe("ending a monitor is armed before it fires", () => {
  const tile = () => (MONITORS.value?.tiles ?? [])[0];
  const drawSettings = (t: MonitorsTile) => draw(<MonitorManagement tile={t} />);

  it("does not remove the monitor on the first click", async () => {
    const deleteMonitorsPin = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({
      driver: { submit: async () => {}, deleteMonitorsPin } as unknown as never,
    });
    const t = tile();
    drawSettings(t);
    fireEvent.click(await screen.findByRole("button", { name: /Stop monitoring this/ }));
    expect(deleteMonitorsPin).not.toHaveBeenCalled();

    // The reassurance is now the thing being acknowledged, above the
    // confirming control rather than below the firing one.
    const armed = document.querySelector("[data-stop-monitor-armed]");
    expect(armed).not.toBeNull();
    expect(armed).toHaveTextContent(/Nothing is deleted/);
    expect(armed).toHaveTextContent(new RegExp(escapeRe(t.label)));
  });

  it("removes it on the second, and the reversible choice is offered first", async () => {
    const deleteMonitorsPin = vi.fn().mockResolvedValue(undefined);
    useSessionStore.setState({
      driver: { submit: async () => {}, deleteMonitorsPin } as unknown as never,
    });
    const t = tile();
    drawSettings(t);
    fireEvent.click(await screen.findByRole("button", { name: /Stop monitoring this/ }));

    const keep = await screen.findByRole("button", { name: /Keep monitoring/ });
    const stop = screen.getByRole("button", { name: /Yes, stop monitoring/ });
    // Reversible first in the DOM, so it is first for a keyboard too.
    expect(keep.compareDocumentPosition(stop) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    fireEvent.click(keep);
    expect(document.querySelector("[data-stop-monitor-armed]")).toBeNull();
    expect(deleteMonitorsPin).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Stop monitoring this/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Yes, stop monitoring/ }));
    expect(deleteMonitorsPin).toHaveBeenCalledWith(t.pinId);
  });
});

/**
 * THE MONITOR AFFORDANCES ARE PRESENT, not hovered into existence.
 *
 * Filed monitors 7-10 by three reviewers: `MonitorThis` defaults to the
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
describe("monitor affordances are visible without a pointer", () => {
  it("draws the control that opens a monitor at full opacity", () => {
    // The settings trigger this replaces was `opacity-0
    // group-hover:opacity-100` — no control at all on a touch screen, in a
    // screenshot or on a projector, and the only way into a monitor's own
    // settings. The whole tile is that control now, and the rule holds.
    const { container } = draw(
      <ul>
        <DigestTile
          tile={(MONITORS.value?.tiles ?? [])[0]}
          moved={false}
          expanded={false}
          onToggle={() => {}}
        />
      </ul>,
    );
    const trigger = container.querySelector<HTMLElement>("button")!;
    expect(trigger).not.toBeNull();
    expect(trigger.className).not.toMatch(/\bopacity-0\b/);
    expect(trigger.className).not.toMatch(/group-hover:opacity/);
  });

  it("draws the compact Monitor this at full opacity", () => {
    useSessionStore.setState({
      driver: {
        submit: async () => {},
        createMonitorsPin: async () => ({}),
        listMonitorsPins: async () => [],
      } as unknown as never,
    });
    const { container } = draw(
      <MonitorThis
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

  it("splits the monitors it walked, and says so when they do not add up", () => {
    // Two monitors the walk counted nowhere at all. Computed off the
    // capture's own parts rather than pinned to a number: how many
    // monitors moved changes every load, and what is under test is that
    // the census refuses to let the split silently not add up.
    const held = BRIEF.value!.immaterial;
    const accounted =
      BRIEF.value!.entries.filter((e) => e.kind === "pin_movement" || e.kind === "rank_flip")
        .length +
      (held.withheldByKind.pin_movement ?? 0) +
      (held.withheldByKind.rank_flip ?? 0) +
      held.pinMovements +
      held.notYetComparable +
      held.unavailable;
    const brief = { ...BRIEF.value!, pinsEvaluated: accounted + 2 };
    const { container, unmount } = draw(<BriefPanel brief={brief} />);
    expect(container.querySelector("[data-walk-census]")?.textContent).toContain(
      "2 not accounted for",
    );
    unmount();

    // …and when the server accounts for those two, the split closes and
    // the census stops saying it does not.
    const closed = {
      ...brief,
      immaterial: { ...brief.immaterial, notYetComparable: held.notYetComparable + 2 },
    };
    const second = draw(<BriefPanel brief={closed} />);
    const census = second.container.querySelector("[data-walk-census]")!;
    expect(census.textContent).toContain(
      `${held.notYetComparable + 2} with nothing to compare against yet`,
    );
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
      screen.getByText(
        "1 of the 2 consecutive loads Revi requires before it will call this confirmed",
      ),
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
    // viewport, so "Save and restart this monitor" sat 2px past the bottom
    // with no scroll container and nothing under it to scroll to.
    draw(
      <MonitorSensitivityForm
        submitLabel="Save and restart this monitor"
        pending={false}
        onSubmit={() => {}}
        onCancel={() => {}}
      />,
    );
    const submit = screen.getByRole("button", { name: "Save and restart this monitor" });
    const row = submit.closest("div")?.parentElement;
    expect(row?.className).toContain("sticky");
    expect(row?.className).toContain("bottom-0");
  });

  it("keeps the server's refusal beside the control that produced it", () => {
    const refusal = "a threshold in 'cents' is only honest for a 'money_cents' contract";
    draw(
      <MonitorSensitivityForm
        submitLabel="Start monitoring"
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
    const tile = (MONITORS.value?.tiles ?? []).find((t) => t.warnings.length > 0)!;
    expect(tile, "the live capture must contain a tile with caveats").toBeDefined();
    const { container } = drawTile(tile);
    const atom = container.querySelector("[data-integrity-atom]")!;
    expect(atom.textContent).not.toMatch(/things to know\d/);
  });

  it("draws the measured window in solid muted ink, not at 80%", () => {
    // `--muted-foreground` at 80% measures 3.48:1 on card, 3.27:1 on the
    // page and 3.16:1 on sunken — below the 4.5:1 floor at 12px. Solid:
    // 5.24 / 4.80 / 4.57.
    const tile = (MONITORS.value?.tiles ?? []).find((t) => t.windowStart !== undefined)!;
    const { container } = drawTile(tile);
    const html = container.innerHTML;
    expect(html).not.toContain("text-muted-foreground/80");
  });
});

function escapeRe(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
