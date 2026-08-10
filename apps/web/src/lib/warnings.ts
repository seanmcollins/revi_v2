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

import { humanizeIsoDates } from "@/lib/format";
import { humanizeInline } from "@/lib/humanize";
import { tidyProse } from "@/lib/prose";

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
  // The fourth verdict: a
  // movement between two suppressed ceilings, a comparison whose panels
  // are not equally settled, and a size the platform could not parse are
  // each UNVERIFIABLE — neither confirmed nor refuted. Deliberately not
  // "Premise unverified", which reads as "we did not get round to it";
  // the fact is that the evidence cannot decide it either way.
  NOT_COMPARABLE_WINDOWS: "These two periods can't be subtracted",
  PARENT_LEVEL: "How these parts relate to the whole",
  PREMISE_UNVERIFIABLE: "The premise cannot be checked on this evidence",
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
  // The cells a directional selection took OUT, named. "Show me all
  // twelve" returned ten and the two it dropped were the only two that
  // improved — an omission that flatters the premise, which is the one
  // kind a reader will never notice on their own.
  DIRECTION_OMITTED: "Some cells were left out of this selection",
  // Context carried onto a clarification resume from the thread it
  // interrupted, rather than defaulted. An INFO: it does not change how a
  // number reads, it says where the scope came from.
  RESUMED_CONTEXT: "Scope carried over from the question this answers",
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
  // The two registers of a clarification, declared by the engine — a
  // question with choices is an offer, not a verdict; only a declared
  // no-options dead end wears the loud register.
  CLARIFICATION_OPTIONS_OFFERED: "One answer from you settles this",
  CLARIFICATION_NO_OPTIONS: "There is no answerable option to offer here",
  // This platform's own dimension swap, disclosed on the turn that made
  // it: the drill does not read the cut the card was detected on.
  DIMENSION_REPOINTED: "This drill reads a different cut",
  // A monitor declaration that registered NOTHING. Deliberately not "Monitor
  // not created", which reads as bookkeeping: the fact an analyst has to
  // take away is that the thing they asked for is not happening, and the
  // engine's sentence underneath names the phrasings that would work.
  MONITOR_NOT_CREATED: "Nothing is being monitored",
  // The other half: the declaration is being HELD while the clarification
  // it triggered is on screen. Not a refusal and not a confirmation — the
  // one state where saying nothing is what destroyed the monitor.
  MONITOR_PENDING_CLARIFICATION: "Your monitor is waiting on the question below",
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

/* ------------------------------------------------------------------ */
/* No internal identifiers on a default surface                        */
/* ------------------------------------------------------------------ */

/**
 * `(portfolio_denial_trend, 1 row(s))` — a plan node and its row count,
 * printed inside the sentence that tells an analyst which measures came
 * back with nothing.
 *
 * Live, `PROBE_FAMILIES_EMPTY` reaches the screen as:
 *
 *   "8 metric famil(ies) on this plan were read and produced no published
 *    finding, so nothing above speaks for them: denial_rate
 *    (portfolio_denial_trend, 1 row(s)); cash_posted
 *    (portfolio_cash_trend, 1 row(s)); underpayment_variance
 *    (portfolio_underpayment, 12 row(s)); …"
 *
 * Eight frame ids and eight row counts, in the middle of the one caution
 * that says which parts of the question went unanswered. The frame id is
 * the engine's handle for a plan node; an analyst has never seen it, can
 * do nothing with it, and it is the reason this sentence reads as a log
 * line instead of a caveat.
 *
 * Matched narrowly — a snake_case token, a comma, a count, the literal
 * "row(s)" — so nothing that carries meaning is ever inside the match.
 */
const PLAN_NODE_CENSUS = /\s*\(([a-z][a-z0-9]*(?:_[a-z0-9]+)+),\s*[\d,]+\s+rows?\(s\)\)/g;

/**
 * A bare warehouse identifier inside prose: `denial_rate`,
 * `'timely_filing_at_risk_dollars'`, `ar_over_90_pct`.
 *
 * Deliberately requires at least one underscore. A single lowercase word
 * is a word — "service", "remit", "claim" are all bases and grains the
 * engine names in plain English, and re-spelling those would be this
 * function inventing a correction where there is no defect.
 */
