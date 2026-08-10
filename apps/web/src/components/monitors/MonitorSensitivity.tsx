"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { formatCents, formatWholeDollars } from "@/lib/format";
import type { MonitorMode, MonitorModel, MonitorUnit } from "@/lib/monitors";
import { cn } from "@/lib/utils";

/**
 * WHAT IT TAKES TO BRIEF YOU — one monitor's own sensitivity.
 *
 * Four modes, and the fourth is a different kind of question: `crosses`
 * measures against a LEVEL, not against the prior value. They are named
 * for what they do to the analyst's morning rather than for the rule
 * inside them ("Tell me when it crosses a level…", not "crosses").
 *
 * EVERY OPTION IS A SENTENCE THE READER SAYS, not a setting they decode.
 * Read back to the owner, the previous four read as platform vocabulary
 * with the platform filed off, and the default option was the worst of
 * them — see {@link recommendedRuleLabel} for what it said and why every
 * NAME for that threshold failed.
 *
 * THE UNIT IS THE HONESTY CONTROL AND IT IS NOT PRE-VALIDATED HERE.
 * A `points` threshold on a money contract is refused by the server with a
 * sentence naming the legal alternatives. Hiding the illegal option would
 * replace a sentence that teaches ("this contract is measured in dollars;
 * state the threshold in cents or as a relative percentage") with a
 * control that is quietly incapable of being wrong — and the analyst would
 * never learn what their metric is measured in. So every unit is
 * offerable, and the refusal is rendered verbatim where the choice was
 * made.
 *
 * A threshold may LOOSEN the governed gate as well as tighten it. Somebody
 * monitoring one specific cell knows things the pack's blanket threshold
 * does not. That is allowed and it is paid for: entries briefed on a loose
 * threshold say so, and the brief names the pattern once per load if it
 * keeps happening.
 *
 * MOUNTED IN A `p-3` SCROLLPORT. Both call sites are a `PopoverContent`
 * with that padding, and the action row bleeds through it (`-mx-3 -mb-3`)
 * so the pinned footer spans the panel rather than floating inside it.
 */
