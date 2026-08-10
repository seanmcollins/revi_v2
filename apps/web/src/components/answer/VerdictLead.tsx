"use client";

import type { WarningEvent } from "@/lib/types";
import { cn } from "@/lib/utils";
import { publicWarningBody, warningTitle } from "@/lib/warnings";

/**
 * The verdict, said the way an analyst would say it out loud.
 *
 * `PREMISE_FALSE`, `PREMISE_PARTIAL`, `PREMISE_UNVERIFIABLE`,
 * `PREMISE_VERIFIED`, `RANKING_REFUSED` and `DIRECTION_UNMATCHED` are not
 * cautions about how to read a number — they ARE the answer's finding
 * about the question asked. "It did not double — denial rate rose 11.5%,
 * short of the 100.0% a doubling assumes" is the answer to "why did our
 * denial rate double", and rendering it in an amber box above the writing
 * files the most important sentence on the screen under "warnings".
 *
 * So it leads, in prose, in reading size, with a rule instead of a box.
 * The rule is `--warning` ink because the fact is a correction; the
 * TEXT is foreground, because it is the answer. Nothing is collapsed,
 * nothing is hidden behind a disclosure, and the code still rides on the
 * element — a verdict is the one class of warning this product is not
 * allowed to tuck away, and `data-verdict` is how that is checked.
 *
 * The title is a lead-in clause rather than a heading: "The premise holds
 * in direction, not in size — denial rate rose 11.5% …" reads as one
 * sentence, which is what it is.
 */
export function VerdictLead({
  verdicts,
  debug = false,
  className,
}: {
  verdicts: readonly WarningEvent[];
  debug?: boolean;
  className?: string;
}) {
  if (verdicts.length === 0) return null;
  return (
    <div className={cn("space-y-2.5", className)}>
      {verdicts.map((warning, index) => {
        const title = warningTitle(warning.code);
        const body = publicWarningBody(warning.code, warning.message);
        return (
          <p
            key={`${warning.code}:${index}`}
            data-warning-code={warning.code}
            data-severity={warning.severity}
            data-verdict="true"
            className="verdict-rule pl-3.5 text-lead text-foreground"
          >
            {debug && (
              <code className="mr-1.5 font-mono text-micro text-muted-foreground">
                {warning.code}
              </code>
            )}
            {title && <span className="font-semibold">{title} — </span>}
            <span className={cn(title ? "text-foreground/85" : undefined)}>{body.text}</span>
          </p>
        );
      })}
    </div>
  );
}
