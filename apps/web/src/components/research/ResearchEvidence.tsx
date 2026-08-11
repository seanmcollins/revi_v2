"use client";

import { ChevronRight, Database } from "lucide-react";
import { useId, useState } from "react";

import { formatCount } from "@/lib/format";
import type { ResearchAngleEvidence } from "@/lib/deepResearch";
import { useSessionStore } from "@/lib/store";
import { cn } from "@/lib/utils";

/**
 * THE WORKING BEHIND THE REPORT, ANGLE BY ANGLE.
 *
 * The same rail the answer surface has, over the object this surface
 * actually holds: a run does not run checks, it runs ANGLES, and each one
 * publishes what it read, how much of that was in scope, how many cells it
 * published and how many it refused. The refusal count is the number worth
 * having here — an angle that published twelve cells and refused
 * twenty-five is the reason a third of the report says "not estimable",
 * and without this rail that fact exists nowhere on the surface.
 *
 * The engine's own vocabulary — the estimator's handle, the content hash of
 * the read — is kept VERBATIM behind an ordinary disclosure, exactly as the
 * Evidence drawer keeps a probe hash. Truth relocates; it never deletes.
 *
 * THE NOUNS FOLLOW THE RUN. A recoverability review reads DENIALS and
 * publishes populations it could or could not price; a study reads whatever
 * its measures are grained at — claims, remits, lines — and publishes
 * groups. Printing "26 denials read" over a study of A/R aging would name a
 * population that run never opened, which is the same defect the run's own
 * population label exists to prevent, one rail to the right.
 */
export function ResearchEvidence({
  evidence,
  mode = "review",
  className,
}: {
  evidence: readonly ResearchAngleEvidence[];
  /** `review` reads denials; `study` reads whatever its measures count. */
  mode?: "review" | "study";
  className?: string;
}) {
  if (evidence.length === 0) return null;
  const read = evidence.reduce((max, angle) => Math.max(max, angle.rows_read), 0);
  const refused = evidence.reduce((total, angle) => total + angle.cells_refused, 0);

  return (
    <section aria-labelledby="research-evidence-heading" className={cn("space-y-2", className)}>
      <h3
        id="research-evidence-heading"
        className="flex items-center gap-1.5 text-micro font-semibold uppercase tracking-widest text-muted-foreground"
      >
        <Database aria-hidden className="size-3" />
        Evidence
      </h3>
      <ul className="space-y-1.5">
        {evidence.map((angle, index) => (
          <AngleNode
            key={`${angle.title}-${index}`}
            angle={angle}
            index={index + 1}
            mode={mode}
          />
        ))}
      </ul>
      <p className="num text-micro leading-snug text-muted-foreground">
        {mode === "study" ? (
          <>
            {formatCount(read)} records read · {formatCount(refused)} group
            {refused === 1 ? "" : "s"} not published across {formatCount(evidence.length)} reading
            {evidence.length === 1 ? "" : "s"}
          </>
        ) : (
          <>
            {formatCount(read)} denials read · {formatCount(refused)} cell
            {refused === 1 ? "" : "s"} refused across {formatCount(evidence.length)} angle
            {evidence.length === 1 ? "" : "s"}
          </>
        )}
      </p>
    </section>
  );
}

function AngleNode({
  angle,
  index,
  mode,
}: {
  angle: ResearchAngleEvidence;
  index: number;
  mode: "review" | "study";
}) {
  const debug = useSessionStore((s) => s.settings.debug);
  const [open, setOpen] = useState(false);
  const detailId = useId();

  return (
    <li className="rounded-md border bg-card">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        aria-controls={detailId}
        className="focus-ring flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            "size-3 shrink-0 text-muted-foreground transition-transform duration-150",
            open && "rotate-90",
          )}
        />
        <span className="num shrink-0 rounded bg-surface-sunken px-1 py-0.5 text-micro text-muted-foreground">
          {index}
        </span>
        <span className="min-w-0 flex-1 truncate text-meta">{angle.title}</span>
        {angle.cells_refused > 0 && (
          <span className="num shrink-0 text-micro text-muted-foreground">
            {formatCount(angle.cells_refused)} refused
          </span>
        )}
      </button>
      {open && (
        <div id={detailId} className="space-y-1 border-t px-2.5 py-2 pl-7 text-meta">
          {mode === "study" ? (
            <>
              <p className="num text-muted-foreground">
                {formatCount(angle.rows_read)} records read
              </p>
              <p className="num text-muted-foreground">
                {formatCount(angle.cells_published)} group
                {angle.cells_published === 1 ? "" : "s"} published ·{" "}
                {formatCount(angle.cells_refused)} too small to publish a figure for
              </p>
            </>
          ) : (
            <>
              <p className="num text-muted-foreground">
                {formatCount(angle.rows_read)} denials read ·{" "}
                {formatCount(angle.rows_in_scope)} with a final answer from the payer
              </p>
              <p className="num text-muted-foreground">
                {formatCount(angle.cells_published)} population
                {angle.cells_published === 1 ? "" : "s"} published ·{" "}
                {formatCount(angle.cells_refused)} too small to publish a rate for
              </p>
            </>
          )}
          {/* The engine's own record, verbatim — the estimator that ran and
              the content hash of the read it ran over. Behind a disclosure
              the reader opened, which is where raw records belong. */}
          <p className="break-words font-mono text-micro text-muted-foreground">
            {angle.estimator}
            {angle.read_fingerprint !== "" && ` · ${angle.read_fingerprint.slice(0, 12)}`}
            {debug && angle.duration_ms > 0 && ` · ${angle.duration_ms} ms`}
          </p>
        </div>
      )}
    </li>
  );
}
