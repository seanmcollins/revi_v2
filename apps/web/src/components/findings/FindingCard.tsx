"use client";

import { GradeBadge } from "@/components/answer/GradeBadge";
import { ReferentChip } from "@/components/answer/ReferentChip";
import { BenchmarkStrip } from "@/components/findings/BenchmarkStrip";
import { Button } from "@/components/ui/button";
import {
  deltaTone,
  formatCents,
  formatCount,
  formatSignedCents,
  formatSignedPct,
} from "@/lib/format";
import { useSessionStore } from "@/lib/store";
import type { Finding } from "@/lib/types";
import { useCountUp } from "@/lib/useCountUp";
import { cn } from "@/lib/utils";

const TONE_TEXT = {
  good: "text-positive",
  bad: "text-negative",
  neutral: "text-foreground",
} as const;

/**
 * One certified finding: impact stat (big signed money, direction-of-good
 * colored), current-vs-prior mini bars, grade, confidence, and typed drill
 * actions that emit refinement objects — no NL in the loop.
 */
export function FindingCard({ finding, turnId }: { finding: Finding; turnId: string }) {
  const emitRefinement = useSessionStore((s) => s.emitRefinement);
  const focused = useSessionStore((s) => s.focusedReferent === finding.referent.value);

  // A CEILING, not a measurement. The wire has said so since wave B
  // (`denial_rate__is_bound`) and this card printed "76.9%" in the same
  // 1.55rem numeral as the measured card three above it, under a title
  // that read "≤ 76.9% … (upper bound)". Two surfaces of one card
  // disagreeing about what kind of number it is.
  const bound = finding.measured?.isBound === true ? finding.measured : undefined;

  const tone =
    finding.impactKind === "delta" && finding.impactCents !== undefined
      ? deltaTone(finding.impactCents, finding.directionOfGood)
      : "neutral";

  // The impact stat lands with a short count-up (reduced-motion snaps).
  const animatedCents = useCountUp(finding.impactCents ?? 0);
  const impact =
    finding.impactDisplay ??
    (finding.impactCents !== undefined
      ? finding.impactKind === "delta"
        ? formatSignedCents(animatedCents)
        : formatCents(animatedCents)
      : null);

  return (
    <article
      id={`referent-${finding.referent.value}`}
      className={cn(
        "flex h-full flex-col gap-2.5 rounded-lg border bg-card p-3.5 transition-all duration-150 hover:-translate-y-px hover:border-ring/30",
        // Solid `--ring`, not `/60`: this ring is the ONLY signal that
        // says which card a referent chip just pointed at, and at 60% it
        // measured 2.15:1 against the card — under the 3:1 SC 1.4.11
        // floor for a non-text indicator. Solid is 3.74:1 / 10.05:1.
        focused && "ring-2 ring-ring",
      )}
    >
      <header className="flex items-start justify-between gap-2">
        {/* The title carries the pack's governed display name in place of
            the raw metric id — composed that way by the server when it
            publishes `metric_display`, and corrected at the wire seam when
            it does not.

            The caveat that was authored WITH that name is printed under
            it, as text. It used to live in a hover tooltip, which meant a
            screenshotted card — the form these cards actually travel in —
            shipped the label and left its bound behind. A correction
            published without its bound is the flattering half of a
            governed entry, which is worse than shipping neither. */}
        <div className="min-w-0 space-y-1">
          <h3 className="text-[0.78rem] font-semibold leading-snug">{finding.title}</h3>
          {finding.metricDisplay?.caveat && (
            <p className="text-[0.66rem] leading-snug text-muted-foreground">
              {finding.metricDisplay.caveat}
            </p>
          )}
        </div>
        <ReferentChip value={finding.referent.value} className="mt-0.5 shrink-0" />
      </header>

      <div className="flex items-end justify-between gap-3">
        <div>
          {impact ? (
            <p
              className={cn(
                "numeral text-[1.55rem] font-medium leading-none",
                // A ceiling wears the qualified tint, not the measured
                // ink: the treatment is the second signal (after the "≤")
                // that says this number is an edge and not a reading.
                bound ? "text-muted-foreground" : TONE_TEXT[tone],
              )}
            >
              {impact}
            </p>
          ) : (
            finding.impactWithheldReason && (
              // The server withheld the impact figure on purpose (a
              // mismatched comparison window makes any difference between
              // the two sides meaningless). An empty stat slot reads as a
              // rendering failure; the refusal reads as the engine being
              // careful, which is what actually happened.
              <p className="text-[0.7rem] font-medium leading-tight text-muted-foreground">
                No impact figure
                <span className="ml-1 font-normal">— {finding.impactWithheldReason}</span>
              </p>
            )
          )}
          {/* What the ceiling is a ceiling OVER. A bound without its
              population is unreadable: "at most 76.9%" over thirteen
              claims and over thirteen thousand are different facts, and
              only one of them is worth a payer meeting. The population is
              the engine's own (`__bound_population`) and is stated in the
              same words the finding's statement uses. */}
          {bound && (
            <p className="mt-1 text-[0.62rem] font-medium leading-snug text-warning">
              upper bound
              {bound.boundPopulation !== undefined
                ? ` over a population of ${formatCount(bound.boundPopulation)}`
                : ""}
              <span className="ml-1 font-normal text-muted-foreground">
                — a ceiling, not a measurement
              </span>
            </p>
          )}
          <p className="mt-1.5 flex items-center gap-1.5 text-[0.65rem] text-muted-foreground">
            {finding.impactLabel}
            {finding.deltaPct !== undefined && (
              <span className={cn("num font-medium", TONE_TEXT[tone])}>
                {formatSignedPct(finding.deltaPct)}
              </span>
            )}
          </p>
        </div>
        {finding.comparison && <MiniBars comparison={finding.comparison} tone={tone} />}
      </div>

      <p className="text-[0.72rem] leading-snug text-muted-foreground">{finding.statement}</p>

      {/* The governed external ranges this finding's measure carries. Up
          to seven per finding reached the wire and were rendered nowhere,
          so the only path they took to a reader was inside a confident
          narrative sentence carrying neither the review status nor the
          cautions the entry ships with. See `BenchmarkStrip`. */}
      {finding.benchmarks && finding.benchmarks.length > 0 && (
        <BenchmarkStrip
          benchmarks={finding.benchmarks}
          measured={finding.measured}
          referent={finding.referent.value}
        />
      )}

      <footer className="mt-auto flex items-center justify-between gap-2 border-t pt-2.5">
        <div className="flex items-center gap-1.5">
          <GradeBadge grade={finding.grade} size="xs" />
          <span className="text-[0.62rem] uppercase tracking-wide text-muted-foreground">
            {finding.confidence} conf
          </span>
        </div>
        <div className="flex flex-wrap justify-end gap-1">
          {finding.suggestedRefinements.map((s) => (
            <Button
              key={s.label}
              variant="outline"
              size="xs"
              className="h-5 rounded-full px-2 text-[0.65rem] font-normal text-muted-foreground hover:text-foreground"
              onClick={() =>
                emitRefinement(s.refinement, { turnId, referent: finding.referent.value })
              }
            >
              {s.label}
            </Button>
          ))}
        </div>
      </footer>
    </article>
  );
}

