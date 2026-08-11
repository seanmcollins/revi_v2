"use client";

import { AlertTriangle, Command, Settings2 } from "lucide-react";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { CommandPalette } from "@/components/command/CommandPalette";
import { ResearchEvidence } from "@/components/research/ResearchEvidence";
import { ResearchProgress } from "@/components/research/ResearchProgress";
import { ResearchReportView } from "@/components/research/ResearchReport";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ConnectionPill, DegradedModeBadge } from "@/components/workspace/ConnectionPill";
import { SessionRail } from "@/components/workspace/SessionRail";
import { ApiDriver, fetchHealthDetail, resolveDriverKind } from "@/lib/apiDriver";
import { envDriverKind, type DriverKind } from "@/lib/driver";
import { isRunning, populationLabel } from "@/lib/deepResearch";
import { useSessionStore } from "@/lib/store";
import { useDeepResearchRun } from "@/lib/useDeepResearch";

const noopSubscribe = () => () => {};

/**
 * ONE RUN, AT ITS OWN ADDRESS.
 *
 * `/r/{run_id}` is the waiting room and the report, in that order, without
 * the reader doing anything: the run's own GET says where it has got to,
 * the stream carries it the rest of the way, and this surface renders
 * whichever of the two states the run is in. That is why it is one route
 * and not two — a link handed to somebody mid-run has to still be the link
 * to the report when they open it.
 *
 * IT WEARS THE APP'S OWN SHELL. The session rail on the left (a run IS a
 * session server-side, so the rail lists it and can navigate away from
 * it), the same header with the connection pill and the same two
 * top-right controls, and — at the width the workspace reserves for it —
 * the Evidence rail on the right, carrying the per-angle working. Below
 * that width the same evidence renders inside the report, so nothing is
 * lost on a narrow screen; the two mounts are the same component with
 * complementary visibility, never both in the accessible tree at once.
 *
 * THE DRIVER IS WIRED HERE for the same reason the workspace wires it: the
 * session rail is a store consumer and reads `GET /v1/sessions` through
 * the driver seam, so a surface that mounts the rail has to give the store
 * something to read with. The health poll is the same one, so the
 * connection pill on this page tells the truth rather than sitting at
 * "connecting" forever.
 */
