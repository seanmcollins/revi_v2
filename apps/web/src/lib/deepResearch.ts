/**
 * DEEP RESEARCH, AT THE SEAM.
 *
 * The mode's wire shapes were published with M48 and land in
 * `types.gen.ts` — twenty-five schemas, generated from the OpenAPI
 * document. Nothing here re-declares them: the aliases below name the ones
 * the surface touches so a component imports a word rather than a
 * `components["schemas"][…]` subscript, and the reading functions turn the
 * decimal STRINGS the wire carries (a rate is `"0.5833333333"`, an interval
 * bound is `"0.4220025157"`) into the numbers this product formats.
 *
 * TWO RULES THIS FILE EXISTS TO HOLD.
 *
 *   A RATE THAT WAS NOT PUBLISHED HAS NO NUMBER. `RateCellPayload.rate` is
 *     `null` on a not-estimable cell, and the population and the dollars
 *     behind it are published anyway. Every reader here returns `undefined`
 *     for the rate and keeps `n` and the dollars, because the one thing a
 *     surface must never do with a withheld rate is render a dimmed zero
 *     where the measurement would have been.
 *   AN INTERVAL IS PART OF ITS FIGURE. Both interval shapes are read
 *     alongside the point estimate and never separately, so a call site
 *     cannot end up holding a total without its bounds.
 */

import type { components } from "@/lib/types.gen";

/* ------------------------------------------------------------------ */
/* The wire, named                                                     */
/* ------------------------------------------------------------------ */

/** Which denials a run is about — the closed selector an offer carries. */
export type ResearchSelector = components["schemas"]["DeepResearchSelector"];

/**
 * A place another surface can offer to start a run from — and what that
 * run proposes to do.
 *
 * `DeepResearchAffordance` is the shape an ANSWER or a lead card carries:
 * a label, a sentence, and the closed selector the run would be over. A
 * run is a minute of work and a real model call, so the surface that
 * offers one confirms intent before spending it — and a confirmation is
 * only worth reading if it says what the run will actually look at.
 *
 * THE FIELDS BELOW ARE THAT, AND THEY ARE REAL NOW. `POST /v1/deep-
 * research` with `plan_only: true` resolves the whole dry run — the size
 * of the population, the readings, the alternatives — and starts nothing
 * (`previewDeepResearch`). Each one is still read defensively, because an
 * affordance that arrives WITHOUT a resolved preview (the wire's own
 * `deep_research` block, before anybody asks for one) is a complete offer
 * and the card degrades to what the MODE does rather than to nothing:
 *
 *   `scope` → `{ open_denials, open_dollars_cents }`. The size of the
 *     population, so "this will analyze denials from Atlas Commercial"
 *     becomes "…565 of them, worth $1,153,302.17". The same two
 *     quantities `ExpectedRecoveryRowPayload` publishes, over the whole
 *     population.
 *   `plan` → the standing angles resolved for THIS population without
 *     running them. Only `title` and `purpose` are read here.
 *   `options` → the other populations this offer could run over (the
 *     payer alone, every open denial). Each is a CLOSED selector exactly
 *     like `population`, so choosing one changes what is posted and
 *     nothing else — no sentence is re-parsed and no scope is widened by
 *     the client.
 *   `generalized` → what a run would do about a RESEARCH QUESTION, which
 *     is a different thing from the standing recoverability review: what
 *     it established about the data, which background notes it read, and
 *     which readings it therefore intends to take, each with the reason
 *     it is in the run. Present only when the request carried a question.
 */
type ResearchOfferWire = components["schemas"]["DeepResearchAffordance"];

/** How big the population is, when the offer says. */
export interface ResearchOfferScope {
  openDenials: number;
  openDollarsCents: number;
}

/** One line of what the run will look at: the reading, and why. */
export interface ResearchOfferAngle {
  title: string;
  purpose: string;
}

export interface ResearchOffer extends ResearchOfferWire {
  scope?: ResearchOfferScope;
  /** The readings this run proposes, resolved for this population. */
  plan?: ResearchOfferAngle[];
  /** Other populations this offer could run over, as closed selectors. */
  options?: ResearchSelector[];
  /**
   * The question this offer is for, when a reader asked one.
   *
   * Set only where a preview has already been resolved, and it is what
   * the launch posts — the SAME question the preview described, so the
   * run a reader confirms is the run they read about. Its absence is what
   * tells a card it may resolve a preview of its own.
   */
  question?: string;
  /** What a run would do about that question, resolved without doing it. */
  generalized?: GeneralizedResearchPreview;
}
export type ResearchRun = components["schemas"]["DeepResearchRunResponse"];
export type ResearchReport = components["schemas"]["DeepResearchReport"];

/* ------------------------------------------------------------------ */
/* The two artifacts a run can produce                                 */
/* ------------------------------------------------------------------ */

/**
 * A RESEARCH STUDY'S REPORT — the other thing a run can be.
 *
 * The recoverability review answers one standing question about open
 * denials, so its report is a priced headline with the populations behind
 * it. A research question has no headline dollar figure to BE the answer,
 * and a surface that rendered one over "why has our A/R over 90 been
 * climbing" would be showing a number that answers a different question.
 *
 * So there are two report shapes and the run response discriminates
 * between them (`report_kind`). Nothing about the review's shape moved;
 * this is additive on the wire and additive here.
 */
