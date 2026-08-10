/**
 * MONITORS — the wire shapes of the proactive surface, read into the shapes
 * the surface renders.
 *
 * Monitors is the one screen in this product with no asker in the room. An
 * answer is read by the person who asked the question, three seconds after
 * they asked it, with the context line they chose still on screen. A tile
 * is read at 07:50 by somebody who has not typed anything, and a brief
 * entry is read over coffee by somebody who may never open the
 * investigation behind it. Everything in this module follows from that:
 *
 * **A tile without its integrity atom is not parsed.** `MonitorsTileIntegrity`
 * is the answer-level grade, the count of things to know, the caveats
 * behind that count and the bound/provisional marks. On an answer those
 * marks are one scroll from the number; on a tile they are the only thing
 * standing between a suppressed ceiling and a figure somebody quotes in a
 * board pack. A tile payload missing `integrity` is reported as contract
 * drift and DROPPED — rendering the value without its marks is the exact
 * failure the atom exists to prevent, and a blank slot in a grid is a
 * smaller lie than a confident number.
 *
 * **Prose is passed through, never composed here.** `statement`,
 * `materiality_note`, `verification_note`, `window_note`, the
 * time-to-impact `method`, the fatigue `message` and the brief `headline`
 * are the platform's own sentences, built from the payload server-side.
 * This module renames fields. It does not write any.
 *
 * **Absence is read as absence.** A delta the server did not publish is
 * `undefined`, never a zero; a `time_to_impact` with `kind: "unknown"`
 * carries its reason and is rendered as such rather than hidden.
 */

import {
  asArray,
  asNumber,
  asString,
  isRecord,
  mapFinding,
  mapTimeToImpact,
  mapMonitorModel,
  readTurnWarnings,
  type LeadStatus,
  type TimeToImpact,
  type MonitorModel,
} from "@/lib/contract";
import { GRADE_STRENGTH, type EvidenceGrade, type Finding, type WarningEvent } from "@/lib/types";

/* ------------------------------------------------------------------ */
/* Shared vocabulary                                                    */
/* ------------------------------------------------------------------ */

/** `MonitorsPresentation` — how the analyst chose to see this monitor. */
export type MonitorsPresentation = "chart" | "finding" | "worklist_slice" | "scalar";

/** `MonitorsWindowMode` — what re-running this monitor's window means. */
export type MonitorsWindowMode = "relative" | "absolute" | "anchored";

/**
 * Four Monitors shapes are NOT defined here, and the split is the backend's
 * own: `MonitorModel`, `MonitorDeclarationPayload`, `TimeToImpactPayload`
 * and `lead_status` live in `revi_investigation_contracts.api` rather than
 * in its `monitors` module, because a turn answer and a portfolio card carry
 * them too — "one definition beats two that must agree". The client mirrors
 * that exactly: they live in `lib/contract.ts`, on the path that reads
 * `TurnAnswer` and `AnomalyCard`, and are re-exported here so a Monitors
 * component still imports one module.
 */
export type {
  LeadStatus,
  TimeToImpact,
  MonitorDeclaration,
  MonitorModel,
  MonitorMode,
  MonitorRefusal,
  MonitorUnit,
} from "@/lib/contract";
export {
  HUMAN_LEAD_STATUSES,
  MONITOR_NOT_CREATED,
  mapTimeToImpact,
  mapMonitorDeclaration,
  mapMonitorRefusal,
  mapMonitorModel,
  readMonitorRefusal,
  monitorToWire,
} from "@/lib/contract";

const LEAD_STATUSES: ReadonlySet<string> = new Set<LeadStatus>([
  "open",
  "acknowledged",
  "working",
  "resolved_claimed",
  "resolved_confirmed",
  "regressed",
]);

const GRADES: ReadonlySet<string> = new Set(Object.keys(GRADE_STRENGTH));

function asGrade(value: unknown): EvidenceGrade | undefined {
  return typeof value === "string" && GRADES.has(value) ? (value as EvidenceGrade) : undefined;
}

function asBool(value: unknown): boolean {
  return value === true;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value !== "" ? value : undefined;
}

function asStringList(value: unknown): string[] {
  return asArray(value).filter((entry): entry is string => typeof entry === "string" && entry !== "");
}

/* ------------------------------------------------------------------ */
/* The integrity atom                                                   */
/* ------------------------------------------------------------------ */

/**
 * `MonitorsTileIntegrity` — the M22 integrity line as a payload.
 *
 * Every field is a count of something the tile also carries, so the line
 * a renderer draws from it states facts rather than inventing a score.
 * There is no partial form of this object: it arrives whole or the thing
 * carrying it is not drawn.
 */
