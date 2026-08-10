"use client";

import { Command, Link as LinkIcon, Settings2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useNavigate } from "react-router-dom";

import { CopyTextButton } from "@/components/answer/AnswerActions";
import { ChatThread } from "@/components/chat/ChatThread";
import { TurnInput } from "@/components/chat/TurnInput";
import { CommandPalette } from "@/components/command/CommandPalette";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { ConnectionPill, DegradedModeBadge } from "@/components/workspace/ConnectionPill";
import { ContextPanel } from "@/components/workspace/ContextPanel";
import { SessionRail } from "@/components/workspace/SessionRail";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ApiDriver, fetchHealthDetail, resolveDriverKind } from "@/lib/apiDriver";
import { envDriverKind } from "@/lib/driver";
import type { DriverKind, TurnDriver } from "@/lib/driver";
import { displaySessionTitle, mediumDate, untitledTurnLabel } from "@/lib/format";
import { sessionLinkFor } from "@/lib/links";
import { REFERENCE_QUESTIONS } from "@/lib/mock/reference";
import { MockDriver } from "@/lib/mockDriver";
import { hasUnseenLoad, noteMonitorsRedirect } from "@/lib/monitorsVisit";
import { sessionLinkDisclosure } from "@/lib/shareDisclosure";
import { useSessionStore } from "@/lib/store";

/**
 * The Revi workspace: left rail (sessions + portfolio), center thread,
 * right contextual panel (evidence + lineage). Desktop tool — designed
 * down to 1280px. Keyboard-first: ⌘K opens the command palette.
 *
 * Driver selection: VITE_REVI_DRIVER=mock|api (default api — the
 * live product; mock is a dev/test fixture, not a user-facing mode). A
 * client-side localStorage override exists for tooling but is no longer
 * written by any casual user-facing control. Both drivers speak the same
 * TurnEvent seam; api mode adds session bootstrap, the health-checked
 * connection pill, and live portfolio/lineage fetches.
 *
 * Mounted by two routes: `/` (whatever session this browser is in) and
 * `/s/{session_id}` (that one). The second is the permalink the archive
 * dialog has been promising — see `initialSessionId` and `SessionLink`.
 */
const noopSubscribe = () => () => {};

