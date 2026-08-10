"use client";

import type { ReactNode } from "react";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * A FIGURE AT DISPLAY SIZE — the product's one way of making a number the
 * thing the eye lands on.
 *
 * It exists because "calm" was reading as "empty". Home opened on four
 * paragraphs of 12–13px muted ink and a wordmark, and the owner's first
 * reaction to the live surface was that it looked broken. Nothing about the
 * calm register is wrong — amber stays a verdict colour, nothing is
 * invented, every figure is measured — but a page whose most important
 * number is set at the same size as its footnotes has no hierarchy at all,
 * and a reader with no hierarchy assumes the render failed.
 *
 * So presence comes from SIZE, exactly as `globals.css` says it must:
 * `--text-figure` (30px) for the one number that matters most, `--text-lead`
 * (17px) for the ones beside it, `.numeral` for tabular lining figures, and
 * never a heavier weight — a bold number reads as a sales claim, a large
 * light one reads as a measurement.
 *
 * THREE RULES IT ENFORCES SO NO CALL SITE HAS TO REMEMBER THEM.
 *
 *   THE MARKS SURVIVE THE SIZE. `value` is rendered verbatim, so a `≤`
 *     the engine published is still on the front of a 30px numeral, and
 *     `mark` carries the words that say what the ≤ means ("a ceiling, not
 *     a measurement", "estimated", "still settling") in the same
 *     vocabulary the monitor tiles use. A figure that loses its honesty
 *     marks on the way to display size is the single most dangerous
 *     object this product could draw — it is the one that gets
 *     screenshotted.
 *   THE ACCENT IS A RULE, NEVER THE INK. `emphasis` draws the product's
 *     one gradient as a 2px rule above the cell. The gradient is reserved
 *     for primary actions, active states and the hero glow and is never
 *     content, so it marks the figure without colouring it; the numeral
 *     itself stays foreground ink at full contrast in every cell.
 *   NO AMBER. This is not a verdict surface. A deadline that has passed is
 *     said in words ("passed 47 days ago"), and the one colour this
 *     product reserves for premise corrections and refusals does not leak
 *     onto a dashboard number.
 */
export function KeyFigure({
  label,
  labelDetail,
  value,
  mark,
  context,
  emphasis = false,
  className,
}: {
  /** The quiet label above the figure. Sentence case, never a metric id. */
  label: string;
  /**
   * The server's own definition of what this figure counts, when it
   * published one — the lane descriptions, verbatim.
   *
   * A real control rather than a `title` attribute: `title` is mouse-only
   * and is not read reliably by anything else, and this is the sentence
   * that says whether "still catchable" means what the reader assumes.
   */
  labelDetail?: string;
  /** The figure, already formatted and already carrying its `≤` / `~`. */
  value: string;
  /**
   * What kind of number this is, when it is not a plain measurement —
   * "estimated", "a ceiling, not a measurement". Rendered beside the
   * numeral in the same muted register `ValueMarks` uses on a tile.
   */
  mark?: string;
  /** One quiet sentence under the figure — what it counts, or when. */
  context?: ReactNode;
  /** This is the figure the page is about. Draws the accent rule. */
  emphasis?: boolean;
  className?: string;
}) {
  return (
    <div
      data-key-figure={label}
      className={cn(
        "flex min-w-0 flex-col gap-1 rounded-lg border bg-surface-raised px-3 py-2.5",
        "raised transition-[border-color,box-shadow] duration-200",
        className,
      )}
    >
      {/* The accent, as a rule rather than as ink. Decorative by
          construction — it carries no text and no meaning a reader could
          only get from its colour; the SIZE of the numeral below is what
          says this figure outranks the others. */}
      {emphasis && (
        <span
          aria-hidden
          className="accent-gradient -mt-0.5 mb-0.5 h-0.5 w-8 shrink-0 rounded-full"
        />
      )}
      <p className="text-micro font-semibold uppercase tracking-widest text-muted-foreground">
        {labelDetail === undefined ? (
          label
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="focus-ring rounded text-left uppercase tracking-widest underline decoration-dotted underline-offset-2 transition-colors duration-150 hover:text-foreground"
              >
                {label}
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-80 text-meta leading-snug">
              {labelDetail}
            </TooltipContent>
          </Tooltip>
        )}
      </p>
      <p
        className={cn(
          "numeral min-w-0 break-words leading-none text-foreground",
          emphasis ? "text-figure" : "text-lead",
        )}
      >
        {value}
        {/* The honesty mark travels at display size, in words, on the same
            line as the number — the rule `ValueMarks` follows on every
            monitor tile. */}
        {mark !== undefined && mark !== "" && (
          <span className="ml-1.5 align-baseline text-micro font-normal text-muted-foreground">
            {mark}
          </span>
        )}
      </p>
      {context !== undefined && (
        <p className="num text-micro leading-snug text-muted-foreground">{context}</p>
      )}
    </div>
  );
}

/**
 * The row the figures sit in.
 *
 * A grid rather than a flex row so the four cells share a baseline and one
 * long dollar figure cannot squeeze the count beside it into two lines. It
 * collapses to two columns and then to one, because this band is the first
 * thing on the surface at every width.
 */
export function FigureBand({
  children,
  className,
  ...rest
}: {
  children: ReactNode;
  className?: string;
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("grid gap-2 sm:grid-cols-2 xl:grid-cols-4", className)}
      {...rest}
    >
      {children}
    </div>
  );
}
