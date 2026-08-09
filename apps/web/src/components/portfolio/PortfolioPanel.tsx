"use client";

import { ArrowUpRight, Ban, Check, ChevronDown, GitCompareArrows, Info } from "lucide-react";
import { useState } from "react";

import { WarningList } from "@/components/banners/WarningBanner";
import { DetectionBadge } from "@/components/portfolio/DetectionBadge";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatSignedPct, formatWholeDollars } from "@/lib/format";
import {
  PORTFOLIO_ITEMS,
  PORTFOLIO_META,
  type PortfolioItem,
  type PortfolioLane,
} from "@/lib/mock/portfolio";
import { usePortfolioQuery } from "@/lib/queries";
import { useSessionStore } from "@/lib/store";
import type { WarningEvent } from "@/lib/types";
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
  let warnings: Omit<WarningEvent, "type">[] = [];
  let lanes: PortfolioLane[] = [];
  // "Watermark" is the engine's word for the pinned data load; the panel
  // says "data as of", which is the same fact in the analyst's words.
  let footer = `Computed on the data as of ${PORTFOLIO_META.watermark} · drilling in asks an ordinary question against that same data`;
  let emptyNote: string | null = null;

  if (mode === "api") {
    if (query.data?.kind === "ok") {
      items = query.data.snapshot.items;
      warnings = query.data.snapshot.warnings;
      lanes = query.data.snapshot.lanes;
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

  // The lane split as the SERVER decided it, in its order. Cards named by
  // no lane are not dropped — they land in a trailing ungrouped section,
  // because a worklist that silently loses a card is worse than one whose
  // headings are imperfect.
  const groups = groupByLane(items, lanes);
  const shownCount = groups.reduce(
    (total, group) => total + (expanded ? group.items.length : Math.min(group.items.length, COLLAPSED_COUNT)),
    0,
  );
  const hidden = items.length - shownCount;

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
          not a footnote under 33 rows nobody scrolled to. Code-driven
          since the endpoint started publishing `warnings_v2`, so a
          caution about un-openable cards is styled apart from a note. */}
      <WarningList warnings={warnings.map((w) => ({ ...w, type: "warning" as const }))} />

      {emptyNote ? (
        <p className="px-1 text-[0.62rem] leading-snug text-muted-foreground">{emptyNote}</p>
      ) : (
        <>
          {groups.map((group) => (
            <section key={group.id} className="space-y-1.5">
              {/* Only when the server actually split the list. One lane
                  (or none) gets no heading — a division nobody made
                  should not be announced. */}
              {groups.length > 1 && <LaneHeader lane={group.lane} count={group.items.length} />}
              <ol className="space-y-1.5">
                {(expanded ? group.items : group.items.slice(0, COLLAPSED_COUNT)).map((item) => (
                  <PortfolioCard
                    key={item.referent}
                    item={item}
                    onDrill={() => {
                      if (item.drillSpec) {
                        // A card is not a refinement of whatever you were
                        // looking at: its handle is a typed FIRST turn, so
                        // it opens its own investigation (§18.1-10). The
                        // card's id rides along so the answer can reconcile
                        // its own figure against the one on this card.
                        void submit({ spec: item.drillSpec, anomalyRef: item.referent });
                      } else if (item.drill) {
                        emitRefinement(item.drill.refinement, { referent: item.referent });
                      }
                    }}
                  />
                ))}
              </ol>
            </section>
          ))}
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
          {expanded && shownCount > 0 && items.length > COLLAPSED_COUNT && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setExpanded(false)}
              className="h-6 w-full gap-1 text-[0.65rem] font-normal text-muted-foreground hover:text-foreground"
            >
              Show top {COLLAPSED_COUNT}
              {groups.length > 1 ? " per lane" : ""}
            </Button>
          )}
        </>
      )}

      {footer && <p className="num text-[0.58rem] leading-snug text-muted-foreground">{footer}</p>}
    </section>
  );
}

/** One rendered section of the rail: a published lane, or the leftovers. */
interface LaneGroup {
  id: string;
  lane?: PortfolioLane;
  items: PortfolioItem[];
}

/**
 * Split the cards the way the server split them.
 *
 * `lanes` carries its own membership AND its own order (`anomalyIds` is
 * the ranking), so this follows it rather than re-deriving a ranking from
 * scores the client does not own. Two rules keep it honest:
 *
 *   a lane names a card the snapshot does not carry → skipped silently,
 *     because there is nothing to draw;
 *   a card no lane names → kept, in a trailing ungrouped section. A
 *     worklist that quietly drops work is the one failure this panel
 *     cannot have.
 *
 * With no lanes published (mock mode, or a deployment that does not split)
 * everything lands in a single unlabelled group and the rail looks exactly
 * as it did.
 */