export function MonitorSensitivityForm({
  initial,
  submitLabel,
  pending,
  /** The server's refusal from the last attempt, verbatim. */
  refusal,
  restartNote,
  recommended,
  metricLabel,
  onSubmit,
  onCancel,
}: {
  initial?: MonitorModel;
  submitLabel: string;
  pending: boolean;
  refusal?: string;
  /**
   * THE RECOMMENDED RULE, AS A NUMBER AND A UNIT — when one is published.
   *
   * NOTHING SUPPLIES THIS TODAY, and that is a wire gap rather than an
   * oversight here: `GET /v1/monitors` puts the governed gate only inside
   * prose (`delta.materiality_note` — "…at or above the governed gate of
   * 0.5 points") and `GET /v1/monitors/pins` carries only the analyst's
   * OWN `monitor` object, never a recommended one. A client that read the
   * number back out of that sentence would be parsing its own caption,
   * which this codebase does not do anywhere else and must not start
   * doing on the control that sets an interruption. So the default option
   * renders the honest fallback until a structured field exists, and
   * never invents a figure.
   */
  recommended?: { value: number; unit: MonitorUnit };
  /**
   * The measure in the reader's own noun ("denial rate"), for the one
   * sentence that says whose recommendation this is. Absent, that
   * sentence says "this metric" — vaguer, and true.
   */
  metricLabel?: string;
  /**
   * What saving COSTS, when it costs something.
   *
   * Changing an existing monitor's sensitivity re-registers it, so its
   * baseline becomes today's value and "since you started monitoring"
   * measures from here. That is a real loss and it is stated before the
   * click, not discovered after it — the same discipline the archive
   * dialog follows.
   */
  restartNote?: string;
  onSubmit: (monitor: MonitorModel) => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = useState<MonitorMode>(initial?.mode ?? "governed_default");
  const [value, setValue] = useState<string>(
    initial?.value !== undefined ? String(initial.value) : "",
  );
  const [unit, setUnit] = useState<MonitorUnit>(initial?.unit ?? "points");
  const [direction, setDirection] = useState<MonitorModel["direction"]>(initial?.direction ?? "any");
  const [note, setNote] = useState(initial?.note ?? "");

  const needsValue = mode === "delta_gte" || mode === "crosses";
  const parsed = Number(value);
  const valueOk = !needsValue || (value.trim() !== "" && Number.isFinite(parsed));
  const modes = modeOptions(recommended, metricLabel);

  return (
    <form
      className="space-y-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (!valueOk || pending) return;
        onSubmit({
          mode,
          ...(needsValue ? { value: parsed, unit } : {}),
          direction,
          note: note.trim(),
        });
      }}
    >
      <fieldset className="space-y-1.5">
        {/* NOT A SENTENCE STEM. It read "Brief me when", which only works
            while the options below it are clauses ("It moves at all").
            The options are now whole sentences the reader says, so a stem
            in front of them would make the control ungrammatical to
            anybody reading it top to bottom. */}
        <legend className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
          What is worth telling you about
        </legend>
        {modes.map((option) => (
          <label
            key={option.mode}
            className="flex cursor-pointer items-start gap-2 rounded-md px-1 py-1 text-meta leading-snug transition-colors duration-150 hover:bg-accent/50"
          >
            <input
              type="radio"
              name="monitor-mode"
              value={option.mode}
              checked={mode === option.mode}
              onChange={() => setMode(option.mode)}
              className="mt-1 accent-[var(--ring)]"
            />
            <span className="min-w-0">
              <span className="block font-medium">{option.label}</span>
              <span className="block text-micro text-muted-foreground">{option.detail}</span>
            </span>
          </label>
        ))}
      </fieldset>

      {needsValue && (
        <div className="space-y-1.5 rounded-md border bg-surface-sunken/50 p-2">
          <div className="flex items-center gap-1.5">
            <input
              type="text"
              inputMode="decimal"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              aria-label={mode === "crosses" ? "The level to monitor for" : "How far it has to move"}
              placeholder="0.5"
              className="focus-ring num w-24 rounded-md border bg-background px-2 py-1 text-meta"
            />
            <select
              value={unit}
              onChange={(event) => setUnit(event.target.value as MonitorUnit)}
              aria-label="The unit that number is stated in"
              className="focus-ring rounded-md border bg-background px-2 py-1 text-meta"
            >
              {/* EACH OPTION SAYS WHAT THE TYPED NUMBER MEANS, and none of
                  them is the wire value wearing a capital letter. "cents"
                  and "days" alone are the machine's words for these — a
                  reader seeing "days" beside a box has to guess whether
                  they are typing a date, a count of loads, or a size of
                  movement. "A lag, in days" answers that in the option
                  itself, and every one of them opens with a capital,
                  which the old lowercase list did not. */}
              <option value="points">Percentage points</option>
              <option value="relative_pct">Percent of the current value</option>
              <option value="cents">Money, in cents</option>
              {/* A LAG IS ITS OWN UNIT. "days in A/R moved 2 days" is not
                  two percentage points and not two cents; the engine, the
                  governed rules and the wire have all taken `days` since
                  round 8, and this control was the last copy of the list
                  that had not — so a days monitor opened here read "2
                  percentage points" and saving it submitted a unit the
                  server refuses. */}
              <option value="days">A lag, in days</option>
            </select>
          </div>
          {/* The unit is a claim about what this metric MEASURES, and Revi
              is the authority on that. Said here so a refusal reads as an
              answer to a question the control asked, rather than as a
              validation error. */}
          <p className="text-micro leading-snug text-muted-foreground">
            Pick the unit this measure is kept in. If it is not the one you picked, Revi
            refuses the monitor and names the units this measure does take.
          </p>
        </div>
      )}

      <fieldset className="flex flex-wrap items-center gap-1.5">
        <legend className="mb-1 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
          In which direction
        </legend>
        {DIRECTIONS.map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={direction === option}
            onClick={() => setDirection(option)}
            className={cn(
              "focus-ring rounded-full border px-2 py-0.5 text-micro transition-colors duration-150",
              direction === option
                ? "border-ring/60 bg-accent font-medium text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {DIRECTION_LABELS[option]}
          </button>
        ))}
      </fieldset>

      <label className="block space-y-1">
        <span className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
          Why (optional)
        </span>
        <input
          type="text"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          placeholder="Anything over a point on this payer is worth my morning."
          className="focus-ring w-full rounded-md border bg-background px-2 py-1 text-meta"
        />
        {/* Recorded so a level is a decision somebody made rather than a
            setting nobody remembers — it rides on the brief entry the
            level produces. */}
        <span className="block text-micro leading-snug text-muted-foreground">
          Shown on every brief entry this monitor produces.
        </span>
      </label>

      {/* What saving this will cost — the baseline restarts. A cost stated
          before the click, in the reader's ink: it is context for a
          decision they are about to make, not an alarm about one they
          already made. */}
      {restartNote && (
        <p className="rounded-md border bg-surface-sunken/60 px-2 py-1.5 text-micro leading-snug text-muted-foreground">
          {restartNote}
        </p>
      )}

      {/* THE ACTION ROW IS PINNED, and the refusal is pinned with it.
          Measured before this: the form was 662px tall inside a popover
          with no height cap, so "Save and restart this monitor" rendered two
          pixels below the fold on a 772px viewport — the marquee gesture's
          primary action, unreachable, with nothing under it to scroll to.
          The popover now scrolls internally (see `ui/popover.tsx`) and
          this row stays on the bottom edge of that scrollport.

          The refusal travels INSIDE the pinned row rather than above it:
          it is the answer to the button that was just pressed, and a
          server sentence naming this contract's legal units is worth
          nothing if it scrolls away from the control that produced it. */}
      <div className="sticky bottom-0 -mx-3 -mb-3 space-y-1.5 border-t bg-popover px-3 pb-3 pt-2">
        {refusal && (
          // The platform's own sentence. It names the legal units for this
          // contract, and paraphrasing it would drop the only part worth
          // reading.
          <p
            role="alert"
            data-monitor-refusal
            className="flex items-start gap-1.5 text-micro leading-snug text-negative"
          >
            <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
            {refusal}
          </p>
        )}
        <div className="flex gap-1.5">
          <Button
            type="submit"
            size="xs"
            variant="secondary"
            disabled={pending || !valueOk}
            className="h-6 flex-1 text-meta font-medium"
          >
            {pending ? "Saving…" : submitLabel}
          </Button>
          <Button
            type="button"
            size="xs"
            variant="ghost"
            onClick={onCancel}
            className="h-6 flex-1 text-meta font-normal"
          >
            Cancel
          </Button>
        </div>
      </div>
    </form>
  );
}