/**
 * Current-vs-prior comparison bars. Two steps of one measure with direct
 * labels (ordinal encoding — identity is never color-alone).
 */
function MiniBars({
  comparison,
  tone,
}: {
  comparison: NonNullable<Finding["comparison"]>;
  tone: "good" | "bad" | "neutral";
}) {
  const max = Math.max(comparison.priorCents, comparison.currentCents, 1);
  const rows = [
    { label: comparison.priorLabel, cents: comparison.priorCents, current: false },
    { label: comparison.currentLabel, cents: comparison.currentCents, current: true },
  ];
  return (
    <div className="flex w-36 shrink-0 flex-col gap-1" aria-label="current vs prior">
      {rows.map((row) => (
        <div key={row.label} className="flex items-center gap-1.5">
          <div className="h-3.5 flex-1 overflow-hidden rounded-[3px] bg-surface-sunken">
            <div
              className={cn(
                "h-full rounded-[3px] transition-[width] duration-300",
                row.current
                  ? tone === "bad"
                    ? "bg-negative/75"
                    : tone === "good"
                      ? "bg-positive/75"
                      : "bg-chart-current"
                  : "bg-chart-baseline/45",
              )}
              style={{ width: `${Math.max((row.cents / max) * 100, 2)}%` }}
            />
          </div>
          <span className="num w-14 text-right text-[0.6rem] leading-none text-muted-foreground">
            {compactMoney(row.cents)}
          </span>
        </div>
      ))}
      <div className="flex justify-between text-[0.55rem] uppercase tracking-wide text-muted-foreground">
        <span>{rows[0].label}</span>
        <span>{rows[1].label}</span>
      </div>
    </div>
  );
}

function compactMoney(cents: number): string {
  const dollars = cents / 100;
  if (dollars >= 1_000_000) return `$${(dollars / 1_000_000).toFixed(2)}M`;
  if (dollars >= 1_000) return `$${(dollars / 1_000).toFixed(1)}K`;
  return `$${dollars.toFixed(0)}`;
}