export interface TileIntegrity {
  /** The weakest grade any finding on the tile carries. */
  grade: EvidenceGrade;
  /** How many caveats the tile publishes — exactly `caveatCodes.length`. */
  thingsToKnow: number;
  /** How many of them change how a number here should be READ. */
  thingsToKnowCaution: number;
  /** The stable codes behind the count. A client branches on these. */
  caveatCodes: string[];
  /** Probes this evaluation executed. Zero is a real answer (a cached re-run). */
  checks: number;
  /** The headline value is an upper BOUND, not a measurement. */
  isBound: boolean;
  /** The headline value is not yet settled. */
  provisional: boolean;
}

/**
 * Read the atom, or say why there is none.
 *
 * `null` means the payload did not carry one. Every caller treats that as
 * drift rather than as an empty integrity object: a zeroed atom would
 * render "Verified · 0 things to know" over a number nobody checked, which
 * is worse than the missing field it came from.
 */
export function mapTileIntegrity(raw: unknown): TileIntegrity | null {
  if (!isRecord(raw)) return null;
  const grade = asGrade(raw.grade);
  if (grade === undefined) return null;
  const codes = asStringList(raw.caveat_codes);
  return {
    grade,
    thingsToKnow: asNumber(raw.things_to_know) ?? codes.length,
    thingsToKnowCaution: asNumber(raw.things_to_know_caution) ?? 0,
    caveatCodes: codes,
    checks: asNumber(raw.checks) ?? 0,
    isBound: asBool(raw.is_bound),
    provisional: asBool(raw.provisional),
  };
}

/* ------------------------------------------------------------------ */
/* Deltas                                                              */
/* ------------------------------------------------------------------ */

/**
 * `MonitorsDeltaPayload` — this tile's movement, in the metric's own unit.
 *
 * `deltaText` is the rendered magnitude ("3.6 points", "$4,201.00") and is
 * what a surface prints. The raw `delta` is kept for the arrow's direction
 * only: re-deriving "points" from a ratio here is how a rate's movement
 * turns into a percentage nobody can tell from a relative change.
 */
export interface MonitorsDelta {
  priorWatermarkId: string;
  priorValue?: number;
  priorValueText: string;
  value?: number;
  valueText: string;
  unit?: string;
  delta?: number;
  /** Unsigned magnitude in the contract's unit, rendered by the server. */
  deltaText: string;
  /** Signed fraction of the prior value. Absent for rates — points are honest. */
  deltaFraction?: number;
  direction: "up" | "down" | "flat" | "unknown";
  /** False when the two loads are not two measurements of one thing. */
  comparable: boolean;
  notComparableReason?: string;
  /** Measured from the prior load, or from the monitor's creation baseline. */
  reference: "prior_load" | "baseline";
  /**
   * WHICH CELL each side measured, in the reader's words.
   *
   * A monitor over a ranked breakdown headlines whatever ranks first at that
   * load, so two loads can be two measurements of two different payers —
   * and a percentage between them looks exactly like a movement. When
   * these disagree the server publishes no delta and says why; the client
   * renders the pair so the reason is checkable rather than trusted.
   */
  subjectLabel?: string;
  priorSubjectLabel?: string;
  /**
   * Both loads resolved to the SAME dates. The number is right either way;
   * what changes is what it means — a same-window change is late-arriving
   * data settling, not a movement in the business.
   */
  sameWindow: boolean;
  material: boolean;
  /** Whose threshold decided: the pack's, or the analyst's own. */
  thresholdSource: "governed" | "monitor";
  /** The analyst's threshold briefed what the governed gate calls normal. */
  belowGovernedGate: boolean;
  materialityRule: string;
  materialityNote: string;
}

const DIRECTIONS: ReadonlySet<string> = new Set(["up", "down", "flat", "unknown"]);

export function mapMonitorsDelta(raw: unknown): MonitorsDelta | undefined {
  if (!isRecord(raw)) return undefined;
  const direction = typeof raw.direction === "string" && DIRECTIONS.has(raw.direction)
    ? (raw.direction as MonitorsDelta["direction"])
    : "unknown";
  return {
    priorWatermarkId: asString(raw.prior_watermark_id),
    ...(asNumber(raw.prior_value) !== undefined ? { priorValue: asNumber(raw.prior_value) } : {}),
    priorValueText: asString(raw.prior_value_text),
    ...(asNumber(raw.value) !== undefined ? { value: asNumber(raw.value) } : {}),
    valueText: asString(raw.value_text),
    ...(optionalString(raw.unit) !== undefined ? { unit: optionalString(raw.unit) } : {}),
    ...(asNumber(raw.delta) !== undefined ? { delta: asNumber(raw.delta) } : {}),
    deltaText: asString(raw.delta_text),
    ...(asNumber(raw.delta_fraction) !== undefined
      ? { deltaFraction: asNumber(raw.delta_fraction) }
      : {}),
    direction,
    comparable: raw.comparable !== false,
    ...(optionalString(raw.not_comparable_reason) !== undefined
      ? { notComparableReason: optionalString(raw.not_comparable_reason) }
      : {}),
    reference: raw.reference === "baseline" ? "baseline" : "prior_load",
    ...(optionalString(raw.subject_label) !== undefined
      ? { subjectLabel: optionalString(raw.subject_label) }
      : {}),
    ...(optionalString(raw.prior_subject_label) !== undefined
      ? { priorSubjectLabel: optionalString(raw.prior_subject_label) }
      : {}),
    sameWindow: asBool(raw.same_window),
    material: asBool(raw.material),
    thresholdSource: raw.threshold_source === "monitor" ? "monitor" : "governed",
    belowGovernedGate: asBool(raw.below_governed_gate),
    materialityRule: asString(raw.materiality_rule),
    materialityNote: asString(raw.materiality_note),
  };
}

