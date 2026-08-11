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
 * A place another surface can offer to start a run from — and, WHEN THE
 * WIRE CARRIES IT, what that run proposes to do.
 *
 * `DeepResearchAffordance` is the published shape today: a label, a
 * sentence, and the closed selector the run would be over. A run is a
 * minute of work and a real model call, so the surface that offers one
 * confirms intent before spending it — and a confirmation is only worth
 * reading if it says what the run will actually look at.
 *
 * THE THREE FIELDS BELOW ARE THE SEAM FOR THAT, and every one of them is
 * absent today. They are read defensively (`mapResearchOffer`) and the
 * card degrades to what the current payload supports, so the backend can
 * land them additively with no client change beyond deleting this
 * paragraph. The expected wire shape, in the vocabulary the mode's other
 * schemas already use:
 *
 *   `deep_research.scope` → `{ open_denials: int, open_dollars_cents:
 *     int }`. The size of the population, so "this will analyze denials
 *     from Atlas Commercial" becomes "…565 of them, worth $1,153,302.17".
 *     Named after `ExpectedRecoveryRowPayload`'s own fields, because it is
 *     the same two quantities measured over the same population.
 *   `deep_research.plan` → `{ angles: ResearchAnglePayload[] }`, i.e. the
 *     standing plan resolved for THIS population without running it — the
 *     dry-run the report already publishes as `ResearchPlanPayload`. Only
 *     `title` and `purpose` are read here.
 *   `deep_research.options` → `DeepResearchSelector[]`, the other
 *     populations this offer could run over (the payer alone, the payer
 *     within this facility, every open denial). Each is a CLOSED selector
 *     exactly like `population`, so choosing one changes what is posted
 *     and nothing else — no sentence is re-parsed and no scope is widened
 *     by the client.
 *
 * Until they arrive the card states what the MODE does — which is true of
 * every run and is not a guess about this one — and posts `population`.
 */
type ResearchOfferWire = components["schemas"]["DeepResearchAffordance"];

/** How big the population is, when the offer says. */
export interface ResearchOfferScope {
  openDenials: number;
  openDollarsCents: number;
}

/** One line of the proposed plan: what the run will look at, and why. */
export interface ResearchOfferAngle {
  title: string;
  purpose: string;
}

export interface ResearchOffer extends ResearchOfferWire {
  scope?: ResearchOfferScope;
  /** The angles this run proposes, resolved for this population. */
  plan?: ResearchOfferAngle[];
  /** Other populations this offer could run over, as closed selectors. */
  options?: ResearchSelector[];
}
export type ResearchRun = components["schemas"]["DeepResearchRunResponse"];
export type ResearchReport = components["schemas"]["DeepResearchReport"];
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

/** `planning | running | complete | failed | interrupted`. */
export type ResearchStatus = ResearchRun["status"];
/** `plan | execute | synthesize`. */
export type ResearchPhaseId = ResearchProgress["phase"];

/** A run that has not finished is still moving; everything else is settled. */
export function isRunning(status: ResearchStatus): boolean {
  return status === "planning" || status === "running";
}

/* ------------------------------------------------------------------ */
/* The phases, in words a reader owns                                  */
/* ------------------------------------------------------------------ */

/**
 * THE THREE THINGS A RUN DOES, said the way somebody waiting would say
 * them.
 *
 * The wire's `phase` is `plan | execute | synthesize` — accurate, and three
 * words from a compiler. What the reader is owed is what is happening to
 * THEIR data: it is being read, then measured, then written up. The
 * server's own `message` ("Comparing payers", "Checking filing deadlines")
 * rides underneath as the detail, so nothing here stands in for a sentence
 * the platform already wrote.
 *
 * `note` is on the last one because it is the honest account of where the
 * minute goes: seven angles measure in under twenty milliseconds each and
 * the write-up is a model call. A progress view that implies the measuring
 * is the slow part teaches a reader to expect the wrong thing.
 */
export interface ResearchPhaseModel {
  id: ResearchPhaseId;
  label: string;
  note?: string;
}

export const RESEARCH_PHASES: readonly ResearchPhaseModel[] = [
  { id: "plan", label: "Reading your data" },
  { id: "execute", label: "Running the analysis" },
  {
    id: "synthesize",
    label: "Writing it up",
    note: "Most of the minute goes here. The measuring is quick; the writing is not.",
  },
];

