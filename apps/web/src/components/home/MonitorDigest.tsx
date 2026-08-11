"use client";

import {
  AlertTriangle,
  ArrowDownRight,
  ArrowUpRight,
  ChevronDown,
  MessageSquarePlus,
} from "lucide-react";
import { Link } from "react-router-dom";
import { useId, useRef, useState, type KeyboardEvent } from "react";

import { WarningList } from "@/components/banners/WarningBanner";
import { BriefEntryRow, type BriefLeadHandle } from "@/components/monitors/BriefEntryRow";
import { IntegrityAtom, ValueMarks } from "@/components/monitors/IntegrityAtom";
import { DeltaLine, deltaMark, directionWord, thresholdSourceLabel } from "@/components/monitors/DeltaLine";
import { MonitorHistoryChart } from "@/components/monitors/MonitorHistoryChart";
import { MonitorManagement } from "@/components/monitors/MonitorManagement";
import { Sparkline } from "@/components/monitors/Sparkline";
import { Button } from "@/components/ui/button";
import { humanizeIsoDates, isoRangeLabel } from "@/lib/format";
import { investigationLinkFor } from "@/lib/links";
import { MONITOR_HISTORY_MIN, monitorReadings } from "@/lib/monitorHistory";
import { MONITORS_ZONE_ID } from "@/lib/monitorsAnchor";
import { capitalizeOpening, readableLabel, readableStatement } from "@/lib/prose";
import {
  orderTilesForGrid,
  TILE_BANDS,
  tileBand,
  tileCensus,
  type BriefEntry,
  type MonitorsData,
  type MonitorsPin,
  type MonitorsTile,
} from "@/lib/monitors";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/** How many monitors the digest shows before it offers the rest. */
const DIGEST_COUNT = 4;

/**
 * YOUR MONITORS — the zone that evolves as somebody makes this app theirs,
 * and, since `/monitors` retired, the only place they are read and managed.
 *
 * On day one this is an invitation and nothing else: the tenant has pinned
 * nothing, so there is nothing to digest and a grid of empty slots would be
 * a worse lie than a sentence. Once monitors exist it becomes a digest —
 * name, current value, what it did — and when any of them moved materially
 * at this load the whole zone moves ABOVE the detected anomalies (see
 * `homeShape`). A landing page that never re-orders itself around what
 * somebody asked to be told about never becomes theirs.
 *
 * THE DIGEST USED TO DEFER, AND THERE IS NOWHERE LEFT TO DEFER TO. This
 * file's header said "Home is not where monitors are managed; it is where
 * they are read on the way past", and every row was a link to the surface
 * that managed them. That surface is gone — the owner's reading of the pair
 * was that Home is simply the better one — so the deferral becomes an
 * EXPANSION: a tile opens in place into the monitor's own detail, carrying
 * everything the full tile carried plus the things a grid of twenty cards
 * could never afford (the stored readings as a chart, the lines this
 * monitor put in the brief, a way to ask about it).
 *
 * HOME STAYS CALM. Nothing management-density renders until a tile is
 * opened, one opens at a time, and a page nobody has touched is the same
 * four compact tiles it always was.
 *
 * WHAT DOES NOT GET COMPRESSED AWAY, at either size: the honesty marks. A
 * bounded value still renders its `≤` (it is inside the server's
 * `value_text`) and still says in words that it is a ceiling; a provisional
 * one still says it is still settling. Those are the difference between
 * "denies 29.5% of claims" and "denies at most 29.5%, over a population too
 * small to measure" — and a digest is exactly where a number gets quoted
 * from.
 */
