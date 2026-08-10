"use client";

import { AlertTriangle, ChevronDown } from "lucide-react";
import { useId, useState } from "react";

import {
  ThingsToKnowSheet,
  thingsToKnowLabel,
  thingsToKnowSeverity,
} from "@/components/answer/ThingsToKnow";
import {
  CashTimingSummary,
  LaneHeader,
  PortfolioCard,
} from "@/components/portfolio/PortfolioPanel";
import { Button } from "@/components/ui/button";
import type { PortfolioSnapshotData } from "@/lib/contract";
import { dataLoadDate, rankingVersionLabel } from "@/lib/format";
import { groupByLane } from "@/lib/portfolioLanes";
import { useAsk } from "@/lib/useAsk";
import { useSessionStore } from "@/lib/store";
import type { WarningEvent } from "@/lib/types";

/** How many cards each lane shows before "Show all". */
const COLLAPSED_COUNT = 4;

/**
 * What this worklist says about itself, as a count that opens.
 *
 * The same control the answer's integrity line uses, over the same sheet,
 * with the same severity clause — because it is the same question ("what
 * should I know before I read these numbers?") asked about a list instead
 * of about an answer.
 */
function SnapshotCaveats({ warnings }: { warnings: Omit<WarningEvent, "type">[] }) {
  const [open, setOpen] = useState(false);
  const caveats: WarningEvent[] = warnings.map((w) => ({ ...w, type: "warning" as const }));
  if (caveats.length === 0) return null;
  return (
    <>
      <p data-snapshot-caveats={caveats.length} className="text-micro text-muted-foreground">
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="focus-ring rounded underline decoration-foreground/40 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
        >
          {thingsToKnowLabel(caveats.length)} about this list
        </button>
        <span aria-hidden className="mx-1.5 text-muted-foreground/60">
          ·
        </span>
        <span className="sr-only">, </span>
        {thingsToKnowSeverity(caveats)}
      </p>
      <ThingsToKnowSheet warnings={caveats} open={open} onOpenChange={setOpen} />
    </>
  );
}

/**
 * DETECTED ANOMALIES — Home's default centre, and the whole page for a
 * tenant who has not pinned anything yet.
 *
 * This is the same worklist the rail carries, at the width it deserves: the
 * lanes the server split (must-do compliance work, then value ranked by
 * what is recoverable), the cash-timing split above them so "how much have
 * we not lost yet" is answered before anyone scrolls, and the same cards
 * with the same four honesty disclosures — what the detector claimed, what
 * this platform re-derived, which figure the ranking used, and whether the
 * card can be opened at all.
 *
 * IT REUSES THE RAIL'S CARD, and that is the point. A second card component
 * for the big surface is a second place for `impact_agreement`,
 * `ranked_on`, the recoverable estimate and the drill refusal to be
 * rendered — and the first one to fall behind would be the one nobody reads
 * in a rail. The rail hides its own copy of this list while Home is on
 * screen (see `SessionRail`), so the two are never both drawn.
 *
 * The panel does not re-rank, re-total or re-lane anything: `lanes` carries
 * its own membership and order, and `cash_timing_lanes` carries totals over
 * the whole population rather than over the page.
 */