export type ResearchStudy = components["schemas"]["GeneralizedResearchReport"];
export type ResearchReading = components["schemas"]["ResearchReadingPayload"];
export type ResearchFigure = components["schemas"]["ResearchFigurePayload"];
export type ResearchStudyWalk = components["schemas"]["ResearchWalkPayload"];
export type ResearchStudyRound = components["schemas"]["ResearchRoundPayload"];
export type ResearchWalkStep = components["schemas"]["ResearchWalkStepPayload"];
export type ResearchStudyCensoring = components["schemas"]["ResearchCensoringPayload"];
export type ResearchDetermination = components["schemas"]["DeterminationPayload"];
export type ResearchReportKind = "recovery" | "generalized";

/**
 * Is this payload a study rather than a recoverability review?
 *
 * Read off the payload's OWN discriminator rather than off the run
 * response, because the SSE `research_complete` frame carries the report
 * and nothing else — a watcher holding a frame has no envelope to consult.
 * The review carries no `kind` at all, which is what keeps its bytes
 * identical to what M48 shipped.
 */
export function isResearchStudy(report: unknown): report is ResearchStudy {
  return (
    typeof report === "object" &&
    report !== null &&
    (report as { kind?: unknown }).kind === "generalized_research"
  );
}

/** A figure's number, or nothing at all — the tier is the authority. */
export function figureValue(figure: ResearchFigure): number | undefined {
  if (figure.evidence !== "measured") return undefined;
  return decimal(figure.value);
}

/**
 * The figures a reading MEASURED, in the order it published them.
 *
 * Ceilings and withheld rows are excluded rather than dimmed, because
 * every caller of this is doing arithmetic or drawing a mark — and a
 * ceiling in either is a claim the reading did not make. Both still render
 * on the reading itself, with their marks.
 */
export function measuredFigures(reading: ResearchReading): ResearchFigure[] {
  return (reading.figures ?? []).filter((figure) => figure.evidence === "measured");
}
export type ResearchProgress = components["schemas"]["DeepResearchProgressPayload"];
export type ResearchSummary = components["schemas"]["DeepResearchSummary"];
export type ResearchPlan = components["schemas"]["ResearchPlanPayload"];
export type ResearchAngle = components["schemas"]["ResearchAnglePayload"];
export type ResearchHeadline = components["schemas"]["HeadlinePayload"];
export type ResearchStratum = components["schemas"]["ExpectedRecoveryRowPayload"];
export type ResearchRateCell = components["schemas"]["RateCellPayload"];
export type ResearchContrast = components["schemas"]["ContrastPayload"];
export type ResearchTimeliness = components["schemas"]["TimelinessCurvePayload"];
export type ResearchDeadline = components["schemas"]["DeadlinePayload"];
export type ResearchDeadlineRow = components["schemas"]["DeadlineRowPayload"];
export type ResearchCensoring = components["schemas"]["CensoringPayload"];
export type ResearchThinPopulations = components["schemas"]["ThinPopulationsPayload"];
export type ResearchAngleEvidence = components["schemas"]["AngleEvidencePayload"];
export type ResearchWarning = components["schemas"]["WarningPayload"];

/** `preview | planning | running | complete | failed | interrupted | cancelled`. */
export type ResearchStatus = ResearchRun["status"];
/** `orient | consult | plan | execute | read | round | synthesize`. */
export type ResearchPhaseId = ResearchProgress["phase"];

/**
 * A run that has not finished is still moving; everything else is settled.
 *
 * `preview` is settled by this rule and that is exactly right: a dry run
 * started nothing, so there is nothing to watch, nothing to stream and
 * nothing that will ever change on its own.
 */
export function isRunning(status: ResearchStatus): boolean {
  return status === "planning" || status === "running";
}

/**
 * DID THIS RUN GO WRONG, OR DID SOMEBODY END IT?
 *
 * Three ways a run ends without a report and only two of them are faults.
 * `failed` is the platform's, `interrupted` is a process that died holding
 * the run — both are things that happened TO a reader. `cancelled` is a
 * reader pressing Stop, which is the surface doing what it was asked, and
 * rendering it in the warning register would report somebody's own
 * decision back to them as a problem.
 */
export function wasStopped(status: ResearchStatus): boolean {
  return status === "cancelled";
}

/** A run that ended badly — never a run somebody stopped on purpose. */
export function hasFailed(status: ResearchStatus): boolean {
  return status === "failed" || status === "interrupted";
}

/* ------------------------------------------------------------------ */
/* The dry run, named                                                  */
/* ------------------------------------------------------------------ */

/**
 * WHAT A RUN WOULD DO, RESOLVED WITHOUT DOING ANY OF IT.
 *
 * `POST /v1/deep-research` with `plan_only: true` answers 200 with a run
 * whose id is empty and whose status is `preview`: the population and its
 * size, the readings, the other populations on offer — and, when the
 * request carried a research QUESTION, what the run learned about the
 * data and what it therefore intends to read.
 *
 * The camelCase shapes below are what the surfaces hold. Every string in
 * them was composed by the server beside the figure it quotes, and this
 * file's whole discipline about them is that NOTHING RE-WORDS ONE: a path
 * choice arrives as a whole sentence carrying its own coverage, and a
 * second phrasing of it here would be the one that loses the coverage.
 */
export type ResearchReadingShape =
  components["schemas"]["PlannedReadingPayload"]["shape"];

/** One thing the run established about the data before it chose anything. */
export interface ResearchPathChoice {
  subject: string;
  statement: string;
}