export function MonitorDigest({
  query,
  moved,
  entries,
  leads,
}: {
  query: {
    data: MonitorsData | undefined;
    isPending: boolean;
    error: unknown;
  };
  /** Pin ids that moved materially at this load, from `homeShape`. */
  moved: readonly string[];
  /** This load's brief, so a monitor can show the lines it produced. */
  entries?: readonly BriefEntry[];
  /** The leads behind those lines, so a brief row is work rather than news. */
  leads?: ReadonlyMap<string, BriefLeadHandle>;
}) {
  const tiles = query.data?.tiles ?? [];
  const movedSet = new Set(moved);
  /**
   * ONE OPEN AT A TIME — accordion semantics, and a deliberate choice.
   *
   * An expanded monitor is several screens of detail; two of them open at
   * once turns a calm landing page into a scroll with no landmarks in it.
   * Opening one closes the other, which is also what makes "one tab stop
   * per monitor" hold at twenty of them.
   */
  const [openPin, setOpenPin] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const listId = useId();

  /**
   * MOVED FIRST — and "moved" means the same thing here as in the count
   * above it.
   *
   * `orderTilesForGrid` bands by the TILE's own delta, which is the right
   * ordering for a list of every monitor and the wrong one for a digest of
   * four: a monitor whose leading cell changed carries no comparable delta
   * at all, so it sorts into "nothing to compare" and falls off the end —
   * while the heading counts it among the ones that moved. Four moved, one
   * of them not on screen and none of the four rows saying which. The union
   * that promotes this zone is the same union that orders it, and the band
   * ordering decides everything after.
   *
   * A stable sort: within each half the platform's own order survives.
   */
  const ordered = orderTilesForGrid(tiles)
    .map((tile, index) => ({ tile, index, moved: movedSet.has(tile.pinId) ? 0 : 1 }))
    .sort((a, b) => a.moved - b.moved || a.index - b.index)
    .map((entry) => entry.tile);
  const shown = showAll ? ordered : ordered.slice(0, DIGEST_COUNT);
  const rest = ordered.length - shown.length;

  // What each monitor IS (spec, window mode, threshold) — a different
  // question from what it read, and the one the sensitivity editor needs.
  // Read once for the zone rather than per tile.
  const knownMonitors = useSessionStore((s) => s.knownMonitors);
  const pinsById = new Map(knownMonitors.map((pin) => [pin.pinId, pin]));

  const entriesFor = (pinId: string): BriefEntry[] =>
    (entries ?? []).filter((entry) => entry.pinId === pinId);

  return (
    <section
      id={MONITORS_ZONE_ID}
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
            {/* The read's own caveats. They travelled with the retired
                surface's grid and would otherwise have gone with it — a
                monitors payload that warns about itself and a client that
                drops the warning is the one failure this zone may not
                have. */}
            <WarningList
              warnings={query.data.warnings.map((w) => ({ ...w, type: "warning" as const }))}
            />

            <p id="home-monitor-hint" className="sr-only">
              Each monitor opens in place. Press Enter to see its detail and Escape to close it.
            </p>
            <ul id={listId} className="grid gap-2 md:grid-cols-2">
              {shown.map((tile) => (
                <DigestTile
                  key={tile.pinId}
                  tile={tile}
                  {...(pinsById.get(tile.pinId) ? { pin: pinsById.get(tile.pinId) } : {})}
                  moved={movedSet.has(tile.pinId)}
                  entries={entriesFor(tile.pinId)}
                  {...(leads ? { leads } : {})}
                  expanded={openPin === tile.pinId}
                  onToggle={() =>
                    setOpenPin((current) => (current === tile.pinId ? null : tile.pinId))
                  }
                />
              ))}
            </ul>

            {/* THE ORDER IS DECLARED, once the whole list is on screen. An
                order nobody declared is an order nobody can trust, and
                "first" is the strongest statement a list makes — it used to
                be made by creation date. Not shown over four of nine, where
                the sentence would be describing tiles that are not there. */}
            {showAll && (
              <p data-tile-census className="num max-w-[64ch] text-micro text-muted-foreground">
                {tileCensus(ordered).join(" · ")}. Moved first, then unchanged, then nothing to
                compare, then unavailable — within each, the order the platform published.
              </p>
            )}

            {rest > 0 ? (
              <Button
                variant="outline"
                size="xs"
                onClick={() => setShowAll(true)}
                aria-expanded={false}
                aria-controls={listId}
                className="gap-1 text-meta font-normal"
              >
                <ChevronDown className="size-3" />
                {/* "Levels", not "thresholds". Every sentence this product
                    writes about a monitor's sensitivity calls it a level,
                    and two words for one control is how a reader concludes
                    they are two controls. */}
                Show the other {rest} monitor{rest === 1 ? "" : "s"}, with their levels
              </Button>
            ) : (
              showAll &&
              ordered.length > DIGEST_COUNT && (
                <Button
                  variant="outline"
                  size="xs"
                  onClick={() => {
                    setShowAll(false);
                    // Nothing may stay open behind the fold: an expanded
                    // monitor that is collapsed out of the list takes its
                    // panel with it, and the reader's focus with that.
                    setOpenPin(null);
                  }}
                  aria-expanded
                  aria-controls={listId}
                  className="gap-1 text-meta font-normal"
                >
                  Show the top {DIGEST_COUNT}
                </Button>
              )
            )}
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
 * ONE MONITOR — compact on the way past, its own detail when opened.
 *
 * THE WHOLE TILE IS THE DISCLOSURE. It used to be a link into the
 * investigation the monitor was created from, and the owner's reading of
 * that was the right one: "I'm confused by the experience of clicking on a
 * monitor and having it take you into the chat that started the monitor
 * instead of some kind of more detailed analysis view." A monitor is a
 * standing thing; the conversation that started it is provenance, not
 * destination. So a click opens the monitor, and the investigation demotes
 * to a quiet link inside — still there, because a tile that cannot be
 * traced back to a real investigation with a real trace is exactly the
 * "figure computed off to the side" this product refuses to draw.
 *
 * ONE TAB STOP PER MONITOR, and no roving-tabindex machinery to get it. The
 * retired grid gave each tile `tabIndex={0}` and pushed its own controls to
 * `-1` until Enter "entered" it — a bespoke pattern that existed because
 * five controls per card is a hundred tab stops across twenty monitors.
 * Collapsed, this tile has exactly one control; expanded, its controls are
 * ordinary and in reading order. The browser's own semantics do the work.
 */
export function DigestTile({
  tile,
  pin,
  moved,
  entries = [],
  leads,
  expanded,
  onToggle,
}: {
  tile: MonitorsTile;
  pin?: MonitorsPin;
  moved: boolean;
  /** The brief lines this monitor produced at this load. */
  entries?: readonly BriefEntry[];
  leads?: ReadonlyMap<string, BriefLeadHandle>;
  expanded: boolean;
  onToggle: () => void;
}) {
  const unavailable = tile.status !== "ok";
  const band = tileBand(tile);
  // The repaired card title. Three of this tenant's seven monitors are
  // registered in lower case, and a digest of them opened three of four
  // cards mid-sentence. See `lib/prose::readableLabel`.
  const label = readableLabel(tile.label);
  /**
   * THE READINGS THIS MONITOR HAS STORED — three at most, each one an
   * evaluation at a named data load, none of them interpolated. Below
   * `MONITOR_HISTORY_MIN` nothing is drawn at either size: a line through
   * two dots is a trajectory nobody measured, and five of this tenant's
   * seven monitors carry only two readings.
   */
  const readings = monitorReadings(tile);
  const panelId = `monitor-detail-${tile.pinId}`;
  const triggerRef = useRef<HTMLButtonElement>(null);

  /**
   * ESCAPE CLOSES IT, from anywhere inside.
   *
   * Handled on the list item so it catches the key wherever focus is in the
   * panel — the sensitivity form, the confirm, the brief rows — and focus
   * goes back to the control that opened it rather than to the top of the
   * document.
   */
  const onKeyDown = (event: KeyboardEvent<HTMLLIElement>): void => {
    if (event.key !== "Escape" || !expanded) return;
    event.stopPropagation();
    onToggle();
    triggerRef.current?.focus();
  };

  return (
    <li
      data-tile-pin={tile.pinId}
      data-tile-expanded={expanded ? "true" : "false"}
      onKeyDown={onKeyDown}
      className={cn(
        "flex flex-col rounded-xl border bg-surface-raised",
        "raised raised-hover transition-[border-color,box-shadow] duration-200",
        unavailable && "border-dashed shadow-none",
        // An open monitor is the subject of the zone, so it takes the whole
        // row rather than sitting in a two-column grid beside a compact
        // one — the detail is a chart, a brief and two forms wide.
        expanded && "md:col-span-2 border-ring/40",
      )}
    >
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={expanded}
        aria-controls={panelId}
        aria-describedby="home-monitor-hint"
        onClick={onToggle}
        // NO HOVER-REVEAL AND NO SEPARATE TARGET. The card is the control:
        // an affordance that only exists once a pointer crosses it does not
        // exist on a touch screen, in a screenshot or on a projector.
        className="focus-ring flex w-full flex-col gap-1.5 rounded-xl p-3 text-left"
      >
        {/* Spans throughout: a button's content model is phrasing content,
            and a paragraph inside one is markup the browser silently
            re-parents. */}
        <span className="flex items-baseline justify-between gap-2">
          <span
            className="min-w-0 truncate text-meta font-medium leading-snug"
            title={tile.label}
          >
            {label}
          </span>
          <span className="flex shrink-0 items-center gap-1.5">
            {moved && (
              <span className="text-micro font-medium uppercase tracking-wide text-foreground/70">
                Moved
              </span>
            )}
            <ChevronDown
              aria-hidden
              className={cn(
                "size-3 text-muted-foreground transition-transform duration-200",
                expanded && "rotate-180",
              )}
            />
          </span>
        </span>
        {unavailable ? (
          <span className="text-meta leading-snug text-muted-foreground">
            No value at this load
          </span>
        ) : (
          <>
            {/* The number, with the marks that change what it IS.
                `valueText` already carries the server's `≤`; the words
                beside it say what that means. At display size: this is a
                surface whose whole job is "what does this number say
                today", and the marks travel unchanged — a ceiling at 30px
                is still a ceiling. */}
            <span className="numeral block text-figure leading-none">
              {tile.valueText}
              <ValueMarks integrity={tile.integrity} />
            </span>
            {/* Collapsed, the movement is a chip at reading size and the
                stored readings are a 76px line beside it. Expanded, the
                detail below states both in full — what it moved from,
                whether the window was re-measured, whose level briefed it,
                and the readings as a chart with the loads named — so the
                glance forms stand down rather than saying the same things
                twice in two shapes. */}
            {!expanded && (
              <span className="flex items-end justify-between gap-2">
                <DeltaChip tile={tile} band={band} />
                {readings.length >= MONITOR_HISTORY_MIN && (
                  <Sparkline readings={readings} className="mb-0.5" />
                )}
              </span>
            )}
          </>
        )}
        <span className="sr-only">
          {expanded ? "Hide this monitor's detail" : "Show this monitor's detail"}
        </span>
      </button>

      {expanded && (
        <div id={panelId} className="fade-up space-y-3 border-t px-3 py-3">
          <MonitorDetail tile={tile} pin={pin} entries={entries} leads={leads} label={label} />
        </div>
      )}
    </li>
  );
}

/**
 * THE MONITOR ITSELF, opened.
 *
 * Everything the retired full-surface tile carried, plus the three things
 * it had no room for: the stored readings as a chart somebody can read, the
 * lines this monitor actually put in the brief, and a way to start a
 * conversation about it.
 *
 * The order is the order somebody reads it in — what it did, what it has
 * read before, what to know about the number, what it said in the brief,
 * what to do next — and management comes last, because changing a
 * monitor's sensitivity is a decision made after the reading rather than
 * instead of it.
 */
function MonitorDetail({
  tile,
  pin,
  entries,
  leads,
  label,
}: {
  tile: MonitorsTile;
  pin?: MonitorsPin;
  entries: readonly BriefEntry[];
  leads?: ReadonlyMap<string, BriefLeadHandle>;
  label: string;
}) {
  const readings = monitorReadings(tile);
  /**
   * The BASELINE movement, when it says something the prior-load movement
   * does not. A monitor that drifted five points since somebody started
   * watching it while moving 0.2 overnight is telling two true stories, and
   * showing only the second hides the reason the monitor exists.
   */
  const baseline =
    tile.baselineDelta && tile.baselineDelta.deltaText !== tile.delta?.deltaText
      ? tile.baselineDelta
      : undefined;

  return (
    <>
      {/* WHICH CELL the number is about, resolved to dimension members
          rather than read off the title. A monitor whose label names one
          payer and whose value is another payer's is the defect that gated
          round 7; this is the line that makes the pair checkable. */}
      {tile.headlineSubjectLabel !== "" && !tile.label.includes(tile.headlineSubjectLabel) && (
        <p className="text-micro leading-snug text-muted-foreground">
          Measuring {tile.headlineSubjectLabel}
        </p>
      )}

      {tile.status !== "ok" ? (
        // The platform's own error vocabulary, verbatim. A stored monitor
        // can stop being answerable — a catalog change, a pack redeploy —
        // and a tile that went blank without saying so would look like a
        // zero.
        tile.unavailableReason && (
          <p className="text-micro leading-snug text-muted-foreground">
            {readableStatement(tile.unavailableReason)}
          </p>
        )
      ) : (
        <div className="space-y-1">
          {tile.delta ? (
            <DeltaLine delta={tile.delta} />
          ) : (
            // NO COMPARISON PUBLISHED — said in words rather than left as
            // white space beside the monitors that did move. This states a
            // fact about the payload and invents no reason for it: when the
            // server publishes a non-comparable delta with its own sentence
            // ("first reading at this load"), that sentence is what renders
            // instead of this one.
            <p data-delta-absent className="text-micro leading-snug text-muted-foreground">
              No movement is published for this monitor at this load.
            </p>
          )}
          {baseline && <DeltaLine delta={baseline} />}
          {/* THE RULE THAT DECIDED WHETHER THIS BRIEFED YOU, stated rather
              than hovered. On the retired tile whose level it was rode in a
              chip with the rule in its tooltip; a reader who has opened a
              monitor to decide whether to change its sensitivity should not
              have to find that with a pointer. The sentence is the
              server's — it names the rule and what it compared against, so
              the gate is checkable rather than trusted.

              ONLY WHERE A GATE WAS ACTUALLY APPLIED. On a non-comparable
              delta the server puts its refusal sentence in BOTH fields, so
              this rendered "Revi's recommended level — the earlier load's
              leading cell was …" directly under `DeltaLine`'s copy of the
              same sentence: one fact, twice, the second time attributed to
              a threshold that never ran. */}
          {tile.delta?.comparable === true && tile.delta.materialityNote !== "" && (
            <p data-materiality-rule className="text-micro leading-snug text-muted-foreground">
              <span className="font-medium text-foreground/80">
                {thresholdSourceLabel(tile.delta)}
              </span>{" "}
              — {readableStatement(tile.delta.materialityNote)}
            </p>
          )}
        </div>
      )}

      {/* THE READINGS, AS A PICTURE SOMEBODY CAN READ. Three at most, each
          one stored at a named data load, nothing between them. Below three
          there is no chart and the absence is stated — the alternative is a
          line through two dots, which is a trajectory nobody measured. */}
      {readings.length >= MONITOR_HISTORY_MIN ? (
        <MonitorHistoryChart readings={readings} />
      ) : (
        <p className="text-micro leading-snug text-muted-foreground">
          Revi has stored {readings.length} reading{readings.length === 1 ? "" : "s"} of this
          monitor. A line needs three — two points are a slope with no shape behind it.
        </p>
      )}

      {/* The headline finding's own sentence — what the number is ABOUT.
          Unclamped here: this is the detail, not the tile, and the whole
          statement is what somebody opened it for. */}
      {tile.headlineStatement && (
        // `humanizeIsoDates` on the way through, the same repair
        // `PortfolioPanel` makes to the same class of server sentence: this
        // one arrives as "…over 2026-07-01..2026-07-31." and an ISO range
        // is banned on a default surface (docs/client-language.md §4). The
        // retired tile clamped this to two lines and the range fell off the
        // end; the detail shows it whole, which is where it started
        // mattering.
        <p className="text-micro leading-snug text-muted-foreground">
          {readableStatement(humanizeIsoDates(tile.headlineStatement))}
        </p>
      )}

      <IntegrityAtom integrity={tile.integrity} warnings={tile.warnings} />

      {/* The MEASURED WINDOW — the fact that decides what the number means,
          since two relative windows can resolve to the same dates. */}
      {tile.windowStart && tile.windowEnd && (
        <p className="num text-micro text-muted-foreground">
          {isoRangeLabel(tile.windowStart, tile.windowEnd)}
        </p>
      )}

      {/* WHAT THIS MONITOR PUT IN YOUR BRIEF, if anything. The join is the
          brief entry's own `pin_id`, so this is not a second rendering of
          the same fact — it is the same rows the brief renders, filtered to
          this monitor, with the same lead controls on them. A monitor that
          briefed somebody this morning and cannot show them what it said is
          asking them to go and look for it. */}
      {entries.length > 0 && (
        <section className="space-y-1.5">
          <h4 className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
            What it said in this load&apos;s brief
          </h4>
          <ul className="space-y-2 border-l pl-4">
            {entries.map((entry, index) => (
              <BriefEntryRow
                key={`${entry.kind}-${entry.pinId ?? index}`}
                entry={entry}
                {...(entry.anomalyId && leads?.get(entry.anomalyId)
                  ? { lead: leads.get(entry.anomalyId)! }
                  : {})}
              />
            ))}
          </ul>
        </section>
      )}

      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <AskAboutThis tile={tile} label={label} />
        {/* PROVENANCE, DEMOTED. Every monitor IS a real investigation with a
            real trace rather than a figure computed off to the side, and
            this is where that is checkable. It is no longer what a click on
            the monitor does: the conversation that started a monitor is
            where it came from, not what it is. */}
        {tile.investigationId && (
          <Link
            to={investigationLinkFor(tile.investigationId, "")}
            className="focus-ring rounded text-micro text-muted-foreground underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:text-foreground hover:decoration-foreground"
          >
            View the analysis this came from
          </Link>
        )}
      </div>

      <MonitorManagement tile={tile} {...(pin ? { pin } : {})} />
    </>
  );
}

/**
 * "ASK ABOUT THIS" — the conversation a monitor is supposed to start.
 *
 * A monitor tells somebody a number moved. The next thing they say out loud
 * is "why", and until now the surface's only answer was a link into the
 * conversation that created the monitor months ago.
 *
 * It PREFILLS rather than asks. The question lands in the composer as plain
 * editable text, naming the cell this monitor measures, and nothing hidden
 * travels with it — no spec, no scope, no referent — so what the analyst
 * reads in the box is the whole of what they will send. They can rewrite
 * it, add to it, or delete it; pressing send runs the ordinary path any
 * typed question runs.
 *
 * Two forms, because a leading question is not honest on a monitor that did
 * not move: "why did it change" is asked of a monitor that changed.
 */
function AskAboutThis({ tile, label }: { tile: MonitorsTile; label: string }) {
  const setComposerDraft = useSessionStore((s) => s.setComposerDraft);
  const question = askAboutQuestion(tile, label);
  return (
    <Button
      variant="outline"
      size="xs"
      onClick={() => setComposerDraft(question)}
      // The question itself, so a reader who cannot see the composer fill
      // knows what this control is about to put there.
      title={question}
      className="gap-1.5 text-meta font-normal"
    >
      <MessageSquarePlus className="size-3" />
      Ask about this
    </Button>
  );
}

/**
 * The question, composed from what this monitor measures.
 *
 * The subject is the monitor's own label plus the cell it resolved to when
 * the label does not already name it — "denial rate by payer, monthly for
 * Ashvale Health Plan" — which is the same pair the detail's "Measuring …"
 * line publishes.
 */
function askAboutQuestion(tile: MonitorsTile, label: string): string {
  const subject =
    tile.headlineSubjectLabel !== "" && !tile.label.includes(tile.headlineSubjectLabel)
      ? `${label} for ${tile.headlineSubjectLabel}`
      : label;
  const changed = tile.delta?.comparable === true && (tile.delta.delta ?? 0) !== 0;
  return changed
    ? `Why did ${subject} change at this load?`
    : `What should I know about ${subject} at this load?`;
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
      // and runs to forty words; the whole statement is one click away in
      // the monitor's own detail, and a digest row that grows to five lines
      // stops being a digest.
      <span className="line-clamp-2 min-w-0 text-micro leading-snug text-muted-foreground">
        {band === TILE_BANDS.noComparison && delta?.notComparableReason
          ? readableStatement(delta.notComparableReason)
          : "Nothing to compare against at this load"}
      </span>
    );
  }
  const flat = delta.direction === "flat" || delta.delta === 0;
  const mark = deltaMark(delta);
  const word = directionWord(delta);
  /**
   * THE MOVEMENT, AT A SIZE SOMEBODY READS.
   *
   * "up 7.3 points" was 12px muted ink under a 17px value — the news on a
   * monitoring surface, set smaller than the caption explaining it, and
   * opening in lower case. Both are fixed here and neither is a re-wording:
   * the magnitude is still the server's own `deltaText` in the metric's own
   * unit (a rate moves in POINTS), the mark still follows `deltaMark`, and
   * the only change to the string is the opening capital every other card
   * title on this page now takes.
   */
  const magnitude = capitalizeOpening(
    flat ? "no change" : word === "" ? delta.deltaText : `${word} ${delta.deltaText}`,
  );
  return (
    <span data-digest-delta={mark} className="num block min-w-0 leading-snug">
      <span className="numeral flex items-baseline gap-1 text-lead leading-none text-foreground">
        {mark === "up" ? (
          <ArrowUpRight aria-hidden className="size-3 shrink-0 translate-y-0.5" />
        ) : mark === "down" ? (
          <ArrowDownRight aria-hidden className="size-3 shrink-0 translate-y-0.5" />
        ) : (
          <span aria-hidden className="text-muted-foreground">
            ·
          </span>
        )}
        {magnitude}
      </span>
      <span className="mt-0.5 block text-micro text-muted-foreground">
        {delta.reference === "baseline"
          ? "since you started monitoring"
          : `from ${delta.priorValueText || "the prior load"}`}
      </span>
    </span>
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