/* ------------------------------------------------------------------ */
/* Monitors and pins                                                     */
/* ------------------------------------------------------------------ */

/** `MonitorsPinPayload` — one pinned spec, as stored. */
export interface MonitorsPin {
  pinId: string;
  label: string;
  presentation: MonitorsPresentation;
  /** The typed spec, carried verbatim: it is what re-runs every load. */
  spec: Record<string, unknown>;
  windowMode: MonitorsWindowMode;
  /** What re-running THIS window means, in the server's one sentence. */
  windowNote: string;
  /**
   * The stored spec in the reader's own nouns — "Denial rate, broken down
   * by payer, filtered to Pinnacle Health Plan — last full month (service
   * basis)". The panel headed "What this monitor measures" is the one
   * control that lets somebody catch a monitor measuring the wrong cell, and
   * it was rendering the window note alone while this rode on the wire.
   */
  specSummary: string;
  /** What happened to the request at creation — a narrowing, a duplicate. */
  notes: string[];
  /** This create returned a monitor that already existed rather than a second one. */
  alreadyExisted: boolean;
  createdFromKind: "artifact" | "intent" | "spec";
  createdFromInvestigationId?: string;
  createdFromReferent?: string;
  monitor?: MonitorModel;
  baselineWatermarkId?: string;
  baselineValueText: string;
  createdAt: string;
}

const PRESENTATIONS: ReadonlySet<string> = new Set([
  "chart",
  "finding",
  "worklist_slice",
  "scalar",
]);
const WINDOW_MODES: ReadonlySet<string> = new Set(["relative", "absolute", "anchored"]);

export function mapMonitorsPin(raw: unknown): MonitorsPin | null {
  if (!isRecord(raw)) return null;
  const pinId = optionalString(raw.pin_id);
  if (pinId === undefined) return null;
  const monitor = mapMonitorModel(raw.monitor);
  return {
    pinId,
    label: asString(raw.label),
    presentation: typeof raw.presentation === "string" && PRESENTATIONS.has(raw.presentation)
      ? (raw.presentation as MonitorsPresentation)
      : "finding",
    spec: isRecord(raw.spec) ? raw.spec : {},
    windowMode: typeof raw.window_mode === "string" && WINDOW_MODES.has(raw.window_mode)
      ? (raw.window_mode as MonitorsWindowMode)
      : "relative",
    windowNote: asString(raw.window_note),
    specSummary: asString(raw.spec_summary),
    notes: asStringList(raw.notes),
    alreadyExisted: asBool(raw.already_existed),
    createdFromKind:
      raw.created_from_kind === "artifact" || raw.created_from_kind === "intent"
        ? raw.created_from_kind
        : "spec",
    ...(optionalString(raw.created_from_investigation_id) !== undefined
      ? { createdFromInvestigationId: optionalString(raw.created_from_investigation_id) }
      : {}),
    ...(optionalString(raw.created_from_referent) !== undefined
      ? { createdFromReferent: optionalString(raw.created_from_referent) }
      : {}),
    ...(monitor ? { monitor } : {}),
    ...(optionalString(raw.baseline_watermark_id) !== undefined
      ? { baselineWatermarkId: optionalString(raw.baseline_watermark_id) }
      : {}),
    baselineValueText: asString(raw.baseline_value_text),
    createdAt: asString(raw.created_at),
  };
}

/* ------------------------------------------------------------------ */
/* Tiles                                                                */
/* ------------------------------------------------------------------ */

