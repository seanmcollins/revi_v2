"use client";

import { ArrowRight } from "lucide-react";
import { useMemo } from "react";
import { Link } from "react-router-dom";

import { InvestigationChart } from "@/components/charts/InvestigationChart";
import { selectRenderableCharts } from "@/lib/contract";
import { chartWindowLabel } from "@/lib/format";
import type { HomeAnchor } from "@/lib/homeAnchor";
import { investigationLinkFor } from "@/lib/links";
import { readableLabel } from "@/lib/prose";
import { useInvestigationQuery } from "@/lib/queries";

/**
 * THE ONE FIGURE ON HOME, AND IT IS A REAL ONE.
 *
 * Not a summary chart, not a shape composed on the landing page: the
 * investigation behind this load's top-ranked object is READ
 * (`GET /v1/investigations/{iid}`) and its own published chart is drawn by
 * the same `InvestigationChart` the answer surface uses — the same marks,
 * the same ceiling and provisional vocabulary, the same CSV, the same
 * "Monitor this", and the same live drill: clicking a bar still emits the
 * typed `{op:"DrillInto"}` refinement, so the picture on the landing page
 * is an object you can work rather than a picture of one.
 *
 * WHOSE FIGURE IT IS, SAID OUT LOUD. `homeAnchor` walks a fallback chain
 * (leads first, then this load's brief, then the monitors), so the thing on
 * screen is not always the thing a reader would have guessed. The caption
 * names it and links to it. A chart on a dashboard with no stated subject
 * is indistinguishable from an invented one.
 *
 * WHAT IT DOES WHEN THERE IS NOTHING. Nothing. No skeleton that never
 * resolves, no example series, no empty axes: an investigation that
 * published no chart renders one quiet sentence and a way in, and a load
 * with no anchor at all renders zero pixels. The zone is allowed to be
 * absent; it is not allowed to be fake.
 */
export function LeadAnchor({ anchor, enabled }: { anchor: HomeAnchor; enabled: boolean }) {
  const query = useInvestigationQuery(anchor.investigationId, enabled);

  /**
   * The same selection the answer surface makes. A turn can publish a
   * frame and its superseded comparison twin; `selectRenderableCharts` is
   * the one place that decides which of them is the figure, and Home does
   * not get a second opinion about it.
   */
  const chart = useMemo(() => {
    const value = query.data?.value;
    if (value === undefined || value === null) return undefined;
    if (value.outcome !== "answer") return undefined;
    return selectRenderableCharts(value.charts)[0];
  }, [query.data]);

  /**
   * THE TWO WINDOWS, so the legend on Home says what the legend on the
   * answer says.
   *
   * Home mounted this chart with neither `windowLabel` nor
   * `comparisonWindows`, so a comparison anchored here degraded to "This
   * window" / "The window compared against" — honest, and vaguer than the
   * dates sitting on the very payload this component already read. The
   * answer surface passes both (`AnswerChart`); there is no reason Home
   * should be the surface that knows less.
   */
  const windows = useMemo(() => {
    const value = query.data?.value;
    if (value === undefined || value === null || value.outcome !== "answer") return undefined;
    const header = value.header;
    if (!header || header.asOf) return undefined;
    return {
      label: chartWindowLabel(header.window),
      ...(header.comparison
        ? {
            comparison: {
              current: chartWindowLabel(header.window),
              prior: header.comparison.label ?? chartWindowLabel(header.comparison.window),
            },
          }
        : {}),
    };
  }, [query.data]);

  const title = readableLabel(anchor.title);
  const link = investigationLinkFor(anchor.investigationId, "");

  return (
    <section
      id="home-anchor"
      aria-labelledby="home-anchor-heading"
      className="space-y-2"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id="home-anchor-heading"
          className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
        >
          {/* WHICH STEP OF THE CHAIN THIS CAME FROM, in the reader's words.
              "The lead this load ranked first" and "the monitor that moved
              furthest" are different claims and the heading says which one
              is on screen. */}
          {anchor.source === "lead"
            ? "The lead ranked first"
            : anchor.source === "brief"
              ? "What moved most at this load"
              : "Your first monitor, drawn"}
        </h2>
        <p className="text-micro text-muted-foreground">
          <Link
            to={link}
            className="focus-ring inline-flex items-center gap-1 rounded underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
          >
            Open the whole investigation
            <ArrowRight aria-hidden className="size-2.5" />
          </Link>
        </p>
      </header>

      {/* The subject, before the picture. */}
      <p className="max-w-[64ch] text-meta leading-snug text-muted-foreground">
        This figure belongs to <span className="font-medium text-foreground">{title}</span>.
      </p>

      {chart !== undefined ? (
        <div className="max-w-4xl">
          <InvestigationChart
            spec={chart}
            // The turn id a drill is attributed to. This IS the
            // investigation's own id, which is what the refinement path
            // expects on a figure read back from storage.
            turnId={anchor.investigationId}
            investigationId={anchor.investigationId}
            {...(windows?.label !== undefined ? { windowLabel: windows.label } : {})}
            {...(windows?.comparison ? { comparisonWindows: windows.comparison } : {})}
          />
        </div>
      ) : query.isPending ? (
        <p role="status" aria-live="polite" className="text-meta text-muted-foreground">
          Reading the investigation behind it…
        </p>
      ) : (
        // Two different absences, one sentence each, and neither of them a
        // broken-looking empty frame.
        <p className="max-w-[64ch] text-meta leading-snug text-muted-foreground">
          {query.error !== null && query.error !== undefined
            ? "Could not read the investigation behind this one — it is still reachable at its own link."
            : "This investigation published no figure — its answer is in words, at its own link."}
        </p>
      )}
    </section>
  );
}
