"use client";

import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card";
import { formatSignedCents } from "@/lib/format";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * A stable analyst-visible handle (F1, F2, …). Hover shows the backing
 * finding; click scrolls to and focuses it.
 */
export function ReferentChip({
  value,
  className,
}: {
  value: string;
  className?: string;
}) {
  const entry = useSessionStore((s) => s.referents[value]);
  const focusReferent = useSessionStore((s) => s.focusReferent);

  const chip = (
    <button
      type="button"
      onClick={() => {
        focusReferent(value);
        document
          .getElementById(`referent-${value}`)
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
      }}
      className={cn(
        "inline-flex h-[1.15rem] items-center rounded border border-verified/40 bg-verified/10 px-1 font-mono text-[0.68rem] font-medium leading-none text-verified transition-colors duration-150 hover:bg-verified/20",
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
          <span className="font-mono text-[0.68rem] text-verified">{value}</span>
          {entry.impactCents !== undefined && (
            <span className="num text-xs font-semibold">
              {formatSignedCents(entry.impactCents)}
            </span>
          )}
        </div>
        <p className="text-xs font-medium leading-snug">{entry.label}</p>
        {entry.statement && (
          <p className="line-clamp-3 text-[0.7rem] leading-snug text-muted-foreground">
            {entry.statement}
          </p>
        )}
      </HoverCardContent>
    </HoverCard>
  );
}
