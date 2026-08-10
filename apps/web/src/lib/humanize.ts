/**
 * Warehouse identifiers → the words an analyst uses.
 *
 * Lifted out of `lib/contract.ts` so `lib/warnings.ts` can reach it: the
 * contract module already imports the warning deduper, and a warning
 * sentence that leaks `denial_rate (portfolio_denial_trend, 1 row(s))`
 * onto the answer needs the same spelling rules the chart axes use. One
 * table, one function, no cycle.
 *
 * `contract.ts` re-exports `humanizeColumn` so every existing importer is
 * untouched.
 */

/**
 * The initialisms this product spells, in the spelling it spells them in.
 *
 * `ar` is `A/R`, not `AR`. That is the metric pack's own convention and
 * the one every authored surface already follows — the monitor labelled
 * "days in A/R by payer", the benchmark "percent of A/R aged over 90
 * days", the header note about "an A/R-aging answer". "AR" beside them
 * reads as a second product's word for the same thing; live, one tile
 * managed to print three spellings of one measure ("days in A/R by
 * payer", "Days in ar", "days in ar") inside a single card.
 */
const COLUMN_ACRONYMS: Record<string, string> = {
  carc: "CARC",
  rarc: "RARC",
  ar: "A/R",
  dnfb: "DNFB",
  cpt: "CPT",
  drg: "DRG",
  msdrg: "MS-DRG",
  npi: "NPI",
  pct: "%",
};

export function humanizeColumn(column: string): string {
  const words = column.split(/[_\s]+/).filter(Boolean);
  if (words.length === 0) return column;
  const spelled = words.map((word) => COLUMN_ACRONYMS[word.toLowerCase()] ?? word);
  const [head, ...tail] = spelled;
  const lead = COLUMN_ACRONYMS[words[0].toLowerCase()] ? head : head[0].toUpperCase() + head.slice(1);
  return [lead, ...tail].join(" ");
}

/**
 * The same spelling, mid-sentence.
 *
 * `humanizeColumn` capitalizes because it writes titles and axis labels;
 * a measure named inside a sentence ("nothing above speaks for them:
 * denial rate; cash posted") must not start a capital in the middle of a
 * clause. Acronyms keep their case either way — "AR over 90 %" is not
 * improved by "ar over 90 %".
 */
export function humanizeInline(column: string): string {
  const spelled = humanizeColumn(column);
  const first = column.split(/[_\s]+/).filter(Boolean)[0]?.toLowerCase() ?? "";
  if (COLUMN_ACRONYMS[first]) return spelled;
  return spelled[0]?.toLowerCase() + spelled.slice(1);
}

/**
 * An initialism that arrived already spelled out as a WORD, in the wrong
 * case: "Days in ar", "days in ar as of 2026-08-02".
 *
 * The two functions above turn an identifier into words and get the case
 * right on the way. This one repairs a phrase that has already been
 * through somebody ELSE's humanizer and lost the case there — which is
 * exactly what happens to `MonitorsPin.spec_summary`, composed server-side
 * in `apps/api/src/revi_api/monitors/pins.py::_spec_summary` as
 * `metric_display.name_for(id) or metric_label(id)` with the first letter
 * upper-cased. For a metric the pack publishes no display name for, the
 * fallback splits `days_in_ar` on underscores and knows nothing about
 * initialisms, so the settings panel headed "What this monitor measures"
 * reads "Days in ar" directly under a tile labelled "days in A/R by
 * payer". The fix belongs at that source and is backend territory; this is
 * the same class of presentation hygiene `humanizeIsoDates` and
 * `publicWarningBody` already apply to server prose, and it costs a
 * regex.
 *
 * NARROW BY CONSTRUCTION. Whole words only, matched case-insensitively,
 * and only words that are in the table above — so a payer named "Carc
 * Health" is untouched inside its own capitalisation, and a word this
 * table has never heard of is left exactly as the server wrote it. `pct`
 * is excluded: rewriting the word "pct" to "%" mid-sentence is a
 * translation, not a case repair, and no server prose spells it out.
 */
const INITIALISM_WORD = new RegExp(
  `\\b(${Object.keys(COLUMN_ACRONYMS)
    .filter((word) => word !== "pct")
    .join("|")})\\b`,
  "gi",
);

export function respellInitialisms(text: string): string {
  return text.replace(INITIALISM_WORD, (word) => COLUMN_ACRONYMS[word.toLowerCase()] ?? word);
}
