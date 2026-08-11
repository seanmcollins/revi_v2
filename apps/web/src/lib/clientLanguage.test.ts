/**
 * THE WEB HALF OF THE LANGUAGE CONTRACT, ENFORCED.
 *
 * `docs/client-language.md` governs every string a client can read, and it
 * says so in as many words: "The web half (static UI labels, tooltips,
 * chips, settings copy) is governed by this same document. Server-composed
 * strings and web-composed strings must use the *same* rendering for the
 * same concept."
 *
 * The server's half has been enforced since M43. This side had three
 * hand-copied banned-word arrays covering three different subsets of the
 * list, in three component tests — so "turn" was banned on the monitor
 * setup form and shipped in the session rail's accessible name, in the
 * command palette's hint, in the share disclosure, in the settings panel,
 * and in eleven error sentences. This walks the whole app instead.
 *
 * WHAT IT READS. Two collectors, both narrow on purpose:
 *
 *   1. JSX TEXT NODES — the words between tags. This is most of the
 *      product's authored copy.
 *   2. AUTHORED ATTRIBUTE VALUES — `aria-label`, `title`, `placeholder`,
 *      `alt`, and the props this codebase uses for copy (`label`, `hint`,
 *      `explanation`, `detail`, `submitLabel`, `restartNote`, …). An
 *      accessible name is a client-visible string like any other, and it
 *      is the one with the least room to explain itself.
 *
 * It does NOT read every string literal. `className` lists, wire enum
 * values, ids and query keys are strings too, and a guard that could not
 * tell them from copy would be answered with an allowlist long enough to
 * be meaningless. `isCopy` (whitespace, ≥4 chars) is the same rule the
 * Python guard uses for the same reason.
 *
 * WHAT IT SKIPS, and why each one is exempt by the contract's own opening
 * paragraph: `debug=true` output, the Evidence rail's raw records, the
 * trace, exports, and this repo's mock fixtures (which imitate server
 * payloads rather than authoring UI copy). Each is named in SKIP below.
 *
 * If you are fighting this test, the copy is wrong, not the test.
 */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import { isCopy, violations } from "@/lib/clientLanguage";

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * Paths the contract does not govern. Every entry is exempt by
 * `docs/client-language.md`'s opening paragraph, not by convenience.
 */
const SKIP: ReadonlyArray<{ path: string; why: string }> = [
  { path: "components/debug/", why: "debug=true output keeps full fidelity forever" },
  { path: "lib/export.ts", why: "exports keep full fidelity forever" },
  { path: "lib/mock/", why: "mock payloads stand in for the server, not for UI copy" },
  { path: "lib/mockDriver.ts", why: "the same: a stand-in server, not authored copy" },
  { path: "lib/types.gen.ts", why: "generated from the OpenAPI document" },
  { path: "lib/clientLanguage", why: "this guard names the banned words to ban them" },
];

/**
 * Sites that render a raw record ON PURPOSE, with the record's own
 * vocabulary, behind a control the reader opened.
 *
 * Narrow to a file plus the string, so an exemption cannot silently widen
 * to the rest of the file.
 */
const EXEMPT_STRINGS: ReadonlyArray<{ file: string; text: string; why: string }> = [
  {
    file: "components/evidence/EvidenceDrawer.tsx",
    text: "The exact SQL each check ran, as the engine recorded it.",
    why: "the Evidence rail's raw records — named as such, and the point of the rail",
  },
];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (entry === "node_modules" || entry === "__fixtures__") continue;
      walk(full, out);
    } else if (/\.(ts|tsx)$/.test(entry) && !/\.test\.tsx?$/.test(entry)) {
      out.push(full);
    }
  }
  return out;
}

/**
 * Comments are not copy, and this codebase's comments are long and
 * deliberately quote the platform vocabulary they are removing. Stripped
 * first, or the guard would fail on the paragraph explaining why the guard
 * exists.
 */
function stripComments(source: string): string {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^:])\/\/[^\n]*/g, "$1 ");
}

