"use client";

import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";

import { NEUTRAL_INK } from "@/components/charts/chartForms";
import { formatPct } from "@/lib/format";
import type { ChartRow, ChartSpec } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * SHARES OF A WHOLE — the one thing a bar chart cannot say and this can.
 *
 * Offered only where the payload can bear the sentence. `donutHonest` is
 * the gate and it is strict: one series, an additive unit, no negatives,
 * nothing truncated at the source, and no ceilings. What reaches this
 * component is a census that adds up.
 *
 * WITHHELD IS THE ONE GAP THAT IS ALLOWED THROUGH, AND IT IS DRAWN, NOT
 * DROPPED.
 *
 * A cell the engine refused to publish has no number, so it cannot have an
 * arc: an arc's LENGTH is its claim, and there is no length that honestly
 * represents "we will not say". The temptation is to leave those rows out
 * of the ring entirely — and that is the worse lie, because every
 * remaining share silently rescales to fill the space they would have
 * taken, and the ring reads as a complete census of a population it is
 * missing rows from.
 *
 * So the withheld rows are a SEGMENT OF THE KEY, in the neutral ink the
 * rollup wears, with "no share" where the percentage goes and the "†" the
 * axis uses. The ring states what it is a ring of ("100% of the measured
 * rows"), the key states what is not in it, and neither of them invents a
 * wedge. A reader can see the count they cannot see the size of, which is
 * exactly the fact the engine published.
 *
 * THE KEY IS A DIRECT LABEL, NOT A LEGEND. Each row carries the swatch,
 * the name, the share and the figure — so identity is never colour alone
 * (the palette's relief rule), and the numbers a reader came for do not
 * require a hover. Each row is also the segment's control: clicking it
 * drills exactly as clicking the arc does, so the drill survives the
 * switch of form.
 */
export function ChartDonutView({
  spec,
  formatValue,
  onDrill,
  colorFor,
  subject,
  expanded = false,
}: {
  spec: ChartSpec;
  formatValue: (value: number) => string;
  onDrill: (row: ChartRow) => void;
  colorFor: (row: ChartRow) => string;
  subject?: string;
  expanded?: boolean;
}) {
  const key = spec.series[0]?.key ?? "";
  const drawn = spec.rows.filter(
    (row) => row.withheld !== true && typeof row.values[key] === "number",
  );
  const withheld = spec.rows.filter((row) => row.withheld === true);
  const total = drawn.reduce((sum, row) => sum + (row.values[key] ?? 0), 0);
  const data = drawn.map((row) => ({
    label: row.label,
    value: row.values[key] ?? 0,
    row,
  }));

  return (
    <div className={cn("flex flex-col gap-3 sm:flex-row sm:items-center", expanded && "flex-1")}>
      <div className={cn("h-56 w-full sm:w-1/2", expanded && "h-full min-h-[18rem]")}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="label"
              innerRadius="55%"
              outerRadius="86%"
              // A 2px surface gap between fills — the same spacer the
              // stacked bars use, so two adjacent arcs never read as one
              // mass.
              paddingAngle={1}
              stroke="var(--card)"
              strokeWidth={2}
              isAnimationActive={false}
              onClick={(entry: unknown) => {
                const payload = (entry as { payload?: { row?: ChartRow } }).payload;
                if (payload?.row) onDrill(payload.row);
              }}
              className="cursor-pointer"
            >
              {data.map((slice) => (
                <Cell
                  key={slice.label}
                  fill={colorFor(slice.row)}
                  {...(slice.label === subject
                    ? { stroke: "var(--foreground)", strokeWidth: 2, strokeOpacity: 0.55 }
                    : {})}
                />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
      </div>

      <ul className="flex w-full flex-col gap-0.5 sm:w-1/2">
        {data.map((slice) => (
          <li key={slice.label}>
            <button
              type="button"
              onClick={() => onDrill(slice.row)}
              aria-label={`Drill into ${slice.label}`}
              title={`Drill into ${slice.label}`}
              className={cn(
                "focus-ring flex w-full items-baseline gap-2 rounded px-1.5 py-1 text-left text-meta hover:bg-accent/50",
                slice.label === subject && "bg-accent/40 font-medium",
              )}
            >
              <span
                aria-hidden
                className="size-2 shrink-0 translate-y-px rounded-[2px]"
                style={{ background: colorFor(slice.row) }}
              />
              <span className="min-w-0 flex-1 truncate">
                {slice.label}
                {slice.row.provisional === true && <span aria-hidden>{" *"}</span>}
              </span>
              <span className="num shrink-0 text-muted-foreground">
                {total > 0 ? formatPct(slice.value / total) : "—"}
              </span>
              <span className="num shrink-0 font-medium">{formatValue(slice.value)}</span>
            </button>
          </li>
        ))}
        {withheld.length > 0 && (
          <li
            data-withheld-segment="true"
            className="flex items-baseline gap-2 rounded px-1.5 py-1 text-meta text-muted-foreground"
          >
            <span
              aria-hidden
              className="size-2 shrink-0 translate-y-px rounded-[2px]"
              style={{ background: NEUTRAL_INK }}
            />
            <span className="min-w-0 flex-1">and {withheld.length} withheld †</span>
            <span className="num shrink-0">no share</span>
          </li>
        )}
      </ul>
    </div>
  );
}

/**
 * What the ring is a ring OF, said in the figure's own caption register.
 *
 * Exported so the sentence is pinned in one place and so the renderer can
 * put it with the other honesty notes under the picture rather than
 * inventing a second place for chart-form captions.
 */
export function donutCensusNote(withheld: number): string | undefined {
  if (withheld <= 0) return undefined;
  const cells = withheld === 1 ? "cell" : "cells";
  return `These shares are of the measured rows only: ${withheld} ${cells} were withheld outright, and a cell with no published figure has no share to draw — it is counted in the key, not sized in the ring.`;
}