const SNAKE_IDENTIFIER = /\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g;

/**
 * Is this token a MEASURE NAME, or is it a handle?
 *
 * The difference decides whether re-spelling it helps. `denial_rate` is a
 * measure and "denial rate" is the same fact in the reader's words;
 * `wm_003` is a data load's handle and "wm 003" is not a friendlier
 * version of it, it is a broken one — the same for `inv_0899f9defc32` and
 * `main__window__prior`. A handle that should not be on a default surface
 * is a separate defect from a measure that is merely spelled in
 * warehouse case, and this function refuses to conflate them: anything it
 * cannot prove is a measure name is left exactly as the engine wrote it.
 *
 * Two things have to hold: every segment is entirely letters or entirely
 * digits (which rules out hashes and ids), and at least two segments are
 * words of three letters or more (which rules out `wm_003`).
 */
function isMeasureName(token: string): boolean {
  const parts = token.split("_");
  if (parts.length < 2) return false;
  if (!parts.every((part) => /^[a-z]+$/.test(part) || /^[0-9]+$/.test(part))) return false;
  return parts.filter((part) => /^[a-z]{3,}$/.test(part)).length >= 2;
}

export interface PublicWarningBody {
  /** What a default surface prints. */
  text: string;
  /** True when an identifier or a plan-node census was taken out of it. */
  redacted: boolean;
  /** The engine's own sentence, kept so the surface can offer it. */
  verbatim: string;
}

/**
 * The warning body with the engine's internal handles taken out of it —
 * and the engine's own sentence kept beside it.
 *
 * Two rules the platform already holds itself to, applied to one more
 * surface. "No internal identifiers on any default surface" is why this
 * exists; "nothing is deleted, only relocated" is why it returns the
 * verbatim string as well, so the caller can offer the exact wording one
 * tap away. `answerToText` and the CSV preamble read `warning.message`
 * directly and are untouched: every export still carries the engine's
 * sentence byte for byte.
 *
 * Conservative by construction. Only three things are ever changed — a
 * `(plan_node, N row(s))` census is removed, the underscores come out of
 * a warehouse identifier, which becomes the same name spelled the way the
 * chart axis and the finding title already spell it, and an ISO date
 * literal is spelled the way a reader says it. No summarizing, no
 * reordering, no sentence dropped: a message carrying none of the three
 * comes back identical and `redacted` is false.
 *
 * The DATE rule is the same rule as the identifier rule, applied to the
 * other machine literal the engine writes into prose. The VERDICT is the
 * sentence a VP reads first in the calm layout — live, that sentence is
 * "It did not double — 7.1% → 7.9% vs prior year (2025-01-01..2025-08-02)"
 * — and a bracketed ISO range in it is the same leak as `denial_rate`
 * would be. As with every other change here, the engine's exact wording
 * travels on `verbatim`, the sheet offers it in one tap, and the copied
 * answer and the CSV preamble read `warning.message` directly and are
 * untouched.
 */
export function publicWarningBody(code: string, message: string): PublicWarningBody {
  const verbatim = warningBody(code, message);
  let text = verbatim.replace(PLAN_NODE_CENSUS, "");
  text = text.replace(SNAKE_IDENTIFIER, (token) =>
    isMeasureName(token) ? humanizeInline(token) : token,
  );
  text = humanizeIsoDates(text);
  // Collapse a double space or an orphaned separator left where a census
  // was: "…rate; cash…" not "…rate ; cash…".
  text = text.replace(/\s+([;,.])/g, "$1").replace(/\s{2,}/g, " ").trim();
  // And the fourth mechanical repair, on the same terms as the other
  // three: a stop printed twice where two sentence builders met. Live,
  // the maturity guard ships "Ask again once the thinner side matures.."
  // into the PREMISE_UNVERIFIABLE banner. The engine's exact wording
  // still travels on `verbatim`, one tap away.
  text = tidyProse(text);
  const redacted = text !== verbatim;
  return { text: redacted ? text : verbatim, redacted, verbatim };
}

