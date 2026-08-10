/**
 * Which answer layout this browser is reading.
 *
 * The A/B is decided. Three reviewers — a fresh-eyes RCM director and the
 * two design personas — returned B_with_conditions at high confidence,
 * unanimously; every condition they attached is built, and B is the
 * default:
 *
 *   `b` — "THE CALM ANSWER", and the default. One context line, the
 *     verdict and the narrative as the primary content, at most one
 *     chart, the facts in the Evidence rail, and the integrity line —
 *     what was verified, how many things there are to know and how many
 *     of them change a reading, how many checks ran — as the signature.
 *   `a` — "the refined current": the same anatomy as the old layout, with
 *     the narrative above the findings, every non-verdict caution
 *     collapsed into one expandable group, and the findings as compact
 *     rows. It stays available on the toggle. It is the fallback if the
 *     calm layout turns out to be wrong in the field, so it has to keep
 *     working and it has to keep being honest — the two defects the
 *     review found in it (a comparison chip that dropped its year, a
 *     restored note that pointed the wrong way) are fixed.
 *   `current` — the layout that shipped before either of them. RETIRED
 *     from the toggle: the ⌘K cycle no longer offers it and nothing
 *     reaches it by accident. It is still in the code and still
 *     reachable at `?variant=current` for one round, because a layout
 *     deleted the same week its replacement became the default leaves no
 *     way to check a regression against what it replaced.
 *
 * Resolution order, highest first:
 *
 *   1. `?variant=a|b|current` in the URL. A link is how somebody is sent
 *      to a specific layout, so it wins over whatever this browser chose
 *      last, and it is STICKY — reading the parameter also writes the
 *      choice, so navigating within the app keeps the layout the link
 *      asked for. An unrecognized value is ignored rather than falling
 *      through to a default silently chosen from a typo.
 *   2. `localStorage["revi-answer-variant"]`, written by the ⌘K action.
 *   3. `b`.
 *
 * The resolution itself is a pure function of two strings so it can be
 * tested without a DOM; everything below it is the plumbing.
 */

export type AnswerVariant = "current" | "a" | "b";

export const ANSWER_VARIANT_STORAGE_KEY = "revi-answer-variant";
export const ANSWER_VARIANT_QUERY_KEY = "variant";

/** The default: the calm answer, as of the A/B's unanimous verdict. */
export const DEFAULT_ANSWER_VARIANT: AnswerVariant = "b";

/**
 * The layouts the toggle offers, in cycle order.
 *
 * `current` is deliberately absent — retired from the toggle, kept in the
 * code and at `?variant=current` for one round.
 */
export const TOGGLED_ANSWER_VARIANTS: readonly AnswerVariant[] = ["b", "a"];

const VARIANTS: ReadonlySet<string> = new Set<AnswerVariant>(["current", "a", "b"]);

/** How each layout names itself — in the palette, and in the URL. */
export const ANSWER_VARIANT_LABELS: Readonly<Record<AnswerVariant, string>> = {
  current: "Legacy",
  a: "Detailed",
  b: "Calm",
};

/** One sentence per layout, for the palette row's hint. */
export const ANSWER_VARIANT_HINTS: Readonly<Record<AnswerVariant, string>> = {
  current: "The pre-A/B layout — retired from the toggle, still at ?variant=current",
  a: "Narrative first, cautions in one group, findings as rows",
  b: "The default — the answer as writing; facts and cautions one tap away",
};

/**
 * A stored or typed value, or `null` when it is not one of the three.
 *
 * Never coerces. A `?variant=beta` is not variant B, and treating it as
 * one would send a reviewer to a layout they did not ask for and record
 * their judgement against the wrong one.
 */
export function readAnswerVariant(raw: unknown): AnswerVariant | null {
  if (typeof raw !== "string") return null;
  const value = raw.trim().toLowerCase();
  return VARIANTS.has(value) ? (value as AnswerVariant) : null;
}

/** The layout to render, from the URL parameter and the stored choice. */
export function resolveAnswerVariant(param: unknown, stored: unknown): AnswerVariant {
  return readAnswerVariant(param) ?? readAnswerVariant(stored) ?? DEFAULT_ANSWER_VARIANT;
}

