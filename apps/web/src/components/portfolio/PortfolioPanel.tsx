"use client";

import { ArrowUpRight } from "lucide-react";

import { GradeBadge } from "@/components/answer/GradeBadge";
import { Button } from "@/components/ui/button";
import { formatSignedCents, formatCents } from "@/lib/format";
import { PORTFOLIO_ITEMS, PORTFOLIO_META, type PortfolioItem } from "@/lib/mock/portfolio";
import { usePortfolioQuery } from "@/lib/queries";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * Pre-materialized "top five things today", ranked by the governed
 * dollar-impact policy. Each card drills into an ordinary session turn at
 * the portfolio's pinned watermark.
 *
 * In api mode this comes from GET /v1/portfolio/latest (a 501 is the
 * graceful "not built yet"); mock mode keeps the local fixture.
 */
export function PortfolioPanel() {
  const emitRefinement = useSessionStore((s) => s.emitRefinement);
  const mode = useSessionStore((s) => s.connection.mode);
  const query = usePortfolioQuery(mode === "api");

  let items: PortfolioItem[] = PORTFOLIO_ITEMS;
  let footer = `Pre-materialized at ${PORTFOLIO_META.watermark} · drill-downs continue as ordinary turns at this watermark`;
  let emptyNote: string | null = null;

  if (mode === "api") {
    if (query.data?.kind === "ok") {
      items = query.data.snapshot.items;
      const at = query.data.snapshot.watermark;
      footer = at
        ? `Pre-materialized at ${at} · drill-downs continue as ordinary turns at this watermark`
        : "Drill-downs continue as ordinary turns at the portfolio's watermark";
      if (items.length === 0) emptyNote = "No portfolio items at the current watermark.";
    } else {
      items = [];
      emptyNote = query.isPending
        ? "Loading portfolio…"
        : query.data?.kind === "unavailable"
          ? "Portfolio endpoint not implemented yet (HTTP 501)."
          : "Portfolio unreachable — will retry.";
      footer = "";
    }
  }

  return (
    <section className="space-y-2">
      <header className="flex items-baseline justify-between">
        <h3 className="text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
          Today&apos;s portfolio
        </h3>
        <span className="num font-mono text-[0.58rem] text-muted-foreground/70">
          {mode === "api"
            ? (query.data?.kind === "ok" ? (query.data.snapshot.rankingPolicy ?? "") : "")
            : PORTFOLIO_META.rankingPolicy}
        </span>
      </header>
      {emptyNote ? (
        <p className="px-1 text-[0.62rem] leading-snug text-muted-foreground">{emptyNote}</p>
      ) : (
        <ol className="space-y-1.5">
          {items.map((item) => (
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
                  <p className="numeral mt-1 text-[0.86rem] font-medium">
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
      )}
      {footer && (
        <p className="num text-[0.58rem] leading-snug text-muted-foreground/70">{footer}</p>
      )}
    </section>
  );
}
