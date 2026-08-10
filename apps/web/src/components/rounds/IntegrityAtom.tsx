"use client";

import { useState } from "react";

import {
  ThingsToKnowSheet,
  thingsToKnowLabel,
  thingsToKnowSeverity,
} from "@/components/answer/ThingsToKnow";
import type { TileIntegrity } from "@/lib/rounds";
import type { WarningEvent } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * THE INTEGRITY ATOM — the integrity line, on a surface with no asker.
 *
 * On an answer, the honesty marks can lean on the conversation around
 * them: the reader typed the question, chose the window, and has the
 * context line on screen. A Rounds tile has none of that. It is read at
 * 07:50 by somebody who has not typed anything, and whatever number it
 * shows is the number they will repeat in the huddle. So the same line
 * travels with the tile, and the grade travels ON it rather than in a
 * badge some layout may or may not mount:
 *
 *     ● Verified · 7 things to know, 6 change how a number here should
 *       be read
 *
 * Three things it is not allowed to do, each of which was a defect
 * somewhere else in this product first:
 *
 *   go missing. A tile with no atom is never built (`mapRoundsTile`
 *     drops it and reports drift), and this component takes a
 *     `TileIntegrity` rather than an optional one, so there is no render
 *     path where a value appears without its marks.
 *   state a count it cannot open. `thingsToKnow` is exactly
 *     `caveatCodes.length` on the payload, and the sheet this opens lists
 *     those codes — a count on the line the sheet does not back is the
 *     one error this product cannot make twice.
 *   dress up a ceiling. `isBound` and `provisional` are said in words
 *     beside the number, not implied by a colour.
 */
export function IntegrityAtom({
  integrity,
  /** The tile's own warnings, which are what the count counts. */
  warnings,
  className,
}: {
  integrity: TileIntegrity;
  warnings: readonly Omit<WarningEvent, "type">[];
  className?: string;
}) {
  const [sheetOpen, setSheetOpen] = useState(false);
  const caveats: WarningEvent[] = warnings.map((w) => ({ ...w, type: "warning" as const }));
  const grade = GRADE_CLAUSES[integrity.grade];
  /**
   * THE COUNT IS THE SHEET'S, NOT THE PAYLOAD'S.
   *
   * `integrity.thingsToKnow` counts the caveat CODES the server published;
   * `caveats` is that list after `readTurnWarnings` has collapsed the ones
   * that say the same thing in different probes' words. When the two
   * differ, the sheet is what a reader can actually check, so the sheet
   * wins — the same rule the answer's integrity line follows, and the
   * reason this product's caveat count has never been a number nobody can
   * open.
   *
   * The payload's own count is not thrown away: it rides on
   * `data-caveat-codes`, and a tile that published codes with no sentences
   * behind them still states the count (below), just without a control
   * that would open nothing.
   */
  const shown = caveats.length > 0 ? caveats.length : integrity.thingsToKnow;
  const openable = caveats.length > 0;

  return (
    <>
      <p
        data-integrity-atom
        data-answer-grade={integrity.grade}
        data-things-to-know={shown}
        data-caveat-codes={integrity.caveatCodes.join(" ")}
        className={cn(
          "flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-micro text-foreground/70",
          className,
        )}
      >
        <span
          data-integrity-tone={grade.tone}
          className={cn("mr-0.5 inline-block size-1.5 shrink-0 rounded-full", grade.dot)}
        >
          <span className="sr-only">{grade.label}: </span>
        </span>
        <span className={grade.tone === "qualified" ? "text-warning" : "text-foreground/80"}>
          {grade.text}
        </span>

        {shown > 0 && (
          <>
            <Dot />
            {/* The count is a control, and it looks like one. A caveat
                count nobody can open is a number that makes the tile feel
                audited without being auditable. */}
            {openable ? (
              <button
                type="button"
                onClick={() => setSheetOpen(true)}
                className="focus-ring rounded underline decoration-foreground/40 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
              >
                {thingsToKnowLabel(shown)}
              </button>
            ) : (
              // The tile counted caveats and published no sentences for
              // them. The count is still stated — dropping it would hide a
              // published fact — with no underline, because there is
              // nothing behind it to open.
              <span title={integrity.caveatCodes.join(", ")}>{thingsToKnowLabel(shown)}</span>
            )}
            {/* The severity, beside the count and not behind it, in the
                same words the answer's integrity line uses — and behind
                its own separator, which `IntegrityLine.tsx` has and this
                did not: the accessible string read "…·7 things to know6
                change how a number here should be read", i.e. two counts
                run together into one number nobody can parse. */}
            {openable && (
              // The separator travels INSIDE the clause it separates, so a
              // wrap cannot leave a bare dot dangling at the end of a line
              // — this line wraps on a tile at every width.
              <span className="inline-flex items-baseline gap-x-1.5 text-muted-foreground">
                <Dot />
                {thingsToKnowSeverity(caveats)}
              </span>
            )}
          </>
        )}
      </p>

      <ThingsToKnowSheet warnings={caveats} open={sheetOpen} onOpenChange={setSheetOpen} />
    </>
  );
}

