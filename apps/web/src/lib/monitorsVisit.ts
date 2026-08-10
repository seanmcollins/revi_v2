/**
 * BRIEF-FIRST COLD START — has this browser seen the brief for this load?
 *
 * Monitors is a load-over-load product, so "new" means one thing only: a
 * data load this browser has not been briefed on. Not a time interval, not
 * a session count, not "every morning" — a brief re-shown for a load
 * already read would be the notification badge that trains people to
 * dismiss things.
 *
 * The record is per-browser (`localStorage`), which is honestly the wrong
 * grain and is stated rather than hidden: pins are tenant-scoped in v1 and
 * there is no per-user store to hang this on, so a second browser opens on
 * the brief once. That is the failure worth having — the alternative is
 * one machine reading the brief and everyone else's app never opening on
 * it.
 *
 * Both functions are safe in a server render and in a browser with storage
 * disabled: they answer "not seen" and record nothing, which opens the
 * brief. Erring toward showing it is the honest default for a surface
 * whose whole job is to say what changed.
 */

const KEY = "revi-monitors-seen-watermark";

/** The load this browser was last briefed on, if any. */
export function lastSeenMonitors(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

/**
 * Record that the brief for this load has been RENDERED.
 *
 * Called when the brief payload arrives, not when the route is navigated
 * to: a cold start that redirected here and then failed to read would
 * otherwise mark the load read, and the next morning would open on the
 * thread with the brief never shown.
 */
export function markMonitorsSeen(watermarkId: string): void {
  if (typeof window === "undefined" || watermarkId === "") return;
  try {
    window.localStorage.setItem(KEY, watermarkId);
  } catch {
    // Storage unavailable (privacy mode). The next visit opens on the
    // brief again, which is the harmless direction to fail in.
  }
}

/** Is there a load this browser has not been briefed on? */
export function hasUnseenLoad(newestWatermarkId: string | undefined): boolean {
  if (newestWatermarkId === undefined || newestWatermarkId === "") return false;
  return lastSeenMonitors() !== newestWatermarkId;
}

/* ------------------------------------------------------------------ */
/* What used to live here: the redirect                                */
/* ------------------------------------------------------------------ */

/*
 * A `noteMonitorsRedirect` / `consumeMonitorsRedirect` pair recorded that
 * the cold start had pushed somebody from `/` to `/monitors`, so the
 * destination could move focus and say where they were — a navigation
 * nobody asked for is silent to a screen reader otherwise.
 *
 * `/` is Home now and opens on the brief itself, so there is no navigation
 * to disclose and the pair is gone rather than left behind as dead
 * vocabulary. The a11y behaviour it existed for did NOT go: Home reads
 * `hasUnseenLoad` below, announces the headline through the app's own
 * polite region and moves focus to the zone carrying it.
 *
 * The two functions above stay because both still have live readers:
 * `hasUnseenLoad` drives the rail's "New load" dot and Home's announcement,
 * and `markMonitorsSeen` is written by both surfaces that render a brief.
 */
