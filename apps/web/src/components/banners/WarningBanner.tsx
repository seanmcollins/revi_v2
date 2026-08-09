"use client";

import { AlertTriangle, Info } from "lucide-react";

import type { WarningEvent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { warningBody, warningTitle } from "@/lib/warnings";

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
 *   `severity` decides the treatment. `caution` means "this changes how
 *     you should read the number" and gets the warning tone; `info` means
 *     "worth knowing, does not change the reading" and stays quiet. The
 *     ladder is the server's — nothing here re-derives it.
 *   `code` decides the title. `POPULATION_CAVEAT` is engine vocabulary;
 *     "How to read this number" is the same fact in the reader's words.
 *     A code with no written title (including `UNCLASSIFIED`, which is the
 *     server saying it has no handle for this one) renders its sentence
 *     alone rather than wearing a heading nobody authored.
 *   `count` collapses duplicates. A four-probe plan emitting one caveat
 *     four times is one caveat seen four times, and the badge says so
 *     instead of the rail stacking four identical rows.
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
  const caution = warning.severity === "caution";
  const title = warningTitle(warning.code);
  // Stripped UNCONDITIONALLY, not only when a title exists. The prefix is
  // machine vocabulary either way, and gating the strip on the title meant
  // that the moment the server published a code this file had no entry for,
  // the analyst read "ranking_refused: 52 of the 52 publishable denial rate
  // cells…" — the flagship refusal of the release, wearing a log line. The
  // title is what a missing entry costs; the raw code is not.
  const body = warningBody(warning.code, warning.message);
  const count = warning.count ?? 1;

  return (
    <div
      data-warning-code={warning.code}
      data-severity={warning.severity}
      className={cn(
        "flex items-start gap-2 rounded-md border px-3 py-2 text-[0.72rem] leading-snug",
        caution
          ? "border-warning/40 bg-warning/10"
          : "border-border bg-surface-sunken/60 text-muted-foreground",
      )}
    >
      {caution ? (
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-warning" />
      ) : (
        <Info className="mt-0.5 size-3.5 shrink-0" />
      )}
      <div className="min-w-0 flex-1">
        {title && (
          <p className="flex flex-wrap items-center gap-1.5">
            <span className={cn("font-medium", caution && "text-foreground")}>{title}</span>
            {count > 1 && (
              <span
                className={cn(
                  "num inline-flex h-[0.95rem] items-center rounded-full border px-1.5 text-[0.58rem] font-medium",
                  caution
                    ? "border-warning/40 text-warning"
                    : "border-border text-muted-foreground",
                )}
                title={`The engine raised this ${count} times with the same wording; it is shown once.`}
              >
                ×{count}
              </span>
            )}
          </p>
        )}
        <p className={cn(title && "mt-0.5")}>
          {/* The §12 code is engine vocabulary — precise, and useless to
              an analyst reading a caution. The sentence carries the same
              meaning; the code stays one debug toggle away. */}
          {debug && (
            <code className="mr-1.5 font-mono text-[0.62rem] text-muted-foreground">
              {warning.code}
            </code>
          )}
          {body}
          {/* An untitled warning has nowhere else to put the count. */}
          {!title && count > 1 && (
            <span className="num ml-1.5 text-[0.62rem] text-muted-foreground">
              (raised {count} times)
            </span>
          )}
        </p>
      </div>
    </div>
  );
}

/** The turn's warnings, cautions first — the ones that change the reading. */
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
  // A stable partition, not a sort: within a severity the engine's own
  // order is the order the checks ran in, and shuffling it would lose the
  // one piece of sequencing the payload actually carries.
  const ordered = [
    ...warnings.filter((w) => w.severity === "caution"),
    ...warnings.filter((w) => w.severity !== "caution"),
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
