import { beforeEach, describe, expect, it } from "vitest";

import {
  EVIDENCE_AUTO_COLLAPSE_BELOW,
  PANE_STORAGE_KEYS,
  SESSIONS_AUTO_COLLAPSE_BELOW,
  autoCollapses,
  evidenceOnScreen,
  isTypingTarget,
  paneCollapsed,
  paneForKey,
  paneToggleLabel,
  readStoredPane,
  usePaneStore,
} from "@/lib/panes";

/**
 * The pane module has three states that are easy to collapse into one and
 * wrong to: what this device WANTS (persisted), what the reader CHOSE this
 * session (which beats the viewport), and what a citation BORROWED (which
 * beats both, temporarily, and changes neither). Nearly every test here is
 * about keeping those three apart.
 */

const state = () => usePaneStore.getState();

beforeEach(() => {
  window.localStorage.clear();
  usePaneStore.getState().reset();
  usePaneStore.getState().hydrate();
});

describe("persistence — per device, in the revi-* convention", () => {
  it("writes a collapse and reads it back on a fresh store", () => {
    state().setPreference("sessions", true);
    expect(window.localStorage.getItem(PANE_STORAGE_KEYS.sessions)).toBe("collapsed");

    // A new browser session: the store is rebuilt and re-reads storage.
    usePaneStore.getState().reset();
    expect(paneCollapsed(state(), "sessions")).toBe(true);
    expect(paneCollapsed(state(), "evidence")).toBe(false);
  });

  it("round-trips both panes independently", () => {
    state().setPreference("evidence", true);
    state().setPreference("sessions", false);
    usePaneStore.getState().reset();

    expect(paneCollapsed(state(), "sessions")).toBe(false);
    expect(paneCollapsed(state(), "evidence")).toBe(true);
  });

  it("treats an unrecognised stored value as no preference at all", () => {
    window.localStorage.setItem(PANE_STORAGE_KEYS.sessions, "yes");
    expect(readStoredPane("sessions")).toBeNull();
    usePaneStore.getState().hydrate();
    expect(paneCollapsed(state(), "sessions")).toBe(false);
  });

  it("keys are the app's own convention", () => {
    expect(PANE_STORAGE_KEYS.sessions).toBe("revi-pane-sessions");
    expect(PANE_STORAGE_KEYS.evidence).toBe("revi-pane-evidence");
  });
});

describe("auto-collapse — left first, then right, and a choice beats both", () => {
  it("folds the sessions rail below the width the workspace is designed for", () => {
    expect(autoCollapses("sessions", SESSIONS_AUTO_COLLAPSE_BELOW)).toBe(false);
    expect(autoCollapses("sessions", SESSIONS_AUTO_COLLAPSE_BELOW - 1)).toBe(true);
  });

  it("folds the evidence rail only below the narrower threshold", () => {
    expect(autoCollapses("evidence", SESSIONS_AUTO_COLLAPSE_BELOW - 1)).toBe(false);
    expect(autoCollapses("evidence", EVIDENCE_AUTO_COLLAPSE_BELOW - 1)).toBe(true);
  });

  it("left goes before right, which is the whole point of two thresholds", () => {
    expect(EVIDENCE_AUTO_COLLAPSE_BELOW).toBeLessThan(SESSIONS_AUTO_COLLAPSE_BELOW);
  });

  it("at 1200px the sessions rail is folded and the evidence rail is not", () => {
    state().setViewportWidth(1200);
    expect(paneCollapsed(state(), "sessions")).toBe(true);
    expect(paneCollapsed(state(), "evidence")).toBe(false);
  });

  it("at 900px both are folded", () => {
    state().setViewportWidth(900);
    expect(paneCollapsed(state(), "sessions")).toBe(true);
    expect(paneCollapsed(state(), "evidence")).toBe(true);
  });

  it("an explicit expand at a narrow width wins, and keeps winning on resize", () => {
    state().setViewportWidth(900);
    expect(paneCollapsed(state(), "sessions")).toBe(true);

    // The reader says no.
    state().toggle("sessions");
    expect(paneCollapsed(state(), "sessions")).toBe(false);

    // …and the layout does not re-decide behind them.
    state().setViewportWidth(820);
    expect(paneCollapsed(state(), "sessions")).toBe(false);
  });

  it("a stored collapse holds at any width", () => {
    state().setPreference("evidence", true);
    usePaneStore.getState().reset();
    state().setViewportWidth(1920);
    expect(paneCollapsed(state(), "evidence")).toBe(true);
  });

  it("a stored expand still yields to a viewport that cannot hold it — until chosen", () => {
    state().setPreference("sessions", false);
    usePaneStore.getState().reset();
    state().setViewportWidth(1100);
    // Not chosen in THIS session, so the width decides.
    expect(paneCollapsed(state(), "sessions")).toBe(true);
  });
});

