"use client";

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
      <span className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border bg-surface-sunken px-2 py-0.5 text-[0.62rem] font-medium text-muted-foreground">
        <span className="size-1.5 rounded-full bg-muted-foreground/50" />
        Mock driver
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
        "inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[0.62rem] font-medium",
        look.pill,
      )}
    >
      <span className={cn("size-1.5 rounded-full", look.dot)} />
      {look.label}
    </span>
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

  if (connection.mode === "mock") {
    return (
      <span
        role="status"
        title="Mock is a dev/test fixture — it only matches the reference demo questions, and everything else gets a scripted clarification. The live API is the product; set NEXT_PUBLIC_REVI_DRIVER=api (the default) to use it."
        className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-[0.62rem] font-medium text-warning"
      >
        <span className="size-1.5 rounded-full bg-warning" />
        Demo script mode
      </span>
    );
  }

  if (connection.llmMode === "scripted-demo") {
    return (
      <span
        role="status"
        title="The API is running the scripted stub LLM, not a live model — free-form questions off the reference script get a clarification. Set REVI_MODEL_PIN and restart the API."
        className="inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-warning/40 bg-warning/10 px-2 py-0.5 text-[0.62rem] font-medium text-warning"
      >
        <span className="size-1.5 rounded-full bg-warning" />
        Scripted LLM — set REVI_MODEL_PIN
      </span>
    );
  }

  return null;
}
