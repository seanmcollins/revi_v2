"use client";

import { ChevronRight } from "lucide-react";

import { GradeDot } from "@/components/answer/GradeBadge";
import { ReferentChip } from "@/components/answer/ReferentChip";
import { WatchThis } from "@/components/rounds/WatchThis";
import { Button } from "@/components/ui/button";
import {
  formatCents,
  formatCount,
  formatSignedCents,
  humanizeIsoDates,
} from "@/lib/format";
import {
  echoesVerdict,
  statementBeyondTitle,
  titleCarriesValue,
  titleLabelOnly,
} from "@/lib/findingText";
import { useSessionStore } from "@/lib/store";
import type { Finding } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * One fact, as a row.
 *
 * The hero card is right for the ONE number an answer leads with and
 * wrong for the next seven: eight cards of display-size money, each
 * restating its own title, is three screens of the same sentence in
 * different sizes. A row states the same facts in the same order —
 * referent, what it is, what it measures, how good the evidence is —
 * in a line a reader can scan.
 *
 * Nothing is dropped on the way down from the card. A CEILING still says
 * it is a ceiling, with its population, because a bound that renders as a
 * plain number is the single worst thing this surface can do; a movement
 * between two ceilings still says it is not measurable; a withheld impact
 * still says the engine withheld it; the grade still travels with the
 * number it grades. What a row does not carry is the mini-bars and the
 * drill chips — one drill affordance stands in for them, and the card is
 * still what a referent chip opens.
 */
export function FactRow({
  finding,
  turnId,
  /** In the Evidence rail the row is a target, not a link to itself. */
  anchorPrefix = "referent",
  verdictBodies,
}: {
  finding: Finding;
  turnId: string;
  anchorPrefix?: string;
  /**
   * The verdict sentences this same screen already renders in full.
   *
   * A premise turn publishes its verdict twice — as `PREMISE_PARTIAL` and
   * as F1, whose statement is that sentence — so a layout that leads with
   * the verdict and then lists the facts prints one 180-character clause
   * twice, three hundred pixels apart. The row stays (it is what a
   * citation points at and what the count counts); what it stops doing is
   * saying the sentence a second time. Absent in the rail, where the
   * verdict is not on screen and the full statement is the whole point.
   */
  verdictBodies?: readonly string[];
}) {
  const emitRefinement = useSessionStore((s) => s.emitRefinement);
  const focused = useSessionStore((s) => s.focusedReferent === finding.referent.value);
  /**
   * The server id of the turn this fact belongs to — what a watch is
   * registered against.
   *
   * Read from the store rather than threaded through `FactList`, which is
   * mounted by three layouts and the Evidence rail: the id is a property
   * of the turn, every one of those callers already names the turn, and a
   * fourth prop on all of them would be the same lookup written four
   * times. Absent while a turn is still streaming, and the affordance
   * simply is not there yet — a watch registered against a turn with no id
   * is a watch over nothing.
   */
  const investigationId = useSessionStore(
    (s) => s.turns.find((t) => t.id === turnId)?.answer.investigationId,
  );

  const bound = finding.measured?.isBound === true ? finding.measured : undefined;
  const movement = finding.boundedMovement;
  const drill = finding.suggestedRefinements[0];
  const echo =
    verdictBodies !== undefined && verdictBodies.length > 0
      ? echoesVerdict(finding, verdictBodies)
      : false;

  // The engine builds the figure into most titles. Printing it again in a
  // value column is the same number twice, one line to the right.
  const figure =
    finding.impactDisplay ??
    (finding.impactCents !== undefined
      ? finding.impactKind === "delta"
        ? formatSignedCents(finding.impactCents)
        : formatCents(finding.impactCents)
      : undefined);
  const showFigure = !titleCarriesValue(finding.title, figure);
  // The window as a reader says it. "over 2026-07-01..2026-07-31" inside
  // "Atlas Commercial ranks #1 of 12 measured by denied dollars" is a
  // machine literal in the middle of the sentence a VP reads aloud — the
  // same class of leak as a snake_case column id. The engine's literal
  // survives in the exports and the decision trace.
  // On an echoed verdict the TITLE is most of the duplicate: the engine
  // writes the whole clause into it behind a short heading. The heading
  // is what the row keeps.
  const title = humanizeIsoDates(
    (echo ? titleLabelOnly(finding.title) : undefined) ?? finding.title,
  );
  const detail = echo
    ? undefined
    : humanizeIsoDates(statementBeyondTitle(finding.title, finding.statement));

  return (
    <li
      id={`${anchorPrefix}-${finding.referent.value}`}
      data-referent={finding.referent.value}
      // A jump target, for a keyboard as well as a viewport. A referent
      // chip in the writing moves focus here; without this the element
      // cannot take it, and the jump is a scroll a screen reader never
      // hears about. See `ReferentChip`.
      tabIndex={-1}
      className={cn(
        "group focus-ring scroll-mt-4 rounded-md border border-transparent px-2 py-2 transition-colors duration-150",
        focused && "border-ring/60 bg-accent/60",
      )}
    >
      <div className="flex items-baseline gap-2">
        <ReferentChip value={finding.referent.value} className="mt-px shrink-0" />
        <p className="min-w-0 flex-1 text-meta font-medium leading-snug">
          {/* A ceiling wears its "≤" wherever it renders. The engine
              builds it into the title on the surfaces that publish one;
              the mark below states what it is a ceiling OVER, which is
              the half a number alone cannot carry. */}
          {title}
        </p>
        {showFigure && figure && (
          <span className="num shrink-0 text-meta font-medium tabular-nums">
            {bound ? "≤ " : ""}
            {figure}
          </span>
        )}
        <GradeDot grade={finding.grade} />
      </div>

      {bound && (
        <p className="mt-0.5 pl-8 text-micro text-warning">
          upper bound
          {bound.boundPopulation !== undefined
            ? ` over a population of ${formatCount(bound.boundPopulation)}`
            : ""}
          <span className="ml-1 text-muted-foreground">— a ceiling, not a measurement</span>
        </p>
      )}

      {movement && (
        <p className="mt-0.5 pl-8 text-micro text-warning">
          Movement not measurable
          <span className="ml-1 text-muted-foreground">
            — a difference between ceilings is not a measured change
          </span>
        </p>
      )}

      {finding.impactWithheldReason && figure === undefined && (
        <p className="mt-0.5 pl-8 text-micro text-muted-foreground">
          No impact figure — {finding.impactWithheldReason}
        </p>
      )}

      {finding.metricDisplay?.caveat && (
        <p className="mt-0.5 pl-8 text-micro text-muted-foreground">
          {finding.metricDisplay.caveat}
        </p>
      )}

      {detail && (
        <p className="mt-0.5 pl-8 text-micro leading-snug text-muted-foreground">{detail}</p>
      )}

      {/* THE SAME SENTENCE, ONCE. This fact IS the verdict this answer
          opens with, so the row says where it already is rather than
          printing the clause again under it. */}
      {echo && (
        <p className="mt-0.5 pl-8 text-micro leading-snug text-muted-foreground">
          Stated in full as the verdict at the top of this answer.
        </p>
      )}

      {/* The row's two forward gestures, on one line: go deeper into this
          fact, or ask to be told when it changes. Both are quiet and both
          appear on hover and on keyboard focus — a fact row is a scanning
          surface, and a permanently visible pair of controls on every one
          of thirty rows is the density this layout exists to remove. */}
      {(drill || investigationId) && (
        <div className="mt-1 flex flex-wrap items-center gap-1 pl-8">
          {drill && (
            <Button
              variant="ghost"
              size="xs"
              className="h-5 gap-0.5 rounded px-1.5 text-micro font-normal text-muted-foreground hover:text-foreground"
              onClick={() =>
                emitRefinement(drill.refinement, { turnId, referent: finding.referent.value })
              }
            >
              {drill.label}
              <ChevronRight aria-hidden className="size-3" />
            </Button>
          )}
          {investigationId && (
            <WatchThis
              // Keyed by the artifact, not by the turn: the same finding
              // reached from the answer and from the Evidence rail is one
              // thing to watch, and watching it twice would put two tiles
              // over one measure.
              artifactKey={`${investigationId}:${finding.referent.value}`}
              investigationId={investigationId}
              referent={finding.referent.value}
              presentation="finding"
              label={finding.title}
            />
          )}
        </div>
      )}
    </li>
  );
}

