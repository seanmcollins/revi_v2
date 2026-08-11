"use client";

import { entityColor } from "@/components/charts/chartForms";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  cellInterval,
  cellRate,
  confidenceLabel,
  type ResearchStratum,
  type ResearchThinPopulations,
} from "@/lib/deepResearch";
import { formatCents, formatCount, formatPct } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * THE POPULATIONS, ROW BY ROW — and the one table in this product where a
 * blank cell would be a lie.
 *
 * Every row is a payer × denial-type population the run priced or refused
 * to price, and the two are drawn in ONE list rather than two: a table of
 * measured rows with the refusals filed elsewhere is a table that reads as
 * complete. What separates them is the treatment, not the location.
 *
 *   A MEASURED ROW is plain. The rate, its interval underneath, the number
 *     of denials the payer has already answered, the open inventory, and
 *     the expected recovery with its own range.
 *   A NOT-ESTIMABLE ROW keeps its `n`, keeps its open denials, keeps its
 *     denied dollars — and has NO RATE AT ALL. Not a dash a reader takes
 *     for a zero, not a dimmed number, not an interval with nothing in the
 *     middle of it: the words "not estimable", in the neutral withheld
 *     register this product already uses for a cell the engine declined to
 *     publish (`ChartTableView`'s "Withheld †"). The dollars are real and
 *     are shown; the rate does not exist and is not drawn.
 *
 * THE BAR IS THE RANKING VOCABULARY, INSIDE THE TABLE. The expected column
 * carries a horizontal bar proportional to the row's own figure, in the
 * hue its PAYER wears on every other figure in this report (`entityColor`,
 * the same function the charts colour their marks with) — so an analyst
 * scanning the table and an analyst scanning the payer chart are following
 * the same colour for the same payer. A not-estimable row gets no bar,
 * because there is no figure for a bar to be the length of.
 *
 * NOTHING IS RE-SORTED HERE. The order is the server's, and a client that
 * ranked this table would be publishing an ordering the payload never
 * made — which is the same refusal the chart layer makes about `sort`.
 */
export function ResearchStrataTable({
  strata,
  notEstimable,
  thin,
}: {
  strata: readonly ResearchStratum[];
  notEstimable: readonly ResearchStratum[];
  thin?: ResearchThinPopulations | null;
}) {
  const rows = [...strata, ...notEstimable];
  if (rows.length === 0) return null;

  // The longest bar in the column, so the marks are comparable to each
  // other rather than each to itself.
  const widest = rows.reduce((max, row) => Math.max(max, row.expected_cents ?? 0), 0);

  return (
    <section aria-labelledby="research-strata-heading" className="space-y-2">
      <h3
        id="research-strata-heading"
        className="text-micro font-semibold uppercase tracking-widest text-muted-foreground"
      >
        Every population, priced or refused
      </h3>

      <div className="w-full overflow-x-auto rounded-md border">
        <Table className="text-meta">
          <TableHeader>
            <TableRow className="bg-surface-sunken/60">
              <TableHead className="h-8 px-2 text-micro font-medium">Population</TableHead>
              <TableHead className="h-8 px-2 text-right text-micro font-medium">
                Recovery rate
              </TableHead>
              <TableHead className="h-8 px-2 text-right text-micro font-medium">
                Denials answered
              </TableHead>
              <TableHead className="h-8 px-2 text-right text-micro font-medium">
                Open denials
              </TableHead>
              <TableHead className="h-8 px-2 text-right text-micro font-medium">
                Open denied dollars
              </TableHead>
              <TableHead className="h-8 px-2 text-right text-micro font-medium">
                Expected recoverable
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((row) => (
              <StratumRow key={row.label} row={row} widest={widest} />
            ))}
          </TableBody>
        </Table>
      </div>

      {/* THE POPULATIONS TOO SMALL TO NAME, counted rather than dropped.
          Their denials and their dollars are real and are stated; naming
          them would disclose the handful of denials behind each. */}
      {thin && thin.populations > 0 && (
        <p className="num text-meta leading-snug text-muted-foreground">
          A further {formatCount(thin.populations)} population
          {thin.populations === 1 ? "" : "s"} hold {formatCount(thin.open_denials)} open denial
          {thin.open_denials === 1 ? "" : "s"} worth {formatCents(thin.open_dollars_cents)} between
          them. Each is smaller than {formatCount(thin.floor)} denials, so they are counted here
          rather than named.
        </p>
      )}
    </section>
  );
}

