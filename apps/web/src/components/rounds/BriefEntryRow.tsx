"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";

import { DeltaLine, ThresholdNote } from "@/components/rounds/DeltaLine";
import { IntegrityAtom } from "@/components/rounds/IntegrityAtom";
import { LeadStatusControl } from "@/components/rounds/LeadStatus";
import { TimeToImpactLine } from "@/components/rounds/TimeToImpactLine";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatWholeDollars } from "@/lib/format";
import { humanizeColumn } from "@/lib/humanize";
import { investigationLinkFor } from "@/lib/links";
import type { BriefEntry, LeadStatus } from "@/lib/rounds";
import { cn } from "@/lib/utils";

/**
 * WHAT A BRIEF ENTRY CAN BE OPENED WITH, when the entry carries no
 * investigation of its own.
 *
 * Three of four live brief rows carried `investigation_id: null` — the two
 * new leads at $17,677.33 and $15,181.68, and the self-resolved one — so
 * 75% of the brief, holding the largest dollar figures on it, was a
 * notification: a four-figure number and a confident sentence with nothing
 * to open. This module's own standard says it in one line: "the permalink.
 * An entry a reader cannot open is a notification."
 *
 * The floor is the lead's own drill. It already exists — the platform
 * re-derives it every load to verify claimed resolutions — and the
 * worklist card carries the typed spec that opens it, so a brief row can
 * hand the reader the same destination the worklist does. When the card
 * cannot be drilled the platform says why, and that sentence is rendered
 * instead of a dead control.
 */
export interface BriefLeadHandle {
  /** Where this lead stands, as the load's own snapshot has it. */
  status?: LeadStatus;
  /** The platform's own sentence about that status, verbatim. */
  note?: string;
  /** Opens the lead's own drill investigation. Absent when it has none. */
  open?: () => void;
  /** Why there is nothing to open, in the platform's words. */
  unavailableReason?: string;
}

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
 * And under THAT, the two things that make it a piece of work rather than
 * a notification: somewhere to open, and where the lead stands. A brief
 * that reports a $17,677 lead as new while somebody else has already
 * claimed the fix is two people working one lead on one morning.
 *
 * The eyebrow is the entry's KIND plus who said it — the detection feed,
 * or this platform's own re-run of a stored spec. A brief is the surface
 * furthest from the evidence: somebody reads it over coffee, so each line
 * has to be able to say which system asserted it and by what method.
 */