export default function Workspace({
  initialSessionId,
  initialInvestigationId,
}: { initialSessionId?: string; initialInvestigationId?: string } = {}) {
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
  /**
   * Gated on `pinned` — i.e. on a session existing at all.
   *
   * "New chat" clears the thread but leaves the abandoned session's id in
   * the store until the next turn mints a replacement, so this lookup kept
   * FINDING the discarded session in the rail's list and putting its
   * question back in the H1 over an empty composer. The line beneath it
   * said "New chat — the data load pins when you ask your first question"
   * at the same time. One of them was wrong and it was this one.
   */
  const serverTitle = pinned
    ? sessions.find((s) => s.sessionId === sessionId)?.title
    : undefined;
  const sessionTitle = firstTurn
    ? (firstTurn.submission.utterance ?? untitledTurnLabel(firstTurn.submission))
    : serverTitle
      ? displaySessionTitle(serverTitle)
      : "New session";

  useEffect(() => {
    setDriver(driver);
  }, [driver, setDriver]);

  /**
   * A link that opens the investigation it names.
   *
   * `/s/{session_id}` re-joins the session server-side and rebuilds its
   * thread — which `switchSession` already did for a click in the rail; all
   * this adds is that the id can arrive from a URL. It runs once per id:
   * `switchSession` is a no-op for a session already on screen, and a
   * failure lands on the rail's own error line rather than a blank page.
   *
   * The driver is wired on the first effect pass, so this waits for it —
   * asking a store with no driver to open a session would report "no
   * deployment" about this app's own startup order.
   */
  const switchSession = useSessionStore((s) => s.switchSession);
  const openInvestigation = useSessionStore((s) => s.openInvestigation);
  const opened = useRef<string | null>(null);
  useEffect(() => {
    // `/i/{iid}` resolves to the session that turn belongs to and opens
    // that — a turn read outside its conversation has lost the filters and
    // the cohort that made it mean what it means.
    const target = initialSessionId ?? initialInvestigationId;
    if (!target || !driver) return;
    if (opened.current === target) return;
    opened.current = target;
    void (initialSessionId
      ? switchSession(initialSessionId)
      : openInvestigation(initialInvestigationId as string));
  }, [initialSessionId, initialInvestigationId, driver, switchSession, openInvestigation]);

  /**
   * Keep the address bar pointing at what is on screen.
   *
   * A session is minted by the first turn, so the URL cannot be right
   * before then — and once it exists, the analyst who wants to send this
   * thread to a CFO should be able to use the browser's own address bar
   * rather than hunt for a button. `replace` (not a push) because a
   * session becoming addressable is not a navigation the back button
   * should have to undo.
   *
   * This was a raw `window.history.replaceState`, which Next's router
   * observed. React Router does NOT observe raw history writes — its
   * `useLocation` consumers (the rail's "you are here" on Monitors, most
   * visibly) would go on reporting the previous path forever. So the write
   * goes through the router, and the read that guards it stays on
   * `window.location`: the router calls `history.replaceState`
   * synchronously, so the two are never out of step, and comparing against
   * the real address bar is what keeps this from fighting a permalink that
   * is already correct.
   *
   * The route element is deliberately the same component for `/`, `/s/:id`
   * and `/i/:id` (see `App.tsx`), so this navigation reconciles rather than
   * remounting — the same non-event `replaceState` was.
   */
  const navigate = useNavigate();
  const wasLive = useRef(false);
  useEffect(() => {
    if (sessionLive && sessionId) {
      wasLive.current = true;
      const path = `/s/${encodeURIComponent(sessionId)}`;
      if (window.location.pathname !== path) navigate(path, { replace: true });
      return;
    }
    // "New chat" leaves a real gap with no session in it (`sessionLive:
    // false` — the server mints one on the first turn), so the address bar
    // goes back to `/` rather than keeping a link to the thread that was
    // just discarded. Gated on having BEEN live: on a cold `/s/…` load the
    // store is not live yet either, and rewriting there would throw away
    // the very link that was opened.
    if (!wasLive.current) return;
    wasLive.current = false;
    if (window.location.pathname !== "/") navigate("/", { replace: true });
  }, [sessionLive, sessionId, navigate]);

  // Persisted settings are read on the CLIENT only: the server render has
  // no localStorage, and hydrating from it during render would mismatch.
  useEffect(() => {
    hydrateSettings();
  }, [hydrateSettings]);

  /**
   * BRIEF-FIRST COLD START.
   *
   * When a data load has landed that this browser has not been briefed on,
   * the app opens on Monitors. That is the whole product claim made
   * structural: Revi walks your Monitors every load and tells you what
   * changed, so the first thing on screen is what changed — not an empty
   * composer waiting to be asked.
   *
   * Three things it will not do, and each of them is a way this pattern
   * usually goes wrong:
   *
   *   IT NEVER OVERRIDES A LINK. A permalink is somebody being sent
   *     somewhere specific — `initialSessionId` and `initialInvestigationId`
   *     are exactly that — and redirecting past it would break the one
   *     promise the archive dialog makes in writing. Only the bare `/`
   *     route redirects.
   *   IT NEVER INTERRUPTS WORK. A thread already on screen (a resumed
   *     session, a turn just asked) is not swapped out from under the
   *     analyst; the rail's Monitors link carries the dot instead.
   *   IT HAPPENS ONCE. `redirected` latches, so hitting Back from Monitors
   *     returns here and stays here.
   */
  const newestWatermarkId = useSessionStore((s) => s.connection.newestWatermarkId);
  const redirected = useRef(false);
  useEffect(() => {
    if (redirected.current) return;
    if (connectionMode !== "api") return;
    if (initialSessionId || initialInvestigationId) return;
    if (turns.length > 0 || sessionLive) return;
    if (window.location.pathname !== "/") return;
    if (!hasUnseenLoad(newestWatermarkId)) return;
    redirected.current = true;
    // Recorded before the push so the destination can announce itself and
    // move focus: a navigation nobody asked for is silent to a screen
    // reader otherwise, and this is the one navigation this app makes on
    // the analyst's behalf.
    noteMonitorsRedirect();
    // A router push, not `location.assign`: a client-side navigation keeps
    // the store, the driver and the health poll this component just set
    // up, so Monitors opens without re-bootstrapping everything it needs.
    // A PUSH and not a replace — Back from Monitors has to return here,
    // and the `redirected` latch above is what stops it bouncing straight
    // back out again.
    navigate("/monitors");
  }, [
    navigate,
    connectionMode,
    initialSessionId,
    initialInvestigationId,
    turns.length,
    sessionLive,
    newestWatermarkId,
  ]);

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
              <h1 className="truncate text-body font-semibold tracking-tight">
                {sessionTitle}
              </h1>
              {/* Default: the two facts an analyst acts on — how fresh the
                  data is and how far it runs. The pinned watermark id and
                  pack version are engine vocabulary and live in debug mode
                  and the settings panel's effective-configuration block.
                  Before a session exists there is no pin to state, and the
                  line says that rather than showing a date it cannot
                  stand behind — see `pinned`. */}
              <p className="num truncate text-micro text-muted-foreground">
                {pinned ? (
                  <>
                    {/* BUG 4 — how far the data runs, not the minute the
                        loader finished. "loaded 2026-08-03 04:10" is a
                        machine instant, and it was the second thing on
                        the page: on a 1280px header it truncated to
                        "loaded 2026-…" anyway, which is a timestamp
                        rendered as noise. It stays available beside the
                        data-load id in debug and in the settings panel's
                        effective-configuration block. */}
                    Data through {mediumDate(watermark.newestDataDate)}
                    {debug &&
                      ` · loaded ${watermark.loadedAt} · ${watermark.id} · ${pack.packId}@${pack.version}`}
                  </>
                ) : (
                  <>New chat — the data load pins when you ask your first question</>
                )}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              {/* The answer to the first question every buyer asks in a
                  demo: can an analyst send this to the CFO or paste it
                  into a ticket. It appears only once there is a session
                  server-side to link TO — a link minted before the first
                  turn would resolve to nothing. */}
              {sessionLive && sessionId && <SessionLink sessionId={sessionId} />}
              <ConnectionPill />
              <DegradedModeBadge />
              <p className="num whitespace-nowrap text-micro text-muted-foreground">
                {turns.length} turn{turns.length === 1 ? "" : "s"}
              </p>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    onClick={openSettings}
                    aria-label="Open settings"
                    className="flex items-center rounded-md border bg-surface-sunken/70 px-1.5 py-1 text-micro font-medium text-muted-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground"
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
                    className="flex items-center gap-1 rounded-md border bg-surface-sunken/70 px-1.5 py-1 text-micro font-medium text-muted-foreground transition-colors duration-150 hover:border-ring/40 hover:text-foreground"
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

/**
 * The permalink, and the honest thing to say about it — BEFORE it is
 * copied.
 *
 * `/s/{session_id}` is a link to a session, not to a snapshot: it re-joins
 * the session and rebuilds it from what the server kept, so it shows the
 * thread as it stands when it is opened — including turns asked after the
 * link was sent.
 *
 * That much was already on a tooltip. What was not is the half that
 * decides whether the link is worth sending: what the server kept is not
 * what the live turn published. Measured live, a shared answer opened cold
 * renders "The written analysis was not stored for this turn" in place of
 * the two thousand words everyone in the room had just read — and the
 * operator learned that from the CFO. A tooltip is also the wrong carrier
 * for it: it needs a hover, so on a touch screen and for a keyboard reader
 * the disclosure did not exist.
 *
 * So the button opens the disclosure and the disclosure copies the link:
 * two clicks, and the second one is informed. The lists are derived — see
 * `sessionLinkDisclosure`, which reports what this browser has watched
 * come back from the server rather than what this build believes the
 * server stores.
 */
export function SessionLink({ sessionId }: { sessionId: string }) {
  const turns = useSessionStore((s) => s.turns);
  const disclosure = useMemo(
    () =>
      sessionLinkDisclosure(
        turns.map((turn) => ({
          rehydrated: turn.answer.rehydrated === true,
          narrative: turn.answer.narrative,
          findings: turn.answer.findings.length,
          charts: turn.answer.charts.length,
          hasEvidence: turn.answer.evidence !== undefined,
        })),
      ),
    [turns],
  );

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="focus-ring flex h-5 items-center gap-1 rounded-full px-2 text-micro font-medium text-muted-foreground transition-colors duration-150 hover:text-foreground"
        >
          <LinkIcon aria-hidden className="size-3" />
          Copy link
        </button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[24rem] max-w-[calc(100vw-2rem)] p-3.5">
        <p className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
          What this link opens
        </p>
        <p className="mt-1.5 text-meta leading-snug">{disclosure.lead}</p>

        <p className="mt-2.5 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
          It carries
        </p>
        <ul className="mt-1 space-y-1">
          {disclosure.included.map((line) => (
            <li
              key={line}
              className="flex gap-1.5 text-meta leading-snug text-foreground/85"
            >
              <span aria-hidden className="text-muted-foreground">
                ·
              </span>
              <span>{line}</span>
            </li>
          ))}
        </ul>

        <p className="mt-2.5 text-micro font-semibold uppercase tracking-wide text-muted-foreground">
          It does not carry
        </p>
        <ul className="mt-1 space-y-1">
          {disclosure.omitted.map((line) => (
            <li key={line} className="flex gap-1.5 text-meta leading-snug text-muted-foreground">
              <span aria-hidden>·</span>
              <span>{line}</span>
            </li>
          ))}
        </ul>

        {/* Where the two lists came from. A disclosure has to say whether
            it MEASURED or assumed: the claim that answers restore with
            their prose was made by reading a restored turn in the tab that
            created it, where the client store still held the prose the
            server had not kept. */}
        <p
          data-disclosure-basis={disclosure.basis}
          className="mt-2.5 text-micro leading-snug text-muted-foreground"
        >
          {disclosure.basis === "observed"
            ? "Measured: this is what came back when a turn in this session was re-read from the server."
            : "Not yet measured on this session — every turn here was watched live, and this browser still holds what the server may not."}
        </p>

        <div className="mt-2.5 border-t pt-2.5">
          <CopyTextButton
            label="Copy the link"
            doneLabel="Link copied"
            title="Copy a link to this session"
            className="h-6 px-2 text-meta"
            text={() =>
              sessionLinkFor(
                sessionId,
                typeof window === "undefined" ? "" : window.location.origin,
              )
            }
          />
        </div>
      </PopoverContent>
    </Popover>
  );
}