/** Where a phase sits against the one the run is in: done, now, or ahead. */
export function phaseState(
  phase: ResearchPhaseId,
  current: ResearchPhaseId,
  status: ResearchStatus,
): "done" | "active" | "pending" {
  const at = RESEARCH_PHASES.findIndex((p) => p.id === current);
  const index = RESEARCH_PHASES.findIndex((p) => p.id === phase);
  if (!isRunning(status)) return status === "complete" ? "done" : index <= at ? "done" : "pending";
  if (index < at) return "done";
  if (index === at) return "active";
  return "pending";
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

/**
 * The offer a lead card or an answer carries.
 *
 * Dropped whole when the selector cannot be read: the offer's honesty is
 * that what the reader taps is exactly what runs, and an offer with no
 * population behind it is a button that would launch something nobody
 * chose.
 */
export function mapResearchOffer(raw: unknown): ResearchOffer | undefined {
  if (!isRecord(raw)) return undefined;
  const population = mapResearchSelector(raw.population);
  if (population === undefined) return undefined;

  /* THE PLAN-PREVIEW SEAM. See `ResearchOffer` above for the wire shape
     each of these expects. All three are absent on today's payload and
     every one of them is read as "the platform did not say" rather than
     as a default — a scope of zero denials, an empty plan or a synthesized
     option list would each be this client inventing a fact about a run
     that has not started. */
  const scope = isRecord(raw.scope) ? raw.scope : undefined;
  const openDenials = typeof scope?.open_denials === "number" ? scope.open_denials : undefined;
  const openDollars =
    typeof scope?.open_dollars_cents === "number" ? scope.open_dollars_cents : undefined;

  const planNode = isRecord(raw.plan) ? raw.plan : undefined;
  const angles = Array.isArray(planNode?.angles)
    ? planNode.angles
        .filter(isRecord)
        .map((angle) => ({
          title: typeof angle.title === "string" ? angle.title : "",
          purpose: typeof angle.purpose === "string" ? angle.purpose : "",
        }))
        .filter((angle) => angle.title !== "")
    : [];

  // A selector this client cannot honour is dropped rather than coerced,
  // exactly as `population` is: an option that would post something other
  // than what its label says is worse than one option fewer.
  const options = Array.isArray(raw.options)
    ? raw.options
        .map(mapResearchSelector)
        .filter((selector): selector is ResearchSelector => selector !== undefined)
    : [];

  return {
    population,
    label: typeof raw.label === "string" && raw.label !== "" ? raw.label : "Run deep research",
    description: typeof raw.description === "string" ? raw.description : "",
    ...(openDenials !== undefined && openDollars !== undefined
      ? { scope: { openDenials, openDollarsCents: openDollars } }
      : {}),
    ...(angles.length > 0 ? { plan: angles } : {}),
    ...(options.length > 0 ? { options } : {}),
  };
}

const RUN_STATUSES: ReadonlySet<string> = new Set([
  "planning",
  "running",
  "complete",
  "failed",
  "interrupted",
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
      population: population as ResearchSelector,
      progress: progress ?? {
        phase: "plan",
        angle_index: 0,
        angle_total: 0,
        message: "",
        elapsed_ms: 0,
      },
      ...(isRecord(raw.report) ? { report: raw.report as unknown as ResearchReport } : {}),
      ...(typeof raw.error === "string" && raw.error !== "" ? { error: raw.error } : {}),
    },
    drift: [],
  };
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
  /** Streamed prose, before the finished report supersedes it. */
  draftNarrative: string;
  /** Warnings as they were raised, for a reader watching the run. */
  warnings: ResearchWarning[];
}

export function initialWatchState(run: ResearchRun): ResearchWatchState {
  return {
    run,
    ...(run.report ? { plan: run.report.plan } : {}),
    draftNarrative: "",
    warnings: run.report?.warnings ?? [],
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
        },
      };
    }
    case "research_plan": {
      if (typeof frame.data.research_question !== "string") return state;
      return { ...state, plan: frame.data as unknown as ResearchPlan };
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
      const report = frame.data as unknown as ResearchReport;
      return {
        ...state,
        plan: report.plan,
        draftNarrative: "",
        warnings: report.warnings ?? state.warnings,
        run: { ...state.run, status: "complete", report },
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
}
