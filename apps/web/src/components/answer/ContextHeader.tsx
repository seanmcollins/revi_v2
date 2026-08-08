"use client";

import {
  CalendarRange,
  Database,
  Filter,
  GitCompareArrows,
  Pin,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import {
  comparisonChipLabel,
  DATE_BASIS_LABELS,
  formatCount,
  formatWindow,
  mediumDate,
  windowChipLabel,
} from "@/lib/format";
import type { ContextHeaderData } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * §7.2 hard requirement: EVERY answer carries its explicit context —
 * window + basis · comparison · filters · cohort · watermark. Each chip
 * opens a plain-language explanation of what it pins.
 */
export function ContextHeader({
  header,
  pinnedEpoch,
}: {
  header: ContextHeaderData;
  pinnedEpoch?: boolean;
}) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Chip icon={<CalendarRange className="size-3" />} label={windowChipLabel(header.window)}>
        <ChipDoc title="Analysis window">
          <p>
            {formatWindow(header.window)}
            {header.window.requested && header.window.requested !== "n/a" && (
              <>
                {" "}
                — resolved from “{header.window.requested}” at plan time and stored as
                concrete dates; it never silently re-resolves.
              </>
            )}
          </p>
          <p className="mt-1.5 text-muted-foreground">
            Basis <span className="font-medium text-foreground">{DATE_BASIS_LABELS[header.window.basis]}</span>:
            {" "}
            {BASIS_DOCS[header.window.basis]}
          </p>
        </ChipDoc>
      </Chip>

      {header.comparison && (
        <Chip
          icon={<GitCompareArrows className="size-3" />}
          label={header.comparison.label ? `vs ${header.comparison.label}` : comparisonChipLabel(header.comparison.window)}
        >
          <ChipDoc title="Comparison">
            <p>
              {header.comparison.kind === "prior_period"
                ? "Prior period of equal length: "
                : header.comparison.kind === "same_period_last_year"
                  ? "Same period last year: "
                  : "Custom comparison window: "}
              {formatWindow(header.comparison.window)}.
            </p>
            <p className="mt-1.5 text-muted-foreground">
              Comparison windows re-anchor deterministically from stored concrete dates.
            </p>
          </ChipDoc>
        </Chip>
      )}

      <Chip
        icon={<Filter className="size-3" />}
        label={
          header.filters.length === 0
            ? "Scope: all"
            : header.filters.map((f) => `${f.dimensionLabel}: ${f.values.join(", ")}`).join(" · ")
        }
      >
        <ChipDoc title="Scope">
          {header.filters.length === 0 ? (
            <p>
              No filters applied — every claim, payer, and facility visible at this
              watermark is included.
            </p>
          ) : (
            <ul className="space-y-1">
              {header.filters.map((f) => (
                <li key={`${f.dimension}:${f.values.join(",")}`}>
                  <span className="font-medium">{f.dimensionLabel}</span>{" "}
                  {f.op === "not_in" ? "excludes" : "="} {f.values.join(", ")}
                  <span className="text-muted-foreground"> — from {f.originTurn}</span>
                </li>
              ))}
            </ul>
          )}
        </ChipDoc>
      </Chip>

      {header.cohort && (
        <Chip
          icon={<Users className="size-3" />}
          label={`Cohort: ${formatCount(header.cohort.size)} payers (pinned)`}
          accent
        >
          <ChipDoc title="Pinned cohort">
            <p>{header.cohort.definition}.</p>
            <p className="mt-1.5 text-muted-foreground">
              Pinned at {header.cohort.originTurn}: the member set is frozen — later
              turns reuse exactly these members even if the ranking would change.
            </p>
          </ChipDoc>
        </Chip>
      )}

      <Chip
        icon={<Database className="size-3" />}
        label={`Watermark: ${header.watermark.loadedAt}`}
        trailing={pinnedEpoch ? <Pin className="size-2.5 text-muted-foreground" /> : undefined}
      >
        <ChipDoc title="Data watermark">
          <p>
            Every number in this answer was computed against the warehouse load of{" "}
            <span className="font-medium">{header.watermark.loadedAt}</span> (newest fact
            date {mediumDate(header.watermark.newestDataDate)}).
          </p>
          <p className="mt-1.5 text-muted-foreground">
            Pack {header.packVersion.packId}@{header.packVersion.version}. Re-running at a
            newer watermark can change results; this session stays pinned until you
            re-anchor.
          </p>
        </ChipDoc>
      </Chip>
    </div>
  );
}

const BASIS_DOCS: Record<string, string> = {
  post: "money counts in the week its payment posted to the ledger, not when care happened.",
  service: "activity counts on the date of service.",
  submission: "claims count on the date they were submitted.",
  remit: "adjudication activity counts on the remit date of the 835.",
  discharge: "encounters count on the discharge date.",
};

function Chip({
  icon,
  label,
  trailing,
  accent,
  children,
}: {
  icon: ReactNode;
  label: string;
  trailing?: ReactNode;
  accent?: boolean;
  children: ReactNode;
}) {
  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex h-6 max-w-full items-center gap-1.5 rounded-full border bg-surface-sunken px-2.5 text-[0.7rem] font-medium text-secondary-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground",
            accent && "border-verified/40 bg-verified/10 text-verified hover:text-verified",
          )}
        >
          <span className="shrink-0 opacity-70">{icon}</span>
          <span className="num truncate">{label}</span>
          {trailing}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-3.5 text-xs leading-snug">
        {children}
      </PopoverContent>
    </Popover>
  );
}

function ChipDoc({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <p className="mb-1.5 text-[0.65rem] font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      {children}
    </div>
  );
}
