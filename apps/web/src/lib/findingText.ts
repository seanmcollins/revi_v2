/**
 * A finding says one thing. It should not say it twice.
 *
 * The engine publishes a `title` and a `statement`, and on a whole class
 * of answers the statement OPENS with the title verbatim:
 *
 *   title      "Pinnacle HMO: 47.2% denial rate"
 *   statement  "Pinnacle HMO: 47.2% denial rate over 2026-07-01..
 *               2026-07-31. No position is claimed for it — too much of
 *               this population carries suppressed numerators for an
 *               order to mean anything."
 *
 * The card printed both, one under the other, with the same figure a
 * third time in display size above them — so a six-payer answer was
 * eighteen restatements of six numbers, and the sentence that actually
 * matters ("no position is claimed for it") arrived after the reader had
 * already read the same clause twice.
 *
 * The rule here is the one `foldComposedDisclosures` uses for prose and
 * banners, applied to a card: the duplicate is not printed twice, and
 * NOTHING ELSE is touched. A statement that adds anything at all — "ranks
 * #1 of 12 measured by denied dollars" — comes back byte-identical, and
 * every export keeps the full statement regardless of what a card shows.
 */

import { humanizeIsoDates } from "@/lib/format";

/** Comparable form: no case, no runs of space, no trailing punctuation. */
function normalize(text: string): string {
  return text
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[.;:,]+$/, "")
    .toLowerCase();
}

/** Paragraph → sentences, keeping the delimiter on the sentence it ends. */
function sentencesOf(text: string): string[] {
  return text.split(/(?<=[.!?])\s+/).filter((s) => s.trim() !== "");
}

/**
 * A leftover clause that states only the window the answer's own context
 * line already states: "over 2026-07-01..2026-07-31", "over the window",
 * "in 2026-07".
 *
 * Deliberately a small closed list of shapes rather than a general "looks
 * like a date" test: a clause this function cannot recognize is kept, and
 * keeping a redundant clause is a much smaller cost than dropping one
 * that carried a qualification.
 */
const WINDOW_ONLY =
  /^(?:over|for|in|across|during)\s+(?:the\s+window|\d{4}-\d{2}(?:-\d{2})?(?:\s*\.\.\s*\d{4}-\d{2}(?:-\d{2})?)?)\.?$/i;

/**
 * A short label the engine puts in front of a title: "Premise partly
 * supported: You asked about a doubling…".
 *
 * The label is the card's own heading and the rest of the title is the
 * first sentences of the statement said again — live, that is one
 * 180-character clause printed twice on the same card, once as a title
 * and once as the paragraph under it. Stripping the label gives the
 * second thing to compare the statement against.
 *
 * Bounded at 40 characters so an ordinary sentence with a colon in it
 * ("denial rate by month, 2026-01-01..2026-08-02: 7.3% → 9.1%") is not
 * mistaken for a labelled one.
 */
const TITLE_LABEL = /^[^:]{1,40}: /;

/**
 * The statement with its opening removed when the opening is the title
 * said again — and nothing else touched.
 *
 * Sentences are consumed from the front only while they are BUILDING the
 * title (so a title spanning two sentences is matched as two), and the
 * run stops the moment a sentence carries anything the title does not.
 * The one thing allowed on the end of the last consumed sentence is a
 * bare window clause, because the window is on every answer's context
 * line already.
 *
 * Returns `""` when the whole statement was the title said again, which
 * a card renders as no second paragraph at all.
 */
export function statementBeyondTitle(title: string, statement: string): string {
  const body = statement.trim();
  if (title.trim() === "" || body === "") return body;

  const labelled = title.replace(TITLE_LABEL, "");
  for (const candidate of [title, ...(labelled !== title ? [labelled] : [])]) {
    const stripped = stripHeading(normalize(candidate), body);
    if (stripped !== null) return stripped;
  }
  return body;
}

