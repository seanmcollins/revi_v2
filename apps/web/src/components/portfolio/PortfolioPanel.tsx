"use client";

import {
  ArrowDownWideNarrow,
  ArrowUpRight,
  Ban,
  Check,
  ChevronDown,
  GitCompareArrows,
  Info,
} from "lucide-react";
import { useId, useState } from "react";

import { DownloadCsvButton } from "@/components/answer/AnswerActions";
import { WarningList } from "@/components/banners/WarningBanner";
import { DetectionBadge } from "@/components/portfolio/DetectionBadge";
import { LeadStatusControl } from "@/components/monitors/LeadStatus";
import { TimeToImpactLine } from "@/components/monitors/TimeToImpactLine";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { portfolioToCsv } from "@/lib/export";
import {
  dataLoadDate,
  formatSignedPct,
  formatWholeDollars,
  humanizeIsoDates,
  mediumDate,
  rankingVersionLabel,
} from "@/lib/format";
import { humanizeInline } from "@/lib/humanize";
import {
  PORTFOLIO_ITEMS,
  PORTFOLIO_META,
  type DrillDimensionRepoint,
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
 * In api mode this comes from GET /v1/portfolio/latest; mock mode keeps
 * the local fixture. The route is unconditional and always answers 200: a
 * data load with nothing detected is a normal snapshot carrying
 * `status: "empty"` and the feed's own `PORTFOLIO_FEED_EMPTY` warning, so
 * the only two states here are "a snapshot arrived" and "the request
 * failed". The deployment-serves-no-worklist branch this panel used to
 * carry described a mode the server does not have.
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
  const listId = useId();

  let items: PortfolioItem[] = PORTFOLIO_ITEMS;
  let warnings: Omit<WarningEvent, "type">[] = [];
  let lanes: PortfolioLane[] = [];
  let cashTimingLanes: PortfolioLane[] = [];
  // "Watermark" is the engine's word for the pinned data load; the panel
  // says "data as of", which is the same fact in the analyst's words. The
  // load is named by its DATE — the payload spells it as a loader instant
  // ("2026-08-03 04:10") or, on some deployments, as a bare id, and
  // neither is a thing to print inside a sentence. `dataLoadDate` returns
  // nothing for an id, and the clause drops rather than leaking it.
  let footerLoad: string | undefined = dataLoadDate(PORTFOLIO_META.watermark);
  let footerExact: string | undefined = PORTFOLIO_META.watermark;
  let emptyNote: string | null = null;

  if (mode === "api") {
    if (query.data) {
      items = query.data.items;
      warnings = query.data.warnings;
      lanes = query.data.lanes;
      cashTimingLanes = query.data.cashTimingLanes;
      footerExact = query.data.watermark;
      footerLoad = dataLoadDate(query.data.watermark);
      // A quiet data load, in the SNAPSHOT's own words. `status: "empty"`
      // is the server saying the detection feed found nothing at this
      // watermark — a fact about the data, not about the deployment and
      // not about this panel. The feed publishes its own
      // PORTFOLIO_FEED_EMPTY warning alongside it, which renders above
      // this line, so the note stays short and does not restate it.
      if (items.length === 0) {
        emptyNote =
          query.data.status === "empty"
            ? "Nothing was detected at this data load — the worklist is empty, not missing."
            : "Nothing flagged in this data load.";
      }
    } else {
      items = [];
      emptyNote = query.isPending ? "Loading portfolio…" : "Portfolio unreachable — will retry.";
      footerLoad = undefined;
      footerExact = undefined;
    }
  }

  const rankingPolicy =
    mode === "api" ? (query.data?.rankingPolicy ?? "") : PORTFOLIO_META.rankingPolicy;

  const footer =
    footerExact === undefined
      ? ""
      : footerLoad
        ? `Computed on the data as of ${footerLoad} · drilling in asks an ordinary question against that same data`
        : "Drilling in asks an ordinary question against the same data this list was built on";

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
      <header className="flex items-baseline justify-between gap-2">
        <h3 className="text-meta font-semibold uppercase tracking-wide text-muted-foreground">
          Today&apos;s portfolio
        </h3>
        <span className="flex items-baseline gap-1.5">
          {/* Monday's work leaves as a spreadsheet or it does not leave at
              all. Every honesty column travels with it — what this
              platform re-derived, whether the two agree, how much is
              actually recoverable, which lane, and the priority score —
              because a worklist exported down to rank/title/impact is the
              detector's assertion with the platform's own disagreement
              stripped out. Built from the snapshot already in this
              browser; no request is made. */}
          {items.length > 0 && (
            <DownloadCsvButton
              label="CSV"
              title={`Download all ${items.length} cards as CSV — impact, this platform's re-derivation, whether they agree, recoverable estimate, actionability, lane and priority score. Nothing leaves this browser.`}
              filenameKind="worklist"
              // The data load goes in its own slot now, not smuggled
              // through the free-text tag — see `exportFilename`.
              watermark={
                mode === "api" && query.data
                  ? query.data.watermark
                  : PORTFOLIO_META.watermark
              }
              className="h-5 px-1.5 text-micro"
              csv={() =>
                portfolioToCsv({
                  items,
                  ...(mode === "api" && query.data
                    ? {
                        ...(query.data.watermark
                          ? { watermark: query.data.watermark }
                          : {}),
                        ...(query.data.rankingPolicy
                          ? { rankingPolicy: query.data.rankingPolicy }
                          : {}),
                      }
                    : {
                        watermark: PORTFOLIO_META.watermark,
                        rankingPolicy: PORTFOLIO_META.rankingPolicy,
                      }),
                })
              }
            />
          )}
          {/* Which version of the ranking put these cards in this order —
              a real fact, because two lists ranked by different versions
              are not comparable. Spelled as a version rather than as the
              payload's `anomaly_priority@3`, with the exact identifier on
              the title for whoever needs to quote it. */}
          {rankingPolicy && (
            <span className="num text-micro text-muted-foreground" title={rankingPolicy}>
              {rankingVersionLabel(rankingPolicy)}
            </span>
          )}
        </span>
      </header>

      {/* The snapshot's own caveats about the list, above the list —
          not a footnote under 33 rows nobody scrolled to. Code-driven
          since the endpoint started publishing `warnings_v2`, so a
          caution about un-openable cards is styled apart from a note. */}
      <WarningList warnings={warnings.map((w) => ({ ...w, type: "warning" as const }))} />

      {/* HOW MUCH OF THIS IS STILL CATCHABLE. The one total a director
          asks for before they allocate a morning, and it is not the
          ranking — it is the same cards split by cash timing. */}
      <CashTimingSummary lanes={cashTimingLanes} />

      {emptyNote ? (
        <p className="px-1 text-micro leading-snug text-muted-foreground">{emptyNote}</p>
      ) : (
        <div id={listId} className="space-y-2">
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
              aria-expanded={false}
              aria-controls={listId}
              className="h-6 w-full gap-1 text-meta font-normal text-muted-foreground hover:text-foreground"
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
              aria-expanded
              aria-controls={listId}
              className="h-6 w-full gap-1 text-meta font-normal text-muted-foreground hover:text-foreground"
            >
              Show top {COLLAPSED_COUNT}
              {groups.length > 1 ? " per lane" : ""}
            </Button>
          )}
        </div>
      )}

      {footer && (
        // The load's exact identifier stays one hover away — the sentence
        // carries the date, the title carries what the payload said.
        <p className="num text-micro leading-snug text-muted-foreground" title={footerExact}>
          {footer}
        </p>
      )}
    </section>
  );
}