/* ------------------------------------------------------------------ */
/* The verdict, and everything else                                    */
/* ------------------------------------------------------------------ */

export interface WarningPartition<T> {
  /** The answer to the question that was asked. Never collapsed. */
  verdicts: T[];
  /** Everything else, cautions before notes — the "N things to know". */
  rest: T[];
}

/**
 * Warnings split into the two things they actually are.
 *
 * A verdict (`PREMISE_*`, `RANKING_REFUSED`, `DIRECTION_UNMATCHED`) is
 * not a caveat about how to read a number — it IS the answer's finding
 * about the premise, or about whether an order could be published at
 * all. Both refined layouts lead with it in prose and tuck the rest
 * behind one disclosure, and this is the single place that decides which
 * is which, so the count on the integrity line and the rows in the sheet
 * cannot disagree about it.
 *
 * A stable partition, not a sort: inside each band the engine's own order
 * is the order the checks ran in, and shuffling it would throw away the
 * only sequencing the payload carries.
 */
export function partitionWarnings<T extends { code: string; severity?: string }>(
  warnings: readonly T[],
): WarningPartition<T> {
  return {
    verdicts: warnings.filter((w) => isVerdictCode(w.code)),
    rest: [
      ...warnings.filter((w) => !isVerdictCode(w.code) && w.severity === "caution"),
      ...warnings.filter((w) => !isVerdictCode(w.code) && w.severity !== "caution"),
    ],
  };
}

/* ------------------------------------------------------------------ */
/* One fact, once                                                      */
/* ------------------------------------------------------------------ */

/**
 * The verdict on the question the analyst actually asked.
 *
 * These codes are not cautions about how to read a number — they ARE the
 * answer's finding about the premise or about whether an order could be
 * published at all. Rendered in the same box, tone and type size as
 * "probe 'denial_code_mix__prior' reads 'denied_dollars'", the single most
 * important sentence on the answer has no rank over engine bookkeeping.
 * They are seated first and given heading weight; nothing else about them
 * changes, because the sentence is the server's.
 */
export const VERDICT_CODES: ReadonlySet<string> = new Set([
  "PREMISE_FALSE",
  "PREMISE_PARTIAL",
  "PREMISE_UNVERIFIABLE",
  "PREMISE_VERIFIED",
  "RANKING_REFUSED",
  "DIRECTION_UNMATCHED",
]);

export function isVerdictCode(code: string): boolean {
  return VERDICT_CODES.has(code);
}

/* ------------------------------------------------------------------ */
/* Marks on the data, notes below it, warnings only for verdicts       */
/* ------------------------------------------------------------------ */

/**
 * THE ONE LIST THAT DECIDES REGISTER: which codes may be rendered loud.
 *
 * Everything a warning says is worth saying. Only some of it is worth
 * SHOUTING, and the difference is not severity — it is whether the
 * sentence is a conclusion about the answer or context about the data.
 *
 *   a VERDICT is a conclusion. "The question's premise doesn't hold",
 *     "No ranking was published", "A published value was corrected" —
 *     each one changes what the reader may say out loud about the number,
 *     and a reader who skims past it walks away with a false claim. These
 *     keep the amber: the tinted box, the alert mark, the heading weight.
 *   everything ELSE is context. Basis, windows, suppression, settling,
 *     provisional buckets, comparison assumptions, keying, truncation,
 *     cohort size, an unreviewed benchmark. Every one of them is true and
 *     none of them is a finding against the answer, so they render as
 *     quiet captions in muted ink — below the figure they qualify, one
 *     tap from the full sentence wherever a surface has a disclosure.
 *
 * The register is decided by CODE and never by severity, because severity
 * is the server's answer to a different question ("does this change how
 * you read the number?") and answering it in amber is what made a page of
 * ordinary bookkeeping look like a page of alarms. `caution` still orders
 * the list — cautions before notes — it just no longer sets the ink.
 *
 * Membership, and why the two borderline codes landed where they did:
 *
 *   PREMISE_VERIFIED is a verdict and is deliberately NOT loud. It is the
 *     one premise outcome that is good news, and "the premise you assumed
 *     is correct" printed in the same amber as "your premise is false"
 *     teaches a reader that the colour means nothing. It keeps its seat at
 *     the top of the list (`VERDICT_CODES`) and loses only the alarm.
 *   BOUNDED_CELLS_UNRANKED is not a verdict — it does not lead the answer
 *     in prose — and it IS loud, because it is a refusal: those cells were
 *     not ranked, and a reader who takes the axis order for a league table
 *     has been misled by the picture rather than merely under-informed.
 *
 * This is presentation only. `warnings_v2` on the wire is untouched, the
 * Evidence rail and every export still carry each sentence in full, and
 * nothing on this list or off it is ever dropped from a surface — a note
 * that stops being amber becomes a caption, not a deletion.
 */