/** The statement past `heading`, or null when it does not open with it. */
function stripHeading(heading: string, statement: string): string | null {
  if (heading === "") return null;
  const sentences = sentencesOf(statement);
  let taken = "";

  for (let i = 0; i < sentences.length; i += 1) {
    const next = taken === "" ? sentences[i] : `${taken} ${sentences[i]}`;
    const normalized = normalize(next);

    // Exactly the title, over one sentence or several.
    if (normalized === heading) return sentences.slice(i + 1).join(" ").trim();

    // Still building towards it — keep consuming.
    if (heading.startsWith(normalized)) {
      taken = next;
      continue;
    }

    // Overshot: allowed only by a window clause the answer already states.
    if (normalized.startsWith(heading)) {
      const remainder = normalized.slice(heading.length).trim().replace(/^[-–—:,]\s*/, "");
      if (remainder === "" || WINDOW_ONLY.test(remainder)) {
        return sentences.slice(i + 1).join(" ").trim();
      }
    }
    return null;
  }
  // The statement ran out before the title did: it is a fragment of the
  // heading, not a repetition of it, and nothing is dropped.
  return null;
}

/**
 * Is this finding the VERDICT, printed a second time as a row?
 *
 * A premise turn publishes the verdict twice by construction: once as
 * `PREMISE_PARTIAL` ("You asked about a doubling in denial rate. It did
 * not double — denial rate rose 11.5%") and once as F1, whose statement
 * is that same sentence. The calm layout leads with the verdict in
 * reading size and then, three hundred pixels below, prints it again as
 * a fact row — the same 180-character clause twice on one screen.
 *
 * The row is NOT dropped: F1 is what a citation in the prose points at
 * and what the rail anchors, and a fact that vanishes from the list
 * breaks the count beside it. What the row does instead is say where the
 * sentence already is — see `FactRow`.
 *
 * Deliberately conservative, in both directions. Only whole statements
 * are compared (after the engine's `Label: ` heading is taken off both
 * sides), only against the verdict text the surface actually rendered,
 * and only when both are long enough that containment means something —
 * a short statement inside a long verdict is a coincidence, not a
 * repetition.
 */
export function echoesVerdict(
  finding: { title: string; statement: string },
  verdictBodies: readonly string[],
): boolean {
  // Both sides through the same date spelling before they are compared.
  // The warning surface prints "vs prior year (Jan 1–Aug 2, 2025)" where
  // the engine wrote an ISO range, and comparing the printed sentence
  // against the raw one would find two different strings where there is
  // one sentence — the repetition would then survive precisely because
  // it was cleaned up.
  const spell = (text: string): string =>
    normalize(humanizeIsoDates(text.replace(TITLE_LABEL, "")));
  const statement = spell(finding.statement);
  if (statement.length < 40) return false;
  return verdictBodies.some((body) => {
    const verdict = spell(body);
    if (verdict.length < 40) return false;
    return verdict.includes(statement) || statement.includes(verdict);
  });
}

/**
 * The engine's short heading on a title, when it carries one.
 *
 * "Premise partly supported: You asked about a doubling in denial rate…"
 * → "Premise partly supported". Used by a row that has established its
 * statement is the verdict said twice: the heading names the fact, and
 * the sentence itself stays where it was already printed in full.
 */
export function titleLabelOnly(title: string): string | undefined {
  const match = TITLE_LABEL.exec(title.trim());
  return match ? match[0].replace(/: $/, "") : undefined;
}

/**
 * Does the title already print this figure?
 *
 * A compact row shows the referent, the title and a value column, and on
 * most live answers the engine builds the figure into the title
 * ("Atlas Commercial: $33,954.90 denied dollars"). Printing it again in
 * the value column is the same repetition one line to the right.
 */
export function titleCarriesValue(title: string, value: string | undefined): boolean {
  if (value === undefined || value.trim() === "") return false;
  return normalize(title).includes(normalize(value));
}
