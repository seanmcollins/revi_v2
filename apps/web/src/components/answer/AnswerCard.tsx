"use client";

import { AlertTriangle, FileSearch, History, SearchX, Zap } from "lucide-react";
import { useMemo } from "react";

import { CopyTextButton } from "@/components/answer/AnswerActions";
import { ContextHeader } from "@/components/answer/ContextHeader";
import { GradeBadge } from "@/components/answer/GradeBadge";
import { InterpretationPanel } from "@/components/answer/InterpretationPanel";
import {
  hasGovernedProvenance,
  MetricProvenanceBadge,
} from "@/components/answer/MetricProvenanceBadge";
import { NarrativeText } from "@/components/answer/NarrativeText";
import { AnomalyReconciliationStrip } from "@/components/banners/AnomalyReconciliationStrip";
import { ReconciliationBanner } from "@/components/banners/ReconciliationBanner";
import { WarningList } from "@/components/banners/WarningBanner";
import { InvestigationChart } from "@/components/charts/InvestigationChart";
import { StageRail } from "@/components/chat/StageRail";
import { ClarificationPrompt } from "@/components/clarification/ClarificationPrompt";
import { DebugTracePanel } from "@/components/debug/DebugTracePanel";
import { DefinitionCard } from "@/components/definitional/DefinitionCard";
import { FeedbackTriage } from "@/components/feedback/FeedbackTriage";
import { FindingCard } from "@/components/findings/FindingCard";
import { AnswerWorklist } from "@/components/worklist/AnswerWorklist";
import { Button } from "@/components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { humanizeColumn, selectRenderableCharts } from "@/lib/contract";
import { answerToText, windowLine } from "@/lib/export";
import { chartWindowLabel } from "@/lib/format";
import { useSessionStore, type TurnRecord } from "@/lib/store";
import { cn } from "@/lib/utils";
import { splitErrorMessage } from "@/lib/warnings";

/**
 * The composed answer: stage rail → context header (always) →
 * interpretation strip → trust badges → findings → charts → narrative →
 * warnings → feedback. Nothing renders a number without its context.
 */