export const LOUD_CODES: ReadonlySet<string> = new Set([
  /* -- premise corrections: the answer's finding about the question -- */
  "PREMISE_FALSE",
  "PREMISE_PARTIAL",
  "PREMISE_UNVERIFIABLE",
  "DIRECTION_UNMATCHED",
  /* -- refusals: an order the platform declined to publish ----------- */
  "RANKING_REFUSED",
  "BOUNDED_CELLS_UNRANKED",
  /* -- a figure this platform published and then took back ----------- */
  "VALUE_CORRECTED",
]);

/**
 * May this code wear the warning register — amber ink, a tinted box, an
 * alert mark? Everything not on the list renders quiet, whatever its
 * severity.
 */
export function isLoudCode(code: string): boolean {
  return LOUD_CODES.has(code);
}

/**
 * `probe 'main__window__prior'` — an internal plan node, named inside a
 * warning sentence. The probe is the only thing that differs between six
 * otherwise identical `ALTERNATE_BASIS_USED` warnings, and it is the one
 * token in them an analyst has never seen and cannot act on.
 */
const PROBE_REFERENCE = /\bprobe(s)? '([^']+)'/g;

/** The same fact, spelled without whichever plan node happened to raise it. */
function factKey(code: string, message: string): string {
  return `${code}\0${message
    .replace(PROBE_REFERENCE, "probe ⟨⟩")
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase()}`;
}

/**
 * Warnings collapsed on the FACT they state rather than on their exact
 * wording.
 *
 * The server dedupes on `(code, message)` and defends it — "two
 * alternate_basis_used warnings naming different probes are two facts".
 * They are not; they are one fact spelled six ways. Live, one answer
 * carried six of them differing only by `probe 'main'`, `'premise'`,
 * `'main__window'`, `'main__window__prior'`, `'premise__window'`,
 * `'premise__window__prior'`, and another carried ten — 1,356px of amber,
 * 2.7 screen-heights, before a single number.
 *
 * Applied at the wire seam, so every surface downstream inherits it: the
 * banner rail, the copied text, and the CSV preamble that was carrying the
 * engine's plan structure above row 1 of a spreadsheet.
 *
 * Idempotent by construction. When the server's own fact-keyed dedupe
 * lands, each group has one member, the probe reference is left exactly as
 * the server wrote it, and this function returns its input unchanged.
 */
export function dedupeWarnings<T extends { code: string; message: string; count?: number }>(
  warnings: readonly T[],
): T[] {
  const order: string[] = [];
  const groups = new Map<string, { first: T; count: number; probes: string[] }>();
  for (const warning of warnings) {
    const key = factKey(warning.code, warning.message);
    const probes = [...warning.message.matchAll(PROBE_REFERENCE)].map((m) => m[2]);
    const group = groups.get(key);
    if (group === undefined) {
      order.push(key);
      groups.set(key, { first: warning, count: warning.count ?? 1, probes: [...probes] });
      continue;
    }
    group.count += warning.count ?? 1;
    for (const probe of probes) if (!group.probes.includes(probe)) group.probes.push(probe);
  }

  return order.map((key) => {
    const group = groups.get(key)!;
    if (group.count <= 1) return group.first;
    // Two or more entries collapsed into one. When they differed by their
    // probe, the surviving sentence must not keep one arbitrary plan node
    // in it — that would read as a fact about `main` when it is a fact
    // about six probes. The ids travel on `probes` instead, which debug
    // mode and the count badge both render.
    const distinctProbes = group.probes.length > 1;
    const message = distinctProbes
      ? group.first.message.replace(PROBE_REFERENCE, (_m, plural: string | undefined) =>
          plural ? "probes" : "a probe",
        )
      : group.first.message;
    return {
      ...group.first,
      message,
      count: group.count,
      ...(group.probes.length > 0 ? { probes: group.probes } : {}),
    };
  });
}

