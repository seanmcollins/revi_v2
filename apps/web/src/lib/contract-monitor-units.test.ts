/**
 * The monitor vocabulary exists in FOUR places, and this is the one that
 * asserts they are the same list.
 *
 * Round-8 pinned the engine's `MONITOR_THRESHOLD_UNITS` against the wire's
 * `MonitorUnit` in Python CI, because one token of skew (`days`) had
 * 500'd `GET /v1/monitors/pins` for a whole tenant. Nobody pinned the third
 * and fourth copies, both of which live here: `contract.ts`'s `MONITOR_UNITS`
 * — the parser's allow-list, which silently dropped an unrecognised unit to
 * `undefined` — and the `<option>` list in the settings control the analyst
 * actually edits a monitor through.
 *
 * The cost of that gap, live, on the monitor the VC signed a
 * condition-precedent on: a stored `{delta_gte, 2.0, days}` threshold
 * opened in the editor reading "2 percentage points", and pressing Save
 * submitted `unit: "points"` on a lag metric — which the server correctly
 * refused, so the monitor was uneditable and every attempt restarted its
 * baseline.
 *
 * Both server-side lists are READ FROM THEIR OWN SOURCE rather than copied
 * here, the way `contract-followups.test.ts` reads every warning code out
 * of `warning_codes.py`. A second copy in a test is the defect this test
 * exists to catch. An unreadable source FAILS: "the server's list could not
 * be read, so nothing was checked" is not a pass.
 */

import { describe, expect, it } from "vitest";

import { MONITOR_UNITS } from "@/lib/contract";
import { readRepoFile } from "@/lib/repoRoot";

/**
 * Repo-relative, not `../`-relative: the root is found by walking up to
 * `pyproject.toml` (see `lib/repoRoot`), so this pin keeps reading the
 * engine's own source wherever this app sits in the tree.
 */
function read(relative: string): string {
  return readRepoFile(relative);
}

/** The engine's own list — the one the phrase parser and the pack read. */
function engineUnits(): string[] {
  const source = read(
    "packages/investigation/src/revi_investigation/application/ports.py",
  );
  const match = /MONITOR_THRESHOLD_UNITS:\s*tuple\[str, \.\.\.\]\s*=\s*\(([^)]*)\)/.exec(source);
  if (!match) throw new Error("MONITOR_THRESHOLD_UNITS not found in ports.py");
  return [...match[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
}

/** The published wire contract, as the OpenAPI document states it. */
function wireUnits(): string[] {
  const spec = JSON.parse(read("contracts/openapi.json")) as {
    components: {
      schemas: Record<string, { properties?: Record<string, { anyOf?: { enum?: string[] }[] }> }>;
    };
  };
  const unit = spec.components.schemas.MonitorModel?.properties?.unit;
  const branch = unit?.anyOf?.find((b) => Array.isArray(b.enum));
  if (!branch?.enum) throw new Error("MonitorModel.unit enum not found in openapi.json");
  return branch.enum;
}

/** The units the settings control actually offers, off its own markup. */
function offeredUnits(): string[] {
  const source = read("apps/web/src/components/monitors/MonitorSensitivity.tsx");
  const select = /aria-label="The unit that number is stated in"[\s\S]*?<\/select>/.exec(source);
  if (!select) throw new Error("the unit <select> was not found in MonitorSensitivity.tsx");
  return [...select[0].matchAll(/<option value="([a-z_]+)"/g)].map((m) => m[1]);
}

describe("the monitor-unit enum, in every copy of it", () => {
  it("is the same set in contract.ts as in the engine's own module", () => {
    expect([...MONITOR_UNITS].sort()).toEqual(engineUnits().sort());
  });

  it("is the same set in contract.ts as on the published wire", () => {
    expect([...MONITOR_UNITS].sort()).toEqual(wireUnits().sort());
  });

  it("is offered, one option per unit, by the control that edits a monitor", () => {
    // Not a subset: an option the parser will drop is a Save that
    // dead-ends, and a unit with no option is a threshold nobody can
    // state. Duplicates would render two identical rows in the select.
    const offered = offeredUnits();
    expect(new Set(offered).size).toBe(offered.length);
    expect(offered.slice().sort()).toEqual([...MONITOR_UNITS].sort());
  });

  it("carries `days` — named, so the regression reads in a failure log", () => {
    // The specific token that shipped skewed three times. Spelled out
    // rather than inferred from a set difference.
    expect(MONITOR_UNITS.has("days")).toBe(true);
    expect(engineUnits()).toContain("days");
    expect(wireUnits()).toContain("days");
    expect(offeredUnits()).toContain("days");
  });
});