export function ResearchSurface({ runId }: { runId: string }) {
  const driverKind = useSyncExternalStore<DriverKind>(
    noopSubscribe,
    resolveDriverKind,
    envDriverKind,
  );
  const driver = useMemo(
    () =>
      new ApiDriver({
        onSession: (session) => useSessionStore.getState().adoptSession(session),
        onConnectionState: (state, detail) =>
          useSessionStore.getState().setConnection({ state, detail }),
        onContractDrift: (paths) => useSessionStore.getState().reportContractDrift(paths),
      }),
    [],
  );
  const setDriver = useSessionStore((s) => s.setDriver);
  const openSettings = useSessionStore((s) => s.openSettings);
  const hydrateSettings = useSessionStore((s) => s.hydrateSettings);
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    if (driverKind === "api") setDriver(driver);
  }, [driver, driverKind, setDriver]);
  useEffect(() => {
    hydrateSettings();
  }, [hydrateSettings]);

  useEffect(() => {
    const { setConnection } = useSessionStore.getState();
    if (driverKind !== "api") {
      setConnection({ mode: "mock", state: "online", healthChecked: true });
      return;
    }
    setConnection({ mode: "api", state: "connecting" });
    let cancelled = false;
    let timer: number | undefined;
    const poll = async (): Promise<void> => {
      const health = await fetchHealthDetail();
      if (cancelled) return;
      setConnection({
        state: health.ok ? "online" : "offline",
        llmMode: health.llmMode,
        storeMode: health.storeMode,
        authMode: health.authMode,
        newestWatermarkId: health.watermarkId,
        healthChecked: true,
      });
      timer = window.setTimeout(() => void poll(), health.ok ? 30_000 : 5_000);
    };
    void poll();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [driverKind]);

  const { state, loading, error } = useDeepResearchRun(runId, driverKind === "api");
  const report = state?.run.report;
  const population = state ? populationLabel(state.run.population) : "";

  return (
    <div className="relative h-dvh overflow-hidden bg-background">
      <div aria-hidden className="page-glow pointer-events-none absolute inset-0" />

      <div className="relative grid h-full grid-cols-[16.5rem_minmax(0,1fr)] xl:grid-cols-[16.5rem_minmax(0,1fr)_19rem]">
        <SessionRail />

        <main className="flex h-full min-h-0 flex-col">
          <header className="flex shrink-0 items-center justify-between gap-4 border-b bg-background/55 px-6 py-2.5 backdrop-blur-md">
            <div className="min-w-0">
              <h1 className="truncate text-body font-semibold tracking-tight">
                {report ? "Deep research report" : "Deep research"}
              </h1>
              <p className="num truncate text-micro text-muted-foreground">
                {population !== "" ? population : "Reading this run…"}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              <ConnectionPill />
              <DegradedModeBadge />
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={openSettings}
                    aria-label="Open settings"
                    className="focus-ring flex items-center rounded-md border bg-surface-sunken/70 px-1.5 py-1 text-micro font-medium text-muted-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground"
                  >
                    <Settings2 className="size-3" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="text-meta">
                  Settings · internal
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => setPaletteOpen(true)}
                    aria-label="Open command palette"
                    className="focus-ring flex items-center gap-1 rounded-md border bg-surface-sunken/70 px-1.5 py-1 text-micro font-medium text-muted-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground"
                  >
                    <Command className="size-3" />
                    <span className="font-mono">K</span>
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="text-meta">
                  Command palette · ⌘K
                </TooltipContent>
              </Tooltip>
            </div>
          </header>

          {/* `.answer-column` so the report's charts measure themselves
              against the same container the answer surface's do — the
              width-aware axis machinery reads its own container and this
              is what gives it one. */}
          {/* WIDER THAN AN ANSWER, and deliberately. The thread caps at
              `max-w-3xl` because a conversation is prose and prose has a
              reading measure; this surface's centre of gravity is a
              six-column table of every population the run priced or
              refused, plus eight figures. The PROSE still caps itself at
              its own measure (`max-w-[68ch]` inside the report), so the
              writing is unchanged and only the data spends the room. */}
          <div className="answer-column min-h-0 flex-1 overflow-y-auto">
            <div className="mx-auto w-full max-w-5xl px-6 py-6">
              {driverKind !== "api" ? (
                <p className="max-w-[64ch] text-body leading-relaxed text-muted-foreground">
                  Deep research is the live API&apos;s. This browser is running the mock fixture,
                  which has no data to research and no runs to read.
                </p>
              ) : error !== null ? (
                <p
                  role="alert"
                  className="flex max-w-[64ch] items-start gap-1.5 text-meta leading-snug text-negative"
                >
                  <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
                  <span>
                    {error} Nothing here is out of date — there is nothing here.
                  </span>
                </p>
              ) : loading || state === null ? (
                <p role="status" aria-live="polite" className="text-body text-muted-foreground">
                  Reading this run…
                </p>
              ) : report && !isRunning(state.run.status) ? (
                <ResearchReportView report={report} runId={state.run.id} />
              ) : (
                <ResearchProgress state={state} />
              )}
            </div>
          </div>
        </main>

        {/* THE EVIDENCE RAIL. A real column at the width the workspace
            reserves for one, carrying the per-angle working: what each
            angle read, how much was in scope, and how many cells it
            refused. Below that width the same component renders inside the
            report (see `ResearchReportView`), so the working is never
            unreachable — only relocated. */}
        <aside className="hidden min-h-0 flex-col overflow-y-auto border-l panel px-4 py-4 xl:flex">
          {report ? (
            <ResearchEvidence evidence={report.evidence ?? []} />
          ) : (
            <p className="text-meta leading-snug text-muted-foreground">
              The working appears here — what each angle read, and how many populations it could
              not publish a rate for — once the run has finished measuring.
            </p>
          )}
        </aside>
      </div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <SettingsPanel />
    </div>
  );
}
