"use client";

import { Info } from "lucide-react";

import { WarningList } from "@/components/banners/WarningBanner";
import { BriefEntryRow, type BriefLeadHandle } from "@/components/monitors/BriefEntryRow";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatWholeDollars, mediumDate } from "@/lib/format";
import type { BriefData } from "@/lib/monitors";

/**
 * THIS LOAD'S BRIEF — what changed since the last time Revi walked your
 * Monitors.
 *
 * The zone is a single column at reading measure with a hairline spine
 * down its left edge and one mark per entry. The spine is not decoration:
 * it says these lines are ONE walk, in order, at one load — which is
 * exactly what distinguishes a brief from a notification list, and it is
 * the same left-rule device the calm answer already uses to mark a verdict
 * (`.verdict-rule`).
 *
 * "NOTHING MATERIAL CHANGED" IS THE BOLDEST TYPE ON THE SURFACE. It is the
 * one place in this product where a sentence takes the size reserved for a
 * headline figure, and that is the argument made visually: the best
 * possible morning is a quiet one, Revi walked everything and measured it,
 * and the counts underneath prove the walk happened. A pale grey empty
 * state would say the opposite — that the tool had nothing to offer.
 *
 * Everything the gate held back is COUNTED under the entries, never
 * hidden. Suppressing a movement silently and suppressing it visibly are
 * different products: the first is a filter the analyst cannot audit.
 */
export function BriefPanel({
  brief,
  leads,
}: {
  brief: BriefData;
  /**
   * The leads this load's worklist carries, by anomaly id — where each one
   * stands and how to open it. Absent in mock mode and in a test that
   * renders the panel alone, which is why every row degrades to the entry's
   * own fields.
   */
  leads?: ReadonlyMap<string, BriefLeadHandle>;
}) {
  const quiet = brief.status !== "material_changes";

  return (
    <section aria-labelledby="brief-heading" className="space-y-4">
      <header className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2
          id="brief-heading"
          className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
        >
          This load&apos;s brief
        </h2>
        <MaterialityNote brief={brief} />
      </header>

      {quiet ? (
        // THE PROUD STATE, and the one place this product spends a little
        // delight. A quiet morning is the best outcome Monitors has, so it
        // is set at figure size — the size otherwise reserved for one
        // headline number — over the same soft accent glow the answer
        // being read wears, and it fades up rather than appearing. Calm,
        // not cute: no illustration, no confetti, one sentence and some
        // light behind it. Its counts are the measurement behind the
        // claim; a quiet brief that could not say how much it had walked
        // would be indistinguishable from a brief that did not run.
        <div className="fade-up relative space-y-2 py-2">
          <div
            aria-hidden
            // Anchored to this panel's own top edge for the same reason the
            // answer card's is — see `AnswerCard`. A decoration that begins
            // above its own box is drawn over whatever is stacked above it.
            className="answer-glow pointer-events-none absolute -inset-x-8 top-0 -z-10 h-40"
          />
          <p className="numeral max-w-[26ch] text-figure leading-tight">
            {brief.status === "first_load"
              ? "First walk of your Monitors."
              : "Nothing material changed."}
          </p>
          <p className="max-w-[64ch] text-body leading-relaxed text-muted-foreground">
            {brief.headline}
          </p>
        </div>
      ) : (
        <>
          <p className="max-w-[64ch] text-lead leading-snug text-foreground">{brief.headline}</p>
          <ol className="brief-spine space-y-5 border-l pl-4">
            {brief.entries.map((entry, index) => (
              <BriefEntryRow
                key={`${entry.kind}:${entry.anomalyId ?? entry.pinId ?? index}`}
                entry={entry}
                {...(entry.anomalyId && leads?.get(entry.anomalyId)
                  ? { lead: leads.get(entry.anomalyId)! }
                  : {})}
              />
            ))}
          </ol>
        </>
      )}

      <ImmaterialLine brief={brief} />
      <FatigueAdvisory brief={brief} />

      {/* The brief's own caveats about itself, classified, above the
          counts rather than under thirty lines nobody scrolled to. */}
      <WarningList warnings={brief.warnings.map((w) => ({ ...w, type: "warning" as const }))} />

      <WalkCensus brief={brief} />
    </section>
  );
}