function groupByLane(items: PortfolioItem[], lanes: PortfolioLane[]): LaneGroup[] {
  if (lanes.length === 0) return items.length > 0 ? [{ id: "all", items }] : [];
  const byReferent = new Map(items.map((item) => [item.referent, item]));
  const claimed = new Set<string>();
  const groups: LaneGroup[] = [];
  for (const lane of lanes) {
    const laneItems: PortfolioItem[] = [];
    for (const id of lane.anomalyIds) {
      const item = byReferent.get(id);
      if (item === undefined || claimed.has(id)) continue;
      claimed.add(id);
      laneItems.push(item);
    }
    if (laneItems.length > 0) groups.push({ id: lane.id, lane, items: laneItems });
  }
  const orphans = items.filter((item) => !claimed.has(item.referent));
  if (orphans.length > 0) groups.push({ id: "ungrouped", items: orphans });
  return groups;
}

/**
 * A lane heading with the server's own explanation of why the lane
 * exists. Compliance work is done because the rule says so — a $824
 * credit balance and a $84,000 one carry the same obligation — and value
 * work is ranked by what is recoverable; mixing them into one ordered list
 * is what let a small mandatory refund sink below discretionary work.
 */
function LaneHeader({ lane, count }: { lane?: PortfolioLane; count: number }) {
  if (!lane) {
    return (
      <p className="px-0.5 text-[0.6rem] font-semibold uppercase tracking-wide text-muted-foreground">
        Not in a lane ({count})
      </p>
    );
  }
  return (
    <div className="flex items-baseline justify-between gap-2 px-0.5">
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className="rounded text-left text-[0.6rem] font-semibold uppercase tracking-wide text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground focus-ring"
          >
            {lane.label}
          </button>
        </TooltipTrigger>
        <TooltipContent side="right" className="max-w-80 text-[0.68rem] leading-snug">
          {lane.description}
        </TooltipContent>
      </Tooltip>
      <span className="num text-[0.58rem] text-muted-foreground">
        {lane.itemCount} · {formatWholeDollars(lane.impactCents)}
      </span>
    </div>
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

          <ImpactAgreement item={item} />
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

          {/* What the number MEASURES, in the pack's governed words. A
              worklist has no answer to hang a §6.6 population caveat on,
              so the correction travels on the card — and it is published
              only for the ids that overclaim, which is why most cards
              show nothing here. */}
          {item.metricDisplayName && (
            <p className="mt-1 text-[0.6rem] leading-snug text-muted-foreground">
              Measures <span className="font-medium">{item.metricDisplayName}</span>
            </p>
          )}

          {/* The drill probes a different measure than the card reports,
              and the server wrote down why. Only said when the two names
              genuinely differ — `drill_spec.metric_ids[0]` is the measure
              that will actually be probed, NOT the card's `metric_id`. */}
          {item.drillRepointedFrom && item.drillMetricId !== item.drillRepointedFrom && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="mt-1 flex items-center gap-1 rounded text-left text-[0.58rem] leading-snug text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground focus-ring"
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
              priority={item.priority}
              priorityScore={item.priorityScore}
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
                  <span tabIndex={0} className="rounded-full focus-ring">
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

/**
 * Whether the card's dollar figure survives this platform's own arithmetic.
 *
 * The card's number is the external detection system's assertion — its
 * window, its population, its valuation basis, computed when it fired.
 * The platform now re-derives the same named cell from its governed
 * contract at the pinned watermark and publishes BOTH, plus the delta and
 * a written reason. Live, 12 of 29 ranked cards diverge (largest gap
 * 131.5%) and 8 could not be re-derived at all — so a rail that shows only
 * the detector's figure is showing a number this platform does not agree
 * with, without saying so.
 *
 * Three states, three different claims, none of them collapsed into
 * another: agreed (a quiet confirmation), diverged (both figures, the
 * gap, and the reason on hover), unavailable (the detector's figure
 * stands alone, and the card says that is what it is).
 */
function ImpactAgreement({ item }: { item: PortfolioItem }) {
  const agreement = item.impactAgreement;
  if (agreement === undefined) return null;

  if (agreement === "agreed") {
    return (
      <p className="num mt-0.5 flex items-center gap-1 text-[0.6rem] leading-snug text-verified">
        <Check className="size-2.5 shrink-0" aria-hidden />
        Matches this platform&apos;s own figure
      </p>
    );
  }

  const note =
    item.impactReconciliationNote ||
    (agreement === "diverged"
      ? "This platform re-derived a different figure for the same cell from its governed contract."
      : "This platform could not re-derive this figure from its governed contracts.");

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={cn(
            "num mt-0.5 flex items-center gap-1 rounded text-left text-[0.6rem] leading-snug underline decoration-dotted underline-offset-2 focus-ring",
            agreement === "diverged" ? "text-warning" : "text-muted-foreground",
          )}
        >
          <GitCompareArrows className="size-2.5 shrink-0" aria-hidden />
          {agreement === "diverged" && item.reconciledImpactCents !== undefined ? (
            <>
              this platform: {formatWholeDollars(item.reconciledImpactCents)}
              {item.impactDeltaFraction !== undefined && (
                <span className="font-medium">
                  {" "}
                  ({formatSignedPct(item.impactDeltaFraction)})
                </span>
              )}
            </>
          ) : (
            "not re-derived here — the detector's figure alone"
          )}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-80 text-[0.68rem] leading-snug">
        {note}
      </TooltipContent>
    </Tooltip>
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
          className="rounded font-medium underline decoration-dotted underline-offset-2 hover:text-foreground focus-ring"
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
