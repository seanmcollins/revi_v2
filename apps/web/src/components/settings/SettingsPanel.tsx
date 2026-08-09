"use client";

import { Bug, RotateCcw, ShieldAlert, SlidersHorizontal } from "lucide-react";
import { useEffect } from "react";
import { Dialog as DialogPrimitive } from "radix-ui";

import { Button } from "@/components/ui/button";
import { apiBaseUrl } from "@/lib/apiDriver";
import {
  decimalToNumber,
  exceedsCeiling,
  numberToDecimal,
  type EvidenceDepth,
  type NarrativeDepth,
  type SessionSettings,
  type SettingsBounds,
} from "@/lib/settings";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * The internal settings panel (⌘K → "Settings", or the gear by the
 * connection pill).
 *
 * Two rules it exists to keep:
 *
 * 1. **Only real controls.** Every control below is rendered from
 *    `GET /v1/capabilities` — a deployment that publishes no model tiers
 *    (because its language model applies no per-call override) gets no
 *    model tier control at all, rather than a knob whose every value the
 *    server would refuse. When the bounds cannot be read, the panel says
 *    so and offers nothing.
 * 2. **Refusals are shown, never pre-empted.** Nothing here is clamped to
 *    fit a bound. An out-of-bounds value is sent as chosen and the
 *    server's `POLICY_DENIED` sentence — which names the bound and what
 *    would satisfy it — is repeated verbatim at the top of the panel.
 *
 * Nothing here can weaken a check: there is no validation, suppression or
 * grading that a setting can skip. Cost and speed trade through model
 * tier, evidence scope and ceilings only.
 */