/**
 * STILL CATCHABLE — the worklist split by cash timing rather than by
 * governance.
 *
 * The question this answers is "how much money have we not lost yet?", and
 * until the server published this split it could not be answered from a
 * payload that contained the answer: every card carried its own
 * `time_to_impact.lane`, no surface totalled it, and the one figure on
 * screen was $830,501.93 of governed recoverable estimate across all
 * thirty-three — a number that reads like a reply to the question and is
 * not one.
 *
 * Three rules it keeps.
 *
 *   IT RENDERS ONLY WHAT THE SERVER SPLIT. No client-side summing over
 *     whatever cards happen to be on this page: `items` is a PAGE and the
 *     lanes describe the whole population, so a total derived here would
 *     be a fraction wearing a total's clothes.
 *   IT NAMES WHICH DOLLARS. The recoverable estimate when the lane
 *     publishes one (what is left to save), the detected impact otherwise
 *     (what went wrong) — never the two silently interchanged.
 *   A DEADLINE IS A REAL DATE OR IT IS NOTHING. `soonest_deadline_date`
 *     is only ever a limit the detector published; a projection never
 *     sets one, because an estimate rendered beside a filing deadline is
 *     indistinguishable from one. And the count of cards that carry a date
 *     travels with it, so a horizon computed from three of thirty-one is
 *     read as a fact about three.
 */
