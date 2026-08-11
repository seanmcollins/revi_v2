"use client";

import { AlertTriangle, Check, ChevronDown, RotateCcw } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { HUMAN_LEAD_STATUSES, type LeadStatus } from "@/lib/monitors";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * WHERE A LEAD STANDS, and the one asymmetry that makes it worth having.
 *
 * Four of the six statuses are a person's to set. Two are not:
 * `resolved_confirmed` and `regressed` are verdicts the PLATFORM reaches
 * by re-running the lead's own drill across consecutive loads. "Mark as
 * resolved" everywhere else in this category is a checkbox, and a checkbox
 * is an opinion — so the menu offers exactly the four, and the two the
 * platform owns arrive as sentences with a measurement behind them.
 *
 * The sentence is shown VERBATIM. "ANM-031 is no longer in the detection
 * feed at wm_003 … that is 1 of the 2 consecutive loads the governed rule
 * requires" is the difference between a claim and a confirmation, and a
 * paraphrase would quietly turn the second back into the first.
 *
 * `open` renders nothing. It is the default and the honest reading of a
 * lead nobody has touched, and a chip on 33 of 33 cards would bury the
 * four somebody is actually working.
 */
export function LeadStatusControl({
  anomalyId,
  /** From the portfolio snapshot: where this lead stood at the load. */
  cardStatus,
  cardNote,
}: {
  anomalyId: string;
  cardStatus?: LeadStatus;
  cardNote?: string;
}) {
  const driver = useSessionStore((s) => s.driver);
  const live = useSessionStore((s) => s.leadStates[anomalyId]);
  const pendingId = useSessionStore((s) => s.leadPendingId);
  const leadError = useSessionStore((s) => s.leadError);
  const setLeadStatus = useSessionStore((s) => s.setLeadStatus);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");

  // A status changed in this browser thirty seconds ago is newer than the
  // snapshot, which was built when the load landed. The live record also
  // carries what the platform MEASURED, which the card's flat fields
  // cannot.
  const status: LeadStatus = live?.status ?? cardStatus ?? "open";
  const statusNote = live?.verificationNote || live?.note || cardNote || "";
  const pending = pendingId === anomalyId;
  const error = leadError?.anomalyId === anomalyId ? leadError.message : undefined;

  if (!driver?.setLeadStatus) {
    // No deployment to record a status on. The card still shows what the
    // snapshot said, because that is a published fact about the lead.
    return status === "open" ? null : <StatusLine status={status} note={statusNote} />;
  }

  return (
    <div className="mt-1 space-y-1">
      {status !== "open" && <StatusLine status={status} note={statusNote} />}

      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="ghost"
            size="xs"
            disabled={pending}
            aria-label={`Change where ${anomalyId} stands`}
            className={cn(
              "h-5 gap-0.5 rounded px-1.5 text-micro font-normal text-muted-foreground hover:text-foreground",
              // Quiet, and PRESENT. It was `opacity-0` until hover, which
              // is not quiet on a touch screen or a projector — it is
              // absent. The same repair as the two monitor affordances on
              // this page: the muted ink, not a hidden control (and not
              // an opacity step on that token — `contrast.test.ts` bans
              // those, because they drop 12px text under the AA floor).
              status === "open" && "transition-colors duration-150",
            )}
          >
            {pending ? "Saving…" : status === "open" ? "Set where this stands" : "Change"}
            <ChevronDown aria-hidden className="size-2.5" />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[20rem] max-w-[calc(100vw-2rem)] p-3">
          <p className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
            Where {anomalyId} stands
          </p>
          <div className="mt-1.5 space-y-0.5">
            {HUMAN_LEAD_STATUSES.map((option) => (
              <button
                key={option}
                type="button"
                disabled={pending}
                aria-current={status === option ? "true" : undefined}
                onClick={() => {
                  void setLeadStatus(anomalyId, option, note.trim());
                  setOpen(false);
                  setNote("");
                }}
                className={cn(
                  "focus-ring block w-full rounded-md px-2 py-1 text-left text-meta transition-colors duration-150 hover:bg-accent/60",
                  status === option && "bg-accent font-medium",
                )}
              >
                <span className="block">{STATUS_LABELS[option]}</span>
                <span className="block text-micro leading-snug text-muted-foreground">
                  {STATUS_DETAIL[option]}
                </span>
              </button>
            ))}
          </div>
          <label className="mt-2 block space-y-1">
            <span className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
              Note (optional)
            </span>
            <input
              type="text"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              placeholder="All 14 accounts billed on Jul 30."
              className="focus-ring w-full rounded-md border bg-background px-2 py-1 text-meta"
            />
          </label>
          <p className="mt-1.5 text-micro leading-snug text-muted-foreground">
            Claiming a fix does not close it. Revi keeps re-running this lead&apos;s own drill
            every load and tells you whether the data agrees.
          </p>
        </PopoverContent>
      </Popover>

      {error && (
        <p role="alert" className="flex items-start gap-1 text-micro leading-snug text-negative">
          <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
          {error}
        </p>
      )}
    </div>
  );
}

/**
 * The status, and the platform's own sentence about it.
 *
 * A confirmation and a regression are the two lines on this card a reader
 * should stop at, so they take the tone; the four a person set are stated
 * in the same ink as everything else, because "somebody is working this"
 * is a fact about a rota, not about the data.
 */
function StatusLine({ status, note }: { status: LeadStatus; note: string }) {
  const meta = STATUS_TONE[status];
  return (
    <div data-lead-status={status} className="space-y-0.5">
      <p className={cn("flex items-center gap-1 text-micro font-medium", meta.tone)}>
        {status === "resolved_confirmed" && <Check aria-hidden className="size-2.5 shrink-0" />}
        {status === "regressed" && <RotateCcw aria-hidden className="size-2.5 shrink-0" />}
        {meta.label}
      </p>
      {note !== "" && (
        // Verbatim. The verification sentence names the load, the rule and
        // how far along the streak is — and "could not verify" is a result,
        // not a silence.
        <p className="text-micro leading-snug text-muted-foreground">{note}</p>
      )}
    </div>
  );
}

const STATUS_LABELS: Readonly<Record<LeadStatus, string>> = {
  open: "Untouched",
  acknowledged: "Seen it",
  working: "Working it",
  resolved_claimed: "Fixed — check the data",
  resolved_confirmed: "Fix confirmed in the data",
  regressed: "It is back",
};

const STATUS_DETAIL: Readonly<Record<LeadStatus, string>> = {
  open: "Nobody has picked this up.",
  acknowledged: "Read and triaged, not started.",
  working: "Somebody is on it now.",
  resolved_claimed: "You believe it is fixed. Revi re-measures it every load from here.",
  resolved_confirmed: "The platform re-measured it and agrees.",
  regressed: "The platform re-measured it and it is back.",
};

const STATUS_TONE: Readonly<Record<LeadStatus, { label: string; tone: string }>> = {
  open: { label: "Untouched", tone: "text-muted-foreground" },
  acknowledged: { label: "Seen it", tone: "text-muted-foreground" },
  working: { label: "Working it", tone: "text-foreground/80" },
  resolved_claimed: { label: "Fixed — checking the data", tone: "text-foreground/80" },
  resolved_confirmed: { label: "Fix confirmed in the data", tone: "text-verified" },
  regressed: { label: "It is back", tone: "text-warning" },
};
