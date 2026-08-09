"use client";

import { Command, Settings2 } from "lucide-react";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { ChatThread } from "@/components/chat/ChatThread";
import { TurnInput } from "@/components/chat/TurnInput";
import { CommandPalette } from "@/components/command/CommandPalette";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { ConnectionPill, DegradedModeBadge } from "@/components/workspace/ConnectionPill";
import { ContextPanel } from "@/components/workspace/ContextPanel";
import { SessionRail } from "@/components/workspace/SessionRail";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiDriver, fetchHealthDetail, resolveDriverKind } from "@/lib/apiDriver";
import { envDriverKind } from "@/lib/driver";
import type { DriverKind, TurnDriver } from "@/lib/driver";
import { displaySessionTitle, mediumDate, untitledTurnLabel } from "@/lib/format";
import { REFERENCE_QUESTIONS } from "@/lib/mock/reference";
import { MockDriver } from "@/lib/mockDriver";
import { useSessionStore } from "@/lib/store";

/**
 * The Revi workspace: left rail (sessions + portfolio), center thread,
 * right contextual panel (evidence + lineage). Desktop tool — designed
 * down to 1280px. Keyboard-first: ⌘K opens the command palette.
 *
 * Driver selection: NEXT_PUBLIC_REVI_DRIVER=mock|api (default api — the
 * live product; mock is a dev/test fixture, not a user-facing mode). A
 * client-side localStorage override exists for tooling but is no longer
 * written by any casual user-facing control. Both drivers speak the same
 * TurnEvent seam; api mode adds session bootstrap, the health-checked
 * connection pill, and live portfolio/lineage fetches.
 */
const noopSubscribe = () => () => {};

