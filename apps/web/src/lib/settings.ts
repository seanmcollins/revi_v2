/**
 * Session settings — the internal control surface, and the one place that
 * decides what actually goes on the wire.
 *
 * Every control here maps to a mechanism the API publishes on
 * `GET /v1/capabilities` (`SettingsBoundsPayload`). Nothing is rendered
 * that the deployment did not say it supports, and nothing is corrected
 * on the way out: an out-of-bounds value is SENT and the server's
 * `POLICY_DENIED` refusal is shown verbatim. Clamping in the browser would
 * produce a session whose panel says one thing and whose answers were
 * computed under another.
 *
 * Two rules shape this file:
 *
 * - **Default means absent.** When every control sits at its default the
 *   wire body carries no `settings` key at all, so the default path is
 *   byte-identical to the one that existed before this feature. `null` for
 *   `max_turn_cost_usd` is NOT "unlimited" — it is "run no per-turn
 *   ledger", leaving each call bounded by `REVI_LLM_MAX_BUDGET_USD`
 *   exactly as before.
 * - **Per-turn, not per-session.** Settings ride on `TurnRequest.settings`
 *   (published, optional, turn-scoped) rather than `OpenSessionRequest`.
 *   A refusal then lands on the turn that used the setting, verbatim and
 *   recoverable by changing the control and re-asking — instead of making
 *   session bootstrap itself fail and every later turn report a bound the
 *   analyst set an hour ago. The session record is never rewritten, so this
 *   panel stays the single source of truth for what the next turn will run
 *   under, with no second server-side copy to drift from it.
 *
 * `max_turn_cost_usd` is a DECIMAL STRING on the wire, and stays a string
 * here for the same reason it is one there: a budget rounded through a
 * float is not the budget that was set.
 */

/** How much narrative the composer is asked to write. */
export type NarrativeDepth = "summary" | "analyst";

/** How wide the platform's own top-N cutoffs are planned. */
export type EvidenceDepth = "standard" | "deep";

export interface SessionSettings {
  /** Model id; null = the deployment's pin. */
  modelTier: string | null;
  /** Decimal string ("0.25"); null = no per-turn ledger. */
  maxTurnCostUsd: string | null;
  narrativeDepth: NarrativeDepth;
  evidenceDepth: EvidenceDepth;
  debug: boolean;
}

export const DEFAULT_SETTINGS: SessionSettings = {
  modelTier: null,
  maxTurnCostUsd: null,
  narrativeDepth: "summary",
  evidenceDepth: "standard",
  debug: false,
};

/** What `/v1/capabilities` publishes about what this deployment accepts. */
export interface SettingsBounds {
  /** Empty when the wired model applies no per-call override — then the
   *  control is not offered at all, because choosing would change nothing. */
  modelTiers: string[];
  defaultModelTier: string;
  modelTierEffective: boolean;
  /** Largest per-turn ceiling a session may set (decimal string). */
  maxTurnCostUsd: string;
  narrativeDepths: NarrativeDepth[];
  evidenceDepths: EvidenceDepth[];
  /** How much wider `deep` plans the platform's top-N cutoffs. */
  evidenceDepthDeepMultiplier: number;
  debugAvailable: boolean;
}

/** `GET /v1/capabilities` — the deployment's live wiring plus its bounds. */
export interface DeploymentCapabilities {
  llm: string;
  packId: string;
  packVersion: string;
  packSnapshotId: string;
  newestWatermarkId: string;
  settings: SettingsBounds;
}

export const SETTINGS_STORAGE_KEY = "revi-settings";

const NARRATIVE_DEPTHS: ReadonlySet<string> = new Set<NarrativeDepth>(["summary", "analyst"]);
const EVIDENCE_DEPTHS: ReadonlySet<string> = new Set<EvidenceDepth>(["standard", "deep"]);

export function isNarrativeDepth(value: unknown): value is NarrativeDepth {
  return typeof value === "string" && NARRATIVE_DEPTHS.has(value);
}

export function isEvidenceDepth(value: unknown): value is EvidenceDepth {
  return typeof value === "string" && EVIDENCE_DEPTHS.has(value);
}

export function isDefaultSettings(settings: SessionSettings): boolean {
  return (
    settings.modelTier === null &&
    settings.maxTurnCostUsd === null &&
    settings.narrativeDepth === DEFAULT_SETTINGS.narrativeDepth &&
    settings.evidenceDepth === DEFAULT_SETTINGS.evidenceDepth &&
    settings.debug === DEFAULT_SETTINGS.debug
  );
}

/**
 * The published `SessionSettingsModel`, or null when every control is at
 * its default — in which case the caller omits the key entirely rather
 * than sending a body that means "the defaults, again".
 */
