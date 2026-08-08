"use client";

import { ArrowUpRight } from "lucide-react";

import { GradeBadge } from "@/components/answer/GradeBadge";
import { Button } from "@/components/ui/button";
import { formatSignedCents, formatCents } from "@/lib/format";
import { PORTFOLIO_ITEMS, PORTFOLIO_META } from "@/lib/mock/portfolio";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * Pre-materialized "top five things today", ranked by the governed
 * dollar-impact policy. Each card drills into an ordinary session turn at
 * the portfolio's pinned watermark.
 */
export function PortfolioPanel() {
  const emitRefinement = useSessionStore((s) => s.emitRefinement);

  return (
    <section className="space-y-2">
      <header className="flex items-baseline justify-between">
        <h3 className="text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
          Today&apos;s portfolio
        </h3>
        <span className="num font-mono text-[0.58rem] text-muted-foreground/70">
          {PORTFOLIO_META.rankingPolicy}
        </span>
      </header>
      <ol className="space-y-1.5">
        {PORTFOLIO_ITEMS.map((item) => (
          <li
            key={item.referent}
            className="group rounded-md border bg-card p-2.5 transition-colors duration-150 hover:border-ring/40"
          >
            <div className="flex items-start gap-2">
              <span className="num mt-px w-3 shrink-0 text-[0.65rem] font-medium text-muted-foreground">
                {item.rank}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[0.7rem] font-medium leading-snug" title={item.title}>
                  {item.title}
                </p>
                <p className="num mt-0.5 text-[0.78rem] font-semibold tracking-tight">
                  <span className={cn(item.impactCents < 0 && "text-negative")}>
                    {item.impactCents < 0
                      ? formatSignedCents(item.impactCents)
                      : formatCents(item.impactCents)}
                  </span>
                  <span className="ml-1.5 text-[0.6rem] font-normal text-muted-foreground">
                    {item.impactLabel}
                  </span>
                </p>
                <p className="mt-1 line-clamp-2 text-[0.62rem] leading-snug text-muted-foreground">
                  {item.detail}
                </p>
                <div className="mt-1.5 flex items-center justify-between">
                  <GradeBadge grade={item.grade} size="xs" />
                  <Button
                    variant="ghost"
                    size="xs"
                    className="h-5 gap-0.5 rounded-full px-1.5 text-[0.62rem] font-normal text-verified opacity-0 transition-opacity duration-150 hover:text-verified group-hover:opacity-100"
                    onClick={() =>
                      emitRefinement(item.drill.refinement, { referent: item.referent })
                    }
                  >
                    {item.drill.label}
                    <ArrowUpRight className="size-2.5" />
                  </Button>
                </div>
              </div>
            </div>
          </li>
        ))}
      </ol>
      <p className="num text-[0.58rem] leading-snug text-muted-foreground/70">
        Pre-materialized at {PORTFOLIO_META.watermark} · drill-downs continue as
        ordinary turns at this watermark
      </p>
    </section>
  );
}