/**
 * THE WORK BEHIND THE BRIEF, AND WHETHER IT ADDS UP.
 *
 * Two defects, one line. The first is vocabulary: this line used to end
 * "at wm_003, against wm_002" — the two warehouse ids, on the surface a
 * champion screenshots, in the sentence that is the entire evidence for
 * the claim to have walked anything. The brief speaks in dates now, and
 * the ids stay in provenance where an auditor can still reach them.
 *
 * The second is arithmetic. "18 monitors re-run" was printed above a brief
 * carrying one movement and one held-back movement, with the other
 * sixteen — first readings, nothing to compare against — neither briefed
 * nor counted. On a surface whose stated discipline is "withheld visibly,
 * never silently", a total that does not reconcile to its parts is the one
 * number it may not publish. So the parts are named, and if they do not
 * close, the remainder is named too rather than absorbed.
 */
function WalkCensus({ brief }: { brief: BriefData }) {
  const held = brief.immaterial;
  // Movements this brief actually printed, plus any the cap dropped —
  // the server publishes what it withheld by kind, so a capped movement is
  // still accounted for rather than vanishing from the census.
  const briefedPins =
    brief.entries.filter((e) => e.kind === "pin_movement" || e.kind === "rank_flip").length +
    (held.withheldByKind.pin_movement ?? 0) +
    (held.withheldByKind.rank_flip ?? 0);
  const accounted =
    briefedPins + held.pinMovements + held.notYetComparable + held.unavailable;
  const remainder = brief.pinsEvaluated - accounted;

  const parts: string[] = [];
  if (briefedPins > 0) parts.push(`${briefedPins} briefed`);
  if (held.pinMovements > 0) parts.push(`${held.pinMovements} moved too little to brief`);
  if (held.notYetComparable > 0) {
    parts.push(`${held.notYetComparable} with nothing to compare against yet`);
  }
  if (held.unavailable > 0) parts.push(`${held.unavailable} the platform could not measure`);

  return (
    <p data-walk-census className="num text-micro leading-snug text-muted-foreground">
      {brief.pinsEvaluated} monitor{brief.pinsEvaluated === 1 ? "" : "s"} re-run
      {brief.leadsVerified > 0 &&
        `, ${brief.leadsVerified} claimed fix${
          brief.leadsVerified === 1 ? "" : "es"
        } verified`}
      {brief.newestDataDate
        ? ` on the data through ${safeDate(brief.newestDataDate)}`
        : " at this data load"}
      {/* NO WAREHOUSE IDS ON THIS SURFACE. When the prior load's data date
          is published the brief names it; when it is not, it says "the
          previous load" rather than reaching for `wm_002`. The ids are
          still one hover away on every entry's provenance, which is where
          an auditor looks and a champion does not. */}
      {brief.priorNewestDataDate
        ? `, against the ${safeDate(brief.priorNewestDataDate)} load`
        : brief.priorWatermarkId
          ? ", against the previous load"
          : ""}
      .
      {parts.length > 0 && ` Of those: ${parts.join(", ")}.`}
      {/* The parts do not close. Said rather than absorbed: a census that
          silently monitors is the filter this surface exists not to be. */}
      {remainder !== 0 && brief.pinsEvaluated > 0 && (
        <span>
          {" "}
          {Math.abs(remainder)} {remainder > 0 ? "not accounted for" : "counted twice"} in that
          split.
        </span>
      )}
    </p>
  );
}

/** A data date in the reader's words, or the ISO string if it will not parse. */
function safeDate(iso: string): string {
  try {
    return mediumDate(iso);
  } catch {
    return iso;
  }
}

