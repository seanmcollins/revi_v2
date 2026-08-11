"use client";

import { create } from "zustand";

/**
 * THE TWO SIDE PANES, AND WHY THEY COLLAPSE DIFFERENTLY.
 *
 * The workspace is three columns: sessions on the left, the answer in the
 * middle, evidence on the right. Both sides can go — but they are not the
 * same kind of thing, and collapsing them the same way would be a
 * symmetry that costs the reader something.
 *
 * The LEFT rail is wayfinding. It is how somebody starts a new chat, gets
 * back to Home, and reaches Monitors, and it carries the one indicator
 * that says whether the deployment is answering at all. A pane that can
 * vanish entirely takes all of that with it, so it collapses to a SLIM
 * ICON STRIP instead: narrower, still a nav landmark, every icon named.
 *
 * The RIGHT rail is on-demand depth. Evidence is the working behind an
 * answer, opened when a number is challenged and closed the rest of the
 * time, so it collapses ALL THE WAY to a thin edge tab. Nothing about the
 * answer needs it on screen.
 *
 * ---------------------------------------------------------------------
 * THE THREE STATES THIS MODULE KEEPS APART
 *
 * 1. `preference` — what this device wants, persisted in `localStorage`.
 *    Written only by a control whose whole job is the pane: the toggle at
 *    the pane's inner edge, the edge tab, `[` / `]`, the palette.
 *
 * 2. `chosen` — whether the preference was set BY HAND in this session.
 *    Until it is, a narrow viewport may auto-collapse over it; after it
 *    is, the reader's choice stands for the rest of the session no matter
 *    how the window is resized. A layout that keeps re-deciding after
 *    somebody has decided is a layout arguing with its user.
 *
 * 3. `evidenceTransient` — the rail forced open by an explicit gesture
 *    (a referent chip, the integrity line, "open evidence") while the
 *    persisted preference still says collapsed. Explicit intent wins for
 *    as long as the rail is open, and closing it returns to collapsed
 *    rather than quietly rewriting the preference. The alternative —
 *    letting a chip tap flip the persisted setting — means one click on a
 *    citation silently undoes a layout somebody chose, and they find out
 *    tomorrow when the workspace opens differently.
 *
 * There is no transient state for the left rail because there is no
 * gesture that needs one: nothing in an answer points at wayfinding.
 */

export type PaneId = "sessions" | "evidence";

/** Per-device, per-pane. `revi-*`, like every other key this app writes. */
export const PANE_STORAGE_KEYS: Record<PaneId, string> = {
  sessions: "revi-pane-sessions",
  evidence: "revi-pane-evidence",
};

/**
 * The thresholds are the workspace's own, not new ones.
 *
 * `Workspace` is documented as a desktop tool "designed down to 1280px",
 * and at 1280 its three columns are 16.5rem + 21rem of rails around a
 * 680px answer. Below that the answer column is the thing that shrinks,
 * so the left rail folds to its icon strip first — it gives back 216px
 * and loses nothing but labels. Below 1024 the evidence rail goes too,
 * which is the point at which the middle column would otherwise be
 * narrower than the prose measure it is set on.
 *
 * Left first, then right: wayfinding compresses; depth closes.
 */
export const SESSIONS_AUTO_COLLAPSE_BELOW = 1280;
export const EVIDENCE_AUTO_COLLAPSE_BELOW = 1024;

const AUTO_COLLAPSE_BELOW: Record<PaneId, number> = {
  sessions: SESSIONS_AUTO_COLLAPSE_BELOW,
  evidence: EVIDENCE_AUTO_COLLAPSE_BELOW,
};

/** Would this width fold this pane, absent a choice by hand? */
export function autoCollapses(pane: PaneId, viewportWidth: number | null): boolean {
  if (viewportWidth === null) return false;
  return viewportWidth < AUTO_COLLAPSE_BELOW[pane];
}

/* ------------------------------------------------------------------ */
/* Persistence                                                         */
/* ------------------------------------------------------------------ */

/**
 * A word, not a boolean-shaped string. `"collapsed"` / `"expanded"` reads
 * the same in devtools as it does here, and an unrecognised value is
 * treated as no preference at all rather than as `false`.
 */
