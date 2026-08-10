/**
 * WHAT A MONITOR HAS ACTUALLY READ, LOAD BY LOAD.
 *
 * A tile publishes three numbers about itself and only ever draws one: the
 * value at this load, the value at the load before it (`delta.prior_value`)
 * and the value at the load the monitor was created on
 * (`baseline_delta.prior_value`). Those are readings this platform took and
 * stored, at named data loads — not a series it interpolated — so a small
 * line through them is the only trend on this surface that costs nothing in
 * honesty.
 *
 * `MonitorTile` used to carry a comment saying a sparkline "implies a shape
 * the tile has not published and cannot defend". That was true of a
 * decorative 40px trend behind the figure. It is not true of these points:
 * each one is a stored evaluation with its own watermark id, and the three
 * rules below are what keep it that way.
 *
 *   NOTHING IS INVENTED BETWEEN THE POINTS. A reading exists or it does
 *     not; a load the monitor did not evaluate is simply absent from the
 *     list, and no point is placed for it.
 *   TWO POINTS ARE NOT A TREND. `MONITOR_HISTORY_MIN` is 3. A line through
 *     two dots is a slope with no shape behind it, and drawing one on the
 *     five tiles that carry a single prior reading would put a made-up
 *     trajectory on the majority of this tenant's monitors.
 *   A CEILING IS NOT A MEASUREMENT, AT ANY SIZE. The payload publishes each
 *     historical reading's own rendered text, and a reading the engine
 *     bounded carries the "≤" in it — that is the only per-reading evidence
 *     on the wire, so it is what the bound is read from. The CURRENT reading
 *     additionally has the tile's own `integrity`, which is where
 *     "provisional" comes from; no historical point is ever claimed to be
 *     provisional, because nothing on the wire says whether it was.
 */

import type { MonitorsDelta, MonitorsTile } from "@/lib/monitors";

/** Below this many stored readings, no line is drawn. See the note above. */
export const MONITOR_HISTORY_MIN = 3;

/** One stored evaluation of a monitor, at one data load. */
export interface MonitorReading {
  /** The data load this reading was taken at. */
  watermarkId: string;
  /** The value, in the contract's unit, as the server published it. */
  value: number;
  /** The server's own rendering of it — carries any `≤`. */
  valueText: string;
  /** This reading is a ceiling over a population too small to measure. */
  bounded: boolean;
  /** This reading has not finished settling. Only ever known for the newest. */
  provisional: boolean;
  /** The reading taken at the load on screen. */
  current: boolean;
}

/** The "≤" the engine puts in front of a ceiling, wherever it renders it. */
const CEILING = /[≤<]/;

function priorReading(delta: MonitorsDelta | undefined): MonitorReading | undefined {
  if (delta === undefined) return undefined;
  if (delta.priorValue === undefined) return undefined;
  if (delta.priorWatermarkId === "") return undefined;
  return {
    watermarkId: delta.priorWatermarkId,
    value: delta.priorValue,
    valueText: delta.priorValueText,
    bounded: CEILING.test(delta.priorValueText),
    // Nothing on the wire says whether a stored reading was still
    // settling when it was taken, so nothing here claims it was.
    provisional: false,
    current: false,
  };
}

/**
 * This monitor's stored readings, oldest first.
 *
 * Order is by provenance rather than by a date: the creation baseline is
 * older than the prior load, which is older than this one. That is the only
 * ordering the payload supports — the readings carry watermark ids, not
 * timestamps this client may sort on.
 *
 * De-duplicated by watermark id, because a monitor created one load ago
 * publishes the SAME load as both its baseline and its prior — two names for
 * one reading, and drawing it twice would invent a flat segment.
 */
export function monitorReadings(tile: MonitorsTile): MonitorReading[] {
  const readings: MonitorReading[] = [];
  const seen = new Set<string>();
  const push = (reading: MonitorReading | undefined): void => {
    if (reading === undefined) return;
    if (seen.has(reading.watermarkId)) return;
    seen.add(reading.watermarkId);
    readings.push(reading);
  };

  push(priorReading(tile.baselineDelta));
  push(priorReading(tile.delta));

  if (tile.status === "ok" && tile.value !== undefined && tile.watermarkId !== "") {
    push({
      watermarkId: tile.watermarkId,
      value: tile.value,
      valueText: tile.valueText,
      // Both sources agree on the live payload; either one alone is
      // enough to refuse to draw this point as a measurement.
      bounded: tile.integrity.isBound || CEILING.test(tile.valueText),
      provisional: tile.integrity.provisional,
      current: true,
    });
  }

  return readings;
}

/** Whether this monitor has enough stored readings to draw a line at all. */
export function hasDrawableHistory(tile: MonitorsTile): boolean {
  return monitorReadings(tile).length >= MONITOR_HISTORY_MIN;
}
