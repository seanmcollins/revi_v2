"use client";

import { ChevronRight } from "lucide-react";
import { useId, useState } from "react";

import type { Benchmark, MeasuredValue } from "@/lib/types";
import { cn } from "@/lib/utils";
import { humanizeInline } from "@/lib/humanize";

/**
 * The governed external ranges a finding was quoted against.
 *
 * Seven of these per finding reached the wire and were rendered NOWHERE:
 * `grep -rni benchmark apps/web/src` returned zero matches across ninety
 * files. The only path any of them took to a reader was inside a narrative
 * sentence — "Silverline's 11.6% sits below the low end of that range" —
 * which asserted the comparison with full confidence and carried neither
 * the `machine_researched` review status nor the cautions the entry ships
 * with, one of which says the two endpoints of the quoted band are
 * different periods measured on different bases.
 *
 * Four design commitments, each answering a specific way this could lie:
 *
 *   the RANGE is never shown without its COHORT. Live, a finding about a
 *     Medicaid MCO carries an ACA-marketplace plan-reported band; a
 *     provider-side A/R finding carries a commercial-payer share. Those
 *     are different populations and the difference is the whole story, so
 *     the cohort label sits on the same line as the number and is not
 *     abbreviated, truncated or moved into a tooltip.
 *   the REVIEW STATUS is a chip, not a footnote. Every entry shipped so
 *     far is `machine_researched` — gathered by an automated search and
 *     checked by no one — and a reader comparing their book against it
 *     deserves to know that before they quote it, not after.
 *   the comparison is stated only when the UNITS line up, in points, and
 *     as arithmetic rather than as a verdict: "9.5 points above the top of
 *     this range" is checkable from two numbers on screen. When the
 *     benchmark's unit and the finding's do not agree, the strip says the
 *     two are on different bases and compares nothing.
 *   the CAUTIONS are one keystroke away, never welded shut. They are the
 *     part that bounds the range, so the disclosure names how many there
 *     are rather than hiding the count too.
 *
 * Typography only — no bars, no gauges, no coloured tracks. A range and a
 * measurement are two numbers; drawing them makes the comparison look more
 * certain than the cohort mismatch underneath it warrants.
 */
export function BenchmarkStrip({
  benchmarks,
  measured,
  referent,
}: {
  benchmarks: readonly Benchmark[];
  measured?: MeasuredValue;
  /** The finding's handle (F1), so the comparison names what it compares. */
  referent: string;
}) {
  const [showAll, setShowAll] = useState(false);
  const listId = useId();
  if (benchmarks.length === 0) return null;

  const shown = showAll ? benchmarks : benchmarks.slice(0, VISIBLE_BY_DEFAULT);
  const hidden = benchmarks.length - shown.length;
  // One chip for the whole strip when every entry carries the same status,
  // which live is always: seven unreviewed ranges do not need seven chips.
  const uniformStatus =
    benchmarks.every((b) => b.reviewStatus === benchmarks[0].reviewStatus)
      ? benchmarks[0].reviewStatus
      : undefined;

  return (
    <section className="border-t pt-2" aria-label="External benchmark ranges">
      <p className="flex flex-wrap items-center gap-x-1.5 gap-y-1">
        <span className="text-micro font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          External {benchmarks.length === 1 ? "range" : "ranges"}
        </span>
        {benchmarks.length > 1 && (
          <span className="num text-micro text-muted-foreground">{benchmarks.length}</span>
        )}
        {uniformStatus && <ReviewChip status={uniformStatus} />}
      </p>

      <ul id={listId} className="mt-1 space-y-2">
        {shown.map((benchmark) => (
          <BenchmarkRow
            key={benchmark.id || `${benchmark.cohortLabel}:${benchmark.valueLow}`}
            benchmark={benchmark}
            measured={measured}
            referent={referent}
            showStatus={uniformStatus === undefined}
          />
        ))}
      </ul>

      {(hidden > 0 || showAll) && (
        <button
          type="button"
          onClick={() => setShowAll((s) => !s)}
          aria-expanded={showAll}
          aria-controls={listId}
          className="focus-ring mt-1.5 inline-flex items-center gap-1 rounded text-micro text-muted-foreground hover:text-foreground"
        >
          <ChevronRight className={cn("size-2.5 transition-transform", showAll && "rotate-90")} />
          {showAll
            ? `Show fewer ranges`
            : `${hidden} more ${hidden === 1 ? "range" : "ranges"} for this measure`}
        </button>
      )}
    </section>
  );
}

