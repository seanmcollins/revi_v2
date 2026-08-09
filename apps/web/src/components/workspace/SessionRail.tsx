"use client";

import {
  AlertTriangle,
  Archive,
  FlaskConical,
  Loader2,
  MessagesSquare,
  MessageSquarePlus,
  Play,
  RefreshCw,
  Search,
} from "lucide-react";
import { useEffect, useState } from "react";

import { CopyTextButton } from "@/components/answer/AnswerActions";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { PortfolioPanel } from "@/components/portfolio/PortfolioPanel";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { apiBaseUrl } from "@/lib/apiDriver";
import { displaySessionTitle, relativeTime } from "@/lib/format";
import { sessionLinkFor } from "@/lib/links";
import { REFERENCE_QUESTIONS } from "@/lib/mock/reference";
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
          {/* The mark carries a letter, so it takes the text-safe stops —
              a logotype is exempt from AA, but it sits 40px above a CTA
              painted from the same pair and two different teals there
              read as a rendering bug. */}
          <span className="accent-gradient-cta flex size-6 items-center justify-center rounded-md font-mono text-sm font-bold text-white">
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
          // The app's most prominent button, and the one whose label was
          // hardest to read: white on the display gradient measured
          // 3.74:1 light and 2.49:1 dark. The CTA stops carry the same
          // white at 5.21:1 → 5.48:1 across the sweep, in both themes.
          className="accent-gradient-cta w-full gap-1.5 text-[0.72rem] font-medium text-white shadow-sm transition-all duration-150 hover:brightness-110 hover:shadow-md"
        >
          <MessageSquarePlus className="size-3" />
          New chat
        </Button>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-5 px-3 pb-4">
          <SessionList />

          {/* Below the sessions, not above them: this is a demo utility,
              and sitting beside "New chat" it read as a peer action —
              one click from wiping an open investigation and spending
              five live model turns doing it. */}
          <ReplayDemoButton
            disabled={newChatBusy}
            progress={replayProgress}
            onReplay={() => void replayReference()}
          />

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
        <p className="num text-[0.58rem] leading-relaxed text-muted-foreground">
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
 * The reference-demo replay, with its two real costs stated before the
 * click rather than discovered after it: it spends five live model turns,
 * and it starts a new chat — which clears whatever is open in the thread.
 *
 * The confirmation only appears when there is something to lose. A first
 * click on an empty workspace runs immediately; a first click over an open
 * investigation asks, because "New chat" is called inside `replayReference`
 * and there is no undo behind it.
 */
