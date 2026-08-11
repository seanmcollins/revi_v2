/**
 * YOUR MONITORS, AS A PLACE ON HOME RATHER THAN A ROUTE.
 *
 * `/monitors` was a second landing surface and the owner's reading of the
 * pair was that Home is the better one: "the /monitors view is pointless
 * and the current home view is just a superior version." So the route is
 * retired and the monitors keep their zone — the digest, which now expands
 * a tile in place into everything the full surface offered.
 *
 * What a retired route leaves behind is every control that pointed AT it:
 * the rail's entry, the ⌘K verb, the note under an answer that started a
 * monitor. None of them may become a link to nowhere and none of them may
 * become a link to the top of Home, which is a different zone. They all
 * become this: an anchor at `/#home-monitors`.
 *
 * A REAL FRAGMENT, NOT A CLICK HANDLER. The href is a genuine address, so
 * a middle-click opens it in a tab and a cold load lands on the zone (Home
 * reads the hash on arrival). The handler beside it is what a fragment
 * cannot do on its own: MOVE FOCUS. A fragment navigation scrolls without
 * focusing in every browser that has not shipped the fix, so the next Tab
 * would resume from the rail — which is the bug wearing a fix's clothes.
 */

import { scrollIntoViewRespectingMotion } from "@/lib/useReducedMotion";

/** The zone the digest renders into, and what the anchor lands on. */
export const MONITORS_ZONE_ID = "home-monitors";

/** `/#home-monitors`, as react-router wants it. */
export const MONITORS_ANCHOR = { pathname: "/", hash: `#${MONITORS_ZONE_ID}` } as const;

/**
 * Put the reader in the digest zone.
 *
 * Retried for a few frames rather than called once: the caller is usually
 * navigating to Home at the same moment, and the zone does not exist until
 * Home has mounted. It gives up quietly — on the mock fixture Home renders
 * no digest at all, and a focus call that never lands is better than one
 * that throws.
 */
export function focusMonitorsZone(): void {
  if (typeof window === "undefined") return;
  let tries = 0;
  const tick = (): void => {
    const zone = document.getElementById(MONITORS_ZONE_ID);
    if (zone) {
      zone.focus();
      scrollIntoViewRespectingMotion(zone, { block: "start" });
      return;
    }
    if (tries++ < 10) window.setTimeout(tick, 32);
  };
  window.setTimeout(tick, 0);
}
