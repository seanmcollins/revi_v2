"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { GRADE_EXPLANATIONS, GRADE_LABELS, type EvidenceGrade } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * THE WORDS ARE NOT THIS FILE'S ANY MORE — only the ink is.
 *
 * This map used to carry a label and an explanation per grade, and two
 * other surfaces carried their own. The wording now comes from
 * {@link GRADE_LABELS} and {@link GRADE_EXPLANATIONS}, which every surface
 * that prints a grade reads, so a badge and the integrity line beside it
 * cannot describe one number two ways. What stays here is the palette,
 * which is genuinely a property of the badge.
 */
const GRADE_INK: Record<EvidenceGrade, { dotClass: string; textClass: string }> = {
  direct: { dotClass: "bg-grade-direct", textClass: "text-grade-direct" },
  derived: { dotClass: "bg-grade-derived", textClass: "text-grade-derived" },
  proxy: { dotClass: "bg-grade-proxy", textClass: "text-grade-proxy" },
  discovery: { dotClass: "bg-grade-discovery", textClass: "text-grade-discovery" },
  unavailable: { dotClass: "bg-grade-unavailable", textClass: "text-grade-unavailable" },
};

/**
 * The one rule that makes the grades a system rather than five adjectives.
 *
 * It said "cannot turn it into a certified conclusion", and `certified` is
 * the platform's word for "standardized in your definitions library" — a
 * reader who has never seen it takes "uncertified" to mean "wrong". The
 * sentence says the same thing without asking anybody to know that.
 */
const GRADE_LAW =
  "A result is only as strong as its weakest input. Putting strong evidence beside weak evidence does not make the weak part any stronger.";

/**
 * The grade, at row scale.
 *
 * A dot is enough for `direct` — the expected case, on most rows, where a
 * pill per row is eight pills of the same word. It is NOT enough for
 * anything else: "proxy" and "uncertified" are the grades that change
 * what a number may be used for, and leaving those to a hue would be
 * identity carried by colour alone on the one signal that says how much
 * to trust the figure beside it. So the exceptions keep their word, and
 * every dot carries its grade in its accessible name and its tooltip.
 */
export function GradeDot({ grade, className }: { grade: EvidenceGrade; className?: string }) {
  const ink = GRADE_INK[grade];
  const label = GRADE_LABELS[grade];
  const spelled = grade !== "direct";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex shrink-0 cursor-default items-center gap-1 text-micro font-medium",
            ink.textClass,
            className,
          )}
        >
          <span aria-hidden className={cn("size-1.5 rounded-full", ink.dotClass)} />
          {/* "Direct evidence" was grammatical; "Measured directly evidence"
              is not. The accessible name says which of the two nouns is
              being described rather than gluing the label onto one. */}
          {spelled ? label : <span className="sr-only">Evidence: {label.toLowerCase()}</span>}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-64">
        <p className="mb-1 font-medium">{label}</p>
        <p className="text-micro leading-snug opacity-90">{GRADE_EXPLANATIONS[grade]}</p>
        <p className="mt-1.5 text-micro leading-snug opacity-70">{GRADE_LAW}</p>
      </TooltipContent>
    </Tooltip>
  );
}

export function GradeBadge({
  grade,
  size = "sm",
  className,
}: {
  grade: EvidenceGrade;
  size?: "sm" | "xs";
  className?: string;
}) {
  const ink = GRADE_INK[grade];
  const label = GRADE_LABELS[grade];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            // `whitespace-nowrap` and `shrink-0`: the labels are clauses
            // now ("Calculated from measured values"), and a pill that
            // wraps mid-clause inside a finding's title row reads as two
            // badges. It takes the width it needs and the title beside it
            // truncates, which is the right loser of that fight — the
            // title is repeated in the card body and the grade is not.
            "inline-flex shrink-0 cursor-default items-center gap-1.5 whitespace-nowrap rounded-full border px-2 font-medium",
            size === "sm" ? "h-5 text-meta" : "h-[1.2rem] text-micro",
            ink.textClass,
            className,
          )}
        >
          <span className={cn("size-1.5 shrink-0 rounded-full", ink.dotClass)} />
          {label}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-64">
        <p className="mb-1 font-medium">{label}</p>
        <p className="text-meta leading-snug opacity-90">{GRADE_EXPLANATIONS[grade]}</p>
        <p className="mt-1.5 text-meta leading-snug opacity-70">{GRADE_LAW}</p>
      </TooltipContent>
    </Tooltip>
  );
}
