"use client";

import { AlertTriangle, ArrowDownRight, ArrowRight, ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";

import { ValueMarks } from "@/components/monitors/IntegrityAtom";
import { deltaMark, directionWord } from "@/components/monitors/DeltaLine";
import { investigationLinkFor } from "@/lib/links";
import { readableStatement } from "@/lib/prose";
import { orderTilesForGrid, TILE_BANDS, tileBand, type MonitorsTile } from "@/lib/monitors";
import { cn } from "@/lib/utils";

/** How many monitors the digest names before deferring to Monitors itself. */
const DIGEST_COUNT = 4;

/**
 * YOUR MONITORS — the zone that evolves as somebody makes this app theirs.
 *
 * On day one this is an invitation and nothing else: the tenant has pinned
 * nothing, so there is nothing to digest and a grid of empty slots would be
 * a worse lie than a sentence. Once monitors exist it becomes a digest —
 * name, current value, what it did — and when any of them moved materially
 * at this load the whole zone moves ABOVE the detected anomalies (see
 * `homeShape`). A landing page that never re-orders itself around what
 * somebody asked to be told about never becomes theirs.
 *
 * THE DIGEST IS NOT A SMALLER TILE. `MonitorTile` is a card with a
 * settings menu, a threshold editor, a caveat sheet and a roving-tabindex
 * grid pattern behind it — everything needed to MANAGE a monitor, at
 * twenty of them. Home is not where monitors are managed; it is where they
 * are read on the way past. So a row is one link, one tab stop, and the
 * page it links to is the one that manages them.
 *
 * WHAT DOES NOT GET COMPRESSED AWAY: the honesty marks. A bounded value
 * still renders its `≤` (it is inside the server's `value_text`) and still
 * says in words that it is a ceiling; a provisional one still says it is
 * still settling. Those are the difference between "denies 29.5% of
 * claims" and "denies at most 29.5%, over a population too small to
 * measure" — and a digest is exactly where a number gets quoted from.
 */
export function MonitorDigest({
  query,
  moved,
}: {
  query: {
    data: { tiles: MonitorsTile[] } | undefined;
    isPending: boolean;
    error: unknown;
  };
  /** Pin ids that moved materially at this load, from `homeShape`. */
  moved: readonly string[];
}) {
  const tiles = query.data?.tiles ?? [];
  const movedSet = new Set(moved);
  /**
   * MOVED FIRST — and "moved" means the same thing here as in the count
   * above it.
   *
   * `orderTilesForGrid` bands by the TILE's own delta, which is the right
   * ordering for the Monitors grid and the wrong one for a digest of four:
   * a monitor whose leading cell changed carries no comparable delta at
   * all, so it sorts into "nothing to compare" and falls off the end —
   * while the heading counts it among the ones that moved. Four moved, one
   * of them not on screen and none of the four rows saying which. The
   * union that promotes this zone is the same union that orders it, and
   * the band ordering decides everything after.
   *
   * A stable sort: within each half the platform's own order survives.
   */
  const ordered = orderTilesForGrid(tiles)
    .map((tile, index) => ({ tile, index, moved: movedSet.has(tile.pinId) ? 0 : 1 }))
    .sort((a, b) => a.moved - b.moved || a.index - b.index)
    .map((entry) => entry.tile);
  const digest = ordered.slice(0, DIGEST_COUNT);
  const rest = ordered.length - digest.length;

  return (
    <section
      id="home-monitors"
      tabIndex={-1}
      aria-labelledby="home-monitors-heading"
      className="space-y-3 outline-none"
    >
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id="home-monitors-heading"
          className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
        >
          Your monitors
        </h2>
        {query.data && tiles.length > 0 && (
          <p className="num text-micro text-muted-foreground">
            {/* The census, in one clause, so the count in the heading
                reconciles to what is on screen. */}
            {tiles.length} monitor{tiles.length === 1 ? "" : "s"} re-run at this load
            {movedSet.size > 0
              ? `, ${movedSet.size} moved enough to brief you`
              : ", none moved enough to brief you"}
          </p>
        )}
      </header>

      {query.data ? (
        tiles.length === 0 ? (
          <MonitorInvitation />
        ) : (
          <>
            <ul className="grid gap-2 md:grid-cols-2">
              {digest.map((tile) => (
                <DigestRow key={tile.pinId} tile={tile} moved={movedSet.has(tile.pinId)} />
              ))}
            </ul>
            <p className="text-micro text-muted-foreground">
              <Link
                to="/monitors"
                className="focus-ring inline-flex items-center gap-1 rounded underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
              >
                {rest > 0
                  ? `All ${tiles.length} monitors, with their thresholds`
                  : "Your monitors, with their thresholds"}
                <ArrowRight aria-hidden className="size-2.5" />
              </Link>
            </p>
          </>
        )
      ) : query.isPending ? (
        <p role="status" aria-live="polite" className="text-body text-muted-foreground">
          Re-running your monitors…
        </p>
      ) : (
        <p
          role="alert"
          className="flex max-w-[64ch] items-start gap-1.5 text-meta leading-snug text-negative"
        >
          <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0" />
          <span>
            Could not read your monitors.{" "}
            {query.error instanceof Error
              ? query.error.message
              : "The request did not complete."}{" "}
            Nothing here is out of date — there is nothing here.
          </span>
        </p>
      )}
    </section>
  );
}

/**
 * ONE MONITOR, on the way past.
 *
 * The whole row is one link to the investigation behind the number,
 * because every tile IS a real investigation with a real trace — not a
 * figure computed off to the side. One tab stop, and Enter opens it.
 */
function DigestRow({ tile, moved }: { tile: MonitorsTile; moved: boolean }) {
  const unavailable = tile.status !== "ok";
  const band = tileBand(tile);
  const body = (
    <>
      <p className="flex items-baseline justify-between gap-2">
        <span className="min-w-0 truncate text-meta font-medium leading-snug" title={tile.label}>
          {tile.label}
        </span>
        {moved && (
          <span className="shrink-0 text-micro font-medium uppercase tracking-wide text-foreground/70">
            Moved
          </span>
        )}
      </p>
      {unavailable ? (
        <p className="text-meta leading-snug text-muted-foreground">No value at this load</p>
      ) : (
        <>
          {/* The number, with the marks that change what it IS. `valueText`
              already carries the server's `≤`; the words beside it say
              what that means. */}
          <p className="numeral text-lead leading-none">
            {tile.valueText}
            <ValueMarks integrity={tile.integrity} />
          </p>
          <DeltaChip tile={tile} band={band} />
        </>
      )}
    </>
  );

  const className = cn(
    "flex flex-col gap-1.5 rounded-xl border bg-surface-raised p-3",
    "raised raised-hover transition-[border-color,box-shadow] duration-200",
    unavailable && "border-dashed shadow-none",
  );

  return (
    <li>
      {tile.investigationId ? (
        <Link
          to={investigationLinkFor(tile.investigationId, "")}
          aria-label={`${tile.label}: ${
            unavailable ? "no value at this load" : tile.valueText
          } — open the investigation behind it`}
          className={cn(className, "focus-ring hover:border-ring/40")}
        >
          {body}
        </Link>
      ) : (
        <div className={className}>{body}</div>
      )}
    </li>
  );
}

/**
 * WHAT IT DID, in one chip, in the metric's own unit.
 *
 * The magnitude is the server's rendered `deltaText` ("3.6 points",
 * "$4,201.00") and is never re-derived: a rate's movement is POINTS, and a
 * client that turned 0.0358 into "+3.6%" would print a number nobody can
 * tell from a relative change. The MARK follows `deltaMark`, which is the
 * one place that decides an arrow may be drawn at all — a re-measurement of
 * the same window earns no direction, because that is the data catching up
 * rather than the world moving.
 *
 * Absence is said, not drawn as flatness: a monitor with nothing to compare
 * against says so.
 */
function DeltaChip({ tile, band }: { tile: MonitorsTile; band: number }) {
  const delta = tile.delta;
  if (!delta || !delta.comparable) {
    return (
      // Two lines at most. The server's refusal sentence names both cells
      // and runs to forty words; the whole statement is one tap away on
      // the investigation, and a digest row that grows to five lines
      // stops being a digest.
      <p className="line-clamp-2 text-micro leading-snug text-muted-foreground">
        {band === TILE_BANDS.noComparison && delta?.notComparableReason
          ? readableStatement(delta.notComparableReason)
          : "Nothing to compare against at this load"}
      </p>
    );
  }
  const flat = delta.direction === "flat" || delta.delta === 0;
  const mark = deltaMark(delta);
  const word = directionWord(delta);
  return (
    <p
      data-digest-delta={mark}
      className="num flex flex-wrap items-baseline gap-x-1.5 text-micro leading-snug"
    >
      <span className="inline-flex items-baseline gap-1 text-foreground/80">
        {mark === "up" ? (
          <ArrowUpRight aria-hidden className="size-2.5 translate-y-0.5" />
        ) : mark === "down" ? (
          <ArrowDownRight aria-hidden className="size-2.5 translate-y-0.5" />
        ) : (
          <span aria-hidden className="text-muted-foreground">
            ·
          </span>
        )}
        {flat ? "no change" : word === "" ? delta.deltaText : `${word} ${delta.deltaText}`}
      </span>
      <span className="text-muted-foreground">
        {delta.reference === "baseline"
          ? "since you started monitoring"
          : `from ${delta.priorValueText || "the prior load"}`}
      </span>
    </p>
  );
}

/**
 * NOTHING PINNED YET — an invitation, not a shrug.
 *
 * One sentence and one example, and both describe an affordance that
 * really exists: "Monitor this" is one click away on any chart, finding or
 * worklist an answer produces, and the composer genuinely understands a
 * monitor declaration in words.
 */
export function MonitorInvitation() {
  return (
    <p
      data-monitor-invitation
      className="max-w-[64ch] text-body leading-relaxed text-muted-foreground"
    >
      Nothing is being monitored yet. Monitor what matters to you — choose{" "}
      <span className="font-medium text-foreground">Monitor this</span> on anything Revi shows
      you, or ask{" "}
      <span className="font-medium text-foreground">
        “watch Halvern&apos;s denial rate”
      </span>{" "}
      and Revi will answer once, then keep watching it every load.
    </p>
  );
}
