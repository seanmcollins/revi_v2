"use client";

import { AlertTriangle, Check, Eye } from "lucide-react";
import { useEffect, useState } from "react";

import { MonitorSensitivityForm } from "@/components/monitors/MonitorSensitivity";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { MonitorModel } from "@/lib/monitors";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * MONITOR THIS — one calm control, at the pin point of an artifact.
 *
 * It appears three places, always as the same control with the same word:
 * beside a chart's export, on a finding row, and on the worklist header.
 * One label everywhere, because "Monitor this" and "Add to Monitors" and "Pin"
 * would be three names for one gesture, and the vocabulary of an interface
 * is the signposting people learn.
 *
 * What it registers is a SPEC, not a snapshot. The server resolves the
 * investigation's own stored spec — no text is re-interpreted, no model is
 * called — so tomorrow's tile re-runs the question that produced this
 * artifact rather than remembering the number it produced. The button says
 * so on hover rather than leaving an analyst to discover which of the two
 * they got.
 *
 * The sensitivity form is BEHIND the button, not in front of it. The
 * common case is "tell me when this matters", which is the pack's governed
 * threshold and needs no decision; the analyst who wants their own gate
 * opens the popover. Putting four modes and a unit picker in the way of
 * every monitor would make the calm gesture a form.
 */
export function MonitorThis({
  /** Identifies THIS artifact, so only its own affordance changes state. */
  artifactKey,
  investigationId,
  /** The finding referent or chart id inside that investigation. */
  referent,
  presentation,
  label,
  /** Compact rendering for a dense row (a finding, a chart action bar). */
  size = "inline",
  className,
}: {
  artifactKey: string;
  investigationId: string;
  referent?: string;
  presentation: "chart" | "finding" | "worklist_slice" | "scalar";
  label?: string;
  size?: "inline" | "row";
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const driver = useSessionStore((s) => s.driver);
  const createMonitor = useSessionStore((s) => s.createMonitor);
  const justMonitored = useSessionStore((s) => s.monitors[artifactKey]);
  const pendingKey = useSessionStore((s) => s.monitorPendingKey);
  const monitorError = useSessionStore((s) => s.monitorError);
  const pending = pendingKey === artifactKey;
  const refusal = monitorError?.key === artifactKey ? monitorError.message : undefined;

  /**
   * IS THIS ALREADY MONITORED? Asked of the SERVER, not only of this page.
   *
   * `monitors` remembers what this browser registered a minute ago, which
   * is what makes the control feel immediate — and it forgets on reload,
   * which is when the failure appears: an analyst returns to a permalink
   * the next morning, the button says "Monitor this" over a monitor that has
   * been running all week, and clicking it puts two tiles over one
   * measure. The pin list publishes the provenance of every monitor
   * (`created_from_investigation_id` + `created_from_referent`), so the
   * question is answerable, and one read answers it for every affordance
   * on the page.
   */
  const loadMonitors = useSessionStore((s) => s.loadMonitors);
  const knownMonitors = useSessionStore((s) => s.knownMonitors);
  useEffect(() => {
    void loadMonitors();
  }, [loadMonitors, driver]);
  const existing = knownMonitors.find(
    (pin) =>
      pin.createdFromInvestigationId === investigationId &&
      (referent === undefined
        ? pin.createdFromReferent === undefined
        : pin.createdFromReferent === referent),
  );
  const monitor = justMonitored ?? existing;

  // A driver with no deployment has nowhere to register a monitor. Saying
  // nothing is better than a control whose click cannot do the thing its
  // label promises.
  if (!driver?.createMonitorsPin) return null;

  // Registered. The confirmation is the SERVER's baseline, so the analyst
  // can see they are monitoring the right cell before they walk away from
  // it — a "Monitoring ✓" with no figure in it would be a receipt for
  // something nobody checked.
  if (monitor) {
    return (
      <span
        data-monitoring={monitor.pinId}
        className={cn(
          "num inline-flex items-center gap-1 text-micro text-verified",
          className,
        )}
      >
        <Check aria-hidden className="size-3 shrink-0" />
        Monitoring
        {/* The same fragment `MonitorDeclarationNote` renders, spelled the
            same way: one product, one spelling of "Baseline 12.4%". */}
        {monitor.baselineValueText !== "" && (
          <span className="text-muted-foreground">· Baseline {monitor.baselineValueText}</span>
        )}
      </span>
    );
  }

  const trigger = (
    <Button
      variant="ghost"
      size="xs"
      disabled={pending}
      aria-label={label ? `Monitor ${label}` : "Monitor this"}
      className={cn(
        "h-5 gap-1 px-1.5 text-micro font-normal text-muted-foreground hover:text-foreground",
        // PERSISTENT, not hover-revealed. The compact form defaulted to
        // `opacity-0 group-hover:opacity-100`, and `FactRow` passes no
        // size — so on every finding card, the control that starts the
        // proactive monitoring this product is sold on did not exist for
        // a touch user, in a screenshot, or on a projector. It is drawn
        // in the muted ink instead, which is the volume that was wanted;
        // invisible is not a volume. Not an opacity step on that token —
        // `contrast.test.ts` bans those, and correctly. Filed monitors 7-10.
        size === "inline" && "transition-colors duration-150",
        className,
      )}
    >
      <Eye className="size-2.5" />
      {pending ? "Starting…" : "Monitor this"}
    </Button>
  );

  return (
    <span className="inline-flex items-center gap-1.5">
      <Popover open={open} onOpenChange={setOpen}>
        <Tooltip>
          <TooltipTrigger asChild>
            <PopoverTrigger asChild>{trigger}</PopoverTrigger>
          </TooltipTrigger>
          <TooltipContent side="top" className="max-w-72 text-meta leading-snug">
            Adds this to your Monitors. Revi re-runs the question behind it every time a data
            load lands and briefs you when it moves — it is monitoring the measure, not
            remembering this number.
          </TooltipContent>
        </Tooltip>
        <PopoverContent align="end" className="w-[22rem] max-w-[calc(100vw-2rem)] p-3">
          <p className="mb-2 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
            Monitor {label ?? "this"}
          </p>
          <MonitorSensitivityForm
            submitLabel="Start monitoring"
            pending={pending}
            {...(refusal ? { refusal } : {})}
            onSubmit={(model: MonitorModel) => {
              void (async () => {
                await createMonitor(artifactKey, {
                  investigationId,
                  ...(referent !== undefined ? { referent } : {}),
                  presentation,
                  ...(model.mode !== "governed_default" || model.direction !== "any" || model.note !== ""
                    ? { monitor: model }
                    : {}),
                });
                if (useSessionStore.getState().monitorError?.key === artifactKey) return;
                setOpen(false);
              })();
            }}
            onCancel={() => setOpen(false)}
          />
        </PopoverContent>
      </Popover>

      {refusal && !open && (
        <span
          role="alert"
          className="inline-flex items-start gap-1 text-micro leading-snug text-negative"
        >
          <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
          {refusal}
        </span>
      )}
    </span>
  );
}