/* ------------------------------------------------------------------ */
/* One fact, one SURFACE                                               */
/* ------------------------------------------------------------------ */

/** Comparable form of a sentence: no case, no runs of space, no full stop. */
function normalizeSentence(text: string): string {
  return text
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[.;:]+$/, "")
    .toLowerCase();
}

/** Paragraph → sentences, keeping the delimiter on the sentence it ends. */
function sentencesOf(paragraph: string): string[] {
  return paragraph.split(/(?<=[.!?])\s+/).filter((s) => s.trim() !== "");
}

/**
 * A terminal stop with the next sentence welded onto it.
 *
 * Live, on the flagship answer: the composer appended the
 * `FINDINGS_TRUNCATED` disclosure to the clause before it with no space —
 *
 *   "…govern how far these figures can be
 *    generalized.Superlatives and spread statements on this answer
 *    describe the published slice, not the full population."
 *
 * — and two things followed from the missing space. The reader saw a
 * mangled join at the end of the write-up, and the FOLD could not see the
 * disclosure at all: `sentencesOf` splits on a stop FOLLOWED BY SPACE, so
 * the disclosure and its host were one unit, matched no banner, and the
 * sentence the fold exists to remove was printed twice.
 *
 * So the join is repaired before the split, not after it. This is the same
 * class of repair `tidyProse` makes to a doubled stop — a mechanical
 * defect made where two builders met, not a re-wording — and it is applied
 * BEFORE the comparison rather than after because `normalizeSentence`
 * collapses whitespace anyway: mending the join changes where sentences
 * begin and end, which is exactly the point, and changes no string that is
 * ever compared.
 *
 * THE FOLD REMOVES WHOLE SENTENCES AND ONLY WHOLE SENTENCES. There is no
 * path here that splices a fragment back into the prose: a sentence either
 * matches a banner's sentence entire and goes, or it stays entire.
 */
const WELDED_STOP = /(?<=[a-z]{2})([.!?])(?=[A-Za-z])/g;

/**
 * How many words a welded tail must have before it is treated as a
 * sentence.
 *
 * The guard, and the reason this is not a blind `replace`. "export.csv"
 * and "no.2" are not two sentences, and a rule that separated them would
 * be inventing a defect. A mandatory disclosure is a paragraph-length
 * clause; five words is comfortably below the shortest one the composer
 * writes and comfortably above a file extension.
 */
const WELDED_TAIL_WORDS = 5;

function mendWeldedStops(text: string): string {
  return text.replace(WELDED_STOP, (stop: string, _p1: string, offset: number) => {
    const clause = /^[^.!?]*/.exec(text.slice(offset + 1))?.[0] ?? "";
    const words = clause.trim().split(/\s+/).filter((w) => w !== "");
    return words.length < WELDED_TAIL_WORDS ? stop : `${stop} `;
  });
}

