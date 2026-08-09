"use client";

import { Maximize2 } from "lucide-react";
import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { DownloadCsvButton } from "@/components/answer/AnswerActions";
import { Button } from "@/components/ui/button";
import { capChartSeries, OTHERS_SERIES_KEY } from "@/lib/contract";
import { chartToCsv } from "@/lib/export";
import { formatMeasure, formatMeasureTick } from "@/lib/format";
import { useSessionStore } from "@/lib/store";
import type { ChartSeries, ChartSpec } from "@/lib/types";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";

/**
 * TWO series are a comparison — current against its baseline — and they
 * keep the semantic pair the rest of the product uses.
 */
const ROLE_COLOR: Record<"current" | "baseline", string> = {
  current: "var(--chart-current)",
  baseline: "var(--chart-baseline)",
};

/**
 * THREE or more are identities, and identity needs a categorical palette:
 * eight validated slots, assigned in fixed order and never cycled (the
 * order is the colour-vision safety mechanism, not a preference). Anything
 * past the eighth is not given a ninth hue — `capChartSeries` has already
 * folded it into the rollup, which wears the neutral ink because it is not
 * an entity.
 */
const CATEGORICAL = [
  "var(--chart-cat-1)",
  "var(--chart-cat-2)",
  "var(--chart-cat-3)",
  "var(--chart-cat-4)",
  "var(--chart-cat-5)",
  "var(--chart-cat-6)",
  "var(--chart-cat-7)",
  "var(--chart-cat-8)",
] as const;

function seriesColors(series: readonly ChartSeries[]): Record<string, string> {
  const out: Record<string, string> = {};
  let slot = 0;
  for (const s of series) {
    if (s.key === OTHERS_SERIES_KEY) {
      out[s.key] = "var(--chart-cat-other)";
      continue;
    }
    out[s.key] =
      series.length <= 2
        ? ROLE_COLOR[s.role]
        : (CATEGORICAL[slot] ?? "var(--chart-cat-other)");
    slot += 1;
  }
  return out;
}

interface RowDatum {
  label: string;
  referent?: string;
  [key: string]: string | number | undefined;
}

/**
 * Charts are live objects: clicking a bar emits a typed
 * `{op: "DrillInto", target}` refinement — no natural language in the
 * loop. Truncation is always surfaced ("showing top 8 of 12 — Expand").
 */