/** `className="…"` and `className={…}` are style, never copy. */
function stripClassNames(source: string): string {
  return source
    .replace(/className\s*=\s*"[^"]*"/g, " ")
    .replace(/className\s*=\s*\{`[^`]*`\}/g, " ")
    .replace(/\bcn\(/g, "IGNORED(");
}

/** Props this codebase uses to pass authored copy to a component. */
const COPY_ATTRS = [
  "aria-label",
  "aria-description",
  "title",
  "placeholder",
  "alt",
  "label",
  "hint",
  "explanation",
  "detail",
  "description",
  "submitLabel",
  "restartNote",
  "doneLabel",
  "legend",
  "heading",
  "note",
].join("|");

/**
 * `>` and `<` are also comparison and generic brackets, so the text-node
 * collector inevitably catches a run of TypeScript between an arrow's `=>`
 * and the next `<`. Prose does not carry a semicolon, an arrow, a keyword
 * or a call — every one of these is a token that only appears in code.
 *
 * Erring towards DROPPING a candidate is safe here in a way that erring
 * towards keeping one is not: a dropped line is one string this guard does
 * not read, and a kept one is a failure nobody can act on, which is how a
 * guard gets an allowlist and then gets deleted.
 */
function looksLikeCode(text: string): boolean {
  return (
    /[;{}]|=>|===|\?\?|&&|\|\||\breturn\b|\bconst\b|\bfunction\b|\w\(|\)\s*$|=\s/.test(
      text,
    ) ||
    // A run of an object literal or an argument list — the price of
    // reading text that ends at a `{`, which is what lets a sentence with
    // a spliced value in it be read whole. No authored sentence opens on
    // a comma, a closing bracket or a colon.
    /^[\s,):]/.test(text) ||
    /^[A-Z][A-Z0-9_]*\b/.test(text.trim())
  );
}

/**
 * Every authored string in one file, with a line number so a failure names
 * the site rather than the file.
 */
function copyStrings(source: string): Array<{ line: number; text: string }> {
  const cleaned = stripClassNames(stripComments(source));
  const found: Array<{ line: number; text: string }> = [];
  const lineOf = (index: number): number => cleaned.slice(0, index).split("\n").length;

  // 1. JSX text nodes: the words between a `>` and the next `<`.
  //
  // The closing delimiter is `<` OR `{`, and the opening one is `>` OR
  // `}`, because a sentence with a spliced value in it is still a
  // sentence: `<p>Answered without reading {name} — everything…</p>` is
  // three fragments to a regex and one paragraph to a reader. Matching
  // only `>…<` read the first fragment of such a paragraph and silently
  // skipped the rest, which is how "…going back to the warehouse" was
  // still on screen after this guard went green.
  const textNode = /[>}]([^<>{}"'`]{4,}?)[<{]/g;
  let m: RegExpExecArray | null;
  while ((m = textNode.exec(cleaned)) !== null) {
    // `&apos;` and `&rsquo;` end in a semicolon, and a semicolon is one of
    // the tokens that says "this is code" — so every sentence with a typed
    // apostrophe in it was being dropped silently.
    const text = m[1].replace(/&[a-z]+;/gi, "'").replace(/\s+/g, " ").trim();
    if (isCopy(text) && /[a-z]{3}/i.test(text) && !looksLikeCode(text)) {
      found.push({ line: lineOf(m.index), text });
    }
  }

  // 2. Authored attribute and prop values, in all three JSX spellings.
  const attr = new RegExp(
    `\\b(?:${COPY_ATTRS})\\s*=\\s*(?:"([^"]*)"|\\{"([^"]*)"\\}|\\{\`([^\`]*)\`\\})`,
    "g",
  );
  while ((m = attr.exec(cleaned)) !== null) {
    const text = (m[1] ?? m[2] ?? m[3] ?? "").replace(/\s+/g, " ").trim();
    if (isCopy(text)) found.push({ line: lineOf(m.index), text });
  }

  // 3. Ternary branches inside those props — `hint={a ? "…" : "…"}` is how
  //    half the settings panel's copy is written.
  const ternaryAttr = new RegExp(`\\b(?:${COPY_ATTRS})\\s*=\\s*\\{[^}]*\\}`, "g");
  while ((m = ternaryAttr.exec(cleaned)) !== null) {
    for (const quoted of m[0].matchAll(/"([^"]{4,})"/g)) {
      const text = quoted[1].replace(/\s+/g, " ").trim();
      if (isCopy(text)) found.push({ line: lineOf(m.index), text });
    }
  }
  return found;
}

describe("the words this app says out loud", () => {
  const files = walk(SRC).filter(
    (file) => !SKIP.some((skip) => relative(SRC, file).startsWith(skip.path)),
  );

  it("reads a real share of the app, so a pass means something", () => {
    // A collector that quietly stopped matching would make every
    // assertion below vacuous and green.
    expect(files.length).toBeGreaterThan(60);
    const total = files.reduce((n, f) => n + copyStrings(readFileSync(f, "utf8")).length, 0);
    expect(total).toBeGreaterThan(250);
  });

  it("says nothing on the NEVER-SAY list, anywhere a reader can read it", () => {
    const offences: string[] = [];
    for (const file of files) {
      const rel = relative(SRC, file);
      for (const { line, text } of copyStrings(readFileSync(file, "utf8"))) {
        if (EXEMPT_STRINGS.some((e) => rel.startsWith(e.file) && text.includes(e.text))) continue;
        const found = violations(text);
        if (found.length > 0) {
          offences.push(`${rel}:${line} — ${found.join(", ")} — “${text.slice(0, 110)}”`);
        }
      }
    }
    expect(offences, `\n${offences.join("\n")}\n`).toEqual([]);
  });

  it("bans each word the contract bans, and none of its English collisions", () => {
    // The guard's own regression test. `overturn` is KEEP vocabulary
    // (appeals) and `turnaround`, `package`, `framework`, `specific` and
    // `planned` are ordinary English — a rule that caught them would make
    // the copy worse, which is the failure mode §3 warns about by name.
    expect(violations("this turn ended")).toContain("turn");
    expect(violations("the pack's threshold")).toContain("pack");
    expect(violations("gated by the governed pack")).toEqual(["pack", "governed"]);
    expect(violations("an uncertified value")).toContain("certified");
    expect(violations("at data load wm_003")).toContain("watermark id (wm_003)");
    expect(violations("denied_dollars by payer")).toContain(
      "snake_case identifier (denied_dollars)",
    );
    expect(violations("base-rcm@1.0.0 is pinned")).toContain("version pin (base-rcm@1.0.0)");
    expect(violations("RECONCILIATION_FAILED on this cell")).toContain(
      "ALL_CAPS enum token (RECONCILIATION_FAILED)",
    );
    expect(violations("over 2026-07-01 to 2026-07-31")).toContain("ISO date (2026-07-01)");

    for (const fine of [
      "the appeal was overturned",
      "a two-day turnaround on this payer",
      "a packing slip came with it",
      "the framework this sits in",
      "one specific payer",
      "a planned discharge",
      "return the claim",
      "denials over 90 days in A/R",
      "the CARC and RARC on the remit",
    ]) {
      expect(violations(fine), `“${fine}” is ordinary English`).toEqual([]);
    }
  });
});
