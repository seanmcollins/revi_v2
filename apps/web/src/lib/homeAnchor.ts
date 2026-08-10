/**
 * WHICH ONE THING ON THIS LOAD GETS DRAWN.
 *
 * Home's zones were four blocks of small type. Nothing on the page was
 * larger than 17px, nothing was a picture, and the owner's first reading of
 * it was that the app had failed to render. The fix is not decoration: it
 * is to take the single most important object on this load and draw the
 * figure it already published, at size, with its drill affordance intact.
 *
 * THE RANK ORDER, AND THE HOLE IN IT.
 *
 * The intended order is "the top-ranked LEAD, then the next lead that has a
 * chart". It cannot be satisfied from the worklist today and the reason is
 * structural rather than a missing field: a worklist card is not an
 * investigation and does not have one. It carries a typed `drill_spec`, and
 * opening it SUBMITS that spec — the investigation is minted at that
 * moment. `GET /v1/portfolio/latest` publishes no `investigation_id` on any
 * of the thirty-three cards because there is none to publish.
 *
 * So the chain is walked and it falls through, deliberately and visibly:
 *
 *   1. LEADS. Any candidate carrying an investigation of its own, in the
 *      order the ranking put them in. Today: none, on every card.
 *   2. THIS LOAD'S BRIEF, in the brief's own order. These are the entries
 *      the platform itself decided were the news at this load, and the
 *      monitor movements among them DO carry an investigation each
 *      (`pin_movement`, `rank_flip`).
 *   3. THE MONITORS, in their published order, for a load whose brief was
 *      quiet — a quiet morning still deserves a figure, and the tile's
 *      investigation is a real one.
 *
 * WHATEVER IS CHOSEN IS NAMED. The caption says which object the figure
 * belongs to and links to it, because a chart on a landing page with no
 * stated subject is the exact shape of an invented dashboard. If nothing in
 * the chain carries an investigation, NOTHING is drawn — there is no
 * placeholder, no skeleton that never resolves and no example chart.
 */

import type { BriefEntry, MonitorsTile } from "@/lib/monitors";

/** The object a drawn figure belongs to. */
export interface HomeAnchor {
  investigationId: string;
  /** What to call it on screen — already the object's own title. */
  title: string;
  /** Which step of the chain produced it, for the caption's wording. */
  source: "lead" | "brief" | "monitor";
  /** The brief entry kind, when the brief chose it. */
  kind?: string;
}

/**
 * The first candidate with a real investigation behind it.
 *
 * Order is the argument's own order in every step: nothing here re-ranks a
 * worklist, re-orders a brief, or prefers one monitor over another. The
 * chain is a fallback, not a preference.
 */
export function homeAnchor({
  leads = [],
  entries = [],
  tiles = [],
}: {
  /**
   * Worklist cards in ranked order, each with whatever investigation the
   * payload published for it. Typed as optional because the wire publishes
   * none today — see the note above.
   */
  leads?: readonly { investigationId?: string; title: string }[];
  entries?: readonly BriefEntry[];
  tiles?: readonly MonitorsTile[];
}): HomeAnchor | undefined {
  for (const lead of leads) {
    if (lead.investigationId !== undefined && lead.investigationId !== "") {
      return { investigationId: lead.investigationId, title: lead.title, source: "lead" };
    }
  }
  for (const entry of entries) {
    if (entry.investigationId !== undefined && entry.investigationId !== "") {
      return {
        investigationId: entry.investigationId,
        title: entry.title,
        source: "brief",
        kind: entry.kind,
      };
    }
  }
  for (const tile of tiles) {
    if (tile.status === "ok" && tile.investigationId !== undefined && tile.investigationId !== "") {
      return { investigationId: tile.investigationId, title: tile.label, source: "monitor" };
    }
  }
  return undefined;
}