/** One background note the run read, by title. */
export interface ResearchConsultedNote {
  title: string;
  matchedOn: string[];
}

/** One reading the run intends to take, and the reason it is there. */
export interface ResearchPlannedReading {
  title: string;
  reason: string;
  round: number;
  /** What this reading goes after, when it goes after something. */
  chases: string;
  /**
   * The reading's family, when this build knows the one it was sent.
   *
   * Never rendered — the shapes are wire tokens and get no client
   * rendering — so an unfamiliar one costs the reading nothing. It is
   * dropped from the shape field and the reading is kept, because the
   * title and the reason are the whole of what a reader is owed.
   */
  shape?: ResearchReadingShape;
}

/** What a run would do about a research question. */
export interface GeneralizedResearchPreview {
  researchQuestion: string;
  /**
   * WHAT THIS RUN ACTUALLY READS, in a reader's words.
   *
   * Not the same thing as the recoverability review's population, and the
   * difference is a correctness bug rather than a wording preference: a
   * question about A/R over 90 reads balances, aging buckets and filing
   * runway across claims and never opens a denial inventory, so a card
   * headed "deep research on every open denial" over it names a
   * population the run does not touch.
   */
  populationLabel: string;
  /** The period it will read, in words ("Jul 1, 2026 through Aug 2, 2026"). */
  windowLabel: string;
  pathChoices: ResearchPathChoice[];
  knowledgeStatement: string;
  knowledgeConsulted: ResearchConsultedNote[];
  readings: ResearchPlannedReading[];
  rationale: string;
  /** `model` chose the readings, or `revi` fell back to its standing set. */
  authoredBy: "model" | "revi";
  roundsPlanned: number;
  /** Non-empty when nothing in the data can answer the question. */
  refusal: string;
}

/** The whole dry run, as the surfaces hold it. */
export interface ResearchPreview {
  population: ResearchSelector;
  scope?: ResearchOfferScope;
  plan: ResearchOfferAngle[];
  options: ResearchSelector[];
  dataLoadLabel: string;
  generalized?: GeneralizedResearchPreview;
}

/* ------------------------------------------------------------------ */
/* The phases, in words a reader owns                                  */
/* ------------------------------------------------------------------ */

/**
 * WHAT A RUN DOES, said the way somebody waiting would say it.
 *
 * The wire's `phase` is seven words from a compiler — `orient | consult |
 * plan | execute | read | round | synthesize`. What the reader is owed is
 * what is happening to THEIR data: it is being read, then measured, then
 * — if the run finds something worth chasing — read again, and finally
 * written up. The server's own `message` ("Comparing payers", "Reading the
 * background notes that bear on this") rides underneath as the detail, so
 * nothing here stands in for a sentence the platform already wrote.
 *
 * SEVEN PHASES, FOUR ROWS, AND `covers` IS WHY. `orient`, `consult` and
 * `plan` are three ways of reading the data before anything is measured;
 * drawn as three checkboxes they would tell a reader that the wait has
 * three parts when it has one. `read` and `round` are one state too —
 * the run finished a pass and went after what it found. Collapsing them
 * is not a simplification: each row is still exactly one thing that
 * happens, and the server's sentence underneath says which part of it.
 *
 * THE ITERATION ROW IS DRAWN ONLY IF IT HAPPENS. Most runs take one pass,
 * and a permanently pending "going after what it found" would promise a
 * second round that the question never earned.
 *
 * `note` is on the last one because it is the honest account of where the
 * minute goes: seven angles measure in under twenty milliseconds each and
 * the write-up is a model call. A progress view that implies the measuring
 * is the slow part teaches a reader to expect the wrong thing.
 */
export interface ResearchPhaseModel {
  /** The wire phase this row is named for, and its handle on the surface. */
  id: ResearchPhaseId;
  /** Every wire phase that IS this row. */
  covers: readonly ResearchPhaseId[];
  label: string;
  note?: string;
  /** Drawn only once the run has actually been here. */
  onlyWhenReached?: boolean;
}

export const RESEARCH_PHASES: readonly ResearchPhaseModel[] = [
  { id: "plan", covers: ["orient", "consult", "plan"], label: "Reading your data" },
  { id: "execute", covers: ["execute"], label: "Running the analysis" },
  {
    id: "round",
    covers: ["read", "round"],
    label: "Going after what it found",
    onlyWhenReached: true,
  },
  {
    id: "synthesize",
    covers: ["synthesize"],
    label: "Writing it up",
    note: "Most of the minute goes here. The measuring is quick; the writing is not.",
  },
];

/** The row a wire phase belongs to — the first one, for an unknown phase. */
export function researchPhaseFor(phase: ResearchPhaseId): ResearchPhaseModel {
  return RESEARCH_PHASES.find((row) => row.covers.includes(phase)) ?? RESEARCH_PHASES[0]!;
}

/** Where a phase sits against the one the run is in: done, now, or ahead. */
export function phaseState(
  phase: ResearchPhaseId,
  current: ResearchPhaseId,
  status: ResearchStatus,
): "done" | "active" | "pending" {
  const at = RESEARCH_PHASES.indexOf(researchPhaseFor(current));
  const index = RESEARCH_PHASES.indexOf(researchPhaseFor(phase));
  if (!isRunning(status)) return status === "complete" ? "done" : index <= at ? "done" : "pending";
  if (index < at) return "done";
  if (index === at) return "active";
  return "pending";
}

