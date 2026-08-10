"use client";

import { MoreHorizontal } from "lucide-react";
import { Link } from "react-router-dom";
import { useEffect, useRef, useState } from "react";

import { DeltaLine, ThresholdNote } from "@/components/monitors/DeltaLine";
import { IntegrityAtom, ValueMarks } from "@/components/monitors/IntegrityAtom";
import { MonitorSensitivityForm } from "@/components/monitors/MonitorSensitivity";
import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { investigationLinkFor } from "@/lib/links";
import { readableStatement } from "@/lib/prose";
import type { MonitorsPin, MonitorsTile, MonitorModel } from "@/lib/monitors";
import { useSessionStore } from "@/lib/store";
import { isoRangeLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Everything inside a tile that the browser would give its own tab stop. */
const FOCUSABLE = 'a[href], button:not([disabled]), input, select, textarea, [tabindex]';

/**
 * ONE MONITOR, at this load.
 *
 * The anatomy is the calm answer's, compressed to something readable at a
 * glance and stacked twenty times without becoming a monitoring console:
 * a label, the number, what it did, and the integrity line. No sparkline —
 * a 40px trend behind a figure is decoration that implies a shape the tile
 * has not published and cannot defend, and this surface's whole argument
 * is that a quiet morning should look quiet.
 *
 * Four rules the tile follows and the review rounds asked for by name:
 *
 *   THE INTEGRITY ATOM IS NOT OPTIONAL. It is rendered on every tile
 *     including an unavailable one, because "no value at this load" is
 *     still a claim about data and the grade is still what says how far it
 *     may be taken. The parser guarantees the atom exists; this component
 *     guarantees it is drawn.
 *   THE NUMBER CARRIES ITS MARKS. A ceiling says it is a ceiling and a
 *     provisional figure says it is still settling, in words, on the same
 *     line as the value.
 *   A TAP OPENS THE REAL INVESTIGATION. Every tile IS an investigation
 *     with a real trace, so the label is a link to its permalink rather
 *     than a drawer of pre-computed rows.
 *   ABSENCE IS SAID, NOT DRAWN AS FLATNESS. A tile that genuinely did not
 *     move renders "— no change from $176,112.25"; a tile with no
 *     comparison rendered nothing at all, so absence and stability were
 *     the same empty space. Measured live: 9 of 12 tiles silent.
 *
 * ONE TAB STOP, INTERNALS REACHABLE WITHIN. Five focusable controls per
 * tile is ~100 tab stops to cross a 20-monitor surface. The tile is a single
 * stop; Enter enters it and Escape leaves, which is the grid pattern and
 * the only one that keeps a dense surface crossable from the keyboard
 * without hiding anything from it.
 */
export function MonitorTile({ tile, pin }: { tile: MonitorsTile; pin?: MonitorsPin }) {
  const unavailable = tile.status !== "ok";
  /**
   * The BASELINE movement, when it says something the prior-load movement
   * does not.
   *
   * A tile that drifted five points since somebody started monitoring it
   * while moving 0.2 overnight is telling two true stories, and showing
   * only the second hides the reason the monitor exists. Shown when the two
   * magnitudes actually differ; a baseline delta identical to the prior
   * one is the same sentence twice.
   */
  const baseline =
    tile.baselineDelta && tile.baselineDelta.deltaText !== tile.delta?.deltaText
      ? tile.baselineDelta
      : undefined;

  const tileRef = useRef<HTMLLIElement>(null);
  const [entered, setEntered] = useState(false);

  /**
   * The tile's own controls are out of the tab order until the tile is
   * entered. Applied to the DOM rather than threaded through every child,
   * because the children are three different components (a link, a menu
   * trigger, the atom's count button) and none of them should have to know
   * it is inside a grid.
   *
   * Deliberately no dependency array: the set of controls changes with the
   * tile's own state (a refusal appears, a menu mounts), and re-applying
   * on every render of five nodes is cheaper than monitoring for it.
   */
  useEffect(() => {
    const node = tileRef.current;
    if (!node) return;
    for (const el of node.querySelectorAll<HTMLElement>(FOCUSABLE)) {
      el.tabIndex = entered ? 0 : -1;
    }
  });

  return (
    <li
      ref={tileRef}
      data-tile-pin={tile.pinId}
      data-tile-entered={entered ? "true" : "false"}
      tabIndex={0}
      aria-label={`${tile.label}: ${unavailable ? "no value at this load" : tile.valueText}`}
      aria-describedby="monitors-tile-hint"
      onKeyDown={(event) => {
        if (event.key === "Enter" && event.target === tileRef.current) {
          event.preventDefault();
          setEntered(true);
          // The first control is the label's own link to the
          // investigation, so Enter-then-Enter is "open this monitor" — the
          // gesture a pointer user gets from one click.
          const first = tileRef.current?.querySelector<HTMLElement>(FOCUSABLE);
          window.setTimeout(() => first?.focus(), 0);
        } else if (event.key === "Escape" && entered) {
          event.stopPropagation();
          setEntered(false);
          tileRef.current?.focus();
        }
      }}
      onFocus={(event) => {
        if (event.target !== tileRef.current) setEntered(true);
      }}
      onBlur={(event) => {
        if (!tileRef.current?.contains(event.relatedTarget as Node | null)) setEntered(false);
      }}
      // A LOOKED-AFTER SURFACE, not a monitored one. The warmth here is
      // three cheap things and no new visual language: a softer corner
      // (`xl`, the radius scale's top step, matching the answer card), a
      // raised surface with a MEASURED elevation token (the hardcoded
      // `shadow-[0_1px_2px_rgba(0,0,0,0.03)]` it replaces computed to
      // 1.068:1 against the page, which is no shadow at all), and a 180ms
      // border transition: long enough to feel like a
      // response, short enough not to be an effect.
      className={cn(
        "group relative flex flex-col gap-2 rounded-xl border bg-surface-raised p-3.5",
        "raised raised-hover transition-[border-color,box-shadow] duration-200",
        "hover:border-ring/40 focus-within:border-ring/40",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--ring)]",
        unavailable && "border-dashed shadow-none",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          {tile.investigationId ? (
            <Link
              to={investigationLinkFor(tile.investigationId, "")}
              title={`Open the investigation behind ${tile.label}`}
              // A PERSISTENT underline, not one that appears on hover. A
              // hover state does not exist on a touch screen or in a
              // screenshot, and this is the tile's only path to the
              // investigation behind its number — a link nobody can
              // identify is a link that did not happen.
              className="focus-ring min-w-0 rounded text-meta font-medium leading-snug underline decoration-foreground/30 underline-offset-[3px] transition-colors duration-150 hover:decoration-foreground"
            >
              {tile.label}
            </Link>
          ) : (
            <p className="min-w-0 text-meta font-medium leading-snug">{tile.label}</p>
          )}
          {/* WHICH CELL the number is about, resolved to dimension members
              rather than read off the title. A tile whose label names one
              payer and whose value is another payer's is the defect that
              gated round 7; this is the line that makes the pair
              checkable. Drawn only when it says something the label does
              not already contain. */}
          {tile.headlineSubjectLabel !== "" &&
            !tile.label.includes(tile.headlineSubjectLabel) && (
              <p className="text-micro leading-snug text-muted-foreground">
                Measuring {tile.headlineSubjectLabel}
              </p>
            )}
        </div>
        <TileMenu tile={tile} pin={pin} />
      </div>

      {unavailable ? (
        <>
          <p className="text-meta leading-snug text-muted-foreground">No value at this load</p>
          {/* The platform's own error vocabulary, verbatim. A stored spec
              can stop being answerable — a catalog change, a pack
              redeploy — and a tile that went blank without saying so would
              look like a zero. */}
          {tile.unavailableReason && (
            <p className="text-micro leading-snug text-muted-foreground">
              {readableStatement(tile.unavailableReason)}
            </p>
          )}
        </>
      ) : (
        <>
          <p className="numeral text-lead leading-none">
            {tile.valueText}
            <ValueMarks integrity={tile.integrity} />
          </p>
          {tile.delta ? (
            <DeltaLine delta={tile.delta} />
          ) : (
            // NO COMPARISON PUBLISHED — said in words rather than left as
            // white space beside the tiles that did move. This states a
            // fact about the payload and invents no reason for it: when
            // the server publishes a non-comparable delta with its own
            // sentence ("first reading at this load"), that sentence is
            // what renders instead of this one.
            <p data-delta-absent className="text-micro leading-snug text-muted-foreground">
              No movement is published for this monitor at this load.
            </p>
          )}
          {baseline && <DeltaLine delta={baseline} />}
          {/* The headline finding's own sentence — what the number is
              ABOUT. Two lines at most: this is a tile, and the whole
              statement is one tap away on the investigation.

              Two mechanical repairs and no re-wording: a rank over a set
              of ONE is not a rank ("ranks #1 of 1" — two of this tenant's
              tiles publish it, one of them the JOC account), and a stop
              printed twice is a defect in a join. See `lib/prose`. */}
          {tile.headlineStatement && (
            <p className="line-clamp-2 text-micro leading-snug text-muted-foreground">
              {readableStatement(tile.headlineStatement)}
            </p>
          )}
        </>
      )}

      <IntegrityAtom integrity={tile.integrity} warnings={tile.warnings} className="pt-0.5" />

      {/* The MEASURED WINDOW — the fact that decides what the number means,
          since two relative windows can resolve to the same dates. In
          solid muted ink: at 80% it measured 3.48:1 on card, 3.27:1 on the
          page and 3.16:1 on sunken, all below the 4.5:1 floor at 12px.
          Solid it is 5.24 / 4.80 / 4.57. */}
      <p className="num flex flex-wrap items-baseline gap-x-1.5 text-micro text-muted-foreground">
        {tile.windowStart && tile.windowEnd && (
          <span>{isoRangeLabel(tile.windowStart, tile.windowEnd)}</span>
        )}
        {tile.delta && <ThresholdNote delta={tile.delta} />}
      </p>
    </li>
  );
}

/**
 * The quiet menu: change what it takes to brief you, or stop monitoring.
 *
 * Both controls state their cost before the click. Changing sensitivity
 * RE-REGISTERS the monitor — the routes publish no partial update, so the
 * only honest way to change a threshold is to start the monitor again — and
 * that resets the baseline "since you started monitoring" is measured from.
 * The dialog says so in those words rather than letting an analyst
 * discover it tomorrow when a five-point drift becomes a fresh zero.
 */
function TileMenu({ tile, pin }: { tile: MonitorsTile; pin?: MonitorsPin }) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  /**
   * "Stop monitoring this" is ARMED before it fires.
   *
   * It was one click from ending a monitor's history, with the reassurance
   * copy sitting UNDER the button — read on the way past rather than
   * acknowledged. The two-step makes that sentence the thing the analyst
   * agrees to, which is what it was written to be, and it costs one
   * keystroke on the one control here that cannot be undone from this
   * surface: re-registering a monitor resets the baseline "since you
   * started monitoring" is measured from, so an undo would silently be a
   * different monitor.
   */
  const [armed, setArmed] = useState(false);
  const createMonitor = useSessionStore((s) => s.createMonitor);
  const removeMonitor = useSessionStore((s) => s.removeMonitor);
  const pendingKey = useSessionStore((s) => s.monitorPendingKey);
  const monitorError = useSessionStore((s) => s.monitorError);
  // WHY THERE IS NO PIN, when there is none. The settings this menu edits
  // ride on `GET /v1/monitors/pins`, which is a different read from the one
  // that drew the tile — so it can fail on its own, and it has: one
  // malformed monitor 500'd it tenant-wide and every tile's editor went grey
  // with no sentence anywhere on the page.
  const loadMonitors = useSessionStore((s) => s.loadMonitors);
  const monitorsError = useSessionStore((s) => s.monitorsError);
  const monitorsLoading = useSessionStore((s) => s.monitorsLoading);
  const monitorsLoaded = useSessionStore((s) => s.monitorsLoaded);
  const key = `tile:${tile.pinId}`;
  const pending = pendingKey === key;
  const refusal = monitorError?.key === key ? monitorError.message : undefined;

  /**
   * The one sentence this menu owes the reader when it has no settings to
   * show. Three states, three different facts — and never a bare disabled
   * control, which is the shape that says "this feature is off" about a
   * read that failed thirty seconds ago and would succeed on a reload.
   */
  const missingSettings = pin
    ? undefined
    : monitorsLoading
      ? { text: "Reading this monitor's settings…", failed: false }
      : monitorsError !== null
        ? {
            text: `Could not read this monitor's settings — reload to try again. ${monitorsError}`,
            failed: true,
          }
        : monitorsLoaded
          ? {
              // A 200 that did not contain this monitor. Measured live
              // alongside the 500, and it deserves its own sentence: the
              // read worked and this pin was not in it.
              text: "Could not read this monitor's settings — reload to try again. The deployment's monitor list came back without this monitor in it.",
              failed: true,
            }
          : { text: "This monitor's settings have not been read yet.", failed: false };

  const onSave = (monitor: MonitorModel): void => {
    if (!pin) return;
    void (async () => {
      // Create first, archive second. If the create is refused — an
      // illegal threshold unit — the monitor that exists is the one that
      // was already working, rather than none at all.
      await createMonitor(key, {
        spec: pin.spec,
        presentation: pin.presentation,
        label: pin.label,
        monitor,
      });
      if (useSessionStore.getState().monitorError?.key === key) return;
      await removeMonitor(`${key}:old`, pin.pinId);
      setEditing(false);
      setOpen(false);
    })();
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) {
          setEditing(false);
          setArmed(false);
        }
      }}
    >
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="xs"
          aria-label={`Settings for the monitor ${tile.label}`}
          // PERSISTENT, not hover-revealed. It was `opacity-0
          // group-hover:opacity-100`, which is no control at all on a
          // touch screen, in a screenshot or on a projector — and the
          // only way into a monitor's own settings. Quiet is the right
          // volume for it; invisible is not a volume. Filed four monitors
          // running by three reviewers. Solid `text-muted-foreground`,
          // not an opacity step: `contrast.test.ts` bans those on this
          // token because they drop 12px text under the AA floor, and a
          // control quiet enough to miss is the bug being fixed.
          className="size-5 shrink-0 rounded p-0 text-muted-foreground transition-colors duration-150 hover:text-foreground"
        >
          <MoreHorizontal className="size-3" />
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[22rem] max-w-[calc(100vw-2rem)] p-3">
        {editing && pin ? (
          <MonitorSensitivityForm
            {...(pin.monitor ? { initial: pin.monitor } : {})}
            submitLabel="Save and restart this monitor"
            pending={pending}
            {...(refusal ? { refusal } : {})}
            restartNote={
              pin.baselineValueText
                ? `Saving starts this monitor again. Its baseline becomes today's ${tile.valueText}, so “since you started monitoring” will measure from here instead of from ${pin.baselineValueText}.`
                : "Saving starts this monitor again, so “since you started monitoring” will measure from today's value."
            }
            onSubmit={onSave}
            onCancel={() => setEditing(false)}
          />
        ) : (
          <div className="space-y-2">
            <div>
              <p className="text-micro font-semibold uppercase tracking-wide text-muted-foreground">
                What this monitor measures
              </p>
              {/* THE SPEC, in the reader's own nouns. This panel is the one
                  control that lets somebody catch a monitor measuring the
                  wrong cell, and it was rendering the window note alone
                  while the summary rode on the wire unread. */}
              {pin?.specSummary && (
                <p className="mt-1 text-micro leading-snug text-foreground/80">
                  {pin.specSummary}
                </p>
              )}
              {/* The window mode in the server's own sentence: a moving
                  period (a real movement) or fixed dates (late-arriving
                  data). It decides how every delta on this tile should be
                  read, and it is not derivable from the spec on screen.
                  Only when this monitor's settings were actually read: with
                  no pin at all, the sentence below says why, and a generic
                  "published on its pin" over a failed read would be this
                  panel answering a question nobody could ask. */}
              {pin && (
                <p className="mt-1 text-micro leading-snug text-muted-foreground">
                  {pin.windowNote || "This monitor's window is published on its pin."}
                </p>
              )}
              {/* What happened to the request at creation — the cell it was
                  narrowed to, a duplicate returned instead of a second
                  monitor. Facts about THIS monitor that no other line carries. */}
              {pin?.notes.map((note) => (
                <p key={note} className="mt-1 text-micro leading-snug text-muted-foreground">
                  {note}
                </p>
              ))}
              {pin?.alreadyExisted && (
                <p className="mt-1 text-micro leading-snug text-muted-foreground">
                  This monitor already existed — the platform returned it rather than creating a
                  second one over the same spec.
                </p>
              )}
              {pin?.monitor?.note && (
                <p className="mt-1 text-micro leading-snug text-muted-foreground">
                  Your reason: {pin.monitor.note}
                </p>
              )}
              {/* THE STATED ABSENCE. `role="alert"` only when something
                  actually failed — an in-flight read is not an error and
                  must not interrupt a screen reader as one. */}
              {missingSettings && (
                <p
                  data-monitor-settings-error={missingSettings.failed ? "true" : undefined}
                  {...(missingSettings.failed ? { role: "alert" as const } : {})}
                  className={cn(
                    "mt-1 text-micro leading-snug",
                    missingSettings.failed ? "text-negative" : "text-muted-foreground",
                  )}
                >
                  {missingSettings.text}
                </p>
              )}
            </div>
            {/* THE CONTROL EXPLAINS ITSELF. With settings in hand it opens
                the editor; without them it is the retry for the read that
                failed — never a grey rectangle whose only account of
                itself is that it cannot be pressed. */}
            {pin ? (
              <Button
                variant="outline"
                size="xs"
                onClick={() => setEditing(true)}
                className="w-full justify-start text-meta font-normal"
              >
                Change what it takes to brief you
              </Button>
            ) : (
              <Button
                variant="outline"
                size="xs"
                disabled={monitorsLoading}
                onClick={() => {
                  void loadMonitors({ force: true });
                }}
                className="w-full justify-start text-meta font-normal"
              >
                {monitorsLoading
                  ? "Reading this monitor's settings…"
                  : "Read this monitor's settings again"}
              </Button>
            )}
            {/* THE TWO-STEP. The reassurance sentence is above the
                confirming button rather than below the firing one, so it
                is what the analyst acknowledges instead of what they read
                on the way past. "Keep monitoring" is the wider target and
                comes first, because the reversible choice should be the
                easy one. */}
            {armed ? (
              <div data-stop-monitor-armed className="space-y-1.5 rounded-md border p-2">
                <p className="text-meta font-medium leading-snug">
                  Stop monitoring {tile.label}?
                </p>
                <p className="text-micro leading-snug text-muted-foreground">
                  Nothing is deleted. The loads this monitor has already been briefed on stay
                  readable, and its investigations keep their links. Starting it again later
                  measures “since you started monitoring” from that day, not from this one.
                </p>
                <div className="flex gap-1.5 pt-0.5">
                  <Button
                    variant="outline"
                    size="xs"
                    onClick={() => setArmed(false)}
                    className="flex-1 justify-center text-meta font-normal"
                  >
                    Keep monitoring
                  </Button>
                  <Button
                    variant="ghost"
                    size="xs"
                    disabled={pending}
                    onClick={() => {
                      void removeMonitor(key, tile.pinId);
                      setArmed(false);
                      setOpen(false);
                    }}
                    className="flex-1 justify-center text-meta font-normal text-negative hover:text-negative"
                  >
                    {pending ? "Stopping…" : "Yes, stop monitoring"}
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                variant="ghost"
                size="xs"
                disabled={pending}
                onClick={() => setArmed(true)}
                className="w-full justify-start text-meta font-normal text-muted-foreground hover:text-foreground"
              >
                Stop monitoring this
              </Button>
            )}
            {refusal && (
              <p role="alert" className="text-micro leading-snug text-negative">
                {refusal}
              </p>
            )}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