export function AnswerCard({ turn, active = false }: { turn: TurnRecord; active?: boolean }) {
  const openDrawer = useSessionStore((s) => s.openDrawer);
  const debug = useSessionStore((s) => s.settings.debug);
  const streaming = turn.answer.status === "streaming";
  const a = turn.answer;

  // A comparison turn publishes the same measure twice (`main` and
  // `main__compare`, byte-identical rows) and single-row frames for
  // scalars. Both were being drawn: two identical charts stacked, and a
  // "trend" through one point. See `selectRenderableCharts`.
  const charts = useMemo(() => selectRenderableCharts(a.charts), [a.charts]);

  /**
   * The worklist's intro line, lifted out of the turn's warnings.
   *
   * `WORKLIST_ATTACHED` is the sentence that says the ranked cards below
   * are the detection feed's work and NOT findings this turn computed. Left
   * in the general warning list it would sit above the findings and far
   * from the cards it is about, so a reader meets eight ranked dollar
   * figures and finds the disclaimer somewhere else entirely. It is moved,
   * not dropped: it opens the worklist block, and it is removed from the
   * list below so the same sentence is never printed twice.
   */
  const worklistIntro = a.worklist
    ? a.warnings.find((w) => w.code === "WORKLIST_ATTACHED")
    : undefined;
  const warnings = useMemo(
    () =>
      worklistIntro === undefined
        ? a.warnings
        : a.warnings.filter((w) => w !== worklistIntro),
    [a.warnings, worklistIntro],
  );
  // A chart read on its own — screenshotted into a deck, scrolled past the
  // header — otherwise carries no period at all. On a SNAPSHOT contract
  // the period is a moment, not a range, and the payload's window is not
  // what was measured, so the subtitle says the as-of date instead of a
  // range this chart's numbers do not honour.
  const windowLabel = a.header
    ? a.header.asOf
      ? `as of ${a.header.asOf}`
      : chartWindowLabel(a.header.window)
    : undefined;

  /**
   * An answer that completed with nothing to show. This is a real, typed
   * outcome — the question was legible, the probes ran, and the governed
   * data had no rows to report — and it used to render as a card that was
   * blank apart from a "Governed" badge, which reads as a bug and, worse,
   * as a certified nothing. The branch below says what was checked
   * instead, from what the payload actually carries.
   */
  // Worth offering only once the turn has something to take away. A
  // clarification or an error card has no numbers to carry into a
  // meeting, and a streaming one is not finished saying what it means.
  const copyable =
    !streaming &&
    a.status === "complete" &&
    (a.findings.length > 0 || a.narrative.trim() !== "");

  const emptyResult =
    !streaming &&
    a.status === "complete" &&
    a.findings.length === 0 &&
    !a.definition &&
    !a.clarification &&
    !a.error &&
    a.narrative.trim() === "";

  return (
    <div className={cn("space-y-3", active && "relative isolate")}>
      {/* Depth model: a faint accent glow marks the answer being read. */}
      {active && (
        <div
          aria-hidden
          className="answer-glow pointer-events-none absolute -inset-x-10 -top-8 -z-10 h-80"
        />
      )}
      {/* A turn rebuilt when this session was re-opened was never watched
          running, and the server keeps no stage timings — so it says where
          it came from instead of drawing a pipeline nobody observed. */}
      {a.rehydrated ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              className="focus-ring flex items-center gap-1.5 rounded text-[0.68rem] text-muted-foreground hover:text-foreground"
            >
              <History className="size-3" />
              Restored from this session&apos;s history
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom" className="max-w-80 text-[0.68rem] leading-snug">
            Re-opening a session replays what the server kept: this turn&apos;s findings, its
            charts (rebuilt from the frames it stored), its evidence bundle (projected from
            its recorded trace) and — when the store holds them — its stated context and
            written analysis. Its stage timings were never persisted.
          </TooltipContent>
        </Tooltip>
      ) : (
        <StageRail
          stages={a.stages}
          streaming={streaming}
          cacheHits={a.cacheHits}
          debug={debug}
        />
      )}

      {a.header && (
        <div className="fade-up">
          <ContextHeader header={a.header} />
        </div>
      )}
      {a.interpretation && <InterpretationPanel interpretation={a.interpretation} />}

      {/* Space is reserved while the pipeline runs — content replaces the
          shimmer in place, never jumping the layout. */}
      {streaming &&
        a.findings.length === 0 &&
        a.charts.length === 0 &&
        a.narrative === "" &&
        !a.definition &&
        !a.clarification && (
          <div aria-hidden className="grid gap-2.5 min-[1450px]:grid-cols-2">
            <div className="skeleton h-28" />
            <div className="skeleton hidden h-28 min-[1450px]:block" />
          </div>
        )}

      {(a.answerGrade ||
        hasGovernedProvenance(a.metric) ||
        a.evidence?.zeroProbeTurn ||
        copyable) && (
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
            // The explanation used to be a native `title` on a `<span>`:
            // no keyboard path, no touch equivalent. It is the sentence
            // that stops "answered from cache" being read as a freshness
            // claim, so it belongs on a focusable trigger like every
            // other load-bearing explanation in the product.
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  className="focus-ring inline-flex h-5 items-center gap-1 rounded-full border border-verified/40 bg-verified/10 px-2 text-[0.7rem] font-medium text-verified"
                >
                  <Zap className="size-3" />
                  {a.evidence.cacheHits > 0 ? (
                    <>
                      {/* "No new queries" read as a performance boast about
                          the whole answer. What is actually true is narrower
                          and more useful: the results came from cache, at the
                          SAME data load, so nothing here is fresher than what
                          preceded it. */}
                      Answered from cached results
                      {a.header?.watermark.id
                        ? ` (same data load ${a.header.watermark.id})`
                        : ""}{" "}
                      — no new warehouse query
                    </>
                  ) : (
                    "No warehouse query was needed for this answer"
                  )}
                </button>
              </TooltipTrigger>
              <TooltipContent side="bottom" className="max-w-80 text-[0.68rem] leading-snug">
                {a.evidence.cacheHits > 0
                  ? "Every probe this turn needed was already in the evidence cache from earlier in this session, at this same data load. The warehouse was not queried again — the numbers are not newer than the ones above them."
                  : "This turn answered without reading the warehouse at all — it needed no data probe, so there was nothing to query and nothing to reuse from the cache."}
              </TooltipContent>
            </Tooltip>
          )}
          {a.evidence && (
            <Button
              variant="ghost"
              size="xs"
              className="h-5 gap-1 rounded-full px-2 text-[0.68rem] font-normal text-muted-foreground hover:text-foreground"
              onClick={() => openDrawer(turn.id)}
            >
              <FileSearch className="size-3" />
              Evidence
            </Button>
          )}
          {/* An analyst who has to quote a figure in a meeting should not
              have to retype it — and a figure retyped by hand arrives
              without the window, the scope or the caveats that bound it.
              This copies the whole answer, caveats included, from payload
              already in this browser: nothing is fetched and nothing is
              uploaded. See `answerToText`. */}
          {copyable && (
            <CopyTextButton
              label="Copy answer"
              title="Copy this answer as text — findings, the written analysis, every caveat the platform attached, and a provenance line naming the data load and metric pack. Nothing leaves this browser."
              text={() =>
                answerToText({
                  ...(turn.submission.utterance
                    ? { question: turn.submission.utterance }
                    : {}),
                  ...(a.header ? { header: a.header } : {}),
                  findings: a.findings,
                  narrative: a.narrative,
                  warnings: a.warnings,
                  ...(a.metric ? { metric: a.metric } : {}),
                  ...(a.investigationId ? { investigationId: a.investigationId } : {}),
                  ...(a.rehydrated ? { restored: true } : {}),
                })
              }
            />
          )}
        </div>
      )}

      {a.evidence && <ReconciliationBanner result={a.evidence.reconciliation} />}

      {/* This turn opened a portfolio card: the card's figure and the
          answer's, side by side, with the verdict. First-class, above the
          findings — the two numbers are on consecutive screens and the
          reader compares them whether or not anyone reconciles them. */}
      <AnomalyReconciliationStrip reconciliation={a.anomalyReconciliation} />

      {/* Code-driven, not prose-parsed: severity decides the treatment,
          the code decides the title, and identical warnings collapse with
          a count. See `WarningBanner`. */}
      <WarningList warnings={warnings} debug={debug} />

      {a.definition && <DefinitionCard definition={a.definition} />}

      {a.findings.length > 0 && (
        <div className="grid gap-2.5 min-[1450px]:grid-cols-2">
          {a.findings.map((finding, i) => (
            <div
              key={finding.referent.value}
              className="fade-up h-full"
              style={{ animationDelay: `${Math.min(i, 6) * 45}ms` }}
            >
              <FindingCard finding={finding} turnId={turn.id} />
            </div>
          ))}
        </div>
      )}

      {emptyResult && <EmptyResult answer={a} chartCount={charts.length} />}

      {charts.map((spec) => (
        <div key={spec.id} className="fade-up">
          <InvestigationChart spec={spec} turnId={turn.id} windowLabel={windowLabel} />
        </div>
      ))}

      {(a.narrative || streaming) && a.status !== "clarification" && (
        <NarrativeText text={a.narrative} streaming={streaming} />
      )}

      {/* A restored turn with findings and no prose. The write-up is
          composed at answer time and, on the payload generations that do
          not persist it, is simply not among the things the server kept —
          which is a different fact from "this turn had nothing to say",
          and the difference matters because what DOES survive a restore is
          the caveats, so a silent gap here leaves yesterday's answer
          looking like caveats with the answer removed. */}
      {a.rehydrated && !streaming && a.narrative.trim() === "" && a.findings.length > 0 && (
        <p className="rounded-md border border-dashed bg-card/60 px-3 py-2 text-[0.7rem] leading-snug text-muted-foreground">
          The written analysis was not stored for this turn — the findings above, and the
          context they were measured under, are what the server kept.
        </p>
      )}

      {/* The governed conversation→worklist bridge. Below the findings and
          the write-up because it is not what this turn measured — it is
          the ranked work the platform already had, handed over because the
          question routed to it. Rendered on a CLARIFICATION too: "what
          should my team work first" is exactly the question that used to
          end in four ranking bases with the 33-card list never mentioned,
          so the list travels even when the turn still has to ask. */}
      {a.worklist && (
        <div className="fade-up">
          <AnswerWorklist
            worklist={a.worklist}
            {...(worklistIntro ? { intro: worklistIntro } : {})}
          />
        </div>
      )}

      {a.clarification && <ClarificationPrompt clarification={a.clarification} />}

      {a.error && <TurnErrorCard error={a.error} />}

      {/* Debug mode: how this turn was decided, from the server's own
          recorded trace. Never rendered in the default experience. */}
      {debug && !streaming && <DebugTracePanel turnId={turn.id} answer={a} />}

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
 * Two things it now says that it did not. First, WHICH budget stopped it:
 * `QUERY_BUDGET_EXCEEDED` is two failures wearing one code, and the
 * envelope's `subcode` separates them. A warehouse-read stop means the
 * question read too much and the recovery is a narrower question; a
 * model-spend stop means the question was fine and the wallet was the
 * constraint, and sending that reader off to rewrite their question points
 * them at something that was never the problem. The server's own sentence
 * already differs per subcode, so what is added here is the NEXT STEP —
 * a spend stop is fixed from the settings panel, and the panel is one
 * click away rather than one guess away.
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
  // list literals into the one place a reader looks for what to do next;
  // it goes where the rest of the operator material already lives.
  const { sentence, machine } = splitErrorMessage(error.code, error.message);

  return (
    <div className="flex items-start gap-2 rounded-md border border-negative/50 bg-negative/10 px-3 py-2 text-[0.75rem]">
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-negative" />
      <div className="min-w-0 flex-1">
        {heading && <p className="font-semibold">{heading}</p>}
        <p className={cn(heading && "mt-0.5")}>
          <code className="mr-1.5 font-mono text-[0.65rem]">{error.code}</code>
          {sentence}
        </p>
        {debug && machine && (
          <p className="mt-1 break-words font-mono text-[0.62rem] text-muted-foreground">
            {machine}
          </p>
        )}
        {(spendStop || error.usage) && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.68rem] text-muted-foreground">
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
                className="h-5 rounded-full px-2 text-[0.65rem] font-normal"
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

/**
 * "Nothing found" rendered as an answer rather than as an absence.
 *
 * Everything in it comes off the payload that arrived: the window and
 * basis from the context header, the governed contracts from the metric
 * block, the probe count from the evidence bundle. Nothing is inferred —
 * a turn that published none of those says so by simply not listing them,
 * which is still a great deal more than an empty card wearing a badge.
 *
 * Deliberately tolerant of both payload shapes: today emptiness is a
 * `findings: []` answer, and the backend is making it typed. This branch
 * keys off what is on screen, so a typed empty answer lands here too.
 */
function EmptyResult({
  answer,
  chartCount,
}: {
  answer: TurnRecord["answer"];
  chartCount: number;
}) {
  const checked: string[] = [];
  // `windowLine` states an as-of date for a snapshot contract and a range
  // for a flow one — the same distinction the window chip makes, so an
  // empty card cannot describe a scope the header just refused to claim.
  if (answer.header) checked.push(windowLine(answer.header));
  const filters = answer.header?.filters ?? [];
  for (const filter of filters) {
    checked.push(`${filter.dimensionLabel} ${filter.op} ${filter.values.join(", ")}`);
  }
  // Governed measure names in the analyst's spelling — the badge above
  // already carries the contract ids and versions for anyone who wants them.
  const metrics = answer.metric?.metrics.map((m) => humanizeColumn(m.id)) ?? [];
  if (metrics.length > 0) checked.push(metrics.join(", "));
  const checks = answer.evidence?.probes.length ?? 0;
  if (checks > 0) {
    checked.push(`${checks} data check${checks === 1 ? "" : "s"} against this data load`);
  }

  return (
    <div className="rounded-lg border border-dashed bg-card/60 p-3.5">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border bg-surface-sunken">
          <SearchX className="size-3.5 text-muted-foreground" />
        </span>
        <div className="min-w-0 space-y-2">
          <p className="text-[0.8rem] font-medium leading-snug">
            No findings for this question — here&apos;s what was checked
          </p>
          {checked.length > 0 ? (
            <ul className="space-y-0.5 text-[0.7rem] leading-snug text-muted-foreground">
              {checked.map((line) => (
                <li key={line} className="flex gap-1.5">
                  <span aria-hidden>·</span>
                  {line}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[0.7rem] leading-snug text-muted-foreground">
              This turn came back with no window, no measure and no record of what ran — so
              there is nothing further this card can honestly say about it.
            </p>
          )}
          <p className="text-[0.7rem] leading-snug text-muted-foreground">
            {answer.warnings.length > 0
              ? "The notes above are the engine's own account of why."
              : chartCount > 0
                ? "The chart below carries the rows the probes did return."
                : "The governed data has no rows matching that question at this data load — narrow it differently, or widen the window."}
          </p>
        </div>
      </div>
    </div>
  );
}
