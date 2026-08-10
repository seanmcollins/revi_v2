"use client";

import { AlertTriangle, Command, Settings2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CommandPalette } from "@/components/command/CommandPalette";
import { HeroQuestions } from "@/components/chat/HeroQuestions";
import { TurnInput } from "@/components/chat/TurnInput";
import { ContractDriftBanner } from "@/components/banners/ContractDriftBanner";
import { DetectedAnomalies } from "@/components/home/DetectedAnomalies";
import { MonitorDigest } from "@/components/home/MonitorDigest";
import { WhatChangedStrip } from "@/components/home/WhatChangedStrip";
import { useLeadHandles } from "@/components/monitors/useLeadHandles";
import { SettingsPanel } from "@/components/settings/SettingsPanel";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { ConnectionPill, DegradedModeBadge } from "@/components/workspace/ConnectionPill";
import { SessionRail } from "@/components/workspace/SessionRail";
import { announce } from "@/lib/announce";
import { mediumDate } from "@/lib/format";
import { homeShape } from "@/lib/homeLayout";
import { hasUnseenLoad, markMonitorsSeen } from "@/lib/monitorsVisit";
import { useBriefQuery, useMonitorsQuery, usePortfolioQuery } from "@/lib/queries";
import { useSessionStore } from "@/lib/store";
import { useAsk } from "@/lib/useAsk";
import { useDeployment } from "@/lib/useDeployment";

/**
 * HOME — the picture as of this load.
 *
 * The front door used to be an empty composer over a wordmark: a product
 * that walks your data every night, opening on a blinking cursor. Home is
 * the same product's opening claim made structural — three zones, read top
 * to bottom, and a composer one keystroke away underneath them.
 *
 *   WHAT CHANGED   one sentence from the brief, expandable in place to the
 *                  brief itself. A quiet load says so, proudly.
 *   THE ZONES THAT SWAP   the detected worklist and the analyst's own
 *                  monitors, in an order that depends on what happened at
 *                  this load — see `homeShape`, which is the whole
 *                  "ready to evolve" claim in one pure function.
 *   THE COMPOSER   at the bottom, focused, with the four hero questions.
 *                  Submitting goes INTO the session the turn mints
 *                  (`useAsk`), because Home renders no thread and an
 *                  answer arriving somewhere nobody can see is worse than
 *                  no answer.
 *
 * IT REPLACED A REDIRECT. The cold start used to push `/` → `/monitors`
 * whenever a data load had landed that this browser had not been briefed
 * on: the only navigation this app ever made on somebody's behalf, latched
 * to happen once, with a focus move and an announcement at the far end to
 * make it survivable for a screen reader. Home fulfils that natively — the
 * brief is the first thing on the page — so the navigation is gone and the
 * a11y half is kept: a load nobody has been briefed on still announces its
 * headline politely and still moves focus to the thing that just arrived.
 *
 * THE MOCK FIXTURE DOES NOT COME HERE. `HomeRoute` sends it to the
 * workspace instead: Home is made of three live reads (brief, monitors,
 * worklist) and a page of invented tiles would be the opposite of what this
 * surface is for. See `routes/HomeRoute.tsx`.
 */
