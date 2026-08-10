"use client";

import { AlertTriangle, History } from "lucide-react";

import { AnswerBodyCalm } from "@/components/answer/AnswerBodyCalm";
import { AnswerBodyCurrent } from "@/components/answer/AnswerBodyCurrent";
import { AnswerBodyDetailed } from "@/components/answer/AnswerBodyDetailed";
import { useAnswerModel } from "@/components/answer/useAnswerModel";
import { ClarificationPrompt } from "@/components/clarification/ClarificationPrompt";
import { DebugTracePanel } from "@/components/debug/DebugTracePanel";
import { FeedbackTriage } from "@/components/feedback/FeedbackTriage";
import { WatchDeclarationNote } from "@/components/rounds/WatchDeclarationNote";
import { StageRail } from "@/components/chat/StageRail";
import { AnswerWorklist } from "@/components/worklist/AnswerWorklist";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSessionStore, type TurnRecord } from "@/lib/store";
import { useAnswerVariant } from "@/lib/useAnswerVariant";
import { cn } from "@/lib/utils";
import { splitErrorMessage } from "@/lib/warnings";

/**
 * The composed answer.
 *
 * This file is the SHELL — the parts every layout of an answer shares:
 * what a screen reader is told, where the turn came from (streamed or
 * restored), the ranked worklist, a clarification, a refusal, the
 * decision trace, the feedback control. The body between them is the
 * layout, and there are three:
 *
 *   `b`        THE CALM ANSWER, and the default — the writing is the
 *              answer, the facts are in the Evidence rail, one chart, one
 *              integrity line carrying the grade, the caveat count and
 *              how many of those caveats change a reading.
 *   `a`        the refined current — narrative above findings, cautions
 *              grouped, findings as rows. On the toggle, and the fallback.
 *   `current`  the pre-A/B layout. Retired from the toggle, kept in the
 *              code and at `?variant=current` for one round.
 *
 * Selection is `?variant=` / localStorage / ⌘K (see `lib/answerVariant`).
 * The A/B is decided: three reviewers returned B_with_conditions at high
 * confidence, unanimously, and the conditions are built.
 *
 * Every derivation the three bodies could disagree about — which warnings
 * are the verdict, which are the "things to know", what the prose is once
 * the banners have taken their sentences back, which chart is primary —
 * is computed once in `useAnswerModel` and handed to all of them.
 */
export function AnswerCard({ turn, active = false }: { turn: TurnRecord; active?: boolean }) {
  const debug = useSessionStore((s) => s.settings.debug);
  const variant = useAnswerVariant();
  const model = useAnswerModel(turn);
  const a = turn.answer;

  /**
   * The governed conversation→worklist bridge.
   *
   * Not what this turn measured — it is the ranked work the platform
   * already had, handed over because the question routed to it. Rendered
   * on a CLARIFICATION too.
   *
   * WHERE it renders is a layout decision, so the block is built once
   * here and placed by each layout. The calm layout takes it as a slot
   * and seats it above its integrity line: that line is the answer's
   * closing signature, and thirty-three ranked cards after it left the
   * signature mid-page on the flagship proactive question. The other two
   * end with their own anatomy, so it stays below them.
   */
  const worklistBlock = a.worklist ? (
    <div className="fade-up">
      <AnswerWorklist
        worklist={a.worklist}
        {...(model.worklistIntro ? { intro: model.worklistIntro } : {})}
        // The turn this list arrived on, so the block can offer to watch
        // the slice it is showing. A worklist is a ranked population that
        // moves every load, which makes it the artifact on this page most
        // worth being told about.
        {...(a.investigationId ? { investigationId: a.investigationId } : {})}
      />
    </div>
  ) : null;

  return (
    <div
      className={cn("space-y-3", active && "relative isolate")}
      data-answer-variant={variant}
      // The pipeline is running and this region is not finished changing.
      aria-busy={model.streaming || undefined}
    >
      {/* Off-screen, polite, and the only thing on the answer path that
          speaks: one sentence when the turn lands. */}
      <p role="status" aria-live="polite" className="sr-only">
        {model.completionMessage}
      </p>
      {/* Depth model: a faint accent glow marks the answer being read. */}
      {active && (
        <div
          aria-hidden
          className="answer-glow pointer-events-none absolute -inset-x-10 -top-8 -z-10 h-80"
        />
      )}
      {/* A turn rebuilt when this session was re-opened was never watched
          running, and the server keeps no stage timings — so it says where
          it came from instead of drawing a pipeline nobody observed.

          BUG 3 — said ONCE. The calm layout carries the restored mark on
          its context line, so this line stands down there; in the other
          two it is this line, and the header chip stands down instead. */}
      {a.rehydrated ? (
        variant === "b" ? null : (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                className="focus-ring flex items-center gap-1.5 rounded text-micro text-muted-foreground hover:text-foreground"
              >
                <History className="size-3" />
                Restored from this session&apos;s history
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom" className="max-w-80 text-micro leading-snug">
              Re-opening a session replays what the server kept: this turn&apos;s findings, its
              charts (rebuilt from the frames it stored), its evidence bundle (projected from
              its recorded trace) and — when the store holds them — its stated context and
              written analysis. Its stage timings were never persisted.
            </TooltipContent>
          </Tooltip>
        )
      ) : (
        <StageRail
          stages={a.stages}
          streaming={model.streaming}
          cacheHits={a.cacheHits}
          debug={debug}
        />
      )}

      {/* A WATCH WAS STARTED BY THIS TURN. Above the answer rather than
          below it, and in all three layouts: the answer is what was asked
          for, but a state now exists on the server that will interrupt
          this person tomorrow morning, and that belongs where they will
          see it before they scroll. */}
      {a.watch && <WatchDeclarationNote watch={a.watch} />}

      {variant === "b" ? (
        <AnswerBodyCalm
          turn={turn}
          model={model}
          debug={debug}
          {...(worklistBlock ? { worklist: worklistBlock } : {})}
        />
      ) : variant === "a" ? (
        <AnswerBodyDetailed turn={turn} model={model} debug={debug} />
      ) : (
        <AnswerBodyCurrent turn={turn} model={model} debug={debug} />
      )}

      {variant !== "b" && worklistBlock}

      {a.clarification && <ClarificationPrompt clarification={a.clarification} />}

      {a.error && <TurnErrorCard error={a.error} />}

      {/* Debug mode: how this turn was decided, from the server's own
          recorded trace. Never rendered in the default experience. */}
      {debug && !model.streaming && <DebugTracePanel turnId={turn.id} answer={a} />}

      {a.status === "complete" && (
        <div className="border-t pt-2">
          <FeedbackTriage turnId={turn.id} />
        </div>
      )}
    </div>
  );
}