export function DetectedAnomalies({
  query,
}: {
  query: { data: PortfolioSnapshotData | undefined; isPending: boolean; error: unknown };
}) {
  const emitRefinement = useSessionStore((s) => s.emitRefinement);
  const ask = useAsk();
  const [expanded, setExpanded] = useState(false);
  const listId = useId();

  const snapshot = query.data;
  const items = snapshot?.items ?? [];
  const groups = groupByLane(items, snapshot?.lanes ?? []);
  const shown = groups.reduce(
    (total, group) =>
      total + (expanded ? group.items.length : Math.min(group.items.length, COLLAPSED_COUNT)),
    0,
  );
  const hidden = items.length - shown;
  const loadDate = snapshot?.watermark ? dataLoadDate(snapshot.watermark) : undefined;

  return (
    <section
      id="home-anomalies"
      tabIndex={-1}
      aria-labelledby="home-anomalies-heading"
      className="space-y-3 outline-none"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id="home-anomalies-heading"
          className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
        >
          Detected anomalies
        </h2>
        <p className="num text-micro text-muted-foreground">
          {items.length > 0 && (
            <>
              {items.length} lead{items.length === 1 ? "" : "s"}
              {loadDate ? `, on the data as of ${loadDate}` : ""}
              {snapshot?.rankingPolicy && (
                <span title={snapshot.rankingPolicy}>
                  {" · "}
                  {rankingVersionLabel(snapshot.rankingPolicy)}
                </span>
              )}
            </>
          )}
        </p>
      </header>

      {snapshot ? (
        <>
          {/* HOW MUCH OF THIS IS STILL CATCHABLE — the one total a director
              asks for before they allocate a morning, and it is not the
              ranking. Same component the rail uses, so the dollars and the
              deadline rules cannot drift between the two. */}
          <div className="max-w-3xl rounded-lg border bg-surface-sunken/50 px-3 py-2.5">
            <CashTimingSummary lanes={snapshot.cashTimingLanes} />
          </div>

          {/* THE SNAPSHOT'S OWN CAVEATS, COUNTED RATHER THAN STACKED.
              This list publishes six of them and every one is real — five
              cards not comparable, ten not re-derivable, six diverging,
              a repointed cut, two ranking bases. Rendered as six
              full-width boxes they are the first thing on the opening
              shot, three hundred words deep, and a reader learns to scroll
              past the whole zone.

              Not hidden: the count is a control, the severity clause says
              why it is worth opening, and the sentences behind it are the
              server's verbatim in the sheet the answer surface already
              uses for exactly this. A caveat nobody can open is the thing
              this product refuses; a caveat nobody reads is the thing that
              happens when it is the loudest object on the page. */}
          <SnapshotCaveats warnings={snapshot.warnings} />

          {items.length === 0 ? (
            <p className="max-w-[64ch] text-body leading-relaxed text-muted-foreground">
              {snapshot.status === "empty"
                ? "Nothing was detected at this data load — the worklist is empty, not missing."
                : "Nothing flagged in this data load."}
            </p>
          ) : (
            <div id={listId} className="space-y-5">
              {groups.map((group) => (
                <section key={group.id} className="space-y-2">
                  {/* Only when the server actually split the list. */}
                  {groups.length > 1 && (
                    <LaneHeader {...(group.lane ? { lane: group.lane } : {})} count={group.items.length} />
                  )}
                  <ol className="grid gap-2 lg:grid-cols-2 2xl:grid-cols-3">
                    {(expanded ? group.items : group.items.slice(0, COLLAPSED_COUNT)).map(
                      (item) => (
                        <PortfolioCard
                          key={item.referent}
                          item={item}
                          // HOME'S PRIMARY ACTION IS NOT HOVER-REVEALED.
                          // This list IS the page for a tenant who has
                          // pinned nothing, opening a card is the whole
                          // reason it is on screen, and the rail's
                          // `opacity-0 group-hover:opacity-100` treatment
                          // makes that action non-existent on a touch
                          // screen, in a screenshot and on a projector.
                          // The rail keeps its tighter version — see
                          // `DrillAffordance`.
                          drillAffordance="persistent"
                          onDrill={() => {
                            if (item.drillSpec) {
                              // A card is not a refinement of whatever was
                              // last on screen: its handle is a typed FIRST
                              // turn, so it opens its own investigation.
                              ask({ spec: item.drillSpec, anomalyRef: item.referent });
                            } else if (item.drill) {
                              emitRefinement(item.drill.refinement, { referent: item.referent });
                            }
                          }}
                        />
                      ),
                    )}
                  </ol>
                </section>
              ))}

              {hidden > 0 && (
                <Button
                  variant="outline"
                  size="xs"
                  onClick={() => setExpanded(true)}
                  aria-expanded={false}
                  aria-controls={listId}
                  className="gap-1 text-meta font-normal"
                >
                  <ChevronDown className="size-3" />
                  Show the other {hidden}
                </Button>
              )}
              {expanded && items.length > COLLAPSED_COUNT && (
                <Button
                  variant="outline"
                  size="xs"
                  onClick={() => setExpanded(false)}
                  aria-expanded
                  aria-controls={listId}
                  className="gap-1 text-meta font-normal"
                >
                  Show the top {COLLAPSED_COUNT}
                  {groups.length > 1 ? " of each" : ""}
                </Button>
              )}
              <p className="num text-micro leading-snug text-muted-foreground">
                {loadDate
                  ? `Computed on the data as of ${loadDate} · opening one asks an ordinary question against that same data.`
                  : "Opening one asks an ordinary question against the same data this list was built on."}
              </p>
            </div>
          )}
        </>
      ) : query.isPending ? (
        <p role="status" aria-live="polite" className="text-body text-muted-foreground">
          Reading this load&apos;s worklist…
        </p>
      ) : (
        <p
          role="alert"
          className="flex max-w-[64ch] items-start gap-1.5 text-meta leading-snug text-negative"
        >
          <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span>
            Could not read this load&apos;s worklist.{" "}
            {query.error instanceof Error
              ? query.error.message
              : "The request did not complete."}{" "}
            Nothing here is out of date — there is nothing here.
          </span>
        </p>
      )}
    </section>
  );
}
