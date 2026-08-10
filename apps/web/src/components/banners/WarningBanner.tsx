"use client";

import { AlertTriangle, Info } from "lucide-react";
import { useId, useState } from "react";

import type { WarningEvent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { isLoudCode, isVerdictCode, publicWarningBody, warningTitle } from "@/lib/warnings";

/**
 * One warning, rendered from its CODE rather than from its prose.
 *
 * The old rendering keyed off severity alone and printed the sentence
 * whole, because that was all a client could do: warnings travelled as
 * strings, and grouping, counting or titling them meant matching
 * substrings — which breaks the day the wording improves. The server now
 * classifies each one at the boundary (`warnings_v2`), so this component
 * branches on a handle:
 *
 *   `code` decides the REGISTER. A verdict — a premise correction, a
 *     refusal, a corrected figure — is a conclusion about the answer and
 *     keeps the warning tone. Everything else is context about the data
 *     and renders as a quiet note in muted ink: no amber, no alert mark,
 *     no tinted box. See `isLoudCode`, which is the whole list.
 *   `code` decides the title too. `POPULATION_CAVEAT` is engine
 *     vocabulary; "How to read this number" is the same fact in the
 *     reader's words. A code with no written title (including
 *     `UNCLASSIFIED`, which is the server saying it has no handle for this
 *     one) renders its sentence alone rather than wearing a heading nobody
 *     authored.
 *   `severity` decides the ORDER, and no longer the ink. `caution` means
 *     "this changes how you should read the number" and seats the row
 *     above the notes; the ladder is the server's and nothing here
 *     re-derives it. Painting every caution amber is what turned a page of
 *     ordinary bookkeeping — window assumed, basis used, top rows kept —
 *     into a page that reads as alarms, and taught readers to skip the one
 *     row that was not.
 *   `count` collapses duplicates. A plan running one caveat on four checks
 *     is one caveat seen four times, and the badge says so instead of the
 *     rail stacking four identical rows.
 *
 * NOTHING IS DROPPED to get quiet. A row that loses its amber keeps every
 * word it had, in the same place; the Evidence rail, the copied answer and
 * every CSV are untouched.
 *
 * The message is always the platform's own sentence. The only edit is
 * dropping a machine prefix that IS the code (`population_caveat: …`),
 * because the title now carries that in plain language — see
 * `warningBody`, which refuses to strip anything else.
 */
export function WarningBanner({
  warning,
  debug = false,
}: {
  warning: WarningEvent;
  debug?: boolean;
}) {
  // MARKS ON THE DATA, NOTES BELOW IT, WARNINGS ONLY FOR VERDICTS. `loud`
  // is the register and it is decided by the code, not by the severity:
  // a premise correction, a refusal and a corrected figure are findings
  // against the answer, and a caution about which date basis was used is
  // not. Same sentence either way — only the ink changes.
  const loud = isLoudCode(warning.code);
  // THE VERDICT on the question that was asked, not a note about how to
  // read a number. `PREMISE_PARTIAL` — "denied dollars rose 14.1%, short
  // of the 100.0% a doubling assumes" — rendered in the same box, tone and
  // type size as a note about which check read which column, which gives
  // the most important sentence on the answer no rank at all over engine
  // bookkeeping. Same sentence, same list, more weight.
  const verdict = isVerdictCode(warning.code);
  const title = warningTitle(warning.code);
  // Stripped UNCONDITIONALLY, not only when a title exists. The prefix is
  // machine vocabulary either way, and gating the strip on the title meant
  // that the moment the server published a code this file had no entry for,
  // the analyst read "ranking_refused: 52 of the 52 publishable denial rate
  // cells…" — the flagship refusal of the release, wearing a log line. The
  // title is what a missing entry costs; the raw code is not.
  //
  // The engine's plan handles come off with it, too. Live,
  // `PROBE_FAMILIES_EMPTY` reached this banner carrying eight frame ids
  // and eight row counts. The exact wording is one tap away on the
  // banner itself and travels whole in every export; see
  // `publicWarningBody`.
  const body = publicWarningBody(warning.code, warning.message);
  const [verbatim, setVerbatim] = useState(false);
  const bodyId = useId();
  const count = warning.count ?? 1;

  return (
    <div
      data-warning-code={warning.code}
      data-severity={warning.severity}
      {...(verdict ? { "data-verdict": "true" } : {})}
      {...(loud ? { "data-register": "loud" } : { "data-register": "quiet" })}
      className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2 text-meta leading-snug",
        loud
          ? "border-warning/40 bg-warning/10"
          : "border-border bg-surface-sunken/60 text-muted-foreground",
        verdict && "px-3.5 py-2.5",
        loud && verdict && "border-l-2 border-l-warning",
      )}
    >
      {loud ? (
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />
      ) : (
        <Info className="mt-0.5 size-3.5 shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        {title && (
          <p className="flex flex-wrap items-center gap-1.5">
            <span
              className={cn(
                // Weight is RANK and is keyed on the verdict; ink is
                // REGISTER and is keyed on `loud`. A verified premise is
                // still the answer's own finding and still leads — it just
                // does not need to look like bad news to do it.
                "font-medium",
                loud && "text-foreground",
                verdict && "text-body font-semibold text-foreground",
              )}
            >
              {title}
            </span>
            {count > 1 && (
              <span
                className={cn(
                  "num inline-flex h-[1.15rem] items-center rounded-full border px-1.5 text-micro font-medium",
                  loud
                    ? "border-warning/40 text-warning"
                    : "border-border text-muted-foreground",
                )}
                title={
                  // HOW MANY checks raised it, when they were the only
                  // difference between the collapsed entries. The plan
                  // handles themselves are debug material and stay one
                  // toggle away, as they do everywhere else on a default
                  // surface.
                  warning.probes && warning.probes.length > 1
                    ? `One fact, raised on ${count} checks. It is stated once.`
                    : `The engine raised this ${count} times with the same wording; it is shown once.`
                }
              >
                ×{count}
              </span>
            )}
          </p>
        )}
        <p id={bodyId} className={cn(title && "mt-0.5")}>
          {/* The code is engine vocabulary — precise, and useless to an
              analyst reading a caution. The sentence carries the same
              meaning; the code stays one debug toggle away. */}
          {debug && (
            <code className="mr-1.5 font-mono text-micro text-muted-foreground">
              {warning.code}
            </code>
          )}
          {verbatim ? body.verbatim : body.text}
          {/* An untitled warning has nowhere else to put the count. */}
          {!title && count > 1 && (
            <span className="num ml-1.5 text-micro text-muted-foreground">
              (raised {count} times)
            </span>
          )}
        </p>
        {/* NOTHING IS DELETED, ONLY RELOCATED. Offered only when the two
            spellings actually differ — on the warnings that carried a
            plan-node census or a warehouse id. */}
        {body.redacted && (
          <button
            type="button"
            onClick={() => setVerbatim(!verbatim)}
            aria-expanded={verbatim}
            aria-controls={bodyId}
            className="focus-ring mt-1 rounded text-micro text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
          >
            {verbatim ? "Show plain wording" : "Show the engine's exact wording"}
          </button>
        )}
        {/* The plan nodes the collapsed entries differed by. Operator
            material — it belongs beside the trace, not above the verdict. */}
        {debug && warning.probes && warning.probes.length > 1 && (
          <p className="mt-1 break-words font-mono text-micro text-muted-foreground">
            checks: {warning.probes.join(", ")}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * The turn's warnings: the VERDICT first, then the cautions that change
 * how a number should be read, then the notes that do not.
 *
 * A stable partition, not a sort — within a band the engine's own order is
 * the order the checks ran in, and shuffling it would lose the one piece
 * of sequencing the payload actually carries. The verdict band exists
 * because the answer to the question asked was arriving in the middle of a
 * wall of engine bookkeeping, in the same ink.
 *
 * Order is still severity's job; ink is not. The rows below the verdict
 * band are notes in muted ink whether the server called them cautions or
 * not — see `isLoudCode`.
 */
export function WarningList({
  warnings,
  debug = false,
  className,
}: {
  warnings: readonly WarningEvent[];
  debug?: boolean;
  className?: string;
}) {
  if (warnings.length === 0) return null;
  const ordered = [
    ...warnings.filter((w) => isVerdictCode(w.code)),
    ...warnings.filter((w) => !isVerdictCode(w.code) && w.severity === "caution"),
    ...warnings.filter((w) => !isVerdictCode(w.code) && w.severity !== "caution"),
  ];
  return (
    <div className={cn("space-y-2", className)}>
      {ordered.map((warning, index) => (
        <WarningBanner
          key={`${warning.code}:${warning.message}:${index}`}
          warning={warning}
          debug={debug}
        />
      ))}
    </div>
  );
}
