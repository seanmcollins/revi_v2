/**
 * THE LANE SPLIT, as the server decided it.
 *
 * Lifted out of `PortfolioPanel` unchanged when Home began rendering the
 * same worklist at a different width: two components splitting one list two
 * ways is how a card goes missing from one of them.
 *
 * `lanes` carries its own membership AND its own order (`anomalyIds` is the
 * ranking), so this follows it rather than re-deriving a ranking from
 * scores the client does not own. Two rules keep it honest:
 *
 *   a lane names a card the snapshot does not carry → skipped silently,
 *     because there is nothing to draw;
 *   a card no lane names → kept, in a trailing ungrouped section. A
 *     worklist that quietly drops work is the one failure it cannot have.
 *
 * With no lanes published (mock mode, or a deployment that does not split)
 * everything lands in a single unlabelled group.
 */

import type { PortfolioItem, PortfolioLane } from "@/lib/mock/portfolio";

/** One rendered section of a worklist: a published lane, or the leftovers. */
export interface LaneGroup {
  id: string;
  lane?: PortfolioLane;
  items: PortfolioItem[];
}

export function groupByLane(items: PortfolioItem[], lanes: PortfolioLane[]): LaneGroup[] {
  if (lanes.length === 0) return items.length > 0 ? [{ id: "all", items }] : [];
  const byReferent = new Map(items.map((item) => [item.referent, item]));
  const claimed = new Set<string>();
  const groups: LaneGroup[] = [];
  for (const lane of lanes) {
    const laneItems: PortfolioItem[] = [];
    for (const id of lane.anomalyIds) {
      const item = byReferent.get(id);
      if (item === undefined || claimed.has(id)) continue;
      claimed.add(id);
      laneItems.push(item);
    }
    if (laneItems.length > 0) groups.push({ id: lane.id, lane, items: laneItems });
  }
  const orphans = items.filter((item) => !claimed.has(item.referent));
  if (orphans.length > 0) groups.push({ id: "ungrouped", items: orphans });
  return groups;
}
