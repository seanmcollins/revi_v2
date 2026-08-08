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
