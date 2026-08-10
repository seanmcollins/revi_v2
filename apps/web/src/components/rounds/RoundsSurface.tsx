"use client";

import { AlertTriangle, ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useEffect } from "react";

import { WarningList } from "@/components/banners/WarningBanner";
import { BriefPanel } from "@/components/rounds/BriefPanel";
import { WatchTile } from "@/components/rounds/WatchTile";
import { ConnectionPill } from "@/components/workspace/ConnectionPill";
import { SessionRail } from "@/components/workspace/SessionRail";
import { ThemeToggle } from "@/components/theme/ThemeToggle";
import { ApiDriver, fetchHealthDetail, resolveDriverKind } from "@/lib/apiDriver";
import { envDriverKind, type DriverKind, type TurnDriver } from "@/lib/driver";
import { mediumDate } from "@/lib/format";
import { MockDriver } from "@/lib/mockDriver";
import { markRoundsSeen } from "@/lib/roundsVisit";
import { useBriefQuery, useRoundsQuery } from "@/lib/queries";
import { useSessionStore } from "@/lib/store";
import { useMemo, useSyncExternalStore } from "react";

const noopSubscribe = () => () => {};

/**
 * ROUNDS — the surface Revi walks for you.
 *
 * Two zones and one argument. THE BRIEF is what changed at this load, in
 * sentences, gated by governed materiality and capped so it can be
 * finished. THE WATCHES are the things somebody asked to be told about,
 * each re-run at this load through the ordinary governed pipeline — every
 * tile is a real investigation with a real trace and a real permalink, not
 * a number computed off to the side.
 *
 * The argument is that a proactive surface earns its place by being QUIET.
 * Everything here is built so a morning with nothing in it looks like an
 * answer rather than an empty page: the brief's proud state is the largest
 * type on the screen, the counts behind it are published, and the gate
 * that produced it is one hover away.
 *
 * It reuses the workspace's own rail rather than inventing navigation.
 * Rounds is where the analyst starts and the thread is where they go; two
 * different chromes for two halves of one product would make the second
 * feel like somewhere else.
 */
export function RoundsSurface() {
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

  useEffect(() => {
    setDriver(driver);
  }, [driver, setDriver]);

  // The deployment's newest load, which is what Rounds is about. It also
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
  const rounds = useRoundsQuery(enabled, watermarkKey);
  // What each watch IS (spec, window mode, threshold) — a different
  // question from what it read, and the one the sensitivity editor needs.
  const loadWatches = useSessionStore((s) => s.loadWatches);
  const knownWatches = useSessionStore((s) => s.knownWatches);
  useEffect(() => {
    void loadWatches();
  }, [loadWatches, driver]);

  // "Seen" is recorded once the brief for this load has actually been
  // rendered — not on navigation. A cold start that redirected here and
  // then failed to load would otherwise mark the load read, and the next
  // morning would open on a thread with the brief never shown.
  useEffect(() => {
    if (brief.data) markRoundsSeen(brief.data.watermarkId);
  }, [brief.data]);

  const pinsById = new Map(knownWatches.map((pin) => [pin.pinId, pin]));

  return (
    <div className="relative h-dvh overflow-hidden bg-background">
      <div aria-hidden className="page-glow pointer-events-none absolute inset-0" />
      <div className="relative grid h-full grid-cols-[16.5rem_minmax(0,1fr)] min-[1440px]:grid-cols-[17.5rem_minmax(0,1fr)]">
        <SessionRail />

        <main className="flex h-full min-h-0 flex-col">
          <header className="flex shrink-0 items-center justify-between gap-4 border-b bg-background/55 px-6 py-2.5 backdrop-blur-md">
            <div className="min-w-0">
              <h1 className="truncate text-body font-semibold tracking-tight">Rounds</h1>
              <p className="num truncate text-micro text-muted-foreground">
                {brief.data?.newestDataDate
                  ? `Walked on the data through ${safeDate(brief.data.newestDataDate)}`
                  : "The watches Revi walks every time a load lands"}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2.5">
              <Link
                href="/"
                className="focus-ring inline-flex items-center gap-1 rounded-md border bg-surface-sunken/70 px-2 py-1 text-micro font-medium text-muted-foreground transition-colors duration-200 hover:border-ring/40 hover:text-foreground"
              >
                <ArrowLeft aria-hidden className="size-3" />
                Ask a question
              </Link>
              <ConnectionPill />
              <ThemeToggle />
            </div>
          </header>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {/* Left-aligned to the header above it, not centred in the
                column. Rounds is read as a document — the brief's own
                measure is the reading measure, and centring it would put
                the first word of every sentence in a different place from
                the page's own title. */}
            <div className="max-w-4xl space-y-10 px-6 py-8">
              {!live ? (
                <NoDeployment />
              ) : (
                <>
                  <BriefZone query={brief} />
                  <WatchZone query={rounds} pinsById={pinsById} />
                </>
              )}
            </div>
          </div>
        </main>
      </div>
    </div>
  );
}