export function settingsToWire(settings: SessionSettings): Record<string, unknown> | null {
  if (isDefaultSettings(settings)) return null;
  return {
    model_tier: settings.modelTier,
    max_turn_cost_usd: settings.maxTurnCostUsd,
    narrative_depth: settings.narrativeDepth,
    evidence_depth: settings.evidenceDepth,
    debug: settings.debug,
  };
}

/**
 * Read persisted settings. Only this app's OWN storage is being parsed, so
 * an unreadable field falls back to its default; nothing here is checked
 * against the deployment's bounds — that is the server's call, and pre-
 * empting it would hide the refusal the analyst needs to see.
 */
export function readSettings(raw: unknown): SessionSettings {
  if (typeof raw !== "object" || raw === null) return DEFAULT_SETTINGS;
  const record = raw as Record<string, unknown>;
  const modelTier =
    typeof record.modelTier === "string" && record.modelTier.trim() !== ""
      ? record.modelTier
      : null;
  const budget =
    typeof record.maxTurnCostUsd === "string" && record.maxTurnCostUsd.trim() !== ""
      ? record.maxTurnCostUsd
      : null;
  return {
    modelTier,
    maxTurnCostUsd: budget,
    narrativeDepth: isNarrativeDepth(record.narrativeDepth)
      ? record.narrativeDepth
      : DEFAULT_SETTINGS.narrativeDepth,
    evidenceDepth: isEvidenceDepth(record.evidenceDepth)
      ? record.evidenceDepth
      : DEFAULT_SETTINGS.evidenceDepth,
    debug: record.debug === true,
  };
}

export function loadSettings(): SessionSettings {
  if (typeof window === "undefined") return DEFAULT_SETTINGS;
  try {
    const stored = window.localStorage.getItem(SETTINGS_STORAGE_KEY);
    if (stored === null) return DEFAULT_SETTINGS;
    return readSettings(JSON.parse(stored));
  } catch {
    // Storage unavailable (privacy mode) or corrupt JSON — defaults, and
    // the panel still works for this session.
    return DEFAULT_SETTINGS;
  }
}

export function saveSettings(settings: SessionSettings): void {
  if (typeof window === "undefined") return;
  try {
    if (isDefaultSettings(settings)) {
      window.localStorage.removeItem(SETTINGS_STORAGE_KEY);
      return;
    }
    window.localStorage.setItem(SETTINGS_STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Non-fatal: the settings still apply to this tab's turns.
  }
}

/* ------------------------------------------------------------------ */
/* Bounds (GET /v1/capabilities → SettingsBoundsPayload)               */
/* ------------------------------------------------------------------ */

function asStringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
}

/**
 * Read the published bounds tolerantly: a deployment that omits a list is
 * a deployment that offers no such control, which is exactly how the panel
 * renders it. Nothing is invented — an absent `model_tiers` means no model
 * tier control, not "assume the usual two".
 */
export function parseSettingsBounds(raw: unknown): SettingsBounds {
  const record = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  return {
    modelTiers: asStringList(record.model_tiers),
    defaultModelTier:
      typeof record.default_model_tier === "string" ? record.default_model_tier : "",
    modelTierEffective: record.model_tier_effective === true,
    maxTurnCostUsd: typeof record.max_turn_cost_usd === "string" ? record.max_turn_cost_usd : "0",
    narrativeDepths: asStringList(record.narrative_depths).filter(isNarrativeDepth),
    evidenceDepths: asStringList(record.evidence_depths).filter(isEvidenceDepth),
    evidenceDepthDeepMultiplier:
      typeof record.evidence_depth_deep_multiplier === "number"
        ? record.evidence_depth_deep_multiplier
        : 1,
    debugAvailable: record.debug_available === true,
  };
}

export function parseCapabilities(raw: unknown): DeploymentCapabilities {
  const record = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
  const str = (value: unknown): string => (typeof value === "string" ? value : "");
  return {
    llm: str(record.llm),
    packId: str(record.pack_id),
    packVersion: str(record.pack_version),
    packSnapshotId: str(record.pack_snapshot_id),
    newestWatermarkId: str(record.newest_watermark_id),
    settings: parseSettingsBounds(record.settings),
  };
}

/* ------------------------------------------------------------------ */
/* Money helpers (decimal strings, never floats)                       */
/* ------------------------------------------------------------------ */

/** "0.25" → 0.25 for slider geometry ONLY; the string stays canonical. */
export function decimalToNumber(value: string | null): number | null {
  if (value === null) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Slider position → the decimal string that is actually sent. */
export function numberToDecimal(value: number): string {
  return value.toFixed(2);
}

/** True when a persisted ceiling is above what this deployment will accept. */
export function exceedsCeiling(value: string | null, ceiling: string): boolean {
  const requested = decimalToNumber(value);
  const bound = decimalToNumber(ceiling);
  if (requested === null || bound === null) return false;
  return requested > bound;
}
