import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * `cn`, taught this product's type scale.
 *
 * The scale is five named steps (`text-micro` … `text-figure`, declared in
 * `globals.css`), and tailwind-merge does not know them: its font-size
 * group recognizes t-shirt sizes and arbitrary values, and everything else
 * matching `text-*` falls through to the TEXT COLOUR group. So
 * `cn("text-meta text-muted-foreground")` silently returned
 * `"text-muted-foreground"` — the size dropped, the element inheriting
 * 16px, on precisely the surfaces the type sweep had just set.
 *
 * Registering the scale in the font-size group is the whole fix: a size
 * and a colour stop colliding, and two sizes in one call still resolve
 * last-wins, which is what the merge is for.
 */
const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["micro", "meta", "body", "lead", "figure"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
