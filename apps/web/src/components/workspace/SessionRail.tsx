"use client";

import {
  AlertTriangle,
  FlaskConical,
  Loader2,
  MessagesSquare,
  MessageSquarePlus,
  Play,
  RefreshCw,
} from "lucide-react";
import { useEffect } from "react";

import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { PortfolioPanel } from "@/components/portfolio/PortfolioPanel";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { apiBaseUrl } from "@/lib/apiDriver";
import { relativeTime } from "@/lib/format";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

export function SessionRail() {
  const simulateWatermarkRefresh = useSessionStore((s) => s.simulateWatermarkRefresh);
  const toggleFailurePreview = useSessionStore((s) => s.toggleFailurePreview);
  const showFailurePreview = useSessionStore((s) => s.showFailurePreview);
  const newChat = useSessionStore((s) => s.newChat);
  const newChatPending = useSessionStore((s) => s.newChatPending);
  const replayReference = useSessionStore((s) => s.replayReference);
  const replaying = useSessionStore((s) => s.replaying);
  const replayProgress = useSessionStore((s) => s.replayProgress);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const mode = useSessionStore((s) => s.connection.mode);
  const loadSessions = useSessionStore((s) => s.loadSessions);
  const driver = useSessionStore((s) => s.driver);
  const switchingSessionId = useSessionStore((s) => s.switchingSessionId);
  const newChatBusy = newChatPending || streaming || replaying || switchingSessionId !== null;

  // The list is a server read, so it starts on mount rather than on a
  // click: the rail's whole job is to show what already exists. Keyed on
  // the driver because the workspace wires one after the first paint —
  // reading before it exists would report "no deployment" about the app's
  // own startup order.
  useEffect(() => {
    if (driver) void loadSessions();
  }, [driver, loadSessions]);

  return (
    <aside className="panel flex h-full min-h-0 flex-col border-r">
      <div className="flex items-center justify-between px-4 py-3.5">
        <div className="flex items-center gap-2">
          <span className="accent-gradient flex size-6 items-center justify-center rounded-md font-mono text-sm font-bold text-white">
            R
          </span>
          <span className="text-[0.9rem] font-semibold tracking-tight">Revi</span>
          <span className="mt-0.5 text-[0.55rem] font-medium uppercase tracking-widest text-muted-foreground">
            RCM
          </span>
        </div>
        <ThemeToggle />
      </div>

      <div className="space-y-1.5 px-3 pb-3">
        <Button
          onClick={() => void newChat()}
          disabled={newChatBusy}
          size="sm"
          className="accent-gradient w-full gap-1.5 text-[0.72rem] font-medium text-white shadow-sm transition-all duration-150 hover:brightness-110 hover:shadow-md"
        >
          <MessageSquarePlus className="size-3" />
          New chat
        </Button>
        <Button
          onClick={() => void replayReference()}
          disabled={newChatBusy}
          variant="outline"
          size="sm"
          className="w-full gap-1.5 text-[0.72rem] font-medium"
        >
          <Play className="size-3" />
          {replayProgress
            ? `Replaying ${replayProgress.index}/${replayProgress.total}…`
            : "Replay reference demo"}
        </Button>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-5 px-3 pb-4">
          <SessionList />

          <Separator />
          <PortfolioPanel />

          {/*
            Fixture-only previews. Both of these fabricate state — a
            watermark that does not exist, a reconciliation failure that
            never happened — which is a useful thing to see against the
            mock fixture and a lie against a live deployment. They exist
            where the whole driver is already a fixture, and nowhere else.
          */}
          {mode === "mock" && (
            <>
              <Separator />
              <section className="space-y-1.5">
                <h3 className="flex items-center gap-1.5 px-1 text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
                  <FlaskConical className="size-3" />
                  Fixture previews
                </h3>
                <div className="space-y-1 px-1">
                  <Button
                    variant="outline"
                    size="xs"
                    className="w-full justify-start gap-1.5 text-[0.65rem] font-normal"
                    onClick={simulateWatermarkRefresh}
                  >
                    <RefreshCw className="size-3" />
                    Simulate a newer data load
                  </Button>
                  <Button
                    variant="outline"
                    size="xs"
                    className={cn(
                      "w-full justify-start gap-1.5 text-[0.65rem] font-normal",
                      showFailurePreview && "border-negative/50 text-negative",
                    )}
                    onClick={toggleFailurePreview}
                  >
                    {showFailurePreview ? "Hide" : "Preview"} reconciliation failure
                  </Button>
                </div>
              </section>
            </>
          )}
        </div>
      </ScrollArea>

      <div className="border-t px-4 py-2.5">
        <p className="num text-[0.58rem] leading-relaxed text-muted-foreground/70">
          {mode === "api" ? (
            <>
              Live API · {apiBaseUrl()}
              <br />
              This is the product — mock is a dev/test fixture
            </>
          ) : (
            <>
              Mock data · seed 20260807 · snap_003
              <br />
              Dev/test fixture — set NEXT_PUBLIC_REVI_DRIVER=api for the live API
            </>
          )}
        </p>
      </div>
    </aside>
  );
}

