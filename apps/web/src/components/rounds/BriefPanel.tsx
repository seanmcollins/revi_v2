"use client";

import { AlertTriangle } from "lucide-react";

import { WarningList } from "@/components/banners/WarningBanner";
import { BriefEntryRow } from "@/components/rounds/BriefEntryRow";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatWholeDollars } from "@/lib/format";
import type { BriefData } from "@/lib/rounds";

/**
 * THIS LOAD'S BRIEF — what changed since the last time Revi walked your
 * Rounds.
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
export function BriefPanel({ brief }: { brief: BriefData }) {
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
        // delight. A quiet morning is the best outcome Rounds has, so it
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
            className="answer-glow pointer-events-none absolute -inset-x-8 -top-6 -z-10 h-40"
          />
          <p className="numeral max-w-[26ch] text-figure leading-tight">
            {brief.status === "first_load"
              ? "First walk of your Rounds."
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
              <BriefEntryRow key={`${entry.kind}:${entry.anomalyId ?? entry.pinId ?? index}`} entry={entry} />
            ))}
          </ol>
        </>
      )}

      <ImmaterialLine brief={brief} />
      <FatigueAdvisory brief={brief} />

      {/* The brief's own caveats about itself, classified, above the
          counts rather than under thirty lines nobody scrolled to. */}
      <WarningList warnings={brief.warnings.map((w) => ({ ...w, type: "warning" as const }))} />

      <p className="num text-micro leading-snug text-muted-foreground/80">
        {brief.pinsEvaluated} watch{brief.pinsEvaluated === 1 ? "" : "es"} re-run
        {brief.leadsVerified > 0 && `, ${brief.leadsVerified} claimed fix${
          brief.leadsVerified === 1 ? "" : "es"
        } verified`}{" "}
        at {brief.watermarkId}
        {brief.priorWatermarkId ? `, against ${brief.priorWatermarkId}` : ""}.
      </p>
    </section>
  );
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
    held.pinMovements + held.newLeads + held.selfResolved + held.entriesWithheldByCap;
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
  return (
    <p
      role="status"
      data-fatigue-advisory
      className="flex max-w-[64ch] items-start gap-1.5 rounded-md border border-warning/40 bg-warning/10 px-2.5 py-2 text-meta leading-snug"
    >
      <AlertTriangle aria-hidden className="mt-0.5 size-3 shrink-0 text-warning" />
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
          gated by {m.source.split("/").slice(-2).join("/") || "the governed pack"}
        </button>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-96 text-meta leading-snug">
        <p className="font-medium">What had to be true for a line to appear</p>
        <ul className="num mt-1 space-y-0.5 text-micro">
          {Object.entries(m.unitKinds).map(([unit, rules]) => (
            <li key={unit}>
              {UNIT_NOUNS[unit] ?? unit}: {describeRules(unit, rules)}
            </li>
          ))}
          <li>
            a new lead: at least {formatWholeDollars(m.newLeadMinImpactCents)}
            {m.alwaysMaterialLanes.length > 0 &&
              ` — except ${m.alwaysMaterialLanes.join(", ")} work, which is briefed whatever its size`}
          </li>
          <li>at most {m.maxEntries} lines</li>
        </ul>
        <p className="mt-1 font-mono text-micro text-muted-foreground">{m.contentHash.slice(0, 12)}</p>
      </TooltipContent>
    </Tooltip>
  );
}

/** The pack's unit kinds, in the reader's nouns. */
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
  return parts.join(" ") || "the pack's own threshold";
}