/**
 * The entity whose colour this row wears.
 *
 * The payer, when the stratum names one — that is the identity a reader is
 * tracking across the report's figures. Falling back to the row's whole
 * label keeps a stratum cut some other way (a denial type on its own)
 * consistently coloured with itself rather than uncoloured.
 */
function rowEntity(row: ResearchStratum): string {
  const payer = row.parts?.find((part) => part.stratifier === "payer");
  return payer?.value ?? row.label;
}

function StratumRow({ row, widest }: { row: ResearchStratum; widest: number }) {
  const measured = row.evidence === "measured";
  const rate = cellRate(row.rate_cell);
  const interval = cellInterval(row.rate_cell);
  // `expected_cents` is `null` on a refused row, and `null` is not a
  // number this table may format: it becomes "not estimable", never a
  // zero. Narrowed here so no branch below can reach a formatter with it.
  const expected =
    measured && typeof row.expected_cents === "number" ? row.expected_cents : undefined;
  const expectedInterval = measured ? (row.expected_interval ?? undefined) : undefined;
  const share = widest > 0 && expected !== undefined ? Math.max(0.02, expected / widest) : 0;

  return (
    <TableRow data-evidence={row.evidence} className={cn(!measured && "bg-surface-sunken/30")}>
      <TableCell className="max-w-[22rem] px-2 py-1.5 align-top">
        <span className="flex items-baseline gap-1.5">
          {/* The entity's own hue, as a 3px rule rather than as ink on the
              name: a coloured label is a label whose contrast is set by a
              palette, and this product sets its text contrast itself. */}
          <span
            aria-hidden
            className="mt-1 inline-block size-1.5 shrink-0 rounded-full"
            style={{ backgroundColor: entityColor(rowEntity(row)) }}
          />
          <span className="min-w-0">{row.label}</span>
        </span>
      </TableCell>

      <TableCell className="num px-2 py-1.5 text-right align-top">
        {measured && rate !== undefined ? (
          <>
            <span>{formatPct(rate)}</span>
            {interval && (
              <span className="block text-micro font-normal text-muted-foreground">
                {formatPct(interval.low)}–{formatPct(interval.high)}
                {interval.confidence !== undefined && (
                  <span className="sr-only">
                    {" "}
                    at {confidenceLabel(interval.confidence)} confidence
                  </span>
                )}
              </span>
            )}
          </>
        ) : (
          // NO NUMBER REACHES THIS PATH. A rate the run declined to publish
          // is stated in words, in the neutral register — never a dimmed
          // figure, and never a dash that reads as zero.
          <span className="font-normal text-muted-foreground">Not estimable</span>
        )}
      </TableCell>

      <TableCell className="num px-2 py-1.5 text-right align-top">
        {formatCount(row.rate_cell.n)}
      </TableCell>
      <TableCell className="num px-2 py-1.5 text-right align-top">
        {formatCount(row.open_denials)}
      </TableCell>
      <TableCell className="num px-2 py-1.5 text-right align-top">
        {formatCents(row.open_dollars_cents)}
      </TableCell>

      <TableCell className="num px-2 py-1.5 text-right align-top">
        {expected !== undefined ? (
          <>
            <span>{formatCents(expected)}</span>
            {expectedInterval && (
              <span className="block text-micro font-normal text-muted-foreground">
                {formatCents(expectedInterval.low_cents)}–
                {formatCents(expectedInterval.high_cents)}
              </span>
            )}
            {/* The ranking mark: this row's figure against the largest in
                the column, in its payer's own hue. Decorative by
                construction — the numeral above it carries the value. */}
            <span aria-hidden className="mt-1 block h-1 w-full rounded-full bg-border/60">
              <span
                className="block h-1 rounded-full"
                style={{
                  width: `${(share * 100).toFixed(1)}%`,
                  backgroundColor: entityColor(rowEntity(row)),
                }}
              />
            </span>
          </>
        ) : (
          <span className="font-normal text-muted-foreground">Not estimable</span>
        )}
      </TableCell>
    </TableRow>
  );
}
