"use client";

import { Maximize2 } from "lucide-react";
import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
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
  /** This row is a CEILING, not a measurement (see `ChartRow.bounded`). */
  bounded?: boolean;
  /** The population the ceiling was taken over. */
  denominator?: number;
  /** The bucket is calendar-partial or still adjudicating. */
  provisional?: boolean;
  [key: string]: string | number | boolean | undefined;
}

/**
 * The mark treatment for a cell the engine published as a ceiling.
 *
 * Desaturated fill plus a dashed outline: the bar reads as an EDGE rather
 * than a quantity, and the difference survives a screenshot, a projector
 * and colour-vision deficiency, because none of it is carried by hue. The
 * "≤" on the axis tick and in the tooltip is the text half of the same
 * signal — relief, never colour alone.
 */
const BOUNDED_MARK = {
  fillOpacity: 0.2,
  strokeWidth: 1.5,
  strokeDasharray: "3 2",
} as const;

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

  const hasBounded = spec.rows.some((row) => row.bounded === true);
  const hasProvisional = spec.rows.some((row) => row.provisional === true);

  /**
   * A provisional point is not the terminus of a solid line.
   *
   * The engine publishes the sentence ("the week of 2026-07-20 point is
   * PROVISIONAL and is excluded from that movement") and the wire carries
   * the flag; the path drew straight through to it anyway, so the strongest
   * honesty feature in the build was invisible everywhere except the prose.
   *
   * Each series is drawn TWICE from the same rows: the settled key, whose
   * values are blank on provisional buckets so the solid path terminates at
   * the last settled one, and a `…__provisional` key carrying the
   * provisional points plus the settled point before them, so the dashed
   * segment joins up without asserting its endpoint has stopped moving.
   *
   * Written for the general case (a provisional bucket anywhere in the
   * series), not only the terminal one: a censored middle bucket is exactly
   * as unfinished as a censored last one.
   */
  const provisionalKey = (key: string): string => `${key}__provisional`;
  const data: RowDatum[] = spec.rows.map((row, index) => {
    const datum: RowDatum = {
      label: row.label,
      referent: row.referent,
      ...(row.bounded === true ? { bounded: true } : {}),
      ...(row.denominator !== undefined ? { denominator: row.denominator } : {}),
      ...(row.provisional === true ? { provisional: true } : {}),
    };
    const pending = row.provisional === true;
    const joins = pending || spec.rows[index + 1]?.provisional === true;
    for (const [key, value] of Object.entries(row.values)) {
      if (!pending) datum[key] = value;
      if (hasProvisional && joins) datum[provisionalKey(key)] = value;
    }
    return datum;
  });

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

  // The axis says which categories are ceilings, in text. Colour and
  // outline carry it on the mark; the tick carries it for anyone reading
  // the labels, printing in greyscale, or looking at a screenshot.
  const boundedLabels = new Set(
    spec.rows.filter((row) => row.bounded === true).map((row) => row.label),
  );
  const provisionalLabels = new Set(
    spec.rows.filter((row) => row.provisional === true).map((row) => row.label),
  );
  const categoryTick = (v: string): string => {
    const short = v.length > 11 ? `${v.slice(0, 10)}…` : v;
    if (boundedLabels.has(v)) return `≤ ${short}`;
    if (provisionalLabels.has(v)) return `${short}*`;
    return short;
  };

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

      {/* The engine's own census of this figure. It rides on the wire as
          `annotations[0]` ("upper bounds: 4 of 12 marks are ceilings, not
          measurements…") and was being fed to a `ReferenceLine` as an x
          value, where it matched no category and drew nothing at all. */}
      {spec.note && (
        <p className="mb-1.5 text-[0.62rem] leading-snug text-warning">{spec.note}</p>
      )}

      {/* THE RANKING WAS REFUSED. Said above the picture, at full width,
          because the alternative is what shipped: a bar chart sorted by
          value 400px below a banner explaining that ordering ceilings
          against measurements sorts by population size. */}
      {spec.order?.refused && (
        <p className="mb-1.5 rounded border border-warning/40 bg-warning/10 px-2 py-1 text-[0.65rem] leading-snug">
          <span className="font-medium">No ranking is published on this answer.</span>{" "}
          <span className="text-muted-foreground">
            These marks are in the order the engine emitted them — read them as a set, not as a
            league table.
          </span>
        </p>
      )}

      {/* The rows the wire sent are not uniquely keyed by the axes this
          chart declares, and this measure cannot be added up — so there is
          no figure to draw and none is drawn. The rows are still in the
          CSV, exactly as they arrived. */}
      {spec.keying?.mode === "unkeyable" ? (
        <div className="rounded-md border border-dashed bg-surface-sunken/60 px-3 py-4 text-[0.7rem] leading-snug text-muted-foreground">
          <p className="font-medium text-foreground">This chart is not drawn</p>
          <p className="mt-1">{spec.keying.note}</p>
        </div>
      ) : (
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
              {/* The unsettled tail. Dashed, hollow-dotted, and drawn from
                  the last SETTLED point so the segment joins up without
                  claiming its endpoint is final. `legendType: none` — it is
                  the same series in a different state, not a second one. */}
              {hasProvisional &&
                spec.series.map((s) => (
                  <Line
                    key={provisionalKey(s.key)}
                    dataKey={provisionalKey(s.key)}
                    name={`${s.label} (provisional)`}
                    stroke={colors[s.key]}
                    strokeWidth={2}
                    strokeDasharray="4 3"
                    legendType="none"
                    dot={{ r: 3, strokeWidth: 1.5, fill: "var(--card)", stroke: colors[s.key] }}
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
                tickFormatter={categoryTick}
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
                >
                  {/* A ceiling does not draw as a quantity. Per-cell, so a
                      bounded category is visibly a different KIND of mark
                      from the measured ones beside it — the defect a buyer
                      would have screenshotted was twelve identical bars,
                      four of which were suppression bounds. */}
                  {(hasBounded || hasProvisional) &&
                    data.map((row) => (
                      <Cell
                        key={`${s.key}:${row.label}`}
                        fill={colors[s.key]}
                        {...(row.bounded === true || row.provisional === true
                          ? { ...BOUNDED_MARK, stroke: colors[s.key] }
                          : {})}
                      />
                    ))}
                </Bar>
              ))}
            </BarChart>
          )}
        </ResponsiveContainer>
      </div>
      )}

      {/* The keying census, under the picture it explains. Live, a chart
          declaring `x=month, series=payer` sent thirty rows over three
          distinct keys; the old mapper kept whichever arrived last and drew
          $3,468 of $441,808. */}
      {spec.keying?.mode === "summed" && (
        <p className="mt-1.5 text-[0.62rem] leading-snug text-warning">{spec.keying.note}</p>
      )}

      {/* What the marks mean when some of them are not measurements.
          Composed as ONE string per fact rather than interpolated across
          spans: this sentence is read aloud, copied out of a screenshot
          and searched for, and a phrase split across three text nodes is
          none of those things. */}
      {(hasBounded || hasProvisional) && (
        <p className="mt-1.5 text-[0.62rem] leading-snug text-muted-foreground">
          {hasBounded && <span className="block">{boundedLegend(spec)}</span>}
          {hasProvisional && (
            <span className="block">
              * marks a provisional bucket: still settling, so its value will move.
            </span>
          )}
        </p>
      )}

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
 * What "≤" means on this figure, as one sentence with the census in it.
 *
 * Exported so the wording is pinned in one place: the same census reaches
 * the CSV preamble and the copied text, and three surfaces of one answer
 * counting its ceilings differently is how this class of defect starts.
 */