/**
 * THE DEFAULT OPTION NAMES NO THRESHOLD, BECAUSE EVERY NAME FOR IT FAILED.
 *
 * It read "The pack's own threshold for this kind of measure", and the
 * owner's response to that sentence was "what the fuck does that even
 * mean? That sounds like nonsense to me." The repair that renamed it "the
 * standard threshold" failed on a second and worse ground: "I would never
 * trust that." Both failures are the same failure. A NAME for a gate the
 * reader cannot see is either jargon (it names a thing they have never
 * heard of) or an authority claim (it asks them to take a number on
 * faith), and neither is the rule.
 *
 * So the default option states the RULE, with the number and the unit
 * inside it — "Tell me when it moves more than 0.5 points". Nothing to
 * decode, nothing to disbelieve, and a reader who thinks the level is
 * wrong can see exactly what they disagree with before they change it.
 *
 * AND WHEN NO NUMBER IS PUBLISHED, IT SAYS SO BY SAYING LESS. No metric
 * carries a structured recommended value on the wire today (see the
 * `recommended` prop), so this is the branch every metric currently
 * renders. "Tell me about meaningful changes" is vaguer than the sentence
 * above it and it is TRUE, which the alternative — a plausible-looking
 * 0.5 composed here — would not be.
 */
export function recommendedRuleLabel(recommended?: { value: number; unit: MonitorUnit }): string {
  if (recommended === undefined || !Number.isFinite(recommended.value)) {
    return "Tell me about meaningful changes";
  }
  return `Tell me when it moves more than ${thresholdPhrase(recommended.value, recommended.unit)}`;
}

/**
 * The level as a phrase somebody reads aloud, in the unit it was stated
 * in. Plural-correct: "1 points" is the seam that makes a reader stop
 * trusting the sentence around it, and this sentence is the whole reason
 * the default option no longer has a name.
 *
 * The unit is not decoration. `points`, `cents` and `days` are three
 * different questions about the same-looking number, and a threshold
 * rendered in the wrong one is a monitor that fires on the wrong thing.
 */
