/**
 * Warning codes → what a reader should call them.
 *
 * The platform classifies every warning at the API boundary and publishes
 * `{code, severity, message, count}` as `warnings_v2` beside the untouched
 * prose (`revi_api.warning_codes`). This file is the client half of that
 * contract: the code is the handle the UI branches on, and the title is
 * the plain sentence that goes above the engine's own.
 *
 * Three rules, mirroring the server's:
 *
 *   the engine's sentence is never replaced. A title is added ABOVE the
 *     message, never instead of it — the message is the fact, the title is
 *     the shelf it sits on.
 *   an unknown code still renders. A code with no title here (including
 *     `UNCLASSIFIED`, which is what the server publishes for a sentence no
 *     rule recognized) shows its message alone rather than being dropped
 *     or captioned with a guess.
 *   severity is the server's, never re-derived. `caution` means "this
 *     changes how you should read the number"; `info` means "worth
 *     knowing, does not change the reading".
 *
 * The titles are deliberately not the codes prettified. `POPULATION_CAVEAT`
 * prettifies to "Population caveat", which is the same jargon with a
 * capital letter; what an analyst needs to be told is "how to read this
 * number".
 */

/** The code the server publishes for a sentence matching no known family. */
export const UNCLASSIFIED = "UNCLASSIFIED";

/**
 * Plain-language titles, keyed by the codes `revi_api.warning_codes`
 * publishes. Kept in that module's order so the two lists can be read side
 * by side; `contract-expectations.test.ts` pins that every published code
 * is either titled here or deliberately left bare.
 */
export const WARNING_TITLES: Readonly<Record<string, string>> = {
  /* -- how the number was scoped or qualified (changes the reading) -- */
  EMPTY_RESULT: "Why this came back empty",
  VALUE_CORRECTED: "A published value was corrected",
  DIRECTION_UNMATCHED: "The movement went the other way",
  WINDOW_ASSUMED: "Window assumed",
  DROPPED_GRAIN: "Part of the breakdown was dropped",
  FILTER_REDUNDANT: "A filter changed nothing",
  COMPARISON_WINDOW_LENGTH: "The two periods are different lengths",
  POPULATION_CAVEAT: "How to read this number",
  ALTERNATE_BASIS_USED: "Computed on a different date basis",
  COMPARISON_WINDOW_MISMATCH: "The comparison windows don't line up",
  RECONCILIATION_FAILED: "The parts don't add up to the whole",
  SCOPE_INTERACTS_WITH_CONTRACT: "This filter interacts with the metric's own definition",
  PROBE_OMITTED: "One of the planned checks did not run",
  RESULT_TRUNCATED: "Only the top rows were kept",
  COHORT_WINDOW_DROPPED: "The pinned population kept no window",
  ASSUMPTION_COMMITTED: "An assumption was made and applied",
  CLARIFICATION_ANSWER_APPLIED: "Read as an answer to the question above",
  /* -- what the platform did with the answer (does not change it) ---- */
  SUPPRESSION_APPLIED: "Small cells were suppressed",
  NARRATIVE_REDACTED: "A sentence was cut from the write-up",
  NARRATIVE_NOT_COMPOSED: "No write-up was composed",
  PROBE_TEMPLATE_SKIPPED: "A planned check was skipped",
  TRANSFORM_NOT_EXECUTABLE: "A calculation step could not run here",
  TRANSFORM_SKIPPED: "A calculation step was skipped",
  /* -- worklist-level facts about the portfolio ---------------------- */
  PORTFOLIO_CARDS_NOT_INVESTIGABLE: "Some of this list cannot be opened",
  PORTFOLIO_FEED_EMPTY: "Nothing was detected at this data load",
  PORTFOLIO_IMPACT_UNRECONCILED: "Some figures could not be re-derived here",
  PORTFOLIO_IMPACT_DIVERGED: "Some figures disagree with this platform's own",
};

/**
 * The title for a code, or `undefined` when there is none to show.
 *
 * `UNCLASSIFIED` deliberately returns nothing: the server is saying "we
 * have no handle for this one", and inventing a confident heading over an
 * unrecognized sentence would be the client claiming the classification
 * the server just declined to make.
 */
export function warningTitle(code: string): string | undefined {
  return WARNING_TITLES[code];
}

/**
 * The message with a redundant machine prefix removed.
 *
 * The engine writes `population_caveat: Deadline proximity …` — a sentence
 * carrying its own code as a prefix, which made sense when the code had
 * nowhere else to travel. It travels in `code` now, and is rendered as a
 * title, so repeating it in the body is a stutter.
 *
 * Stripped ONLY when the prefix IS the code (normalizing case and word
 * separators), so nothing that carries information is ever removed:
 * `suppression: cells counting fewer than 11 …` keeps its prefix, because
 * "suppression" is not `SUPPRESSION_APPLIED` and this function does not
 * get to decide that it means the same thing.
 */
export function warningBody(code: string, message: string): string {
  const separator = message.indexOf(": ");
  if (separator <= 0) return message;
  const head = message.slice(0, separator);
  const normalized = head.trim().toUpperCase().replace(/[\s-]+/g, "_");
  if (normalized !== code) return message;
  return message.slice(separator + 2).trim() || message;
}

/**
 * An error message split into the sentence written for the reader and the
 * machine tail written for whoever has to fix it.
 *
 * The envelope carries both in one string:
 *
 *   "That metric can't be dated the way this question needs in this
 *    warehouse. … [DATE_BASIS_INVALID: date basis 'remit' is not allowed
 *    for metric 'ar_balance' (allowed: ['service', 'submission'])]"
 *
 * and the card was printing the whole thing under a `<code>` chip carrying
 * the same code — so the code appeared twice, next to a raw metric id and
 * a Python list literal, in the place a reader looks to find out what to
 * do next. The tail is operator material: it belongs in debug mode, where
 * the trace already lives.
 *
 * Split ONLY when the bracketed token IS this error's own code, so nothing
 * that carries information the envelope has not already stated is removed.
 * A tail naming some other code stays on screen, because this function
 * cannot prove it is a duplicate and will not guess.
 *
 * The client composes NOTHING here. Whatever recovery an error offers is
 * the server's sentence to write — a recovery derived on this side is how
 * a card came to recommend a date basis the same message declares illegal.
 */
export function splitErrorMessage(
  code: string,
  message: string,
): { sentence: string; machine?: string } {
  const match = /\s*\[([A-Z][A-Z0-9_]*):[\s\S]*\]\s*$/.exec(message);
  if (!match || match[1] !== code) return { sentence: message };
  const sentence = message.slice(0, match.index).trim();
  if (sentence === "") return { sentence: message };
  return { sentence, machine: match[0].trim() };
}
