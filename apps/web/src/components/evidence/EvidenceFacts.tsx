"use client";

import { BarChart3, ChevronRight, ListTree } from "lucide-react";
import { useId, useState } from "react";

import { AnswerChart } from "@/components/answer/AnswerChart";
import { EVIDENCE_FACT_ANCHOR } from "@/components/answer/ReferentChip";
import { useAnswerModel } from "@/components/answer/useAnswerModel";
import { FactList } from "@/components/findings/FactRow";
import type { TurnRecord } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * The facts, in the rail — the calm layout's other half.
 *
 * "I would love the summary to be in the main display and maybe the facts
 * pop up in Evidence" is the owner's sketch, and this is the Evidence
 * side of it. The rail already held the best-organized surface in the
 * product (the question, the cache note, DATA CHECKS, DOES IT ADD UP?);
 * the facts are seated ABOVE those, because they are what a referent chip
 * in the writing points at and what a reader opens the rail for.
 *
 * Nothing about a fact is thinned on the way here. The rows carry the
 * ceiling marks, the populations, the withheld-impact refusals, the
 * grades and the drill each card carried — see `FactRow`. What changes is
 * only where they sit and how much room each one takes.
 *
 * Every row is an anchor (`evidence-fact-F1`), which is what makes the
 * chips in the narrative land on the right line rather than merely
 * opening the panel.
 */
export function EvidenceFacts({ turn }: { turn: TurnRecord }) {
  const model = useAnswerModel(turn);
  const total = turn.answer.findings.length;
  if (total === 0) return null;
  // The answer is already showing them, because it had no writing to show
  // instead. The same rows in both places on one screen is the repetition
  // this change exists to remove — see `AnswerModel.factsInline`.
  if (model.factsInline) return null;

  return (
    <section className="mb-4">
      <SectionTitle icon={<ListTree className="size-3" />}>
        Facts ({total})
      </SectionTitle>
      <FactList
        measured={model.measuredFindings}
        bounded={model.boundedFindings}
        turnId={turn.id}
        anchorPrefix={EVIDENCE_FACT_ANCHOR}
        totalFindings={total}
      />
    </section>
  );
}

/**
 * "3 more charts" — the figures the calm answer did not lead with.
 *
 * A turn publishes up to four frames and one of them answers the question
 * asked (`selectPrimaryChart`); the rest are supporting reads. They are
 * kept, whole, with their own CSV and every annotation they carry — one
 * disclosure away rather than three screens down.
 *
 * Closed by default: a rail that opens with four figures in a 21rem
 * column is the wall of charts moved sideways.
 */
export function EvidenceCharts({ turn }: { turn: TurnRecord }) {
  const model = useAnswerModel(turn);
  const [open, setOpen] = useState(false);
  const listId = useId();
  const charts = model.secondaryCharts;
  if (charts.length === 0) return null;

  return (
    <section className="mb-4">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={listId}
        className="focus-ring flex w-full items-center gap-1.5 rounded text-micro font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
      >
        <ChevronRight
          aria-hidden
          className={cn("size-3 transition-transform duration-150", open && "rotate-90")}
        />
        <BarChart3 aria-hidden className="size-3" />
        {charts.length} more chart{charts.length === 1 ? "" : "s"}
      </button>
      {open && (
        <div id={listId} className="mt-2 space-y-2.5">
          {charts.map((spec) => (
            <AnswerChart key={spec.id} turn={turn} model={model} spec={spec} />
          ))}
        </div>
      )}
    </section>
  );
}

function SectionTitle({
  icon,
  children,
}: {
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <h4 className="mb-1.5 flex items-center gap-1.5 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
      {icon}
      {children}
    </h4>
  );
}