/**
 * The marks that change what the number IS, said as words next to it.
 *
 * `≤` and "provisional" are not adornments on a figure — they are the
 * difference between "State Medicaid denies 29.5% of claims" and "State
 * Medicaid denies at most 29.5%, over a population too small to measure".
 * They sit with the value, in the value's own line, because that is where
 * somebody reading only the number will be looking.
 */
export function ValueMarks({ integrity }: { integrity: TileIntegrity }) {
  if (!integrity.isBound && !integrity.provisional) return null;
  return (
    <span className="ml-1.5 inline-flex flex-wrap items-baseline gap-x-1.5 text-micro font-normal text-warning">
      {integrity.isBound && <span>a ceiling, not a measurement</span>}
      {integrity.isBound && integrity.provisional && (
        <span aria-hidden className="text-muted-foreground/60">
          ·
        </span>
      )}
      {integrity.provisional && <span>still settling</span>}
    </span>
  );
}

/**
 * What each grade says, in the same words the answer's integrity line
 * uses. A grade is a claim about the evidence, so the phrasing does not
 * get to soften between surfaces.
 */
const GRADE_CLAUSES: Readonly<
  Record<TileIntegrity["grade"], { text: string; dot: string; tone: string; label: string }>
> = {
  direct: {
    text: "Verified against your data",
    dot: "integrity-dot bg-verified",
    tone: "verified",
    label: "Verified",
  },
  derived: {
    text: "Derived — calculated from validated fields",
    dot: "bg-foreground/50",
    tone: "measured",
    label: "Derived",
  },
  proxy: {
    text: "Indicative — computed from a stand-in measure",
    dot: "bg-warning",
    tone: "qualified",
    label: "Indicative",
  },
  discovery: {
    text: "Uncertified — fields nobody has certified for this purpose",
    dot: "bg-warning",
    tone: "qualified",
    label: "Uncertified",
  },
  unavailable: {
    text: "No adequate measurement exists for this",
    dot: "bg-muted-foreground/70",
    tone: "qualified",
    label: "No adequate measurement",
  },
};

/**
 * The separator between two clauses of the atom — a middle dot for the eye
 * and a comma for a screen reader.
 *
 * Both halves are load-bearing here. The dot alone is `aria-hidden`, so the
 * accessible string ran two counts together into one unparseable number:
 * "…·7 things to know6 change how a number here should be read". The
 * sr-only comma is what makes the spoken line the same line as the drawn
 * one.
 */
function Dot() {
  return (
    <>
      <span aria-hidden className="text-muted-foreground/60">
        ·
      </span>
      <span className="sr-only">, </span>
    </>
  );
}