/** `MonitorsTilePayload` — one pin, evaluated at one load. */
export interface MonitorsTile {
  pinId: string;
  label: string;
  presentation: MonitorsPresentation;
  /** `ok` answered · `unavailable` the platform refused · `clarification`. */
  status: "ok" | "unavailable" | "clarification";
  watermarkId: string;
  /** The dates this evaluation actually measured, after the window resolved. */
  windowStart?: string;
  windowEnd?: string;
  /** The investigation a tap opens. Every tile IS a real investigation. */
  investigationId?: string;
  headlineTitle: string;
  headlineStatement: string;
  /**
   * WHICH CELL this tile's number is about, as one human phrase
   * ("Pinnacle Health Plan"). Empty for a monitor with no dimension at all.
   *
   * The field that makes "the tile measures the cell that was pinned"
   * checkable by a reader rather than eyeballed against the label — which
   * is exactly the pair that disagreed on the tile that gated round 7.
   */
  headlineSubjectLabel: string;
  /** The headline number, rendered in its contract unit (with any `≤`). */
  valueText: string;
  value?: number;
  unit?: string;
  metricId?: string;
  /** Never optional. A tile without it is not built — see `mapMonitorsTile`. */
  integrity: TileIntegrity;
  warnings: Omit<WarningEvent, "type">[];
  findings: Finding[];
  delta?: MonitorsDelta;
  /** Movement since the monitor's own creation-load baseline. */
  baselineDelta?: MonitorsDelta;
  /** Why the tile has no value, in the platform's error vocabulary. */
  unavailableReason?: string;
}

const TILE_STATUSES: ReadonlySet<string> = new Set(["ok", "unavailable", "clarification"]);

/**
 * One tile, or nothing plus a drift path.
 *
 * THE CONSERVATION RULE OF THIS SURFACE: a tile is dropped when it carries
 * no `integrity`, and the drop is reported. Everything else about a tile
 * degrades gracefully — an unavailable tile renders its refusal, a tile
 * with no delta renders no delta — but a value drawn without its grade and
 * its caveat count is the one output this surface is not allowed to
 * produce, so there is no code path that produces it.
 */
export function mapMonitorsTile(raw: unknown, index: number, drift: string[]): MonitorsTile | null {
  if (!isRecord(raw)) {
    drift.push(`tiles[${index}]`);
    return null;
  }
  const pinId = optionalString(raw.pin_id);
  if (pinId === undefined) {
    drift.push(`tiles[${index}].pin_id`);
    return null;
  }
  const integrity = mapTileIntegrity(raw.integrity);
  if (integrity === null) {
    drift.push(`tiles[${index}].integrity`);
    return null;
  }
  const findings: Finding[] = [];
  for (const entry of asArray(raw.findings)) {
    const finding = mapFinding(entry);
    if (finding !== null) findings.push(finding);
  }
  return {
    pinId,
    label: asString(raw.label),
    presentation: typeof raw.presentation === "string" && PRESENTATIONS.has(raw.presentation)
      ? (raw.presentation as MonitorsPresentation)
      : "finding",
    status: typeof raw.status === "string" && TILE_STATUSES.has(raw.status)
      ? (raw.status as MonitorsTile["status"])
      : "ok",
    watermarkId: asString(raw.watermark_id),
    ...(optionalString(raw.window_start) !== undefined
      ? { windowStart: optionalString(raw.window_start) }
      : {}),
    ...(optionalString(raw.window_end) !== undefined
      ? { windowEnd: optionalString(raw.window_end) }
      : {}),
    ...(optionalString(raw.investigation_id) !== undefined
      ? { investigationId: optionalString(raw.investigation_id) }
      : {}),
    headlineTitle: asString(raw.headline_title),
    headlineStatement: asString(raw.headline_statement),
    headlineSubjectLabel: asString(raw.headline_subject_label),
    valueText: asString(raw.value_text),
    ...(asNumber(raw.value) !== undefined ? { value: asNumber(raw.value) } : {}),
    ...(optionalString(raw.unit) !== undefined ? { unit: optionalString(raw.unit) } : {}),
    ...(optionalString(raw.metric_id) !== undefined
      ? { metricId: optionalString(raw.metric_id) }
      : {}),
    integrity,
    warnings: readTurnWarnings(
      raw.warnings_v2,
      asArray(raw.warnings).filter((w): w is string => typeof w === "string"),
    ),
    findings,
    ...(mapMonitorsDelta(raw.delta) !== undefined ? { delta: mapMonitorsDelta(raw.delta) } : {}),
    ...(mapMonitorsDelta(raw.baseline_delta) !== undefined
      ? { baselineDelta: mapMonitorsDelta(raw.baseline_delta) }
      : {}),
    ...(optionalString(raw.unavailable_reason) !== undefined
      ? { unavailableReason: optionalString(raw.unavailable_reason) }
      : {}),
  };
}

/* ------------------------------------------------------------------ */
/* Ordering the grid                                                    */
/* ------------------------------------------------------------------ */

