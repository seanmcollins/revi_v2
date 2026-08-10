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
import { portfolioToCsv } from "@/lib/export";
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
    expect(portfolio.status).toBe("ok");

    const card = portfolio.items.find(
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
    expect(header.header.watermark.id).toBe(portfolio.watermark);
    expect(header.header.filters.length).toBeGreaterThan(0);
    for (const filter of header.header.filters) expect(filter.op).toBe("eq");

    expect(events[events.length - 1]).toMatchObject({
      type: "turn_complete",
      status: "complete",
    });
  }, 60_000);

  it("lists real sessions and switches between them, rebuilding the thread", async () => {
    // Two independent drivers = two independent sessions, exactly as two
    // "New chat" clicks would be.
    const first = newDriver();
    await drive(first.driver, { utterance: "Why did cash decline last week?" });
    const second = newDriver();
    await drive(second.driver, { utterance: "What is PR3?" });
    expect([...first.drift, ...second.drift]).toEqual([]);

    const page = await second.driver.listSessions();
    const titles = new Map(page.sessions.map((s) => [s.title, s]));
    expect(page.total).toBeGreaterThanOrEqual(2);
    // Titles are the questions actually asked — no client-side naming.
    expect(titles.has("Why did cash decline last week?")).toBe(true);
    expect(titles.has("What is PR3?")).toBe(true);
    const cashSession = titles.get("Why did cash decline last week?");
    if (!cashSession) throw new Error("the cash session is not listed");
    expect(cashSession.turnCount).toBeGreaterThanOrEqual(1);
    // Newest activity leads: "What is PR3?" was answered last.
    expect(page.sessions[0]?.title).toBe("What is PR3?");

    // Switch the SECOND driver into the FIRST session and rebuild it.
    const resumed = await second.driver.resumeSession(cashSession.sessionId);
    expect(second.drift).toEqual([]);
    expect(resumed.sessionId).toBe(cashSession.sessionId);
    expect(resumed.watermark.id).toBe("wm_003"); // the original pin, re-joined
    expect(resumed.turns).toHaveLength(1);
    const rebuilt = resumed.turns[0];
    expect(rebuilt.question).toBe("Why did cash decline last week?");
    const findings = rebuilt.events.filter((e) => e.type === "finding");
    expect(
      findings.map((e) => (e.type === "finding" ? e.finding.referent.value : "")),
    ).toEqual(["F1", "F2", "F3", "F4"]);
    expect(rebuilt.events[rebuilt.events.length - 1]).toMatchObject({
      type: "turn_complete",
      status: "complete",
    });
  }, 120_000);

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

/**
 * Wave-A: the fields this release started publishing, read off the wire.
 *
 * Same discipline as the suite above and for the same reason — every one
 * of these was verified once by hand against a running server, and a
 * hand-verified fact rots silently. Checked in so it cannot.
 */
