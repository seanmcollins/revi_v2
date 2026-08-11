"use client";

import {
  MONITOR_HISTORY_MIN,
  type MonitorReading,
  type MonitorReadingOrigin,
} from "@/lib/monitorHistory";
import { cn } from "@/lib/utils";

/**
 * THE READINGS THIS MONITOR HAS STORED — the sparkline, grown up.
 *
 * `Sparkline` is the glance form: 76×22, no labels, read out of the corner
 * of the eye on a tile somebody is scrolling past. This is the same three
 * numbers when somebody has stopped and opened the monitor, so the two
 * things the small one cannot carry are carried here — WHICH LOAD each
 * point was taken at, and WHAT each point read.
 *
 * IT IS THE SAME VOCABULARY, DELIBERATELY. A ceiling is a hollow point and
 * every segment touching one is dashed, exactly as `Sparkline` and
 * `InvestigationChart` draw a mark the engine did not measure — the three
 * pictures in this product must not disagree about what a hollow dot means.
 * The marks are also stated in WORDS under the chart, because a reader who
 * quotes one of these numbers in a huddle is quoting the ceiling with it.
 *
 * THE AXIS IS PROVENANCE, NOT DATES. The wire publishes three named
 * readings — the load the monitor was created on, the load before this one,
 * and this one — and no date for any of them. A data load's own handle is
 * banned from a client surface, and inventing a date for a point would be
 * worse than naming it for what it is. So the axis says "When you started",
 * "Prior load", "This load", which is exactly what is known.
 *
 * NOTHING IS INTERPOLATED and nothing is drawn below three readings; both
 * rules live in `lib/monitorHistory` and this component depends on them
 * rather than restating them.
 */

/** Intrinsic size. The labels are HTML beneath it, in the app's type scale. */
const W = 256;
const H = 60;
/** Room for the point radius so an extreme reading is not clipped. */
const PAD = 5;

const ORIGIN_LABELS: Readonly<Record<MonitorReadingOrigin, string>> = {
  baseline: "When you started",
  prior: "Prior load",
  current: "This load",
};

/** The words a qualified point carries, so the mark is never only a shape. */
function readingMarks(reading: MonitorReading): string[] {
  return [
    reading.bounded ? "a ceiling, not a measurement" : "",
    reading.provisional ? "still settling" : "",
  ].filter(Boolean);
}

export function MonitorHistoryChart({
  readings,
  className,
}: {
  readings: readonly MonitorReading[];
  className?: string;
}) {
  // The rule's home is `lib/monitorHistory`; this does not depend on being
  // called correctly. Two points are a slope with no shape behind it.
  if (readings.length < MONITOR_HISTORY_MIN) return null;

  const values = readings.map((r) => r.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;

  const x = (index: number): number => PAD + (index * (W - PAD * 2)) / (readings.length - 1);
  // A monitor that has not moved is a flat line down the middle: the shape
  // of "no change" is the fact, and the values are stated beside it.
  const y = (value: number): number =>
    span === 0 ? H / 2 : H - PAD - ((value - min) / span) * (H - PAD * 2);

  const qualified = (r: MonitorReading): boolean => r.bounded || r.provisional;

  const segments = readings.slice(1).map((reading, i) => {
    const previous = readings[i];
    return {
      key: `${previous.watermarkId}->${reading.watermarkId}`,
      x1: x(i),
      y1: y(previous.value),
      x2: x(i + 1),
      y2: y(reading.value),
      // Either end unmeasured makes the whole segment unmeasured — the same
      // joining rule the big line chart uses.
      dashed: qualified(previous) || qualified(reading),
    };
  });

  const anyQualified = readings.some(qualified);

  return (
    <figure
      data-monitor-history={readings.length}
      className={cn("w-[16rem] max-w-full space-y-1", className)}
    >
      <svg
        role="img"
        aria-label={`Readings, oldest first: ${readings
          .map((r) => {
            const marks = readingMarks(r);
            const where = ORIGIN_LABELS[r.origin].toLowerCase();
            return marks.length > 0
              ? `${r.valueText} ${where} (${marks.join(", ")})`
              : `${r.valueText} ${where}`;
          })
          .join(", ")}`}
        data-history-points={readings.length}
        data-history-qualified={readings.filter(qualified).length}
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        preserveAspectRatio="xMidYMid meet"
        className="overflow-visible"
      >
        {/* The baseline of the plot. Not an axis with ticks — there is no
            scale to publish here, and a gridline would imply one. */}
        <line
          x1={0}
          y1={H - 0.5}
          x2={W}
          y2={H - 0.5}
          stroke="var(--border)"
          strokeWidth={1}
        />
        {segments.map((segment) => (
          <line
            key={segment.key}
            x1={segment.x1}
            y1={segment.y1}
            x2={segment.x2}
            y2={segment.y2}
            stroke="var(--chart-current)"
            strokeWidth={1.75}
            strokeLinecap="round"
            {...(segment.dashed ? { strokeDasharray: "4 3", strokeOpacity: 0.75 } : {})}
          />
        ))}
        {readings.map((reading, i) => {
          const hollow = qualified(reading);
          const r = reading.current ? 3.4 : 2.6;
          return (
            <circle
              key={reading.watermarkId}
              cx={x(i)}
              cy={y(reading.value)}
              r={r}
              fill={hollow ? "var(--card)" : "var(--chart-current)"}
              stroke="var(--chart-current)"
              strokeWidth={hollow ? 1.5 : 0}
            />
          );
        })}
      </svg>

      {/* THE AXIS, AS TYPE RATHER THAN AS SVG TEXT. Kept out of the drawing
          so it takes the page's own type scale and its own ink, and so a
          reader who needs 200% zoom gets bigger words rather than a bigger
          picture. Hidden from a screen reader: the figure's own label
          already reads every value with its load and its marks, and a
          second pass over the same three numbers is noise. */}
      <div aria-hidden className="flex items-start justify-between gap-1">
        {readings.map((reading, i) => (
          <p
            key={reading.watermarkId}
            data-history-reading={reading.origin}
            className={cn(
              "num min-w-0 text-micro leading-tight",
              i === 0 ? "text-left" : i === readings.length - 1 ? "text-right" : "text-center",
            )}
          >
            <span className={cn("block", reading.current ? "text-foreground" : "text-muted-foreground")}>
              {reading.valueText}
            </span>
            <span className="block text-muted-foreground">{ORIGIN_LABELS[reading.origin]}</span>
          </p>
        ))}
      </div>

      {anyQualified && (
        // The marks, said. A hollow dot is a shape; "a ceiling, not a
        // measurement" is the claim, and the claim is what somebody repeats.
        <figcaption className="text-micro leading-snug text-muted-foreground">
          Hollow points are{" "}
          {readings
            .filter(qualified)
            .flatMap(readingMarks)
            .filter((mark, i, all) => all.indexOf(mark) === i)
            .join(", or ")}
          . The movement across one of them is not a measured movement, so its line is dashed.
        </figcaption>
      )}
    </figure>
  );
}
