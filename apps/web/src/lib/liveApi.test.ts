/**
 * Live end-to-end check: the REAL `ApiDriver` against a REAL running API.
 *
 * Every other suite in this directory is hermetic — it feeds the driver
 * fixtures. That is exactly how M13 shipped a green frontend whose parser
 * could not read a single live frame: the fixtures agreed with the parser
 * and disagreed with the server. This file is the check that cannot be
 * fooled that way, so it is checked in rather than run once by hand.
 *
 * It is SKIPPED unless `REVI_LIVE_API` names a running server, so
 * `pnpm test` stays hermetic and offline:
 *
 *     # terminal 1
 *     REVI_LLM_MOCK=1 REVI_WAREHOUSE_PATH=data/revi_warehouse.duckdb \
 *       uv run uvicorn revi_api.main:app --port 8018
 *     # terminal 2
 *     cd apps/web && REVI_LIVE_API=http://127.0.0.1:8018 npx vitest run src/lib/liveApi.test.ts
 */

import { describe, expect, it } from "vitest";

import { ApiDriver, fetchPortfolioLatest } from "@/lib/apiDriver";
import type { TurnEvent } from "@/lib/types";

const BASE_URL = process.env.REVI_LIVE_API;
const live = BASE_URL ? describe : describe.skip;

async function drive(
  driver: ApiDriver,
  submission: Parameters<ApiDriver["submit"]>[0],
): Promise<TurnEvent[]> {
  const events: TurnEvent[] = [];
  await driver.submit(submission, (event) => events.push(event));
  return events;
}

function newDriver(): { driver: ApiDriver; drift: string[] } {
  const drift: string[] = [];
  const driver = new ApiDriver({
    baseUrl: BASE_URL,
    onContractDrift: (paths, context) => drift.push(...paths.map((p) => `${context}: ${p}`)),
  });
  return { driver, drift };
}

live("ApiDriver against a live API", () => {
  it("streams T1 with zero contract drift and a complete answer", async () => {
    const { driver, drift } = newDriver();
    expect(await driver.checkHealth()).toBe(true);

    const events = await drive(driver, { utterance: "Why did cash decline last week?" });
    expect(drift, "a live turn must produce no contract drift").toEqual([]);

    const kinds = new Set(events.map((e) => e.type));
    for (const kind of ["stage", "context_header", "finding", "chart_spec", "narrative_delta"]) {
      expect(kinds, `T1 must deliver a ${kind} frame`).toContain(kind);
    }

    const header = events.find((e) => e.type === "context_header");
    if (header?.type !== "context_header") throw new Error("no header");
    expect(header.header.watermark.id).toBe("wm_003");
    // completed from the session pin, which is where these three live
    expect(header.header.watermark.loadedAt).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/);
    expect(header.header.packVersion.packId).toBe("base-rcm");

    const findings = events.filter((e) => e.type === "finding");
    expect(findings.map((e) => (e.type === "finding" ? e.finding.referent.value : ""))).toEqual([
      "F1",
      "F2",
      "F3",
    ]);
    // the answer-key impact, carried intact through the mapping layer
    const first = findings[0];
    if (first?.type !== "finding") throw new Error("no finding");
    expect(first.finding.impactCents).toBe(-9_909_308);
    expect(first.finding.impactKind).toBe("delta");

    const charts = events.filter((e) => e.type === "chart_spec");
    expect(charts.length).toBeGreaterThan(0);
    for (const chart of charts) {
      if (chart.type !== "chart_spec") continue;
      expect(chart.spec.series.length).toBeGreaterThan(0);
      expect(chart.spec.rows.length).toBeGreaterThan(0);
    }

    // the demo narrative cites THIS turn's handles and survives grounding
    const narrative = events
      .filter((e) => e.type === "narrative_delta")
      .map((e) => (e.type === "narrative_delta" ? e.text : ""))
      .join("");
    expect(narrative).toContain("F1");
    expect(narrative).not.toContain("[redacted");

    const terminal = events[events.length - 1];
    expect(terminal).toMatchObject({ type: "turn_complete", status: "complete" });
  }, 60_000);

  it("drills a portfolio card from a fresh session as a typed first turn", async () => {
    const portfolio = await fetchPortfolioLatest({ baseUrl: BASE_URL });
    expect(portfolio.kind).toBe("ok");
    if (portfolio.kind !== "ok") throw new Error("no portfolio");

    const card = portfolio.snapshot.items.find(
      (item) =>
        Array.isArray((item.drillSpec as { metric_ids?: unknown })?.metric_ids) &&
        (item.drillSpec as { metric_ids: string[] }).metric_ids[0] === "denied_dollars",
    );
    expect(card, "the portfolio must carry an executable drill handle").toBeDefined();
    if (!card?.drillSpec) throw new Error("no drill spec");

    // A brand-new driver: no session, no prior investigation to refine.
    const { driver, drift } = newDriver();
    const events = await drive(driver, { spec: card.drillSpec });
    expect(drift).toEqual([]);

    // The gap this closes: a cold-start drill used to be a clarification.
    expect(events.some((e) => e.type === "clarification")).toBe(false);
    const findings = events.filter((e) => e.type === "finding");
    expect(findings.length, "a drilled card must answer, not just execute").toBeGreaterThan(0);

    const header = events.find((e) => e.type === "context_header");
    if (header?.type !== "context_header") throw new Error("no header");
    expect(header.header.watermark.id).toBe(portfolio.snapshot.watermark);
    expect(header.header.filters.length).toBeGreaterThan(0);
    for (const filter of header.header.filters) expect(filter.op).toBe("eq");

    expect(events[events.length - 1]).toMatchObject({
      type: "turn_complete",
      status: "complete",
    });
  }, 60_000);

  it("renders a clarification as a clarification, not an empty answer", async () => {
    const { driver, drift } = newDriver();
    const events = await drive(driver, { utterance: "mumble" });
    expect(drift).toEqual([]);
    expect(events.some((e) => e.type === "clarification")).toBe(true);
    expect(events[events.length - 1]).toMatchObject({
      type: "turn_complete",
      status: "clarification_required",
    });
  }, 60_000);
});
