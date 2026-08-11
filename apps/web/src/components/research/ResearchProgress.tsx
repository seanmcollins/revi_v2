"use client";

import { AlertTriangle, Check, Square, Telescope } from "lucide-react";

import { formatCount } from "@/lib/format";
import {
  angleTitles,
  hasFailed,
  isRunning,
  populationLabel,
  researchPhaseRows,
  wasStopped,
  type ResearchWatchState,
} from "@/lib/deepResearch";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";
import { cn } from "@/lib/utils";

/**
 * THE MINUTE, ACCOUNTED FOR.
 *
 * A deep-research run takes about sixty seconds and the honest thing to
 * draw for sixty seconds is not a spinner. A spinner says "something is
 * happening"; this says which of three things is happening, how many of
 * the angles have been measured, and — the fact that actually calibrates
 * the wait — that almost all of the minute is the write-up rather than the
 * measuring.
 *
 * THE PHASES ARE THE WIRE'S, IN THE READER'S WORDS. `orient | consult |
 * plan | execute | read | round | synthesize` is the run's own vocabulary
 * and seven words from a compiler; "reading your data / running the
 * analysis / going after what it found / writing it up" is the same run
 * said the way somebody waiting would say it (`RESEARCH_PHASES` holds the
 * mapping and the reason for it). Underneath, the SERVER's own progress
 * sentence ("Comparing payers", "Round 1 — chasing it: the payer spread
 * was decisive") is printed verbatim — nothing here stands in for a
 * sentence the platform already wrote, and nothing here composes a second
 * wording of a decision the run has already explained.
 *
 * A RUN THAT WENT BACK FOR MORE SAYS SO. Most questions take one pass;
 * some earn a second, where the run reads what came back and goes after
 * the thing that separated. That is a different state from "still going"
 * and the reader is entitled to know which one they are in — so the
 * iteration row appears when, and only when, it happens.
 *
 * THE ANGLES TICK OFF ONLY ONCE THEY ARE NAMED. The plan frame arrives
 * with the finished report, so for most of the run this client holds a
 * COUNT and no names — and it says the count rather than inventing eight
 * plausible titles that would then be replaced by the real ones.
 *
 * LEAVING IS SAFE AND IT SAYS SO, AND SO IS STOPPING. Two different
 * promises, and the surface owes both. Leaving is safe because the run
 * outlives the page: it is written whether or not anybody is looking, and
 * this address is where it will be. Stopping is REAL — `POST
 * .../cancel` ends the run itself between two readings and the model call
 * goes with it — which is what makes the control honest to draw. Until
 * that route existed this file argued the opposite case, and it was right
 * then for the same reason it is wrong now: a "Stop" that only abandoned
 * the watcher while the server kept spending would have been a button
 * that lied about what it did.
 *
 * A STOPPED RUN IS NOT A FAILED ONE. It gets the calm register — the run
 * did what it was told — while `failed` and `interrupted` keep the warning
 * one. Reporting a reader's own decision back to them in red is the
 * clearest way to teach them not to use a control that saves them money.
 */