/**
 * The narrative with the sentences the banners already carry taken out of
 * it.
 *
 * Two correct fixes were never reconciled. The composer deliberately
 * builds mandatory disclosures into the prose (`mandatory_disclosures`)
 * while the card independently renders the same `warnings_v2` as
 * banners. Measured on one live turn: the
 * write-up is 4,933 characters of which 1,704 — 34.5% — are byte-identical
 * copies of banners on the same screen; one census sentence appears four
 * times on one answer; "this is not a ranking" is restated fourteen times
 * on one page.
 *
 * The BANNER is kept and the prose defers to it, not the other way round,
 * for one reason: warnings survive a reload and composed prose does not.
 * Collapsing onto the surface that does not persist would have made the
 * shared permalink even thinner than it already is.
 *
 * Deliberately conservative. A prose sentence is dropped only when it
 * matches a rendered warning's own sentence exactly (modulo case, spacing
 * and trailing punctuation), or is a whole sentence of one. WHOLE
 * SENTENCES ONLY: a partial match drops nothing, so no fragment is ever
 * spliced back into the prose. Nothing is paraphrased, nothing is
 * summarized, and a narrative that shares no sentence with any banner
 * comes back byte-identical — apart from a welded terminal stop, which is
 * separated so the reader does not read one (see `mendWeldedStops`).
 *
 * THE LEAD SENTENCE IS NEVER FOLDED, and that is unconditional.
 *
 * On a provisional window the composer opens the write-up with
 * the SETTLED reading — "Through June 2026 — the last period that has
 * finished settling — denial rate reads 9.1%…" — and the engine also
 * publishes that same paragraph as the body of `ADJUDICATION_INCOMPLETE`.
 * Byte-identical, so this function deleted it: the default layout's first
 * screen then carried the provisional 12.8% three times (lead sentence,
 * finding card, unmarked bar) and the settled 9.1% zero times, because the
 * warning it had been folded onto is not a verdict code and renders inside
 * a "things to know" disclosure that is collapsed by default.
 *
 * The fold's contract is that the PROSE defers to the banner on a caution.
 * A sentence the composer wrote as the ANSWER is not a caution, and the
 * opening sentence of the write-up is the one sentence this function can
 * identify as the answer without reading it. So it is exempt whatever it
 * happens to match — a fact stated twice costs a reader a repeated
 * sentence; a fact stated zero times costs them the answer.
 */
export function foldComposedDisclosures(
  narrative: string,
  warnings: readonly { code: string; message: string }[],
): { text: string; folded: number } {
  // The join is repaired FIRST, so a disclosure the composer welded to the
  // clause before it is a sentence this function can see — and so no
  // reader is left with "…generalized.Superlatives and spread statements…"
  // whether it folds or not. See `mendWeldedStops`.
  const mended = mendWeldedStops(narrative);
  if (mended.trim() === "" || warnings.length === 0) return { text: mended, folded: 0 };

  const banner = new Set<string>();
  for (const warning of warnings) {
    // BOTH spellings. The banner strips a machine prefix that IS the code
    // (`population_caveat: …`) and the composer builds the message into
    // the prose as the engine wrote it — so the same fact reaches the two
    // surfaces one prefix apart, and matching only one of them would fold
    // nothing on precisely the warnings that carry a prefix.
    for (const form of [warning.message, warningBody(warning.code, warning.message)]) {
      banner.add(normalizeSentence(form));
      // A disclosure composed of several sentences reaches the prose as
      // several sentences; each one of them is still the banner's text.
      for (const sentence of sentencesOf(form)) banner.add(normalizeSentence(sentence));
    }
  }
  banner.delete("");

  let folded = 0;
  const paragraphs = mended.split(/\n{2,}/);
  const kept: string[] = [];
  // Where the write-up's opening sentence is, wherever leading blank
  // lines put it: the first paragraph that has a sentence at all. Held out
  // of the fold by POSITION, so no wording rule is involved and a
  // paragraph further down that happens to open with the same words is
  // still an ordinary repeat.
  const leadParagraph = paragraphs.findIndex((p) => sentencesOf(p).length > 0);
  paragraphs.forEach((paragraph, p) => {
    const sentences = sentencesOf(paragraph);
    const keptSentences = sentences.filter((sentence, i) => {
      if (p === leadParagraph && i === 0) return true;
      if (!banner.has(normalizeSentence(sentence))) return true;
      folded += 1;
      return false;
    });
    if (keptSentences.length > 0) kept.push(keptSentences.join(" "));
  });

  // `folded` is what was actually removed — the count the fold note
  // prints, and never an estimate: each increment is one whole sentence
  // that is no longer in the returned text.
  if (folded === 0) return { text: mended, folded: 0 };
  return { text: kept.join("\n\n").trim(), folded };
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