/** How many ranges a card shows before the reader asks for the rest. */
const VISIBLE_BY_DEFAULT = 2;

function BenchmarkRow({
  benchmark,
  measured,
  referent,
  showStatus,
}: {
  benchmark: Benchmark;
  measured?: MeasuredValue;
  referent: string;
  showStatus: boolean;
}) {
  const [openCautions, setOpenCautions] = useState(false);
  const cautionsId = useId();
  const notes = [...benchmark.cautions, ...benchmark.sources.map((s) => `Source: ${s}`)];
  const comparison = compareToRange(benchmark, measured);

  return (
    <li className="space-y-0.5">
      <p className="flex flex-wrap items-baseline gap-x-1.5">
        <span className="num text-body font-medium leading-none">
          {rangeText(benchmark)}
        </span>
        {benchmark.unit && (
          <span className="text-micro text-muted-foreground">{benchmark.unit}</span>
        )}
        {showStatus && <ReviewChip status={benchmark.reviewStatus} />}
      </p>

      {/* The population, the period and who published it — on screen, at
          the same weight, because the range means nothing without them. */}
      <p className="text-micro leading-snug text-muted-foreground">
        {[benchmark.cohortLabel, benchmark.period, benchmark.authority]
          .filter((part) => part !== "")
          .join(" · ")}
      </p>

      {comparison && (
        <p className="text-meta leading-snug">
          <span className="font-medium">{referent}</span>
          <span className="text-muted-foreground">{comparison}</span>
        </p>
      )}

      {notes.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setOpenCautions((o) => !o)}
            aria-expanded={openCautions}
            aria-controls={cautionsId}
            className="focus-ring inline-flex items-center gap-1 rounded text-micro text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground"
          >
            <ChevronRight
              className={cn("size-2.5 transition-transform", openCautions && "rotate-90")}
            />
            {benchmark.cautions.length > 0
              ? `${benchmark.cautions.length} ${benchmark.cautions.length === 1 ? "caution" : "cautions"} on this range`
              : "Where this range comes from"}
          </button>
          {openCautions && (
            <ul
              id={cautionsId}
              className="mt-1 space-y-0.5 border-l pl-2 text-micro leading-snug text-muted-foreground"
            >
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </li>
  );
}

/**
 * `machine_researched` is not a grade and deliberately does not borrow the
 * GradeBadge vocabulary: an evidence grade certifies how THIS platform
 * computed a number from certified semantics, and this is an external
 * figure nobody here has checked. It says so in a word a reader does not
 * have to decode.
 *
 * A MARK, NOT A WARNING. The chip is where the reader's eye already is —
 * on the range itself — and a dashed-outline chip in neutral ink says
 * "nobody checked this" without saying "something is wrong here". The
 * amber it used to wear made an ordinary reference band look like a
 * finding against the analyst's own number, which is the opposite of what
 * an external range is for. The full sentence is on the chip and the
 * cautions are one tap below it, both unchanged.
 */
function ReviewChip({ status }: { status: string }) {
  if (status === "") return null;
  const unreviewed = status === "machine_researched";
  return (
    <span
      className={cn(
        "inline-flex h-[1.15rem] items-center rounded-full border px-1.5 text-micro font-medium uppercase tracking-wide text-muted-foreground",
        unreviewed ? "border-dashed border-muted-foreground/50" : "border-border",
      )}
      title={
        unreviewed
          ? "Machine-researched: gathered by an automated search of public sources and not reviewed by a person. Treat it as a reference point, not as a target anybody signed off on."
          : `How this range was reviewed: ${humanizeInline(status)}.`
      }
    >
      {unreviewed ? "Unreviewed" : humanizeInline(status)}
    </span>
  );
}

/** "19–20", or a single figure when the source published one. */
function rangeText(benchmark: Benchmark): string {
  if (benchmark.valueHigh === "" || benchmark.valueLow === benchmark.valueHigh) {
    return benchmark.valueLow || benchmark.valueHigh;
  }
  if (benchmark.valueLow === "") return benchmark.valueHigh;
  return `${benchmark.valueLow}–${benchmark.valueHigh}`;
}

/**
 * The finding's own figure against the range, in points — or nothing.
 *
 * Only attempted when both sides are percentages: the finding's display
 * unit says `percent`, and the benchmark's free-text unit says so too
 * ("percent of in-network claims denied", "percent of A/R aged over 90
 * days"). Anything else — a dollar total beside a rate, a unit string this
 * build has not seen — returns a sentence saying the two are on different
 * bases, because a silent absence reads as "no comparison was needed"
 * rather than "no comparison is valid".
 *
 * The wording is arithmetic, never a verdict. "9.5 points above the top of
 * this range" is checkable from the two numbers beside it; "worse than its
 * peers" would be this client asserting the cohorts are comparable, which
 * is exactly the claim the cohort label above it disputes.
 */
export function compareToRange(
  benchmark: Benchmark,
  measured: MeasuredValue | undefined,
): string | undefined {
  if (measured === undefined) return undefined;
  // `Number("")` is 0, and a one-ended range read as `0..low` would put a
  // bound on screen the source never published.
  const usable = [benchmark.valueLow, benchmark.valueHigh]
    .filter((raw) => raw.trim() !== "")
    .map((raw) => Number(raw))
    .filter((n) => Number.isFinite(n));
  if (usable.length === 0) return undefined;

  const unitText = benchmark.unit.trim().toLowerCase();
  const bothPercent =
    measured.unit === "percent" && (unitText.startsWith("percent") || unitText.startsWith("%"));
  if (!bothPercent) {
    return `'s figure is stated on a different basis from this range — not compared here`;
  }

  const bottom = Math.min(...usable);
  const top = Math.max(...usable);
  const value = measured.value;
  const points = (n: number): string => `${Math.abs(n).toFixed(1)} ${Math.abs(n) === 1 ? "point" : "points"}`;

  // A CEILING cannot be placed against a range. The strip used to print
  // "F4's 76.9% is 56.9 points above the top of this range" over a
  // suppression bound taken across thirteen claims — a figure equally
  // consistent with 0%, stated as a 56.9-point overshoot of a peer band.
  //
  // Exactly one comparison survives the arithmetic: a ceiling BELOW the
  // bottom of the range proves the true value is below it too. Everything
  // else is unknown, and the sentence says unknown rather than nothing —
  // a silent absence reads as "no comparison was needed".
  if (measured.isBound === true) {
    const over =
      measured.boundPopulation !== undefined
        ? `, taken over a population of ${measured.boundPopulation}`
        : "";
    if (value < bottom) {
      return `'s ceiling of ≤ ${value.toFixed(1)}%${over} is below the bottom of this range — the measured value is lower still`;
    }
    return `'s figure is an upper bound of ≤ ${value.toFixed(1)}%${over}, so it cannot be placed against this range — the measured value is somewhere at or below it`;
  }

  if (value > top) return `'s ${value.toFixed(1)}% is ${points(value - top)} above the top of this range`;
  if (value < bottom) {
    return `'s ${value.toFixed(1)}% is ${points(bottom - value)} below the bottom of this range`;
  }
  return `'s ${value.toFixed(1)}% falls inside this range`;
}