/**
 * Everything the gate held back, counted rather than hidden — including
 * the entries a length cap dropped.
 *
 * Alert fatigue is the death mode of a daily surface, so the cap is real;
 * a cap the reader cannot see is a filter they cannot audit, so the count
 * is real too.
 */
function ImmaterialLine({ brief }: { brief: BriefData }) {
  const held = brief.immaterial;
  const total =
    held.pinMovements +
    held.newLeads +
    held.selfResolved +
    held.entriesWithheldByCap +
    // Counted here too, so a load whose only held-back facts are first
    // readings still renders the server's own sentence about them. The
    // note is composed server-side from every bucket; gating it on four of
    // six would hide it exactly when the census needs it most.
    held.notYetComparable +
    held.unavailable;
  if (total === 0 || held.note === "") return null;
  return (
    <p data-immaterial-summary className="max-w-[64ch] text-meta leading-snug text-muted-foreground">
      {/* The server's own sentence, which names each category and its
          count. */}
      {held.note}
      {held.entriesWithheldByCap > 0 && (
        <>
          {" "}
          {brief.entriesTotal} cleared the gate and this brief shows{" "}
          {brief.entries.length}, because a brief nobody finishes is a brief nobody opens.
          {/* WHAT the cap dropped, not just how many. "12 further entries"
              does not tell a reader whether a confirmed fix or a
              regression was among them — which is the only question worth
              asking about a dropped line. */}
          {Object.keys(held.withheldByKind).length > 0 && (
            <>
              {" "}
              Dropped:{" "}
              {Object.entries(held.withheldByKind)
                .filter(([, count]) => count > 0)
                .map(([kind, count]) => `${count} ${KIND_NOUNS[kind] ?? kind.replace(/_/g, " ")}`)
                .join(", ")}
              .
            </>
          )}
        </>
      )}
    </p>
  );
}

/**
 * The brief noticing that somebody's own thresholds are too loose — once
 * per load, in the governed wording, with the counts that back it.
 *
 * An advisory that nagged would be the fatigue it is warning about, which
 * is why the condition (consecutive loads) lives in the pack and the
 * surface only renders what the server decided.
 */
function FatigueAdvisory({ brief }: { brief: BriefData }) {
  if (!brief.fatigue.active || brief.fatigue.message === "") return null;
  // An advisory about the reader's own thresholds is a note, not an
  // alarm: nothing is wrong with the data and no number here reads
  // differently because of it. Quiet ink, same sentence, same place.
  return (
    <p
      role="status"
      data-fatigue-advisory
      className="flex max-w-[64ch] items-start gap-1.5 rounded-md border bg-surface-sunken/60 px-2.5 py-2 text-meta leading-snug text-muted-foreground"
    >
      <Info aria-hidden className="mt-0.5 size-3 shrink-0" />
      {brief.fatigue.message}
    </p>
  );
}

/**
 * The gate that was actually applied, one hover away.
 *
 * The thresholds ride on the response so a reader can CHECK the gate that
 * produced a brief — or the absence of one — rather than take it on faith,
 * and so two deployments running different packs can be told apart from
 * the payload. The content hash is what makes that checkable.
 */
