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
    label: "Discovery",
    dotClass: "bg-grade-discovery",
    textClass: "text-grade-discovery",
    explanation:
      "Involves uncertified catalog fields — scoping over them downgrades the whole chain.",
  },
  unavailable: {
    label: "Unavailable",
    dotClass: "bg-grade-unavailable",
    textClass: "text-grade-unavailable",
    explanation: "No adequate measurement exists for this concept.",
  },
};

const GRADE_LAW =
  "Grade law: every transform output carries the weakest grade among its inputs — proxy evidence cannot launder into a certified conclusion through arithmetic.";

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
            size === "sm" ? "h-5 text-[0.7rem]" : "h-[1.05rem] text-[0.65rem]",
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
        <p className="text-[0.7rem] leading-snug opacity-90">{meta.explanation}</p>
        <p className="mt-1.5 text-[0.65rem] leading-snug opacity-70">{GRADE_LAW}</p>
      </TooltipContent>
    </Tooltip>
  );
}
