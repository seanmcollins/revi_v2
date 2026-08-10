"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { DeltaLine, ThresholdNote } from "@/components/rounds/DeltaLine";
import { IntegrityAtom } from "@/components/rounds/IntegrityAtom";
import { TimeToImpactLine } from "@/components/rounds/TimeToImpactLine";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatWholeDollars } from "@/lib/format";
import { investigationLinkFor } from "@/lib/links";
import type { BriefEntry, BriefEntryKind } from "@/lib/rounds";
import { cn } from "@/lib/utils";

/**
 * ONE LINE OF THE BRIEF.
 *
 * The unit of this surface is a SENTENCE, not a metric with a delta. A
 * list of metrics with deltas is the report an analyst already ignores;
 * what makes this feel like an investigator is that each line is a
 * statement about the world — "Denial rate by payer, monthly: 29.5% at
 * wm_003, up 3.6 points from 25.9%" — composed server-side from the
 * payload and rendered here verbatim.
 *
 * Under it, in the quietest ink, the things that decide how to read it:
 * the integrity atom (a Round has no asker in the room, so the grade rides
 * on every entry that has one), the cash timing, and whose threshold
 * briefed it.
 *
 * The eyebrow is the entry's KIND plus who said it — the detection feed,
 * or this platform's own re-run of a stored spec. A brief is the surface
 * furthest from the evidence: somebody reads it over coffee, so each line
 * has to be able to say which system asserted it and by what method.
 */
export function BriefEntryRow({ entry }: { entry: BriefEntry }) {
  const kind = KINDS[entry.kind];

  return (
    <li data-brief-entry={entry.kind} className="relative">
      {/* The mark sits ON the spine, not beside it: the offset is the
          list's own left padding plus half the mark, so the dot is
          centred on the hairline. A mark floating next to a rule is two
          devices; a mark on it is one walk with stops.

          Its tone is the only place kind is carried by colour, and only
          for the two entries that are verdicts about somebody's work —
          everything else takes the hairline ink, because a brief where
          every line is a coloured badge is the console this product is
          positioned against. */}
      <span
        aria-hidden
        className={cn(
          "absolute -left-[1.19rem] top-[0.45rem] size-1.5 rounded-full ring-2 ring-background",
          kind.mark,
        )}
      />
      <p className="flex flex-wrap items-baseline gap-x-1.5 text-micro uppercase tracking-widest text-muted-foreground">
        <span className={cn("font-semibold", kind.tone)}>{kind.label}</span>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="focus-ring rounded normal-case tracking-normal underline decoration-dotted underline-offset-2 hover:text-foreground"
            >
              {entry.provenance.source === "detection_feed"
                ? "detected this load"
                : "your watch, re-run at this load"}
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-80 text-meta leading-snug">
            {/* How this entry was decided, in the server's own sentence. */}
            {entry.provenance.method}
            {entry.provenance.formulaVersion && (
              <span className="mt-1 block text-micro text-muted-foreground">
                ranked by {entry.provenance.formulaVersion}
              </span>
            )}
          </TooltipContent>
        </Tooltip>
      </p>

      {/* THE SENTENCE. Composed from the payload server-side and never by
          a model — and never re-worded here, because every figure in it is
          a figure the platform measured. */}
      <p className="mt-0.5 max-w-[68ch] text-body leading-relaxed text-foreground">
        {entry.statement}
      </p>

      {entry.delta && <DeltaLine delta={entry.delta} className="mt-1" />}

      {entry.integrity && (
        <IntegrityAtom
          integrity={entry.integrity}
          // A brief entry publishes its marks and its counts, not the
          // caveat sentences themselves — those live on the investigation
          // the entry links to. The atom states the count it was given and
          // does not offer a sheet it cannot fill.
          warnings={[]}
          className="mt-1"
        />
      )}

      <p className="num mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-micro text-muted-foreground">
        {entry.impactCents !== undefined && (
          <span className="text-foreground/80">{formatWholeDollars(entry.impactCents)}</span>
        )}
        {entry.timeToImpact && <TimeToImpactLine timeToImpact={entry.timeToImpact} />}
        {entry.delta && <ThresholdNote delta={entry.delta} />}
        {entry.investigationId && (
          <Link
            href={investigationLinkFor(entry.investigationId, "")}
            className="focus-ring group/link inline-flex items-center gap-1 rounded underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
          >
            Open the investigation
            <ArrowRight
              aria-hidden
              className="size-2.5 transition-transform duration-150 group-hover/link:translate-x-0.5"
            />
          </Link>
        )}
      </p>
    </li>
  );
}

/**
 * What each kind of change is CALLED, in the reader's nouns.
 *
 * Two of the five are verdicts about work somebody did — the platform
 * re-measured a claimed fix and either agreed or found it back — and those
 * two carry a tone. The other three are things that happened, and a
 * coloured badge on each of them would make the two that matter invisible.
 */
const KINDS: Readonly<Record<BriefEntryKind, { label: string; mark: string; tone: string }>> = {
  new_lead: { label: "New lead", mark: "bg-foreground/40", tone: "text-foreground/80" },
  pin_movement: { label: "A watch moved", mark: "bg-foreground/40", tone: "text-foreground/80" },
  self_resolved: {
    label: "Gone on its own",
    mark: "bg-muted-foreground/50",
    tone: "text-muted-foreground",
  },
  resolution_confirmed: {
    label: "Fix confirmed",
    mark: "bg-verified",
    tone: "text-verified",
  },
  resolution_regressed: { label: "Back again", mark: "bg-warning", tone: "text-warning" },
};
