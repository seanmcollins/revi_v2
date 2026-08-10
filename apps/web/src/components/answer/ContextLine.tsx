"use client";

import { ChevronDown, History } from "lucide-react";

import { ContextHeader } from "@/components/answer/ContextHeader";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  chartWindowLabel,
  DATE_BASIS_LABELS,
  formatCount,
  mediumDate,
} from "@/lib/format";
import type { ContextHeaderData } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * The answer's context as ONE line of prose.
 *
 * §7.2 requires every answer to carry its explicit context — window and
 * basis, comparison, filters, cohort, data load. It does not require six
 * chips. Live, that row reads
 *
 *   RESTORED · WINDOW Jul 1–Jul 31 (service date) · SCOPE all ·
 *   DATA AS OF 2026-08-03 04:10 · Governed · Direct · [cache pill] · …
 *
 * which is eight controls and two timestamps above a paragraph nobody has
 * reached yet. Every fact in it is worth stating; none of them is worth a
 * control of its own on the first screen.
 *
 * So the same facts are set as a sentence — "Jul 2026 · service dates ·
 * no filters · data through Aug 2, 2026" — and the line itself is the
 * control: tapping it opens the chips, each with the plain-language
 * explanation it has always had. One tap to the whole context, two to any
 * single fact's definition, and nothing is stated here that the chips do
 * not state identically.
 */
export function contextSegments(
  header: ContextHeaderData,
  options: {
    /**
     * The turn carries `WINDOW_ASSUMED` — the question named no period
     * and the platform chose one.
     *
     * In the calm layout this line is the ONLY statement of the window on
     * the first screen, so an assumed window that is not marked here is a
     * caveat filed behind a link: the reader is told the answer covers
     * July without being told nobody asked for July. It is marked on the
     * line and stated in full in the sheet, which is the same discipline
     * the rest of the line follows.
     */
    windowAssumed?: boolean;
  } = {},
): string[] {
  const segments: string[] = [];

  // A SNAPSHOT contract states a balance AT a moment; the payload's
  // window is not what was measured, so the line says the as-of date and
  // no range — exactly as the chip does.
  const period = header.asOf
    ? `as of ${safeMediumDate(header.asOf)}`
    : chartWindowLabel(header.window);
  segments.push(options.windowAssumed ? `${period} (assumed)` : period);
  segments.push(`${DATE_BASIS_LABELS[header.window.basis]}s`);

  if (header.comparison) {
    segments.push(
      `vs ${header.comparison.label ?? chartWindowLabel(header.comparison.window)}`,
    );
  }

  segments.push(
    header.filters.length === 0
      ? "no filters"
      : header.filters
          .map((f) => `${f.dimensionLabel}: ${f.values.join(", ")}`)
          .join(" · "),
  );

  if (header.cohort) {
    const grain = header.cohort.entityGrain;
    segments.push(
      `${formatCount(header.cohort.size)} ${grain ? pluralGrain(grain, header.cohort.size) : "entities"}${
        header.cohort.pinned ? " (pinned)" : ""
      }`,
    );
  }

  // The DATA DATE, not the load timestamp. "2026-08-03 04:10" is a
  // machine instant printed to the minute in the analyst's most-trusted
  // line; how far the data runs is the fact they act on, and the exact
  // load time is inside the chip that owns it.
  segments.push(`data through ${safeMediumDate(header.watermark.newestDataDate)}`);

  return segments;
}

export function ContextLine({
  header,
  windowAssumed = false,
  className,
}: {
  header: ContextHeaderData;
  /** The turn carries `WINDOW_ASSUMED` — see `contextSegments`. */
  windowAssumed?: boolean;
  className?: string;
}) {
  const segments = contextSegments(header, { windowAssumed });

  return (
    <div className={cn("flex flex-wrap items-center gap-x-2 gap-y-1", className)}>
      <Popover>
        <PopoverTrigger asChild>
          {/* THE AFFORDANCE. Everything the chip row used to show lives
              behind this one control, so it has to read as a control: a
              persistent underline at ≥3:1 rather than a transparent one
              that appears on hover, and a disclosure caret. On a
              screenshot and on a touch screen a hover state does not
              exist, and what shipped was indistinguishable from a
              caption — which made the calm layout's context strictly
              less reachable than the chips it replaced. */}
          <button
            type="button"
            className="focus-ring group inline-flex max-w-full items-center gap-1 rounded text-micro text-muted-foreground transition-colors duration-150 hover:text-foreground"
            aria-label={`Context for this answer: ${segments.join(", ")}. Open for the full definition of each.`}
          >
            <span className="num truncate underline decoration-foreground/40 underline-offset-[3px] transition-colors duration-150 group-hover:decoration-foreground">
              {segments.join(" · ")}
            </span>
            <ChevronDown
              aria-hidden
              className="size-3 shrink-0 text-muted-foreground transition-transform duration-150 group-hover:text-foreground"
            />
          </button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[27rem] max-w-[calc(100vw-2rem)] p-3.5">
          <p className="mb-2 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
            What this answer was measured under
          </p>
          {/* The chips themselves, unchanged — each one still opens the
              explanation it has always carried. Nothing about the context
              is re-worded on the way in here. */}
          <ContextHeader header={header} />
        </PopoverContent>
      </Popover>

      {/* A RESTORED turn says so once, quietly. The line above is the same
          set of facts the live turn published; this mark says they were
          read back rather than monitored, and the popover's own Restored
          chip carries the server's account of what the store kept.

          Solid muted ink, not 80% of it: `--muted-foreground` at 80%
          measures 3.48:1 on card, 3.27:1 on the page and 3.16:1 on sunken
          — under the 4.5:1 AA floor at 12px. Solid: 5.24 / 4.80 / 4.57.
          The Monitors surface banned the class after round 8 and this was
          the straggler; the ban is repo-wide now. */}
      {header.restored && (
        <span className="inline-flex items-center gap-1 text-micro text-muted-foreground">
          <History aria-hidden className="size-3" />
          Restored
        </span>
      )}
    </div>
  );
}

/** `mediumDate` throws on anything that is not an ISO date. */
function safeMediumDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
}

/** The entity grain as a countable noun — the cohort chip's own rule. */
function pluralGrain(grain: string, size: number): string {
  if (grain === "") return size === 1 ? "entity" : "entities";
  if (size === 1) return grain;
  return grain.endsWith("s") ? grain : `${grain}s`;
}
