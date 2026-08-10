"use client";

import { InvestigationChart } from "@/components/charts/InvestigationChart";
import type { AnswerModel } from "@/components/answer/useAnswerModel";
import type { TurnRecord } from "@/lib/store";
import type { ChartSpec } from "@/lib/types";

/**
 * One figure, with everything the turn knows about it attached.
 *
 * The window it was measured over, the two windows a comparison names,
 * the data load and metric pack for the CSV's provenance, the question it
 * answered, and the turn's caveats — so a chart exported from the answer
 * and the same chart exported from the Evidence rail carry an identical
 * preamble. Three layouts and two surfaces draw these; assembling the
 * props in each of them is how they would come to disagree.
 */
export function AnswerChart({
  turn,
  model,
  spec,
}: {
  turn: TurnRecord;
  model: AnswerModel;
  spec: ChartSpec;
}) {
  const a = turn.answer;
  return (
    <InvestigationChart
      spec={spec}
      turnId={turn.id}
      {...(model.windowLabel !== undefined ? { windowLabel: model.windowLabel } : {})}
      {...(model.comparisonWindows ? { comparisonWindows: model.comparisonWindows } : {})}
      {...(a.header?.watermark.id ? { watermarkId: a.header.watermark.id } : {})}
      {...(a.header
        ? { packLabel: `${a.header.packVersion.packId}@${a.header.packVersion.version}` }
        : {})}
      {...(turn.submission.utterance ? { question: turn.submission.utterance } : {})}
      {...(a.investigationId ? { investigationId: a.investigationId } : {})}
      caveats={model.csvCaveats}
    />
  );
}