/**
 * HAS THIS RUN READ SOMETHING AND GONE AFTER IT?
 *
 * Either the wire says which round it is on, or the phase itself says the
 * run is deciding what to chase. Both are the same fact, and a surface
 * that waited for the counter would say nothing about a deployment whose
 * progress payload predates it.
 */
export function hasIterated(progress: ResearchProgress): boolean {
  return (
    (progress.round_index ?? 0) > 0 ||
    progress.phase === "read" ||
    progress.phase === "round"
  );
}

export interface ResearchPhaseRow {
  phase: ResearchPhaseModel;
  state: "done" | "active" | "pending";
}

/**
 * The rows to draw, in order, for where this run has got to.
 *
 * The iteration row is the only one with a rule of its own: a run in its
 * SECOND pass is back on `execute`, which is earlier in the order, and
 * ordering alone would call the round it already finished "not started".
 * The counter is the authority there, because it is a record of something
 * that happened rather than a position in a list.
 */
export function researchPhaseRows(
  progress: ResearchProgress,
  status: ResearchStatus,
): ResearchPhaseRow[] {
  const iterated = hasIterated(progress);
  const rows: ResearchPhaseRow[] = [];
  for (const phase of RESEARCH_PHASES) {
    if (phase.onlyWhenReached && !iterated) continue;
    const state = phaseState(phase.id, progress.phase, status);
    rows.push({
      phase,
      state: phase.id === "round" && iterated && state === "pending" ? "done" : state,
    });
  }
  return rows;
}

/* ------------------------------------------------------------------ */
/* Decimal strings → numbers                                           */
/* ------------------------------------------------------------------ */

/**
 * A decimal the wire sent as a string, or `undefined`.
 *
 * The mode publishes rates, p-values and interval bounds as exact decimal
 * strings rather than as floats — `"0.5833333333"`, `"0E-10"` — because a
 * rate that has been through a float is not the rate that was measured.
 * They are parsed HERE and nowhere else, so a component never has to decide
 * what `null` means.
 */
export function decimal(value: string | null | undefined): number | undefined {
  if (value === null || value === undefined || value === "") return undefined;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : undefined;
}

/** A cell's measured rate as a fraction, or nothing at all. */
export function cellRate(cell: ResearchRateCell): number | undefined {
  // The evidence tier is the authority, not the presence of a string: a
  // `not_estimable` cell must never render a rate even if one arrived.
  if (cell.evidence !== "measured") return undefined;
  return decimal(cell.rate);
}

/** Its interval, when there is a measurement for one to be around. */
export function cellInterval(
  cell: ResearchRateCell,
): { low: number; high: number; confidence: number | undefined } | undefined {
  if (cell.evidence !== "measured" || !cell.interval) return undefined;
  const low = decimal(cell.interval.low);
  const high = decimal(cell.interval.high);
  if (low === undefined || high === undefined) return undefined;
  return { low, high, confidence: decimal(cell.interval.confidence) };
}

/**
 * "95%" from the wire's `"0.95"`.
 *
 * Rounded to whole percent because that is how a confidence level is said
 * out loud, and the wire never sends a fractional one.
 */
export function confidenceLabel(confidence: string | number | undefined): string {
  const value = typeof confidence === "number" ? confidence : decimal(confidence);
  if (value === undefined) return "";
  return `${Math.round(value * 100)}%`;
}

/* ------------------------------------------------------------------ */
/* The population, in words                                            */
/* ------------------------------------------------------------------ */

/**
 * What a run is over, as a phrase a sentence can contain.
 *
 * The server writes the label ("denials from Atlas Commercial", "every open
 * denial") and it is preferred whenever it sent one. The fallbacks are for
 * a selector composed on this side — the all-open link on Home — and they
 * are the same words the server would have used.
 */
export function populationLabel(selector: ResearchSelector | undefined): string {
  if (selector === undefined) return "every open denial";
  if (selector.label !== undefined && selector.label !== "") return selector.label;
  const values = selector.values ?? [];
  if (selector.kind === "all_open" || values.length === 0) return "every open denial";
  const named = values.join(", ");
  return selector.kind === "facility" ? `denials at ${named}` : `denials from ${named}`;
}

/** The selector Home's still-catchable figure launches: everything open. */
export function allOpenSelector(): ResearchSelector {
  return { kind: "all_open", values: [], label: "every open denial" };
}

/* ------------------------------------------------------------------ */
/* Reading the wire defensively                                        */
/* ------------------------------------------------------------------ */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * A selector, read from an untyped payload.
 *
 * `kind` is the load-bearing field: it is what the run is POSTed back with,
 * and a kind this build does not know is a selector it cannot honour. So an
 * unrecognized one is dropped rather than coerced to `all_open`, which
 * would silently widen the population a reader chose.
 */
const SELECTOR_KINDS: ReadonlySet<string> = new Set([
  "all_open",
  "payer",
  "recovery_class",
  "facility",
]);

export function mapResearchSelector(raw: unknown): ResearchSelector | undefined {
  if (!isRecord(raw)) return undefined;
  const kind = raw.kind;
  if (typeof kind !== "string" || !SELECTOR_KINDS.has(kind)) return undefined;
  const values = Array.isArray(raw.values)
    ? raw.values.filter((v): v is string => typeof v === "string" && v !== "")
    : [];
  return {
    kind: kind as ResearchSelector["kind"],
    values,
    label: typeof raw.label === "string" ? raw.label : "",
  };
}

