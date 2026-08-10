"use client";

import { AlertTriangle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { WatchMode, WatchModel, WatchUnit } from "@/lib/rounds";
import { cn } from "@/lib/utils";

/**
 * WHAT IT TAKES TO BRIEF YOU — one watch's own sensitivity.
 *
 * Four modes, and the fourth is a different kind of question: `crosses`
 * measures against a LEVEL, not against the prior value. They are named
 * for what they do to the analyst's morning rather than for the rule
 * inside them ("Only when it moves at least…", not "delta_gte").
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
 * watching one specific cell knows things the pack's blanket threshold
 * does not. That is allowed and it is paid for: entries briefed on a loose
 * threshold say so, and the brief names the pattern once per load if it
 * keeps happening.
 *
 * MOUNTED IN A `p-3` SCROLLPORT. Both call sites are a `PopoverContent`
 * with that padding, and the action row bleeds through it (`-mx-3 -mb-3`)
 * so the pinned footer spans the panel rather than floating inside it.
 */
export function WatchSensitivityForm({
  initial,
  submitLabel,
  pending,
  /** The server's refusal from the last attempt, verbatim. */
  refusal,
  restartNote,
  onSubmit,
  onCancel,
}: {
  initial?: WatchModel;
  submitLabel: string;
  pending: boolean;
  refusal?: string;
  /**
   * What saving COSTS, when it costs something.
   *
   * Changing an existing watch's sensitivity re-registers it, so its
   * baseline becomes today's value and "since you started watching"
   * measures from here. That is a real loss and it is stated before the
   * click, not discovered after it — the same discipline the archive
   * dialog follows.
   */
  restartNote?: string;
  onSubmit: (watch: WatchModel) => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = useState<WatchMode>(initial?.mode ?? "governed_default");
  const [value, setValue] = useState<string>(
    initial?.value !== undefined ? String(initial.value) : "",
  );
  const [unit, setUnit] = useState<WatchUnit>(initial?.unit ?? "points");
  const [direction, setDirection] = useState<WatchModel["direction"]>(initial?.direction ?? "any");
  const [note, setNote] = useState(initial?.note ?? "");

  const needsValue = mode === "delta_gte" || mode === "crosses";
  const parsed = Number(value);
  const valueOk = !needsValue || (value.trim() !== "" && Number.isFinite(parsed));

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
        <legend className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
          Brief me when
        </legend>
        {MODES.map((option) => (
          <label
            key={option.mode}
            className="flex cursor-pointer items-start gap-2 rounded-md px-1 py-1 text-meta leading-snug transition-colors duration-150 hover:bg-accent/50"
          >
            <input
              type="radio"
              name="watch-mode"
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
              aria-label={mode === "crosses" ? "The level to watch for" : "How far it has to move"}
              placeholder="0.5"
              className="focus-ring num w-24 rounded-md border bg-background px-2 py-1 text-meta"
            />
            <select
              value={unit}
              onChange={(event) => setUnit(event.target.value as WatchUnit)}
              aria-label="The unit that number is stated in"
              className="focus-ring rounded-md border bg-background px-2 py-1 text-meta"
            >
              <option value="points">percentage points</option>
              <option value="relative_pct">% of the current value</option>
              <option value="cents">cents</option>
            </select>
          </div>
          {/* The unit is a claim about what this metric MEASURES, and the
              platform is the authority on that. Said here so a refusal
              reads as an answer to a question the control asked, rather
              than as a validation error. */}
          <p className="text-micro leading-snug text-muted-foreground">
            Percentage points suit a rate, cents suit money. If this measure is not stated in
            the unit you pick, the platform refuses the watch and names the units it does take.
          </p>
        </div>
      )}

      <fieldset className="flex flex-wrap items-center gap-1.5">
        <legend className="mb-1 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
          In which direction
        </legend>
        {DIRECTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            aria-pressed={direction === option.value}
            onClick={() => setDirection(option.value)}
            className={cn(
              "focus-ring rounded-full border px-2 py-0.5 text-micro transition-colors duration-150",
              direction === option.value
                ? "border-ring/60 bg-accent font-medium text-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {option.label}
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
        {/* Recorded so a threshold is a decision somebody made rather than
            a setting nobody remembers — it rides on the brief entry the
            threshold produces. */}
        <span className="block text-micro leading-snug text-muted-foreground">
          Shown on every brief entry this threshold produces.
        </span>
      </label>

      {restartNote && (
        <p className="rounded-md border border-warning/40 bg-warning/10 px-2 py-1.5 text-micro leading-snug">
          {restartNote}
        </p>
      )}

      {/* THE ACTION ROW IS PINNED, and the refusal is pinned with it.
          Measured before this: the form was 662px tall inside a popover
          with no height cap, so "Save and restart this watch" rendered two
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
            data-watch-refusal
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

const MODES: ReadonlyArray<{ mode: WatchMode; label: string; detail: string }> = [
  {
    mode: "governed_default",
    label: "It moves enough to matter",
    detail: "The pack's own threshold for this kind of measure. The default, and the quietest.",
  },
  {
    mode: "any_movement",
    label: "It moves at all",
    detail: "Every load it is not identical. Loud on purpose — for a cell you are actively working.",
  },
  {
    mode: "delta_gte",
    label: "It moves at least this much",
    detail: "Your own threshold, which may be tighter or looser than the pack's.",
  },
  {
    mode: "crosses",
    label: "It crosses this level",
    detail: "Measured against the level, not against the last load.",
  },
];

const DIRECTIONS: ReadonlyArray<{ value: WatchModel["direction"]; label: string }> = [
  { value: "any", label: "either way" },
  { value: "up", label: "only up" },
  { value: "down", label: "only down" },
];
