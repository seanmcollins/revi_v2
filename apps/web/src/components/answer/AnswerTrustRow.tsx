"use client";

import { FileSearch, Zap } from "lucide-react";

import { CopyTextButton } from "@/components/answer/AnswerActions";
import { GradeBadge } from "@/components/answer/GradeBadge";
import {
  hasGovernedProvenance,
  MetricProvenanceBadge,
} from "@/components/answer/MetricProvenanceBadge";
import type { AnswerModel } from "@/components/answer/useAnswerModel";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { answerToText } from "@/lib/export";
import { useSessionStore, type TurnRecord } from "@/lib/store";

/**
 * The row of trust badges and the two actions that take an answer out of
 * the browser.
 *
 * Extracted so the three layouts share one copy of the hardest thing on
 * it to get right — `answerToText`, which must hand over the findings,
 * the write-up and EVERY caveat that bounds them, in the same complete
 * format on every layout. The A/B toggle changes where a caveat is read;
 * it does not change what leaves in the export. That is the whole point
 * of putting this in one file.
 *
 * Rendered by the two layouts that keep the chip row. The calm layout
 * mounts none of it: "Governed" and the answer grade are said in words on
 * the integrity line (see `verificationClause`), the Evidence entry is on
 * that line too, and the cache note has moved into the rail. This file
 * used to carry a `showBadges` flag documenting that arrangement — no
 * caller ever passed it, and the docstring asserting that the grade was
 * "said in words on the integrity line" was how the grade came to be
 * dropped from the default surface without anyone noticing. The flag is
 * gone and the behaviour it described is now built.
 */
export function AnswerTrustRow({
  turn,
  model,
}: {
  turn: TurnRecord;
  model: AnswerModel;
}) {
  const openDrawer = useSessionStore((s) => s.openDrawer);
  const a = turn.answer;

  const badges = a.answerGrade || hasGovernedProvenance(a.metric) || a.evidence?.zeroProbeTurn;
  if (!badges && !a.evidence && !model.copyable) return null;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {/* A turn that measured nothing governed (definitional, META)
          publishes the block with an empty metric list — no badge,
          because there is no governed contract to point at. */}
      {a.metric && <MetricProvenanceBadge metric={a.metric} />}
      {a.answerGrade && <GradeBadge grade={a.answerGrade} />}
      {/* Two different facts wearing one flag. `zeroProbeTurn` is
          `warehouseQueries === 0`, which is ALSO true of a META or
          definitional turn that never had a probe to cache — and
          "answered from cached results" over a turn that read nothing
          claims a reuse that never happened. The cache hit count is
          what separates them, so the copy gates on it. */}
      {a.evidence?.zeroProbeTurn && (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="focus-ring inline-flex h-5 items-center gap-1 rounded-full border border-verified/40 bg-verified/10 px-2 text-micro font-medium text-verified"
            >
              <Zap className="size-3" />
              {a.evidence.cacheHits > 0 ? (
                <>
                  Answered from results already computed
                  {a.header?.watermark.id ? " (same data load)" : ""} — your data was not read
                  again
                </>
              ) : (
                "This answer needed no reading of your data"
              )}
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-80 text-micro leading-snug">
            {a.evidence.cacheHits > 0
              ? "Every check this answer needed had already run earlier in this session, at this same data load. Your data was not read again — the numbers are not newer than the ones above them."
              : "This answer needed no reading of your data at all — there was no check to run, so there was nothing to read and nothing to reuse."}
          </TooltipContent>
        </Tooltip>
      )}
      {a.evidence && (
        <Button
          variant="ghost"
          size="xs"
          className="h-5 gap-1 rounded-full px-2 text-micro font-normal text-muted-foreground hover:text-foreground"
          onClick={() => openDrawer(turn.id)}
        >
          <FileSearch className="size-3" />
          Evidence
        </Button>
      )}
      {/* An analyst who has to quote a figure in a meeting should not have
          to retype it — and a figure retyped by hand arrives without the
          window, the scope or the caveats that bound it. This copies the
          whole answer, caveats included, from payload already in this
          browser: nothing is fetched and nothing is uploaded. */}
      {model.copyable && (
        <CopyTextButton
          label="Copy answer"
          title="Copy this answer as text — findings, the written analysis, every caveat Revi attached, and a line naming the data load and the definitions it was measured against. Nothing leaves this browser."
          text={() =>
            answerToText({
              ...(turn.submission.utterance ? { question: turn.submission.utterance } : {}),
              ...(a.header ? { header: a.header } : {}),
              findings: a.findings,
              // The prose exactly as the page shows it — the caveats it
              // repeated are printed in full in the CAVEATS block below
              // it, so the artifact loses no reasoning and gains no
              // duplicate.
              narrative: model.prose.text,
              warnings: a.warnings,
              ...(model.charts.length > 0 ? { charts: model.charts } : {}),
              ...(a.worklist ? { worklist: a.worklist } : {}),
              ...(a.metric ? { metric: a.metric } : {}),
              ...(a.investigationId ? { investigationId: a.investigationId } : {}),
              ...(a.rehydrated ? { restored: true } : {}),
              ...(a.header?.restorationNotes
                ? { restorationNotes: a.header.restorationNotes }
                : {}),
            })
          }
        />
      )}
    </div>
  );
}