/**
 * The bands a tile grid is ordered and counted in.
 *
 * Twenty tiles in creation order is a wall: nothing puts the monitors that
 * MOVED first, nothing separates them from the ones that held still, and a
 * monitor that moved below its own threshold appears in neither the brief
 * nor any visible zone. The bands are derived from the server's own
 * `material` flag and the delta it published — what counts as movement is
 * a governed decision, and no part of it is re-derived here.
 */
export const TILE_BANDS = {
  material: 0,
  moved: 1,
  flat: 2,
  noComparison: 3,
  unavailable: 4,
} as const;

export function tileBand(tile: MonitorsTile): number {
  if (tile.status !== "ok") return TILE_BANDS.unavailable;
  const delta = tile.delta;
  if (delta === undefined || !delta.comparable) return TILE_BANDS.noComparison;
  if (delta.material) return TILE_BANDS.material;
  if (delta.direction === "flat" || delta.delta === 0) return TILE_BANDS.flat;
  return TILE_BANDS.moved;
}

/**
 * Moved first, and the rest in an order somebody chose.
 *
 * A STABLE sort: within a band the platform's own order survives, because
 * it is the only sequencing the payload carries and re-ranking it here
 * would be this client inventing a priority.
 */
export function orderTilesForGrid(tiles: readonly MonitorsTile[]): MonitorsTile[] {
  return [...tiles]
    .map((tile, index) => ({ tile, index, band: tileBand(tile) }))
    .sort((a, b) => a.band - b.band || a.index - b.index)
    .map((entry) => entry.tile);
}

/** The grid, counted in the same bands it is ordered by. */
export function tileCensus(tiles: readonly MonitorsTile[]): string[] {
  const counts = new Map<number, number>();
  for (const tile of tiles) {
    const band = tileBand(tile);
    counts.set(band, (counts.get(band) ?? 0) + 1);
  }
  const moved = (counts.get(TILE_BANDS.material) ?? 0) + (counts.get(TILE_BANDS.moved) ?? 0);
  const flat = counts.get(TILE_BANDS.flat) ?? 0;
  const none = counts.get(TILE_BANDS.noComparison) ?? 0;
  const unavailable = counts.get(TILE_BANDS.unavailable) ?? 0;
  const parts: string[] = [];
  if (moved > 0) parts.push(`${moved} moved`);
  if (flat > 0) parts.push(`${flat} unchanged`);
  if (none > 0) parts.push(`${none} with nothing to compare`);
  if (unavailable > 0) parts.push(`${unavailable} the platform could not measure`);
  return parts;
}

/** `MonitorsResponse` — the surface at one load. */
export interface MonitorsData {
  tenant: string;
  watermarkId: string;
  newestDataDate?: string;
  priorWatermarkId?: string;
  tiles: MonitorsTile[];
  warnings: Omit<WarningEvent, "type">[];
}

export interface MonitorsParse {
  value: MonitorsData | null;
  drift: string[];
}

export function parseMonitors(raw: unknown): MonitorsParse {
  const drift: string[] = [];
  if (!isRecord(raw) || !Array.isArray(raw.tiles)) return { value: null, drift: ["tiles"] };
  const tiles: MonitorsTile[] = [];
  raw.tiles.forEach((tile, index) => {
    const mapped = mapMonitorsTile(tile, index, drift);
    if (mapped !== null) tiles.push(mapped);
  });
  return {
    value: {
      tenant: asString(raw.tenant),
      watermarkId: asString(raw.watermark_id),
      ...(optionalString(raw.newest_data_date) !== undefined
        ? { newestDataDate: optionalString(raw.newest_data_date) }
        : {}),
      ...(optionalString(raw.prior_watermark_id) !== undefined
        ? { priorWatermarkId: optionalString(raw.prior_watermark_id) }
        : {}),
      tiles,
      warnings: readTurnWarnings(
        raw.warnings_v2,
        asArray(raw.warnings).filter((w): w is string => typeof w === "string"),
      ),
    },
    drift,
  };
}

/* ------------------------------------------------------------------ */
/* The brief                                                            */
/* ------------------------------------------------------------------ */

/**
 * The things one load can change, as the server names them.
 *
 * `rank_flip` is NOT a movement and never carries a delta: it is the fact
 * that the cell a ranked monitor headlines is a different cell from the one
 * it headlined last load ("State Medicaid MCO overtook Pinnacle as your
 * worst payer"). It is listed here because a client that did not know the
 * kind would have DROPPED the entry — see `mapBriefEntry`, which no longer
 * drops one for that reason.
 */
export type KnownBriefEntryKind =
  | "new_lead"
  | "pin_movement"
  | "self_resolved"
  | "resolution_confirmed"
  | "resolution_regressed"
  | "rank_flip";

/**
 * The kind as published. Widened on purpose: an entry whose kind this
 * build has never seen is a real change at this load, and the one thing a
 * brief may not do is lose it. Unknown kinds render (with the server's own
 * sentence, under a label derived from the id) and are reported as drift.
 */