export function BriefEntryRow({
  entry,
  lead,
}: {
  entry: BriefEntry;
  /** The lead behind this entry, when this load's worklist carries it. */
  lead?: BriefLeadHandle;
}) {
  const kind = KINDS[entry.kind] ?? unknownKind(entry.kind);
  // The status the row should speak in: what this browser changed a minute
  // ago wins over the brief, which was composed when the load landed.
  const status = lead?.status ?? entry.leadStatus;

  return (
    <li data-brief-entry={entry.kind} className="group relative">
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
        <span className={cn("font-semibold", kind.tone)}>
          {kind.label}
          {/* A lead somebody has already picked up is not "new" to the
              team, whatever it is to the detector. The eyebrow says so
              rather than leaving the reader to find it three lines down. */}
          {statusSuffix(entry.kind, status) && (
            <span className="font-normal text-muted-foreground">
              {statusSuffix(entry.kind, status)}
            </span>
          )}
        </span>
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

      {entry.integrity ? (
        <IntegrityAtom
          integrity={entry.integrity}
          // A brief entry publishes its marks and its counts, not the
          // caveat sentences themselves — those live on the investigation
          // the entry links to. The atom states the count it was given and
          // does not offer a sheet it cannot fill.
          warnings={[]}
          className="mt-1"
        />
      ) : (
        entry.impactCents !== undefined && (
          // NO ATOM, AND THE REASON FOR IT. A four-figure sum with no
          // grade beside it reads as a measured figure on the one surface
          // with nobody in the room to ask. This says whose number it is:
          // the detection feed's own, carried onto the brief, not
          // something this platform re-measured at this load.
          <p className="mt-1 text-micro leading-snug text-muted-foreground">
            The detector&apos;s own figure — not re-measured at this load.
          </p>
        )
      )}

      <p className="num mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-micro text-muted-foreground">
        {entry.impactCents !== undefined && (
          <span className="text-foreground/80">{formatWholeDollars(entry.impactCents)}</span>
        )}
        {entry.timeToImpact && <TimeToImpactLine timeToImpact={entry.timeToImpact} />}
        {entry.delta && <ThresholdNote delta={entry.delta} />}
        {/* THE DESTINATION. The entry's own investigation when it has one;
            the lead's drill when it does not; and the platform's sentence
            when neither exists — never a row that ends in nothing. */}
        {entry.investigationId ? (
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
        ) : lead?.open ? (
          <button
            type="button"
            onClick={lead.open}
            className="focus-ring group/link inline-flex items-center gap-1 rounded underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
          >
            Open this lead
            <ArrowRight
              aria-hidden
              className="size-2.5 transition-transform duration-150 group-hover/link:translate-x-0.5"
            />
          </button>
        ) : lead?.unavailableReason ? (
          <span className="text-muted-foreground">
            Cannot be opened: {lead.unavailableReason}
          </span>
        ) : null}
      </p>

      {/* WHERE THIS LEAD STANDS, on the row that reports it. The control
          is the worklist's own — one gesture, one vocabulary, two
          surfaces — so a lead claimed from Rounds is claimed everywhere. */}
      {entry.anomalyId && (
        <LeadStatusControl
          anomalyId={entry.anomalyId}
          {...(status ? { cardStatus: status } : {})}
          {...(lead?.note ? { cardNote: lead.note } : {})}
        />
      )}
    </li>
  );
}

/**
 * What each kind of change is CALLED, in the reader's nouns.
 *
 * Two of the six are verdicts about work somebody did — the platform
 * re-measured a claimed fix and either agreed or found it back — and those
 * two carry a tone. The others are things that happened, and a coloured
 * badge on each of them would make the two that matter invisible.
 */
const KINDS: Readonly<Record<string, { label: string; mark: string; tone: string }>> = {
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
  // NOT a movement, and deliberately not worded like one: the cell a
  // ranked watch headlines is a different cell from last load's. "Your
  // worst payer changed" is the headline the phantom delta it replaces was
  // pretending to be.
  rank_flip: {
    label: "A different cell now leads",
    mark: "bg-foreground/40",
    tone: "text-foreground/80",
  },
};

/**
 * A kind this build has never seen, named from its own id.
 *
 * The alternative was dropping the entry, which made this client's
 * vocabulary a filter on the server's facts. The statement underneath is
 * the server's and says what happened; the eyebrow says as much as an id
 * honestly can.
 */
function unknownKind(kind: string): { label: string; mark: string; tone: string } {
  return {
    label: humanizeColumn(kind),
    mark: "bg-muted-foreground/50",
    tone: "text-muted-foreground",
  };
}

/**
 * "New lead, already claimed" — the eyebrow's second clause.
 *
 * Only on the kinds where the status changes what the line MEANS to the
 * team. A confirmation or a regression is already a statement about the
 * lifecycle, so repeating the status on it would be the same fact twice.
 */
function statusSuffix(kind: string, status: LeadStatus | undefined): string {
  if (status === undefined || status === "open") return "";
  if (kind !== "new_lead" && kind !== "self_resolved") return "";
  switch (status) {
    case "acknowledged":
      return ", already seen";
    case "working":
      return ", already being worked";
    case "resolved_claimed":
      return ", already claimed fixed";
    case "resolved_confirmed":
      return ", already confirmed fixed";
    case "regressed":
      return ", and it had come back before";
    default:
      return "";
  }
}
