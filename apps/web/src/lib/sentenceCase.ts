/**
 * THE OWNER'S RULE, MADE MECHANICAL: no sentence-position text on this
 * product begins with a lower-case letter.
 *
 * `lib/prose` already carried the repair (`capitalizeOpening`, and
 * `readableStatement` / `readableLabel` around it). What it did not carry
 * was a way to find the places nobody had applied it — and they were
 * everywhere, because the defect is invisible to a test written per
 * component: each surface asserts the string it renders, and the string it
 * renders is the string the server sent, so every one of those tests passed
 * while the live page opened three of four cards mid-sentence.
 *
 * So this walks the rendered DOM instead. Point it at a surface built from
 * a real payload and it returns every text node that OPENS something and
 * opens it in lower case. It is deliberately a render-time check: the
 * exports keep raw fidelity (an export exists to reproduce a query), and
 * the only place the repair belongs is the last inch before a reader.
 *
 * WHAT "SENTENCE POSITION" MEANS HERE, and why it is not simply "the first
 * character of a string".
 *
 * A string can be correct in one position and wrong in another. The
 * worklist's actionability class renders twice on two different cards:
 * once as the whole of its own line ("Highly recoverable"), and once as the
 * tail of "~$169,306 recoverable — highly recoverable", where a capital
 * would be the error. So position is computed from the DOM, in two steps:
 *
 *   1. Nothing may precede the text inside its own BOUNDARY — the nearest
 *      block-level element or interactive control that contains it. Text
 *      after "29.5%" inside one `<p>` is a continuation and is left alone.
 *   2. If that boundary is a CONTROL, the control must not itself be a word
 *      inside a sentence. A button standing alone in a `<p>` opens a line;
 *      the same button preceded by "~$169,306 recoverable — " does not.
 *
 * Step 2 is what makes this catch CONTROL LABELS — radio labels, chips,
 * `<option>`s, submit buttons — rather than prose alone. That gap was real:
 * the monitor setup's direction chips rendered "either way / only up / only
 * down" and a prose-only walker never looked at them, because each chip sat
 * behind a `<legend>` whose text made the chip look like a continuation.
 *
 * IT RUNS IN JSDOM, WHICH HAS NO CSS. Every rule above is structural —
 * tag names, roles, `aria-hidden` — with one exception: text that CSS
 * upper-cases is exempt, and the only evidence available for that in jsdom
 * is the Tailwind class name. `uppercase` / `capitalize` on the node or any
 * ancestor is therefore read off `className`, which is exactly how the
 * severity chips ("high", "critical") are correctly ignored: they render
 * `HIGH` and `CRITICAL` on screen.
 */

/** Elements that start a new line of reading. */
const BLOCK_TAGS: ReadonlySet<string> = new Set([
  "ADDRESS", "ARTICLE", "ASIDE", "BLOCKQUOTE", "BODY", "CAPTION", "DD", "DIV",
  "DL", "DT", "FIELDSET", "FIGCAPTION", "FIGURE", "FOOTER", "FORM", "H1", "H2",
  "H3", "H4", "H5", "H6", "HEADER", "LEGEND", "LI", "MAIN", "NAV", "OL", "P",
  "PRE", "SECTION", "TABLE", "TBODY", "TD", "TFOOT", "TH", "THEAD", "TR", "UL",
]);

/**
 * Elements whose text is a LABEL a person acts on. Their content opens a
 * reading of its own unless the control is sitting inside a sentence — see
 * step 2 above.
 */
const CONTROL_TAGS: ReadonlySet<string> = new Set([
  "A", "BUTTON", "LABEL", "OPTION", "OPTGROUP", "SUMMARY", "TEXTAREA",
]);

const CONTROL_ROLES: ReadonlySet<string> = new Set([
  "button", "checkbox", "link", "menuitem", "menuitemcheckbox", "menuitemradio",
  "option", "radio", "switch", "tab", "treeitem",
]);

/** Text CSS will re-case before anybody reads it. */
const RECASED = /(?:^|:)(?:uppercase|capitalize)(?:$|\s)/;

function classNameOf(el: Element): string {
  const raw = el.className as string | { baseVal?: string };
  if (typeof raw === "string") return raw;
  return raw?.baseVal ?? "";
}

function isRecased(el: Element | null): boolean {
  for (let node: Element | null = el; node !== null; node = node.parentElement) {
    for (const token of classNameOf(node).split(/\s+/)) {
      if (token === "") continue;
      if (RECASED.test(token)) return true;
    }
  }
  return false;
}

function isControl(el: Element): boolean {
  if (CONTROL_TAGS.has(el.tagName)) return true;
  const role = el.getAttribute("role");
  return role !== null && CONTROL_ROLES.has(role);
}

function isBoundary(el: Element): boolean {
  return BLOCK_TAGS.has(el.tagName) || isControl(el);
}

/** Text nobody reads: decoration, and anything the browser will not draw. */
function isIgnorable(el: Element): boolean {
  if (el.getAttribute("aria-hidden") === "true") return true;
  if (el.hasAttribute("hidden")) return true;
  const tag = el.tagName;
  return tag === "SCRIPT" || tag === "STYLE" || tag === "SVG" || tag === "svg";
}