export type BriefEntryKind = KnownBriefEntryKind | (string & {});

export const KNOWN_ENTRY_KINDS: ReadonlySet<string> = new Set<KnownBriefEntryKind>([
  "new_lead",
  "pin_movement",
  "self_resolved",
  "resolution_confirmed",
  "resolution_regressed",
  "rank_flip",
]);

/** `MonitorsProvenancePayload` — where one line came from. On every entry. */
export interface BriefProvenance {
  source: "detection_feed" | "pinned_spec";
  watermarkId: string;
  priorWatermarkId?: string;
  formulaVersion?: string;
  /** How this entry was decided, in the server's one sentence. */
  method: string;
}

export interface BriefEntry {
  kind: BriefEntryKind;
  title: string;
  /** The line itself, composed server-side from the payload. Never rewritten. */
  statement: string;
  anomalyId?: string;
  pinId?: string;
  /** The permalink. An entry a reader cannot open is a notification. */
  investigationId?: string;
  category?: string;
  lane?: string;
  impactCents?: number;
  timeToImpact?: TimeToImpact;
  delta?: MonitorsDelta;
  baselineDelta?: MonitorsDelta;
  leadStatus?: LeadStatus;
  /** The honesty marks travel onto the brief with the number. */
  integrity?: TileIntegrity;
  provenance: BriefProvenance;
}

/** `MonitorsMaterialityPayload` — the governed gate that was applied. */
export interface BriefMateriality {
  unitKinds: Record<string, Record<string, number>>;
  newLeadMinImpactCents: number;
  alwaysMaterialLanes: string[];
  maxEntries: number;
  /** Content hash of the governed file that produced this brief. */
  contentHash: string;
  source: string;
}

/** `MonitorsImmaterialSummary` — everything the gate held back, counted. */
export interface BriefImmaterial {
  pinMovements: number;
  newLeads: number;
  selfResolved: number;
  entriesWithheldByCap: number;
  /**
   * Monitors with nothing to compare against at the load this brief diffs
   * from — a first reading, or a monitor created after that load.
   *
   * The count that makes the census CLOSE. Live, 18 monitors were evaluated
   * against a brief carrying one movement and one held-back movement; the
   * other sixteen were first readings, and they were neither briefed nor
   * counted — a total that does not reconcile to its parts, on the surface
   * whose whole claim is "withheld visibly, never silently".
   */
  notYetComparable: number;
  /** Monitors whose stored spec could not be answered at this load. */
  unavailable: number;
  /** What the cap dropped, BY KIND — "12 further entries" hides a regression. */
  withheldByKind: Record<string, number>;
  /** The line the brief owes its reader, composed server-side. */
  note: string;
}

/** `MonitorsFatigueAdvisory` — the brief noticing somebody's own gate is loose. */
export interface BriefFatigue {
  active: boolean;
  monitorsBelowGovernedGate: number;
  consecutiveLoads: number;
  loadsRequired: number;
  /** The sentence itself. Empty when inactive. */
  message: string;
}

export interface BriefData {
  tenant: string;
  status: "first_load" | "nothing_material" | "material_changes";
  watermarkId: string;
  newestDataDate?: string;
  priorWatermarkId?: string;
  /**
   * The data date of the load this brief diffs AGAINST.
   *
   * The brief speaks in dates, not in warehouse ids: "since the Aug 1
   * load" is a sentence a VP reads and `wm_002` is one they forward to
   * somebody else to explain. The ids stay in provenance, where an auditor
   * can still reach them.
   */
  priorNewestDataDate?: string;
  /** One sentence, composed from the counts. The thing read before anything else. */
  headline: string;
  entries: BriefEntry[];
  /** How many CLEARED the gate, before the cap. `entries` may be shorter. */
  entriesTotal: number;
  immaterial: BriefImmaterial;
  fatigue: BriefFatigue;
  materiality: BriefMateriality;
  /** The work behind the brief, so "nothing material" is visibly a measurement. */
  pinsEvaluated: number;
  leadsVerified: number;
  generatedAt?: string;
  warnings: Omit<WarningEvent, "type">[];
}

export interface BriefParse {
  value: BriefData | null;
  drift: string[];
}

