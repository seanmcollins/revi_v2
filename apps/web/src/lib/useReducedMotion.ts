"use client";

import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";

function subscribe(onChange: () => void): () => void {
  const media = window.matchMedia(QUERY);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

/**
 * Live `prefers-reduced-motion` state. CSS handles most of our motion, but
 * animation that lives in JS/SVG props (Recharts draw-in) has to ask.
 * Server snapshot is `false` so SSR markup matches the common case; the
 * client corrects on hydration before any animation is committed.
 */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  );
}

/**
 * `scrollIntoView`, animated only for readers who want animation.
 *
 * `scroll-behavior: auto !important` in the reduced-motion media query
 * does NOT govern this: a `behavior: "smooth"` passed as an option to the
 * DOM method wins over the CSS property, so every ⌘K jump, referent jump
 * and lineage jump kept animating for a reader who asked it not to. The
 * preference is read at call time (these are all event handlers, not
 * render paths), so a mid-session change to the setting is honoured.
 */
export function scrollIntoViewRespectingMotion(
  element: Element | null | undefined,
  options: Omit<ScrollIntoViewOptions, "behavior"> = {},
): void {
  if (!element) return;
  const reduced =
    typeof window !== "undefined" && window.matchMedia?.(QUERY).matches === true;
  element.scrollIntoView({ ...options, behavior: reduced ? "auto" : "smooth" });
}