describe("evidence — transient open versus the persisted preference", () => {
  it("an open-evidence gesture shows the rail without flipping the preference", () => {
    state().setPreference("evidence", true);
    expect(evidenceOnScreen(state())).toBe(false);

    state().openEvidence();

    expect(evidenceOnScreen(state())).toBe(true);
    // The preference — the thing that survives a reload — is untouched.
    expect(state().preference.evidence).toBe(true);
    expect(window.localStorage.getItem(PANE_STORAGE_KEYS.evidence)).toBe("collapsed");
  });

  it("closing a borrowed rail returns to collapsed, not to expanded", () => {
    state().setPreference("evidence", true);
    state().openEvidence();
    expect(evidenceOnScreen(state())).toBe(true);

    state().toggle("evidence");

    expect(evidenceOnScreen(state())).toBe(false);
    expect(state().preference.evidence).toBe(true);
    expect(window.localStorage.getItem(PANE_STORAGE_KEYS.evidence)).toBe("collapsed");
  });

  it("and it is still collapsed on the next visit", () => {
    state().setPreference("evidence", true);
    state().openEvidence();
    state().toggle("evidence");

    usePaneStore.getState().reset();
    expect(evidenceOnScreen(state())).toBe(false);
  });

  it("a second gesture re-opens it — a borrow ended is not a door locked", () => {
    state().setPreference("evidence", true);
    state().openEvidence();
    state().toggle("evidence");
    expect(evidenceOnScreen(state())).toBe(false);

    state().openEvidence();
    expect(evidenceOnScreen(state())).toBe(true);
  });

  it("the toggle itself DOES flip the preference — that is what it is for", () => {
    state().setPreference("evidence", true);
    usePaneStore.getState().reset();

    state().toggle("evidence");

    expect(evidenceOnScreen(state())).toBe(true);
    expect(state().preference.evidence).toBe(false);
    expect(window.localStorage.getItem(PANE_STORAGE_KEYS.evidence)).toBe("expanded");
  });

  it("collapsing an expanded rail persists the collapse", () => {
    state().toggle("evidence");
    expect(state().preference.evidence).toBe(true);
    expect(window.localStorage.getItem(PANE_STORAGE_KEYS.evidence)).toBe("collapsed");
  });

  it("a gesture over an already-open rail borrows nothing", () => {
    state().openEvidence();
    expect(state().evidenceTransient).toBe(false);

    // …so the next toggle is an ordinary collapse, not a borrow ending.
    state().toggle("evidence");
    expect(evidenceOnScreen(state())).toBe(false);
    expect(state().preference.evidence).toBe(true);
  });

  it("a borrow over an AUTO-collapsed rail also leaves the preference alone", () => {
    state().setViewportWidth(900);
    expect(evidenceOnScreen(state())).toBe(false);

    state().openEvidence();
    expect(evidenceOnScreen(state())).toBe(true);
    expect(state().preference.evidence).toBe(false);

    state().toggle("evidence");
    expect(evidenceOnScreen(state())).toBe(false);
    expect(state().preference.evidence).toBe(false);
  });
});

describe("the keyboard — bare brackets, never while typing", () => {
  function key(init: KeyboardEventInit & { target?: HTMLElement }): KeyboardEvent {
    const { target, ...rest } = init;
    const event = new KeyboardEvent("keydown", rest);
    if (target) Object.defineProperty(event, "target", { value: target });
    return event;
  }

  it("maps [ to the sessions pane and ] to the evidence pane", () => {
    expect(paneForKey(key({ key: "[" }))).toBe("sessions");
    expect(paneForKey(key({ key: "]" }))).toBe("evidence");
  });

  it("ignores every other key", () => {
    expect(paneForKey(key({ key: "k" }))).toBeNull();
    expect(paneForKey(key({ key: "{" }))).toBeNull();
  });

  it("leaves modifier chords to the browser", () => {
    expect(paneForKey(key({ key: "[", metaKey: true }))).toBeNull();
    expect(paneForKey(key({ key: "[", ctrlKey: true }))).toBeNull();
    expect(paneForKey(key({ key: "]", altKey: true }))).toBeNull();
  });

  it("is suppressed in a text input — the composer must be able to type a bracket", () => {
    const input = document.createElement("input");
    const textarea = document.createElement("textarea");
    expect(paneForKey(key({ key: "[", target: input }))).toBeNull();
    expect(paneForKey(key({ key: "]", target: textarea }))).toBeNull();
  });

  it("is suppressed in a contenteditable region too", () => {
    const div = document.createElement("div");
    div.contentEditable = "true";
    // jsdom does not derive `isContentEditable` from the attribute.
    Object.defineProperty(div, "isContentEditable", { value: true });
    expect(isTypingTarget(div)).toBe(true);
    expect(paneForKey(key({ key: "[", target: div }))).toBeNull();
  });

  it("fires over an ordinary element", () => {
    expect(isTypingTarget(document.createElement("div"))).toBe(false);
    expect(paneForKey(key({ key: "[", target: document.createElement("div") }))).toBe(
      "sessions",
    );
  });
});

describe("copy — one rendering per verb", () => {
  it("names the pane and the direction", () => {
    expect(paneToggleLabel("sessions", false)).toBe("Collapse the sessions pane");
    expect(paneToggleLabel("sessions", true)).toBe("Expand the sessions pane");
    expect(paneToggleLabel("evidence", false)).toBe("Collapse the evidence pane");
    expect(paneToggleLabel("evidence", true)).toBe("Expand the evidence pane");
  });

  it("says nothing the client-language doc forbids", () => {
    // §3 NEVER-SAY, on word boundaries — the same test the server-composed
    // strings take. "pane" is ordinary English; "drawer", "frame" and
    // "turn" are ours.
    const banned = /\b(turn|frame|pack|cohort|watermark|spec|playbook|probe)\b/i;
    for (const pane of ["sessions", "evidence"] as const) {
      for (const collapsed of [true, false]) {
        expect(paneToggleLabel(pane, collapsed)).not.toMatch(banned);
      }
    }
  });
});