function mapBriefEntry(raw: unknown, index: number, drift: string[]): BriefEntry | null {
  if (!isRecord(raw)) {
    drift.push(`entries[${index}]`);
    return null;
  }
  // A KIND THIS BUILD DOES NOT KNOW IS STILL A CHANGE AT THIS LOAD.
  //
  // It used to be dropped, which made the client's vocabulary a filter on
  // the server's facts: the load `rank_flip` shipped, every brief carrying
  // one would have rendered without it and the census would not have
  // closed. The drift is still reported — the mismatch is real and worth
  // seeing — but the entry renders, because the statement is the server's
  // and it is the part a reader needs.
  const kind = typeof raw.kind === "string" && raw.kind !== "" ? raw.kind : undefined;
  if (kind === undefined) {
    drift.push(`entries[${index}].kind`);
    return null;
  }
  if (!KNOWN_ENTRY_KINDS.has(kind)) drift.push(`entries[${index}].kind:${kind}`);
  const statement = optionalString(raw.statement);
  if (statement === undefined) {
    // The statement IS the entry. A line with a title and no sentence
    // would be a notification, which is the thing this surface is not.
    drift.push(`entries[${index}].statement`);
    return null;
  }
  const provenanceRaw = isRecord(raw.provenance) ? raw.provenance : undefined;
  if (provenanceRaw === undefined) {
    drift.push(`entries[${index}].provenance`);
    return null;
  }
  const leadStatus = typeof raw.lead_status === "string" && LEAD_STATUSES.has(raw.lead_status)
    ? (raw.lead_status as LeadStatus)
    : undefined;
  return {
    kind,
    title: asString(raw.title),
    statement,
    ...(optionalString(raw.anomaly_id) !== undefined
      ? { anomalyId: optionalString(raw.anomaly_id) }
      : {}),
    ...(optionalString(raw.pin_id) !== undefined ? { pinId: optionalString(raw.pin_id) } : {}),
    ...(optionalString(raw.investigation_id) !== undefined
      ? { investigationId: optionalString(raw.investigation_id) }
      : {}),
    ...(optionalString(raw.category) !== undefined
      ? { category: optionalString(raw.category) }
      : {}),
    ...(optionalString(raw.lane) !== undefined ? { lane: optionalString(raw.lane) } : {}),
    ...(asNumber(raw.impact_cents) !== undefined
      ? { impactCents: asNumber(raw.impact_cents) }
      : {}),
    ...(mapTimeToImpact(raw.time_to_impact) !== undefined
      ? { timeToImpact: mapTimeToImpact(raw.time_to_impact) }
      : {}),
    ...(mapMonitorsDelta(raw.delta) !== undefined ? { delta: mapMonitorsDelta(raw.delta) } : {}),
    ...(mapMonitorsDelta(raw.baseline_delta) !== undefined
      ? { baselineDelta: mapMonitorsDelta(raw.baseline_delta) }
      : {}),
    ...(leadStatus !== undefined ? { leadStatus } : {}),
    ...(mapTileIntegrity(raw.integrity) !== null
      ? { integrity: mapTileIntegrity(raw.integrity) as TileIntegrity }
      : {}),
    provenance: {
      source: provenanceRaw.source === "pinned_spec" ? "pinned_spec" : "detection_feed",
      watermarkId: asString(provenanceRaw.watermark_id),
      ...(optionalString(provenanceRaw.prior_watermark_id) !== undefined
        ? { priorWatermarkId: optionalString(provenanceRaw.prior_watermark_id) }
        : {}),
      ...(optionalString(provenanceRaw.formula_version) !== undefined
        ? { formulaVersion: optionalString(provenanceRaw.formula_version) }
        : {}),
      method: asString(provenanceRaw.method),
    },
  };
}

/** A `{name: count}` map, keeping only the entries that are counts. */
function mapCounts(raw: unknown): Record<string, number> {
  if (!isRecord(raw)) return {};
  const out: Record<string, number> = {};
  for (const [key, value] of Object.entries(raw)) {
    const numeric = asNumber(value);
    if (numeric !== undefined) out[key] = numeric;
  }
  return out;
}

function mapUnitKinds(raw: unknown): Record<string, Record<string, number>> {
  if (!isRecord(raw)) return {};
  const out: Record<string, Record<string, number>> = {};
  for (const [unit, rules] of Object.entries(raw)) {
    if (!isRecord(rules)) continue;
    const mapped: Record<string, number> = {};
    for (const [rule, value] of Object.entries(rules)) {
      const numeric = asNumber(value);
      if (numeric !== undefined) mapped[rule] = numeric;
    }
    out[unit] = mapped;
  }
  return out;
}

