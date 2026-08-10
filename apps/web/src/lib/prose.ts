/**
 * PRESENTATION HYGIENE — the two repairs a surface is allowed to make to
 * a sentence the platform composed.
 *
 * The rule everywhere else in this client is that prose is passed through
 * and never rewritten: every figure in a statement is a figure the engine
 * measured, and a client that re-words them is a second author nobody can
 * audit. These two functions do not re-word anything. They repair a
 * MECHANICAL defect in the join — a stop printed twice where two builders
 * met, and a rank grammar applied to a set of one — and both are
 * conservative by construction: a string carrying neither defect comes
 * back byte-identical.
 *
 * The same discipline `humanizeIsoDates` and `publicWarningBody` already
 * follow: change only what can be proven wrong, leave everything else,
 * and keep the engine's exact wording reachable on the surfaces that
 * exist to reproduce a query (the decision trace, the debug panel).
 *
 * The EXPORTS take exactly one of these — `tidyProse`, on the caveat
 * lines, in `lib/export.ts`. They take none of the rest: no identifier
 * redaction, no date respelling, no rank rewrite, because a file whose job
 * is to reproduce a query may not re-word it. The stop printed twice is
 * admitted because it is not wording, and because a caveat that reads
 * "…matures.." on the sheet and "…matures." on the screen is one answer
 * with two spellings depending on which button was pressed.
 */

import { respellInitialisms } from "@/lib/humanize";

/**
 * A parenthetical that brought its own full stop into a sentence that
 * already had one: "…anything over a point.)." → "…anything over a
 * point.)".
 *
 * Live on `/monitors`, in the materiality note of the JOC account's tile —
 * the reviewer's own note was rewritten twice and the stacked stop
 * survived both rewrites, because it is not in either half. It is made
 * where they join.
 */
const STACKED_STOP = /\.\)([.,;:])/g;

/**
 * Exactly two dots, next to neither a third dot nor a digit.
 *
 * The digit guard is the whole reason this is a regex and not a
 * `replace("..", ".")`: `2026-07-01..2026-07-31` is an ISO range and the
 * dots are its operator. The dot guard keeps an ellipsis whole — "Ask
 * again once the thinner side matures.." is a defect and "Reading this
 * monitor's settings..." is not.
 */
const DOUBLED_STOP = /(?<![.\d])\.\.(?![.\d])/g;

/**
 * The sentence with its doubled punctuation collapsed, and nothing else
 * touched.
 *
 * Applied where a composed sentence reaches a reader — a tile statement,
 * a brief line, a warning body, a finding row. Not applied to anything
 * whose job is reproducibility.
 */
export function tidyProse(text: string): string {
  if (text === "") return text;
  return text.replace(STACKED_STOP, ")$1").replace(DOUBLED_STOP, ".");
}

/**
 * "#1 of 1" is not a rank.
 *
 * A breakdown narrowed to a single payer keeps the ranking grammar the
 * breakdown builder writes — "Ashvale Health Plan ranks #1 of 1 measured
 * by denial rate over 2026-07-01..2026-07-31: 22.9%" — and a rank over a
 * set of one is a claim about an order that was never measured. Two of
 * the demo tenant's own tiles publish it, one of them the JOC account
 * named in the curated note.
 *
 * The repair keeps every word the engine wrote except the rank clause
 * itself, which is replaced with what the payload actually supports: this
 * was the only cell there was. `#1 of 12` is untouched — the boundary
 * after the second `1` is what distinguishes them.
 */
export function scalarizeRankOfOne(text: string): string {
  return text.replace(
    /\branks\s+#1\s+of\s+1\b(\s+measured\s+by\b)?/gi,
    (_match, measuredBy: string | undefined) =>
      measuredBy === undefined
        ? "is the only cell measured here"
        : "is the only cell measured here —",
  );
}

/**
 * A data load named by its internal handle, in a sentence a normal reader
 * meets on the default surface.
 *
 * The worklist's own summary arrives as "8 of 33 ranked cards at
 * watermark wm_003, highest governed priority first." `wm_003` is a
 * database handle: it tells an analyst nothing that "this data load" does
 * not, and "data load" is what the rest of the product says. So the
 * PHRASE is rewritten and the handle goes with it.
 *
 * Deliberately narrow. It matches only a handle introduced by the words
 * that make it one ("watermark wm_…", "data load wm_…"), so a bare id
 * standing on its own — which may be the only thing a sentence is about —
 * is left alone rather than guessed at. The id is never re-spelled or
 * word-split; `publicWarningBody` has a test pinning that "wm 003" is a
 * broken spelling of a handle rather than a friendlier one, and this does
 * not go near it. The engine's untouched sentence stays reachable on the
 * surface that renders it.
 */
