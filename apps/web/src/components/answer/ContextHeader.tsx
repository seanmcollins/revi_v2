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
 * window + basis · comparison · filters · cohort · data load. Each chip
 * opens a plain-language explanation of what it pins. The chip named
 * "Data as of" is the pinned WATERMARK; it is labelled the way an analyst
 * would say it, and the engine's own name for it is one debug toggle away.
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
      <Chip
        icon={<CalendarRange className="size-3" />}
        name="Window"
        label={windowChipLabel(header.window)}
      >
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
          name="vs"
          label={
            header.comparison.label ??
            comparisonChipLabel(header.comparison.window).replace(/^vs /, "")
          }
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
              Comparison windows are stored as concrete dates, so the same question
              always compares the same two periods.
            </p>
          </ChipDoc>
        </Chip>
      )}

      <Chip
        icon={<Filter className="size-3" />}
        name="Scope"
        label={
          header.filters.length === 0
            ? "all"
            : header.filters.map((f) => `${f.dimensionLabel}: ${f.values.join(", ")}`).join(" · ")
        }
      >
        <ChipDoc title="Scope">
          {header.filters.length === 0 ? (
            <p>
              No filters applied — every claim, payer, and facility in this data load is
              included.
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

      {header.cohort && <CohortChip cohort={header.cohort} />}

      {/* Same fact as before — the pinned data load — under the name an
          analyst uses for it. "Watermark" is the engine's word and stays
          available in debug mode and the settings panel. */}
      <Chip
        icon={<Database className="size-3" />}
        name="Data as of"
        label={header.watermark.loadedAt}
        trailing={pinnedEpoch ? <Pin className="size-2.5 text-muted-foreground" /> : undefined}
      >
        <ChipDoc title="Data as of">
          <p>
            Every number in this answer was computed against the data load of{" "}
            <span className="font-medium">{header.watermark.loadedAt}</span> (newest
            activity {mediumDate(header.watermark.newestDataDate)}).
          </p>
          <p className="mt-1.5 text-muted-foreground">
            Metric definitions {header.packVersion.packId}@{header.packVersion.version}.
            Re-running against a newer load can change the numbers; this session stays on
            this one until you choose to move it.
          </p>
        </ChipDoc>
      </Chip>
    </div>
  );
}

/**
 * The pinned population, said in words instead of shown as a hash.
 *
 * The chip used to read "312 payers (pinned)" over a `cohort` field that
 * held `coh_9f2a11…`, and both halves of that were wrong: the definition
 * was the id (unreadable, and a label that cannot be checked is decoration
 * on a platform whose whole claim is that the context is inspectable), and
 * "payers" was a hardcoded noun over a population whose grain the payload
 * actually publishes — these are claims, or lines, or remits.
 *
 * So the label is the INTENSIONAL definition (`payer in [State Medicaid,
 * Atlas Commercial, Meridian Health]`), the size is stated in its own
 * grain (`86,415 claims`), the origin referent and turn are a subtle
 * reference underneath, and the hash — which is a debugging handle, not a
 * name — sits in the popover.
 *
 * A turn that published only the header's id and size still renders: it
 * says the id and the size, and says the definition was not published,
 * rather than dressing the handle up as a definition.
 */
function CohortChip({ cohort }: { cohort: NonNullable<ContextHeaderData["cohort"]> }) {
  const grain = cohort.entityGrain ?? "";
  const sized = `${formatCount(cohort.size)} ${pluralGrain(grain, cohort.size)}`;
  return (
    <Chip
      icon={<Users className="size-3" />}
      name="Cohort"
      label={cohort.detailed ? cohort.definition : cohort.id}
      trailing={
        <span className="num shrink-0 text-[0.6rem] font-normal text-verified/70">{sized}</span>
      }
      accent
    >
      <ChipDoc title={cohort.pinned ? "Pinned cohort" : "Cohort"}>
        {cohort.detailed ? (
          <>
            <p>
              <span className="font-medium">{sized}</span> selected by{" "}
              <span className="font-medium">{cohort.definition}</span>.
            </p>
            {(cohort.windowStart || cohort.windowEnd) && (
              <p className="mt-1.5 text-muted-foreground">
                The definition carries its own window,{" "}
                {mediumDate(cohort.windowStart ?? "")} – {mediumDate(cohort.windowEnd ?? "")}.
              </p>
            )}
            {!cohort.windowStart && !cohort.windowEnd && (
              <p className="mt-1.5 text-muted-foreground">
                The definition carries no window of its own, so it covers this population
                across all time rather than only the window above.
              </p>
            )}
          </>
        ) : (
          <p>
            {sized}. This turn published the population&rsquo;s handle and its size, but not
            the rule that selected it — so the rule is not shown here rather than guessed at.
          </p>
        )}
        {cohort.pinned ? (
          <p className="mt-1.5 text-muted-foreground">
            Pinned{cohort.originReferent ? ` from ${cohort.originReferent}` : ""}
            {cohort.originTurn ? ` in ${cohort.originTurn}` : ""}
            {cohort.pinnedWatermarkId ? `, at data load ${cohort.pinnedWatermarkId}` : ""}: the
            member set is frozen — later turns reuse exactly these members even if the
            ranking would change.
          </p>
        ) : (
          <p className="mt-1.5 text-muted-foreground">
            Not pinned: the rule is re-evaluated against the data each turn, so the members
            can change.
          </p>
        )}
        {/* The handle, where a handle belongs — reachable when an
            operator needs it, never the name an analyst is shown. */}
        <p className="mt-1.5 font-mono text-[0.62rem] text-muted-foreground">{cohort.id}</p>
      </ChipDoc>
    </Chip>
  );
}

/**
 * The entity grain as a countable noun. The wire publishes the kernel's
 * own vocabulary (`claim`, `line`, `encounter`, `transaction`, `remit`,
 * `denial`), all of which pluralize with an "s" — and a grain this build
 * has never seen is printed as published rather than mangled by a rule
 * written for the ones it has. A payload with no grain says "entities",
 * which is honest: the size is real and what it counts was not stated.
 */
function pluralGrain(grain: string, size: number): string {
  if (grain === "") return size === 1 ? "entity" : "entities";
  if (size === 1) return grain;
  return grain.endsWith("s") ? grain : `${grain}s`;
}

const BASIS_DOCS: Record<string, string> = {
  post: "money counts in the week its payment posted to the ledger, not when care happened.",
  service: "activity counts on the date of service.",
  submission: "claims count on the date they were submitted.",
  remit: "adjudication activity counts on the remit date of the 835.",
  discharge: "encounters count on the discharge date.",
};

/**
 * A metadata chip: uppercase wide-tracked micro-label (WINDOW · DATA AS OF
 * · SCOPE) carrying the §10.3 verbatim value in tabular numerals. The
 * label names the dimension so the value never has to repeat it.
 */
function Chip({
  icon,
  name,
  label,
  trailing,
  accent,
  children,
}: {
  icon: ReactNode;
  name: string;
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
          <span
            className={cn(
              "shrink-0 text-[0.55rem] font-semibold uppercase tracking-[0.14em]",
              accent ? "text-verified/70" : "text-muted-foreground",
            )}
          >
            {name}
          </span>
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