export function InvestigationChart({
  spec: published,
  turnId,
  windowLabel,
  watermarkId,
  packLabel,
  question,
  investigationId,
  caveats,
}: {
  spec: ChartSpec;
  turnId: string;
  /** The turn's window, appended to the composed title (see AnswerCard). */
  windowLabel?: string;
  /** The data load these rows were measured at — it names the CSV file. */
  watermarkId?: string;
  /** The metric pack that defined the measure, for the CSV's provenance. */
  packLabel?: string;
  /** The question this chart answered. */
  question?: string;
  investigationId?: string;
  /** The turn's caveats, so the CSV cannot leave without them. */
  caveats?: readonly string[];
}) {
  const emitRefinement = useSessionStore((s) => s.emitRefinement);
  const reducedMotion = usePrefersReducedMotion();

  // What is DRAWN is capped; what is EXPORTED is everything. Thirty plan
  // series in two colours was unreadable, and a CSV that matched the
  // picture would have dropped the rows an analyst came for.
  const { spec, hiddenSeries, note: capNote } = useMemo(
    () => capChartSeries(published),
    [published],
  );
  const colors = useMemo(() => seriesColors(spec.series), [spec.series]);
  // A composition is drawn as one. Recharts stacks by shared `stackId`, and
  // a `stacked_bar` frame drawn without one claims a comparison the engine
  // never published.
  const stackId = spec.stacked && spec.kind !== "line" ? spec.id : undefined;

  /**
   * Draw in ONCE, fast (260ms ease-out) — Recharts' 1.5s default is a
   * different product's motion language, and it re-runs on every data
   * change. `animationId` is pinned to the spec so a re-render (theme
   * flip, focus change) never replays the draw.
   */
  const drawIn = {
    isAnimationActive: !reducedMotion,
    animationDuration: 260,
    animationEasing: "ease-out",
    animationId: spec.id,
  } as const;

  const data: RowDatum[] = spec.rows.map((row) => ({
    label: row.label,
    referent: row.referent,
    ...row.values,
  }));

  const handleBarClick = (entry: unknown) => {
    const payload = (entry as { payload?: RowDatum }).payload;
    if (!payload) return;
    emitRefinement(
      { op: "DrillInto", target: payload.referent ?? `${spec.id}:${payload.label}` },
      { turnId, referent: payload.referent },
    );
  };

  // Values arrive in their DISPLAY unit: `mapChartSpec` scales a wire
  // `ratio` frame (0.079945) into percentage points (7.9945) once, at the
  // boundary, so no renderer has to know which convention it was handed.
  const formatValue = (value: number): string => formatMeasure(value, spec.unit);
  const formatTick = (value: number): string => formatMeasureTick(value, spec.unit);

  const axisProps = {
    stroke: "var(--chart-axis)",
    tickLine: false,
    axisLine: false,
    tick: { fontSize: 10, fill: "var(--chart-axis)" },
  } as const;

  const tooltipContent = (props: unknown) => (
    <ChartTooltipContent {...(props as TooltipRenderProps)} formatValue={formatValue} />
  );

  return (
    <figure className="rounded-lg border bg-card p-3.5">
      <figcaption className="mb-1 flex items-baseline justify-between gap-2">
        <span className="text-[0.72rem] font-medium" title={spec.wireTitle}>
          {/* Composed from the frame's own columns ("Cash posted by
              payer"), not the engine's frame bookkeeping ("cash posted —
              cash by payer  compare"). The published title stays on the
              `title` attribute so the reduction is checkable. */}
          {spec.title}
          {windowLabel && (
            <span className="font-normal text-muted-foreground"> — {windowLabel}</span>
          )}
        </span>
        {/* A legend is present for every multi-series chart and names each
            series beside its swatch: three of the eight light-mode hues
            sit below 3:1 on white, and the rule for that is relief — the
            identity must never be carried by colour alone. The swatch is
            the cue; the label is the fact. */}
        {spec.series.length > 1 && (
          <span className="flex max-w-[60%] flex-wrap items-center justify-end gap-x-2.5 gap-y-0.5">
            {spec.series.map((s) => (
              <span
                key={s.key}
                className="flex items-center gap-1 text-[0.62rem] text-muted-foreground"
              >
                <span className="size-2 rounded-[2px]" style={{ background: colors[s.key] }} />
                {s.label}
              </span>
            ))}
          </span>
        )}
      </figcaption>

      {/* Said above the picture, not in a tooltip: a reader who does not
          know eleven series were folded into one mark is reading a
          different chart from the one the data supports. */}
      {capNote && (
        <p className="mb-1.5 text-[0.62rem] leading-snug text-muted-foreground">{capNote}</p>
      )}

      <div className="h-52 w-full">
        <ResponsiveContainer width="100%" height="100%">
          {spec.kind === "line" ? (
            <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis dataKey="label" {...axisProps} interval="preserveStartEnd" />
              <YAxis {...axisProps} tickFormatter={formatTick} width={48} />
              <Tooltip
                content={tooltipContent}
                cursor={{ stroke: "var(--chart-axis)", strokeDasharray: "3 3", strokeOpacity: 0.4 }}
              />
              {spec.highlightLabel && (
                <ReferenceLine
                  x={spec.highlightLabel}
                  stroke="var(--warning)"
                  strokeDasharray="4 3"
                  strokeOpacity={0.7}
                />
              )}
              {spec.series.map((s) => (
                <Line
                  key={s.key}
                  dataKey={s.key}
                  name={s.label}
                  stroke={colors[s.key]}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                  {...drawIn}
                />
              ))}
            </LineChart>
          ) : (
            <BarChart
              data={data}
              margin={{ top: 8, right: 8, bottom: 0, left: 0 }}
              barGap={2}
              barCategoryGap="26%"
            >
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" />
              <XAxis
                dataKey="label"
                {...axisProps}
                interval={0}
                tickFormatter={(v: string) => (v.length > 11 ? `${v.slice(0, 10)}…` : v)}
              />
              <YAxis {...axisProps} tickFormatter={formatTick} width={48} />
              <Tooltip content={tooltipContent} cursor={{ fill: "var(--chart-grid)" }} />
              {spec.highlightLabel && (
                <ReferenceLine
                  x={spec.highlightLabel}
                  stroke="var(--warning)"
                  strokeDasharray="4 3"
                  strokeOpacity={0.7}
                />
              )}
              {spec.series.map((s, i) => (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  name={s.label}
                  {...(stackId ? { stackId } : {})}
                  fill={colors[s.key]}
                  // A stack's segments are one column: only the top one
                  // gets the rounded cap, and a 2px surface gap keeps
                  // adjacent fills from reading as a single mass.
                  radius={stackId && i < spec.series.length - 1 ? 0 : [3, 3, 0, 0]}
                  {...(stackId ? { stroke: "var(--card)", strokeWidth: 2 } : {})}
                  maxBarSize={stackId ? 26 : spec.series.length > 1 ? 14 : 22}
                  className="cursor-pointer"
                  onClick={handleBarClick}
                  {...drawIn}
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>

      <div className="mt-1.5 flex items-center justify-between gap-2">
        <span className="text-[0.62rem] text-muted-foreground">
          {spec.xLabel ?? (spec.kind !== "line" ? "click a bar to drill in" : "")}
          {/* What put the bars in this order. The chart sits under findings
              that read "best to worst", so an axis whose order it does not
              state is an axis the reader has to assume — and the assumption
              was wrong: it was alphabetical. */}
          {orderNote(spec) && (
            <span className="text-muted-foreground"> · {orderNote(spec)}</span>
          )}
        </span>
        <span className="flex items-center gap-1">
          {spec.truncation && spec.truncation.total > spec.truncation.shown && (
            <Button
              variant="ghost"
              size="xs"
              className="h-5 gap-1 px-1.5 text-[0.65rem] font-normal text-warning hover:text-warning"
              onClick={() => emitRefinement({ op: "Expand" }, { turnId })}
            >
              showing top {spec.truncation.shown} of {spec.truncation.total}
              <Maximize2 className="size-2.5" />
              Expand
            </Button>
          )}
          {/* The rows behind the picture, in the unit the picture draws
              them in. A chart an analyst cannot get the numbers out of is
              a chart they photograph and retype. Client-side only: these
              rows are already in this browser. */}
          {/* The PUBLISHED spec, not the drawn one: every series, including
              the ones folded into the rollup, and every caveat the turn
              attached. An export that matched the picture would be
              complete-looking and short by eleven columns. */}
          <DownloadCsvButton
            label="CSV"
            title={`Download the ${published.rows.length} row${published.rows.length === 1 ? "" : "s"} and ${published.series.length} series behind this chart as CSV, in the unit shown, with this answer's caveats as comment lines above them. Nothing leaves this browser.`}
            filenameKind="chart"
            filenameTag={published.title || published.id}
            {...(watermarkId ? { watermark: watermarkId } : {})}
            className="h-5 px-1.5 text-[0.62rem]"
            csv={() =>
              chartToCsv(published, {
                ...(windowLabel ? { windowLabel } : {}),
                ...(watermarkId ? { watermarkId } : {}),
                ...(packLabel ? { packLabel } : {}),
                ...(question ? { question } : {}),
                ...(investigationId ? { investigationId } : {}),
                ...(caveats && caveats.length > 0 ? { caveats } : {}),
                ...(hiddenSeries > 0 && capNote ? { renderNote: capNote } : {}),
              })
            }
          />
        </span>
      </div>
    </figure>
  );
}

/**
 * "ordered by denied_dollars, high to low" — or nothing at all when the
 * rows are simply in the order the engine emitted them, which is a fact
 * this figure should not dress up as a ranking.
 */
export function orderNote(spec: ChartSpec): string | undefined {
  const order = spec.order;
  if (order === undefined || order.basis === "wire") return undefined;
  if (order.basis === "ordinal-bucket") return "ordered by bucket";
  const direction = order.descending === false ? "low to high" : "high to low";
  return order.by ? `ordered by ${order.by}, ${direction}` : `ordered ${direction}`;
}

interface TooltipEntry {
  dataKey?: string | number;
  value?: number | string;
  name?: string | number;
  color?: string;
  payload?: RowDatum;
}

interface TooltipRenderProps {
  active?: boolean;
  label?: string | number;
  payload?: TooltipEntry[];
}

function ChartTooltipContent({
  active,
  label,
  payload,
  formatValue,
}: TooltipRenderProps & {
  formatValue: (value: number) => string;
}) {
  if (!active || !payload || payload.length === 0) return null;
  const referent = payload[0]?.payload?.referent;
  return (
    <div className="rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
      <p className="mb-1 flex items-center gap-1.5 font-medium">
        {String(label)}
        {referent && (
          <span className="rounded border border-verified/40 bg-verified/10 px-1 font-mono text-[0.6rem] text-verified">
            {referent}
          </span>
        )}
      </p>
      <ul className="space-y-0.5">
        {payload.map((entry) => (
          <li key={String(entry.dataKey)} className="flex items-center justify-between gap-4">
            <span className="flex items-center gap-1.5 text-muted-foreground">
              <span className="size-2 rounded-[2px]" style={{ background: entry.color }} />
              {String(entry.name)}
            </span>
            <span className="num font-medium">
              {typeof entry.value === "number" ? formatValue(entry.value) : entry.value}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
