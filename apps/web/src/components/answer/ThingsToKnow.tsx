"use client";

import { ChevronRight, X } from "lucide-react";
import { Dialog as DialogPrimitive } from "radix-ui";
import { useId, useState } from "react";

import type { WarningEvent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { publicWarningBody, warningTitle } from "@/lib/warnings";

/**
 * The caveats an answer carries, in one place instead of eight.
 *
 * Live, the flagship answer stacked EIGHT amber banners above any answer
 * content — 2.7 screen-heights of caution before a single number, on a
 * turn whose first three cautions were "window assumed", "only the top
 * rows were written up" and "computed on a different date basis". Every
 * one of them is true and worth keeping; none of them is the answer.
 *
 * So they are grouped, not deleted, and the group states its own size.
 * Both refined layouts use the same rows — the same titles, the same
 * sentences, the same count — because "3 things to know" on the integrity
 * line has to be a count of exactly what opens when it is tapped.
 *
 * A VERDICT never reaches this component. `PREMISE_*`, `RANKING_REFUSED`
 * and `DIRECTION_UNMATCHED` are the answer's own finding about the
 * question asked, and they lead in prose — see `VerdictLead`.
 */

/** One caveat: the reader's title, the engine's sentence, its count. */
function CaveatRow({ warning }: { warning: WarningEvent }) {
  const [verbatim, setVerbatim] = useState(false);
  const bodyId = useId();
  const title = warningTitle(warning.code);
  const body = publicWarningBody(warning.code, warning.message);
  const count = warning.count ?? 1;

  return (
    <li
      data-warning-code={warning.code}
      data-severity={warning.severity}
      className="border-t border-border/70 py-2.5 first:border-t-0 first:pt-0"
    >
      {title && (
        <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-meta font-medium text-foreground">{title}</span>
          {count > 1 && (
            <span
              className="num text-micro text-muted-foreground"
              title={
                warning.probes && warning.probes.length > 1
                  ? `One fact, raised on ${count} checks. It is stated once.`
                  : `The engine raised this ${count} times with the same wording; it is shown once.`
              }
            >
              stated {count} times
            </span>
          )}
        </p>
      )}
      <p
        id={bodyId}
        className={cn("text-meta text-muted-foreground", title && "mt-0.5")}
      >
        {verbatim ? body.verbatim : body.text}
      </p>
      {/* NOTHING IS DELETED, ONLY RELOCATED. When the plain-language body
          differs from the engine's sentence — a plan-node census taken
          out, a warehouse id spelled as words — the exact wording is one
          tap away, on the same row. The copied answer and every CSV carry
          the verbatim message regardless; this is for the reader who
          wants to see what was changed. */}
      {body.redacted && (
        <button
          type="button"
          onClick={() => setVerbatim(!verbatim)}
          aria-expanded={verbatim}
          aria-controls={bodyId}
          className="focus-ring mt-1 rounded text-micro text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
        >
          {verbatim ? "Show plain wording" : "Show the engine's exact wording"}
        </button>
      )}
    </li>
  );
}

export function CaveatList({
  warnings,
  className,
}: {
  warnings: readonly WarningEvent[];
  className?: string;
}) {
  return (
    <ul className={cn("m-0 list-none p-0", className)}>
      {warnings.map((warning, index) => (
        <CaveatRow key={`${warning.code}:${index}`} warning={warning} />
      ))}
    </ul>
  );
}

/** "3 things to know" — the phrase both layouts and the line agree on. */
export function thingsToKnowLabel(count: number): string {
  return `${count} thing${count === 1 ? "" : "s"} to know`;
}

/**
 * What the group is MADE OF, in one clause — the severity signal.
 *
 * "12 things to know" and "12 things to know · 10 change how a number
 * here should be read" are different products. The first is a count of
 * paperwork and a reader learns, correctly, that they can ignore it; the
 * second is the reason to open it. Variant A gave this away for free
 * above the findings and the calm layout's integrity line did not, which
 * is the one place the calm layout was quieter than it was honest.
 *
 * One function, so the line and the group cannot drift: the phrase is the
 * same sentence in both, over the same partition.
 */
export function thingsToKnowSeverity(warnings: readonly WarningEvent[]): string {
  const cautions = warnings.filter((w) => w.severity === "caution").length;
  if (cautions === 0) return "notes about how this answer was produced";
  if (cautions === warnings.length) return "each one changes how a number here should be read";
  return `${cautions} change how a number here should be read`;
}

/**
 * VARIANT A's group: one expandable row where eight banners used to be.
 *
 * Inline rather than in a sheet, because A's whole thesis is that the
 * present anatomy is right and only its density is wrong. Closed it is
 * one line; open it is the same eight sentences, in the same order the
 * engine raised them.
 */
export function ThingsToKnowGroup({ warnings }: { warnings: readonly WarningEvent[] }) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (warnings.length === 0) return null;

  return (
    <section className="rounded-lg border bg-surface-sunken/40">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={listId}
        className="focus-ring flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "size-3.5 shrink-0 text-muted-foreground transition-transform duration-150",
            open && "rotate-90",
          )}
        />
        <span className="text-meta font-medium">{thingsToKnowLabel(warnings.length)}</span>
        {/* What the group is made of, so collapsing it never hides that
            some of these change how a number reads. */}
        <span className="text-micro text-muted-foreground">
          {thingsToKnowSeverity(warnings)}
        </span>
      </button>
      {open && (
        <div id={listId} className="border-t px-3 py-2.5">
          <CaveatList warnings={warnings} />
        </div>
      )}
    </section>
  );
}

/**
 * VARIANT B's sheet: the full truth, one tap from the integrity line.
 *
 * A dialog rather than an inline expansion because in B the answer is a
 * piece of writing and the caveats are its apparatus — opening them
 * should not reflow the sentence somebody is in the middle of reading.
 */
export function ThingsToKnowSheet({
  warnings,
  open,
  onOpenChange,
}: {
  warnings: readonly WarningEvent[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="overlay-in fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]" />
        <DialogPrimitive.Content className="panel-in fixed left-1/2 top-[12%] z-50 flex max-h-[76vh] w-[36rem] max-w-[calc(100vw-2rem)] -translate-x-1/2 flex-col overflow-hidden rounded-xl border bg-surface-overlay shadow-2xl shadow-black/20">
          <div className="flex items-start justify-between gap-4 border-b px-5 py-3.5">
            <div>
              <DialogPrimitive.Title className="text-body font-semibold">
                {thingsToKnowLabel(warnings.length)}
              </DialogPrimitive.Title>
              <DialogPrimitive.Description className="text-micro text-muted-foreground">
                Everything the platform attached to this answer, in the engine&apos;s own
                words. All of it travels with the copied answer and every CSV.
              </DialogPrimitive.Description>
            </div>
            <DialogPrimitive.Close
              aria-label="Close"
              className="focus-ring -mr-1 rounded p-1 text-muted-foreground hover:text-foreground"
            >
              <X className="size-4" />
            </DialogPrimitive.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-3.5">
            <CaveatList warnings={warnings} />
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}
