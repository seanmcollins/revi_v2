"use client";

import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import type { ChartRow, ChartSpec } from "@/lib/types";
import { cn } from "@/lib/utils";

/**
 * TWO WINDOWS, ONE LINE PER CATEGORY — the movement drawn as a movement.
 *
 * Grouped bars are the honest default for a comparison and they are bad at
 * exactly one thing: reading the CHANGE. Twelve pairs of columns asks the
 * eye to subtract twelve times, which is why this file's tooltip states
 * the delta in words rather than leaving it to be measured. A slope draws
 * the subtraction: the segment's angle IS the change, and twelve of them
 * on one pair of axes sorts itself into "these went up, these went down"
 * before a single number is read.
 *
 * It is a choice and not the default because it costs something real —
 * two windows on one continuous axis reads as a trend through time, which
 * these are not (they are two readings, with nothing published in
 * between). So the axis has exactly two ticks and they are named, never
 * numbered.
 *
 * THE MARKS SURVIVE THE FORM, which is what earns it a place on the
 * switcher:
 *
 *   · a segment that touches a CEILING is dashed with hollow points, the
 *     same treatment the trend line gives an unmeasured point — a ceiling
 *     minus a measurement is not a movement, and a solid slope between
 *     them would claim one;
 *   · a category ABSENT from one window gets no segment at all, because
 *     the compare operator's zero-fill is a join and not a reading, and a
 *     line drawn down to it would draw a collapse that never happened.
 *     Its name carries the "‡" the axis uses;
 *   · a WITHHELD row has no figure in either window and is not drawn; the
 *     figure's own notes count it, as they do in every other form.
 */
export function ChartSlopeView({
  spec,
  currentKey,
  priorKey,
  currentLabel,
  priorLabel,
  currentAxisLabel,
  priorAxisLabel,
  formatValue,
  formatTick,
  colorFor,
  onDrill,
  subject,
  tooltip,
  expanded = false,
}: {
  spec: ChartSpec;
  currentKey: string;
  priorKey: string;
  currentLabel: string;
  priorLabel: string;
  /**
   * The two ticks, SHORT.
   *
   * The key below the plot has room for "The window compared against (Jul
   * 1 – Jul 2, 2026)"; a two-tick axis does not — the first tick is
   * centred a few pixels inside the plot's left edge, so half of a
   * 27-character name is drawn outside the figure and clipped by the
   * SVG. Live, that lost the left-hand window's whole name. The ticks
   * take the turn's own dates where the header published them, which is
   * what a reader wants on this axis anyway.
   */
  currentAxisLabel: string;
  priorAxisLabel: string;
  formatValue: (value: number) => string;
  formatTick: (value: number) => string;
  colorFor: (row: ChartRow) => string;
  onDrill: (row: ChartRow) => void;
  subject?: string;
  tooltip?: (props: unknown) => React.ReactElement;
  expanded?: boolean;
}) {
  const cellOf = (row: ChartRow, key: string) => row.cells?.[key];
  const value = (row: ChartRow, key: string): number | undefined => {
    if (row.withheld === true) return undefined;
    if (cellOf(row, key)?.absent === true) return undefined;
    const raw = row.values[key];
    return typeof raw === "number" ? raw : undefined;
  };
  const bounded = (row: ChartRow, key: string): boolean =>
    row.cells !== undefined ? cellOf(row, key)?.bounded === true : row.bounded === true;

  /** One point per window; the category is a SERIES on this shape. */
  const drawable = spec.rows.filter(
    (row) => value(row, priorKey) !== undefined && value(row, currentKey) !== undefined,
  );
  const data = [
    {
      window: priorAxisLabel,
      ...Object.fromEntries(drawable.map((row) => [row.label, value(row, priorKey)])),
    },
    {
      window: currentAxisLabel,
      ...Object.fromEntries(drawable.map((row) => [row.label, value(row, currentKey)])),
    },
  ];
  const undrawn = spec.rows.filter((row) => !drawable.includes(row));

  return (
    <div className={cn("flex flex-col gap-2", expanded && "min-h-0 flex-1")}>
      <div className={cn("w-full", expanded ? "min-h-[18rem] flex-1" : "h-56")}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 10, right: 18, bottom: 0, left: 6 }}>
            <XAxis
              dataKey="window"
              stroke="var(--chart-axis)"
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 12, fill: "var(--chart-axis)" }}
              // Room for the tick's own width either side of it: both
              // ticks are centred on their point, and a point at the edge
              // of the plot draws half its label outside the figure.
              padding={{ left: 72, right: 72 }}
            />
            <YAxis
              stroke="var(--chart-axis)"
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 12, fill: "var(--chart-axis)" }}
              tickFormatter={formatTick}
              width={48}
            />
            {tooltip && (
              <Tooltip
                content={tooltip}
                cursor={{ stroke: "var(--chart-axis)", strokeDasharray: "3 3", strokeOpacity: 0.4 }}
              />
            )}
            {drawable.map((row) => {
              // Either end being a ceiling makes the whole segment a
              // qualified one: the angle between a bound and a reading is
              // not a measured movement, whichever end the bound is on.
              const qualified = bounded(row, priorKey) || bounded(row, currentKey);
              const color = colorFor(row);
              return (
                <Line
                  key={row.label}
                  dataKey={row.label}
                  name={row.label}
                  stroke={color}
                  strokeWidth={row.label === subject ? 3 : 2}
                  {...(qualified ? { strokeDasharray: "4 3", strokeOpacity: 0.8 } : {})}
                  dot={
                    qualified
                      ? { r: 3.5, strokeWidth: 1.5, fill: "var(--card)", stroke: color }
                      : { r: 3.5, strokeWidth: 0, fill: color }
                  }
                  activeDot={{ r: 5, strokeWidth: 0 }}
                  isAnimationActive={false}
                />
              );
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* THE DIRECT LABELS, and the drill. A slope's lines are not
          separable by hue alone (twelve categories, twelve hues, and two
          of them can land on one slot), so every line is named here beside
          its swatch with both readings — and the row is the control, so
          the drill this figure offers survives the change of form. */}
      <ul className="flex flex-wrap gap-x-3 gap-y-0.5">
        {drawable.map((row) => (
          <li key={row.label}>
            <button
              type="button"
              onClick={() => onDrill(row)}
              aria-label={`Drill into ${row.label}`}
              title={`Drill into ${row.label}`}
              className={cn(
                "focus-ring flex items-baseline gap-1.5 rounded px-1 text-micro text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                row.label === subject && "font-medium text-foreground",
              )}
            >
              <span
                aria-hidden
                className="size-2 shrink-0 translate-y-px rounded-[2px]"
                style={{ background: colorFor(row) }}
              />
              {row.label}
              <span className="num">
                {bounded(row, priorKey) ? "≤ " : ""}
                {formatValue(value(row, priorKey) ?? 0)}
                {" → "}
                {bounded(row, currentKey) ? "≤ " : ""}
                {formatValue(value(row, currentKey) ?? 0)}
              </span>
            </button>
          </li>
        ))}
      </ul>

      {/* The categories this shape cannot draw, named rather than
          silently absent. A slope of nine lines under a title promising
          twelve categories is a figure that lost three of them. */}
      {undrawn.length > 0 && (
        <p className="text-micro leading-snug text-muted-foreground">
          No slope is drawn for{" "}
          {undrawn
            .map((row) => `${row.label}${row.withheld === true ? " †" : " ‡"}`)
            .join(", ")}
          : a figure is missing from one of the two windows, and a line drawn to a gap would claim a
          movement nothing measured.
        </p>
      )}
    </div>
  );
}