function CashTimingSummary({ lanes }: { lanes: PortfolioLane[] }) {
  if (lanes.length === 0) return null;
  const order = ["pre_cash", "already_hit", "unknown"];
  const sorted = [...lanes].sort(
    (a, b) =>
      (order.indexOf(a.id) === -1 ? order.length : order.indexOf(a.id)) -
      (order.indexOf(b.id) === -1 ? order.length : order.indexOf(b.id)),
  );
  return (
    <ul data-cash-timing className="space-y-0.5 px-0.5">
      {sorted.map((lane) => {
        const recoverable = lane.recoverableCents;
        const total = recoverable ?? lane.impactCents;
        return (
          <li key={lane.id} className="num text-micro leading-snug">
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="focus-ring rounded text-left underline decoration-dotted underline-offset-2"
                >
                  <span
                    className={cn(
                      "font-medium",
                      lane.id === "pre_cash" ? "text-foreground" : "text-muted-foreground",
                    )}
                  >
                    {lane.id === "pre_cash" ? "Still catchable" : lane.label}
                  </span>
                  <span className="text-muted-foreground">
                    : {formatWholeDollars(total)}
                    {recoverable !== undefined ? " recoverable" : " detected"} across{" "}
                    {lane.itemCount} lead{lane.itemCount === 1 ? "" : "s"}
                  </span>
                </button>
              </TooltipTrigger>
              <TooltipContent side="right" className="max-w-80 text-meta leading-snug">
                {lane.description}
              </TooltipContent>
            </Tooltip>
            {/* The soonest REAL dated limit in this lane, and how many of
                its cards carry one at all. */}
            {lane.soonestDeadlineDate !== undefined && (
              <span
                className={cn(
                  "block text-micro",
                  // A deadline in the PAST is not a horizon, it is a loss —
                  // and "-47 days" is a number nobody reads as one. Live,
                  // the soonest dated limit in the still-catchable lane had
                  // already passed by seven weeks.
                  (lane.soonestDeadlineDays ?? 0) < 0 ? "text-warning" : "text-muted-foreground",
                )}
              >
                Soonest deadline {safeMediumDate(lane.soonestDeadlineDate)}
                {lane.soonestDeadlineDays !== undefined &&
                  (lane.soonestDeadlineDays < 0
                    ? ` — passed ${Math.abs(lane.soonestDeadlineDays)} day${
                        lane.soonestDeadlineDays === -1 ? "" : "s"
                      } ago`
                    : ` — ${lane.soonestDeadlineDays} day${
                        lane.soonestDeadlineDays === 1 ? "" : "s"
                      }`)}
                {lane.datedItemCount > 0 &&
                  `, on ${lane.datedItemCount} of ${lane.itemCount} of them`}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function safeMediumDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
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
      <p className="px-0.5 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
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
            className="rounded text-left text-micro font-semibold uppercase tracking-wide text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground focus-ring"
          >
            {lane.label}
          </button>
        </TooltipTrigger>
        <TooltipContent side="right" className="max-w-80 text-meta leading-snug">
          {lane.description}
        </TooltipContent>
      </Tooltip>
      <span className="num text-micro text-muted-foreground">
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
        <span className="num mt-px w-3 shrink-0 text-meta font-medium text-muted-foreground">
          {item.rank}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-meta font-medium leading-snug" title={item.title}>
            {item.title}
          </p>

          {/* Impact and what of it is actually recoverable — the second
              number is the one that decides whether this is worth a
              morning, and it was not on screen at all. */}
          <p className="numeral mt-1 text-[0.86rem] font-medium leading-tight">
            <span className={cn(item.impactCents < 0 && "text-negative")}>
              {formatWholeDollars(item.impactCents)}
            </span>
            <span className="ml-1.5 text-micro font-normal text-muted-foreground">
              {item.impactLabel || "detected"}
            </span>
          </p>

          <ImpactAgreement item={item} />
          <RankedOnLabel item={item} />
          {recoverable !== undefined && (
            <p className="num mt-0.5 text-micro leading-snug text-muted-foreground">
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
            <p className="mt-0.5 text-micro leading-snug text-muted-foreground">
              <ActionabilityLabel item={item} />
            </p>
          )}

          {/* The detector's own grading and how stale it is. */}
          {(item.severity || item.ageDays !== undefined) && (
            <p className="mt-1 flex items-center gap-1.5 text-micro uppercase tracking-wide">
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
                  Detected {item.ageDays}d ago
                </span>
              )}
            </p>
          )}

          {/* WHEN this card's dollars hit cash. Published context beside
              the money, never a re-ranking: the list is ordered by
              `anomaly_priority@3` and nothing on this panel sorts on the
              timing lane. A projection wears "provisional" in words, so a
              32-day estimate never reads like the filing deadline two
              cards below it. */}
          {item.timeToImpact && (
            <TimeToImpactLine timeToImpact={item.timeToImpact} className="mt-0.5 flex" />
          )}

          {/* The detector writes its own sentence, and it writes dates the
              way a warehouse does ("since 2026-05"). Same repair the fact
              rows make: spell the date, touch nothing else. */}
          <p className="mt-1 line-clamp-2 text-micro leading-snug text-muted-foreground">
            {humanizeIsoDates(item.detail)}
          </p>

          {/* Where this lead stands with the humans working it, and the
              platform's own verdict when it has reached one. */}
          <LeadStatusControl
            anomalyId={item.referent}
            {...(item.leadStatus ? { cardStatus: item.leadStatus } : {})}
            {...(item.leadStatusNote ? { cardNote: item.leadStatusNote } : {})}
          />

          {/* What the number MEASURES, in the pack's governed words. A
              worklist has no answer to hang a §6.6 population caveat on,
              so the correction travels on the card — and it is published
              only for the ids that overclaim, which is why most cards
              show nothing here. */}
          {item.metricDisplayName && (
            <p className="mt-1 text-micro leading-snug text-muted-foreground">
              Measures <span className="font-medium">{item.metricDisplayName}</span>
            </p>
          )}

          {/* The card's CUT was substituted too, and for a different
              reason than its measure. Rendered separately for exactly that
              reason: one is "we probed another number", the other is "we
              cut it a different way, and the two count different things".
              See `DimensionRepointDisclosure`. */}
          {item.drillDimensionRepoints?.map((repoint) => (
            <DimensionRepointDisclosure
              key={`${repoint.fromDimension}->${repoint.toDimension}`}
              repoint={repoint}
            />
          ))}

          {/* The drill probes a different measure than the card reports,
              and the server wrote down why. Only said when the two names
              genuinely differ — `drill_spec.metric_ids[0]` is the measure
              that will actually be probed, NOT the card's `metric_id`. */}
          {item.drillRepointedFrom && item.drillMetricId !== item.drillRepointedFrom && (
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="mt-1 flex items-center gap-1 rounded text-left text-micro leading-snug text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground focus-ring"
                >
                  <Info className="size-2.5 shrink-0" />
                  {/* The measures in the analyst's spelling. `denied_dollars`
                      is the catalog's key, not a phrase anyone says out
                      loud, and the tooltip below keeps the server's own
                      sentence verbatim. */}
                  Drills {item.drillMetricId ? humanizeInline(item.drillMetricId) : "a different measure"}, not{" "}
                  {humanizeInline(item.drillRepointedFrom)}
                </button>
              </TooltipTrigger>
              <TooltipContent side="right" className="max-w-72 text-meta leading-snug">
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
                className="h-5 gap-0.5 rounded-full px-1.5 text-micro font-normal text-verified opacity-0 transition-opacity duration-150 hover:text-verified group-hover:opacity-100 focus-visible:opacity-100"
                onClick={onDrill}
              >
                {item.drill?.label ?? "Drill in"}
                <ArrowUpRight className="size-2.5" />
              </Button>
            ) : (
              <Tooltip>
                <TooltipTrigger asChild>
                  {/* `aria-disabled`, not `disabled`. A `disabled` button
                      is out of the focus path, so the refusal that was on
                      its `aria-label` was unreachable — and the wrapper
                      span that carried the focus had no role and no
                      accessible name, so a keyboard user landed on an
                      anonymous element beside a card they could not open.
                      The name and the refusal live on the focusable
                      element now; the click is inert either way because
                      there is no handler to fire. */}
                  <Button
                    variant="ghost"
                    size="xs"
                    aria-disabled
                    aria-label={`Cannot drill into ${item.title} — ${item.drillUnavailableReason ?? "the platform refused this drill"}`}
                    // No dimming. Inactive is carried by the Ban icon,
                    // the words, the cursor and `aria-disabled` — four
                    // signals, none of them a contrast reduction. The
                    // control's whole content is the platform's refusal,
                    // and dimming it to 2.88:1 (light, at 0.70) made the
                    // most-refused cards the least readable ones.
                    className="h-5 cursor-not-allowed gap-0.5 rounded-full px-1.5 text-micro font-normal text-muted-foreground hover:bg-transparent hover:text-muted-foreground"
                  >
                    <Ban className="size-2.5" />
                    Can&apos;t drill
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="left" className="max-w-72 text-meta leading-snug">
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
 * Four states, four different claims, none of them collapsed into
 * another: agreed (a quiet confirmation), diverged (both figures, the
 * gap, and the reason on hover), not_comparable (both figures, NO gap,
 * because the two are different kinds of measurement and the difference
 * is not attributable to either side), unavailable (the detector's figure
 * stands alone, and the card says that is what it is).
 */
function ImpactAgreement({ item }: { item: PortfolioItem }) {
  const agreement = item.impactAgreement;
  if (agreement === undefined) return null;

  if (agreement === "agreed") {
    return (
      <p className="num mt-0.5 flex items-center gap-1 text-micro leading-snug text-verified">
        <Check className="size-2.5 shrink-0" aria-hidden />
        Matches this platform&apos;s own figure
      </p>
    );
  }

  const note =
    item.impactReconciliationNote ||
    (agreement === "diverged"
      ? "This platform re-derived a different figure for the same cell from its governed contract."
      : agreement === "not_comparable"
        ? "This platform re-derived the cell, but the two figures are different kinds of measurement, so the gap between them is not a disagreement about the number."
        : "This platform could not re-derive this figure from its governed contracts.");

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className={cn(
            "num mt-0.5 flex items-center gap-1 rounded text-left text-micro leading-snug underline decoration-dotted underline-offset-2 focus-ring",
            agreement === "diverged" ? "text-warning" : "text-muted-foreground",
          )}
        >
          <GitCompareArrows className="size-2.5 shrink-0" aria-hidden />
          {/* `not_comparable` is NOT a divergence and must not wear its
              tone: the platform re-derived the cell and then said the two
              numbers measure different things (a snapshot balance against
              a windowed total). It publishes its own figure, so the card
              shows it — without a delta, because a difference between two
              kinds of measurement is not a percentage anyone should act
              on, and the payload leaves `impact_delta_fraction` null on
              exactly these cards. */}
          {(agreement === "diverged" || agreement === "not_comparable") &&
          item.reconciledImpactCents !== undefined ? (
            <>
              This platform: {formatWholeDollars(item.reconciledImpactCents)}
              {agreement === "diverged" && item.impactDeltaFraction !== undefined && (
                <span className="font-medium">
                  {" "}
                  ({formatSignedPct(item.impactDeltaFraction)})
                </span>
              )}
              {agreement === "not_comparable" && (
                <span className="font-normal"> — measured differently, not a disagreement</span>
              )}
            </>
          ) : (
            "Not re-derived here — the detector's figure alone"
          )}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-80 text-meta leading-snug">
        {note}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * WHICH of the two published figures put this card where it is.
 *
 * The rail shows two dollar amounts on a diverged card — the detector's
 * and this platform's — and until now said nothing about which one the
 * ORDERING used. Live that is not one answer across the list: 19 cards
 * ranked on the detector's figure, 9 on this platform's, and 5 on the
 * detector's precisely BECAUSE this platform's is not a comparable
 * quantity. A worklist ordered by a basis that varies card by card, read
 * as if it were uniform, allocates a Monday morning wrongly.
 *
 * Deliberately subtle — one line of small type under the agreement state,
 * not a badge. It qualifies the ordering; it is not a finding about the
 * card, and the position on the list is already the loud signal.
 *
 * Nothing is said on a card the server did not speak about, and nothing is
 * said when the ranking used the figure printed directly above it with no
 * disagreement in play (`detector` with an `agreed` or absent
 * reconciliation) — that is the default reading, and a line restating it
 * on 19 of 33 cards is noise that would bury the 14 that differ.
 */
function RankedOnLabel({ item }: { item: PortfolioItem }) {
  const rankedOn = item.rankedOn;
  if (rankedOn === undefined) return null;
  if (rankedOn === "detector" && item.impactAgreement !== "diverged") return null;

  const label =
    rankedOn === "platform"
      ? "Ranked on this platform's figure"
      : rankedOn === "not_comparable"
        ? "Ranked on the detector's figure — not comparable"
        : "Ranked on the detector's figure";
  // The server's own sentence, per card. It is the only thing that
  // separates "we used ours because they diverge" from "we used theirs
  // because ours measures something else", and neither is derivable here.
  const note =
    item.rankedOnNote ||
    (rankedOn === "platform"
      ? "This card was ordered by this platform's re-derived figure rather than the detection system's, because the two diverge."
      : rankedOn === "not_comparable"
        ? "This card was ordered by the detection system's figure: this platform's re-derivation is not a comparable quantity, so substituting it would change the claim rather than correct it."
        : "This card was ordered by the detection system's own figure.");

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="num mt-0.5 flex items-center gap-1 rounded text-left text-micro leading-snug text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground focus-ring"
        >
          <ArrowDownWideNarrow className="size-2.5 shrink-0" aria-hidden />
          {label}
          {item.rankedImpactCents !== undefined &&
            item.rankedImpactCents !== item.impactCents && (
              <span className="font-medium">
                {" "}
                ({formatWholeDollars(item.rankedImpactCents)})
              </span>
            )}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-80 text-meta leading-snug">
        {note}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * The detector's CUT was substituted, and the substitution changes what
 * gets counted.
 *
 * Not the same disclosure as the measure repoint above it. The detection
 * feed cuts procedures at `proc_group`, which binds on `claim_line`, so a
 * claim-grain contract has no legal procedure cut at all — four cards, the
 * largest on the worklist among them, refused outright with
 * GRAIN_INCOMPATIBLE. The catalog certifies `primary_proc_group` (the
 * claim's dominant procedure group) at the claim grain, and the drill is
 * pointed at that.
 *
 * The whole point is that this is a SUBSTITUTION rather than a
 * translation: the detector counted LINES in the group, the drill counts
 * CLAIMS whose largest procedure group is that one. Those are the same
 * population for a single-procedure claim and different populations for a
 * multi-procedure one — which is a legitimate reason for the card's figure
 * and the drill's to differ, and it has to be on screen BEFORE the click,
 * not discovered afterwards in a reconciliation strip.
 */
function DimensionRepointDisclosure({ repoint }: { repoint: DrillDimensionRepoint }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="mt-1 flex items-start gap-1 rounded text-left text-micro leading-snug text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground focus-ring"
        >
          <GitCompareArrows className="mt-px size-2.5 shrink-0" aria-hidden />
          <span>
            Cuts by {humanizeInline(repoint.toDimension)}, not{" "}
            {humanizeInline(repoint.fromDimension)}
          </span>
        </button>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-80 text-meta leading-snug">
        {/* The server's reasoning, verbatim. It is the sentence that
            explains the drill counts claims where the detector counted
            lines, and summarizing it here would drop exactly that. */}
        {repoint.rationale}
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
      <TooltipContent side="right" className="max-w-80 text-meta leading-snug">
        {item.actionabilityRationale}
      </TooltipContent>
    </Tooltip>
  );
}