/* ------------------------------------------------------------------ */
/* The browser half                                                    */
/* ------------------------------------------------------------------ */

type Listener = () => void;
const listeners = new Set<Listener>();

function emit(): void {
  for (const listener of listeners) listener();
}

function storedVariant(): string | null {
  try {
    return window.localStorage.getItem(ANSWER_VARIANT_STORAGE_KEY);
  } catch {
    // Storage unavailable (privacy mode). The URL parameter still works,
    // and without one the default layout is what renders.
    return null;
  }
}

function queryVariant(): string | null {
  try {
    return new URLSearchParams(window.location.search).get(ANSWER_VARIANT_QUERY_KEY);
  } catch {
    return null;
  }
}

/**
 * The variant this browser is on.
 *
 * Cached because `useSyncExternalStore` compares snapshots by identity
 * and would loop on a value re-derived every render; invalidated by
 * `setAnswerVariant` and by a `storage` event from another tab.
 */
let snapshot: AnswerVariant | null = null;

export function currentAnswerVariant(): AnswerVariant {
  if (typeof window === "undefined") return DEFAULT_ANSWER_VARIANT;
  if (snapshot !== null) return snapshot;
  const fromQuery = readAnswerVariant(queryVariant());
  // A link into a layout is sticky: the reviewer follows it, then clicks
  // around, and the layout under judgement should not revert on the
  // second page. Writing it here rather than in an effect keeps the
  // first client render and every later one on the same answer.
  if (fromQuery !== null) writeVariant(fromQuery);
  snapshot = resolveAnswerVariant(fromQuery, storedVariant());
  return snapshot;
}

/** The SERVER snapshot: no URL, no storage, so the default layout. */
export function serverAnswerVariant(): AnswerVariant {
  return DEFAULT_ANSWER_VARIANT;
}

function writeVariant(variant: AnswerVariant): void {
  try {
    if (variant === DEFAULT_ANSWER_VARIANT) {
      window.localStorage.removeItem(ANSWER_VARIANT_STORAGE_KEY);
      return;
    }
    window.localStorage.setItem(ANSWER_VARIANT_STORAGE_KEY, variant);
  } catch {
    // Non-fatal: the choice applies to this tab for as long as it lives.
  }
}

/**
 * Switch layouts, live.
 *
 * No reload: the answer surface is a pure function of the turns already
 * in the store, so re-rendering it under a different layout is the whole
 * change — and a reload would cost a reviewer the thread they are
 * judging. The URL is kept in step so the address bar is a shareable
 * pointer at what is on screen.
 */
export function setAnswerVariant(variant: AnswerVariant): void {
  if (typeof window === "undefined") return;
  writeVariant(variant);
  snapshot = variant;
  try {
    const url = new URL(window.location.href);
    if (variant === DEFAULT_ANSWER_VARIANT) {
      url.searchParams.delete(ANSWER_VARIANT_QUERY_KEY);
    } else {
      url.searchParams.set(ANSWER_VARIANT_QUERY_KEY, variant);
    }
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  } catch {
    // A URL this build cannot rewrite is not a reason to refuse the switch.
  }
  emit();
}

/**
 * The next layout in the cycle — what the ⌘K action offers.
 *
 * Two layouts, not three: the calm answer and the detailed one. A browser
 * sitting on the retired `current` (a stored choice from before the flip,
 * or a `?variant=current` link) is offered the DEFAULT next, so the one
 * keystroke that used to cycle away from it now returns to the layout
 * that won.
 */
export function nextAnswerVariant(variant: AnswerVariant): AnswerVariant {
  const index = TOGGLED_ANSWER_VARIANTS.indexOf(variant);
  if (index === -1) return DEFAULT_ANSWER_VARIANT;
  return TOGGLED_ANSWER_VARIANTS[(index + 1) % TOGGLED_ANSWER_VARIANTS.length];
}

export function subscribeAnswerVariant(listener: Listener): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent): void => {
    if (event.key !== null && event.key !== ANSWER_VARIANT_STORAGE_KEY) return;
    snapshot = null;
    listener();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

/** Test seam: forget the cached snapshot. */
export function resetAnswerVariantCache(): void {
  snapshot = null;
}
