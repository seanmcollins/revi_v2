"use client";

import { AlertTriangle, Telescope } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "@/components/ui/button";
import { populationLabel, type ResearchOffer, type ResearchSelector } from "@/lib/deepResearch";
import { formatCents, formatCount } from "@/lib/format";
import { useLaunchDeepResearch } from "@/lib/useDeepResearch";
import { cn } from "@/lib/utils";

/**
 * THE CONFIRMATION IN FRONT OF A MINUTE OF WORK.
 *
 * Every way into deep research lands here first — the composer's "run deep
 * research on X", a lead card's action, Home's still-catchable figure —
 * and that is the whole design. A run is about sixty seconds and a real
 * model call, and the product's own rule for a real consequence is that it
 * is stated BEFORE the click rather than discovered after it (the same
 * discipline the reference-demo replay follows in the session rail).
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
 *   WHAT IT WILL LOOK AT — the proposed angles when the payload carries a
 *     plan (`offer.plan`), each with the platform's own sentence for why
 *     it is in the run. Where it does not, the card states what the MODE
 *     does, which is true of every run and is not a claim about this one.
 *   WHAT IT COSTS — about a minute, and that leaving is safe — then one
 *     Run button and nothing else to decide.
 *
 * The plan section is written against a payload that does not exist yet;
 * see `ResearchOffer` in `lib/deepResearch.ts`, which names the expected
 * wire shape. Nothing here waits on it: the card is complete today and
 * grows a section when the backend lands the dry run.
 *
 * IT IS NOT A REFUSAL, and must not read as one. On the composer path the
 * answer above it is a real answer to a real question; this offers to go
 * deeper on the same population. So it sits in the quiet register — no
 * amber, no alert mark — which is what this product reserves for verdicts.
 */
export function ResearchLaunchCard({
  offer,
  variant = "page",
  onLaunched,
}: {
  offer: ResearchOffer;
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

  // The population this card will post. Starts at the offer's own and only
  // moves when the reader picks one the platform published.
  const [chosen, setChosen] = useState<ResearchSelector>(offer.population);
  const options = offer.options ?? [];
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
          {offer.scope && (
            <p className="num mt-0.5 text-meta leading-snug text-muted-foreground">
              {formatCount(offer.scope.openDenials)} open denial
              {offer.scope.openDenials === 1 ? "" : "s"}, worth{" "}
              {formatCents(offer.scope.openDollarsCents)} in denied dollars.
            </p>
          )}

          <p
            className={cn(
              "mt-1 text-meta leading-snug text-muted-foreground",
              !compact && "max-w-[62ch]",
            )}
          >
            {offer.description ||
              `Measure what is realistically recoverable out of ${population}, on your own history, and write it up.`}
          </p>

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

          {/* WHAT THE RUN WILL LOOK AT. The platform's own proposed angles
              when it published them; otherwise the three things the MODE
              always does — statements about deep research, not predictions
              about this run. */}
          <ul className="mt-2 space-y-1">
            {(offer.plan ?? []).length > 0
              ? (offer.plan ?? []).map((angle) => (
                  <li
                    key={angle.title}
                    className="flex gap-1.5 text-meta leading-snug text-foreground/85"
                  >
                    <span aria-hidden className="text-muted-foreground">
                      ·
                    </span>
                    <span>
                      {angle.title}
                      {angle.purpose !== "" && (
                        <span className="block text-micro leading-snug text-muted-foreground">
                          {angle.purpose}
                        </span>
                      )}
                    </span>
                  </li>
                ))
              : MODE_DOES.map((line) => (
                  <li
                    key={line}
                    className="flex gap-1.5 text-meta leading-snug text-foreground/85"
                  >
                    <span aria-hidden className="text-muted-foreground">
                      ·
                    </span>
                    <span>{line}</span>
                  </li>
                ))}
          </ul>

          <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <Button
              size={compact ? "xs" : "sm"}
              disabled={pending}
              onClick={() => {
                launch(chosen);
                onLaunched?.();
              }}
              aria-label={`Run deep research on ${population}`}
              className={cn(
                "accent-gradient-cta gap-1.5 font-medium text-white shadow-sm transition-all duration-150 hover:brightness-110",
                compact ? "h-6 px-2 text-meta" : "text-meta",
              )}
            >
              <Telescope className="size-3" />
              {pending ? "Starting…" : offer.label || "Run deep research"}
            </Button>
            {/* THE COST, BEFORE THE CLICK — and the same account of the
                minute the waiting room gives, so the two never disagree. */}
            <p className="text-micro leading-snug text-muted-foreground">
              About a minute. It opens its own page — you can leave it and come back.
            </p>
          </div>

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
 * What deep research does, whatever population it is pointed at.
 *
 * These are claims about the MODE — every run measures rates on this
 * organization's own answered denials, prices each population with its own
 * range, and separates what is still inside the filing deadline — so they
 * are safe to state before a plan exists. They are replaced, not
 * supplemented, the moment the payload carries the real angles: two lists
 * describing one run is how a reader learns to skip both.
 */
const MODE_DOES: readonly string[] = [
  "Recovery rates measured on your own answered denials — nothing filled in from an industry average.",
  "Expected recoverable dollars per payer and denial type, each with the range around it.",
  "What is still inside the filing deadline, separated from what is already past it.",
];
