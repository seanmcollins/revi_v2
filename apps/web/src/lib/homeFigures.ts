/**
 * THE FOUR NUMBERS HOME OPENS ON.
 *
 * Not a new measurement — a READING of the worklist snapshot that the
 * detected-anomalies zone already consumes. Every figure below is a field
 * the server published, or a count of the cards it published; nothing here
 * sums a page of cards into a total, re-ranks anything, or dates anything
 * the payload left undated.
 *
 * WHY IT EXISTS. The zone opened with the cash-timing split rendered as
 * three 12px sentences with dotted underlines. All three facts were on
 * screen and none of them was legible from two feet away, so the surface
 * whose whole claim is "here is what is still catchable" answered it in
 * footnote type. The owner's reading of the live page was that it looked
 * broken. This is the same three facts plus the count, at a size that says
 * which one the morning is about.
 *
 * FOUR RULES, and each of them is a defect this file refuses to reproduce.
 *
 *   THE LANES DECIDE, NOT THE PAGE. `cashTimingLanes` describe the whole
 *     population; `items` is a page of it. Every dollar figure comes off a
 *     lane, so the band cannot drift from the sentences under it.
 *   RECOVERABLE AND DETECTED ARE DIFFERENT CLAIMS. A lane publishing a
 *     governed recoverable estimate is read as "what is left to save" and
 *     wears the `~` that says it is an estimate; a lane without one is read
 *     as "what went wrong". They are never silently interchanged.
 *   A DEADLINE IS A REAL DATE OR IT IS NOTHING. Only `soonestDeadlineDate`,
 *     which the server sets from published limits and never from a
 *     projection. A lane with no dated member contributes no deadline, and
 *     when no lane has one the figure is absent rather than blank.
 *   A COUNT MUST RECONCILE. "Open" is the lead status the payload carries,
 *     not an interpretation of it, and the cards that are NOT open are
 *     stated beside the count so the two add up to the list.
 */

import type { PortfolioSnapshotData } from "@/lib/contract";
import { formatWholeDollars, mediumDate } from "@/lib/format";
import type { PortfolioItem, PortfolioLane } from "@/lib/mock/portfolio";

/** One cell of the band: a label, a figure, its mark, its context. */
export interface KeyFigureModel {
  key: string;
  label: string;
  /** Already formatted, already carrying its `~` / `≤`. */
  value: string;
  /** What kind of number this is, when it is not a plain measurement. */
  mark?: string;
  context?: string;
  /** The server's own definition of the lane, verbatim, when it has one. */
  labelDetail?: string;
  /** The one figure the page is about. */
  emphasis?: boolean;
}

function lane(lanes: readonly PortfolioLane[], id: string): PortfolioLane | undefined {
  return lanes.find((l) => l.id === id);
}

/**
 * A lane's dollars, in the terms the lane itself supports.
 *
 * `~` is this product's existing mark for a governed recoverable estimate —
 * the worklist cards print "~$169,306 recoverable" — and it travels to
 * display size for the same reason a `≤` does: a number that loses the mark
 * that says what KIND of number it is has been upgraded on the way to being
 * read.
 */
function laneFigure(
  target: PortfolioLane,
  key: string,
  label: string,
  emphasis: boolean,
): KeyFigureModel {
  const recoverable = target.recoverableCents;
  const estimated = recoverable !== undefined;
  const cents = recoverable ?? target.impactCents;
  return {
    key,
    label,
    value: `${estimated ? "~" : ""}${formatWholeDollars(cents)}`,
    // The MARK is one word, because it rides on the same line as a 30px
    // numeral and a two-word mark wraps under it — which reads as a second
    // figure rather than as a qualifier on the first. What KIND of dollars
    // these are goes in the context line under it, where it has room.
    mark: estimated ? "estimated" : "detected",
    context: `${estimated ? "Recoverable" : "Detected"}, across ${target.itemCount} lead${
      target.itemCount === 1 ? "" : "s"
    }`,
    // The lane's own sentence, verbatim. It is what separates "still
    // catchable" from a slogan: the server says the cash effect has not
    // landed yet, and working these changes what gets paid.
    ...(target.description !== "" ? { labelDetail: target.description } : {}),
    ...(emphasis ? { emphasis: true } : {}),
  };
}