export function boundedLegend(spec: ChartSpec): string {
  const bounded = spec.boundedRows ?? spec.rows.filter((row) => row.bounded === true).length;
  const one = bounded === 1;
  const total = spec.rows.length;
  return (
    `≤ marks an upper bound — ${bounded} of ${total} ${total === 1 ? "mark" : "marks"} ` +
    `${one ? "is a ceiling" : "are ceilings"} over a suppressed numerator, ` +
    `not ${one ? "a measurement" : "measurements"}, and ${one ? "it is" : "they are"} ` +
    "not ranked against the measured ones."
  );
}

/**
 * "ordered by denied_dollars, high to low" — or nothing at all when the
 * rows are simply in the order the engine emitted them, which is a fact
 * this figure should not dress up as a ranking.
 *
 * Two facts it now also states. A REFUSED ranking is not the same as an
 * unstated one, and saying "unranked" out loud is the difference between a
 * reader treating the leftmost bar as the worst offender and treating it as
 * the first row the engine happened to emit. And an order that HELD BOUNDED
 * CELLS OUT of itself says so, because those marks sit at the end of the
 * axis and would otherwise read as the smallest values on it.
 */
export function orderNote(spec: ChartSpec): string | undefined {
  const order = spec.order;
  if (order === undefined) return undefined;
  if (order.refused === true) return "unranked — no ranking was published for this answer";
  if (order.basis === "wire") return undefined;
  const held =
    order.boundedExcluded !== undefined
      ? `; ${order.boundedExcluded} bounded cell${order.boundedExcluded === 1 ? "" : "s"} held out of it, at the end`
      : "";
  // The catalog SAID so, and the difference from the line below it is
  // worth the extra words: one is a published fact about the dimension,
  // the other is this client reading numbers out of label text.
  if (order.basis === "axis-order")
    return order.by
      ? `in the catalog's declared order for ${order.by}${held}`
      : `in the catalog's declared bucket order${held}`;
  if (order.basis === "ordinal-bucket") return `ordered by bucket${held}`;
  const direction = order.descending === false ? "low to high" : "high to low";
  return order.by
    ? `ordered by ${order.by}, ${direction}${held}`
    : `ordered ${direction}${held}`;
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
  const row = payload[0]?.payload;
  const referent = row?.referent;
  // The hover is where a reader goes to read one number exactly. A ceiling
  // read there as a measurement is the same lie the bar told, at higher
  // precision.
  const bounded = row?.bounded === true;
  const provisional = row?.provisional === true;
  const denominator = typeof row?.denominator === "number" ? row.denominator : undefined;
  return (
    // BOUNDED. A series key is not always a short name: the server folds an
    // undeclared grouping column into `series` as a `" / "`-joined
    // composite ("Orthopedic Surgery / CO"), so eight rows of an unbounded
    // popover grew wider than the figure it belongs to. The names wrap
    // inside the cap instead of being truncated — a half-read identity on
    // the one surface an analyst goes to for an exact number is worse than
    // two lines — and the number itself never wraps away from its label.
    <div className="max-w-72 rounded-md border bg-popover px-3 py-2 text-xs shadow-md">
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
          <li
            key={String(entry.dataKey)}
            className="flex items-start justify-between gap-4 leading-snug"
          >
            <span className="flex min-w-0 items-start gap-1.5 text-muted-foreground">
              <span
                className="mt-[0.28rem] size-2 shrink-0 rounded-[2px]"
                style={{ background: entry.color }}
              />
              {String(entry.name)}
            </span>
            <span className="num shrink-0 font-medium">
              {typeof entry.value === "number"
                ? `${bounded ? "≤ " : ""}${formatValue(entry.value)}`
                : entry.value}
            </span>
          </li>
        ))}
      </ul>
      {bounded && (
        <p className="mt-1 max-w-56 border-t pt-1 text-[0.65rem] leading-snug text-warning">
          Upper bound{denominator !== undefined ? ` over n = ${denominator}` : ""} — a ceiling, not
          a measurement. It has no position in a ranking.
        </p>
      )}
      {provisional && (
        <p className="mt-1 max-w-56 border-t pt-1 text-[0.65rem] leading-snug text-warning">
          Provisional — this bucket is calendar-partial or still adjudicating, so the value will
          move.
        </p>
      )}
    </div>
  );
}
