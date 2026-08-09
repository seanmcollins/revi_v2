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
  // "The question takes a movement of 'down' as given, and over this
  // window there was none." The answer that follows is context for a
  // movement that did not happen, not confirmation of one.
  PREMISE_FALSE: "The question's premise doesn't hold",
  // Neither confirmed nor refuted: the movement went the way the question
  // assumes and fell short of the SIZE it assumes. "Denials did not rise"
  // would be as false as "denials doubled" over a real +72.6%.
  PREMISE_PARTIAL: "The premise holds in direction, not in size",
  // The other half of the same verdict, and it has to be published as
  // loudly: a premise probe runs on every turn that asserts a movement,
  // and reporting it only when it FAILS leaves a reader unable to tell
  // "we checked and it happened" from "we never checked".
  PREMISE_VERIFIED: "The question's premise holds",
  // The flagship refusal, and until now the one that printed as
  // "ranking_refused:" in front of the analyst. Deliberately not "Ranking
  // refused" — the fact is that no order was published, and the engine's
  // own sentence beneath it does the arithmetic.
  RANKING_REFUSED: "No ranking was published",
  BOUNDED_CELLS_UNRANKED: "These cells are ceilings, not ranked",
  // A terminal bucket computed over a fraction of the records its
  // siblings hold. NOT "incomplete data" — the data is fine and the
  // period is not over.
  ADJUDICATION_INCOMPLETE: "The last point is still settling",
  // Distinct from RESULT_TRUNCATED ("Only the top rows were kept"), which
  // is about the probe's rows. This one is about the write-up: the rows
  // exist, in the chart and the evidence frame, and only some were
  // narrated — so superlatives describe the published slice.
  FINDINGS_TRUNCATED: "Only the top rows were written up",
  WINDOW_RELATIVE: "Your period was anchored to this data load",
  // "Next 30 days" names when the WORK happens, not a period to measure —
  // and no data exists after the newest data date.
  WINDOW_HORIZON: "That names when the work happens, not what to measure",
  NAMED_CUT_APPLIED: "What you named was resolved to this",
  // The complement of SUPPRESSION_APPLIED: these cells were NOT withheld.
  // A numerator under the threshold over a publishable population is shown
  // as "at most N", so the best-performing cells stay in the ranking with
  // their uncertainty stated instead of vanishing from it.
  SUPPRESSION_BOUNDED: "Some cells show upper bounds",
  // Deliberately not "Period outside this data". The engine emits this on
  // PARTIAL coverage — the asked-for period runs past the newest data and
  // the figures cover the part that exists — so "outside" would overstate
  // it into a period with no data at all, which is a different warning.
  WINDOW_OUT_OF_RANGE: "Part of that period isn't in this data",
  COMPARISON_ASSUMED: "Comparison window assumed",
  // A cell that fell outside the other window's top-N was never read, so
  // its prior figure is UNKNOWN — which is not zero, and the difference is
  // the whole reason this warning exists.
  COMPARISON_PRIOR_UNKNOWN: "Some cells have no prior figure to compare",
  // A refinement that resolved to a plan already executed: same evidence,
  // same findings, same caveats, different presentation.
  REFINEMENT_REUSED_PLAN: "The same answer, presented differently",
  // The operators the analyst asked for that the served answer does not
  // reflect — named rather than silently dropped.
  REFINEMENT_NOT_APPLIED: "Part of what you asked for was not applied",
  // The rows a chart published were not uniquely keyed by the axes it
  // declared, so cells that look like one category are several.
  CHART_ROWS_COLLAPSED: "This chart's rows aren't uniquely keyed",
  VALUE_CORRECTED: "A published value was corrected",
  DIRECTION_UNMATCHED: "The movement went the other way",
  WINDOW_ASSUMED: "Window assumed",
  // An as-of contract applies no start..end predicate at all, so the
  // period in the question scoped the cohort and the charts but not the
  // number. What the reader is owed is that last fact.
  SNAPSHOT_AS_OF: "Naming a period doesn't narrow this",
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
  // This platform's own dimension swap, disclosed on the turn that made
  // it: the drill does not read the cut the card was detected on.
  DIMENSION_REPOINTED: "This drill reads a different cut",
  /* -- what the platform did with the answer (does not change it) ---- */
  SUPPRESSION_APPLIED: "Small cells were suppressed",
  NARRATIVE_REDACTED: "A sentence was cut from the write-up",
  NARRATIVE_NOT_COMPOSED: "No write-up was composed",
  PROBE_TEMPLATE_SKIPPED: "A planned check was skipped",
  TRANSFORM_NOT_EXECUTABLE: "A calculation step could not run here",
  TRANSFORM_SKIPPED: "A calculation step was skipped",
  // Coverage the pipeline measured and did not publish: metric families
  // that were read, returned rows, and produced no finding — so the
  // ranking above speaks only for the families that did publish, and a
  // family's absence is not evidence that it is fine.
  PROBE_FAMILIES_EMPTY: "Some measures produced no finding",
  /* -- worklist-level facts about the portfolio ---------------------- */
  PORTFOLIO_CARDS_NOT_INVESTIGABLE: "Some of this list cannot be opened",
  PORTFOLIO_FEED_EMPTY: "Nothing was detected at this data load",
  PORTFOLIO_IMPACT_UNRECONCILED: "Some figures could not be re-derived here",
  PORTFOLIO_IMPACT_DIVERGED: "Some figures disagree with this platform's own",
  // NOT a divergence, and deliberately worded apart from one. The platform
  // re-derived the cell and then declined to compare the two figures
  // (a snapshot balance against a windowed flow); neither is stated as a
  // correction to the other.
  PORTFOLIO_IMPACT_NOT_COMPARABLE: "Some figures aren't comparable to this platform's",
  // WHICH figure ordered the list. These two are the same fact reported
  // from opposite sides, and the distinction decides how to read the
  // ranking: `PLATFORM` means the detector's number was set aside because
  // the two diverge, `DETECTOR` means this platform's own re-derivation
  // was set aside because it is not the same kind of quantity.
  PORTFOLIO_RANKED_ON_PLATFORM: "Ranked on re-derived figures",
  PORTFOLIO_RANKED_ON_DETECTOR: "Ranked on detector figures",
  /* -- the worklist, read into a conversation ------------------------ */
  // The intro line above the ranked cards on an answer. Load-bearing: the
  // cards are the detection feed's ranked work, NOT findings the turn
  // computed, and this is the sentence that keeps the two apart.
  // The question ROUTED to the governed work-prioritization playbook, so
  // the ranked cards are not a companion to the answer — they are it.
  WORKLIST_LEADS: "The worklist is the answer here",
  WORKLIST_ATTACHED: "Worklist attached",
  WORKLIST_UNAVAILABLE: "Worklist unavailable",
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
