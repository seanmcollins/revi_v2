import { existsSync, readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/**
 * WHERE THE REPOSITORY ROOT IS, WITHOUT COUNTING `../`.
 *
 * Three contract pins read files this app does not own — the Python
 * engine's `MONITOR_THRESHOLD_UNITS`, the API's `warning_codes.py`, and
 * `contracts/openapi.json` — because a second copy of any of those living
 * in a test is the exact defect those tests exist to catch. They found
 * them by writing `resolve(here, "../../../..")`, which encodes this
 * file's depth inside `apps/web` as a constant: moving a test one
 * directory, or moving the app, turns a pin into a thrown ENOENT at best
 * and a silently different file at worst.
 *
 * So: walk up until a directory contains `pyproject.toml`. That marker is
 * the Python workspace root, which is the repository root — nothing on the
 * path from `apps/web/src/lib` up to it has one — and it exists whether
 * this app sits at `apps/web`, one level deeper, or somewhere else
 * entirely.
 *
 * NOT SILENT ABOUT FAILURE, by design. If the marker cannot be found the
 * search throws, and every read below throws on a missing file, because
 * "the engine's list could not be read, so nothing was checked" must never
 * be reported as a passing pin.
 *
 * Node-only. Imported by tests; nothing the browser build can reach.
 */

const MARKER = "pyproject.toml";

export function findRepoRoot(from: string): string {
  let dir = resolve(from);
  for (;;) {
    if (existsSync(resolve(dir, MARKER))) return dir;
    const parent = dirname(dir);
    if (parent === dir) {
      throw new Error(
        `repository root not found: no ${MARKER} in any directory above ${from}`,
      );
    }
    dir = parent;
  }
}

/** The root, resolved once from this module's own location. */
export const REPO_ROOT = findRepoRoot(dirname(fileURLToPath(import.meta.url)));

/** Read a repo-relative source file, or throw naming what was missing. */
export function readRepoFile(relative: string): string {
  return readFileSync(resolve(REPO_ROOT, relative), "utf8");
}
