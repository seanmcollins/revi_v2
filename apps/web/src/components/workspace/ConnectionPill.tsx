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
      title={connection.detail}
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
