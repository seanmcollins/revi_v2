"use client";

import { ArrowUpRight } from "lucide-react";

import { HERO_QUESTIONS } from "@/lib/guideQuestions";
import { cn } from "@/lib/utils";

/**
 * THE FOUR QUESTIONS THE PRODUCT OPENS WITH.
 *
 * One per verb of a working day — detect, diagnose, act, prevent — so a
 * reader who clicks all four has walked the whole loop. The full guide set
 * stays reachable from ⌘K; these are the front door.
 *
 * Extracted from the workspace's empty state when Home began offering the
 * same four beside its own composer. Two copies of a chip grid is two
 * places for the type scale, the disabled rule and the hover affordance to
 * drift apart — and these chips are the first thing anybody clicks in a
 * demo.
 *
 * The grid is sized by CONTAINER width, not viewport: both call sites sit
 * in a column whose width is set by the rails around it, so a viewport
 * breakpoint would fire at the wrong moment. The parent supplies
 * `@container`.
 */
export function HeroQuestions({
  onAsk,
  disabled = false,
  className,
}: {
  onAsk: (question: string) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("grid w-full items-stretch gap-1.5 text-left @lg:grid-cols-2", className)}>
      {HERO_QUESTIONS.map((question, i) => (
        <button
          key={question}
          type="button"
          disabled={disabled}
          onClick={() => onAsk(question)}
          className="fade-up focus-ring group flex items-center justify-between gap-2 rounded-lg border bg-card/55 px-3 py-2 text-left text-meta leading-snug backdrop-blur-sm transition-all duration-150 hover:-translate-y-px hover:border-ring/40 hover:bg-card hover:shadow-sm disabled:opacity-50"
          style={{ animationDelay: `${80 + i * 40}ms` }}
        >
          <span className="text-pretty">{question}</span>
          <ArrowUpRight className="size-3 shrink-0 text-muted-foreground opacity-0 transition-opacity duration-150 group-hover:opacity-100" />
        </button>
      ))}
    </div>
  );
}
