/**
 * BRIEF-FIRST COLD START — has this browser seen the brief for this load?
 *
 * Rounds is a load-over-load product, so "new" means one thing only: a
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

const KEY = "revi-rounds-seen-watermark";

/** The load this browser was last briefed on, if any. */
export function lastSeenRounds(): string | null {
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
export function markRoundsSeen(watermarkId: string): void {
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
  return lastSeenRounds() !== newestWatermarkId;
}

/* ------------------------------------------------------------------ */
/* The redirect, as a fact the destination can read                    */
/* ------------------------------------------------------------------ */

const REDIRECT_KEY = "revi-rounds-redirected";

/**
 * ARRIVING SOMEWHERE YOU DID NOT ASK TO GO IS AN EVENT.
 *
 * The cold start pushes an analyst from `/` to `/rounds`. For a sighted
 * pointer user the page visibly changes; for a screen-reader user nothing
 * is announced and focus stays where it was, so the app silently becomes a
 * different app under a focus ring pointing at an element that no longer
 * exists. The redirect records itself here and Rounds consumes it — moving
 * focus to its own heading and saying, once, where they are.
 *
 * `sessionStorage`, not a query parameter: the destination is a permalink
 * people bookmark, and a `?redirected=1` in it would be re-announced every
 * time that bookmark was opened.
 */
export function noteRoundsRedirect(): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(REDIRECT_KEY, "1");
  } catch {
    // Storage unavailable. Rounds then behaves as if the analyst typed the
    // URL, which is the harmless direction to fail in.
  }
}

/** Was this visit a redirect? Answers once — reading clears it. */
export function consumeRoundsRedirect(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const value = window.sessionStorage.getItem(REDIRECT_KEY);
    if (value === null) return false;
    window.sessionStorage.removeItem(REDIRECT_KEY);
    return true;
  } catch {
    return false;
  }
}