const LOAD_HANDLE = /\b(?:watermark|data load)\s+(wm_[A-Za-z0-9_]+)/gi;

export function humanizeLoadHandles(text: string): string {
  return text.replace(LOAD_HANDLE, "this data load");
}

/**
 * A composed sentence that starts in lower case.
 *
 * The engine builds statements by concatenating clauses, and the leading
 * clause is often a bare metric phrase — "denial rate is ≤ 76.9% over…",
 * "the prior load produced no value for this monitor…". On a tile, that
 * clause IS the sentence a reader meets, and a screen of sentences that
 * all start in lower case reads as unfinished rather than as considered.
 * The owner's question about it — "do lowercase starts to sentences
 * inspire confidence?" — has one answer.
 *
 * The narrowest possible repair: the FIRST character, only when it is a
 * lower-case letter, and never inside a token that is evidently an
 * identifier rather than a word (`denial_rate` stays `denial_rate` — a
 * capitalized id is a wrong id, and dropping it is not this function's
 * job). No other character is touched, so a sentence that already opens
 * with a capital, a figure, a quotation mark or a symbol comes back
 * byte-identical.
 */
export function capitalizeOpening(text: string): string {
  const first = text.search(/\S/);
  if (first === -1) return text;
  const char = text[first];
  if (char < "a" || char > "z") return text;
  // An opening token carrying an underscore is a machine name, not a word.
  const token = /^\S+/.exec(text.slice(first))?.[0] ?? "";
  if (token.includes("_")) return text;
  return text.slice(0, first) + char.toUpperCase() + text.slice(first + 1);
}

/**
 * The mechanical repairs, in the order they have to run: the rank clause
 * first (it rewrites words), the punctuation second (it repairs joins,
 * including any the first repair produced), the initialisms third, and the
 * opening capital last (it acts on whatever ends up first, and must run
 * AFTER the initialisms so "ar over 90 %" becomes "A/R over 90 %" rather
 * than "Ar over 90 %").
 *
 * The initialism pass is `respellInitialisms`, and it is the same kind of
 * repair as the other three: the engine composes statements through a
 * fallback humanizer that splits an id on underscores and knows nothing
 * about how this product spells `ar`, so one monitor tile printed "days in
 * A/R by payer" as its label and "…measured by days in ar as of
 * 2026-08-02" as its statement, one card, two spellings. Words the
 * spelling table has never heard of are untouched.
 */
export function readableStatement(text: string): string {
  return capitalizeOpening(respellInitialisms(tidyProse(scalarizeRankOfOne(text))));
}

/**
 * THE SAME REPAIRS, FOR A LABEL RATHER THAN A SENTENCE.
 *
 * A monitor's `label`, a lead's actionability class, a session's title: not
 * prose, but text that OPENS a card, and the owner's rule is about the
 * opening rather than about the grammar behind it. Live, Home's monitor
 * digest opened three of its four cards with "denial rate for State
 * Medicaid MCO", "days in A/R by payer", "monthly denial rate for Veritas
 * Comp Fund" — the analyst's own words, stored the way they were typed, and
 * a screen of them reads as a page that failed to finish rendering.
 *
 * It differs from `readableStatement` in exactly one way, and the
 * difference is the point: `scalarizeRankOfOne` is not applied. That repair
 * rewrites a rank CLAUSE, which is a thing a statement can contain and a
 * label cannot; running it over a label would be this client editing a name
 * somebody chose.
 *
 * Same conservatism as everything else in this file. A label already
 * opening with a capital, a figure or a symbol comes back byte-identical,
 * an opening token carrying an underscore is left alone (a capitalized id
 * is a wrong id), and the raw value is what continues to reach the exports —
 * this is a render-time repair, on the same terms as `tidyProse`.
 */
export function readableLabel(text: string): string {
  return capitalizeOpening(respellInitialisms(tidyProse(text)));
}