function ignored(el: Element | null, stopAt: Node): boolean {
  for (let node: Element | null = el; node !== null && node !== stopAt; node = node.parentElement) {
    if (isIgnorable(node)) return true;
  }
  return false;
}

/** The nearest ancestor that starts a reading, or `root`. */
function boundaryOf(node: Node, root: Element): Element {
  let el = node.parentElement;
  while (el !== null && el !== root && !isBoundary(el)) el = el.parentElement;
  return el ?? root;
}

/**
 * Visible text preceding `target` inside `container`, counting ONLY text
 * that belongs to the container's own run — anything living inside a nested
 * boundary is a different reading and does not make this one a
 * continuation.
 */
function textBefore(container: Element, target: Node): string {
  const walker = container.ownerDocument.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let before = "";
  let node = walker.nextNode();
  while (node !== null) {
    if (node === target || (target.contains !== undefined && target.contains(node))) break;
    const parent = node.parentElement;
    if (
      parent !== null &&
      !ignored(parent, container) &&
      boundaryOf(node, container) === container
    ) {
      before += node.nodeValue ?? "";
    }
    node = walker.nextNode();
  }
  return before;
}

/** One text node that opens something, in lower case. */
export interface LowercaseOpening {
  /** The offending text, trimmed. */
  text: string;
  /** Where it is, as a short element path — enough to find the component. */
  path: string;
}

/**
 * Text that legitimately opens in lower case.
 *
 * Every entry carries the reason, and the list is meant to stay at or near
 * zero: an entry here is a claim that a reader is better served by the
 * lower case, not a place to park a defect. It is matched against the
 * TRIMMED text, exactly.
 */
export interface SentenceCaseException {
  pattern: RegExp;
  why: string;
}

export const SENTENCE_CASE_ALLOWLIST: readonly SentenceCaseException[] = [
  {
    // A URL is not a sentence and its scheme is case-sensitive by
    // convention; "Http://localhost:8000" is a different string from the
    // one an analyst would paste into a browser.
    pattern: /^[a-z][a-z0-9+.-]*:\/\//,
    why: "a URL — capitalizing the scheme would print an address nobody typed",
  },
  {
    // A warehouse identifier that must stay quotable: `denial_rate`,
    // `wm_003`, `anomaly_priority@3`. `capitalizeOpening` already refuses
    // to touch a token carrying an underscore for exactly this reason (a
    // capitalized id is a wrong id), so the walker must agree with it.
    pattern: /^[a-z][a-z0-9]*[_@][a-z0-9_@.]+$/,
    why: "a machine identifier — a capitalized id is a wrong id (see capitalizeOpening)",
  },
];

function allowed(text: string): boolean {
  return SENTENCE_CASE_ALLOWLIST.some((entry) => entry.pattern.test(text));
}

function pathOf(el: Element | null): string {
  const parts: string[] = [];
  for (let node = el; node !== null && parts.length < 4; node = node.parentElement) {
    const cls = classNameOf(node).split(/\s+/).filter(Boolean).slice(0, 2).join(".");
    parts.push(
      node.tagName.toLowerCase() +
        (node.id !== "" ? `#${node.id}` : "") +
        (cls !== "" ? `.${cls}` : ""),
    );
  }
  return parts.join(" < ");
}

/**
 * Every text node under `root` that OPENS a reading and opens it lower
 * case, allowlist applied.
 */
export function lowercaseSentenceOpenings(root: Element): LowercaseOpening[] {
  const walker = root.ownerDocument.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const found: LowercaseOpening[] = [];
  let node = walker.nextNode();
  while (node !== null) {
    const current = node;
    node = walker.nextNode();

    const text = (current.nodeValue ?? "").trim();
    if (text === "" || !/^[a-z]/.test(text)) continue;
    if (allowed(text)) continue;

    const parent = current.parentElement;
    if (parent === null) continue;
    if (ignored(parent, root)) continue;
    if (isRecased(parent)) continue;

    // 1. Nothing precedes it inside its own boundary.
    const boundary = boundaryOf(current, root);
    if (textBefore(boundary, current).trim() !== "") continue;

    // 2. …and if that boundary is a control, the control is not itself a
    //    word inside somebody's sentence.
    if (boundary !== root && isControl(boundary) && inlineControl(boundary, root)) continue;

    found.push({ text, path: pathOf(parent) });
  }
  return found;
}

/** A control sitting mid-sentence rather than standing on its own line. */
function inlineControl(control: Element, root: Element): boolean {
  let block: Element | null = control.parentElement;
  while (block !== null && block !== root && !BLOCK_TAGS.has(block.tagName)) {
    // A control nested inside another control inherits its position.
    if (isControl(block)) return inlineControl(block, root);
    block = block.parentElement;
  }
  if (block === null) return false;
  return textBefore(block, control).trim() !== "";
}

/** The failure message a test prints — every site, with its path. */
export function describeLowercaseOpenings(found: readonly LowercaseOpening[]): string {
  return found.map((f) => `  «${f.text.slice(0, 90)}»\n    at ${f.path}`).join("\n");
}
