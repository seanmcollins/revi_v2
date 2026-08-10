"use client";

import { ArrowRight } from "lucide-react";

import { LeadStatusControl } from "@/components/rounds/LeadStatus";
import { formatWholeDollars } from "@/lib/format";
import type { LeadState, LeadStatus } from "@/lib/rounds";

/** One lead, as this surface needs it: where it stands and how to open it. */
export interface LeadRow {
  anomalyId: string;
  title: string;
  status: LeadStatus;
  /** The platform's own sentence about this status, verbatim. */
  note: string;
  impactCents?: number;
  /** Opens the lead's own drill investigation. */
  open?: () => void;
  /** The live record, when this browser has one — it carries the streak. */
  live?: LeadState;
}

/**
 * THE LEAD LIFECYCLE — what we claimed we fixed, and whether it stuck.
 *
 * The standing question an RCM director has every morning, and until now
 * it had no home on the surface built to answer standing questions. Rounds
 * had exactly two zones — this load's brief and the watches — and both are
 * DIFFS: a lead only appears if it moved at this load. So ANM-031 sitting
 * at "1 of the 2 consecutive loads the governed rule requires", and
 * ANM-021 marked working with a director's note, appeared nowhere unless
 * they happened to change.
 *
 * This zone is the other axis: state rather than change. It is rendered
 * from the load's own worklist snapshot (which publishes `lead_status` and
 * the platform's sentence on every card) merged with whatever this browser
 * has changed since, so a status set thirty seconds ago is not waiting for
 * the next data load to appear.
 *
 * THREE GROUPS, IN THE ORDER A MORNING SHOULD READ THEM. A regression is
 * work that came back and somebody believes is done; a claim awaiting
 * confirmation is the platform mid-verdict; work in progress is the rota.
 * Confirmed fixes come last — they are the good news, and the good news
 * does not go at the top of a worklist.
 *
 * Untouched leads are NOT here. They are the worklist, they are thirty of
 * thirty-three, and a lifecycle view that listed them would be the
 * worklist with extra steps.
 */
export function LeadLifecyclePanel({
  leads,
  totalLeads,
  headingId,
}: {
  leads: readonly LeadRow[];
  /** Every lead on this load, so the census can say what is not here. */
  totalLeads: number;
  headingId: string;
}) {
  const groups = GROUPS.map((group) => ({
    ...group,
    rows: leads.filter((lead) => group.statuses.includes(lead.status)),
  })).filter((group) => group.rows.length > 0);

  const claimed = leads.length;

  return (
    <section aria-labelledby={headingId} className="max-w-4xl space-y-3">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id={headingId}
          tabIndex={-1}
          className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
        >
          Leads somebody is working
        </h2>
        {totalLeads > 0 && (
          <span className="num text-micro text-muted-foreground">
            {claimed} of {totalLeads} leads have somebody on them
          </span>
        )}
      </header>

      {groups.length === 0 ? (
        <p className="max-w-[64ch] text-meta leading-snug text-muted-foreground">
          Nobody has picked up a lead yet. Set where one stands from the worklist or from a
          brief entry above, and Revi re-runs its own drill every load to tell you whether the
          data agrees.
        </p>
      ) : (
        <div className="space-y-4">
          {groups.map((group) => (
            <section key={group.id} className="space-y-1.5">
              <h3 className="text-micro font-medium text-muted-foreground">
                {group.label} ({group.rows.length})
              </h3>
              <ul className="space-y-1.5">
                {group.rows.map((lead) => (
                  <LeadLifecycleRow key={lead.anomalyId} lead={lead} />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </section>
  );
}

function LeadLifecycleRow({ lead }: { lead: LeadRow }) {
  const streak = confirmationStreak(lead);
  return (
    <li
      data-lifecycle-lead={lead.anomalyId}
      className="group rounded-xl border bg-surface-raised p-2.5 raised raised-hover transition-[border-color,box-shadow] duration-200 hover:border-ring/40 focus-within:border-ring/40"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <p className="min-w-0 text-meta font-medium leading-snug">
          <span className="num mr-1.5 text-micro text-muted-foreground">{lead.anomalyId}</span>
          {lead.title}
        </p>
        <span className="num shrink-0 text-micro text-muted-foreground">
          {lead.impactCents !== undefined && formatWholeDollars(lead.impactCents)}
        </span>
      </div>

      {/* HOW FAR ALONG THE VERDICT IS. A claim is not a fix: the platform
          re-runs this lead's own drill every load and only agrees after
          the governed number of consecutive loads. The streak is the
          difference between "somebody said so" and "the data says so". */}
      {streak && <p className="num mt-0.5 text-micro text-muted-foreground">{streak}</p>}

      <LeadStatusControl anomalyId={lead.anomalyId} cardStatus={lead.status} cardNote={lead.note} />

      {lead.open && (
        <button
          type="button"
          onClick={lead.open}
          className="focus-ring group/link mt-1 inline-flex items-center gap-1 rounded text-micro text-muted-foreground underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
        >
          Open this lead
          <ArrowRight
            aria-hidden
            className="size-2.5 transition-transform duration-150 group-hover/link:translate-x-0.5"
          />
        </button>
      )}
    </li>
  );
}

/**
 * "1 of the 2 consecutive loads the governed rule requires" — from the
 * live record, which is the only place the streak exists.
 *
 * The card carries the status and the platform's sentence; the counts ride
 * on the lead record returned when somebody changes it. No record, no
 * count — a streak this client cannot read is not one it may estimate.
 */
function confirmationStreak(lead: LeadRow): string | null {
  const live = lead.live;
  if (!live || live.confirmationsRequired <= 0) return null;
  if (lead.status !== "resolved_claimed" && lead.status !== "resolved_confirmed") return null;
  return `${live.confirmingWatermarks.length} of the ${live.confirmationsRequired} consecutive loads the governed rule requires`;
}

const GROUPS: ReadonlyArray<{ id: string; label: string; statuses: LeadStatus[] }> = [
  { id: "regressed", label: "It came back", statuses: ["regressed"] },
  {
    id: "claimed",
    label: "Claimed fixed — Revi is checking the data",
    statuses: ["resolved_claimed"],
  },
  { id: "working", label: "Being worked", statuses: ["working", "acknowledged"] },
  { id: "confirmed", label: "Confirmed fixed in the data", statuses: ["resolved_confirmed"] },
];