export function ResearchProgress({
  state,
  onStop,
  stopping = false,
  stopError = null,
}: {
  state: ResearchWatchState;
  /** Stop the run. Absent where nothing can be stopped (a replayed run). */
  onStop?: () => void;
  stopping?: boolean;
  /** Why a stop was refused, in the server's own words. */
  stopError?: string | null;
}) {
  const reducedMotion = usePrefersReducedMotion();
  const { run } = state;
  const progress = run.progress;
  const titles = angleTitles(state);
  const total = progress.angle_total || titles.length;
  const done = Math.min(progress.angle_index, total);
  /**
   * WHAT THIS RUN IS ABOUT, from the moment it starts.
   *
   * A study is about the QUESTION and reads whatever answers it; the
   * recoverability review is about a population and answers a question
   * nobody typed. With only the population to go on, a study of A/R aging
   * spent its whole minute headed "every open denial" — a population it
   * never opens.
   */
  const asked = run.research_question ?? "";
  const population = asked !== "" ? asked : populationLabel(run.population);
  const failed = hasFailed(run.status);
  const stopped = wasStopped(run.status);
  const running = isRunning(run.status);
  const rows = researchPhaseRows(progress, run.status);
  const round = progress.round_index ?? 0;
  const rounds = progress.round_total ?? 0;

  return (
    <section
      data-research-progress={run.status}
      aria-labelledby="research-progress-heading"
      className="mx-auto w-full max-w-3xl space-y-5"
    >
      <header className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-micro font-semibold uppercase tracking-widest text-muted-foreground">
            <Telescope aria-hidden className="size-3" />
            Deep research
          </p>
          <h2 id="research-progress-heading" className="mt-1 text-lead font-medium">
            {failed ? "This run stopped" : stopped ? "Stopped working through" : "Working through"}{" "}
            {population}
          </h2>
          {asked !== "" && !stopped && (
            <p className="mt-0.5 text-meta leading-snug text-muted-foreground">
              Revi is measuring what your data can say about this, then going after whatever
              separates.
            </p>
          )}
          {run.data_load_label !== "" && !stopped && (
            <p className="num mt-0.5 text-micro text-muted-foreground">
              Every number in this report is read at {run.data_load_label}.
            </p>
          )}
        </div>

        {/* STOP, WHILE THERE IS SOMETHING TO STOP. A run is about a minute
            of measuring and a real model call that carry on whether or not
            anybody is watching, so a reader who has changed their mind is
            offered the one control that ends the work rather than only the
            watching. It is drawn nowhere else: a run that has finished,
            failed or already been stopped has nothing left to end, and a
            button that did nothing would be the same lie in the other
            direction. */}
        {running && onStop !== undefined && (
          <button
            type="button"
            onClick={onStop}
            disabled={stopping}
            data-research-stop
            className="focus-ring flex shrink-0 items-center gap-1.5 rounded-md border bg-surface-sunken/70 px-2 py-1 text-meta font-medium text-muted-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground disabled:opacity-60"
          >
            <Square aria-hidden className="size-3" />
            {stopping ? "Stopping…" : "Stop this run"}
          </button>
        )}
      </header>

      {/* A STOP THAT DID NOT LAND, in the server's own words. The run is
          still going and still drawn below; the only thing that failed is
          the request to end it, so that is the only thing this says. */}
      {stopError !== null && (
        <p
          role="alert"
          className="flex items-start gap-2 rounded-md border border-negative/50 bg-negative/10 px-3 py-2 text-meta leading-snug"
        >
          <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-negative" />
          <span>{stopError}</span>
        </p>
      )}

      {stopped ? (
        /* THE CALM REGISTER, because nothing went wrong. The server's own
           sentence, then the account of what the minute bought: how far it
           had got before it ended. No report is offered because none was
           written — a stop lands between two readings precisely so that no
           half-measured figure ever exists to be shown. */
        <div className="space-y-2 rounded-lg border bg-surface-sunken/40 px-3 py-2.5">
          <p role="status" className="text-meta leading-snug">
            {run.error ??
              "This run was stopped on request, so nothing was published. What it had got through is kept."}
          </p>
          {/* HOW FAR, in the counter's own terms. "It had reached angle 3
              of 8" rather than "it measured 3": the third was in hand when
              the stop landed, and claiming it as measured would be this
              surface adding a reading the run never published. */}
          {done > 0 && total > 0 && (
            <p className="num text-meta leading-snug text-muted-foreground">
              It had reached angle {formatCount(done)} of {formatCount(total)}
              {rounds > 1 ? `, on round ${formatCount(round + 1)} of ${formatCount(rounds)}` : ""}.
            </p>
          )}
          {run.data_load_label !== "" && (
            <p className="num text-micro text-muted-foreground">
              It was reading {run.data_load_label}. Running it again starts a new run at
              whichever load is newest then.
            </p>
          )}
        </div>
      ) : failed ? (
        /* The server's own sentence, verbatim. Nothing partial is
           published on a failed run, so there is no half-report to offer
           and the surface does not imply there is one. */
        <p
          role="alert"
          className="flex items-start gap-2 rounded-md border border-negative/50 bg-negative/10 px-3 py-2 text-meta leading-snug"
        >
          <AlertTriangle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-negative" />
          <span>{run.error ?? "This run stopped before it could finish."}</span>
        </p>
      ) : (
        <>
          <ol className="space-y-2.5">
            {rows.map(({ phase, state: at }) => {
              return (
                <li
                  key={phase.id}
                  data-phase={phase.id}
                  data-phase-state={at}
                  data-round={phase.id === "round" ? round : undefined}
                  className="flex items-start gap-2.5"
                >
                  <span
                    aria-hidden
                    className={cn(
                      "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border",
                      at === "done" && "border-verified/50 bg-verified/10 text-verified",
                      at === "active" && "border-ring/60 bg-accent",
                      at === "pending" && "border-border",
                    )}
                  >
                    {at === "done" ? (
                      <Check className="size-2.5" />
                    ) : at === "active" ? (
                      <span
                        className={cn(
                          "size-1.5 rounded-full bg-foreground",
                          // The one moving thing on the surface, and it is
                          // switched off for a reader who asked for that.
                          !reducedMotion && "animate-pulse",
                        )}
                      />
                    ) : null}
                  </span>
                  <div className="min-w-0 flex-1">
                    <p
                      className={cn(
                        "text-body leading-snug",
                        at === "pending" && "text-muted-foreground",
                        at === "active" && "font-medium",
                      )}
                    >
                      {phase.label}
                      {/* WHICH ROUND, once the run has moved past one.
                          Only where the row is not the active one: the
                          server's own sentence for an active round opens
                          "Round 1 — chasing it", and a count beside it
                          would be this surface saying the same number
                          twice. */}
                      {phase.id === "round" && at !== "active" && rounds > 1 && (
                        <span className="num font-normal text-muted-foreground">
                          {" · "}
                          round {formatCount(round)} of {formatCount(rounds)}
                        </span>
                      )}
                      {/* The screen reader is told the state in words
                          rather than by the dot's colour. */}
                      <span className="sr-only">
                        {at === "done"
                          ? " — done"
                          : at === "active"
                            ? " — happening now"
                            : " — not started"}
                      </span>
                    </p>
                    {/* THE SERVER'S OWN SENTENCE about what it is doing,
                        under the phase it belongs to and nowhere else —
                        and only when it says something the label does not.
                        The write-up's own message IS "Writing it up", and
                        printing it under a heading reading "Writing it up"
                        is a line that costs a reader a glance and returns
                        nothing. */}
                    {at === "active" &&
                      progress.message !== "" &&
                      progress.message.toLowerCase() !== phase.label.toLowerCase() && (
                        <p className="mt-0.5 text-meta leading-snug text-muted-foreground">
                          {progress.message}
                          {phase.id === "execute" && total > 0 && (
                            <span className="num">
                              {" · "}
                              {formatCount(done)} of {formatCount(total)}
                            </span>
                          )}
                        </p>
                      )}
                    {phase.note !== undefined && at !== "pending" && (
                      <p className="mt-0.5 text-meta leading-snug text-muted-foreground">
                        {phase.note}
                      </p>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>

          {/* WHAT IT IS LOOKING AT, once the platform has named the
              angles. Before that the count stands alone — a real fact,
              where eight invented titles would not be. */}
          {titles.length > 0 ? (
            <section aria-labelledby="research-angles-heading" className="rounded-lg border p-3">
              <h3
                id="research-angles-heading"
                className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
              >
                What it is looking at
              </h3>
              <ul className="mt-1.5 space-y-1.5">
                {titles.map((angle, index) => {
                  /* A STUDY'S READINGS ARRIVE AS THEY ARE TAKEN, so every
                     one on this list has already been measured except the
                     newest — which is the one the progress frame is
                     describing right now. A study therefore ticks off by
                     position in the list, while the review's plan (which
                     arrives whole, at the end) ticks off by the angle
                     counter. Two honest inputs; the one that exists wins. */
                  const complete =
                    angle.reason === undefined
                      ? angle.lastIndex < done
                      : index < titles.length - 1 || !isRunning(run.status);
                  return (
                    <li
                      key={`${angle.title}-${angle.lastIndex}`}
                      data-angle-done={complete ? "true" : "false"}
                      data-angle-round={angle.round}
                      className="flex items-baseline gap-1.5 text-meta leading-snug"
                    >
                      <span
                        aria-hidden
                        className={cn(
                          "shrink-0",
                          complete ? "text-verified" : "text-muted-foreground",
                        )}
                      >
                        {complete ? "✓" : "·"}
                      </span>
                      <span className={cn(!complete && "text-muted-foreground")}>
                        {angle.title}
                        {angle.cuts > 1 && (
                          <span className="num text-muted-foreground">
                            {" · "}
                            {angle.cuts} cuts
                          </span>
                        )}
                        {/* WHY IT IS BEING READ. The platform wrote this
                            sentence when it chose the reading; a checklist
                            that showed only titles would be a list of what
                            happened rather than a record of what was
                            decided — and on a run that goes back for more,
                            the reason is the whole of what changed. */}
                        {angle.reason !== undefined && angle.reason !== "" && (
                          <span className="block text-micro leading-snug text-muted-foreground">
                            {angle.round !== undefined && angle.round > 0 && (
                              <span className="num">Round {formatCount(angle.round)} — </span>
                            )}
                            {angle.reason}
                          </span>
                        )}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : (
            total > 0 && (
              <p className="num text-meta leading-snug text-muted-foreground">
                {formatCount(total)} angle{total === 1 ? "" : "s"} to measure. Each is named in
                the report.
              </p>
            )
          )}

          {/* TWO FACTS THAT ARE EASY TO CONFUSE, SAID TOGETHER. Leaving
              does not stop anything — the run is written whether or not
              anybody is looking, and this address is where it will be.
              Stopping does stop it, work and all. A surface that offered
              the control without the distinction would leave a reader
              guessing which of the two closing the tab was. */}
          {running && (
            <p className="max-w-[62ch] text-meta leading-snug text-muted-foreground">
              You can leave this page. The run keeps going and the report will be here at this
              link when it is done.
              {onStop !== undefined
                ? " Stopping it is the other thing: the run ends where it is and nothing is published."
                : ""}
            </p>
          )}
        </>
      )}
    </section>
  );
}