function MaterialityNote({ brief }: { brief: BriefData }) {
  const m = brief.materiality;
  if (m.contentHash === "") return null;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="focus-ring num rounded text-micro text-muted-foreground underline decoration-dotted underline-offset-2 hover:text-foreground"
        >
          {/* NO PACK FILE PATH ON THE FACE OF IT. "gated by
              rcm/materiality.yaml" is a repository path an analyst has
              never seen; which pack it was and the hash that identifies it
              are the auditor's material and live one hover away, where the
              rest of the gate already is. */}
          {/* "Gated by the governed pack" was four words and two of them
              were platform vocabulary. What a reader wants to know is what
              had to be true for a line to appear, which is exactly what the
              hover says. */}
          What it took to reach this list
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-96 text-meta leading-snug">
        <p className="font-medium">What had to be true for a line to appear</p>
        <ul className="num mt-1 space-y-0.5 text-micro">
          {Object.entries(m.unitKinds).map(([unit, rules]) => (
            <li key={unit}>
              {/* `?? unit` used to leak the raw wire kind (`money_cents`)
                  the moment the map missed one. A measure this surface
                  cannot name is described, not spelled in the warehouse's
                  word for it. */}
              {sentenceCase(UNIT_NOUNS[unit] ?? "this kind of measure")}:{" "}
              {describeRules(unit, rules)}
            </li>
          ))}
          <li>
            A new lead: at least {formatWholeDollars(m.newLeadMinImpactCents)}
            {m.alwaysMaterialLanes.length > 0 &&
              ` — except ${m.alwaysMaterialLanes.join(", ")} work, which is briefed whatever its size`}
          </li>
          <li>At most {m.maxEntries} lines</li>
        </ul>
        {/* Which pack, and the hash that pins it — so two deployments
            running different gates can be told apart from the payload. */}
        <p className="mt-1 text-micro text-muted-foreground">
          {/* THE AUDITOR'S HALF, and only here. The source used to render
              as `rcm/materiality.yaml` — a repository path an analyst has
              never seen — on the face of the tooltip. It is a provenance
              handle, so it is labelled as one rather than dropped: an
              auditor comparing two deployments needs it, and a first-time
              reader now knows what they are looking at. */}
          Rule set{" "}
          <span className="font-mono">{m.source.split("/").slice(-1)[0] || "unnamed"}</span> ·{" "}
          <span className="font-mono">{m.contentHash.slice(0, 12)}</span>
        </p>
      </TooltipContent>
    </Tooltip>
  );
}

/**
 * Entry kinds in the reader's nouns, for the one place a COUNT of them is
 * printed. The row itself takes its label from `BriefEntryRow.KINDS`; this
 * is the plural form that reads inside a sentence.
 */
const KIND_NOUNS: Readonly<Record<string, string>> = {
  new_lead: "new leads",
  pin_movement: "monitor movements",
  self_resolved: "gone on their own",
  resolution_confirmed: "confirmed fixes",
  resolution_regressed: "fixes that came back",
  rank_flip: "changes of leading cell",
};

/** The pack's unit kinds, in the reader's nouns. */
/** A list entry starts with a capital, whatever noun the map supplies. */
function sentenceCase(text: string): string {
  const first = text[0];
  if (first === undefined || first !== first.toLowerCase()) return text;
  return first.toUpperCase() + text.slice(1);
}

const UNIT_NOUNS: Readonly<Record<string, string>> = {
  ratio: "a rate",
  money_cents: "money",
  count: "a count",
  days: "days",
};

/**
 * The rules for one unit kind, said the way the pack MEANS them.
 *
 * Two details that are the difference between describing the gate and
 * misdescribing it. Money is a percentage AND a floor, conjoined — "or"
 * would describe a gate twice as loud as the one that ran. And a floor is
 * only money when the unit is money: the pack's `count` floor is 25
 * things, and rendering it as $0.25 would be this surface inventing a
 * currency for a number of claims.
 */
function describeRules(unit: string, rules: Record<string, number>): string {
  const parts: string[] = [];
  if (rules.min_points !== undefined) parts.push(`${(rules.min_points * 100).toFixed(1)} points`);
  if (rules.min_relative !== undefined) parts.push(`${(rules.min_relative * 100).toFixed(0)}%`);
  if (rules.min_absolute !== undefined) {
    const floor =
      unit === "money_cents"
        ? formatWholeDollars(rules.min_absolute)
        : unit === "days"
          ? `${rules.min_absolute} day${rules.min_absolute === 1 ? "" : "s"}`
          : String(rules.min_absolute);
    parts.push(rules.min_relative !== undefined ? `and ${floor}` : floor);
  }
  // NOT "the pack's own threshold" — the owner's reaction to that phrase
  // was "what the fuck does that even mean?". A gate this surface cannot
  // spell out is described by what it does, not by what owns it.
  return parts.join(" ") || "Revi's recommended level for this kind of measure";
}
