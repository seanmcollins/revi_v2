"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { EvidenceGrade } from "@/lib/types";
import { cn } from "@/lib/utils";

const GRADE_META: Record<
  EvidenceGrade,
  { label: string; dotClass: string; textClass: string; explanation: string }
> = {
  direct: {
    label: "Direct",
    dotClass: "bg-grade-direct",
    textClass: "text-grade-direct",
    explanation: "The field explicitly represents the concept being measured.",
  },
  derived: {
    label: "Derived",
    dotClass: "bg-grade-derived",
    textClass: "text-grade-derived",
    explanation: "Deterministically calculated from validated fields.",
  },
  proxy: {
    label: "Proxy",
    dotClass: "bg-grade-proxy",
    textClass: "text-grade-proxy",
    explanation:
      "Correlated with the concept but does not prove it. Treat as indicative.",
  },
  discovery: {
    // The engine calls this grade "discovery"; on the badge it says what it
    // means. The engine's own token is in the tooltip and in debug mode's
    // decision trace, so the mapping stays auditable both ways.
    label: "Uncertified",
    dotClass: "bg-grade-discovery",
    textClass: "text-grade-discovery",
    explanation:
      "Uses catalog fields nobody has certified for this purpose (the engine grades this “discovery”) — scoping over them downgrades the whole chain.",
  },
  unavailable: {
    label: "Unavailable",
    dotClass: "bg-grade-unavailable",
    textClass: "text-grade-unavailable",
    explanation: "No adequate measurement exists for this concept.",
  },
};

const GRADE_LAW =
  "A result is only as strong as its weakest input: combining weak evidence with strong evidence cannot turn it into a certified conclusion.";

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
  const meta = GRADE_META[grade];
  const spelled = grade !== "direct";
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex shrink-0 cursor-default items-center gap-1 text-micro font-medium",
            meta.textClass,
            className,
          )}
        >
          <span aria-hidden className={cn("size-1.5 rounded-full", meta.dotClass)} />
          {spelled ? meta.label : <span className="sr-only">{meta.label} evidence</span>}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-64">
        <p className="mb-1 font-medium">{meta.label} evidence</p>
        <p className="text-micro leading-snug opacity-90">{meta.explanation}</p>
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
  const meta = GRADE_META[grade];
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className={cn(
            "inline-flex cursor-default items-center gap-1.5 rounded-full border px-2 font-medium",
            size === "sm" ? "h-5 text-meta" : "h-[1.2rem] text-micro",
            meta.textClass,
            className,
          )}
        >
          <span className={cn("size-1.5 rounded-full", meta.dotClass)} />
          {meta.label}
        </span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-64">
        <p className="mb-1 font-medium">{meta.label} evidence</p>
        <p className="text-meta leading-snug opacity-90">{meta.explanation}</p>
        <p className="mt-1.5 text-meta leading-snug opacity-70">{GRADE_LAW}</p>
      </TooltipContent>
    </Tooltip>
  );
}