/**
 * The tenant's sessions, exactly as `GET /v1/sessions` lists them: each row
 * titled by the first question asked in it and dated by its last answered
 * turn. Clicking one re-joins it server-side and rebuilds its thread.
 *
 * There is no local fallback list. A driver with no deployment behind it
 * (the mock fixture) says so — inventing plausible titles here is what this
 * panel used to do, and every one of them was a dead button.
 */
function SessionList() {
  const sessions = useSessionStore((s) => s.sessions);
  const total = useSessionStore((s) => s.sessionsTotal);
  const state = useSessionStore((s) => s.sessionsState);
  const error = useSessionStore((s) => s.sessionsError);
  const switchError = useSessionStore((s) => s.switchError);
  const currentSessionId = useSessionStore((s) => s.sessionId);
  const switchingSessionId = useSessionStore((s) => s.switchingSessionId);
  const switchSession = useSessionStore((s) => s.switchSession);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const replaying = useSessionStore((s) => s.replaying);
  const newChatPending = useSessionStore((s) => s.newChatPending);
  // Switching mid-turn would abandon a stream whose answer is still
  // arriving, so the rows are inert until the pipeline is free.
  const busy = streaming || replaying || newChatPending || switchingSessionId !== null;

  return (
    <section className="space-y-1">
      <h3 className="flex items-center justify-between gap-1.5 px-1 text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <MessagesSquare className="size-3" />
          Sessions
        </span>
        {state === "ready" && total > sessions.length && (
          <span className="num text-[0.6rem] font-normal text-muted-foreground/70">
            {sessions.length} of {total}
          </span>
        )}
      </h3>

      {switchError && (
        <p className="flex items-start gap-1.5 px-1 text-[0.62rem] leading-snug text-negative">
          <AlertTriangle className="mt-0.5 size-3 shrink-0" />
          {switchError}
        </p>
      )}

      {state === "unavailable" ? (
        <p className="px-1 text-[0.62rem] leading-snug text-muted-foreground">{error}</p>
      ) : state !== "ready" && sessions.length === 0 ? (
        // Includes "idle" — before the read has answered, "no sessions" is
        // a claim the app has not earned yet.
        <p className="px-1 text-[0.62rem] leading-snug text-muted-foreground">Loading sessions…</p>
      ) : sessions.length === 0 ? (
        <p className="px-1 text-[0.62rem] leading-snug text-muted-foreground">
          No sessions yet. Ask a question and this one appears here.
        </p>
      ) : (
        <ul className="space-y-0.5">
          {sessions.map((session) => {
            const active = session.sessionId === currentSessionId;
            const pending = session.sessionId === switchingSessionId;
            return (
              <li key={session.sessionId}>
                <button
                  type="button"
                  disabled={busy && !pending}
                  aria-current={active ? "true" : undefined}
                  title={`${session.title} · ${session.turnCount} turn${
                    session.turnCount === 1 ? "" : "s"
                  } · last activity ${session.lastActivity}`}
                  onClick={() => void switchSession(session.sessionId)}
                  className={cn(
                    "flex w-full items-baseline justify-between gap-2 rounded-md px-2 py-1.5 text-left text-[0.7rem] transition-colors duration-150",
                    active
                      ? "bg-accent font-medium"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                    busy && !pending && "cursor-not-allowed opacity-50",
                  )}
                >
                  <span className="truncate">{session.title}</span>
                  <span className="num flex shrink-0 items-center gap-1 text-[0.6rem] text-muted-foreground/70">
                    {pending ? (
                      <Loader2 className="size-2.5 animate-spin" />
                    ) : (
                      relativeTime(session.lastActivity)
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