export function readStoredPane(pane: PaneId): boolean | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(PANE_STORAGE_KEYS[pane]);
    if (raw === "collapsed") return true;
    if (raw === "expanded") return false;
    return null;
  } catch {
    // Storage unavailable (privacy mode): no preference, and the panes
    // still collapse for this session.
    return null;
  }
}

export function writeStoredPane(pane: PaneId, collapsed: boolean): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PANE_STORAGE_KEYS[pane], collapsed ? "collapsed" : "expanded");
  } catch {
    // Non-fatal: the choice still applies to this session.
  }
}

function storedPreferences(): Record<PaneId, boolean> {
  return {
    sessions: readStoredPane("sessions") ?? false,
    evidence: readStoredPane("evidence") ?? false,
  };
}

/* ------------------------------------------------------------------ */
/* The store                                                           */
/* ------------------------------------------------------------------ */

export interface PaneState {
  /** Persisted per-device preference: true = collapsed. */
  preference: Record<PaneId, boolean>;
  /** Set by hand in this session — from here on, resizing does not decide. */
  chosen: Record<PaneId, boolean>;
  /** Evidence forced open by an explicit gesture, preference untouched. */
  evidenceTransient: boolean;
  /** Last observed viewport width; null until something measures one. */
  viewportWidth: number | null;
  /**
   * True while a workspace is on screen to collapse. The palette offers
   * the two pane verbs only then — Home and Monitors mount the same
   * palette and have no evidence rail, and an action that silently does
   * nothing is worse than one that is not offered.
   */
  hostMounted: boolean;

  toggle: (pane: PaneId) => void;
  setPreference: (pane: PaneId, collapsed: boolean) => void;
  /** An "open evidence" gesture: opens without flipping the preference. */
  openEvidence: () => void;
  /** The rail was closed from elsewhere (a thread reset): drop the transient. */
  clearEvidenceTransient: () => void;
  setViewportWidth: (width: number) => void;
  setHostMounted: (mounted: boolean) => void;
  /** Re-read `localStorage`. Tests use it; the app reads at store creation. */
  hydrate: () => void;
  reset: () => void;
}

function initialState(): Pick<
  PaneState,
  "preference" | "chosen" | "evidenceTransient" | "viewportWidth" | "hostMounted"
> {
  return {
    preference: storedPreferences(),
    chosen: { sessions: false, evidence: false },
    evidenceTransient: false,
    viewportWidth: null,
    hostMounted: false,
  };
}

export const usePaneStore = create<PaneState>((set, get) => ({
  ...initialState(),

  setPreference: (pane, collapsed) => {
    writeStoredPane(pane, collapsed);
    set((state) => ({
      preference: { ...state.preference, [pane]: collapsed },
      chosen: { ...state.chosen, [pane]: true },
      // Expanding for real ends the borrowed open; collapsing ends it too
      // (that is the whole "closing it again returns to collapsed" rule).
      evidenceTransient: pane === "evidence" ? false : state.evidenceTransient,
    }));
  },

  toggle: (pane) => {
    const state = get();
    /*
     * CLOSING A BORROWED RAIL IS NOT A PREFERENCE CHANGE.
     *
     * Evidence is on screen because a chip asked for it, over a stored
     * preference that says collapsed. Toggling here means "I am done with
     * it" — so the borrow ends and the preference it was borrowed against
     * is exactly what it was. Without this branch the toggle would read
     * the rail as expanded, write "collapsed", and the reader would have
     * silently acquired a preference they never set.
     */
    if (pane === "evidence" && state.evidenceTransient && paneCollapsed(state, "evidence")) {
      set({ evidenceTransient: false });
      return;
    }
    get().setPreference(pane, !paneCollapsed(state, pane));
  },

  openEvidence: () => {
    const state = get();
    // Already on screen — nothing to borrow, and marking it borrowed
    // would arm the branch above against a preference that is expanded.
    if (!paneCollapsed(state, "evidence")) return;
    set({ evidenceTransient: true });
  },

  clearEvidenceTransient: () => set({ evidenceTransient: false }),

  setViewportWidth: (width) => {
    if (get().viewportWidth === width) return;
    set({ viewportWidth: width });
  },

  setHostMounted: (mounted) => set({ hostMounted: mounted }),

  hydrate: () => set({ preference: storedPreferences() }),

  reset: () => set(initialState()),
}));