live("wave-A wire fields against a live API", () => {
  it("serves the portfolio unconditionally, with a status of its own", async () => {
    // No 501 branch: the route always answers 200, and an empty feed is a
    // snapshot saying `status: "empty"` — a fact about the data load, not
    // about the deployment.
    const snapshot = await fetchPortfolioLatest({ baseUrl: BASE_URL });
    expect(snapshot.status).toBe("ok");
    expect(snapshot.items.length).toBeGreaterThan(0);
  });

  it("publishes ranked_on, its figure and its note on every card", async () => {
    const { items } = await fetchPortfolioLatest({ baseUrl: BASE_URL });
    for (const item of items) {
      expect(["detector", "platform", "not_comparable"]).toContain(item.rankedOn);
      expect(typeof item.rankedImpactCents).toBe("number");
    }
    // The whole reason the label exists: the basis is NOT uniform across
    // the list, so a rail that shows only the number shows a ranking whose
    // basis varies card by card without saying so.
    const bases = new Set(items.map((item) => item.rankedOn));
    expect(bases.size).toBeGreaterThan(1);
    // Every card ranked on something other than the detector's own figure
    // carries the server's sentence explaining why.
    for (const item of items.filter((i) => i.rankedOn !== "detector")) {
      expect(item.rankedOnNote, `${item.referent} must say why`).toBeTruthy();
    }
  });

  it("publishes the dimension repoint with its reasoning", async () => {
    const { items } = await fetchPortfolioLatest({ baseUrl: BASE_URL });
    const repointed = items.filter((i) => (i.drillDimensionRepoints?.length ?? 0) > 0);
    expect(repointed.length, "some cards substitute the detector's cut").toBeGreaterThan(0);
    for (const item of repointed) {
      for (const repoint of item.drillDimensionRepoints ?? []) {
        expect(repoint.fromDimension).toBe("proc_group");
        expect(repoint.toDimension).toBe("primary_proc_group");
        // The rationale IS the disclosure — it is what says the detector
        // counted lines and the drill counts claims.
        expect(repoint.rationale.length).toBeGreaterThan(80);
      }
    }
  });

  it("decomposes the impact term into checkable arithmetic", async () => {
    const { items } = await fetchPortfolioLatest({ baseUrl: BASE_URL });
    for (const item of items) {
      const priority = item.priority;
      if (!priority?.rankedImpactCents || !priority.impactNormalizerCents) continue;
      // `impact_norm = ranked_impact_cents / impact_normalizer_cents`,
      // which is the one term a reader could not previously check: the
      // ratio was published and neither of its two inputs was.
      const computed = priority.rankedImpactCents / priority.impactNormalizerCents;
      expect(
        Math.abs(computed - priority.impactNorm),
        `${item.referent}: impact_norm must be its published inputs`,
      ).toBeLessThan(0.001);
    }
  });

  it("carries the ranked worklist when the question routes to it", async () => {
    const { driver, drift } = newDriver();
    // The typed handle, which is the twin of the governed routing: a
    // surface that already knows asks outright. Additive by contract —
    // the turn investigates what it would have anyway.
    const events = await drive(driver, {
      utterance: "Why did cash decline last week?",
      worklist: { limit: 5 },
    });
    expect(drift, "a worklist turn must produce no contract drift").toEqual([]);

    const terminal = events[events.length - 1];
    if (terminal?.type !== "turn_complete") throw new Error("no terminal frame");
    const worklist = terminal.worklist;
    expect(worklist, "the typed handle must attach the worklist").toBeDefined();
    if (!worklist) return;

    expect(["playbook", "concept", "typed_query"]).toContain(worklist.matchedOn);
    // `items` is a PAGE of `totalItems`; the lanes describe the whole
    // population, which is why a lane's count exceeds the page.
    expect(worklist.items.length).toBeLessThanOrEqual(worklist.totalItems);
    expect(worklist.items.length).toBeGreaterThan(0);
    expect(worklist.formulaVersion).toMatch(/^anomaly_priority@/);
    // The same cards the rail draws, from the same build.
    const { items } = await fetchPortfolioLatest({ baseUrl: BASE_URL });
    const railById = new Map(items.map((item) => [item.referent, item]));
    for (const card of worklist.items) {
      const rail = railById.get(card.referent);
      expect(rail, `${card.referent} must exist on the rail too`).toBeDefined();
      // One computation behind both surfaces: they cannot disagree about
      // the money or the basis.
      expect(card.rankedImpactCents).toBe(rail?.rankedImpactCents);
      expect(card.rankedOn).toBe(rail?.rankedOn);
    }
  }, 120_000);

  it("archives a session out of the list, and keeps it fetchable by id", async () => {
    const { driver } = newDriver();
    await drive(driver, { utterance: "Why did cash decline last week?" });
    const before = await driver.listSessions(200);
    const mine = before.sessions[0];
    expect(mine).toBeDefined();
    if (!mine) return;

    await driver.archiveSession(mine.sessionId);

    const after = await driver.listSessions(200);
    expect(after.sessions.some((s) => s.sessionId === mine.sessionId)).toBe(false);
    // SOFT: nothing was deleted, and a link in a ticket still resolves.
    const resumed = await driver.resumeSession(mine.sessionId);
    expect(resumed.sessionId).toBe(mine.sessionId);
    // Idempotent — a double click is not an error.
    await expect(driver.archiveSession(mine.sessionId)).resolves.toBeUndefined();
  }, 120_000);

  it("publishes a restored investigation's own account of the restore", async () => {
    const { driver, drift } = newDriver();
    await drive(driver, { utterance: "Why did cash decline last week?" });
    const page = await driver.listSessions(1);
    const target = page.sessions[0];
    if (!target) throw new Error("no session to restore");

    const resumed = await driver.resumeSession(target.sessionId);
    expect(drift, "a restore must produce no contract drift").toEqual([]);
    expect(resumed.turns.length).toBeGreaterThan(0);

    // The header comes back with the server's `context_header_restored`
    // and its `restoration_notes`, which the Restored popover renders in
    // place of this client's own approximation.
    const headerEvent = resumed.turns[0]?.events.find((e) => e.type === "context_header");
    if (headerEvent?.type !== "context_header") throw new Error("no restored header");
    expect(headerEvent.header.restored).toBe(true);
    expect(headerEvent.header.restorationNotes?.length ?? 0).toBeGreaterThan(0);
  }, 120_000);

  it("lights the honesty columns up in the worklist CSV", async () => {
    const snapshot = await fetchPortfolioLatest({ baseUrl: BASE_URL });
    const csv = portfolioToCsv({
      items: snapshot.items,
      ...(snapshot.watermark ? { watermark: snapshot.watermark } : {}),
    });
    const header = csv.split("\n")[0] ?? "";
    // These columns are emitted only once some card publishes them. They
    // were dark for a release; live, every card carries ranked_on and
    // three carry a dimension repoint, so both light up.
    for (const column of [
      "ranked_on",
      "ranked_impact_usd",
      "ranked_on_note",
      "drill_dimension_repoint",
    ]) {
      expect(header, `the live worklist CSV must carry ${column}`).toContain(column);
    }
    expect(csv).toContain("proc_group\u2192primary_proc_group");
  });

  it("draws the by-month grain as a trend line, not as disconnected bars", async () => {
    const { driver, drift } = newDriver();
    const events = await drive(driver, {
      utterance: "show me denied dollars by month for the last 6 months",
    });
    expect(drift, "a by-month turn must produce no contract drift").toEqual([]);

    const charts = events
      .filter((e) => e.type === "chart_spec")
      .map((e) => (e.type === "chart_spec" ? e.spec : null))
      .filter((spec) => spec !== null);
    const trend = charts.find((spec) => spec.xLabel === "month");
    expect(trend, "the by-month grain must publish a chart").toBeDefined();
    if (!trend) return;

    // The wire declares `stacked_bar` over an ordered ISO month axis, so
    // the axis has to decide: six disconnected bars beside a finding whose
    // own title reads "$885,721.50 → $1,193,126.92" is the shape this
    // fixes. The published type still travels on `wireChartType`.
    expect(trend.wireChartType).toBe("stacked_bar");
    expect(trend.kind).toBe("line");
    expect(trend.rows.length).toBeGreaterThanOrEqual(2);
    // Money arrives in cents and is NOT rescaled — only `ratio` is — and
    // the title is composed from the frame's own columns.
    expect(trend.unit).toBe("cents");
    expect(trend.title).toBe("Denied dollars by month");
    for (const row of trend.rows) {
      expect(row.label).toMatch(/^\d{4}-\d{2}(-\d{2})?$/);
    }
  }, 180_000);
});
