"use client";

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { announce } from "@/lib/announce";
import { formatSignedCents } from "@/lib/format";
import { useSessionStore } from "@/lib/store";
import { useAnswerVariant } from "@/lib/useAnswerVariant";
import { scrollIntoViewRespectingMotion } from "@/lib/useReducedMotion";
import { cn } from "@/lib/utils";

/** Where a fact is anchored in the Evidence rail. */
export const EVIDENCE_FACT_ANCHOR = "evidence-fact";

/**
 * A stable analyst-visible handle (F1, F2, …).
 *
 * Hover shows the backing finding. What a CLICK does depends on where the
 * fact lives in the layout being read, and that is the whole of the
 * difference:
 *
 *   default / detailed — the cards are on the answer, so the chip scrolls
 *     to the card and rings it.
 *   calm — the facts are in the Evidence rail, so the chip OPENS the rail
 *     on this fact's own turn and scrolls the rail to it. The citation in
 *     a sentence and the fact it cites are one gesture apart, which is
 *     the thing that makes moving the facts off the answer honest rather
 *     than merely quieter.
 *
 * The rail is a sibling region that is always mounted, so the fact is in
 * the page's accessible structure either way; the chip names its target
 * so a screen-reader user is told which of the two will happen.
 */
export function ReferentChip({
  value,
  tone = "chip",
  className,
}: {
  value: string;
  /**
   * `chip` is the row handle — a bordered green box in front of a fact,
   * where it is the row's own name. `citation` is the form the WRITING
   * uses: a quiet superscript, because in the calm layout the prose is
   * the answer and a dozen green boxes in one paragraph is the detail
   * spam this change exists to remove, re-imported into the sentences.
   */
  tone?: "chip" | "citation";
  className?: string;
}) {
  const entry = useSessionStore((s) => s.referents[value]);
  const focusReferent = useSessionStore((s) => s.focusReferent);
  const openDrawer = useSessionStore((s) => s.openDrawer);
  const variant = useAnswerVariant();
  const toRail = variant === "b";

  const chip = (
    <button
      type="button"
      aria-label={
        entry
          ? toRail
            ? `${value}: ${entry.label} — open in Evidence`
            : `${value}: ${entry.label} — go to this finding`
          : value
      }
      onClick={() => {
        focusReferent(value);
        if (toRail && entry) {
          openDrawer(entry.turnId);
          // The rail renders this fact on the next commit; the jump waits
          // for it rather than reaching for an element that does not
          // exist yet.
          window.setTimeout(() => {
            jumpTo(document.getElementById(`${EVIDENCE_FACT_ANCHOR}-${value}`), value, true);
          }, 0);
          return;
        }
        jumpTo(document.getElementById(`referent-${value}`), value, false);
      }}
      className={cn(
        "focus-ring transition-colors duration-150",
        tone === "citation"
          ? // A footnote marker: superscript, monospace, subordinate to
            // the entity name it follows, with a dotted rule so it is
            // still identifiably a control at rest.
            "num align-super font-mono text-[0.62em] font-medium leading-none text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-verified hover:decoration-verified"
          : "inline-flex h-[1.15rem] items-center rounded border border-verified/40 bg-verified/10 px-1 font-mono text-micro font-medium leading-none text-verified hover:bg-verified/20",
        className,
      )}
    >
      {value}
    </button>
  );

  if (!entry) return chip;

  return (
    <HoverCard openDelay={150} closeDelay={80}>
      <HoverCardTrigger asChild>{chip}</HoverCardTrigger>
      <HoverCardContent side="top" className="w-72 space-y-1.5 p-3">
        <div className="flex items-baseline justify-between gap-2">
          <span className="font-mono text-micro text-verified">{value}</span>
          {entry.impactCents !== undefined && (
            <span className="num text-meta font-semibold">
              {formatSignedCents(entry.impactCents)}
            </span>
          )}
        </div>
        <p className="text-meta font-medium leading-snug">{entry.label}</p>
        {entry.statement && (
          <p className="line-clamp-3 text-micro leading-snug text-muted-foreground">
            {entry.statement}
          </p>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}

/**
 * The jump, for every reader — not only the one holding a mouse.
 *
 * What shipped was `openDrawer` plus a scroll: the viewport moved and
 * nothing else did. A keyboard user's focus stayed in the paragraph, so
 * the next Tab continued from the citation rather than from the fact,
 * and a screen reader was told nothing at all — which means "the facts
 * moved to the rail, one gesture away" was true for a pointer and false
 * for everybody else, and the ≤2-taps invariant did not hold.
 *
 * So the target takes focus (it is `tabIndex={-1}` for exactly this — see
 * `FactRow`), which both moves the tab order and makes a screen reader
 * read the fact it just jumped to. When the target is not on the page,
 * one polite sentence says what happened instead of a silent no-op.
 */
function jumpTo(target: HTMLElement | null, value: string, toRail: boolean): void {
  if (!target) {
    announce(
      toRail
        ? `${value} is shown in the Evidence panel.`
        : `${value} could not be shown on this screen.`,
    );
    return;
  }
  // Focus first, scroll second: focusing scrolls the element into view by
  // the browser's own rule, which ignores the reader's motion preference.
  target.focus({ preventScroll: true });
  scrollIntoViewRespectingMotion(target, { block: "center" });
}
