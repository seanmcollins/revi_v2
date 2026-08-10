#!/usr/bin/env node
/**
 * Capture the live fixtures this app's tests assert against.
 *
 * Until now there was no checked-in tool for this. The fixtures under
 * `src/lib/__fixtures__/` were captured by "an httpx script (ad hoc, not
 * checked in)" — the walkthrough says so in those words — which meant a
 * re-sync after an engine wording change was an archaeology exercise, and
 * the assertions drifted from the sentences the engine actually emits.
 * They are the tests' only contact with real payloads, so a fixture nobody
 * can regenerate is a guard nobody can trust.
 *
 * Usage (with the API running and a warehouse behind it):
 *
 *     node scripts/capture-fixtures.mjs                 # all groups
 *     node scripts/capture-fixtures.mjs rounds          # one group
 *     REVI_API=http://localhost:8018 node scripts/capture-fixtures.mjs
 *
 * Every capture is a REAL request against a real deployment. Nothing here
 * composes a payload, edits one, or fills a gap — a fixture with a
 * hand-written field in it is a test that passes against a server that
 * does not exist.
 *
 * The `rounds` group is TYPED-ONLY: every request it makes either reads a
 * route or posts a typed spec, so it costs no model calls and can be run
 * against a `REVI_LLM_MOCK=1` deployment. The `answers` group replays
 * natural-language questions and therefore does spend model calls; it is
 * opt-in for that reason.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIXTURES = join(HERE, "..", "src", "lib", "__fixtures__");
const API = process.env.REVI_API ?? "http://localhost:8000";

async function get(path) {
  const response = await fetch(`${API}${path}`, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`GET ${path} → HTTP ${response.status}: ${(await response.text()).slice(0, 400)}`);
  }
  return response.json();
}

async function post(path, body) {
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`POST ${path} → HTTP ${response.status}: ${(await response.text()).slice(0, 400)}`);
  }
  return response.json();
}

function write(name, payload) {
  mkdirSync(FIXTURES, { recursive: true });
  writeFileSync(join(FIXTURES, name), `${JSON.stringify(payload, null, 2)}\n`);
  console.log(`wrote ${name} (${JSON.stringify(payload).length} bytes)`);
}

/**
 * The Rounds surface, whole: the brief, the tiles, the pins behind them
 * and one lead's lifecycle record.
 *
 * Captured together and at ONE watermark on purpose. A brief that diffed
 * wm_002→wm_003 beside tiles evaluated at wm_004 would be a fixture that
 * cannot happen, and a test built on it would assert a shape the product
 * never produces.
 */
async function captureRounds() {
  const health = await get("/v1/health");
  const brief = await get("/v1/rounds/brief");
  const rounds = await get("/v1/rounds");
  const pins = await get("/v1/rounds/pins");
  const portfolio = await get("/v1/portfolio/latest");

  // A lead with a real lifecycle on it. Preferred over an untouched one:
  // `open` is the state that needs no fixture, and the verification note
  // is the sentence the surface must render verbatim.
  const worked = portfolio.items.find((item) => item.lead_status && item.lead_status !== "open");
  const lead = worked ? await get(`/v1/rounds/leads/${worked.anomaly_id}`) : null;

  write("live-rounds.json", {
    _meta: {
      captured_from: `${API} — GET /v1/rounds/brief, /v1/rounds, /v1/rounds/pins, /v1/rounds/leads/{id}`,
      captured_by: "scripts/capture-fixtures.mjs rounds",
      watermark_id: health.watermark,
      store_mode: health.store_mode,
      llm_mode: health.llm_mode,
    },
    brief,
    rounds,
    pins,
    lead,
    // Two cards, not thirty-three: the ones that carry the fields this
    // surface added. A whole snapshot is already fixtured in
    // wire-samples.json and duplicating it here would be two copies of one
    // payload drifting apart.
    cards: portfolio.items
      .filter((item) => item.time_to_impact || (item.lead_status && item.lead_status !== "open"))
      .slice(0, 4),
  });
}

/**
 * The engine's own warning prose, re-captured through TYPED turns.
 *
 * Typed rather than natural-language so this group needs no model: a typed
 * first turn runs the identical planning, §6.6 validation and findings
 * stage, and emits the identical warning sentences. What it does not
 * reproduce is the composed narrative, which is why the answer fixtures
 * that need prose live in the `answers` group.
 */
async function captureWireWarnings() {
  const health = await get("/v1/health");
  const session = await post("/v1/sessions", { tenant: process.env.REVI_TENANT ?? "demo" });
  const captured = {};
  for (const [name, spec] of Object.entries(TYPED_SPECS)) {
    const answer = await post(`/v1/sessions/${session.session_id}/turns`, {
      idempotency_key: `capture-${name}-${Date.now()}`,
      spec,
    });
    // A refused turn is not a fixture. Failing loudly here is the whole
    // point: a capture that quietly wrote an error envelope into the
    // fixture would turn a broken spec into a passing test.
    if (answer.outcome !== "answer") {
      throw new Error(
        `${name}: the platform did not answer (${answer.outcome}) — ${answer.error?.message ?? ""}`,
      );
    }
    captured[name] = await get(`/v1/investigations/${answer.investigation_id}`);
    console.log(`  ${name}: ${captured[name].warnings_v2?.length ?? 0} warnings`);
  }
  write("live-typed-turns.json", {
    _meta: {
      captured_from: `${API} — POST /v1/sessions/{sid}/turns with a typed spec, then GET /v1/investigations/{iid}`,
      captured_by: "scripts/capture-fixtures.mjs warnings",
      watermark_id: health.watermark,
      note: "Typed first turns: no model call, identical warning prose to the NL path.",
    },
    ...captured,
  });
}

/**
 * Typed specs chosen for the WARNINGS they produce, not for their answers.
 * Each one is here because a test pins a sentence the engine emits on it.
 */
const TYPED_SPECS = {
  // Mostly-suppressed ranking → RANKING_REFUSED / BOUNDED_CELLS_UNRANKED /
  // SUPPRESSION_BOUNDED, and the chart's own `upper bounds:` annotation.
  bounded_ranking: {
    metric_ids: ["denial_rate"],
    dimensions: ["provider"],
    window: { quantity: "1", unit: "month", mode: "full_periods" },
    basis: "service",
  },
  // A plain money cut: the quiet baseline, so a test can tell a warning
  // that always appears from one that appears on the bounded path.
  denied_by_payer: {
    metric_ids: ["denied_dollars"],
    dimensions: ["payer"],
    window: { quantity: "1", unit: "month", mode: "full_periods" },
  },
};

const GROUPS = { rounds: captureRounds, warnings: captureWireWarnings };

const requested = process.argv.slice(2);
const names = requested.length > 0 ? requested : Object.keys(GROUPS);
for (const name of names) {
  const group = GROUPS[name];
  if (!group) {
    console.error(`unknown group ${name}; known: ${Object.keys(GROUPS).join(", ")}`);
    process.exit(2);
  }
  console.log(`capturing ${name} from ${API}…`);
  await group();
}