export function SettingsPanel() {
  const open = useSessionStore((s) => s.settingsOpen);
  const closeSettings = useSessionStore((s) => s.closeSettings);
  const settings = useSessionStore((s) => s.settings);
  const patchSettings = useSessionStore((s) => s.patchSettings);
  const resetSettings = useSessionStore((s) => s.resetSettings);
  const capabilities = useSessionStore((s) => s.capabilities);
  const capabilitiesState = useSessionStore((s) => s.capabilitiesState);
  const capabilitiesError = useSessionStore((s) => s.capabilitiesError);
  const loadCapabilities = useSessionStore((s) => s.loadCapabilities);
  const lastPolicyDenial = useSessionStore((s) => s.lastPolicyDenial);
  const connection = useSessionStore((s) => s.connection);
  const watermark = useSessionStore((s) => s.watermark);
  const pack = useSessionStore((s) => s.pack);

  // Opening through the store already asks for the bounds; a panel opened
  // any other way (deep link, test) still gets exactly one read.
  useEffect(() => {
    if (open && capabilitiesState === "idle") void loadCapabilities();
  }, [open, capabilitiesState, loadCapabilities]);

  const bounds = capabilities?.settings;

  return (
    <DialogPrimitive.Root open={open} onOpenChange={(next) => !next && closeSettings()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="overlay-in fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]" />
        <DialogPrimitive.Content className="panel-in fixed left-1/2 top-[10%] z-50 flex max-h-[80vh] w-[34rem] max-w-[calc(100vw-2rem)] -translate-x-1/2 flex-col overflow-hidden rounded-xl border bg-surface-overlay shadow-2xl shadow-black/20">
          <div className="flex items-center gap-2 border-b px-4 py-3">
            <SlidersHorizontal className="size-3.5 shrink-0 text-muted-foreground" />
            <DialogPrimitive.Title className="text-[0.82rem] font-semibold tracking-tight">
              Settings
            </DialogPrimitive.Title>
            <span className="rounded-full border border-warning/40 bg-warning/10 px-1.5 py-0.5 text-[0.55rem] font-semibold uppercase tracking-[0.12em] text-warning">
              Internal
            </span>
            <kbd className="ml-auto rounded border bg-surface-sunken px-1.5 py-0.5 font-mono text-[0.6rem] text-muted-foreground">
              esc
            </kbd>
          </div>

          <DialogPrimitive.Description className="sr-only">
            Internal controls for model tier, per-turn cost ceiling, narrative and
            evidence depth, and debug mode. Only controls this deployment supports
            are shown.
          </DialogPrimitive.Description>

          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">
            {lastPolicyDenial && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-md border border-negative/50 bg-negative/10 px-3 py-2 text-[0.72rem] leading-snug"
              >
                <ShieldAlert className="mt-0.5 size-3.5 shrink-0 text-negative" />
                <div>
                  <p className="font-medium text-negative">The API refused these settings</p>
                  <p className="mt-0.5 text-secondary-foreground">{lastPolicyDenial}</p>
                </div>
              </div>
            )}

            {capabilitiesState === "loading" && (
              <p className="text-[0.72rem] text-muted-foreground">
                Reading what this deployment accepts…
              </p>
            )}

            {capabilitiesState === "unavailable" && (
              <div
                role="alert"
                className="rounded-md border bg-surface-sunken/60 px-3 py-2.5 text-[0.72rem] leading-snug text-muted-foreground"
              >
                <p className="font-medium text-secondary-foreground">
                  No controls — this deployment&rsquo;s bounds could not be read.
                </p>
                <p className="mt-0.5">{capabilitiesError}</p>
                <Button
                  variant="outline"
                  size="xs"
                  className="mt-2 text-[0.65rem] font-normal"
                  onClick={() => void loadCapabilities()}
                >
                  Try again
                </Button>
              </div>
            )}

            {bounds && (
              <ControlList bounds={bounds} settings={settings} patchSettings={patchSettings} />
            )}

            <section className="space-y-1.5">
              <h3 className="text-[0.6rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
                Effective configuration
              </h3>
              <p className="text-[0.68rem] leading-snug text-muted-foreground">
                Read-only — what this deployment is actually running. Change it in the
                server&rsquo;s environment, not here.
              </p>
              <dl className="num grid grid-cols-[9rem_minmax(0,1fr)] gap-x-3 gap-y-1 rounded-md border bg-surface-sunken/50 px-3 py-2.5 text-[0.68rem]">
                <Fact label="API" value={apiBaseUrl()} />
                <Fact label="Connection" value={`${connection.mode} · ${connection.state}`} />
                <Fact label="Model" value={connection.llmMode ?? capabilities?.llm ?? "unknown"} />
                <Fact
                  label="Model pin"
                  value={
                    bounds?.defaultModelTier ||
                    (capabilitiesState === "ready" ? "not pinned" : "unknown")
                  }
                />
                <Fact label="Stores" value={connection.storeMode ?? "unknown"} />
                <Fact label="Auth" value={connection.authMode ?? "unknown"} />
                <Fact
                  label="Data as of"
                  value={`${watermark.loadedAt} · through ${watermark.newestDataDate}`}
                />
                <Fact
                  label="Newest load"
                  value={
                    capabilities?.newestWatermarkId ||
                    connection.newestWatermarkId ||
                    watermark.id
                  }
                />
                <Fact label="Definitions" value={`${pack.packId}@${pack.version}`} />
              </dl>
              {bounds && !bounds.modelTierEffective && (
                <p className="text-[0.65rem] leading-snug text-muted-foreground">
                  Model tier is not offered here: this deployment&rsquo;s language model
                  applies no per-call override, so choosing a tier would change nothing
                  about the answer.
                </p>
              )}
            </section>
          </div>

          <div className="flex items-center gap-3 border-t px-4 py-2.5">
            <p className="text-[0.62rem] leading-snug text-muted-foreground">
              Applies to the next turn you send. Kept in this browser only.
            </p>
            <Button
              variant="ghost"
              size="xs"
              className="ml-auto gap-1 text-[0.65rem] font-normal text-muted-foreground"
              onClick={resetSettings}
            >
              <RotateCcw className="size-3" />
              Reset to defaults
            </Button>
          </div>
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="truncate text-secondary-foreground" title={value}>
        {value}
      </dd>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* Controls — each one rendered only if the deployment published it     */
/* ------------------------------------------------------------------ */

function ControlList({
  bounds,
  settings,
  patchSettings,
}: {
  bounds: SettingsBounds;
  settings: SessionSettings;
  patchSettings: (patch: Partial<SessionSettings>) => void;
}) {
  const ceiling = decimalToNumber(bounds.maxTurnCostUsd) ?? 0;
  const current = decimalToNumber(settings.maxTurnCostUsd);
  const overCeiling = exceedsCeiling(settings.maxTurnCostUsd, bounds.maxTurnCostUsd);
  // A persisted tier this deployment no longer allows stays selected and
  // visible — silently dropping it back to the pin would hide the refusal.
  const tiers =
    settings.modelTier && !bounds.modelTiers.includes(settings.modelTier)
      ? [...bounds.modelTiers, settings.modelTier]
      : bounds.modelTiers;

  // A deployment can publish bounds that offer nothing at all. Saying so
  // beats an empty gap the reader has to interpret.
  const offered =
    (tiers.length > 0 ? 1 : 0) +
    (ceiling > 0 ? 1 : 0) +
    (bounds.narrativeDepths.length > 1 ? 1 : 0) +
    (bounds.evidenceDepths.length > 1 ? 1 : 0) +
    (bounds.debugAvailable ? 1 : 0);

  return (
    <div className="space-y-5">
      {offered === 0 && (
        <p className="text-[0.72rem] leading-snug text-muted-foreground">
          This deployment offers no adjustable settings — everything below is fixed in
          its environment.
        </p>
      )}

      {tiers.length > 0 && (
        <Control
          label="Interpretation model"
          hint="Applies to the model calls that read your question, plan the work and write the answer."
        >
          <Segmented
            name="model-tier"
            value={settings.modelTier ?? ""}
            options={[
              { value: "", label: "Deployment pin", hint: "Best accuracy" },
              ...tiers.map((tier) => ({
                value: tier,
                label: tier,
                hint:
                  tier === bounds.defaultModelTier
                    ? "This deployment's pin"
                    : bounds.modelTiers.includes(tier)
                      ? "Faster and cheaper — may ask to clarify more often"
                      : "Not in this deployment's allowlist — the API will refuse it",
              })),
            ]}
            onChange={(value) => patchSettings({ modelTier: value === "" ? null : value })}
          />
        </Control>
      )}

      {ceiling > 0 && (
        <Control
          label="Per-turn cost ceiling"
          hint="Caps total model spend inside one turn. Off runs no per-turn ledger — each call stays bounded by the deployment's own per-call cap. Running out ends the turn with a question, never a quiet downgrade."
        >
          <div className="space-y-2">
            <div className="flex items-center gap-2.5">
              <input
                id="setting-budget"
                type="range"
                min={0.01}
                max={Math.max(ceiling, current ?? 0)}
                step={0.01}
                disabled={current === null}
                value={current ?? ceiling}
                onChange={(e) =>
                  patchSettings({ maxTurnCostUsd: numberToDecimal(Number(e.target.value)) })
                }
                aria-label="Per-turn cost ceiling in USD"
                className="h-1 min-w-0 flex-1 accent-[var(--verified)] disabled:opacity-40"
              />
              <span className="num w-14 shrink-0 text-right text-[0.72rem] tabular-nums">
                {current === null ? "off" : `$${settings.maxTurnCostUsd}`}
              </span>
            </div>
            <label className="flex items-center gap-2 text-[0.68rem] text-muted-foreground">
              <input
                type="checkbox"
                checked={current === null}
                onChange={(e) =>
                  patchSettings({
                    maxTurnCostUsd: e.target.checked ? null : numberToDecimal(ceiling),
                  })
                }
                className="size-3 accent-[var(--verified)]"
              />
              No per-turn ceiling (deployment per-call cap only)
            </label>
            {overCeiling && (
              <p className="text-[0.65rem] leading-snug text-warning">
                Above this deployment&rsquo;s published ceiling of ${bounds.maxTurnCostUsd} — it
                will be sent as chosen and the API will refuse it.
              </p>
            )}
          </div>
        </Control>
      )}

      {bounds.narrativeDepths.length > 1 && (
        <Control
          label="Answer detail"
          hint="How much the write-up covers. Every number is checked the same way at both settings."
        >
          <Segmented
            name="narrative-depth"
            value={settings.narrativeDepth}
            options={bounds.narrativeDepths.map((depth) => ({
              value: depth,
              label: depth === "summary" ? "Summary" : "Full analyst detail",
              hint:
                depth === "summary"
                  ? "The headline and what moved it"
                  : "Every finding, with the reasoning spelled out",
            }))}
            onChange={(value) => patchSettings({ narrativeDepth: value as NarrativeDepth })}
          />
        </Control>
      )}

      {bounds.evidenceDepths.length > 1 && (
        <Control
          label="Evidence depth"
          hint={`How many rows each check is allowed to bring back. Deep widens this platform's own cutoffs by ${bounds.evidenceDepthDeepMultiplier}× — it costs more time, and it never fetches less than standard does.`}
        >
          <Segmented
            name="evidence-depth"
            value={settings.evidenceDepth}
            options={bounds.evidenceDepths.map((depth) => ({
              value: depth,
              label: depth === "standard" ? "Standard" : "Deep",
              hint:
                depth === "standard"
                  ? "The cutoffs the metric pack authors chose"
                  : `${bounds.evidenceDepthDeepMultiplier}× wider — fewer "top N of" caveats`,
            }))}
            onChange={(value) => patchSettings({ evidenceDepth: value as EvidenceDepth })}
          />
        </Control>
      )}

      {bounds.debugAvailable && (
        <Control
          label="Debug mode"
          hint="Shows how each turn was decided: classification, chosen ids, plan checks, per-query timing and per-model-call cost. Also switches the progress rail back to engine stage names."
        >
          <button
            type="button"
            role="switch"
            aria-checked={settings.debug}
            onClick={() => patchSettings({ debug: !settings.debug })}
            className={cn(
              "inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-[0.7rem] font-medium transition-colors duration-150",
              settings.debug
                ? "border-warning/40 bg-warning/10 text-warning"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            <Bug className="size-3" />
            {settings.debug ? "On" : "Off"}
          </button>
        </Control>
      )}
    </div>
  );
}

function Control({
  label,
  hint,
  children,
}: {
  label: string;
  hint: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-[0.75rem] font-medium tracking-tight">{label}</h3>
      <p className="text-[0.68rem] leading-snug text-muted-foreground">{hint}</p>
      <div className="pt-0.5">{children}</div>
    </section>
  );
}

function Segmented({
  name,
  value,
  options,
  onChange,
}: {
  name: string;
  value: string;
  options: { value: string; label: string; hint?: string }[];
  onChange: (value: string) => void;
}) {
  return (
    <div role="radiogroup" aria-label={name} className="flex flex-wrap gap-1">
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          title={option.hint}
          onClick={() => onChange(option.value)}
          className={cn(
            "rounded-md border px-2 py-1 text-left text-[0.7rem] transition-colors duration-150",
            option.value === value
              ? "border-ring/50 bg-accent font-medium text-foreground"
              : "text-muted-foreground hover:border-ring/40 hover:text-foreground",
          )}
        >
          {option.label}
          {option.hint && (
            <span className="ml-1.5 text-[0.6rem] text-muted-foreground">{option.hint}</span>
          )}
        </button>
      ))}
    </div>
  );
}
