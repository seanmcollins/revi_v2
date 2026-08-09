/**
 * The reduced-motion gap CSS could not close.
 *
 * `scroll-behavior: auto !important` inside the reduced-motion media
 * query does NOT govern a `behavior: "smooth"` passed as an OPTION to
 * `scrollIntoView` — the option wins. So every ⌘K jump, referent chip
 * jump, lineage jump and stream follow kept animating for a reader who
 * had asked the platform not to move things.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { scrollIntoViewRespectingMotion } from "@/lib/useReducedMotion";

function withPreference(reduce: boolean): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: reduce && query.includes("prefers-reduced-motion"),
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe("scrollIntoViewRespectingMotion", () => {
  it("animates for a reader who has not asked it not to", () => {
    withPreference(false);
    const scrollIntoView = vi.fn();
    scrollIntoViewRespectingMotion({ scrollIntoView } as unknown as Element, { block: "center" });
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "center", behavior: "smooth" });
  });

  it("jumps for a reader who has", () => {
    withPreference(true);
    const scrollIntoView = vi.fn();
    scrollIntoViewRespectingMotion({ scrollIntoView } as unknown as Element, { block: "end" });
    expect(scrollIntoView).toHaveBeenCalledWith({ block: "end", behavior: "auto" });
  });

  it("does nothing at all when the target is not on the page", () => {
    withPreference(false);
    expect(() => scrollIntoViewRespectingMotion(null)).not.toThrow();
    expect(() => scrollIntoViewRespectingMotion(undefined)).not.toThrow();
  });
});