export function parseBrief(raw: unknown): BriefParse {
  const drift: string[] = [];
  if (!isRecord(raw)) return { value: null, drift: ["status"] };
  const status =
    raw.status === "first_load" || raw.status === "nothing_material" || raw.status === "material_changes"
      ? raw.status
      : undefined;
  if (status === undefined) return { value: null, drift: ["status"] };
  const entries: BriefEntry[] = [];
  asArray(raw.entries).forEach((entry, index) => {
    const mapped = mapBriefEntry(entry, index, drift);
    if (mapped !== null) entries.push(mapped);
  });
  const immaterialRaw = isRecord(raw.immaterial) ? raw.immaterial : {};
  const fatigueRaw = isRecord(raw.fatigue) ? raw.fatigue : {};
  const materialityRaw = isRecord(raw.materiality) ? raw.materiality : {};
  return {
    value: {
      tenant: asString(raw.tenant),
      status,
      watermarkId: asString(raw.watermark_id),
      ...(optionalString(raw.newest_data_date) !== undefined
        ? { newestDataDate: optionalString(raw.newest_data_date) }
        : {}),
      ...(optionalString(raw.prior_watermark_id) !== undefined
        ? { priorWatermarkId: optionalString(raw.prior_watermark_id) }
        : {}),
      ...(optionalString(raw.prior_newest_data_date) !== undefined
        ? { priorNewestDataDate: optionalString(raw.prior_newest_data_date) }
        : {}),
      headline: asString(raw.headline),
      entries,
      entriesTotal: asNumber(raw.entries_total) ?? entries.length,
      immaterial: {
        pinMovements: asNumber(immaterialRaw.pin_movements) ?? 0,
        newLeads: asNumber(immaterialRaw.new_leads) ?? 0,
        selfResolved: asNumber(immaterialRaw.self_resolved) ?? 0,
        entriesWithheldByCap: asNumber(immaterialRaw.entries_withheld_by_cap) ?? 0,
        notYetComparable: asNumber(immaterialRaw.not_yet_comparable) ?? 0,
        unavailable: asNumber(immaterialRaw.unavailable) ?? 0,
        withheldByKind: mapCounts(immaterialRaw.entries_withheld_by_kind),
        note: asString(immaterialRaw.note),
      },
      fatigue: {
        active: asBool(fatigueRaw.active),
        monitorsBelowGovernedGate: asNumber(fatigueRaw.monitors_below_governed_gate) ?? 0,
        consecutiveLoads: asNumber(fatigueRaw.consecutive_loads) ?? 0,
        loadsRequired: asNumber(fatigueRaw.loads_required) ?? 0,
        message: asString(fatigueRaw.message),
      },
      materiality: {
        unitKinds: mapUnitKinds(materialityRaw.unit_kinds),
        newLeadMinImpactCents: asNumber(materialityRaw.new_lead_min_impact_cents) ?? 0,
        alwaysMaterialLanes: asStringList(materialityRaw.always_material_lanes),
        maxEntries: asNumber(materialityRaw.max_entries) ?? 0,
        contentHash: asString(materialityRaw.content_hash),
        source: asString(materialityRaw.source),
      },
      pinsEvaluated: asNumber(raw.pins_evaluated) ?? 0,
      leadsVerified: asNumber(raw.leads_verified) ?? 0,
      ...(optionalString(raw.generated_at) !== undefined
        ? { generatedAt: optionalString(raw.generated_at) }
        : {}),
      warnings: readTurnWarnings(
        raw.warnings_v2,
        asArray(raw.warnings).filter((w): w is string => typeof w === "string"),
      ),
    },
    drift,
  };
}

/* ------------------------------------------------------------------ */
/* Leads                                                                */
/* ------------------------------------------------------------------ */

/** `MonitorsLeadPayload` — one lead's lifecycle state. */
export interface LeadState {
  anomalyId: string;
  status: LeadStatus;
  note: string;
  updatedAt?: string;
  claimedAtWatermark?: string;
  /** The platform's own re-derived exposure at the claim load. */
  baselineCents?: number;
  baselineBasis: string;
  /** Loads that have verified the claim so far, in order. */
  confirmingWatermarks: string[];
  confirmationsRequired: number;
  /** What the last verification measured, including "could not verify". */
  verificationNote: string;
}

export function mapLeadState(raw: unknown): LeadState | null {
  if (!isRecord(raw)) return null;
  const anomalyId = optionalString(raw.anomaly_id);
  if (anomalyId === undefined) return null;
  return {
    anomalyId,
    status: typeof raw.status === "string" && LEAD_STATUSES.has(raw.status)
      ? (raw.status as LeadStatus)
      : "open",
    note: asString(raw.note),
    ...(optionalString(raw.updated_at) !== undefined
      ? { updatedAt: optionalString(raw.updated_at) }
      : {}),
    ...(optionalString(raw.claimed_at_watermark) !== undefined
      ? { claimedAtWatermark: optionalString(raw.claimed_at_watermark) }
      : {}),
    ...(asNumber(raw.baseline_cents) !== undefined
      ? { baselineCents: asNumber(raw.baseline_cents) }
      : {}),
    baselineBasis: asString(raw.baseline_basis),
    confirmingWatermarks: asStringList(raw.confirming_watermarks),
    confirmationsRequired: asNumber(raw.confirmations_required) ?? 0,
    verificationNote: asString(raw.verification_note),
  };
}

