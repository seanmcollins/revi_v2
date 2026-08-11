"use client";

import { useState } from "react";

import { MonitorSensitivityForm } from "@/components/monitors/MonitorSensitivity";
import { Button } from "@/components/ui/button";
import { readableLabel } from "@/lib/prose";
import type { MonitorsPin, MonitorsTile, MonitorModel } from "@/lib/monitors";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * MANAGING ONE MONITOR — what it measures, what it takes to brief you, and
 * how to stop it.
 *
 * This was a popover hanging off a `…` trigger on a tile in a grid, on a
 * route that no longer exists. It is the same content, rendered INLINE
 * inside the monitor's own expanded detail on Home, which is where the
 * decision is now made: the reader has already opened this monitor, so a
 * second click into a floating panel is a step that buys nothing.
 *
 * Both controls state their cost before the click. Changing sensitivity
 * RE-REGISTERS the monitor — the routes publish no partial update, so the
 * only honest way to change a threshold is to start the monitor again — and
 * that resets the baseline "since you started monitoring" is measured from.
 * The form says so in those words rather than letting an analyst discover
 * it tomorrow when a five-point drift becomes a fresh zero.
 */
export function MonitorManagement({ tile, pin }: { tile: MonitorsTile; pin?: MonitorsPin }) {
  // The same repaired title the tile draws — a control that announces "Stop
  // monitoring denial rate for State Medicaid MCO?" is naming a card whose
  // visible title opens in capitals.
  const label = readableLabel(tile.label);
  const [editing, setEditing] = useState(false);
  /**
   * "Stop monitoring this" is ARMED before it fires.
   *
   * It was one click from ending a monitor's history, with the reassurance
   * copy sitting UNDER the button — read on the way past rather than
   * acknowledged. The two-step makes that sentence the thing the analyst
   * agrees to, which is what it was written to be, and it costs one
   * keystroke on the one control here that cannot be undone: re-registering
   * a monitor resets the baseline "since you started monitoring" is
   * measured from, so an undo would silently be a different monitor.
   */
  const [armed, setArmed] = useState(false);
  const createMonitor = useSessionStore((s) => s.createMonitor);
  const removeMonitor = useSessionStore((s) => s.removeMonitor);
  const pendingKey = useSessionStore((s) => s.monitorPendingKey);
  const monitorError = useSessionStore((s) => s.monitorError);
  // WHY THERE IS NO PIN, when there is none. The settings edited here ride
  // on `GET /v1/monitors/pins`, which is a different read from the one that
  // drew the tile — so it can fail on its own, and it has: one malformed
  // monitor 500'd it tenant-wide and every editor went grey with no
  // sentence anywhere on the page.
  const loadMonitors = useSessionStore((s) => s.loadMonitors);
  const monitorsError = useSessionStore((s) => s.monitorsError);
  const monitorsLoading = useSessionStore((s) => s.monitorsLoading);
  const monitorsLoaded = useSessionStore((s) => s.monitorsLoaded);
  const key = `tile:${tile.pinId}`;
  const pending = pendingKey === key;
  const refusal = monitorError?.key === key ? monitorError.message : undefined;

  /**
   * The one sentence this panel owes the reader when it has no settings to
   * show. Three states, three different facts — and never a bare disabled
   * control, which is the shape that says "this feature is off" about a
   * read that failed thirty seconds ago and would succeed on a reload.
   */
  const missingSettings = pin
    ? undefined
    : monitorsLoading
      ? { text: "Reading this monitor's settings…", failed: false }
      : monitorsError !== null
        ? {
            text: `Could not read this monitor's settings — reload to try again. ${monitorsError}`,
            failed: true,
          }
        : monitorsLoaded
          ? {
              // A 200 that did not contain this monitor. Measured live
              // alongside the 500, and it deserves its own sentence: the
              // read worked and this pin was not in it.
              text: "Could not read this monitor's settings — reload to try again. The deployment's monitor list came back without this monitor in it.",
              failed: true,
            }
          : { text: "This monitor's settings have not been read yet.", failed: false };

  const onSave = (monitor: MonitorModel): void => {
    if (!pin) return;
    void (async () => {
      // Create first, archive second. If the create is refused — an illegal
      // threshold unit — the monitor that exists is the one that was
      // already working, rather than none at all.
      await createMonitor(key, {
        spec: pin.spec,
        presentation: pin.presentation,
        label: pin.label,
        monitor,
      });
      if (useSessionStore.getState().monitorError?.key === key) return;
      await removeMonitor(`${key}:old`, pin.pinId);
      setEditing(false);
    })();
  };

  if (editing && pin) {
    return (
      /* The form's pinned action row bleeds through a `p-3` box (`-mx-3
         -mb-3`), so it is given one here exactly as the popover gave it
         one. `recommended` is THE NUMBER, off the pin that was just read:
         the default option said "Tell me about meaningful changes" on every
         monitor in the product because nothing supplied it. */
      <div className="rounded-lg border bg-surface-sunken/40 p-3">
        <MonitorSensitivityForm
          {...(pin.monitor ? { initial: pin.monitor } : {})}
          submitLabel="Save and restart this monitor"
          pending={pending}
          {...(refusal ? { refusal } : {})}
          {...(pin.recommendedThreshold ? { recommended: pin.recommendedThreshold } : {})}
          restartNote={
            pin.baselineValueText
              ? `Saving starts this monitor again. Its baseline becomes today's ${tile.valueText}, so “since you started monitoring” will measure from here instead of from ${pin.baselineValueText}.`
              : "Saving starts this monitor again, so “since you started monitoring” will measure from today's value."
          }
          onSubmit={onSave}
          onCancel={() => setEditing(false)}
        />
      </div>
    );
  }

  return (
    <div data-monitor-management={tile.pinId} className="space-y-2">
      <div>
        <p className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
          What this monitor measures
        </p>
        {/* THE SPEC, in the reader's own nouns. This is the one control that
            lets somebody catch a monitor measuring the wrong cell.
            `readableLabel` repairs the server's fallback humanizer, which
            splits `days_in_ar` on underscores and knows no initialisms — so
            this read "Days in ar" under a tile labelled "days in A/R by
            payer": one measure, three spellings, one card. */}
        {pin?.specSummary && (
          <p className="mt-1 text-micro leading-snug text-foreground/80">
            {readableLabel(pin.specSummary)}
          </p>
        )}
        {/* The window mode in the server's own sentence: a moving period (a
            real movement) or fixed dates (late-arriving data). It decides
            how every delta on this monitor should be read, and it is not
            derivable from the spec on screen. */}
        {pin && (
          <p className="mt-1 text-micro leading-snug text-muted-foreground">
            {pin.windowNote || "This monitor's window is published on its pin."}
          </p>
        )}
        {/* What happened to the request at creation — the cell it was
            narrowed to, a duplicate returned instead of a second monitor. */}
        {pin?.notes.map((note) => (
          <p key={note} className="mt-1 text-micro leading-snug text-muted-foreground">
            {note}
          </p>
        ))}
        {pin?.alreadyExisted && (
          <p className="mt-1 text-micro leading-snug text-muted-foreground">
            This monitor already existed — Revi returned it rather than starting a second one
            measuring the same thing.
          </p>
        )}
        {pin?.monitor?.note && (
          <p className="mt-1 text-micro leading-snug text-muted-foreground">
            Your reason: {pin.monitor.note}
          </p>
        )}
        {/* THE STATED ABSENCE. `role="alert"` only when something actually
            failed — an in-flight read is not an error and must not
            interrupt a screen reader as one. */}
        {missingSettings && (
          <p
            data-monitor-settings-error={missingSettings.failed ? "true" : undefined}
            {...(missingSettings.failed ? { role: "alert" as const } : {})}
            className={cn(
              "mt-1 text-micro leading-snug",
              missingSettings.failed ? "text-negative" : "text-muted-foreground",
            )}
          >
            {missingSettings.text}
          </p>
        )}
      </div>

      {/* THE CONTROL EXPLAINS ITSELF. With settings in hand it opens the
          editor; without them it is the retry for the read that failed —
          never a grey rectangle whose only account of itself is that it
          cannot be pressed. */}
      {pin ? (
        <Button
          variant="outline"
          size="xs"
          onClick={() => setEditing(true)}
          className="w-full justify-start text-meta font-normal"
        >
          Change what it takes to brief you
        </Button>
      ) : (
        <Button
          variant="outline"
          size="xs"
          disabled={monitorsLoading}
          onClick={() => {
            void loadMonitors({ force: true });
          }}
          className="w-full justify-start text-meta font-normal"
        >
          {monitorsLoading
            ? "Reading this monitor's settings…"
            : "Read this monitor's settings again"}
        </Button>
      )}

      {/* THE TWO-STEP. The reassurance sentence is above the confirming
          button rather than below the firing one, so it is what the analyst
          acknowledges instead of what they read on the way past. "Keep
          monitoring" is the wider target and comes first, because the
          reversible choice should be the easy one. */}
      {armed ? (
        <div data-stop-monitor-armed className="space-y-1.5 rounded-md border p-2">
          <p className="text-meta font-medium leading-snug">Stop monitoring {label}?</p>
          <p className="text-micro leading-snug text-muted-foreground">
            Nothing is deleted. The loads this monitor has already been briefed on stay readable,
            and its investigations keep their links. Starting it again later measures “since you
            started monitoring” from that day, not from this one.
          </p>
          <div className="flex gap-1.5 pt-0.5">
            <Button
              variant="outline"
              size="xs"
              onClick={() => setArmed(false)}
              className="flex-1 justify-center text-meta font-normal"
            >
              Keep monitoring
            </Button>
            <Button
              variant="ghost"
              size="xs"
              disabled={pending}
              onClick={() => {
                void removeMonitor(key, tile.pinId);
                setArmed(false);
              }}
              className="flex-1 justify-center text-meta font-normal text-negative hover:text-negative"
            >
              {pending ? "Stopping…" : "Yes, stop monitoring"}
            </Button>
          </div>
        </div>
      ) : (
        <Button
          variant="ghost"
          size="xs"
          disabled={pending}
          onClick={() => setArmed(true)}
          className="w-full justify-start text-meta font-normal text-muted-foreground hover:text-foreground"
        >
          Stop monitoring this
        </Button>
      )}
      {refusal && (
        <p role="alert" className="text-micro leading-snug text-negative">
          {refusal}
        </p>
      )}
    </div>
  );
}
