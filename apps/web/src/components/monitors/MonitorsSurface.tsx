"use client";

import { AlertTriangle, ArrowLeft } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { useEffect, useMemo, useRef, useSyncExternalStore, type ReactNode } from "react";

import { WarningList } from "@/components/banners/WarningBanner";
import { BriefPanel } from "@/components/monitors/BriefPanel";
import type { BriefLeadHandle } from "@/components/monitors/BriefEntryRow";
import { LeadLifecyclePanel, type LeadRow } from "@/components/monitors/LeadLifecycle";
import { MonitorTile } from "@/components/monitors/MonitorTile";
import { ConnectionPill } from "@/components/workspace/ConnectionPill";
import { SessionRail } from "@/components/workspace/SessionRail";
import { announce } from "@/lib/announce";
import { ApiDriver, fetchHealthDetail, resolveDriverKind } from "@/lib/apiDriver";
import { envDriverKind, type DriverKind, type TurnDriver } from "@/lib/driver";
import { mediumDate } from "@/lib/format";
import type { PortfolioItem } from "@/lib/mock/portfolio";
import { MockDriver } from "@/lib/mockDriver";
import { consumeMonitorsRedirect, markMonitorsSeen } from "@/lib/monitorsVisit";
import { useBriefQuery, usePortfolioQuery, useMonitorsQuery } from "@/lib/queries";
import { useSessionStore } from "@/lib/store";
import { orderTilesForGrid, tileCensus } from "@/lib/monitors";

const noopSubscribe = () => () => {};

/**
 * MONITORS — the surface Revi walks for you.
 *
 * Three zones and one argument. THE BRIEF is what changed at this load, in
 * sentences, gated by governed materiality and capped so it can be
 * finished. THE MONITORS are the things somebody asked to be told about,
 * each re-run at this load through the ordinary governed pipeline — every
 * tile is a real investigation with a real trace and a real permalink, not
 * a number computed off to the side. THE LEADS are the other axis: not
 * what changed, but what is being worked and whether the fixes stuck,
 * which is the standing question a director has every morning and the one
 * a surface made only of diffs cannot answer.
 *
 * The argument is that a proactive surface earns its place by being QUIET.
 * Everything here is built so a morning with nothing in it looks like an
 * answer rather than an empty page: the brief's proud state is the largest
 * type on the screen, the counts behind it are published, and the gate
 * that produced it is one hover away.
 *
 * It reuses the workspace's own rail rather than inventing navigation.
 * Monitors is where the analyst starts and the thread is where they go; two
 * different chromes for two halves of one product would make the second
 * feel like somewhere else.
 *
 * WHAT IT SAYS OUT LOUD. This route is genuinely slow the first time a
 * load is opened — it re-runs every monitor and verifies every claimed fix —
 * and it is also the one place the app navigates on the analyst's behalf.
 * Both are announced: the pending states are live regions, the brief
 * announces itself when it lands, and a cold-start redirect moves focus to
 * the heading of the page it just opened.
 */