/** The brief zone, including the two states that are not a brief. */
function BriefZone({ query }: { query: ReturnType<typeof useBriefQuery> }) {
  if (query.data) return <BriefPanel brief={query.data} />;
  if (query.isPending) {
    return (
      <section className="space-y-2">
        <p className="text-micro font-semibold uppercase tracking-widest text-muted-foreground">
          This load&apos;s brief
        </p>
        {/* Named work, not a spinner. This route re-runs every watch and
            verifies every claimed fix on request, so it is genuinely slow
            the first time a load is opened — and a surface that said
            nothing about that would look broken rather than busy. */}
        <p className="text-body leading-relaxed text-muted-foreground">
          Walking your Rounds at this load — re-running each watch and checking the fixes
          anyone claimed.
        </p>
      </section>
    );
  }
  return <ReadFailed what="brief" error={query.error} />;
}

/** The tile grid. */
function WatchZone({
  query,
  pinsById,
}: {
  query: ReturnType<typeof useRoundsQuery>;
  pinsById: Map<string, import("@/lib/rounds").RoundsPin>;
}) {
  return (
    <section aria-labelledby="watches-heading" className="space-y-3">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id="watches-heading"
          className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
        >
          Your watches
        </h2>
        {query.data && query.data.tiles.length > 0 && (
          <span className="num text-micro text-muted-foreground">
            {query.data.tiles.length} watch{query.data.tiles.length === 1 ? "" : "es"}, re-run at{" "}
            {query.data.watermarkId}
          </span>
        )}
      </header>

      {query.data ? (
        query.data.tiles.length === 0 ? (
          // An invitation, not a shrug. The affordance being described is
          // real and one click away on any answer.
          <p className="max-w-[64ch] text-body leading-relaxed text-muted-foreground">
            Nothing is being watched yet. Ask a question, then choose{" "}
            <span className="font-medium text-foreground">Watch this</span> on the chart,
            finding or worklist you want briefed — or say “watch Silverline&apos;s denial rate”
            in the composer and Revi will answer once and then keep watching.
          </p>
        ) : (
          <>
            <WarningList
              warnings={query.data.warnings.map((w) => ({ ...w, type: "warning" as const }))}
            />
            <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
              {query.data.tiles.map((tile) => (
                <WatchTile key={tile.pinId} tile={tile} {...(pinsById.get(tile.pinId) ? { pin: pinsById.get(tile.pinId) } : {})} />
              ))}
            </ul>
          </>
        )
      ) : query.isPending ? (
        <p className="text-meta leading-snug text-muted-foreground">Re-running your watches…</p>
      ) : (
        <ReadFailed what="watches" error={query.error} />
      )}
    </section>
  );
}

/**
 * A failed read says which read failed and repeats the server's own
 * sentence. Rounds is the surface somebody opens instead of asking, so a
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
      Rounds is the live API&apos;s. This browser is running the mock fixture, which has no
      deployment to walk, no watches to store and no loads to compare — and a page of invented
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