/** A string the wire may or may not have sent, never `undefined`. */
function words(raw: unknown): string {
  return typeof raw === "string" ? raw : "";
}

/** A whole number the wire may or may not have sent. */
function count(raw: unknown): number {
  return typeof raw === "number" && Number.isFinite(raw) ? raw : 0;
}

/** Every non-empty string in an array the wire may or may not have sent. */
function wordList(raw: unknown): string[] {
  return Array.isArray(raw) ? raw.filter((v): v is string => typeof v === "string" && v !== "") : [];
}

/**
 * How big the population is — or nothing at all.
 *
 * A scope of zero denials would be this client inventing a fact about a
 * run nobody has started, so a payload that did not say is read as "the
 * platform did not say" rather than as a default.
 */
function readScope(raw: unknown): ResearchOfferScope | undefined {
  const scope = isRecord(raw) ? raw : undefined;
  const openDenials = typeof scope?.open_denials === "number" ? scope.open_denials : undefined;
  const openDollars =
    typeof scope?.open_dollars_cents === "number" ? scope.open_dollars_cents : undefined;
  if (openDenials === undefined || openDollars === undefined) return undefined;
  return { openDenials, openDollarsCents: openDollars };
}

/** The standing angles, titled, from a `ResearchPlanPayload`. */
function readAngles(raw: unknown): ResearchOfferAngle[] {
  const node = isRecord(raw) ? raw : undefined;
  if (!Array.isArray(node?.angles)) return [];
  return node.angles
    .filter(isRecord)
    .map((angle) => ({ title: words(angle.title), purpose: words(angle.purpose) }))
    .filter((angle) => angle.title !== "");
}

/**
 * The other populations on offer.
 *
 * A selector this client cannot honour is dropped rather than coerced,
 * exactly as `population` is: an option that would post something other
 * than what its label says is worse than one option fewer.
 */
function readOptions(raw: unknown): ResearchSelector[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map(mapResearchSelector)
    .filter((selector): selector is ResearchSelector => selector !== undefined);
}

/**
 * The offer a lead card or an answer carries.
 *
 * Dropped whole when the selector cannot be read: the offer's honesty is
 * that what the reader taps is exactly what runs, and an offer with no
 * population behind it is a button that would launch something nobody
 * chose.
 *
 * The dry-run fields are read here too, because the same shape is what
 * `POST /v1/deep-research` returns for a `plan_only` request. On the
 * affordance the server puts beside an ANSWER they are simply absent, and
 * the card says what the MODE does until a preview is resolved for it.
 */
export function mapResearchOffer(raw: unknown): ResearchOffer | undefined {
  if (!isRecord(raw)) return undefined;
  const population = mapResearchSelector(raw.population);
  if (population === undefined) return undefined;

  const scope = readScope(raw.scope);
  const angles = readAngles(raw.plan);
  const options = readOptions(raw.options);

  return {
    population,
    label: typeof raw.label === "string" && raw.label !== "" ? raw.label : "Run deep research",
    description: words(raw.description),
    ...(scope !== undefined ? { scope } : {}),
    ...(angles.length > 0 ? { plan: angles } : {}),
    ...(options.length > 0 ? { options } : {}),
  };
}

/* ------------------------------------------------------------------ */
/* The dry run, read                                                   */
/* ------------------------------------------------------------------ */

/**
 * The reading families this build knows.
 *
 * Read exactly as `mapResearchSelector` reads a kind — an unrecognized
 * value is never coerced into a known one — but with the opposite verdict
 * about the record carrying it. A selector's kind is POSTED back, so an
 * unknown one makes the whole selector unusable; a reading's shape is
 * never rendered and never sent anywhere, so an unknown one costs the
 * reading nothing and the title and the reason still reach the reader.
 */
const READING_SHAPES: ReadonlySet<string> = new Set([
  "measure_profile",
  "stratified_rates",
  "contrast",
  "trend",
  "composition",
]);

function readReadings(raw: unknown): ResearchPlannedReading[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter(isRecord)
    .map((reading) => {
      const shape = words(reading.shape);
      return {
        title: words(reading.title),
        reason: words(reading.reason),
        round: count(reading.round),
        chases: words(reading.chases),
        ...(READING_SHAPES.has(shape) ? { shape: shape as ResearchReadingShape } : {}),
      };
    })
    .filter((reading) => reading.title !== "");
}

/**
 * What a run would do about a research question.
 *
 * Dropped whole when there is no question on it: the payload's own reason
 * for existing is to say what a run would do about ONE question, and a
 * block with no question is a section this card would head with nothing.
 */
export function mapGeneralizedPreview(raw: unknown): GeneralizedResearchPreview | undefined {
  if (!isRecord(raw)) return undefined;
  const researchQuestion = words(raw.research_question);
  if (researchQuestion === "") return undefined;

  const pathChoices = (Array.isArray(raw.path_choices) ? raw.path_choices : [])
    .filter(isRecord)
    .map((choice) => ({ subject: words(choice.subject), statement: words(choice.statement) }))
    .filter((choice) => choice.statement !== "");

  const knowledgeConsulted = (Array.isArray(raw.knowledge_consulted) ? raw.knowledge_consulted : [])
    .filter(isRecord)
    .map((note) => ({ title: words(note.title), matchedOn: wordList(note.matched_on) }))
    .filter((note) => note.title !== "");

  const authored = words(raw.authored_by);
  return {
    researchQuestion,
    populationLabel: words(raw.population_label),
    windowLabel: words(raw.window_label),
    pathChoices,
    knowledgeStatement: words(raw.knowledge_statement),
    knowledgeConsulted,
    readings: readReadings(raw.readings),
    rationale: words(raw.rationale),
    // Anything this build does not recognize is `revi`, which is the
    // honest fallback in both directions: it claims no model choice was
    // made, and a card that says so understates rather than overstates.
    authoredBy: authored === "model" ? "model" : "revi",
    roundsPlanned: count(raw.rounds_planned),
    refusal: words(raw.refusal),
  };
}

