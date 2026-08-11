"use client";

import { FigureBand, KeyFigure } from "@/components/figures/KeyFigure";
import { confidenceLabel, decimal, type ResearchHeadline } from "@/lib/deepResearch";
import { formatCents, formatCount, formatPct } from "@/lib/format";

/**
 * THE DETERMINATION — the deepest thing this product says, at the size it
 * deserves.
 *
 * `KeyFigure` is imported rather than re-invented for the reason its own
 * doc comment gives: it is the product's ONE way of making a number the
 * thing the eye lands on, and a second display-figure vocabulary here would
 * be a second place for the honesty marks to be dropped on the way up.
 *
 * THE INTERVAL IS PART OF THE NUMBER.
 *
 * This is the whole design of the block. An expected-recovery estimate
 * without its bounds is not a smaller claim than the estimate with them —
 * it is a DIFFERENT claim, and the wrong one: "$1,167,668.88" reads as a
 * measurement and "between $874,052.42 and $1,518,693.69" reads as what it
 * is. So the range is set at reading size directly under the numeral,
 * inside the same cell, before any note and before any warning. A footnote
 * would be the honest content of this figure filed where a reader skips.
 *
 * THE SPLIT IS SUPPORTING, NOT PEER. Still inside the filing deadline,
 * already past it, and no limit on file are three facts about the OPEN
 * DENIED DOLLARS — a different quantity from the expected recovery above
 * them — so they sit in the figure band at the supporting size with what
 * they count named on every cell. Reading "$2,831,002.86 still catchable"
 * as recoverable dollars is the one misreading this band can produce, and
 * the context line on each cell is what prevents it.
 */
export function ResearchHeadlineFigures({
  headline,
  dataLoadLabel,
}: {
  headline: ResearchHeadline;
  /** "the load through Aug 2, 2026" — the server's own phrase. */
  dataLoadLabel: string;
}) {
  const interval = headline.total_expected_interval;
  const confidence = confidenceLabel(interval?.confidence);
  const unpriced = decimal(headline.unpriced_share);

  return (
    <section
      data-research-headline
      aria-labelledby="research-headline-heading"
      className="space-y-2.5"
    >
      <h3
        id="research-headline-heading"
        className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
      >
        What this is worth
      </h3>

      <KeyFigure
        emphasis
        label="Expected recoverable"
        labelDetail="Each population's open denials split by whether the filing deadline has passed, each side priced at the rate denials on that side come back at, and multiplied by what a win actually returns on the denied dollar. Only populations this organization's own history can price are in it."
        value={formatCents(headline.total_expected_cents)}
        context={
          <>
            {/* THE RANGE, AT READING SIZE. `KeyFigure`'s context slot is
                12px by default; this span sets its own step, because the
                bounds are not a note about the figure — they are half of
                it. */}
            {interval && (
              <span className="numeral block text-lead leading-tight text-foreground/90">
                Between {formatCents(interval.low_cents)} and {formatCents(interval.high_cents)}
              </span>
            )}
            <span className="mt-1 block leading-snug">
              {confidence !== "" && `A ${confidence} interval. `}
              {headline.range_is_summed_endpoints
                ? "It is the sum of each population's own range — the widest way to add ranges up — so read it as a spread rather than a guarantee."
                : "Read it as a spread rather than a guarantee."}
              {/* WHAT THE BAND DOES NOT MOVE WITH. Only the recovery rate
                  varies inside it; the denied amounts and the share of a
                  denied dollar a win returns enter as known quantities. A
                  band drawn around money reads as a band on the money, and
                  this is the sentence that stops it. */}
              {headline.amounts_treated_as_known &&
                " It moves with the recovery rate only — the denied amounts are treated as known, so the true spread is wider."}
            </span>
          </>
        }
      />

      {/* HOW THE FIGURE WAS BUILT, in the server's own sentence.

          The construction is not a footnote about the headline: it is what
          makes the headline checkable against the tables below it. A
          reader who cannot see that past-deadline dollars were priced at
          the past-deadline rate, and that a win was priced at what a win
          returns, has no way to tell this figure from the one that
          multiplied a count rate by the full denied amount. */}
      {headline.construction !== "" && (
        <p className="num max-w-[68ch] text-meta leading-snug text-muted-foreground">
          {headline.construction}
        </p>
      )}

      {/* WHAT THE FIGURE WAS TAKEN OVER, and what it left out. Derived from
          the headline payload itself rather than restated from a warning:
          the priced base and the unpriced share are fields, and a reader
          looking at the number above is owed the denominator beside it.
          The full sentence about the unpriced populations still travels in
          the caveats, where every other qualification is. */}
      <p className="num max-w-[68ch] text-meta leading-snug text-muted-foreground">
        Priced over {formatCents(headline.priced_open_dollars_cents)} of the{" "}
        {formatCents(headline.total_open_dollars_cents)} still open across{" "}
        {formatCount(headline.total_open_denials)} denial
        {headline.total_open_denials === 1 ? "" : "s"}.{" "}
        {headline.unpriced_open_dollars_cents > 0 && (
          <>
            {formatCents(headline.unpriced_open_dollars_cents)}
            {unpriced !== undefined && ` — ${formatPct(unpriced)} of the open denied dollars`} — is
            in populations your own history cannot price yet, and is not in the figure above.
          </>
        )}
        {headline.unpriced_position_dollars_cents > 0 && (
          <>
            {" "}
            A further {formatCents(headline.unpriced_position_dollars_cents)} sits on a side of the
            filing deadline too few answered denials have reached for a rate to be published, and is
            not in the figure above either.
          </>
        )}
      </p>

      <FigureBand data-research-split className="max-w-5xl">
        <KeyFigure
          label="Still inside the filing deadline"
          value={formatCents(headline.catchable_dollars_cents)}
          context="Of the denied dollars still open"
        />
        <KeyFigure
          label="Past the filing deadline"
          value={formatCents(headline.deadline_passed_dollars_cents)}
          // Said in words, in the same ink. Amber is a verdict colour and a
          // position count is not a verdict — these dollars are past a
          // limit, which is not the same as lost.
          context="A position against the limit, not a write-off"
        />
        <KeyFigure
          label="No filing limit on file"
          value={formatCents(headline.deadline_unknown_dollars_cents)}
          context="Plans with no limit recorded either way"
        />
      </FigureBand>

      {dataLoadLabel !== "" && (
        <p className="num text-micro text-muted-foreground">
          Every figure here is read at {dataLoadLabel}.
        </p>
      )}
    </section>
  );
}