function ReplayDemoButton({
  disabled,
  progress,
  onReplay,
}: {
  disabled: boolean;
  progress: { index: number; total: number } | null;
  onReplay: () => void;
}) {
  const hasOpenThread = useSessionStore((s) => s.turns.length > 0);
  const [confirming, setConfirming] = useState(false);

  if (confirming) {
    return (
      <section className="space-y-1.5 rounded-md border border-warning/40 bg-warning/10 p-2">
        <p className="text-[0.62rem] leading-snug">
          Replaying starts a new chat — this thread is cleared and cannot be brought back. It
          then runs {REFERENCE_QUESTIONS.length} live turns.
        </p>
        <div className="flex gap-1.5">
          <Button
            size="xs"
            variant="secondary"
            className="h-6 flex-1 text-[0.65rem] font-medium"
            onClick={() => {
              setConfirming(false);
              onReplay();
            }}
          >
            Discard and replay
          </Button>
          <Button
            size="xs"
            variant="ghost"
            className="h-6 flex-1 text-[0.65rem] font-normal"
            onClick={() => setConfirming(false)}
          >
            Keep this thread
          </Button>
        </div>
      </section>
    );
  }

  return (
    <section className="space-y-1">
      <Button
        onClick={() => (hasOpenThread ? setConfirming(true) : onReplay())}
        disabled={disabled}
        variant="outline"
        size="sm"
        className="w-full gap-1.5 text-[0.72rem] font-medium"
      >
        <Play className="size-3" />
        {progress ? `Replaying ${progress.index}/${progress.total}…` : "Replay reference demo"}
      </Button>
      <p className="px-1 text-[0.58rem] leading-snug text-muted-foreground">
        Runs {REFERENCE_QUESTIONS.length} live turns in a new chat — real model calls, billed
        like any other question.
      </p>
    </section>
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
  const allSessions = useSessionStore((s) => s.sessions);
  const total = useSessionStore((s) => s.sessionsTotal);
  const state = useSessionStore((s) => s.sessionsState);
  const error = useSessionStore((s) => s.sessionsError);
  const switchError = useSessionStore((s) => s.switchError);
  const currentSessionId = useSessionStore((s) => s.sessionId);
  const switchingSessionId = useSessionStore((s) => s.switchingSessionId);
  const switchSession = useSessionStore((s) => s.switchSession);
  const archiveSession = useSessionStore((s) => s.archiveSession);
  const driver = useSessionStore((s) => s.driver);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const replaying = useSessionStore((s) => s.replaying);
  const newChatPending = useSessionStore((s) => s.newChatPending);
  const [confirmingArchiveId, setConfirmingArchiveId] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  /**
   * A filter over the sessions THIS RAIL HAS, and it says so.
   *
   * The live tenant has 219 sessions and the list reads 50 of them, so a
   * box that silently searched the loaded page would answer "no sessions
   * called X" about a session that exists — the honest version names the
   * population it searched. Matching is on the title the row displays, so
   * what is typed and what is compared are the same string.
   */
  const needle = query.trim().toLowerCase();
  const sessions =
    needle === ""
      ? allSessions
      : allSessions.filter((session) =>
          displaySessionTitle(session.title).toLowerCase().includes(needle),
        );
  // Switching mid-turn would abandon a stream whose answer is still
  // arriving, so the rows are inert until the pipeline is free.
  const busy = streaming || replaying || newChatPending || switchingSessionId !== null;
  // Offered only by a driver that can actually do it. The mock fixture has
  // no deployment behind it, and a control that would silently do nothing
  // is worse than one that is not there.
  const canArchive = driver?.archiveSession !== undefined;

  return (
    <section className="space-y-1">
      <h3 className="flex items-center justify-between gap-1.5 px-1 text-[0.68rem] font-semibold uppercase tracking-wide text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <MessagesSquare className="size-3" />
          Sessions
        </span>
        {state === "ready" && total > allSessions.length && (
          <span className="num text-[0.6rem] font-normal text-muted-foreground">
            {allSessions.length} of {total}
          </span>
        )}
      </h3>

      {/* Offered once the list is long enough to need it. Client-side over
          the rows already read — no request, no debounce, no spinner. */}
      {state === "ready" && allSessions.length > 6 && (
        <div className="relative px-1 pb-1">
          <Search
            aria-hidden
            className="pointer-events-none absolute left-2.5 top-1/2 size-3 -translate-y-1/2 text-muted-foreground"
          />
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`Filter these ${allSessions.length} sessions`}
            aria-label={`Filter the ${allSessions.length} sessions loaded in this rail by title`}
            className="focus-ring w-full rounded-md border bg-surface-sunken/60 py-1 pl-7 pr-2 text-[0.65rem] placeholder:text-muted-foreground"
          />
        </div>
      )}

      {switchError && (
        <p
          role="alert"
          className="flex items-start gap-1.5 px-1 text-[0.62rem] leading-snug text-negative"
        >
          <AlertTriangle className="mt-0.5 size-3 shrink-0" />
          {switchError}
        </p>
      )}

      {state === "unavailable" ? (
        <p role="alert" className="px-1 text-[0.62rem] leading-snug text-muted-foreground">
          {error}
        </p>
      ) : state !== "ready" && sessions.length === 0 ? (
        // Includes "idle" — before the read has answered, "no sessions" is
        // a claim the app has not earned yet.
        <p className="px-1 text-[0.62rem] leading-snug text-muted-foreground">Loading sessions…</p>
      ) : allSessions.length === 0 ? (
        <p className="px-1 text-[0.62rem] leading-snug text-muted-foreground">
          No sessions yet. Ask a question and this one appears here.
        </p>
      ) : sessions.length === 0 ? (
        // Never "no sessions match": this searched the 50 rows the rail
        // read, and the tenant may have 219.
        <p className="px-1 text-[0.62rem] leading-snug text-muted-foreground">
          Nothing in the {allSessions.length} session{allSessions.length === 1 ? "" : "s"}{" "}
          loaded here matches “{query.trim()}”
          {total > allSessions.length && ` — this tenant has ${total} in all`}.
        </p>
      ) : (
        <ul className="space-y-0.5">
          {sessions.map((session) => {
            const active = session.sessionId === currentSessionId;
            const pending = session.sessionId === switchingSessionId;
            const title = displaySessionTitle(session.title);
            const confirming = session.sessionId === confirmingArchiveId;
            if (confirming) {
              return (
                <li key={session.sessionId}>
                  <ArchiveConfirm
                    title={title}
                    sessionId={session.sessionId}
                    onCancel={() => setConfirmingArchiveId(null)}
                    onConfirm={() => {
                      setConfirmingArchiveId(null);
                      void archiveSession(session.sessionId);
                    }}
                  />
                </li>
              );
            }
            return (
              <li key={session.sessionId} className="group/row relative">
                <button
                  type="button"
                  disabled={busy && !pending}
                  aria-current={active ? "true" : undefined}
                  // The turn count and the exact last-activity instant are
                  // the two facts the row shows nowhere on screen, and
                  // they lived only in a native `title` — mouse-only, with
                  // no keyboard path and no touch equivalent. On the
                  // accessible name they reach everyone; the `title` stays
                  // for the pointer.
                  aria-label={`${title} — ${session.turnCount} turn${
                    session.turnCount === 1 ? "" : "s"
                  }, last activity ${session.lastActivity}`}
                  title={`${title} · ${session.turnCount} turn${
                    session.turnCount === 1 ? "" : "s"
                  } · last activity ${session.lastActivity}`}
                  onClick={() => void switchSession(session.sessionId)}
                  className={cn(
                    // The 2px rail is the SELECTED indicator; the tint is
                    // only its backing. `bg-accent` alone measured 1.15:1
                    // against the translucent rail and `hover:bg-accent/50`
                    // 1.06:1 — hover and selected were the same pixel.
                    // `--ring` on the same surface is 3.61:1 light /
                    // 10.22:1 dark. Every row reserves the 2px so
                    // selecting one never nudges the text.
                    "flex w-full items-baseline justify-between gap-2 rounded-md border-l-2 border-l-transparent px-2 py-1.5 text-left text-[0.7rem] transition-colors duration-150 focus-ring",
                    // Room for the archive control, which sits over the
                    // row's right edge rather than in its flow — so
                    // revealing it never re-flows the title.
                    "pr-7",
                    active
                      ? "border-l-ring bg-accent font-medium"
                      : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                    busy && !pending && "cursor-not-allowed opacity-50",
                  )}
                >
                  <span className="truncate">{title}</span>
                  <span className="num flex shrink-0 items-center gap-1 text-[0.6rem] text-muted-foreground">
                    {pending ? (
                      <Loader2 className="size-2.5 animate-spin" />
                    ) : (
                      relativeTime(session.lastActivity)
                    )}
                  </span>
                </button>
                {/* Quiet on purpose: this is a tidying gesture beside
                    somebody's work, not a peer of "open it". It appears on
                    hover and on keyboard focus — `focus-visible:opacity-100`
                    is what keeps it reachable without a mouse, which is the
                    failure mode every hover-only control has. It is a
                    sibling of the row button, never nested inside it: a
                    button inside a button is invalid, and a click here must
                    not also open the session. */}
                {canArchive && (
                  <Button
                    variant="ghost"
                    size="xs"
                    disabled={busy}
                    aria-label={`Archive ${title}`}
                    title="Remove this session from the list. Nothing is deleted — it keeps its answers and stays reachable at its own link, which the confirmation hands you before you archive."
                    onClick={() => setConfirmingArchiveId(session.sessionId)}
                    className="absolute right-0.5 top-1/2 size-5 -translate-y-1/2 rounded p-0 text-muted-foreground opacity-0 transition-opacity duration-150 hover:text-foreground focus-visible:opacity-100 group-hover/row:opacity-100"
                  >
                    <Archive className="size-3" />
                  </Button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

/**
 * The confirm, in the row's own place.
 *
 * Two words this control has to get right. It says ARCHIVE, not delete,
 * because the server's DELETE is a soft archive: the session keeps its
 * investigations, traces, frames and cohorts and stays fetchable by id, so
 * a link pasted into a ticket still resolves. And it says what actually
 * changes — the row leaves this list — rather than implying an
 * investigation was retracted.
 *
 * It replaces the row rather than opening over it: there is no undo behind
 * the button, and a confirmation that can be dismissed by clicking
 * anywhere is not one.
 *
 * The sentence about staying "reachable by link" was, until this route
 * existed, false: there was no per-session URL anywhere in the product, no
 * archived filter and no way back — so archiving was irreversible from the
 * UI while the dialog said otherwise. It is true now (`/s/{session_id}`),
 * and rather than ask the reader to take that on faith the dialog HANDS
 * OVER the link, here, before the row goes.
 */
function ArchiveConfirm({
  title,
  sessionId,
  onConfirm,
  onCancel,
}: {
  title: string;
  sessionId: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-1.5 rounded-md border border-warning/40 bg-warning/10 p-2">
      <p className="text-[0.62rem] leading-snug">
        Remove <span className="font-medium">{title}</span> from this list? Its answers are
        kept and it stays reachable at its link — only the row goes. Take the link first: once
        the row is gone, this rail has no way back to it.
      </p>
      <CopyTextButton
        label="Copy this session's link"
        doneLabel="Link copied"
        title="Copy the permalink to this session, so it can be re-opened after the row leaves this list"
        className="h-5 px-1.5 text-[0.62rem]"
        text={() =>
          sessionLinkFor(sessionId, typeof window === "undefined" ? "" : window.location.origin)
        }
      />
      <div className="flex gap-1.5">
        <Button
          size="xs"
          variant="secondary"
          className="h-6 flex-1 text-[0.65rem] font-medium"
          onClick={onConfirm}
        >
          Archive
        </Button>
        <Button
          size="xs"
          variant="ghost"
          className="h-6 flex-1 text-[0.65rem] font-normal"
          onClick={onCancel}
        >
          Keep it
        </Button>
      </div>
    </div>
  );
}