/** The whole dry run, dropped when there is no population behind it. */
export function mapResearchPreview(raw: unknown): ResearchPreview | undefined {
  if (!isRecord(raw)) return undefined;
  const population = mapResearchSelector(raw.population);
  if (population === undefined) return undefined;
  const scope = readScope(raw.scope);
  const generalized = mapGeneralizedPreview(raw.generalized);
  return {
    population,
    ...(scope !== undefined ? { scope } : {}),
    plan: readAngles(raw.plan),
    options: readOptions(raw.options),
    dataLoadLabel: words(raw.data_load_label),
    ...(generalized !== undefined ? { generalized } : {}),
  };
}

/**
 * The offer a resolved dry run describes.
 *
 * `question` rides along because it is what the LAUNCH must post: the run
 * a reader confirms has to be the run they just read about, and a launch
 * that dropped the question would start the standing recoverability
 * review under a card describing something else entirely.
 */
export function offerFromPreview(preview: ResearchPreview, question?: string): ResearchOffer {
  return {
    population: preview.population,
    label: "Run deep research",
    description: "",
    ...(preview.scope !== undefined ? { scope: preview.scope } : {}),
    ...(preview.plan.length > 0 ? { plan: preview.plan } : {}),
    ...(preview.options.length > 0 ? { options: preview.options } : {}),
    ...(question !== undefined && question !== "" ? { question } : {}),
    ...(preview.generalized !== undefined ? { generalized: preview.generalized } : {}),
  };
}

/**
 * An offer the server already made, filled in by the dry run resolved for
 * it.
 *
 * The POPULATION, the LABEL and the DESCRIPTION stay the server's own:
 * the offer is what the reader was shown beside an answer, and the
 * preview's job is to say what that run would look at — not to rename it.
 */
export function offerWithPreview(
  offer: ResearchOffer,
  preview: ResearchPreview,
  question?: string,
): ResearchOffer {
  return {
    ...offerFromPreview(preview, question),
    population: offer.population,
    label: offer.label,
    description: offer.description,
  };
}

/**
 * Every status a run response can carry — INCLUDING `preview`.
 *
 * A `plan_only` request answers 200 with a whole run response whose status
 * is `preview`, and while this set omitted it the dry run was rejected as
 * contract drift and read as `null`: the one response the confirmation
 * card exists to render was the one response this parser threw away.
 */
const RUN_STATUSES: ReadonlySet<string> = new Set([
  "preview",
  "planning",
  "running",
  "complete",
  "failed",
  "interrupted",
  // A run somebody stopped. Distinct from `interrupted` on the wire
  // because it is distinct in fact, and a client that folded the two
  // together would have to choose which of the two sentences to tell every
  // reader — the one about their own decision, or the one about ours.
  "cancelled",
]);

/** Every top-level field the run surface cannot render without. */
export const REQUIRED_RESEARCH_RUN_FIELDS = ["id", "status", "session_id"] as const;

export interface ResearchRunParse {
  value: ResearchRun | null;
  drift: string[];
}

/**
 * `GET /v1/deep-research/{run_id}`, read at the seam.
 *
 * The REPORT is passed through as the server composed it rather than
 * re-mapped field by field. That is a deliberate exception to this
 * codebase's usual mapper discipline and it is safe for one reason: the
 * report is a leaf artifact — nothing merges it, nothing re-derives from
 * it, and every renderer below reads it through the accessors at the top of
 * this file, which are total. A twenty-five-schema hand mapper would be
 * twenty-five more places for a rate to be invented.
 */
export function parseResearchRun(raw: unknown): ResearchRunParse {
  const drift: string[] = [];
  if (!isRecord(raw)) return { value: null, drift: [...REQUIRED_RESEARCH_RUN_FIELDS] };
  for (const field of REQUIRED_RESEARCH_RUN_FIELDS) {
    if (raw[field] === undefined || raw[field] === null) drift.push(field);
  }
  const status = raw.status;
  if (typeof status !== "string" || !RUN_STATUSES.has(status)) {
    if (!drift.includes("status")) drift.push("status");
  }
  const population = mapResearchSelector(raw.population);
  if (population === undefined) drift.push("population");
  if (drift.length > 0) return { value: null, drift };

  const progress = isRecord(raw.progress) ? (raw.progress as unknown as ResearchProgress) : undefined;
  return {
    value: {
      id: String(raw.id),
      session_id: String(raw.session_id),
      status: status as ResearchStatus,
      created_at: typeof raw.created_at === "string" ? raw.created_at : "",
      data_load_label: typeof raw.data_load_label === "string" ? raw.data_load_label : "",
      research_question:
        typeof raw.research_question === "string" ? raw.research_question : "",
      population: population as ResearchSelector,
      progress: progress ?? {
        phase: "plan",
        angle_index: 0,
        angle_total: 0,
        message: "",
        elapsed_ms: 0,
        round_index: 0,
        round_total: 0,
      },
      ...(isRecord(raw.report) ? { report: raw.report as unknown as ResearchReport } : {}),
      ...(isResearchStudy(raw.research_report)
        ? { research_report: raw.research_report }
        : {}),
      ...(raw.report_kind === "recovery" || raw.report_kind === "generalized"
        ? { report_kind: raw.report_kind }
        : {}),
      ...(isRecord(raw.preview)
        ? { preview: raw.preview as unknown as ResearchRun["preview"] }
        : {}),
      ...(typeof raw.error === "string" && raw.error !== "" ? { error: raw.error } : {}),
    },
    drift: [],
  };
}

