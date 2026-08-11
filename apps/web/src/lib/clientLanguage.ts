/**
 * THE LEXICON, AS CODE — the web half of `docs/client-language.md`.
 *
 * The server has enforced this contract since M43
 * (`apps/api/tests/test_client_language_guard.py`). The web half had three
 * hand-copied `JARGON` arrays — one in the monitor setup flow's test, one
 * in the stage rail's, and a third inlined in the answer card's — each
 * covering a different subset of the banned list, each drifting on its
 * own. So the words a first-time reader must never meet depended on which
 * component you happened to be editing.
 *
 * This is the one list, and `clientLanguage.test.ts` walks every source
 * file in the app against it. The constants deliberately MIRROR the Python
 * guard's rather than improving on it: two enforcement suites that disagree
 * about what is banned are the same failure as two components that do.
 *
 * WHAT IS NOT GOVERNED, and stays at full fidelity forever: `debug=true`
 * output, the Evidence rail's raw records, the decision trace, and exports.
 * The test's own skip list names them.
 */

/**
 * Word-boundary, case-insensitive. The `\b` is what keeps `overturned`,
 * `turnaround`, `package`, `framework` and `specific` out of the results —
 * see §3's "legitimate English collisions". `overturn` is KEEP vocabulary
 * (appeals), and losing it to a `turn` rule would be the guard making the
 * copy worse.
 */
export const BANNED_WORDS: ReadonlyArray<readonly [string, RegExp]> = [
  ["playbook", /\bplaybooks?\b/i],
  ["spec", /\bspecs?\b/i],
  ["frame", /\bframes?\b/i],
  ["recipe", /\brecipes?\b/i],
  ["turn", /\bturns?\b/i],
  ["grain", /\bgrains?\b/i],
  ["pack", /\bpacks?\b/i],
  ["cohort", /\bcohorts?\b/i],
  ["watermark", /\bwatermarks?\b/i],
  ["probe", /\bprobes?\b/i],
  ["governed", /\bgoverned\b/i],
  ["certified", /\bun-?certified\b|\bcertified\b/i],
  ["warehouse", /\bwarehouses?\b/i],
];

/** Shapes rather than words. */
export const BANNED_SHAPES: ReadonlyArray<readonly [string, RegExp]> = [
  // A data load's handle, in the only spelling it has.
  ["watermark id", /\bwm_\d+\b/],
  // A snake_case governed identifier: metric id, dimension id, column.
  ["snake_case identifier", /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/],
  // A version pin: base-rcm@1.0.0, anomaly_priority@3.
  ["version pin", /\b[a-z][a-z0-9-]*@\d+(?:\.\d+)*\b/i],
  // A model's numeric confidence — a fact about our internals.
  ["confidence number", /\bconfidence\s+\d*\.\d+/i],
  // A machine key/value pair: status=not_applicable, options_dropped=2.
  ["machine key=value", /\b[a-z_]{3,}=\S+/i],
  // An ALL_CAPS enum token: RECONCILIATION_FAILED, POPULATION_CAVEAT.
  ["ALL_CAPS enum token", /\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b/],
  // A raw ISO date or range on a default surface (§4).
  ["ISO date", /\b\d{4}-\d{2}-\d{2}\b/],
];

/**
 * Legitimate English collisions and handles that survive on purpose.
 *
 * Keep this SHORT and comment every entry — each one is a hole in the
 * guard, and the contract says so in as many words.
 */
export const ALLOWLIST: readonly RegExp[] = [
  // A `{expression}` in JSX is a value spliced at render time, not a
  // string a reader sees; whatever it evaluates to is governed where it
  // is composed.
  /\{[^}]*\}/g,
  // `${…}` inside a template literal, for the same reason.
  /\$\{[^}]*\}/g,
  // Referent handles (F1, T2) and lead ids (ANM-021) are LABELS a reader
  // is meant to quote back, not internal ids.
  /\b[FT]\d+\b/g,
  /\bANM-\d+\b/g,
  // RCM-native initialisms that happen to be all-caps — KEEP vocabulary
  // (§1), and the reason the ALL_CAPS shape cannot be read naively.
  /\b(?:CARC|RARC|COB|DNFB|HMO|PPO|A\/R|MCO|EOB|NPI|CSV|API|RCM|USD|HTTP|UI)\b/g,
  // "e.g." / "i.e." confuse the key=value shape when prose puts an equals
  // sign after them.
  /\b(?:e\.g\.|i\.e\.)/g,
];

/** A warning's leading machine code, which clients branch on. */
const MACHINE_PREFIX = /^[a-z][a-z0-9]*(?:_[a-z0-9]+)*:\s*/;

/**
 * Is this string a sentence a reader sees, or a value they branch on?
 *
 * Copy has whitespace. A bare single token — `ratio_points`,
 * `governed_default`, `warnings_v2` — is a wire enum value or a payload
 * field name: renaming one is a breaking contract change, not a
 * translation, and a client renders it through the §2 table rather than
 * printing it. This is the only rule separating the two, so it is stated
 * once and used by every collector.
 */
export function isCopy(text: string): boolean {
  const stripped = text.trim();
  return stripped.length >= 4 && /\s/.test(stripped);
}

function redactAllowed(text: string): string {
  let out = text;
  for (const pattern of ALLOWLIST) out = out.replace(new RegExp(pattern.source, "g"), " ");
  return out;
}

/** Every contract violation in one client-visible string. */
export function violations(text: string): string[] {
  const scanned = redactAllowed(text.replace(MACHINE_PREFIX, ""));
  const found: string[] = [];
  for (const [label, pattern] of BANNED_WORDS) {
    if (new RegExp(pattern.source, pattern.flags).test(scanned)) found.push(label);
  }
  for (const [label, pattern] of BANNED_SHAPES) {
    const match = new RegExp(pattern.source, pattern.flags).exec(scanned);
    if (match) found.push(`${label} (${match[0]})`);
  }
  return found;
}
