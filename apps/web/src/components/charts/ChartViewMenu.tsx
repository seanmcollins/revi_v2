"use client";

import { ChevronDown, Check } from "lucide-react";
import { DropdownMenu as MenuPrimitive } from "radix-ui";

import { Button } from "@/components/ui/button";
import { VIEW_LABEL, type ChartView } from "@/components/charts/chartForms";

/**
 * "VIEW AS" — the reader's own choice of drawing, in the figure's action
 * row.
 *
 * WHY THIS IS NOT A HOVER REVEAL, and why it is not four tabs either.
 *
 * Persistent, because that is this product's rule for chart controls
 * already (`MonitorThis`, `Full screen`): a control that appears on hover
 * does not exist for a touch user, in a screenshot, or on a projector.
 * And a MENU rather than a row of segments because the row would be four
 * more objects in a strip that already carries Full screen, Monitor this,
 * Expand and CSV — the switcher is a small choice most readers will never
 * make, and it should cost one control's worth of space, not five.
 *
 * The trigger says which drawing is on screen ("View as: Bar"), so the
 * control is also the label for the state it controls. A menu whose
 * trigger reads "View as" alone would make the reader open it to find out
 * what they are looking at.
 *
 * KEYBOARD AND NAME. Radix's `DropdownMenu` owns the roving focus, the
 * type-ahead, Escape, and the focus return to the trigger; the items are
 * `RadioItem`s inside a `RadioGroup`, so a screen reader is told that this
 * is a choice of ONE and which one is taken, rather than a list of seven
 * commands. The accessible name carries the figure's own title for the
 * same reason `Full screen` does: a turn that published four charts has
 * four of these, and "View as" four times names nothing.
 *
 * WHAT IS NOT HERE, ON PURPOSE. The offered set is derived from the data
 * by `chartViewForms` and handed in; this component never inspects a spec
 * and never decides whether a shape is honest. One place decides, and it
 * is the one with the census in front of it.
 */
export function ChartViewMenu({
  value,
  options,
  onChange,
  figureTitle,
}: {
  value: ChartView;
  /** The forms this payload may honestly become — see `chartViewForms`. */
  options: readonly ChartView[];
  onChange: (view: ChartView) => void;
  /** The figure's own title, so four switchers on one turn have four names. */
  figureTitle: string;
}) {
  // One honest drawing is not a choice. A menu with a single item is a
  // control that cannot do anything, and drawing it would teach the reader
  // that the control is decoration.
  if (options.length < 2) return null;

  return (
    <MenuPrimitive.Root>
      <MenuPrimitive.Trigger asChild>
        <Button
          variant="ghost"
          size="xs"
          aria-label={`View as, currently ${VIEW_LABEL[value].toLowerCase()}: ${figureTitle}`}
          title="Draw these same rows as another shape. Nothing is re-measured — the numbers, the marks and the CSV are unchanged."
          className="h-5 gap-1 px-1.5 text-micro font-normal text-muted-foreground hover:text-foreground"
        >
          View as: {VIEW_LABEL[value]}
          <ChevronDown aria-hidden className="size-2.5" />
        </Button>
      </MenuPrimitive.Trigger>
      <MenuPrimitive.Portal>
        <MenuPrimitive.Content
          align="end"
          sideOffset={4}
          className="panel-in z-50 min-w-40 overflow-hidden rounded-lg border bg-surface-overlay p-1 shadow-lg shadow-black/10"
        >
          <MenuPrimitive.RadioGroup
            value={value}
            onValueChange={(next) => onChange(next as ChartView)}
          >
            {options.map((option) => (
              <MenuPrimitive.RadioItem
                key={option}
                value={option}
                className="focus-ring flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-meta outline-none data-[highlighted]:bg-accent"
              >
                {/* The tick is the state made visible, and it keeps its
                    own column so the labels stay on one left edge whether
                    they are checked or not. `ItemIndicator` renders only
                    on the taken one, which is why the box is drawn here
                    rather than inside it. */}
                <span className="flex size-3 shrink-0 items-center justify-center">
                  <MenuPrimitive.ItemIndicator>
                    <Check aria-hidden className="size-3" />
                  </MenuPrimitive.ItemIndicator>
                </span>
                {VIEW_LABEL[option]}
              </MenuPrimitive.RadioItem>
            ))}
          </MenuPrimitive.RadioGroup>
        </MenuPrimitive.Content>
      </MenuPrimitive.Portal>
    </MenuPrimitive.Root>
  );
}

/*
 * THE SEAM FOR "SHOW THAT AS A PIE".
 *
 * This control is the CLIENT half of the choice: it re-renders rows that
 * are already in the browser, and it deliberately starts no turn. The
 * other half — a reader typing "show that as a pie" into the composer —
 * is a typed refinement the server has to publish, because a chart type
 * asked for in words has to be resolved against the same honesty census
 * this menu is derived from (`chartViewForms`), on the server, where the
 * census is authoritative.
 *
 * When that lands it is `{ op: "SetChartType", chart: <spec.id>, view:
 * <ChartView> }` — a new member of the `Refinement` union in
 * `src/lib/types.ts`, emitted through `useSessionStore().emitRefinement`
 * exactly as `DrillInto` and `Expand` are emitted from this figure today.
 * The wiring here is one call in `onChange` beside the local state, and
 * the server's answer is authoritative over the local choice: if it comes
 * back with a form this payload cannot honestly take, `resolveChartView`
 * already falls back rather than drawing it.
 *
 * Not built here. A client that invented the operator would be a second
 * contract nobody declared, and the backend lane owns that one.
 */
