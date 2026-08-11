"use client";

import { ArrowUpRight, Ban, GitCompareArrows, ListOrdered } from "lucide-react";

import { MonitorThis } from "@/components/monitors/MonitorThis";
import { RunDeepResearchButton } from "@/components/research/ResearchOffer";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { WorklistData } from "@/lib/contract";
import { dataLoadDate, formatWholeDollars, rankingVersionLabel } from "@/lib/format";
import { humanizeColumn } from "@/lib/humanize";
import type { PortfolioItem } from "@/lib/mock/portfolio";
import { humanizeLoadHandles } from "@/lib/prose";
import { useSessionStore } from "@/lib/store";
import type { WarningEvent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { warningBody } from "@/lib/warnings";

/**
 * The ranked worklist, rendered inside a conversation.
 *
 * This is the governed conversation→worklist bridge on screen. The
 * platform computed a prioritised, reconciled worklist and the
 * conversation could not reach it: "what should my denial team work first
 * this week to recover the most cash?" returned a clarification offering
 * four ranking bases, none of which was the 33-card list with its lanes,
 * recoverable estimates and reconciliation state. Two products in one
 * shell. The turn carries the list now, and this draws it.
 *
 * Three disciplines it inherits from the rail, because the cards ARE the
 * rail's cards — same build, same `anomaly_priority` version, same
 * decomposition, same reconciliation state, parsed by the same mapper:
 *
 *   the cards are the DETECTION FEED's ranked work, not findings this
 *     turn computed. That is what the WORKLIST_ATTACHED warning says, and
 *     it is rendered as this block's intro line rather than left to sink
 *     into the turn's general warning list, where a reader would meet the
 *     cards first and the disclaimer second;
 *   a card that cannot be opened says so, in the platform's own words,
 *     instead of wearing a live button over a refusal;
 *   the figure that RANKED a card is named when it is not the one printed
 *     beside it — 9 of 33 live cards are ordered on this platform's
 *     re-derivation rather than the detector's, and 5 on the detector's
 *     precisely because ours is not a comparable quantity.
 *
 * `items` is a PAGE (live: 8 of 33) while `lanes` carry membership of the
 * whole population, so the lane chips count against the page they can
 * actually show and the footer states the page honestly.
 */
export function AnswerWorklist({
  worklist,
  intro,
  investigationId,
}: {
  worklist: WorklistData;
  /**
   * The WORKLIST_ATTACHED warning, lifted off the turn so it can be this
   * block's opening sentence. Absent when the server sent none — the
   * heading then stands alone rather than inventing a disclaimer.
   */
  intro?: Omit<WarningEvent, "type">;
  /**
   * The turn this list arrived on — what a monitor over it is registered
   * against. Absent while the turn is still streaming, and the affordance
   * is simply not there yet.
   */
  investigationId?: string;
}) {
  const submit = useSessionStore((s) => s.submit);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);

  const shown = worklist.items.length;
  if (shown === 0 && worklist.statement === "") return null;

  // Only the lanes this page can actually show a card from. A chip for a
  // lane with nothing on screen would filter to an empty list and read as
  // a bug; the lane totals still describe the whole population and are
  // stated on the chip's tooltip.
  const laneIds = new Set(worklist.items.map((item) => item.lane).filter(Boolean));
  const lanes = worklist.lanes.filter((lane) => laneIds.has(lane.id as "compliance" | "value"));
  const loadDate = dataLoadDate(worklist.watermarkId);

  return (
    <section className="rounded-lg border bg-card/60 p-3">
      <header className="flex items-baseline justify-between gap-2">
        <h3 className="flex items-center gap-1.5 text-body font-medium">
          <ListOrdered className="size-3.5 text-muted-foreground" aria-hidden />
          {worklist.label || "What to work first"}
        </h3>
        <span className="flex shrink-0 items-baseline gap-1.5">
          {/* MONITOR THIS, at the list's own pin point. A worklist is the
              artifact on this page that changes most between loads —
              cards arrive, cards leave, figures move — so a monitor over it
              is a monitor over the whole ranked population rather than over
              one number in it. */}
          {investigationId && (
            <MonitorThis
              artifactKey={`${investigationId}:worklist`}
              investigationId={investigationId}
              presentation="worklist_slice"
              label={worklist.label || "this worklist"}
              size="row"
            />
          )}
          {/* Which version of the ranking ordered these cards. A real
              fact — two lists ranked by different versions are not
              comparable — spelled as a version rather than as the
              payload's `anomaly_priority@3`, which stays on the title. */}
          {worklist.formulaVersion && (
            <span
              className="num text-micro text-muted-foreground"
              title={worklist.formulaVersion}
            >
              {rankingVersionLabel(worklist.formulaVersion)}
            </span>
          )}
        </span>
      </header>

      {/* The server's own sentence about what these cards are, first —
          before the cards. A reader who meets eight ranked dollar figures
          and finds the "these are not this turn's findings" note further
          down has already read them as findings. */}
      {intro && (
        <p className="mt-1.5 rounded-md border border-dashed bg-surface-sunken/60 px-2.5 py-1.5 text-meta leading-snug text-muted-foreground">
          {warningBody(intro.code, intro.message)}
        </p>
      )}

      {/* The platform's prose summary of the ranked population — its own
          words, never re-derived here. It carries the totals, the lane
          split and the ranking basis in one sentence. */}
      {worklist.statement && (
        /* The sentence is the server's and is not re-derived — but it
           names the data load by its internal handle ("at watermark
           wm_003"), which is a database id in front of an analyst. The
           phrase is humanized; the engine's exact sentence stays on the
           element, one hover away, and every export reads the raw field. */
        <p
          className="mt-1.5 text-meta leading-snug text-muted-foreground"
          title={worklist.statement}
        >
          {humanizeLoadHandles(worklist.statement)}
        </p>
      )}

      {/* The list's OWN caveats — the reconciliation and ranking-basis
          facts about the population, distinct from the turn's warnings. */}
      {worklist.warnings.length > 0 && (
        <ul className="mt-1.5 space-y-1">
          {worklist.warnings.map((warning) => (
            <li
              key={`${warning.code}:${warning.message}`}
              className={cn(
                "border-l-2 pl-2 text-micro leading-snug",
                warning.severity === "caution"
                  ? "border-l-warning/60 text-foreground/80"
                  : "border-l-border text-muted-foreground",
              )}
            >
              {warningBody(warning.code, warning.message)}
            </li>
          ))}
        </ul>
      )}

      {lanes.length > 1 && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {/* A lane chip is a typed re-query of the LIST, not a refinement
              of the answer above it: `TurnRequest.worklist` is additive by
              contract — the turn runs exactly as it would have and the
              worklist rides alongside. No natural language is composed;
              the lane id goes out as a typed field. */}
          {lanes.map((lane) => (
            <Tooltip key={lane.id}>
              <TooltipTrigger asChild>
                <Button
                  variant="outline"
                  size="xs"
                  disabled={streaming}
                  className="h-5 rounded-full px-2 text-micro font-normal"
                  onClick={() =>
                    void submit({
                      worklist: { lane: lane.id, limit: worklist.limit || shown },
                    })
                  }
                >
                  {lane.label}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-80 text-meta leading-snug">
                <p className="mb-1 font-medium">
                  {lane.itemCount} card{lane.itemCount === 1 ? "" : "s"} ·{" "}
                  {formatWholeDollars(lane.impactCents)}
                </p>
                {lane.description}
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
      )}

      <ol className="mt-2 space-y-1">
        {worklist.items.map((item) => (
          <WorklistRow key={item.referent} item={item} />
        ))}
      </ol>

      {/* What this page is a page OF. `total_items` is the whole ranked
          population and `items` is a slice of it, and a block that showed
          eight rows over a list of 33 without saying so would read as the
          whole worklist. */}
      {/* The data load is named by its DATE when the payload carries one.
          `wm_003` is a log token, and a token inside a sentence a director
          reads is the same leak as a snake_case column id — it stays on
          the title, where the person reproducing the query can find it. */}
      <p
        className="num mt-2 text-micro leading-snug text-muted-foreground"
        // The load's HANDLE is a version pin (§3) and reached the hover
        // verbatim. There is nothing for a reader to do with `wm_003`;
        // where the newest data date is published the surfaces say that
        // instead, and this hover now says only that it is one load.
        title={worklist.watermarkId ? "Measured at a single data load" : undefined}
      >
        {shown} of {worklist.totalItems} ranked card
        {worklist.totalItems === 1 ? "" : "s"}
        {loadDate !== undefined && ` at the ${loadDate} data load`}
        {worklist.totalRecoverableCentsEstimate > 0 && (
          <>
            {" · "}
            {formatWholeDollars(worklist.totalRecoverableCentsEstimate)} estimated recoverable
            across the whole list
          </>
        )}
      </p>
    </section>
  );
}

/**
 * One compact card row, deliberately the rail's idiom in one line: rank,
 * title, the ranked figure, what of it is recoverable, and the drill.
 *
 * The rail's card is three-deep because it is the only place those facts
 * appear; here they sit inside an answer, so the row keeps the four that
 * change a staffing decision and puts the reasoning on hover. What it does
 * NOT do is drop the honesty: a card the platform refuses to open still
 * refuses in the platform's own words, and a card ranked on a figure other
 * than the one printed beside it still says which.
 */
function WorklistRow({ item }: { item: PortfolioItem }) {
  const submit = useSessionStore((s) => s.submit);
  const emitRefinement = useSessionStore((s) => s.emitRefinement);
  const canDrill = item.drillable && (item.drillSpec !== undefined || item.drill !== undefined);
  // The figure that ORDERED this card, which is not always the one the
  // detector published. Falls back to the detector's when the server said
  // nothing, because that is what `impact_cents` is.
  const figure = item.rankedImpactCents ?? item.impactCents;
  const recoverable =
    item.recoverableCentsEstimate !== undefined && item.recoverableCentsEstimate !== figure
      ? item.recoverableCentsEstimate
      : undefined;

  return (
    <li
      className={cn(
        "group/row flex items-start gap-2 rounded-md border bg-card px-2 py-1.5",
        !item.drillable && "border-dashed",
      )}
    >
      <span className="num mt-px w-3.5 shrink-0 text-micro font-medium text-muted-foreground">
        {item.rank}
      </span>
      <div className="min-w-0 flex-1">
        <p className="truncate text-meta font-medium leading-snug" title={item.title}>
          {item.title}
        </p>
        <p className="num mt-0.5 flex flex-wrap items-baseline gap-x-1.5 text-micro leading-snug text-muted-foreground">
          <span className={cn("font-medium text-foreground", figure < 0 && "text-negative")}>
            {formatWholeDollars(figure)}
          </span>
          {recoverable !== undefined && <span>~{formatWholeDollars(recoverable)} recoverable</span>}
          {/* The lane in words. `compliance` is the payload's key for it. */}
          {item.lane && <span className="opacity-70">{humanizeColumn(item.lane)}</span>}
        </p>
        <RowBasis item={item} />
      </div>
      {/* The recoverability run this row can launch, when the platform
          offered one. Beside the drill, persistent, and the same control
          the rail's card carries — a lead is a lead wherever it is read. */}
      {item.deepResearch && <RunDeepResearchButton offer={item.deepResearch} />}
      {canDrill ? (
        <Button
          variant="ghost"
          size="xs"
          aria-label={`Drill into ${item.title}`}
          className="h-5 shrink-0 gap-0.5 rounded-full px-1.5 text-micro font-normal text-verified opacity-0 transition-opacity duration-150 hover:text-verified focus-visible:opacity-100 group-hover/row:opacity-100"
          onClick={() => {
            if (item.drillSpec) {
              // A card is a typed FIRST turn, exactly as it is from the
              // rail: it opens its own investigation rather than refining
              // whatever this answer was about. The card's id rides along
              // so the answer can reconcile its figure against this one.
              void submit({ spec: item.drillSpec, anomalyRef: item.referent });
            } else if (item.drill) {
              emitRefinement(item.drill.refinement, { referent: item.referent });
            }
          }}
        >
          Drill in
          <ArrowUpRight className="size-2.5" />
        </Button>
      ) : (
        <Tooltip>
          <TooltipTrigger asChild>
            {/* `aria-disabled`, not `disabled`: the platform's refusal is
                the control's whole content, and a `disabled` button is out
                of the focus path, which puts that refusal out of reach of
                a keyboard. The click is inert either way — there is no
                handler to fire. */}
            <Button
              variant="ghost"
              size="xs"
              aria-disabled
              aria-label={`Cannot drill into ${item.title} — ${item.drillUnavailableReason ?? "the platform refused this drill"}`}
              className="h-5 shrink-0 cursor-not-allowed gap-0.5 rounded-full px-1.5 text-micro font-normal text-muted-foreground hover:bg-transparent hover:text-muted-foreground"
            >
              <Ban className="size-2.5" />
              Can&apos;t drill
            </Button>
          </TooltipTrigger>
          <TooltipContent side="left" className="max-w-72 text-meta leading-snug">
            {item.drillUnavailableReason ??
              "This deployment cannot open this card as an investigation."}
          </TooltipContent>
        </Tooltip>
      )}
    </li>
  );
}

/**
 * The one line of qualification a compact row keeps: which figure ranked
 * this card, and whether this platform's own arithmetic agrees with it.
 *
 * Said only when it changes the reading. A card ranked on the detector's
 * figure with an agreed (or absent) reconciliation is the default case and
 * gets nothing — a line restating it on the majority of rows would bury
 * the ones where the basis actually differs.
 */
function RowBasis({ item }: { item: PortfolioItem }) {
  const rankedOn = item.rankedOn;
  const agreement = item.impactAgreement;
  const interesting =
    rankedOn === "platform" ||
    rankedOn === "not_comparable" ||
    agreement === "diverged" ||
    agreement === "unavailable" ||
    agreement === "not_comparable";
  if (!interesting) return null;

  const label =
    rankedOn === "platform"
      ? "Ranked on this platform's figure"
      : rankedOn === "not_comparable"
        ? "Ranked on the detector's figure — not comparable"
        : agreement === "diverged"
          ? "This platform re-derived a different figure"
          : agreement === "not_comparable"
            ? "Measured differently, not a disagreement"
            : "Not re-derived here — the detector's figure alone";

  // The server's sentence, whichever of the two is the reason. Never
  // composed here: "we used ours because they diverge" and "we used theirs
  // because ours measures something else" are different claims, and only
  // the server knows which one this card is.
  const note = item.rankedOnNote || item.impactReconciliationNote || label;

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
          {label}
        </button>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-80 text-meta leading-snug">
        {note}
      </TooltipContent>
    </Tooltip>
  );
}
