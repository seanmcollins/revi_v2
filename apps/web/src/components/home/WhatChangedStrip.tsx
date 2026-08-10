"use client";

import { AlertTriangle, ChevronDown } from "lucide-react";
import { useId, useState } from "react";

import type { BriefLeadHandle } from "@/components/monitors/BriefEntryRow";
import { BriefPanel } from "@/components/monitors/BriefPanel";
import { mediumDate } from "@/lib/format";
import type { BriefData } from "@/lib/monitors";

/**
 * WHAT CHANGED — the first thing on Home, and one line long.
 *
 * The whole product claim, made structural: Revi walks your Monitors every
 * load and tells you what changed, so the first thing on the page is what
 * changed rather than an empty composer waiting to be asked. The line is
 * the server's own `headline` — "Since the Aug 1 load: 3 monitors moved, 2
 * new leads, 1 new leader, 1 resolved on its own." — composed from the
 * counts, never by a model and never re-worded here.
 *
 * IT EXPANDS IN PLACE, and what it expands to is `BriefPanel`: the same
 * component Monitors renders, with the same entries, the same census, the
 * same materiality tooltip and the same withheld-visibly discipline. A
 * summarised second copy of a brief is how two surfaces start disagreeing
 * about one load.
 *
 * The headline is shown by exactly one of the two states — collapsed by
 * this strip, expanded by the panel, which leads with it. One sentence on
 * screen at a time.
 *
 * "NOTHING MATERIAL CHANGED" IS NOT AN EMPTY STATE. A quiet load is the
 * best morning this product has, so the quiet brief renders `BriefPanel`
 * directly, at the figure size it reserves for that sentence, with no
 * toggle — there is nothing withheld to go and look at.
 */
export function WhatChangedStrip({
  brief,
  leads,
  isPending,
  error,
}: {
  brief: BriefData | undefined;
  leads: ReadonlyMap<string, BriefLeadHandle>;
  isPending: boolean;
  error: unknown;
}) {
  const [open, setOpen] = useState(false);
  const detailId = useId();

  return (
    <section
      id="home-what-changed"
      tabIndex={-1}
      aria-labelledby="home-changed-heading"
      className="max-w-4xl space-y-3 outline-none"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2
            id="home-changed-heading"
            className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
          >
            What changed
          </h2>
          {/* The control lives WITH the label, not under the content it
              expands: opened, the brief runs several screens, and a
              "Hide" button at the bottom of it is a control you have to
              scroll past the thing you wanted to close to reach. */}
          {brief?.status === "material_changes" && (
            <button
              type="button"
              onClick={() => setOpen(!open)}
              aria-expanded={open}
              aria-controls={detailId}
              className="focus-ring inline-flex items-center gap-1 rounded text-micro font-medium text-muted-foreground underline decoration-dotted underline-offset-2 transition-colors duration-150 hover:text-foreground"
            >
              <ChevronDown
                aria-hidden
                className={`size-3 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
              />
              {open
                ? "Hide what changed"
                : `Show what changed — ${brief.entries.length} line${
                    brief.entries.length === 1 ? "" : "s"
                  }`}
            </button>
          )}
        </div>
        {/* The two dates, until the brief itself is on screen — its own
            census states them, and twice is once too many. */}
        {brief?.newestDataDate && !open && (
          <p className="num text-micro text-muted-foreground">
            Walked on the data through {safeDate(brief.newestDataDate)}
            {brief.priorNewestDataDate
              ? `, against the ${safeDate(brief.priorNewestDataDate)} load`
              : ""}
          </p>
        )}
      </header>

      {brief ? (
        brief.status === "material_changes" ? (
          open ? (
            <div id={detailId} className="fade-up">
              <BriefPanel brief={brief} leads={leads} />
            </div>
          ) : (
            // Collapsed: the sentence, at reading size, and nothing else.
            // This is the line somebody reads standing up.
            <p className="max-w-[64ch] text-lead leading-snug text-foreground">
              {brief.headline}
            </p>
          )
        ) : (
          // The proud state. No toggle: a quiet load has nothing folded
          // away, and the counts under the sentence are the measurement
          // that makes "nothing changed" a finding rather than a blank.
          <BriefPanel brief={brief} leads={leads} />
        )
      ) : isPending ? (
        // Named work, not a spinner, and a live region: this read re-runs
        // every monitor and verifies every claimed fix, so it is genuinely
        // slow the first time a load is opened and silence over that wait
        // is indistinguishable from a broken page.
        <p
          role="status"
          aria-live="polite"
          className="max-w-[64ch] text-lead leading-snug text-muted-foreground"
        >
          Walking your Monitors at this load — re-running each monitor and checking the fixes
          anyone claimed.
        </p>
      ) : (
        <p
          role="alert"
          className="flex max-w-[64ch] items-start gap-1.5 text-meta leading-snug text-negative"
        >
          <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span>
            Could not read what changed at this load.{" "}
            {error instanceof Error ? error.message : "The request did not complete."} Nothing
            here is out of date — there is nothing here.
          </span>
        </p>
      )}
    </section>
  );
}

function safeDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
}
