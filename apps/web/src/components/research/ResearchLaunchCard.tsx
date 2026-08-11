"use client";

import { AlertTriangle, Telescope } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  offerWithPreview,
  populationLabel,
  type GeneralizedResearchPreview,
  type ResearchOffer,
  type ResearchSelector,
} from "@/lib/deepResearch";
import { formatCents, formatCount } from "@/lib/format";
import { useLaunchDeepResearch, useResearchPreviewFor } from "@/lib/useDeepResearch";
import { cn } from "@/lib/utils";

/**
 * THE CONFIRMATION IN FRONT OF A MINUTE OF WORK.
 *
 * Every way into deep research lands here first — the composer's "Deep
 * research" control, the composer's "run deep research on X", a lead
 * card's action, Home's still-catchable figure — and that is the whole
 * design. A run is about sixty seconds and a real model call, and the
 * product's own rule for a real consequence is that it is stated BEFORE
 * the click rather than discovered after it (the same discipline the
 * reference-demo replay follows in the session rail).
 *
 * ONE CARD, FOUR THINGS, IN THE ORDER SOMEBODY DECIDING WOULD ASK THEM.
 *
 *   WHAT IT WILL ANALYZE — the population, and how big it is. The size is
 *     the payload's when the payload carries one (`offer.scope`); where it
 *     does not, the population is named and nothing is invented about it.
 *   WHICH POPULATION — offered as a choice only when the platform
 *     published alternatives (`offer.options`). Each is a CLOSED selector,
 *     so choosing one changes what is posted and nothing else: no sentence
 *     is re-parsed, no scope is widened here. With no options published,
 *     there is one population and no chooser.
 *   WHAT IT WILL LOOK AT — the proposed angles when the payload carries
 *     them (`offer.plan`), each with the platform's own sentence for why
 *     it is in the run. Where it does not, the card states what the MODE
 *     does, which is true of every run and is not a claim about this one.
 *   WHAT IT COSTS — about a minute, and that leaving is safe — then one
 *     Run button and nothing else to decide.
 *
 * THE FOUR ZONES DESCRIBE THE RUN, AND ONLY THE RUN.
 *
 * `offer.generalized` arrives when a reader asked a question of their own,
 * and it is not a description of the run on offer: it is what Revi makes
 * of the QUESTION, resolved against the real data — what the question
 * reaches, which background notes bear on it, and what would be read to
 * answer it. Today the platform executes one kind of run, the
 * recoverability review, and the generalized loop is resolved for the
 * preview only. So those readings go in a block of their own BELOW the Run
 * button (`QuestionReasoning`), under headings that say "would", with a
 * sentence naming what the button actually starts.
 *
 * Folding them into WHAT IT WILL LOOK AT would have been the single worst
 * thing this card could do: it would list six readings the run does not
 * take, on the one surface in the product whose entire job is to say what
 * a minute of work will buy. The zone therefore keeps the review's own
 * angles, which is what confirming starts.
 *
 * THE REASONS ARE STILL THE LOAD-BEARING PART. A confirmation that lists
 * what would be read without saying why is a progress bar in advance, and
 * the one thing a reader can actually correct before spending the minute
 * is the REASONING. So every reading prints its own `reason`, and none of
 * the server's sentences is re-worded here: a path choice arrives already
 * composed beside the coverage figure it quotes, and a second phrasing is
 * always the one that loses the coverage.
 *
 * A REFUSAL REPLACES THE READINGS AND NOTHING ELSE. When nothing in the
 * data can answer the question, the refusal stands where the readings
 * would be — and the Run button stays, because the run on offer measures
 * something the refusal is not about. Removing it would refuse a run the
 * platform can perfectly well do.
 *
 * THE CARD ITSELF IS NOT A REFUSAL, and must not read as one. On the
 * answer path the answer above it is a real answer to a real question;
 * this offers to go deeper on the same population. So it sits in the quiet
 * register — no amber, no alert mark — which is what this product reserves
 * for verdicts. The refusal above is the one exception, and it is a
 * verdict.
 */
