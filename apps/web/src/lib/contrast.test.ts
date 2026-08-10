/**
 * The contrast floor, enforced on the CLASS rather than on a screenshot.
 *
 * Tailwind's `/nn` opacity modifier is the one way this codebase can make
 * text unreadable without anybody writing a colour: `text-muted-foreground`
 * is chosen for the token it is, and `/80` then silently blends 20% of the
 * background into it. Measured on the app's own tokens, alpha-composited
 * over each surface and scored against WCAG 2.2 SC 1.4.3 (4.5:1, since
 * every one of these sites is 12px `text-micro`):
 *
 *                       page    card    sunken  rail panel
 *   muted-foreground    4.80    5.24    4.57    5.04      light  PASS
 *   …at 80%             3.27    3.48    3.16    3.38      light  FAIL
 *   …at 70%             2.74    2.88    2.66    2.82      light  FAIL
 *   muted-foreground    7.31    6.92    7.18    7.04      dark   PASS
 *   …at 80%             5.00    4.83    4.94    4.89      dark   pass
 *   …at 70%             4.06    3.97    4.04    4.01      dark   FAIL
 *
 * Light theme is the binding case and both modifiers fail it, which is why
 * this bans the class rather than tuning it. `text-foreground/80` is NOT
 * banned: it measures 8.50–9.29 light and 10.34–10.74 dark, and the 20%
 * there is doing real work separating a lead from a body.
 *
 * Round 8 banned `text-muted-foreground/80` on the Rounds surface by
 * rendering it (`Rounds.test.tsx`). That caught the tiles and missed the
 * two stragglers — the answer card's Restored mark and the session rail's
 * row age — because a render test only sees what it renders. This one
 * reads the source, so a class added to a component nobody has written a
 * test for still fails here.
 */

import { readdirSync, readFileSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * Assembled rather than written out, so this file does not match its own
 * ban and every other file's mention of it is a real one.
 */
const BANNED = ["70", "80"].map((alpha) => `text-muted-foreground/${alpha}`);

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "node_modules" || entry.name === "__fixtures__") continue;
      out.push(...sourceFiles(full));
      continue;
    }
    if (!/\.(ts|tsx|css)$/.test(entry.name)) continue;
    // Tests are allowed to name the class they are banning.
    if (/\.test\.(ts|tsx)$/.test(entry.name)) continue;
    out.push(full);
  }
  return out;
}

describe("body text never runs under the AA floor by way of an opacity modifier", () => {
  it("bans the muted-foreground opacity modifiers everywhere in src", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC)) {
      const source = readFileSync(file, "utf8");
      for (const banned of BANNED) {
        if (source.includes(banned)) offenders.push(`${relative(SRC, file)}: ${banned}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("scans a real tree, so an empty result means something", () => {
    // The failure mode this replaces: a glob that matched nothing and
    // reported a clean sweep of it.
    expect(sourceFiles(SRC).length).toBeGreaterThan(100);
  });
});
