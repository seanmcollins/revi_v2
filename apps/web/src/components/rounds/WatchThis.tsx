"use client";

import { AlertTriangle, Check, Eye } from "lucide-react";
import { useEffect, useState } from "react";

import { WatchSensitivityForm } from "@/components/rounds/WatchSensitivity";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { WatchModel } from "@/lib/rounds";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * WATCH THIS — one calm control, at the pin point of an artifact.
 *
 * It appears three places, always as the same control with the same word:
 * beside a chart's export, on a finding row, and on the worklist header.
 * One label everywhere, because "Watch this" and "Add to Rounds" and "Pin"
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
 * every watch would make the calm gesture a form.
 */
export function WatchThis({
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
  const createWatch = useSessionStore((s) => s.createWatch);
  const justWatched = useSessionStore((s) => s.watches[artifactKey]);
  const pendingKey = useSessionStore((s) => s.watchPendingKey);
  const watchError = useSessionStore((s) => s.watchError);
  const pending = pendingKey === artifactKey;
  const refusal = watchError?.key === artifactKey ? watchError.message : undefined;

  /**
   * IS THIS ALREADY WATCHED? Asked of the SERVER, not only of this page.
   *
   * `watches` remembers what this browser registered a minute ago, which
   * is what makes the control feel immediate — and it forgets on reload,
   * which is when the failure appears: an analyst returns to a permalink
   * the next morning, the button says "Watch this" over a watch that has
   * been running all week, and clicking it puts two tiles over one
   * measure. The pin list publishes the provenance of every watch
   * (`created_from_investigation_id` + `created_from_referent`), so the
   * question is answerable, and one read answers it for every affordance
   * on the page.
   */
  const loadWatches = useSessionStore((s) => s.loadWatches);
  const knownWatches = useSessionStore((s) => s.knownWatches);
  useEffect(() => {
    void loadWatches();
  }, [loadWatches, driver]);
  const existing = knownWatches.find(
    (pin) =>
      pin.createdFromInvestigationId === investigationId &&
      (referent === undefined
        ? pin.createdFromReferent === undefined
        : pin.createdFromReferent === referent),
  );
  const watch = justWatched ?? existing;

  // A driver with no deployment has nowhere to register a watch. Saying
  // nothing is better than a control whose click cannot do the thing its
  // label promises.
  if (!driver?.createRoundsPin) return null;

  // Registered. The confirmation is the SERVER's baseline, so the analyst
  // can see they are watching the right cell before they walk away from
  // it — a "Watching ✓" with no figure in it would be a receipt for
  // something nobody checked.
  if (watch) {
    return (
      <span
        data-watching={watch.pinId}
        className={cn(
          "num inline-flex items-center gap-1 text-micro text-verified",
          className,
        )}
      >
        <Check aria-hidden className="size-3 shrink-0" />
        Watching
        {watch.baselineValueText !== "" && (
          <span className="text-muted-foreground">· baseline {watch.baselineValueText}</span>
        )}
      </span>
    );
  }

  const trigger = (
    <Button
      variant="ghost"
      size="xs"
      disabled={pending}
      aria-label={label ? `Watch ${label}` : "Watch this"}
      className={cn(
        "h-5 gap-1 px-1.5 text-micro font-normal text-muted-foreground hover:text-foreground",
        // PERSISTENT, not hover-revealed. The compact form defaulted to
        // `opacity-0 group-hover:opacity-100`, and `FactRow` passes no
        // size — so on every finding card, the control that starts the
        // proactive monitoring this product is sold on did not exist for
        // a touch user, in a screenshot, or on a projector. It is drawn
        // in the muted ink instead, which is the volume that was wanted;
        // invisible is not a volume. Not an opacity step on that token —
        // `contrast.test.ts` bans those, and correctly. Filed rounds 7-10.
        size === "inline" && "transition-colors duration-150",
        className,
      )}
    >
      <Eye className="size-2.5" />
      {pending ? "Starting…" : "Watch this"}
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
            Adds this to your Rounds. Revi re-runs the question behind it every time a data
            load lands and briefs you when it moves — it is watching the measure, not
            remembering this number.
          </TooltipContent>
        </Tooltip>
        <PopoverContent align="end" className="w-[22rem] max-w-[calc(100vw-2rem)] p-3">
          <p className="mb-2 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
            Watch {label ?? "this"}
          </p>
          <WatchSensitivityForm
            submitLabel="Start watching"
            pending={pending}
            {...(refusal ? { refusal } : {})}
            onSubmit={(model: WatchModel) => {
              void (async () => {
                await createWatch(artifactKey, {
                  investigationId,
                  ...(referent !== undefined ? { referent } : {}),
                  presentation,
                  ...(model.mode !== "governed_default" || model.direction !== "any" || model.note !== ""
                    ? { watch: model }
                    : {}),
                });
                if (useSessionStore.getState().watchError?.key === artifactKey) return;
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
