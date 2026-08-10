/**
 * The three contract pins read files this app does not own — the engine's
 * `MONITOR_THRESHOLD_UNITS`, the API's `warning_codes.py`, and
 * `contracts/openapi.json`. They used to find them by counting `../`, which
 * encodes this file's depth inside `apps/web` as a constant.
 *
 * The replacement walks up to a marker. What has to be true of it is what is
 * asserted here: it finds THE repository root (not the first `pyproject.toml`
 * it trips over on the way), the files those pins name are actually there,
 * and a search that cannot find the marker THROWS rather than quietly
 * resolving to `/` and reading nothing.
 */

import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { findRepoRoot, readRepoFile, REPO_ROOT } from "@/lib/repoRoot";

describe("repo-root discovery replaces the `../../../..` constant", () => {
  it("lands on the directory that owns the whole workspace", () => {
    expect(existsSync(resolve(REPO_ROOT, "pyproject.toml"))).toBe(true);
    // The three things the pins read. Finding a `pyproject.toml` in some
    // nested Python package instead of the root would fail here.
    expect(existsSync(resolve(REPO_ROOT, "contracts/openapi.json"))).toBe(true);
    expect(existsSync(resolve(REPO_ROOT, "apps/api/src/revi_api/warning_codes.py"))).toBe(true);
    expect(
      existsSync(
        resolve(REPO_ROOT, "packages/investigation/src/revi_investigation/application/ports.py"),
      ),
    ).toBe(true);
    expect(existsSync(resolve(REPO_ROOT, "apps/web/package.json"))).toBe(true);
  });

  it("finds the same root from anywhere inside the tree", () => {
    expect(findRepoRoot(resolve(REPO_ROOT, "apps/web/src/lib"))).toBe(REPO_ROOT);
    expect(findRepoRoot(resolve(REPO_ROOT, "apps/web"))).toBe(REPO_ROOT);
    expect(findRepoRoot(REPO_ROOT)).toBe(REPO_ROOT);
  });

  it("throws rather than reporting a clean read of nothing", () => {
    // `/` has no marker above it, so the walk exhausts.
    expect(() => findRepoRoot("/")).toThrow(/repository root not found/);
    expect(() => readRepoFile("contracts/this-file-does-not-exist.json")).toThrow();
  });

  it("reads a real file, so a passing pin has read something", () => {
    expect(readRepoFile("contracts/openapi.json").length).toBeGreaterThan(1000);
  });
});
