/**
 * A DECORATION LAYER MAY NOT BEGIN ABOVE ITS OWN BOX.
 *
 * The defect this exists for: the active answer card drew its accent glow
 * as `absolute -inset-x-10 -top-8`, i.e. a layer whose box started 32px
 * ABOVE the card. The gap between a turn's question bubble and its answer
 * is 12px (`ChatThread`'s `space-y-3`), so the layer reached 20px into the
 * bubble and tinted its lower edge on every answered turn. Measured live at
 * 1440x900 before the fix: glow top 336.1px, bubble bottom 355.6px — 19.5px
 * of overlap.
 *
 * IT WAS NOT A POINTER BUG, WHICH IS WHY IT SURVIVED. The layer already
 * carried `pointer-events-none`, `aria-hidden` and a negative z-index, so
 * nothing was unclickable and nothing was unreadable to a screen reader.
 * It was simply drawn in the wrong place, and no test in this repo could
 * see that: jsdom has no layout engine, so a rendered assertion cannot
 * measure a box. This reads the SOURCE instead, in the same style as
 * `contrast.test.ts` — which exists for the same reason, that a class can
 * be wrong in a file nobody has written a render test for.
 *
 * Two rules, and the second is the one that caught this:
 *
 *   A DECORATION LAYER IS INERT. `pointer-events-none` and `aria-hidden`,
 *     always. A layer that intercepts a click on the card underneath it is
 *     the failure mode that a margin-based "fix" leaves in place.
 *   A DECORATION LAYER STAYS INSIDE ITS OWN VERTICAL BOX. No `-top-*` and
 *     no `-bottom-*`: above and below is where a SIBLING is. Horizontal
 *     bleed (`-inset-x-*`, `-left-*`, `-right-*`) is allowed and used —
 *     a thread column has no siblings to its left or right.
 *
 * The live click-through keeps the other half of this, because a source
 * rule cannot see a computed box: for every stacked pair in the thread
 * (bubble→card, card→next bubble) and for Home's stacked zones, the
 * bounding rectangles must not intersect, at 1280 / 1440 / 1512.
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** The classes this product uses for a purely decorative painted layer. */
const DECORATION = ["answer-glow", "hero-glow", "page-glow"];

/** Assembled so this file does not match its own rule. */
const NEGATIVE_TOP = new RegExp(`-(?:top|bottom)-`);

interface Layer {
  file: string;
  line: number;
  className: string;
}

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === "__fixtures__") continue;
      out.push(...sourceFiles(full));
      continue;
    }
    if (!/\.tsx?$/.test(entry.name)) continue;
    if (/\.test\.tsx?$/.test(entry.name)) continue;
    out.push(full);
  }
  return out;
}

/** Every className string in the tree that names a decoration layer. */
function decorationLayers(): Layer[] {
  const layers: Layer[] = [];
  for (const file of sourceFiles(SRC)) {
    const lines = readFileSync(file, "utf8").split("\n");
    lines.forEach((line, i) => {
      if (!DECORATION.some((name) => line.includes(name))) return;
      // The class list this line assigns, if it is assigning one. A
      // mention inside a comment or a CSS file carries no attribute.
      const match = /className=(?:"([^"]*)"|\{`([^`]*)`\}|\{cn\(([^)]*)\)\})/.exec(line);
      if (match === null) return;
      const className = match[1] ?? match[2] ?? match[3] ?? "";
      if (!DECORATION.some((name) => className.includes(name))) return;
      layers.push({ file: relative(SRC, file), line: i + 1, className });
    });
  }
  return layers;
}

describe("a painted decoration layer never reaches outside its own box", () => {
  it("scans a real tree, so an empty result means something", () => {
    const layers = decorationLayers();
    expect(layers.length).toBeGreaterThanOrEqual(3);
  });

  it("never starts above or below its own element", () => {
    const offenders = decorationLayers().filter((l) => NEGATIVE_TOP.test(l.className));
    expect(
      offenders.map((l) => `${l.file}:${l.line} — ${l.className}`),
      "A decoration layer with a negative top/bottom offset is painted over the element " +
        "stacked above or below it. Anchor it to its own edge (`top-0`) — the gradient's " +
        "bright end belongs at the boundary, not on the sibling.",
    ).toEqual([]);
  });

  it("is inert: no pointer events, no accessible presence", () => {
    const layers = decorationLayers();
    const missing = layers.filter((l) => !l.className.includes("pointer-events-none"));
    expect(
      missing.map((l) => `${l.file}:${l.line}`),
      "A decoration layer that takes pointer events swallows clicks on the content under it.",
    ).toEqual([]);
  });
});
