/**
 * HOW HOME IS ORDERED, AND WHY THE ORDER MOVES.
 *
 * Home opens on what the platform detected, because on day one that is the
 * only thing a tenant has: no monitors, no thresholds, nobody has said what
 * matters to them yet. The detected worklist is the product's opening
 * claim and it is the whole page for a tenant with nothing pinned.
 *
 * But a tenant who has set up their own monitors has told this app what
 * they care about, and a landing page that keeps burying their answer
 * under a generic feed is a landing page that never becomes theirs. So the
 * order is a function of the payloads already on the wire:
 *
 *   NO MONITORS            → the anomalies are the page; the monitors zone
 *                            is an invitation underneath it.
 *   MONITORS THAT MOVED    → the digest goes ABOVE the anomalies. Something
 *                            they asked to be told about changed at this
 *                            load, and that outranks a ranked feed.
 *   MONITORS, NOTHING MOVED→ the digest stays below. A quiet monitor is a
 *                            good morning, not a headline.
 *
 * NOTHING NEW IS STORED TO DECIDE THIS. "Moved" is the governed decision
 * already published twice over: `delta.material` on each evaluated tile,
 * and the brief's own `pin_movement` / `rank_flip` entries, which are the
 * lines that cleared the pack's materiality gate. Re-deriving movement from
 * raw values here would be this client inventing a threshold the pack owns
 * — the exact defect `DeltaLine` and the tile bands exist to prevent.
 *
 * The two sources are UNIONED rather than one preferred over the other,
 * and each covers a real hole in the other. The brief caps its entries, so
 * a monitor that moved can be missing from it; and the brief carries
 * movements for pins whose tile may not be in the page's tile list at all.
 * A union can only be too generous, and being too generous here promotes a
 * zone rather than hiding one.
 */

import { TILE_BANDS, tileBand, type BriefEntry, type MonitorsTile } from "@/lib/monitors";

/** Which of Home's two evolving zones is read first. */
export type HomeZoneOrder = "monitors_first" | "anomalies_first";

export interface HomeShape {
  /** How many monitors this load evaluated. Zero is the invitation state. */
  monitorCount: number;
  /** The pins that moved materially at this load, by pin id. */
  movedPinIds: string[];
  /** Which zone leads. */
  order: HomeZoneOrder;
  /** Nothing is pinned yet — the monitors zone is an invitation. */
  invitation: boolean;
}

/**
 * The brief entry kinds that are a monitor of somebody's changing under
 * them.
 *
 * `rank_flip` is included and is not a movement: it is the ranked monitor
 * headlining a DIFFERENT cell from last load ("State Medicaid MCO overtook
 * Ashvale as your worst payer"). It is still the strongest thing that can
 * happen to a monitor at a load — the number may be identical and mean
 * something else entirely — so it promotes the zone exactly as a movement
 * does.
 */
const MOVEMENT_KINDS: ReadonlySet<string> = new Set(["pin_movement", "rank_flip"]);

/** The pins that moved materially at this load, from both published sources. */
export function movedPinIds(
  tiles: readonly MonitorsTile[],
  entries: readonly BriefEntry[],
): string[] {
  const moved = new Set<string>();
  for (const tile of tiles) {
    // The governed band, not a re-derived comparison: `material` is the
    // server's own flag and `tileBand` is the one place it is read.
    if (tileBand(tile) === TILE_BANDS.material) moved.add(tile.pinId);
  }
  for (const entry of entries) {
    if (entry.pinId !== undefined && MOVEMENT_KINDS.has(entry.kind)) moved.add(entry.pinId);
  }
  return [...moved];
}

/**
 * Home's shape at this load.
 *
 * Both inputs default to empty, which is the honest reading of a query that
 * has not answered yet: no monitors are KNOWN, so the page opens on the
 * anomalies and re-orders itself when the monitors land. It never opens on
 * a digest it cannot fill.
 */
export function homeShape({
  tiles = [],
  entries = [],
}: {
  tiles?: readonly MonitorsTile[];
  entries?: readonly BriefEntry[];
}): HomeShape {
  const moved = movedPinIds(tiles, entries);
  const monitorCount = tiles.length;
  if (monitorCount === 0) {
    return { monitorCount: 0, movedPinIds: [], order: "anomalies_first", invitation: true };
  }
  return {
    monitorCount,
    movedPinIds: moved,
    order: moved.length > 0 ? "monitors_first" : "anomalies_first",
    invitation: false,
  };
}