export interface ResearchPreviewParse {
  value: ResearchPreview | null;
  drift: string[];
}

/**
 * `POST /v1/deep-research` with `plan_only: true`, read at the seam.
 *
 * The response IS a run response — same envelope, empty id, status
 * `preview` — so it goes through `parseResearchRun` first and any drift on
 * the envelope is reported under the same names every other read uses.
 * The preview itself is then mapped field by field, because unlike the
 * report it is not a leaf: a surface renders a radio group from `options`
 * and POSTs the result back.
 */
export function parseResearchPreview(raw: unknown): ResearchPreviewParse {
  const { drift } = parseResearchRun(raw);
  const preview = isRecord(raw) ? mapResearchPreview(raw.preview) : undefined;
  if (preview === undefined) {
    return { value: null, drift: [...drift, "preview"] };
  }
  return { value: preview, drift };
}

/** `GET /v1/deep-research` — this tenant's runs, newest first. */
export function parseResearchList(raw: unknown): ResearchSummary[] {
  if (!isRecord(raw) || !Array.isArray(raw.runs)) return [];
  const out: ResearchSummary[] = [];
  for (const entry of raw.runs) {
    if (!isRecord(entry)) continue;
    const population = mapResearchSelector(entry.population);
    if (
      typeof entry.id !== "string" ||
      typeof entry.session_id !== "string" ||
      typeof entry.status !== "string" ||
      !RUN_STATUSES.has(entry.status) ||
      population === undefined
    ) {
      continue;
    }
    out.push({
      id: entry.id,
      session_id: entry.session_id,
      status: entry.status as ResearchStatus,
      created_at: typeof entry.created_at === "string" ? entry.created_at : "",
      data_load_label: typeof entry.data_load_label === "string" ? entry.data_load_label : "",
      research_question:
        typeof entry.research_question === "string" ? entry.research_question : "",
      population,
      ...(typeof entry.total_expected_cents === "number"
        ? { total_expected_cents: entry.total_expected_cents }
        : {}),
    });
  }
  return out;
}

/* ------------------------------------------------------------------ */
/* The progress stream, reduced                                        */
/* ------------------------------------------------------------------ */

/**
 * What one watcher knows about a run: the run itself, the plan once it
 * exists, and the write-up as it is composed.
 *
 * The narrative is accumulated from `narrative_delta` and then REPLACED by
 * the finished report's own — the composer is entitled to rewrite what it
 * streamed, and welding a rewrite onto a draft is the defect the answer
 * path already learned about (see `turnResponseToEvents`).
 */
export interface ResearchWatchState {
  run: ResearchRun;
  /**
   * The angles this run will look at, once the plan frame has named them.
   *
   * Held beside the run rather than inside it because a RUNNING run has no
   * report for a plan to live on, and the progress view needs the angle
   * list to tick off.
   */
  plan?: ResearchPlan;
  /**
   * A STUDY's readings, once the run has chosen them.
   *
   * The generalized twin of `plan`, and it arrives on its own frame the
   * moment the readings are known rather than with the finished report —
   * so a reader watching a minute of work sees what is being read while
   * it is being read, with the reason each one is in the run.
   */
  readings?: ResearchPlannedReading[];
  /** Streamed prose, before the finished report supersedes it. */
  draftNarrative: string;
  /** Warnings as they were raised, for a reader watching the run. */
  warnings: ResearchWarning[];
}

export function initialWatchState(run: ResearchRun): ResearchWatchState {
  const study = run.research_report;
  return {
    run,
    ...(run.report ? { plan: run.report.plan } : {}),
    ...(study
      ? {
          readings: (study.readings ?? []).map((reading) => ({
            title: reading.title,
            reason: reading.reason,
            round: reading.round ?? 0,
            chases: reading.chases ?? "",
            ...(READING_SHAPES.has(reading.shape) ? { shape: reading.shape } : {}),
          })),
        }
      : {}),
    draftNarrative: "",
    warnings: run.report?.warnings ?? study?.warnings ?? [],
  };
}

/**
 * One `event:` + `data:` frame, applied.
 *
 * Every branch is additive and none of them invents a status: a run is
 * `complete` because `research_complete` arrived carrying the report, and
 * `failed` because `error` did. A watcher that inferred completion from a
 * closed stream would publish a report nobody finished.
 */