export function MonitorsSurface() {
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
  const newestWatermarkId = useSessionStore((s) => s.connection.newestWatermarkId);
  const live = driverKind === "api";
  const navigate = useNavigate();
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    setDriver(driver);
  }, [driver, setDriver]);

  /**
   * ARRIVED HERE WITHOUT ASKING. The cold start pushes `/` → `/monitors`,
   * and a client-side navigation moves no focus and announces nothing —
   * so a screen-reader user's focus stays on a composer that is no longer
   * mounted while the app becomes a different app. Focus goes to this
   * page's own heading, and one sentence says where they are.
   */
  useEffect(() => {
    if (!consumeMonitorsRedirect()) return;
    headingRef.current?.focus();
    announce("Opened Monitors: this data load has not been briefed yet.");
  }, []);

  // The deployment's newest load, which is what Monitors is about. It also
  // keys both queries: a brief is a statement about ONE data load, so a new
  // load is a new question rather than a stale cache.
  useEffect(() => {
    const { setConnection } = useSessionStore.getState();
    if (driverKind !== "api") {
      setConnection({ mode: "mock", state: "online", healthChecked: true });
      return;
    }
    setConnection({ mode: "api", state: "connecting" });
    let cancelled = false;
    void (async () => {
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
    })();
    return () => {
      cancelled = true;
    };
  }, [driverKind]);

  const watermarkKey = newestWatermarkId ?? "";
  const enabled = live && watermarkKey !== "";
  const brief = useBriefQuery(enabled, watermarkKey);
  const monitors = useMonitorsQuery(enabled, watermarkKey);
  // The load's own worklist: where every lead stands, and the typed spec
  // that opens it. Monitors' brief entries carry neither, so without this a
  // $17,677 lead is a sentence with nothing behind it.
  const portfolio = usePortfolioQuery(live);
  // What each monitor IS (spec, window mode, threshold) — a different
  // question from what it read, and the one the sensitivity editor needs.
  const loadMonitors = useSessionStore((s) => s.loadMonitors);
  const knownMonitors = useSessionStore((s) => s.knownMonitors);
  const leadStates = useSessionStore((s) => s.leadStates);
  const submit = useSessionStore((s) => s.submit);
  useEffect(() => {
    void loadMonitors();
  }, [loadMonitors, driver]);

  // "Seen" is recorded once the brief for this load has actually been
  // rendered — not on navigation. A cold start that redirected here and
  // then failed to load would otherwise mark the load read, and the next
  // morning would open on a thread with the brief never shown.
  useEffect(() => {
    if (brief.data) markMonitorsSeen(brief.data.watermarkId);
  }, [brief.data]);

  /**
   * THE BRIEF LANDED. Announced once, politely, with the two facts a
   * reader needs before they decide whether to read on: what it says, and
   * how much was walked to say it. Everything else on this page is
   * reachable by heading; this is the part that arrives after the wait.
   */
  const announced = useRef<string | null>(null);
  useEffect(() => {
    if (!brief.data) return;
    if (announced.current === brief.data.watermarkId) return;
    announced.current = brief.data.watermarkId;
    const tiles = monitors.data ? `, ${monitors.data.tiles.length} monitors re-run` : "";
    announce(`Your Monitors brief: ${brief.data.headline}${tiles}`);
  }, [brief.data, monitors.data]);

  const pinsById = new Map(knownMonitors.map((pin) => [pin.pinId, pin]));

  /**
   * A LEAD'S DESTINATION, and the state it is in.
   *
   * The drill exists — the platform re-derives it every load to verify
   * claimed resolutions — and the worklist card carries the typed spec
   * that opens it. Submitting it opens a real investigation in the thread,
   * which is where an answer belongs, so the navigation follows the turn.
   */
  const openLead = useMemo(
    () =>
      (item: PortfolioItem): (() => void) | undefined => {
        if (!item.drillable || !item.drillSpec) return undefined;
        return () => {
          void submit({ spec: item.drillSpec!, anomalyRef: item.referent });
          navigate("/");
        };
      },
    [submit, navigate],
  );

  const leadHandles = useMemo(() => {
    const handles = new Map<string, BriefLeadHandle>();
    for (const item of portfolio.data?.items ?? []) {
      // What this browser changed a minute ago beats the snapshot, which
      // was composed when the load landed — and it is the only record that
      // carries what the platform MEASURED about the claim.
      const liveState = leadStates[item.referent];
      const open = openLead(item);
      handles.set(item.referent, {
        ...(liveState?.status ?? item.leadStatus
          ? { status: liveState?.status ?? item.leadStatus! }
          : {}),
        ...(liveState?.verificationNote || liveState?.note || item.leadStatusNote
          ? { note: liveState?.verificationNote || liveState?.note || item.leadStatusNote! }
          : {}),
        ...(open ? { open } : {}),
        ...(!open && item.drillUnavailableReason
          ? { unavailableReason: item.drillUnavailableReason }
          : {}),
      });
    }
    return handles;
  }, [portfolio.data, leadStates, openLead]);

  const leadRows = useMemo<LeadRow[]>(() => {
    const rows: LeadRow[] = [];
    for (const item of portfolio.data?.items ?? []) {
      const liveState = leadStates[item.referent];
      const status = liveState?.status ?? item.leadStatus ?? "open";
      if (status === "open") continue;
      const open = openLead(item);
      rows.push({
        anomalyId: item.referent,
        title: item.title,
        status,
        note: liveState?.verificationNote || liveState?.note || item.leadStatusNote || "",
        ...(item.impactCents !== undefined ? { impactCents: item.impactCents } : {}),
        ...(open ? { open } : {}),
        ...(liveState ? { live: liveState } : {}),
      });
    }
    // Anything this browser changed on a lead the snapshot does not carry
    // still belongs here — a status set on a card that has since left the
    // feed is exactly the kind of work that goes missing.
    for (const [anomalyId, state] of Object.entries(leadStates)) {
      if (state.status === "open") continue;
      if (rows.some((row) => row.anomalyId === anomalyId)) continue;
      rows.push({
        anomalyId,
        title: "No longer on this load's worklist",
        status: state.status,
        note: state.verificationNote || state.note,
        live: state,
      });
    }
    return rows;
  }, [portfolio.data, leadStates, openLead]);

  return (
    <div className="relative h-dvh overflow-hidden bg-background">
      {/* FIRST IN THE DOCUMENT, because a skip link anywhere else is not
          one. Measured before this: "Skip to your monitors" sat in the
          main header, which is the 152nd of 224 tab stops — behind the
          rail's fifty session rows and both of each row's controls — so
          the control that exists to save a keyboard reader those stops was
          reachable only by taking them. It is the first focusable element
          on the page now, and `Monitors.test.tsx` asserts exactly that
          rather than asserting it exists. */}
      {live && <SkipLinks />}
      <div aria-hidden className="page-glow pointer-events-none absolute inset-0" />
      <div className="relative grid h-full grid-cols-[16.5rem_minmax(0,1fr)] min-[1440px]:grid-cols-[17.5rem_minmax(0,1fr)]">
        <SessionRail />

        <main className="flex h-full min-h-0 flex-col">
          <header className="flex shrink-0 items-center justify-between gap-4 border-b bg-background/55 px-6 py-2.5 backdrop-blur-md">
            <div className="min-w-0">
              <h1
                ref={headingRef}
                tabIndex={-1}
                className="truncate text-body font-semibold tracking-tight outline-none"
              >
                Monitors
              </h1>
              <p className="num truncate text-micro text-muted-foreground">
                {brief.data?.newestDataDate
                  ? `Walked on the data through ${safeDate(brief.data.newestDataDate)}`
                  : "The monitors Revi walks every time a load lands"}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              <Link
                to="/"
                className="focus-ring inline-flex items-center gap-1 rounded-md border bg-surface-sunken/70 px-2 py-1 text-micro font-medium text-muted-foreground transition-colors duration-200 hover:border-ring/40 hover:text-foreground"
              >
                <ArrowLeft aria-hidden className="size-3" />
                Ask a question
              </Link>
              <ConnectionPill />
              {/* NO SECOND THEME TOGGLE. The rail already carries one on
                  every route, and this header put an identical moon icon
                  with an identical accessible name on the same screen —
                  two controls a screen reader announces the same way,
                  with nothing to tell them apart. The workspace header
                  never had one; this page now matches it. */}
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {/* Left-aligned to the header above it, not centred in the
                column. Monitors is read as a document — the brief's own
                measure is the reading measure, and centring it would put
                the first word of every sentence in a different place from
                the page's own title.

                The measure belongs to the PROSE. It was wrapped around the
                whole page, so the tile grid inherited a reading measure
                and rendered two fixed columns across 848 of 1232 available
                pixels. Each zone now takes the width its own content
                wants. */}
            <div className="space-y-10 px-6 py-8">
              {!live ? (
                <NoDeployment />
              ) : (
                <>
                  {/* The skip link's landing place, and it is the ZONE
                      rather than the brief's own heading: the brief has
                      three states (walked, still walking, could not be
                      read) and only one of them has that heading, so
                      aiming at it would give the reader a dead control on
                      exactly the two loads where the page is hardest to
                      use. */}
                  <div id="brief-zone" tabIndex={-1} className="max-w-4xl outline-none">
                    <BriefZone query={brief} leads={leadHandles} />
                  </div>
                  <MonitorZone query={monitors} pinsById={pinsById} />
                  <LeadLifecyclePanel
                    leads={leadRows}
                    totalLeads={portfolio.data?.items.length ?? 0}
                    headingId="leads-heading"
                  />
                </>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

/**
 * The two jumps worth offering a keyboard reader on this page, in the
 * order the page is read.
 *
 * They are anchors with an `onClick` rather than bare `href="#…"` because
 * a fragment navigation scrolls without moving FOCUS in every browser that
 * has not shipped the fix — so the next Tab would resume from the link,
 * behind the fifty rail rows the reader just skipped, which is the bug
 * wearing a fix's clothes. Both targets carry `tabIndex={-1}` so they can
 * take it.
 *
 * Visually hidden until focused, then drawn over the page rather than in
 * it: a control that reflows the layout of a page nobody has interacted
 * with yet is worse than one that is invisible.
 */
function SkipLinks() {
  return (
    <nav
      aria-label="Skip links"
      className="absolute left-2 top-2 z-50 flex gap-1.5 [&:not(:focus-within)]:pointer-events-none"
    >
      <SkipLink target="brief-zone">Skip to this load&apos;s brief</SkipLink>
      <SkipLink target="monitors-heading">Skip to your monitors</SkipLink>
    </nav>
  );
}

function SkipLink({ target, children }: { target: string; children: ReactNode }) {
  return (
    <a
      href={`#${target}`}
      onClick={(event) => {
        event.preventDefault();
        const heading = document.getElementById(target);
        heading?.focus();
        heading?.scrollIntoView({ block: "start" });
      }}
      className="focus-ring sr-only rounded-md border bg-surface-sunken px-2 py-1 text-micro font-medium shadow-sm focus:not-sr-only focus:relative"
    >
      {children}
    </a>
  );
}

/** The brief zone, including the two states that are not a brief. */
function BriefZone({
  query,
  leads,
}: {
  query: ReturnType<typeof useBriefQuery>;
  leads: ReadonlyMap<string, BriefLeadHandle>;
}) {
  if (query.data) return <BriefPanel brief={query.data} leads={leads} />;
  if (query.isPending) {
    return (
      <section className="space-y-2">
        <p className="text-micro font-semibold uppercase tracking-widest text-muted-foreground">
          This load&apos;s brief
        </p>
        {/* Named work, not a spinner. This route re-runs every monitor and
            verifies every claimed fix on request, so it is genuinely slow
            the first time a load is opened — and a surface that said
            nothing about that would look broken rather than busy. It is a
            live region for the same reason: over a 30-second wait, silence
            is indistinguishable from a broken page. */}
        <p
          role="status"
          aria-live="polite"
          className="text-body leading-relaxed text-muted-foreground"
        >
          Walking your Monitors at this load — re-running each monitor and checking the fixes
          anyone claimed.
        </p>
      </section>
    );
  }
  return <ReadFailed what="brief" error={query.error} />;
}

/**
 * The tile grid.
 *
 * TWO THINGS IT DOES THAT A GRID OF CARDS DOES NOT. It ORDERS — the
 * monitors that moved come first, then the ones that held still, then the
 * ones with nothing to compare, then the ones the platform could not
 * measure — and it SAYS SO, because an order nobody declared is an order
 * nobody can trust. Within each band the server's own order is kept.
 *
 * And it publishes a CENSUS, so the count in the heading reconciles to
 * what is on screen. "12 monitors" over a grid where nine are silent is a
 * number that raises a question the surface then refuses to answer.
 */
function MonitorZone({
  query,
  pinsById,
}: {
  query: ReturnType<typeof useMonitorsQuery>;
  pinsById: Map<string, import("@/lib/monitors").MonitorsPin>;
}) {
  const ordered = useMemo(
    () => (query.data ? orderTilesForGrid(query.data.tiles) : []),
    [query.data],
  );
  const census = useMemo(() => tileCensus(ordered), [ordered]);

  return (
    <section aria-labelledby="monitors-heading" className="space-y-3">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id="monitors-heading"
          tabIndex={-1}
          className="text-micro font-semibold uppercase tracking-widest text-muted-foreground outline-none"
        >
          Your monitors
        </h2>
        {query.data && query.data.tiles.length > 0 && (
          <span className="num text-micro text-muted-foreground">
            {query.data.tiles.length} monitor{query.data.tiles.length === 1 ? "" : "s"}
            {query.data.newestDataDate
              ? `, re-run on the data through ${safeDate(query.data.newestDataDate)}`
              : ", re-run at this data load"}
          </span>
        )}
      </header>

      {query.data ? (
        query.data.tiles.length === 0 ? (
          // An invitation, not a shrug. The affordance being described is
          // real and one click away on any answer.
          <p className="max-w-[64ch] text-body leading-relaxed text-muted-foreground">
            Nothing is being monitored yet. Ask a question, then choose{" "}
            <span className="font-medium text-foreground">Monitor this</span> on the chart,
            finding or worklist you want briefed — or say “monitor Silverline&apos;s denial rate”
            in the composer and Revi will answer once and then keep monitoring.
          </p>
        ) : (
          <>
            <WarningList
              warnings={query.data.warnings.map((w) => ({ ...w, type: "warning" as const }))}
            />
            {/* The census and the ordering rule, in one line. Both are
                claims this surface has to make out loud: the counts
                because a grid of twenty tiles cannot be counted by eye,
                and the order because "first" is the strongest statement a
                list makes and it was being made by creation date. */}
            <p data-tile-census className="num max-w-[64ch] text-micro text-muted-foreground">
              {census.join(" · ")}. Moved first, then unchanged, then nothing to compare, then
              unavailable — within each, the order the platform published.
            </p>
            <p id="monitors-tile-hint" className="sr-only">
              Each monitor is one tab stop. Press Enter to reach its controls and Escape to leave
              it.
            </p>
            <ul className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
              {ordered.map((tile) => (
                <MonitorTile
                  key={tile.pinId}
                  tile={tile}
                  {...(pinsById.get(tile.pinId) ? { pin: pinsById.get(tile.pinId) } : {})}
                />
              ))}
            </ul>
          </>
        )
      ) : query.isPending ? (
        <p
          role="status"
          aria-live="polite"
          className="text-meta leading-snug text-muted-foreground"
        >
          Re-running your monitors…
        </p>
      ) : (
        <ReadFailed what="monitors" error={query.error} />
      )}
    </section>
  );
}

/**
 * A failed read says which read failed and repeats the server's own
 * sentence. Monitors is the surface somebody opens instead of asking, so a
 * blank column here is indistinguishable from "nothing happened" — which
 * is the one thing it must never be mistaken for.
 */
function ReadFailed({ what, error }: { what: string; error: unknown }) {
  return (
    <p role="alert" className="flex max-w-[64ch] items-start gap-1.5 text-meta leading-snug text-negative">
      <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
      <span>
        Could not read your {what}.{" "}
        {error instanceof Error ? error.message : "The request did not complete."} Nothing on
        this page is out of date — there is nothing on it.
      </span>
    </p>
  );
}

function NoDeployment() {
  return (
    <p className="max-w-[64ch] text-body leading-relaxed text-muted-foreground">
      Monitors is the live API&apos;s. This browser is running the mock fixture, which has no
      deployment to walk, no monitors to store and no loads to compare — and a page of invented
      tiles would be the opposite of what this surface is for.
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
