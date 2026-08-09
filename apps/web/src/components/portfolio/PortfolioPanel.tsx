"use client";

import { ArrowUpRight, ChevronDown, Info, Ban } from "lucide-react";
import { useState } from "react";

import { DetectionBadge } from "@/components/portfolio/DetectionBadge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatWholeDollars } from "@/lib/format";
import { PORTFOLIO_ITEMS, PORTFOLIO_META, type PortfolioItem } from "@/lib/mock/portfolio";
import { usePortfolioQuery } from "@/lib/queries";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/** How many cards the rail shows before "Show all". */
const COLLAPSED_COUNT = 5;

const SEVERITY_TONE: Record<string, string> = {
  critical: "border-negative/45 text-negative",
  high: "border-warning/50 text-warning",
  medium: "border-border text-muted-foreground",
  low: "border-border text-muted-foreground",
};

/**
 * The pre-materialized portfolio, ranked by the governed priority formula.
 * Each drillable card opens an ordinary session turn at the portfolio's
 * pinned watermark.
 *
 * In api mode this comes from GET /v1/portfolio/latest (a 501 is the
 * graceful "not built yet"); mock mode keeps the local fixture.
 *
 * Cards wear a DETECTION provenance chip, never a GradeBadge: they are
 * external detections read at a watermark, not numbers this platform
 * computed from certified semantics.
 *
 * Three facts this panel used to hide, all of them published on every card
 * since the endpoint shipped:
 *
 *   the platform refuses to drill 4 of 33 live cards (36% of ranked
 *     impact) and says why on each — the panel showed an identical live
 *     "Drill in" button on all 33;
 *   `recoverable_cents_estimate` is routinely a fraction of `impact_cents`
 *     (one card: $493,266 detected, ~$9,865 recoverable) — the panel
 *     showed only the big number;
 *   nine cards drill a DIFFERENT metric than they report, with a written
 *     rationale — the panel said nothing.
 */
export function PortfolioPanel() {
  const emitRefinement = useSessionStore((s) => s.emitRefinement);
  const submit = useSessionStore((s) => s.submit);
  const mode = useSessionStore((s) => s.connection.mode);
  const query = usePortfolioQuery(mode === "api");
  const [expanded, setExpanded] = useState(false);

  let items: PortfolioItem[] = PORTFOLIO_ITEMS;
  let warnings: string[] = [];
  // "Watermark" is the engine's word for the pinned data load; the panel
  // says "data as of", which is the same fact in the analyst's words.
  let footer = `Computed on the data as of ${PORTFOLIO_META.watermark} · drilling in asks an ordinary question against that same data`;
  let emptyNote: string | null = null;

  if (mode === "api") {
    if (query.data?.kind === "ok") {
      items = query.data.snapshot.items;
      warnings = query.data.snapshot.warnings;
      const at = query.data.snapshot.watermark;
      footer = at
        ? `Computed on the data as of ${at} · drilling in asks an ordinary question against that same data`
        : "Drilling in asks an ordinary question against the same data this list was built on";
      if (items.length === 0) emptyNote = "Nothing flagged in this data load.";
    } else {
      items = [];
      emptyNote = query.isPending
        ? "Loading portfolio…"
        : query.data?.kind === "unavailable"
          ? "Portfolio endpoint not implemented yet (HTTP 501)."
          : "Portfolio unreachable — will retry.";
      footer = "";
    }
  }

  const shown = expanded ? items : items.slice(0, COLLAPSED_COUNT);
  const hidden = items.length - shown.length;

  return (
    <section className="space-y-2">
      <header className="flex items-baseline justify-between">
        <h3 className="text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
          Today&apos;s portfolio
        </h3>
        <span className="num font-mono text-[0.58rem] text-muted-foreground">
          {mode === "api"
            ? (query.data?.kind === "ok" ? (query.data.snapshot.rankingPolicy ?? "") : "")
            : PORTFOLIO_META.rankingPolicy}
        </span>
      </header>

      {/* The snapshot's own caveats about the list, above the list —
          not a footnote under 33 rows nobody scrolled to. */}
      {warnings.map((warning) => (
        <p
          key={warning}
          className="flex items-start gap-1.5 rounded-md border border-warning/35 bg-warning/10 px-2 py-1.5 text-[0.62rem] leading-snug"
        >
          <Info className="mt-px size-3 shrink-0 text-warning" />
          {warning}
        </p>
      ))}

      {emptyNote ? (
        <p className="px-1 text-[0.62rem] leading-snug text-muted-foreground">{emptyNote}</p>
      ) : (
        <>
          <ol className="space-y-1.5">
            {shown.map((item) => (
              <PortfolioCard
                key={item.referent}
                item={item}
                onDrill={() => {
                  if (item.drillSpec) {
                    // A card is not a refinement of whatever you were
                    // looking at: its handle is a typed FIRST turn, so it
                    // opens its own investigation (§18.1-10).
                    void submit({ spec: item.drillSpec });
                  } else if (item.drill) {
                    emitRefinement(item.drill.refinement, { referent: item.referent });
                  }
                }}
              />
            ))}
          </ol>
          {hidden > 0 && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setExpanded(true)}
              className="h-6 w-full gap-1 text-[0.65rem] font-normal text-muted-foreground hover:text-foreground"
            >
              <ChevronDown className="size-3" />
              Show all ({items.length})
            </Button>
          )}
          {expanded && items.length > COLLAPSED_COUNT && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setExpanded(false)}
              className="h-6 w-full gap-1 text-[0.65rem] font-normal text-muted-foreground hover:text-foreground"
            >
              Show top {COLLAPSED_COUNT}
            </Button>
          )}
        </>
      )}

      {footer && <p className="num text-[0.58rem] leading-snug text-muted-foreground">{footer}</p>}
    </section>
  );
}

