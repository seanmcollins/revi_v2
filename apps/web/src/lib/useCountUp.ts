"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Animated number landing (Stripe-style): eases from the previous value to
 * `target` over ~400ms with cubic ease-out. Integer-safe (cents). Snaps
 * instantly under prefers-reduced-motion.
 */
export function useCountUp(target: number, durationMs = 420): number {
  const [value, setValue] = useState(0);
  const fromRef = useRef(0);

  useEffect(() => {
    // All state updates happen inside animation frames — the effect body
    // only schedules them (no synchronous setState cascades).
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const from = fromRef.current;
    let raf = 0;
    if (reduced || from === target) {
      raf = requestAnimationFrame(() => {
        fromRef.current = target;
        setValue(target);
      });
      return () => cancelAnimationFrame(raf);
    }
    const start = performance.now();
    const tick = (now: number) => {
      const t = Math.min((now - start) / durationMs, 1);
      const eased = 1 - Math.pow(1 - t, 3);
      setValue(Math.round(from + (target - from) * eased));
      if (t < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        fromRef.current = target;
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, durationMs]);

  return value;
}