/** The lead statuses that mean nobody has picked this card up yet. */
const UNWORKED: ReadonlySet<string> = new Set(["open", "regressed"]);

function openLeads(items: readonly PortfolioItem[]): KeyFigureModel | undefined {
  if (items.length === 0) return undefined;
  const open = items.filter(
    (item) => item.leadStatus === undefined || UNWORKED.has(item.leadStatus),
  ).length;
  const worked = items.length - open;
  return {
    key: "open_leads",
    label: "Open leads",
    value: String(open),
    ...(worked > 0
      ? {
          context: `${worked} of ${items.length} already acknowledged, being worked or claimed fixed`,
        }
      : { context: `Of ${items.length} on this load's worklist` }),
  };
}

function safeMediumDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
}

/**
 * The soonest REAL dated limit anywhere in the snapshot, and how many cards
 * carry a date at all.
 *
 * Across lanes rather than inside one, because the question "is anything
 * about to miss a deadline" is not a question about cash timing — and
 * because the still-catchable lane is not always the one holding the
 * nearest limit. `soonestDeadlineDays` is the server's own arithmetic
 * against the data load; recomputing it here from today's clock would make
 * the band disagree with every sentence under it the day after a load
 * lands.
 */
function soonestDeadline(lanes: readonly PortfolioLane[]): KeyFigureModel | undefined {
  const dated = lanes.filter(
    (l) => l.soonestDeadlineDate !== undefined && l.soonestDeadlineDays !== undefined,
  );
  if (dated.length === 0) return undefined;
  const soonest = dated.reduce((best, l) =>
    (l.soonestDeadlineDays ?? 0) < (best.soonestDeadlineDays ?? 0) ? l : best,
  );
  const days = soonest.soonestDeadlineDays ?? 0;
  const plural = Math.abs(days) === 1 ? "day" : "days";
  return {
    key: "deadline",
    label: "Soonest deadline",
    // A limit in the PAST is a loss, not a horizon, and "−47 days" is a
    // number nobody reads as one. Said in words, in the same ink as the
    // rest: amber is a verdict colour and a dashboard figure is not a
    // verdict.
    value: days < 0 ? `${Math.abs(days)} ${plural} ago` : `In ${days} ${plural}`,
    ...(days < 0 ? { mark: "already passed" } : {}),
    context: `${safeMediumDate(soonest.soonestDeadlineDate ?? "")} · dated on ${soonest.datedItemCount} of ${soonest.itemCount} in ${soonest.label.toLowerCase()}`,
  };
}

/**
 * The band, in reading order, from the snapshot the zone already holds.
 *
 * Returns only the cells the payload supports: a snapshot with no
 * cash-timing split publishes no dollars, and a band of four boxes with two
 * of them blank is exactly the "is this broken?" impression this exists to
 * remove.
 */
export function keyFigures(snapshot: PortfolioSnapshotData | undefined): KeyFigureModel[] {
  if (snapshot === undefined) return [];
  const lanes = snapshot.cashTimingLanes ?? [];
  const figures: KeyFigureModel[] = [];

  const preCash = lane(lanes, "pre_cash");
  if (preCash !== undefined) {
    figures.push(laneFigure(preCash, "still_catchable", "Still catchable", true));
  }

  const alreadyHit = lane(lanes, "already_hit");
  if (alreadyHit !== undefined) {
    figures.push(laneFigure(alreadyHit, "already_hit", "Already hit cash", false));
  }

  const open = openLeads(snapshot.items);
  if (open !== undefined) figures.push(open);

  const deadline = soonestDeadline(lanes);
  if (deadline !== undefined) figures.push(deadline);

  return figures;
}