export function Home() {
  const { live } = useDeployment();
  const ask = useAsk();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const openSettings = useSessionStore((s) => s.openSettings);
  const hydrateSettings = useSessionStore((s) => s.hydrateSettings);
  const newestWatermarkId = useSessionStore((s) => s.connection.newestWatermarkId);

  useEffect(() => {
    hydrateSettings();
  }, [hydrateSettings]);

  // The deployment's newest load keys the two Monitors reads: a brief is a
  // statement about ONE data load, so a new load is a new question rather
  // than a stale cache.
  const watermarkKey = newestWatermarkId ?? "";
  const enabled = live && watermarkKey !== "";
  const brief = useBriefQuery(enabled, watermarkKey);
  const monitors = useMonitorsQuery(enabled, watermarkKey);
  const portfolio = usePortfolioQuery(live);
  const leads = useLeadHandles(portfolio.data?.items);

  /**
   * THE ORDER OF THE TWO MIDDLE ZONES, and it is not a preference.
   *
   * Derived from the payloads already on screen — the tiles' governed
   * `material` flags and the brief's own movement entries — so a tenant
   * with nothing pinned sees the worklist as the page, and a tenant whose
   * monitor moved overnight finds it above the feed. Nothing is stored,
   * nothing is configured, and no threshold is re-derived here.
   */
  const shape = useMemo(
    () =>
      homeShape({
        ...(monitors.data ? { tiles: monitors.data.tiles } : {}),
        ...(brief.data ? { entries: brief.data.entries } : {}),
      }),
    [monitors.data, brief.data],
  );

  /**
   * A LOAD NOBODY HAS BEEN BRIEFED ON ANNOUNCES ITSELF.
   *
   * What survives from the retired redirect. The headline is said once,
   * politely, and focus moves to the zone that carries it — a landing page
   * whose whole purpose is "here is what changed" owes a screen-reader
   * user the sentence and somewhere to be standing when it is read.
   *
   * "Seen" is recorded when the brief has actually RENDERED, not on
   * arrival: a load marked read by a page that then failed to read it
   * would be a load nobody is ever briefed on.
   */
  const announced = useRef<string | null>(null);
  useEffect(() => {
    const data = brief.data;
    if (!data) return;
    if (announced.current === data.watermarkId) return;
    announced.current = data.watermarkId;
    const unseen = hasUnseenLoad(data.watermarkId);
    markMonitorsSeen(data.watermarkId);
    if (!unseen) return;
    announce(`What changed at this load: ${data.headline}`);
    // The same node the skip link lands on, so there is ONE place focus
    // can be on this zone rather than two nested ones.
    document.getElementById(WHAT_CHANGED_ID)?.focus();
  }, [brief.data]);

  /**
   * A QUESTION THAT WENT NOWHERE.
   *
   * `useAsk` follows a submission into the session it mints, so a turn
   * asked here is normally on another route a second later. The one case
   * that stays is a session bootstrap that failed: the turn exists, no
   * session does, and without this Home would simply look as if the click
   * had not registered.
   */
  const turns = useSessionStore((s) => s.turns);
  const streaming = useSessionStore((s) => s.streamingTurnId !== null);
  const sessionLive = useSessionStore((s) => s.sessionLive);
  const stranded =
    !streaming && !sessionLive && turns.length > 0 ? turns[turns.length - 1] : undefined;

  return (
    <div className="relative h-dvh overflow-hidden bg-background">
      {/* FIRST IN THE DOCUMENT, because a skip link anywhere else is not
          one — the session rail is fifty rows deep and sits ahead of
          everything on this page. The composer is offered as a jump for
          the same reason it is autofocused: this is the page somebody
          lands on to ask something. */}
      <SkipLinks />
      <div aria-hidden className="page-glow pointer-events-none absolute inset-0" />

      <div className="relative grid h-full grid-cols-[16.5rem_minmax(0,1fr)] min-[1440px]:grid-cols-[17.5rem_minmax(0,1fr)]">
        <SessionRail />

        <main className="flex h-full min-h-0 flex-col">
          <header className="flex shrink-0 items-center justify-between gap-4 border-b bg-background/55 px-6 py-2.5 backdrop-blur-md">
            <div className="min-w-0">
              <h1 className="truncate text-body font-semibold tracking-tight">
                Where things stand
              </h1>
              <p className="num truncate text-micro text-muted-foreground">
                {brief.data?.newestDataDate
                  ? `Everything Revi walked on the data through ${safeDate(brief.data.newestDataDate)}`
                  : "What Revi walked the last time a data load landed"}
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

          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="space-y-10 px-6 py-8">
              <ContractDriftBanner />
              {!live ? (
                <NoDeployment />
              ) : (
                <>
                  <WhatChangedStrip
                    brief={brief.data}
                    leads={leads}
                    isPending={brief.isPending}
                    error={brief.error}
                  />

                  {/* THE EVOLUTION, rendered. Two zones, one order, decided
                      by `homeShape` and by nothing on this component. */}
                  {shape.order === "monitors_first" ? (
                    <>
                      <MonitorDigest query={monitors} moved={shape.movedPinIds} />
                      <DetectedAnomalies query={portfolio} />
                    </>
                  ) : (
                    <>
                      <DetectedAnomalies query={portfolio} />
                      <MonitorDigest query={monitors} moved={shape.movedPinIds} />
                    </>
                  )}
                </>
              )}
            </div>
          </div>

          <footer className="shrink-0 border-t bg-background/55 px-6 py-3 backdrop-blur-md">
            <div className="@container mx-auto max-w-3xl space-y-2.5">
              {stranded?.answer.error && (
                <p
                  role="alert"
                  className="flex items-start gap-1.5 text-meta leading-snug text-negative"
                >
                  <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
                  <span>
                    That question did not open a session. {stranded.answer.error.message}
                  </span>
                </p>
              )}
              <HeroQuestions
                disabled={streaming}
                onAsk={(question) => ask({ utterance: question })}
              />
              <TurnInput
                suggestions={[]}
                onAsk={(utterance) => ask({ utterance })}
                autoFocus
              />
            </div>
          </footer>
        </main>
      </div>

      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <SettingsPanel />
    </div>
  );
}

/**
 * The two jumps worth offering a keyboard reader here, in the order the
 * page is read.
 *
 * Anchors with an `onClick` rather than bare `href="#…"`, because a
 * fragment navigation scrolls without moving FOCUS in every browser that
 * has not shipped the fix — so the next Tab would resume from the link,
 * behind the rail the reader just skipped. Both targets carry
 * `tabIndex={-1}` so they can take it.
 */
/**
 * The zone that carries this load's headline — the skip link's landing
 * place, and where focus goes when a load announces itself.
 */
export const WHAT_CHANGED_ID = "home-what-changed";

function SkipLinks() {
  return (
    <nav
      aria-label="Skip links"
      className="absolute left-2 top-2 z-50 flex gap-1.5 [&:not(:focus-within)]:pointer-events-none"
    >
      <SkipLink target={WHAT_CHANGED_ID}>Skip to what changed</SkipLink>
      <SkipLink target="turn-composer">Skip to the composer</SkipLink>
    </nav>
  );
}

function SkipLink({ target, children }: { target: string; children: ReactNode }) {
  return (
    <a
      href={`#${target}`}
      onClick={(event) => {
        event.preventDefault();
        const node = document.getElementById(target);
        node?.focus();
        node?.scrollIntoView({ block: "start" });
      }}
      className="focus-ring sr-only rounded-md border bg-surface-sunken px-2 py-1 text-micro font-medium shadow-sm focus:not-sr-only focus:relative"
    >
      {children}
    </a>
  );
}

function NoDeployment() {
  return (
    <p className="max-w-[64ch] text-body leading-relaxed text-muted-foreground">
      This page is the live API&apos;s. This browser is running the mock fixture, which has no
      deployment to walk, no monitors to store and no loads to compare.
    </p>
  );
}

function safeDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
}