/**
 * A refused or failed turn.
 *
 * Two things it says that it did not. First, WHICH budget stopped it:
 * `QUERY_BUDGET_EXCEEDED` is two failures wearing one code, and the
 * envelope's `subcode` separates them. A warehouse-read stop means the
 * question read too much and the recovery is a narrower question; a
 * model-spend stop means the question was fine and the wallet was the
 * constraint. The server's own sentence already differs per subcode, so
 * what is added here is the NEXT STEP — a spend stop is fixed from the
 * settings panel, one click away rather than one guess away.
 *
 * Second, what the failure cost. `TurnError.usage` publishes the spend of
 * a turn that errored after its model calls; a card that shows only the
 * refusal is quietly under-reporting the bill.
 */
function TurnErrorCard({ error }: { error: NonNullable<TurnRecord["answer"]["error"]> }) {
  const openSettings = useSessionStore((s) => s.openSettings);
  const debug = useSessionStore((s) => s.settings.debug);
  const spendStop = error.subcode === "MODEL_SPEND_BUDGET";
  const readStop = error.subcode === "WAREHOUSE_READ_BUDGET";
  const heading = spendStop
    ? "This turn hit its model-spend ceiling"
    : readStop
      ? "This question reads more of the warehouse than one turn allows"
      : undefined;
  // The server's sentence, and only the server's sentence. The bracketed
  // machine tail repeats the code chip beside it and prints raw ids and
  // list literals into the one place a reader looks for what to do next.
  const { sentence, machine } = splitErrorMessage(error.code, error.message);

  return (
    <div className="flex items-start gap-2 rounded-md border border-negative/50 bg-negative/10 px-3 py-2 text-meta">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-negative" />
      <div className="min-w-0 flex-1">
        {heading && <p className="font-semibold">{heading}</p>}
        <p className={cn(heading && "mt-0.5")}>
          <code className="mr-1.5 font-mono text-micro">{error.code}</code>
          {sentence}
        </p>
        {debug && machine && (
          <p className="mt-1 break-words font-mono text-micro text-muted-foreground">{machine}</p>
        )}
        {(spendStop || error.usage) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-muted-foreground">
            {error.usage && (
              // The decimal string the server sent, unrounded: a price put
              // through a float is not the price that was charged.
              <span className="num">
                Spent ${error.usage.costUsd} on {error.usage.llmCalls} model call
                {error.usage.llmCalls === 1 ? "" : "s"} before stopping
              </span>
            )}
            {spendStop && (
              <Button
                variant="outline"
                size="xs"
                className="h-5 rounded-full px-2 text-micro font-normal"
                onClick={openSettings}
              >
                Adjust cost ceiling
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