/* ------------------------------------------------------------------ */
/* Derivations — plain functions, so a test can ask without rendering  */
/* ------------------------------------------------------------------ */

/**
 * Is this pane collapsed right now?
 *
 * A choice made by hand this session is final. Otherwise the persisted
 * preference and the viewport are OR'd: a stored "collapsed" holds at any
 * width, and a viewport too narrow to hold three columns folds a pane
 * whose stored preference is expanded — until the reader says otherwise.
 */
export function paneCollapsed(state: PaneState, pane: PaneId): boolean {
  if (state.chosen[pane]) return state.preference[pane];
  return state.preference[pane] || autoCollapses(pane, state.viewportWidth);
}

/** The evidence rail is on screen when it is not collapsed, or borrowed. */
export function evidenceOnScreen(state: PaneState): boolean {
  return !paneCollapsed(state, "evidence") || state.evidenceTransient;
}

export function usePaneCollapsed(pane: PaneId): boolean {
  return usePaneStore((state) => paneCollapsed(state, pane));
}

/** Right rail: collapsed for layout purposes means "not on screen". */
export function useEvidenceOnScreen(): boolean {
  return usePaneStore(evidenceOnScreen);
}

/* ------------------------------------------------------------------ */
/* Copy — one rendering per verb, shared by every control that says it */
/* ------------------------------------------------------------------ */

const PANE_NOUNS: Record<PaneId, string> = {
  sessions: "the sessions pane",
  evidence: "the evidence pane",
};

/** The key that toggles this pane, for a hint or a tooltip. */
export const PANE_SHORTCUTS: Record<PaneId, string> = {
  sessions: "[",
  evidence: "]",
};

/**
 * "Collapse the sessions pane" / "Expand the evidence pane".
 *
 * One function so the toggle's accessible name, its tooltip and the
 * palette row cannot drift into three ways of saying one thing — the same
 * rule the client-language doc applies to every other concept in the
 * product (docs/client-language.md §2).
 */
export function paneToggleLabel(pane: PaneId, collapsed: boolean): string {
  return `${collapsed ? "Expand" : "Collapse"} ${PANE_NOUNS[pane]}`;
}

/* ------------------------------------------------------------------ */
/* The viewport watch                                                  */
/* ------------------------------------------------------------------ */

/**
 * Report the window's width to the store, now and on every resize.
 *
 * Returns its own teardown, so the caller is an effect with one line in
 * it. Deliberately not a media query per threshold: the store holds the
 * width and `autoCollapses` owns the comparison, which is what lets a
 * test drive the thresholds without a `matchMedia` stub.
 */
export function watchViewportWidth(): () => void {
  if (typeof window === "undefined") return () => {};
  const report = (): void => usePaneStore.getState().setViewportWidth(window.innerWidth);
  report();
  window.addEventListener("resize", report);
  return () => window.removeEventListener("resize", report);
}

/* ------------------------------------------------------------------ */
/* The keyboard                                                        */
/* ------------------------------------------------------------------ */

/**
 * IS THIS KEYSTROKE SOMEBODY TYPING?
 *
 * `[` and `]` are ordinary characters, and the composer is a text box an
 * analyst types questions into — "CARC 45 [see note]" must reach the
 * field rather than fold a rail. So the shortcut is suppressed for any
 * text-entry target: inputs, textareas, and anything `contenteditable`.
 *
 * A `<input type="checkbox">` is an input and is NOT text entry, but it
 * also does not accept `[`, so the broad test costs nothing and the
 * narrow one would need a list of the twenty-odd input types.
 */
export function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** The pane a bare `[` or `]` toggles, or null for every other key. */
export function paneForKey(event: KeyboardEvent): PaneId | null {
  // ⌘[ / ⌥[ are the browser's own (back, and macOS text motion). A
  // shortcut that fires on a chord it does not own is a bug in somebody
  // else's app.
  if (event.metaKey || event.ctrlKey || event.altKey) return null;
  if (isTypingTarget(event.target)) return null;
  if (event.key === "[") return "sessions";
  if (event.key === "]") return "evidence";
  return null;
}