export function applyResearchFrame(
  state: ResearchWatchState,
  frame: { kind: string; data: Record<string, unknown> },
): ResearchWatchState {
  switch (frame.kind) {
    case "research_started": {
      const population = mapResearchSelector(frame.data.population);
      return {
        ...state,
        run: {
          ...state.run,
          ...(population ? { population } : {}),
          ...(typeof frame.data.data_load === "string"
            ? { data_load_label: frame.data.data_load }
            : {}),
          ...(typeof frame.data.research_question === "string"
            ? { research_question: frame.data.research_question }
            : {}),
        },
      };
    }
    case "research_plan": {
      if (typeof frame.data.research_question !== "string") return state;
      return { ...state, plan: frame.data as unknown as ResearchPlan };
    }
    case "research_readings": {
      const readings = readReadings(frame.data.readings);
      if (readings.length === 0) return state;
      return { ...state, readings };
    }
    case "research_progress": {
      if (!isRecord(frame.data)) return state;
      return {
        ...state,
        run: {
          ...state.run,
          status: state.run.status === "planning" ? "running" : state.run.status,
          progress: frame.data as unknown as ResearchProgress,
        },
      };
    }
    case "research_warning": {
      if (!isRecord(frame.data) || typeof frame.data.code !== "string") return state;
      const warning = frame.data as unknown as ResearchWarning;
      if (state.warnings.some((w) => w.code === warning.code && w.message === warning.message)) {
        return state;
      }
      return { ...state, warnings: [...state.warnings, warning] };
    }
    case "narrative_delta": {
      const delta = frame.data.delta;
      if (typeof delta !== "string") return state;
      return { ...state, draftNarrative: state.draftNarrative + delta };
    }
    case "research_complete": {
      if (typeof frame.data.id !== "string") return state;
      // ONE FRAME, TWO ARTIFACTS. The payload's own `kind` says which it
      // is; a watcher that assumed the review's shape would read a study's
      // determination as a missing headline and render an empty report.
      if (isResearchStudy(frame.data)) {
        const study = frame.data;
        return {
          ...state,
          draftNarrative: "",
          warnings: study.warnings ?? state.warnings,
          run: {
            ...state.run,
            status: "complete",
            research_report: study,
            report_kind: "generalized",
          },
        };
      }
      const report = frame.data as unknown as ResearchReport;
      return {
        ...state,
        plan: report.plan,
        draftNarrative: "",
        warnings: report.warnings ?? state.warnings,
        run: {
          ...state.run,
          status: "complete",
          report,
          report_kind: "recovery",
        },
      };
    }
    case "research_cancelled": {
      // THE RUN WAS STOPPED, AND THAT IS NOT AN ERROR FRAME. It arrives on
      // its own kind for exactly that reason: a watcher in another tab
      // learns that somebody ended the run, and must not be shown a
      // failure for a thing that worked.
      const message = frame.data.message;
      return {
        ...state,
        draftNarrative: "",
        run: {
          ...state.run,
          status: "cancelled",
          error:
            typeof message === "string" && message !== ""
              ? message
              : "This run was stopped before it finished, so nothing was published.",
        },
      };
    }
    case "error": {
      const message = frame.data.message;
      return {
        ...state,
        run: {
          ...state.run,
          status: "failed",
          error:
            typeof message === "string" && message !== ""
              ? message
              : "This run stopped before it could finish.",
        },
      };
    }
    default:
      return state;
  }
}

/**
 * The angles a run is working through, wherever they have got to.
 *
 * The plan arrives on its own frame AFTER the angles have run (the server
 * emits it with the finished report), so a live progress view has the
 * COUNT from `angle_total` long before it has the names. Both are honest
 * inputs and the view uses whichever it holds: names when they exist, the
 * count when they do not — never a placeholder standing in for a name the
 * platform has not sent.
 */
export function angleTitles(state: ResearchWatchState): AngleGroup[] {
  // A STUDY NAMES ITS OWN READINGS, and it names them EARLY: the readings
  // frame arrives as soon as the run has chosen them rather than with the
  // finished report, so the checklist below is real from the first
  // measurement rather than filling in at the end.
  const readings = state.readings ?? [];
  if (readings.length > 0) {
    return readings.map((reading, index) => ({
      title: reading.title,
      cuts: 1,
      lastIndex: index,
      ...(reading.reason !== "" ? { reason: reading.reason } : {}),
      round: reading.round,
    }));
  }
  const angles: ResearchAngle[] = state.plan?.angles ?? state.run.report?.plan.angles ?? [];
  const groups: AngleGroup[] = [];
  for (const [index, angle] of angles.entries()) {
    const last = groups[groups.length - 1];
    // ADJACENT ANGLES THAT SHARE A TITLE ARE ONE LINE, WITH A COUNT.
    //
    // The standing plan runs "Recovery rates by population" three times —
    // once cut by payer, once by denial type, once by denied amount — and
    // the pack titles all three identically. Drawn as three consecutive
    // identical rows, a checklist reads as a rendering fault. The cuts
    // themselves are the wire's raw stratum handles, which get no client
    // rendering, so the honest disambiguation is the COUNT: three cuts of
    // one question, said once.
    if (last !== undefined && last.title === angle.title) {
      last.cuts += 1;
      last.lastIndex = index;
      continue;
    }
    groups.push({ title: angle.title, cuts: 1, lastIndex: index });
  }
  return groups;
}

/** One line of the "what it is looking at" list. */
export interface AngleGroup {
  title: string;
  /** How many adjacent angles share this title (three cuts of one question). */
  cuts: number;
  /** Position of the LAST of them, so the line ticks when all of them have. */
  lastIndex: number;
  /** Why this reading is in the run, where the platform said. */
  reason?: string;
  /** Which round chose it — 0 is the opening read. */
  round?: number;
}