export default function Workspace() {
  // Hydration-safe driver selection: the server snapshot is the env
  // default; the client snapshot honors the palette's localStorage
  // override (which reloads the page on change — no live subscription).
  const driverKind = useSyncExternalStore<DriverKind>(
    noopSubscribe,
    resolveDriverKind,
    envDriverKind,
  );

  const driver = useMemo<TurnDriver>(() => {
    if (driverKind === "api") {
      return new ApiDriver({
        onSession: (session) => useSessionStore.getState().adoptSession(session),
        onConnectionState: (state, detail) =>
          useSessionStore.getState().setConnection({ state, detail }),
        onContractDrift: (paths) => useSessionStore.getState().reportContractDrift(paths),
      });
    }
    return new MockDriver();
  }, [driverKind]);
  const setDriver = useSessionStore((s) => s.setDriver);
  const turns = useSessionStore((s) => s.turns);
  const watermark = useSessionStore((s) => s.watermark);
  const pack = useSessionStore((s) => s.pack);
  const openSettings = useSessionStore((s) => s.openSettings);
  const hydrateSettings = useSessionStore((s) => s.hydrateSettings);
  const debug = useSessionStore((s) => s.settings.debug);
  const sessionId = useSessionStore((s) => s.sessionId);
  const sessions = useSessionStore((s) => s.sessions);
  const llmMode = useSessionStore((s) => s.connection.llmMode);
  const sessionLive = useSessionStore((s) => s.sessionLive);
  const connectionMode = useSessionStore((s) => s.connection.mode);
  /**
   * Is there a session for this pin to belong to?
   *
   * A watermark is a property of a SESSION — the data load it was pinned
   * to when it opened. In api mode a session is minted by the first turn
   * and by nothing else, so on a cold start and across "New chat" there is
   * a real interval with no session at all. The header used to print the
   * store's seed constant through that interval: a specific date and load
   * time, in the analyst's most-trusted line, describing a pin that either
   * had not been chosen yet or belonged to a thread just discarded.
   *
   * Mock mode is exempt because its seed constant IS its watermark — the
   * fixture has one load and always did.
   */
  const pinned = connectionMode !== "api" || sessionLive;
  const [paletteOpen, setPaletteOpen] = useState(false);

  // What this session is called: the first question asked in it — the
  // same derivation the server uses for the rail. The thread's own first
  // turn is preferred because it is already on screen (no waiting on a
  // list refresh) and because its submission says plainly whether it was
  // typed or a gesture; the server's title covers a rejoined session, and
  // one with no turns at all is honestly nameless.
  const firstTurn = turns[0];
  const serverTitle = sessions.find((s) => s.sessionId === sessionId)?.title;
  const sessionTitle = firstTurn
    ? (firstTurn.submission.utterance ?? untitledTurnLabel(firstTurn.submission))
    : serverTitle
      ? displaySessionTitle(serverTitle)
      : "New session";

  useEffect(() => {
    setDriver(driver);
  }, [driver, setDriver]);

  // Persisted settings are read on the CLIENT only: the server render has
  // no localStorage, and hydrating from it during render would mismatch.
  useEffect(() => {
    hydrateSettings();
  }, [hydrateSettings]);

  // Connection state machine (api mode): connecting → online ⇄ offline,
  // driven by a health heartbeat — fast retries while offline, slow while
  // online. Turn submission failures flip it offline via the driver.
  useEffect(() => {
    const { setConnection } = useSessionStore.getState();
    if (driverKind !== "api") {
      setConnection({ mode: "mock", state: "online", detail: undefined, healthChecked: true });
      return;
    }
    setConnection({ mode: "api", state: "connecting" });
    let cancelled = false;
    let timer: number | undefined;
    const probe = async (): Promise<void> => {
      const health = await fetchHealthDetail();
      if (cancelled) return;
      setConnection({
        state: health.ok ? "online" : "offline",
        llmMode: health.llmMode,
        storeMode: health.storeMode,
        authMode: health.authMode,
        newestWatermarkId: health.watermarkId,
        // The badge is a claim about the deployment; it waits for the
        // deployment to have answered.
        healthChecked: true,
      });
      timer = window.setTimeout(() => void probe(), health.ok ? 30_000 : 5_000);
    };
    void probe();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [driverKind]);

  const answeredReference = turns.filter((t) =>
    REFERENCE_QUESTIONS.includes(t.submission.utterance ?? ""),
  ).length;

  // Composer suggestions are the reference drill-down, so they belong
  // where that script is what actually answers: the mock fixture, or a
  // deployment running the scripted stub LLM. Against a live model they
  // were a fixed script pointer dressed as a contextual follow-up — the
  // real follow-ups are the typed refinements each finding carries, which
  // the answer card already renders next to the number they came from.
  const scriptedAnswers = driverKind === "mock" || llmMode === "scripted-demo";
  const suggestions =
    turns.length === 0 || !scriptedAnswers
      ? []
      : answeredReference < REFERENCE_QUESTIONS.length
        ? [REFERENCE_QUESTIONS[answeredReference]]
        : ["What is PR3?"];

  return (
    <div className="relative h-dvh overflow-hidden bg-background">
      <div aria-hidden className="page-glow pointer-events-none absolute inset-0" />

      <div className="relative grid h-full grid-cols-[16.5rem_minmax(0,1fr)_21rem] min-[1440px]:grid-cols-[17.5rem_minmax(0,1fr)_23rem]">
        <SessionRail />

        <main className="flex h-full min-h-0 flex-col">
          <header className="flex shrink-0 items-center justify-between gap-4 border-b bg-background/55 px-6 py-2.5 backdrop-blur-md">
            <div className="min-w-0">
              {/* The session's own name: the first question asked in it, as
                  the server derives it for the rail. It used to be a fixed
                  string ("Cash decline — week of Jul 27") that outlived
                  whatever was actually on screen. */}
              <h1 className="truncate text-[0.85rem] font-semibold tracking-tight">
                {sessionTitle}
              </h1>
              {/* Default: the two facts an analyst acts on — how fresh the
                  data is and how far it runs. The pinned watermark id and
                  pack version are engine vocabulary and live in debug mode
                  and the settings panel's effective-configuration block.
                  Before a session exists there is no pin to state, and the
                  line says that rather than showing a date it cannot
                  stand behind — see `pinned`. */}
              <p className="num truncate text-[0.62rem] text-muted-foreground">
                {pinned ? (
                  <>
                    Data through {mediumDate(watermark.newestDataDate)} · loaded{" "}
                    {watermark.loadedAt}
                    {debug && ` · ${watermark.id} · ${pack.packId}@${pack.version}`}
                  </>
                ) : (
                  <>New chat — the data load pins when you ask your first question</>
                )}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              <ConnectionPill />
              <DegradedModeBadge />
              <p className="num whitespace-nowrap text-[0.62rem] text-muted-foreground">
                {turns.length} turn{turns.length === 1 ? "" : "s"}
              </p>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={openSettings}
                    aria-label="Open settings"
                    className="flex items-center rounded-md border bg-surface-sunken/70 px-1.5 py-1 text-[0.62rem] font-medium text-muted-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground"
                  >
                    <Settings2 className="size-3" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="text-[0.65rem]">
                  Settings · internal
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={() => setPaletteOpen(true)}
                    aria-label="Open command palette"
                    className="flex items-center gap-1 rounded-md border bg-surface-sunken/70 px-1.5 py-1 text-[0.62rem] font-medium text-muted-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground"
                  >
                    <Command className="size-3" />
                    <span className="font-mono">K</span>
                  </button>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="text-[0.65rem]">
                  Command palette · ⌘K
                </TooltipContent>
              </Tooltip>
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto">
            <ChatThread />
          </div>

          <footer className="shrink-0 border-t bg-background/55 px-6 py-3 backdrop-blur-md">
            <div className="mx-auto max-w-3xl">
              <TurnInput suggestions={suggestions} />
            </div>
          </footer>
        </main>

        <ContextPanel />
      </div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <SettingsPanel />
    </div>
  );
}