export function ResearchLaunchCard({
  offer,
  question,
  variant = "page",
  onLaunched,
}: {
  offer: ResearchOffer;
  /**
   * The analyst's own question, when the offer arrived without a dry run
   * resolved for it.
   *
   * This is the answer path: "run deep research on X" comes back as an
   * ordinary answer carrying an affordance, and the question that earned
   * it is the utterance above. Handed one, the card resolves the SAME
   * preview the composer's control resolves, so both routes describe the
   * same run in the same words. An offer that already carries its own
   * question (`offer.question`) has been previewed and is not re-read.
   */
  question?: string;
  /**
   * `page` sits in the thread under an answer. `compact` is the same card
   * inside the popover a dense row opens — same content, same order, less
   * air, because a worklist row cannot spend forty vertical pixels on it.
   */
  variant?: "page" | "compact";
  /** The popover host closes itself once a run has actually started. */
  onLaunched?: () => void;
}) {
  const { launch, pending, error } = useLaunchDeepResearch();
  const headingId = useId();
  const optionsId = useId();

  // THE DRY RUN, WHERE THE OFFER ARRIVED WITHOUT ONE. Gated on the offer
  // not already carrying a question, because that is exactly the mark a
  // preview leaves: previewing a previewed offer would be a second POST
  // for a card that is already showing the answer to it.
  const asked = question ?? "";
  const resolve = asked !== "" && offer.question === undefined;
  const { preview, pending: resolving } = useResearchPreviewFor(offer.population, asked, resolve);
  const shown = preview ? offerWithPreview(offer, preview, asked) : offer;
  const general = shown.generalized;

  // The population this card will post. Starts at the offer's own and only
  // moves when the reader picks one the platform published.
  const [chosen, setChosen] = useState<ResearchSelector>(offer.population);
  const options = shown.options ?? [];
  const population = populationLabel(chosen);
  const compact = variant === "compact";

  return (
    <section
      data-research-launch={chosen.kind}
      aria-labelledby={headingId}
      className={cn(
        "rounded-lg",
        compact ? "" : "border bg-surface-raised p-3.5 raised",
      )}
    >
      <div className={cn("flex items-start", compact ? "gap-2" : "gap-2.5")}>
        {!compact && (
          <span
            aria-hidden
            className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md border bg-surface-sunken/70 text-muted-foreground"
          >
            <Telescope className="size-3" />
          </span>
        )}
        <div className="min-w-0 flex-1">
          <h3 id={headingId} className={cn("font-medium", compact ? "text-meta" : "text-body")}>
            Deep research on {population}
          </h3>

          {/* HOW BIG THE POPULATION IS, from the payload and only from the
              payload. A count composed here would be this client asserting
              the size of a population it has not read. */}
          {shown.scope && (
            <p className="num mt-0.5 text-meta leading-snug text-muted-foreground">
              {formatCount(shown.scope.openDenials)} open denial
              {shown.scope.openDenials === 1 ? "" : "s"}, worth{" "}
              {formatCents(shown.scope.openDollarsCents)} in denied dollars.
            </p>
          )}

          <p
            className={cn(
              "mt-1 text-meta leading-snug text-muted-foreground",
              !compact && "max-w-[62ch]",
            )}
          >
            {shown.description ||
              `Measure what is realistically recoverable out of ${population}, on your own history, and write it up.`}
          </p>

          {resolving && (
            <p role="status" className="mt-1 text-meta leading-snug text-muted-foreground">
              Working out what your question reaches…
            </p>
          )}

          {/* THE POPULATION, AS A CHOICE — only where the platform offered
              alternatives. Native radios: this is a single choice from a
              short list, which is exactly what a radio group is, and the
              browser's own semantics are better than any re-implementation
              of them. */}
          {options.length > 0 && (
            <fieldset className="mt-2" id={optionsId}>
              <legend className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
                What to analyze
              </legend>
              <div className="mt-1 space-y-1">
                {[offer.population, ...options].map((option) => {
                  const label = populationLabel(option);
                  const selected = label === population;
                  return (
                    <label
                      key={`${option.kind}:${option.values?.join("|") ?? ""}`}
                      className="flex cursor-pointer items-baseline gap-1.5 text-meta leading-snug"
                    >
                      <input
                        type="radio"
                        name={optionsId}
                        checked={selected}
                        onChange={() => setChosen(option)}
                        className="focus-ring mt-0.5 size-3 shrink-0 accent-[var(--chart-current)]"
                      />
                      <span className={cn(!selected && "text-muted-foreground")}>{label}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          )}

          {/* WHAT THE RUN WILL LOOK AT — the run that CONFIRMING starts,
              and nothing else. The standing angles when the payload
              published them; otherwise the three things the MODE always
              does, which are statements about deep research rather than
              predictions about this run. The readings resolved for a
              research question are NOT here: see `QuestionReasoning`. */}
          <ul className="mt-2 space-y-1">
            {((shown.plan ?? []).length > 0
              ? (shown.plan ?? []).map((angle) => ({
                  title: angle.title,
                  reason: angle.purpose,
                }))
              : MODE_DOES.map((line) => ({ title: line, reason: "" }))
            ).map((line) => (
              <li
                key={line.title}
                className="flex gap-1.5 text-meta leading-snug text-foreground/85"
              >
                <span aria-hidden className="text-muted-foreground">
                  ·
                </span>
                <span>
                  {line.title}
                  {line.reason !== "" && (
                    <span className="block text-micro leading-snug text-muted-foreground">
                      {line.reason}
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>

          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <Button
              size={compact ? "xs" : "sm"}
              disabled={pending || resolving}
              onClick={() => {
                launch(chosen, shown.question ?? general?.researchQuestion);
                onLaunched?.();
              }}
              aria-label={`Run deep research on ${population}`}
              className={cn(
                "accent-gradient-cta gap-1.5 font-medium text-white shadow-sm transition-all duration-150 hover:brightness-110",
                compact ? "h-6 px-2 text-meta" : "text-meta",
              )}
            >
              <Telescope className="size-3" />
              {pending ? "Starting…" : shown.label || "Run deep research"}
            </Button>
            {/* THE COST, BEFORE THE CLICK — and the same account of the
                minute the waiting room gives, so the two never disagree. */}
            <p className="text-micro leading-snug text-muted-foreground">
              About a minute. It opens its own page — you can leave it and come back.
            </p>
          </div>

          {general && (
            <QuestionReasoning preview={general} compact={compact} runs={population} />
          )}

          {error && (
            <p
              role="alert"
              className="mt-1.5 flex items-start gap-1.5 text-meta leading-snug text-negative"
            >
              <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
              {error}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}

/**
 * WHAT REVI MAKES OF THE QUESTION — and why it is NOT the run above.
 *
 * This block is the whole reason a research question is worth typing: it
 * is the reasoning, resolved against the real data before a minute is
 * spent, and the one thing on this surface a reader can actually correct.
 * What it must never do is read as a description of the run the button
 * starts.
 *
 * THE SEPARATION IS THE POINT, AND IT IS A CORRECTNESS PROPERTY. Today the
 * platform executes ONE kind of run — the recoverability review over open
 * denials — and the generalized loop below is resolved for the preview
 * only. So a card that listed these readings under "what it will look at"
 * would promise six readings the run does not take. Everything above this
 * block describes the run on offer; everything inside it describes the
 * question. The heading, the "would", and the sentence naming what the
 * button actually starts are all carrying that distinction, and none of
 * them is decoration.
 *
 * THE STATEMENTS ARE PRINTED VERBATIM. Each path choice arrives from the
 * server already carrying its own coverage figure ("your data carries this
 * mainly in the remit codes — the category field is filled on 12% of lines
 * here"), and re-wording one would make this the second place that fact is
 * phrased. The second phrasing is always the one that drops the coverage
 * and leaves something true, useless and unfalsifiable.
 *
 * THE NOTES ARE TITLES ONLY, which is the server's own rule and worth
 * keeping: a note's content shapes which reading would run and can never
 * shape what a number says, so printing its key points here would put an
 * industry figure next to a measured one on the same card.
 *
 * WHO CHOSE THE READINGS is the one thing here a reader could be misled
 * about for free. `model` means the control plane looked at what the
 * orientation found and chose them for THIS question, and the rationale is
 * its own sentence about why. `revi` means it did not: it fell back to a
 * standing opening read. Both are fine and only one is a choice, so the
 * block says which.
 */
function QuestionReasoning({
  preview,
  compact,
  runs,
}: {
  preview: GeneralizedResearchPreview;
  compact: boolean;
  /** What the button above actually starts, in the words the card used. */
  runs: string;
}) {
  const {
    researchQuestion,
    populationLabel: reads,
    windowLabel,
    pathChoices,
    knowledgeStatement,
    knowledgeConsulted,
    readings,
    authoredBy,
    rationale,
    roundsPlanned,
    refusal,
  } = preview;

  const span = [windowLabel, reads !== "" ? `across ${reads}` : ""]
    .filter((part) => part !== "")
    .join(", ");

  return (
    <section
      data-research-reasoning
      className={cn("mt-3 border-t pt-2.5", !compact && "max-w-[62ch]")}
    >
      <h4 className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
        What Revi makes of your question
      </h4>
      <p className="mt-1 text-meta leading-snug">{researchQuestion}</p>
      {span !== "" && (
        <p className="num mt-0.5 text-micro leading-snug text-muted-foreground">
          Reading {span}.
        </p>
      )}
      {/* THE SENTENCE THAT KEEPS THE CARD HONEST. Without it the reasoning
          below reads as a description of the button above it. */}
      <p className="mt-1 text-micro leading-snug text-muted-foreground">
        Revi worked this out against your data. Running deep research measures what is
        recoverable out of {runs}. It does not take the readings below.
      </p>

      {pathChoices.length > 0 && (
        <div className="mt-2">
          <h5 className="text-micro font-semibold text-foreground/85">
            What this question reaches in your data
          </h5>
          <ul className="mt-1 space-y-1">
            {pathChoices.map((choice) => (
              <li
                key={`${choice.subject}:${choice.statement}`}
                className="flex gap-1.5 text-meta leading-snug text-foreground/85"
              >
                <span aria-hidden className="text-muted-foreground">
                  ·
                </span>
                <span>{choice.statement}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(knowledgeStatement !== "" || knowledgeConsulted.length > 0) && (
        <div className="mt-2">
          <h5 className="text-micro font-semibold text-foreground/85">
            Background notes it read
          </h5>
          {knowledgeStatement !== "" && (
            <p className="mt-1 text-meta leading-snug text-muted-foreground">
              {knowledgeStatement}
            </p>
          )}
          {knowledgeConsulted.length > 0 && (
            <ul className="mt-1 space-y-0.5">
              {knowledgeConsulted.map((note) => (
                <li
                  key={note.title}
                  className="flex gap-1.5 text-meta leading-snug text-foreground/85"
                >
                  <span aria-hidden className="text-muted-foreground">
                    ·
                  </span>
                  <span>{note.title}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* THE REFUSAL STANDS IN FOR THE READINGS AND NOTHING ELSE. Nothing
          in the data can answer the QUESTION; the run on offer measures
          something else and is unaffected, so its button stays. */}
      {refusal !== "" ? (
        <p className="mt-2 flex items-start gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2.5 py-2 text-meta leading-snug">
          <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0 text-warning" />
          <span>{refusal}</span>
        </p>
      ) : (
        readings.length > 0 && (
          <div className="mt-2">
            <h5 className="text-micro font-semibold text-foreground/85">
              What Revi would read to answer it
            </h5>
            <ul className="mt-1 space-y-1.5">
              {readings.map((reading, index) => (
                <li
                  key={`${reading.title}-${index}`}
                  data-research-reading={index}
                  className="flex gap-1.5 text-meta leading-snug text-foreground/85"
                >
                  <span aria-hidden className="text-muted-foreground">
                    ·
                  </span>
                  <span>
                    {reading.title}
                    {reading.reason !== "" && (
                      <span className="block text-micro leading-snug text-muted-foreground">
                        {reading.reason}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>

            <div className="mt-1.5 space-y-0.5">
              {authoredBy === "model" ? (
                rationale !== "" && (
                  <p className="text-micro leading-snug text-muted-foreground">{rationale}</p>
                )
              ) : (
                <p className="text-micro leading-snug text-muted-foreground">
                  Revi picked these from its own standing set. Nothing chose them for this
                  question in particular.
                </p>
              )}
              {/* MORE THAN ONE PASS, said as what it means rather than as
                  a number in a field. The count is the server's; the
                  sentence is about what it would do with it. */}
              {roundsPlanned > 1 && (
                <p className="num text-micro leading-snug text-muted-foreground">
                  It would read, then decide what to go after next — up to{" "}
                  {formatCount(roundsPlanned)} rounds of that.
                </p>
              )}
            </div>
          </div>
        )
      )}
    </section>
  );
}

/**
 * What deep research does, whatever population it is pointed at.
 *
 * These are claims about the MODE — every run measures rates on this
 * organization's own answered denials, prices each population with its own
 * range, and separates what is still inside the filing deadline — so they
 * are safe to state before a preview exists. They are replaced, not
 * supplemented, the moment the payload carries the real readings: two
 * lists describing one run is how a reader learns to skip both.
 */
const MODE_DOES: readonly string[] = [
  "Recovery rates measured on your own answered denials — nothing filled in from an industry average.",
  "Expected recoverable dollars per payer and denial type, each with the range around it.",
  "What is still inside the filing deadline, separated from what is already past it.",
];
