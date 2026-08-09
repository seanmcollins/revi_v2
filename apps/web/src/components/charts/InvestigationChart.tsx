"use client";

import { Maximize2 } from "lucide-react";
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

import { Button } from "@/components/ui/button";
import { formatMeasure, formatMeasureTick } from "@/lib/format";
import { useSessionStore } from "@/lib/store";
import type { ChartSpec } from "@/lib/types";
import { usePrefersReducedMotion } from "@/lib/useReducedMotion";

const SERIES_COLOR: Record<"current" | "baseline", string> = {
  current: "var(--chart-current)",
  baseline: "var(--chart-baseline)",
};

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
  spec,
  turnId,
  windowLabel,
}: {
  spec: ChartSpec;
  turnId: string;
  /** The turn's window, appended to the composed title (see AnswerCard). */
  windowLabel?: string;
}) {
  const emitRefinement = useSessionStore((s) => s.emitRefinement);
  const reducedMotion = usePrefersReducedMotion();

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
        {spec.series.length > 1 && (
          <span className="flex items-center gap-3">
            {spec.series.map((s) => (
              <span
                key={s.key}
                className="flex items-center gap-1 text-[0.62rem] text-muted-foreground"
              >
                <span
                  className="size-2 rounded-[2px]"
                  style={{ background: SERIES_COLOR[s.role] }}
                />
                {s.label}
              </span>
            ))}
          </span>
        )}
      </figcaption>

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
                  stroke={SERIES_COLOR[s.role]}
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
              {spec.series.map((s) => (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  name={s.label}
                  fill={SERIES_COLOR[s.role]}
                  radius={[3, 3, 0, 0]}
                  maxBarSize={spec.series.length > 1 ? 14 : 22}
                  className="cursor-pointer"
                  onClick={handleBarClick}
                  {...drawIn}
                />
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>

      <div className="mt-1.5 flex items-center justify-between">
        <span className="text-[0.62rem] text-muted-foreground">
          {spec.xLabel ?? (spec.kind !== "line" ? "click a bar to drill in" : "")}
        </span>
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
      </div>
    </figure>
  );
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
