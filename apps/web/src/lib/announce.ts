/**
 * One polite live region for the whole app.
 *
 * Some of this product's affordances are a JUMP: a referent chip in the
 * writing opens the Evidence rail on the fact it cites and moves the
 * reader there. A jump that only scrolls is a sighted-pointer gesture —
 * a keyboard user's focus stays behind in the paragraph and a screen
 * reader is told nothing at all happened, which makes "the facts moved
 * to the rail" untrue for them.
 *
 * So the jump moves focus, and where there is nothing to focus (the
 * target has not mounted, or the rail is not on this viewport) this says
 * in one sentence what changed. Politely: it never interrupts, and it is
 * a sentence rather than a chime because "F1 shown in Evidence" is the
 * fact, and a chime is a guess at it.
 *
 * A single region, reused. Two regions racing each other is how a screen
 * reader ends up reading neither.
 */

const REGION_ID = "revi-live-announcer";

function region(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  const existing = document.getElementById(REGION_ID);
  if (existing) return existing;
  const node = document.createElement("div");
  node.id = REGION_ID;
  node.setAttribute("role", "status");
  node.setAttribute("aria-live", "polite");
  node.setAttribute("aria-atomic", "true");
  node.className = "sr-only";
  document.body.appendChild(node);
  return node;
}

/**
 * Say one sentence to assistive technology and nobody else.
 *
 * The text is cleared first so that announcing the SAME sentence twice —
 * two chips onto the same fact — is two announcements rather than one
 * unchanged node a screen reader ignores.
 */
export function announce(message: string): void {
  const node = region();
  if (!node) return;
  node.textContent = "";
  // A separate task, so the clear and the set are two mutations rather
  // than one no-op collapsed by the DOM.
  window.setTimeout(() => {
    node.textContent = message;
  }, 0);
}
