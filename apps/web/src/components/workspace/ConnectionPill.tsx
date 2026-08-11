"use client";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * Header status pill for the driver connection state machine:
 * mock (neutral) · api connecting (amber, pulsing) · api online (green) ·
 * api offline (red). Details (last failure) surface as a tooltip title.
 */
export function ConnectionPill() {
  const connection = useSessionStore((s) => s.connection);

  if (connection.mode === "mock") {
    return (
      <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border bg-surface-sunken px-2 py-0.5 text-micro font-medium text-muted-foreground">
        <span className="size-1.5 rounded-full bg-muted-foreground/50" />
        Mock fixture
      </span>
    );
  }

  const offlineHint = 'Cannot reach the Revi API. Run "make dev" to start it.';
  const look =
    connection.state === "online"
      ? { dot: "bg-verified", pill: "border-verified/40 bg-verified/10 text-verified", label: "API online" }
      : connection.state === "connecting"
        ? {
            dot: "bg-warning animate-pulse",
            pill: "border-warning/40 bg-warning/10 text-warning",
            label: "Connecting…",
          }
        : { dot: "bg-negative", pill: "border-negative/40 bg-negative/10 text-negative", label: "API offline" };

  return (
    <span
      role="status"
      title={
        connection.state === "offline"
          ? connection.detail
            ? `${connection.detail} — ${offlineHint}`
            : offlineHint
          : connection.detail
      }
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-micro font-medium",
        look.pill,
      )}
    >
      <span className={cn("size-1.5 rounded-full", look.dot)} />
      {look.label}
    </span>
  );
}

/**
 * The same state machine, in 48px of rail.
 *
 * When the sessions pane folds to its icon strip the pill's label has
 * nowhere to go, and dropping the indicator entirely was not an option:
 * "is the deployment answering" is the one fact the rail carries that
 * nothing else on the workspace says, and an analyst whose questions have
 * stopped coming back needs it more when the screen is narrow, not less.
 *
 * So the dot survives and the label moves to the tooltip and to the
 * accessible name — the same words `ConnectionPill` prints, from the same
 * branch, because two renderings of one status is how they come to
 * disagree. It stays a `role="status"` so a change is announced rather
 * than merely drawn.
 */
export function ConnectionDot() {
  const connection = useSessionStore((s) => s.connection);

  const look =
    connection.mode === "mock"
      ? { dot: "bg-muted-foreground/50", label: "Mock fixture" }
      : connection.state === "online"
        ? { dot: "bg-verified", label: "API online" }
        : connection.state === "connecting"
          ? { dot: "bg-warning animate-pulse", label: "Connecting…" }
          : { dot: "bg-negative", label: "API offline" };

  const detail =
    connection.mode === "api" && connection.state === "offline"
      ? connection.detail
        ? `${connection.detail} — Cannot reach the Revi API. Run "make dev" to start it.`
        : 'Cannot reach the Revi API. Run "make dev" to start it.'
      : connection.detail;

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          role="status"
          aria-label={look.label}
          tabIndex={0}
          className="focus-ring flex size-7 items-center justify-center rounded-md"
        >
          <span aria-hidden className={cn("size-2 rounded-full", look.dot)} />
        </span>
      </TooltipTrigger>
      <TooltipContent side="right" className="max-w-64 text-meta leading-snug">
        {detail ? `${look.label} — ${detail}` : look.label}
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * Amber badge, sat next to `ConnectionPill`, for degraded modes that are
 * easy to mistake for the live product: the scripted stub LLM answering
 * behind a live api connection, or the mock driver itself. Never both at
 * once — the two conditions are mutually exclusive by `connection.mode`.
 */
export function DegradedModeBadge() {
  const connection = useSessionStore((s) => s.connection);

  // Nothing until the deployment has actually answered. This badge used to
  // render on the very first paint — before any health poll — because the
  // store opened on `mode: "mock"`, so a live api deployment briefly and
  // wrongly announced itself as a demo script.
  if (!connection.healthChecked) return null;

  if (connection.mode === "mock") {
    return (
      <DegradedBadge
        label="Demo script mode"
        explanation="This is a demonstration script, not your data — it answers only the reference questions, and anything else gets a scripted reply. Ask whoever set this up to point it at the live deployment."
      />
    );
  }

  if (connection.llmMode === "scripted-demo") {
    return (
      // The badge face names the CONDITION; the environment variable that
      // fixes it is an operator's instruction and belongs in the
      // explanation, not on a chip in the header of an analyst's screen.
      <DegradedBadge
        label="Scripted answers"
        explanation="This deployment is running a stand-in for the language model rather than a real one — questions outside the reference script come back as a clarification instead of an answer. Ask whoever set this up to connect a real one."
      />
    );
  }

  return null;
}

/**
 * The explanation used to be a native `title`: mouse-only, with no
 * keyboard path and no touch equivalent — on the one badge in the product
 * whose whole job is to stop a viewer mistaking a scripted fixture for the
 * live system. It is a focusable Radix tooltip now, and the badge keeps
 * `role="status"` so the mode change is announced when it appears.
 */
function DegradedBadge({ label, explanation }: { label: string; explanation: string }) {
  return (
    <span role="status">
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            className="focus-ring inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-micro font-medium text-warning"
          >
            <span className="size-1.5 rounded-full bg-warning" />
            {label}
          </button>
        </TooltipTrigger>
        <TooltipContent side="bottom" className="max-w-80 text-meta leading-snug">
          {explanation}
        </TooltipContent>
      </Tooltip>
    </span>
  );
}
