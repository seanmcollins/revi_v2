"use client";

import { MONITOR_HISTORY_MIN, type MonitorReading } from "@/lib/monitorHistory";
import { cn } from "@/lib/utils";

/**
 * THE READINGS THIS MONITOR HAS STORED, AS A LINE.
 *
 * `MonitorTile` shipped with a comment refusing a sparkline: "a 40px trend
 * behind a figure is decoration that implies a shape the tile has not
 * published and cannot defend". That refusal was right about the thing it
 * refused — a shape drawn behind a number, from nothing. It is not an
 * argument against drawing the readings the payload actually carries, which
 * is what this does: one point per STORED EVALUATION, at a named data load
 * (`lib/monitorHistory`), and no point anywhere else.
 *
 * It draws in the big figures' own vocabulary, because the two must not
 * disagree about what a mark means (`InvestigationChart`):
 *
 *   A CEILING IS DRAWN AS AN EDGE, NOT A QUANTITY. A bounded reading is a
 *     HOLLOW point, and every segment that touches it is DASHED — the same
 *     treatment `InvestigationChart` gives a line whose points the engine
 *     did not measure, for the same reason: the movement between two
 *     numbers, one of which is a ceiling, is not a measured movement.
 *   A PROVISIONAL READING IS THE SAME KIND OF MARK. Still settling means
 *     the point will move, so it is hollow and its segments are dashed too.
 *   NO AXIS, NO GRID, NO NUMBERS. This is a shape beside a figure that is
 *     already stated in full above it. Everything it can say, the tile says
 *     in words.
 *
 * IT NEVER ANIMATES. Not gated on `prefers-reduced-motion` — simply static,
 * at every setting, because a line that draws itself in on a page holding
 * four of them is motion nobody asked for and the reduced-motion reader is
 * not owed a different picture from everybody else.
 *
 * IT IS NOT DECORATION FOR A SCREEN READER EITHER. The points are readings
 * that appear nowhere else on the tile — the creation baseline in
 * particular — so the figure carries `role="img"` and names them, oldest
 * first, with their marks.
 */

/** Intrinsic size. Small enough to sit on the delta line, big enough to read. */
const W = 76;
const H = 22;
/** Room for the point radius so an extreme reading is not clipped. */
const PAD = 3;

export function Sparkline({
  readings,
  className,
}: {
  readings: readonly MonitorReading[];
  className?: string;
}) {
  // TWO POINTS ARE NOT A TREND. The caller is expected to have checked, but
  // this is the rule's home and it does not depend on being called
  // correctly: a line drawn through two dots is a slope with no shape
  // behind it.
  if (readings.length < MONITOR_HISTORY_MIN) return null;

  const values = readings.map((r) => r.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;

  const x = (index: number): number =>
    PAD + (index * (W - PAD * 2)) / (readings.length - 1);
  // A monitor that has not moved is a FLAT line down the middle, not a
  // divide-by-zero and not a line pinned to the floor: the shape of "no
  // change" is the fact, and the tile states the values beside it.
  const y = (value: number): number =>
    span === 0 ? H / 2 : H - PAD - ((value - min) / span) * (H - PAD * 2);

  /** A point the engine did not measure as a settled figure. */
  const qualified = (r: MonitorReading): boolean => r.bounded || r.provisional;

  const segments = readings.slice(1).map((reading, i) => {
    const previous = readings[i];
    return {
      key: `${previous.watermarkId}->${reading.watermarkId}`,
      x1: x(i),
      y1: y(previous.value),
      x2: x(i + 1),
      y2: y(reading.value),
      // Either end unmeasured makes the whole segment unmeasured — the
      // same joining rule the big line chart uses.
      dashed: qualified(previous) || qualified(reading),
    };
  });

  return (
    <svg
      role="img"
      aria-label={`Readings, oldest first: ${readings
        .map((r) => {
          const marks = [
            r.bounded ? "a ceiling, not a measurement" : "",
            r.provisional ? "still settling" : "",
          ].filter(Boolean);
          return marks.length > 0 ? `${r.valueText} (${marks.join(", ")})` : r.valueText;
        })
        .join(", ")}`}
      data-sparkline-points={readings.length}
      data-sparkline-qualified={readings.filter(qualified).length}
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      className={cn("shrink-0 overflow-visible", className)}
    >
      {segments.map((segment) => (
        <line
          key={segment.key}
          x1={segment.x1}
          y1={segment.y1}
          x2={segment.x2}
          y2={segment.y2}
          stroke="var(--chart-current)"
          strokeWidth={1.5}
          strokeLinecap="round"
          {...(segment.dashed
            ? { strokeDasharray: "3 2", strokeOpacity: 0.75 }
            : {})}
        />
      ))}
      {readings.map((reading, i) => {
        const hollow = qualified(reading);
        // The newest reading is the one the figure above is quoting, so it
        // is the one the eye should end on.
        const r = reading.current ? 2.6 : 1.9;
        return (
          <circle
            key={reading.watermarkId}
            cx={x(i)}
            cy={y(reading.value)}
            r={r}
            fill={hollow ? "var(--card)" : "var(--chart-current)"}
            stroke="var(--chart-current)"
            strokeWidth={hollow ? 1.4 : 0}
          />
        );
      })}
    </svg>
  );
}
