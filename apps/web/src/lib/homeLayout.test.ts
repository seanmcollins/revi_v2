/**
 * THE EVOLUTION RULE, as arithmetic.
 *
 * The whole "ready to evolve as somebody sets up their own monitors" claim
 * reduces to one pure function, so it is asserted as one — three states,
 * each from the payloads the wire already carries. `Home.test.tsx` asserts
 * that the page actually renders in the order this returns; this file
 * asserts the order is right.
 */

import { describe, expect, it } from "vitest";

import { homeShape, movedPinIds } from "@/lib/homeLayout";
import type { BriefEntry, MonitorsDelta, MonitorsTile } from "@/lib/monitors";

function delta(overrides: Partial<MonitorsDelta> = {}): MonitorsDelta {
  return {
    priorWatermarkId: "wm_002",
    priorValueText: "22.2%",
    valueText: "29.5%",
    deltaText: "7.3 points",
    direction: "up",
    comparable: true,
    reference: "prior_load",
    sameWindow: false,
    material: true,
    thresholdSource: "governed",
    belowGovernedGate: false,
    materialityRule: "ratio.min_points",
    materialityNote: "",
    ...overrides,
  };
}

function tile(pinId: string, overrides: Partial<MonitorsTile> = {}): MonitorsTile {
  return {
    pinId,
    label: `Monitor ${pinId}`,
    presentation: "finding",
    status: "ok",
    watermarkId: "wm_003",
    headlineTitle: "",
    headlineStatement: "",
    headlineSubjectLabel: "",
    valueText: "29.5%",
    integrity: {
      grade: "direct",
      thingsToKnow: 0,
      thingsToKnowCaution: 0,
      caveatCodes: [],
      checks: 3,
      isBound: false,
      provisional: false,
    },
    warnings: [],
    findings: [],
    ...overrides,
  };
}

function entry(kind: string, pinId?: string): BriefEntry {
  return {
    kind,
    title: "t",
    statement: "s",
    ...(pinId !== undefined ? { pinId } : {}),
    provenance: { source: "pinned_spec", watermarkId: "wm_003", method: "" },
  };
}

describe("the evolution rule — three states, and nothing stored to decide them", () => {
  it("NO MONITORS: the anomalies are the page, and the zone is an invitation", () => {
    const shape = homeShape({ tiles: [], entries: [] });
    expect(shape.order).toBe("anomalies_first");
    expect(shape.invitation).toBe(true);
    expect(shape.monitorCount).toBe(0);
  });

  it("MONITORS THAT MOVED: the digest goes above the anomalies", () => {
    const shape = homeShape({
      tiles: [tile("pin_a", { delta: delta() }), tile("pin_b", { delta: delta({ material: false }) })],
      entries: [],
    });
    expect(shape.order).toBe("monitors_first");
    expect(shape.invitation).toBe(false);
    expect(shape.movedPinIds).toEqual(["pin_a"]);
  });

  it("MONITORS, NOTHING MOVED: the digest stays below — a quiet monitor is not a headline", () => {
    const shape = homeShape({
      tiles: [
        tile("pin_a", { delta: delta({ material: false, direction: "flat", delta: 0 }) }),
        // No comparison at all is not a movement either.
        tile("pin_b"),
      ],
      entries: [entry("new_lead"), entry("self_resolved")],
    });
    expect(shape.order).toBe("anomalies_first");
    expect(shape.invitation).toBe(false);
    expect(shape.movedPinIds).toEqual([]);
    expect(shape.monitorCount).toBe(2);
  });

  it("promotes on the BRIEF's word too, when the tile grid does not carry the movement", () => {
    // The brief caps its entries and the tile list is a different read;
    // a monitor the brief says moved must not be buried because its tile
    // came back without a material delta.
    const shape = homeShape({
      tiles: [tile("pin_a")],
      entries: [entry("pin_movement", "pin_a")],
    });
    expect(shape.order).toBe("monitors_first");
    expect(shape.movedPinIds).toEqual(["pin_a"]);
  });

  it("counts a rank flip as a monitor changing under you", () => {
    // Not a movement — the number may be identical — but the CELL it
    // headlines is a different cell, which is the strongest thing that can
    // happen to a ranked monitor at a load.
    const shape = homeShape({ tiles: [tile("pin_a")], entries: [entry("rank_flip", "pin_a")] });
    expect(shape.order).toBe("monitors_first");
  });

  it("does not count a movement the governed gate held back", () => {
    // `material: false` is the pack's decision, and this module never
    // re-derives a threshold from raw values.
    const shape = homeShape({
      tiles: [tile("pin_a", { delta: delta({ material: false, deltaText: "0.4 points" }) })],
      entries: [],
    });
    expect(shape.order).toBe("anomalies_first");
  });

  it("does not count a delta the server said is not comparable", () => {
    const shape = homeShape({
      tiles: [
        tile("pin_a", {
          delta: delta({ comparable: false, notComparableReason: "First reading at this load." }),
        }),
      ],
      entries: [],
    });
    expect(shape.order).toBe("anomalies_first");
  });

  it("opens on the anomalies while the reads are still in flight", () => {
    // No monitors are KNOWN yet, so the page never opens on a digest it
    // cannot fill and re-orders itself when they land.
    expect(homeShape({}).order).toBe("anomalies_first");
    expect(homeShape({}).invitation).toBe(true);
  });

  it("unions the two sources without double-counting one pin", () => {
    expect(
      movedPinIds([tile("pin_a", { delta: delta() })], [entry("pin_movement", "pin_a")]),
    ).toEqual(["pin_a"]);
  });
});