function thresholdPhrase(value: number, unit: MonitorUnit): string {
  switch (unit) {
    case "points":
      return `${decimal(value)} ${Math.abs(value) === 1 ? "point" : "points"}`;
    case "relative_pct":
      return `${decimal(value)}% of the current value`;
    // Money arrives in cents and is read in dollars. Whole dollars when it
    // is whole — "$1,000.00" implies a precision a recommended level does
    // not have — and the exact figure when it is not.
    case "cents":
      return Math.round(value) % 100 === 0 ? formatWholeDollars(value) : formatCents(value);
    case "days":
      return `${decimal(value)} ${Math.abs(value) === 1 ? "day" : "days"}`;
  }
}

const DECIMAL = new Intl.NumberFormat("en-US", { maximumFractionDigits: 3 });

function decimal(value: number): string {
  return DECIMAL.format(value);
}

/**
 * The measure's noun in the plural, for "Revi's recommended level for
 * denial rates". A recommendation is about a KIND of measure rather than
 * about this one cell, and the singular ("for denial rate") reads as a
 * typo — which is enough to make a reader doubt the sentence.
 */
function pluralMetric(label: string): string {
  const trimmed = label.trim();
  if (trimmed === "" || /s$/i.test(trimmed)) return trimmed;
  if (/[^aeiou]y$/i.test(trimmed)) return `${trimmed.slice(0, -1)}ies`;
  if (/(ch|sh|x|z)$/i.test(trimmed)) return `${trimmed}es`;
  return `${trimmed}s`;
}

/**
 * The four options, in the owner's register: first person, present tense,
 * one sentence each, and not one word a first-time reader has to have been
 * told. The two that need a number end in an ellipsis, because they open
 * something rather than decide something.
 */
function modeOptions(
  recommended: { value: number; unit: MonitorUnit } | undefined,
  metricLabel: string | undefined,
): ReadonlyArray<{ mode: MonitorMode; label: string; detail: string }> {
  const subject =
    metricLabel === undefined || metricLabel.trim() === "" ? "this metric" : pluralMetric(metricLabel);
  return [
    {
      mode: "governed_default",
      label: recommendedRuleLabel(recommended),
      // WHOSE recommendation, and that it is not binding. The owner
      // distrusted an unattributed "standard"; a recommendation with a
      // name on it and an exit beside it is a different offer.
      detail: `Revi's recommended level for ${subject}. You can change it anytime.`,
    },
    {
      mode: "any_movement",
      label: "Tell me about any movement",
      detail:
        "Every change, however small. Loud on purpose — for a cell you are actively working.",
    },
    {
      mode: "delta_gte",
      label: "Set my own level…",
      detail: "You choose how big a change has to be before it reaches you.",
    },
    {
      mode: "crosses",
      // A DIFFERENT KIND OF QUESTION, and the detail has to say so in
      // words. This one is measured against a fixed level rather than
      // against the previous reading, so the same movement can brief on
      // one day and not the next — which is surprising unless it is said.
      label: "Tell me when it crosses a level…",
      detail: "Measured against the level you set, not against what it read last time.",
    },
  ];
}

/**
 * DIRECTION IS NOT GOOD OR BAD, AND THIS CONTROL REFUSES TO GUESS.
 *
 * A rising denial rate is bad news; rising collections are good news; this
 * form does not know which measure it is sitting on. So the labels say
 * only what the monitor WATCHES, never what the movement would mean — no
 * colour, no arrows, no "worsens". A judgement rendered here would be
 * wrong on half the metrics in the product.
 *
 * KEYED BY THE WIRE VALUE, as a total `Record`, so a wire value can never
 * render verbatim: adding a fourth direction upstream stops this map
 * compiling before it can reach a reader. The previous list rendered
 * "either way / only up / only down" — lowercase fragments that read, in
 * the owner's words, as "amateur hour", because they are the enum with a
 * space in it.
 */
const DIRECTION_LABELS: Readonly<Record<MonitorModel["direction"], string>> = {
  any: "Any direction",
  up: "Only when it rises",
  down: "Only when it falls",
};

const DIRECTIONS: ReadonlyArray<MonitorModel["direction"]> = ["any", "up", "down"];