/**
 * A list of facts, with the ceilings kept out of the order.
 *
 * The same partition the cards use, and for the same reason: a ceiling
 * has no position in an order it was never measured for, so it is not
 * seated among the measurements as though it had one.
 */
export function FactList({
  measured,
  bounded,
  turnId,
  anchorPrefix,
  totalFindings,
  verdictBodies,
}: {
  measured: readonly Finding[];
  bounded: readonly Finding[];
  turnId: string;
  anchorPrefix?: string;
  totalFindings: number;
  /** The verdicts printed on this same surface — see `FactRow`. */
  verdictBodies?: readonly string[];
}) {
  const echo = verdictBodies ? { verdictBodies } : {};
  return (
    <>
      {measured.length > 0 && (
        <ul className="m-0 list-none divide-y divide-border/60 p-0">
          {measured.map((finding) => (
            <FactRow
              key={finding.referent.value}
              finding={finding}
              turnId={turnId}
              {...(anchorPrefix ? { anchorPrefix } : {})}
              {...echo}
            />
          ))}
        </ul>
      )}

      {bounded.length > 0 && (
        <section aria-label="Upper bounds" className="mt-2.5">
          <p className="mb-1 text-micro leading-snug text-muted-foreground">
            <span className="font-medium text-foreground">
              Upper bounds — not ranked
              {measured.length > 0 ? ` (${bounded.length} of ${totalFindings})` : ""}
            </span>
            <span className="ml-1">
              — the numerator was suppressed on {bounded.length === 1 ? "this cell" : "these cells"}{" "}
              and a ceiling over the publishable population was published instead. A ceiling has no
              position in an order it was never measured for.
            </span>
          </p>
          <ul className="m-0 list-none divide-y divide-border/60 p-0">
            {bounded.map((finding) => (
              <FactRow
                key={finding.referent.value}
                finding={finding}
                turnId={turnId}
                {...(anchorPrefix ? { anchorPrefix } : {})}
                {...echo}
              />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