function PortfolioCard({ item, onDrill }: { item: PortfolioItem; onDrill: () => void }) {
  const canDrill = item.drillable && (item.drillSpec !== undefined || item.drill !== undefined);
  // Only worth saying when the two numbers disagree — a card whose whole
  // impact is recoverable should not spend a line repeating itself.
  const recoverable =
    item.recoverableCentsEstimate !== undefined &&
    item.recoverableCentsEstimate !== item.impactCents
      ? item.recoverableCentsEstimate
      : undefined;

  return (
    <li
      className={cn(
        "group rounded-md border bg-card p-2.5 transition-colors duration-150 hover:border-ring/40",
        "focus-within:border-ring/40",
        !item.drillable && "border-dashed",
      )}
    >
      <div className="flex items-start gap-2">
        <span className="num mt-px w-3 shrink-0 text-[0.65rem] font-medium text-muted-foreground">
          {item.rank}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-[0.7rem] font-medium leading-snug" title={item.title}>
            {item.title}
          </p>

          {/* Impact and what of it is actually recoverable — the second
              number is the one that decides whether this is worth a
              morning, and it was not on screen at all. */}
          <p className="numeral mt-1 text-[0.86rem] font-medium leading-tight">
            <span className={cn(item.impactCents < 0 && "text-negative")}>
              {formatWholeDollars(item.impactCents)}
            </span>
            <span className="ml-1.5 text-[0.6rem] font-normal text-muted-foreground">
              {item.impactLabel || "detected"}
            </span>
          </p>
          {recoverable !== undefined && (
            <p className="num mt-0.5 text-[0.62rem] leading-snug text-muted-foreground">
              ~{formatWholeDollars(recoverable)} recoverable
              {item.actionabilityLabel && (
                <>
                  {" — "}
                  <ActionabilityLabel item={item} />
                </>
              )}
            </p>
          )}
          {recoverable === undefined && item.actionabilityLabel && (
            <p className="mt-0.5 text-[0.62rem] leading-snug text-muted-foreground">
              <ActionabilityLabel item={item} />
            </p>
          )}

          {/* The detector's own grading and how stale it is. */}
          {(item.severity || item.ageDays !== undefined) && (
            <p className="mt-1 flex items-center gap-1.5 text-[0.58rem] uppercase tracking-wide">
              {item.severity && (
                <span
                  className={cn(
                    "rounded-full border px-1.5 py-px font-medium",
                    SEVERITY_TONE[item.severity] ?? "border-border text-muted-foreground",
                  )}
                >
                  {item.severity}
                </span>
              )}
              {item.ageDays !== undefined && (
                <span className="num text-muted-foreground">
                  detected {item.ageDays}d ago
                </span>
              )}
            </p>
          )}

          <p className="mt-1 line-clamp-2 text-[0.62rem] leading-snug text-muted-foreground">
            {item.detail}
          </p>

          {/* The drill probes a different measure than the card reports,
              and the server wrote down why. Only said when the two names
              genuinely differ — `drill_spec.metric_ids[0]` is the measure
              that will actually be probed, NOT the card's `metric_id`. */}
          {item.drillRepointedFrom && item.drillMetricId !== item.drillRepointedFrom && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="mt-1 flex items-center gap-1 rounded text-left text-[0.58rem] leading-snug text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                >
                  <Info className="size-2.5 shrink-0" />
                  Drills {item.drillMetricId ?? "a different measure"}, not{" "}
                  {item.drillRepointedFrom}
                </button>
              </TooltipTrigger>
              <TooltipContent side="right" className="max-w-72 text-[0.68rem] leading-snug">
                {item.drillRepointRationale ??
                  `This card reports ${item.drillRepointedFrom}; its drill probes ${item.drillMetricId ?? "another measure"}.`}
              </TooltipContent>
            </Tooltip>
          )}

          <div className="mt-1.5 flex items-center justify-between gap-2">
            <DetectionBadge
              priorityFormulaVersion={item.priorityFormulaVersion}
              sourceWatermarkId={item.sourceWatermarkId}
            />
            {canDrill ? (
              <Button
                variant="ghost"
                size="xs"
                aria-label={`Drill into ${item.title}`}
                className="h-5 gap-0.5 rounded-full px-1.5 text-[0.62rem] font-normal text-verified opacity-0 transition-opacity duration-150 hover:text-verified group-hover:opacity-100 focus-visible:opacity-100"
                onClick={onDrill}
              >
                {item.drill?.label ?? "Drill in"}
                <ArrowUpRight className="size-2.5" />
              </Button>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  <span tabIndex={0} className="rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60">
                    <Button
                      variant="ghost"
                      size="xs"
                      disabled
                      aria-label={`Cannot drill into ${item.title} — ${item.drillUnavailableReason ?? "the platform refused this drill"}`}
                      className="pointer-events-none h-5 gap-0.5 rounded-full px-1.5 text-[0.62rem] font-normal text-muted-foreground"
                    >
                      <Ban className="size-2.5" />
                      Can&apos;t drill
                    </Button>
                  </span>
                </TooltipTrigger>
                <TooltipContent side="left" className="max-w-72 text-[0.68rem] leading-snug">
                  {/* The platform's own refusal, verbatim. Softening it
                      into "unavailable" would hide which dimension and
                      which grain made it impossible. */}
                  {item.drillUnavailableReason ??
                    "This deployment cannot open this card as an investigation."}
                </TooltipContent>
              </Tooltip>
            )}
          </div>
        </div>
      </div>
    </li>
  );
}

/** The label, with the engine's rationale one hover away. */
function ActionabilityLabel({ item }: { item: PortfolioItem }) {
  if (!item.actionabilityRationale) {
    return <span className="font-medium">{item.actionabilityLabel}</span>;
  }
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="rounded font-medium underline decoration-dotted underline-offset-2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
        >
          {item.actionabilityLabel}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-80 text-[0.68rem] leading-snug">
        {item.actionabilityRationale}
      </TooltipContent>
    </Tooltip>
  );
}
