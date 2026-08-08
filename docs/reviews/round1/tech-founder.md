# Revi — technical diligence review

**Reviewer persona:** 2x infrastructure founder / CTO-brain. Architecture and operational readiness.
**Repo:** `/Users/dev/revi_v2` @ `4a9afe6`
**Method:** API driven over real HTTP (`REVI_LLM_MOCK=1`, port 8164, isolated process), plus targeted code
reads. Every number below came from a command I ran in this session.

---

## Verdict

This is not demo-ware, and it is also not a system I would put in front of a health system next quarter.
The *determinism spine* is real and I could not break it: three cold sessions on a fresh process produced
byte-identical plan hashes and impacts, and those impacts match `data/answer_key.json` to the cent. The
boundary work is real too — import-linter is 6 kept / 0 broken and the vendor bans genuinely bite. What is
missing is everything between "the engine computes the right number" and "this is a multi-tenant service":
**there is no authentication, no authorization, no tenant enforcement, no rate limiting, no metrics, no
timeouts on the LLM path, idempotency that is not idempotent, and an unbounded write-leak into the
analytical store that has already grown the local warehouse 2.5x during development.** Separately, the
flagship five-turn demo publishes a finding whose headline states the wrong comparison basis, and the
portfolio's top 16 ranked cards are all un-drillable.

Right scaffolding, honest-ish gaps, but the gap list in `docs/acceptance-walkthrough.md` is materially
shorter than the real one — and the items it does list are understated in magnitude.

---

## What genuinely survives scrutiny

1. **Determinism is real, not asserted.** Fresh process, three separate sessions, same question:
   `plan_hash 5b18277f1caa42d1…` all three times, identical `impact_cents`. Cold run 97 ms, warm 13 ms
   (evidence cache). The numbers match ground truth exactly:

   | | API `F1/F2/F3` | `answer_key.json` `3_cash_decline.snap_003` |
   |---|---|---|
   | State Medicaid | `-9909308` (cur `8812843`, prior `18722151`) | `-9909308` / `8812843` / `18722151` |
   | Atlas Commercial | `-4894041` (cur `26330486`) | `-4894041` / `26330486` |
   | Meridian Health | `-3806410` (cur `14613237`) | `-3806410` / `14613237` |

2. **Import-linter boundaries are not theater — where they exist.** `uv run lint-imports` → *"Analyzed 147
   files, 871 dependencies … Contracts: 6 kept, 0 broken."* The vendor bans are `forbidden` contracts with
   `include_external_packages = true`, so indirect chains (`revi_investigation → revi_connector_duckdb →
   duckdb`) are caught, not just direct edges. Only two composition-root edges are whitelisted and both are
   named. This is the good version of this pattern. (See finding #9 for what the contracts do *not* cover.)

3. **The refusal surface is typed and consistent.** Unknown metric → `200 {"outcome":"error","error":
   {"code":"UNSUPPORTED_CONCEPT","message":"typed metric 'unicorn_dollars' is not in the pack"}}`; unknown
   investigation → `404` envelope; malformed body → `422` FastAPI shape; nonsense question →
   `clarification_required`, never a guess. The cardinality budget actually fires: a 4-dimension × 50-year
   typed spec was refused in **13 ms** with `QUERY_BUDGET_EXCEEDED: probe 'main' groups an estimated 14400
   cells, over the 5000-cell budget`. That is a real guard, enforced before any query.

4. **SSE is well-shaped.** T1 streamed 25 typed frames in order — 13 `stage`, `context_header`, 3 `finding`,
   4 `chart_spec`, 3 `narrative_delta`, `turn_complete`. A failing turn streams `stage → error →
   turn_complete` with the same envelope as the blocking JSON body. Streaming and blocking do not diverge.

5. **The LLM spike (`packages/adapter-claude/spikes/RESULTS.md`) is the most honest document in the repo.**
   Measured latency/cost per call, five named traps with reproductions, and the line I most respect:
   *"Operator preference is prompt-steerable but not deterministic at default settings — M9 evals should
   score operator choice, not just parseability."* That is a founder who knows where the risk is.

6. **Content-side self-criticism is genuine.** The exclusion-polarity write-up (7 contracts authored as
   inclusion predicates, 6 inert, 1 live-and-wrong at 66.13% where the truth was 77.76%) is the kind of
   post-mortem most teams bury. The conformance guard that fell out of it fails startup unconditionally.

---

## Findings

### 1. CRITICAL — Published findings state the wrong comparison basis, in the flagship demo

`packages/investigation/src/revi_investigation/application/findings.py:300-310`:

```python
@staticmethod
def _period_phrase(spec: AnalysisSpec) -> str:
    comparison = spec.context.comparison
    if comparison is None:            return "vs prior period"
    if comparison.kind is ComparisonKind.PRIOR_YEAR:  return "vs prior year"
    requested = spec.context.window.requested
    if requested is not None and requested.quantity == 1:
        return f"vs prior {requested.unit.value}"     # <-- reads the CURRENT window
    return "vs prior period"
```

`ComparisonKind.CUSTOM` — which is exactly what `set_comparison(custom={start,end})` produces, i.e. turn 4
of the reference conversation ("Compare that to Q1") — falls through to the `quantity == 1` branch and is
labelled from the *current* window's unit. Observed live on T4:

```
header : "2026-07-27..2026-08-02 (post) · vs 2026-01-01..2026-03-31 · cohort … · watermark wm_003"
F10    : "16 denied dollars down $169,026 vs prior week"          grade=direct  confidence=high
statement: "16: denied dollars moved from 17051059 to 148451 cents (down $169,026, -99.1% vs prior week)."
warnings : ["suppression: …"]     # nothing about the comparison
```

The header says Q1; the finding says "prior week". Same response body. This is a direct violation of the
product's core promise ("every answer carries its effective context"), on a DIRECT-graded, high-confidence
finding, on the turn you will demo.

Worse, the underlying number is semantically junk and nothing flags it: a **7-day** current window is being
differenced against a **90-day** baseline with no normalisation, producing "-99.1%" declines and a $169k
"impact" that are pure period-length artifacts. A CFO acting on F10 would be acting on nothing.

`packages/investigation/tests/test_reference_conversation.py:289-295`
(`test_t4_executes_only_comparison_side`) asserts `header.comparison_kind == "custom"` and the Q1 dates —
and never touches `finding.title` or `finding.statement`. 633 tests, and this class of cross-field
contradiction is not one of them.

**Fix:** (a) `_period_phrase` must render CUSTOM as "vs 2026-01-01..2026-03-31"; (b) `compare` must emit a
warning (or refuse) when current and comparison window lengths differ by more than a tolerance; (c) add a
property test asserting the finding text and the header agree on the comparison for every `ComparisonKind`.

---

### 2. CRITICAL — No authentication, no authorization; `tenant` is a decorative string

There is no security scheme anywhere. `contracts/openapi.json` → `components` has exactly one key
(`schemas`); top-level `security` is `None`. `apps/api/src/revi_api/app.py:153-290` registers seven routes
and not one `Depends`, header check, or auth middleware — the only middleware is CORS.

Demonstrated live:

```
POST /v1/sessions {"tenant":"acme-health-system"}
  -> 200, pack base-rcm@1.0.0, watermark wm_003
POST /v1/sessions/{acme_sid}/turns {"utterance":"Why did cash decline last week?"}
  -> plan_hash 5b18277f1caa42d1…   (identical to tenant "demo")
  -> F1 "State Medicaid cash posted down $99,093"  -9909308
GET  /v1/sessions/{demo_sid}/lineage      -> 200, full lineage of another tenant's session
GET  /v1/investigations/{demo_inv_id}     -> 200, full findings, statements, cent-level values
```

`tenant` is accepted from the client body, stored on the `Session` record, and then never consulted:
`MemorySessionStore.get(session_id)`, `MemoryInvestigationStore.get(investigation_id)`,
`.lineage(session_id)` and `MemoryReferentRegistryStore.resolve(session_id, referent)` all key on id alone
(`apps/api/src/revi_api/memory_stores.py:30-80`). The Postgres implementations mirror them. And
`apps/api/src/revi_api/service.py:103` hard-codes `tenant="api"` on every engine-level request:

```python
engine_request = SubmitTurnRequest(
    tenant="api",  # sessions carry the tenant; new implicit sessions use this
```

Three compounding consequences:
- `GET /v1/portfolio/latest` (`app.py:268`) takes **no session and no tenant** — it is a single global feed.
  Every tenant would see the same 33 anomaly cards.
- `build_components()` composes one warehouse path + one pack for the whole process. Tenancy is not a
  runtime dimension at all; it is a label.
- CORS is `allow_origins=["http://localhost:3000"]`, `allow_credentials=True`, hard-coded at `app.py:159`
  with no env override. You cannot deploy this anywhere without editing source.

For a product whose entire value proposition is *governed* access to PHI-derived financial data, "authn/authz
is future work" is not a gap, it is a missing product surface. This needs to be on the roadmap as a
milestone, not a footnote.

---

### 3. CRITICAL — Unbounded cohort-table leak into the analytical store; the sweep cannot clean it

`packages/connector-duckdb/src/revi_connector_duckdb/repository.py:211-249`:

```python
cohort_id = f"cohort_{uuid.uuid4().hex[:12]}"
con = self._connect(read_only=False)   # short-lived read-write: cohort DDL only
...
con.execute(f"CREATE TABLE {table} AS SELECT DISTINCT …")
...
self._cohorts[cohort_id] = materialization      # process-local registry (line 91)
```

The cohort id is a random UUID, not a content hash, so **identical cohort definitions materialise a brand
new table every single time.** Measured: running the same three-turn conversation five times took the
warehouse from 199 → **209** cohort tables. Current state of `data/revi_warehouse.duckdb`:

```
cohort tables: 209   rows: 11,645,898   file: 146,026,496 bytes
```

Each drill-down turn writes a fresh 55,722-row table. The file has grown ~2.5x (from ~57 MB) purely from
un-GC'd cohorts during development. And `make sweep` cannot fix it:

```
$ uv run python -m revi_scheduler.sweep
WARNING  REVI_DATABASE_URL unset — SKIPPING the cohort-metadata half of the sweep
INFO     dropped 0 expired cohort table(s) in the warehouse
  warehouse: dropped 0 cohort table(s)
             (the connector's cohort registry is process-local: a fresh sweep only ever drops what it
              materialized itself)
```

The walkthrough lists this as follow-up #4 and says the CLI "says so in its own output rather than implying
the warehouse was cleaned." That framing understates it twice: (a) the leak is unbounded and monotonic, not
merely unreported; (b) it is a leak into the **analytical** store, which in production is the customer's
Snowflake. Ship this as-is and Revi is issuing an unbounded stream of `CREATE TABLE` statements into a
customer warehouse and never dropping any of them. At 100 analysts × 20 drill turns/day that is 2,000
orphan tables per day and a storage-credit line item the customer's DBA will find before you do.

This also surfaces an unexamined procurement fact: **Revi needs `CREATE TABLE` privileges on the customer's
analytical warehouse.** `docs/architecture.md` discusses the read path in detail and never mentions the
write path. That is the single hardest sentence in any hospital security review.

**Fix:** content-address cohort ids (hash the definition + watermark) so identical drills reuse one table;
persist the registry (or add a `list_cohort_tables` primitive so the sweep can enumerate by naming
convention); add `expires_at` and make the sweep authoritative.

---

### 4. HIGH — A single concurrent reader breaks every cohort turn; DuckDB is a single-writer bottleneck

Because cohort materialisation opens the warehouse **read-write**, any other process holding the file breaks
it. Reproduced deterministically:

```python
ro = duckdb.connect('data/revi_warehouse.duckdb', read_only=True)   # a 2nd API replica / BI tool / the sweep
# ... then drive a cohort-pinning turn against the running server:
-> 200 {"outcome":"error","error":{"code":"SOURCE_UNAVAILABLE","message":"analytical source is unavailable"}}
```

Server log:

```
duckdb connect failed for data/revi_warehouse.duckdb: IO Error: Could not set lock on file …:
Conflicting lock is held in … (PID 94385) by user sean.
turn failed with SOURCE_UNAVAILABLE: analytical source is unavailable
```

So: you cannot run a second API replica, you cannot run the sweep while the API is up, and any analyst with
the file open in DuckDB CLI takes drill-downs down. The local stack is single-process by construction, which
means every load/HA/failover claim is untested.

Compounding it: `repository.py:122-127` catches the driver error, logs it, and does `raise
SourceUnavailableError(...) from None` — deliberately severing the cause. The API response carries only
`"analytical source is unavailable"`. Your on-call engineer gets a generic 200-with-error-outcome and has to
go read stdout to learn it was a file lock. Attach a machine-readable `details` payload.

---

### 5. HIGH — Idempotency is not idempotent, is not body-bound, and leaks memory

`apps/api/src/revi_api/service.py:72,94-97,126-127`:

```python
self._idempotent: dict[tuple[str, str], TurnResult] = {}
...
stored = self._idempotent.get((session_id, request.idempotency_key))
if stored is not None: return stored
...                                      # <-- full turn executes here
self._idempotent[(session_id, request.idempotency_key)] = response
```

Classic check-then-act with no reservation. Measured with 6 concurrent requests carrying the *same*
`idempotency_key`:

```
sequential same key -> same investigation:  True
concurrent same key -> distinct investigations: 6
investigations created in that session:     6
```

Six full investigations, six sets of referent handles, six lineage nodes — from the exact retry storm an
idempotency key exists to prevent.

Second defect: the key is not bound to the request body. Posting key `K3` with a bad typed spec, then
re-using `K3` with a completely different utterance, returns the *first* response:

```
POST … {"spec":{"metric_ids":["nope"]…}, "idempotency_key":"K3"} -> error UNSUPPORTED_CONCEPT
POST … {"utterance":"Why did cash decline last week?", "idempotency_key":"K3"} -> error UNSUPPORTED_CONCEPT
```

A client that reuses a key gets a silently wrong answer. Stripe's rule — hash the request, `409` on
mismatch — exists for this.

Third: the dict is process-local (so useless behind more than one replica, *even with Postgres wired* —
there is no `revi_*` idempotency table) and unbounded, holding a full `TurnResult` with findings and chart
specs per key, forever. That is a memory leak proportional to traffic.

---

### 6. HIGH — The portfolio's top 16 ranked cards are all un-drillable

I drilled **every** card via its `drill_spec` from a fresh session:

```
ANSWERED 6 / 33
failures: Counter({'UNSUPPORTED_CONCEPT': 18, 'DATE_BASIS_INVALID': 9})

rank  0  ANM-023 credit_balance_dollars  score 0.6000  -> UNSUPPORTED_CONCEPT
rank  1  ANM-024 credit_balance_dollars  score 0.6000  -> UNSUPPORTED_CONCEPT
rank  2  ANM-001 denial_rate             score 0.2726  -> DATE_BASIS_INVALID
rank  3  ANM-013 gross_collection_rate   score 0.2634  -> UNSUPPORTED_CONCEPT
…
rank 16  ANM-004 denials_unworked_pct    score 0.0692  -> first card that answers
```

The walkthrough discloses "6 of the 33 cards answer today" and attributes each refusal to catalog content
gaps — accurate and creditable. What it does not say is that **answerability and priority are almost
perfectly anti-correlated**: ranks 0–15 are 100% dead, and the six that work are ranked 17, 18, 19, 21, 24
and 28 with priority scores of 0.069 down to 0.036. The governed priority formula's entire job is to tell an
analyst what to work first, and everything it points at is a dead end. That is not a residue, it is the
daily-prioritisation product not working.

The right move is to stop *ranking* cards the platform cannot investigate — either suppress them, or badge
them "not investigable at this catalog version" so the ordering stays honest.

---

### 7. HIGH — No timeout, no retry, no circuit breaker, and no aggregate budget on the LLM path

`grep -n "timeout\|asyncio.wait_for\|TimeoutError"` over `packages/adapter-claude/src/…/adapter.py` and
`packages/investigation/src/…/application/llm/*.py` returns **nothing**. A hung Claude Agent SDK subprocess
hangs the request indefinitely; uvicorn has no default request timeout either.

Cost control is `max_budget_usd` **per call**, default `Decimal("0.50")`, and `config.py:14-15` concedes it
is "enforced by the CLI *between* turns, so treat it as a soft cap." There is no per-session, per-tenant, or
per-day ceiling anywhere.

Now the envelope, using the team's own measurements (`spikes/RESULTS.md` §4): **5–15 s wall and
$0.008–$0.021 per pinned Sonnet call.** Observed call counts per turn: T1 = 2, T2/T3/T4 = 3, T5 = 1
(`usage.llm_calls`). So:

- T1 ≈ **10–30 s**; a refinement turn ≈ **15–45 s**. The deterministic engine contributes 13–97 ms. **>99%
  of the latency budget is the model.** A "conversational investigation" loop where each gesture costs half
  a minute is not conversational; SSE stage frames improve perceived latency but do not fix it.
- ~$0.04/turn → a five-turn investigation ≈ **$0.19**. 100 analysts × 20 investigations/day × 5 turns ≈
  10,000 turns/day ≈ **$400/day ≈ $146k/year of COGS for one mid-size health system**, uncapped. That belongs
  in the pricing model, and it needs a per-tenant budget enforced server-side.

Transport is also a concern: the adapter is built on `claude_agent_sdk.query` (the Agent SDK's bundled CLI),
not the Messages API. That means a subprocess per call, no connection reuse, a bundled-CLI version in your
dependency surface, and — per `.env.example` — an auth story that is "optional on a machine with an
interactive Claude Code login." Prompt caching measured 2.4–3.2K `cache_read` tokens in the spike, but the
cache is per-CLI-session; hit rates under server-side concurrency are unmeasured. I would want a
Messages-API adapter behind the same port before load testing means anything.

---

### 8. HIGH — Zero LLM evaluation. Demo mode proves the engine, and proves nothing about the model

`ScriptedLanguageModel.structured` (`apps/api/src/revi_api/scripted_llm.py:76-86`) is first-match-wins
substring matching on the rendered prompt. Every paraphrase of the flagship question falls off a cliff:

```
"Why did cash collections drop last week?"  -> clarification_required
"cash declined last week, why?"             -> clarification_required
"Why did cash decline in the last week?"    -> clarification_required
"What drove the drop in cash last week?"    -> clarification_required
"Show me why cash fell"                     -> clarification_required
```

The module docstring is honest that only the probabilistic layer is scripted, and the engine-side claim
holds up. But the consequence is that **the interpretation layer — the product's entire premise — has never
been measured.** There is no eval package, no eval directory, no scored corpus; `find . -iname "*eval*"`
outside `node_modules` returns nothing. There are exactly **2** `live_llm`-marked references in the whole
suite, both in one adapter smoke file.

The spike already found the failure mode: before a steering sentence was added, *all four* trials compiled
"the top two payers" to `AddFilter(payer in [...])` instead of `DrillInto(F1/F2)` — semantically a different
investigation, with different cohort pinning and different lineage. Schema fidelity was perfect; operator
*choice* was not. Nothing in this repo measures operator choice.

The scripted narrative compounds the blind spot: `compose_demo_narrative` emits handle-and-grade filler
("F1 leads the certified findings on this turn, followed by F2 and F3. Every finding here is graded direct…
Open a finding to see the probes"). Content-free by construction, so the grounding validator has nothing to
catch — and the narrative path, arguably the most user-facing surface, is exercised only by synthetic unit
tests.

**Before the next raise I would want:** a 200–500 question eval corpus with graded expected operators, run
against the real adapter, scored on (a) turn class, (b) metric/dimension ids, (c) operator choice, (d)
refusal correctness — with a tracked pass rate and a regression gate.

---

### 9. MEDIUM — `docs/architecture.md` overstates what import-linter enforces

The doc presents a "vendor isolation" table under the sentence *"Enforced by import-linter (root
`pyproject.toml`, run via `make lint`)"*, including the row:

> | `pydantic` | contracts packages + LLM schemas only — never domain modules |

`grep -n pydantic pyproject.toml` → **one hit, line 113**, inside the `forbidden_modules` list of the
*kernel-standalone* contract. There is no contract preventing `revi_presentation`, `revi_pack`,
`revi_catalog`, or any `*/domain/` module from importing pydantic. Four of the five table rows are enforced;
the fifth is enforced for exactly one package.

Separately, the doc's headline dependency rule —

```
entrypoints → application → domain
infrastructure → application ports + domain
```

— has **zero** mechanical enforcement: `grep -c 'type = "layers"' pyproject.toml` → `0`. The six contracts
are 5 `forbidden` + 1 `independence`. Nothing stops `domain` from importing `application` tomorrow. (I
checked `revi_investigation/domain/*.py` and it is currently clean — this is an unguarded invariant, not a
live violation.) Add an import-linter `layers` contract per capability; it is a ten-line change and it turns
a convention into a rule.

---

### 10. MEDIUM — No metrics, no traces, no alerts; "observability" is trace rows and `logging`

`grep -rn "prometheus|opentelemetry|otel|statsd|datadog|sentry"` across the root and every package
`pyproject.toml` → **no matches**. The only instrumentation is three stdlib `logging.getLogger` calls
(`app.py:58`, `wiring.py:82`, `service.py:38`) with no structured formatter and no correlation id in the log
line. There is no `/metrics`, no span export, no RED metrics, no way to answer "what is p95 turn latency
this week" or "which tenant spent the most on the model yesterday."

The trace record is genuinely rich (I confirmed T5's meta answer cites 6 probe hashes, per-probe
`cache_hit`, the operator DAG with versions, and per-probe grades) — but a trace table is forensics, not
operations. You cannot page on it.

`/v1/health` (`app.py:279-290`) returns `{"status":"ok", …}` unconditionally and hits the warehouse on every
call to fetch the newest watermark. There is no liveness/readiness split, no dependency detail, no build/
version stamp. A 10-second k8s liveness probe would issue a warehouse query every 10 seconds forever.

---

### 11. MEDIUM — No rate limiting and no input caps: trivially abusable, and unauthenticated

Only CORS middleware is registered. Measured against the running server:

| Input | Result |
|---|---|
| 2 MB `utterance` (`"cash "×400000`) | `200` accepted, 248 ms — no length cap anywhere |
| `add_filter` with a **50,000-value** `in` predicate | `200 answer`, **8,785 ms** of engine work |
| 2,000 chained `refinements` in one body | `200 answer`, 3,724 ms |

`packages/investigation/src/revi_investigation/application/llm/guard.py` caps *tabular shape*
(`_DELIMITED_LINES_LIMIT = 8`, `_ROW_OBJECT_LIMIT = 10`) but not length — so in real mode that 2 MB utterance
becomes a ~500K-token prompt and an instant budget event. The 50K-value predicate is the sharper one: an
unauthenticated caller gets ~9 s of query work per request, and there is no cap on predicate cardinality
(`grep max_values|len(values)` in `kernel/filters.py` and `application/validation.py` → nothing). A handful
of concurrent requests saturates the process. Note the cardinality budget guards *group-by cells*, not
*predicate width* — the two need separate limits.

---

### 12. MEDIUM — The evidence cache has no tenant key, no TTL, and no purge path

`EvidenceCache` (`packages/investigation/src/revi_investigation/application/ports.py:162-171`) is keyed on
`(probe_hash, watermark_id, pack_snapshot_id)`. The Postgres table mirrors it exactly
(`packages/store-postgres/src/revi_store_postgres/tables.py:126-135`):

```python
evidence = sa.Table("evidence", metadata,
    sa.Column("probe_hash", sa.Text, primary_key=True),
    sa.Column("watermark_id", sa.Text, primary_key=True),
    sa.Column("pack_snapshot_id", sa.Text, primary_key=True),
    sa.Column("frame", JSONB, nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    schema=CACHE_SCHEMA)
```

Compare the `cohorts` table twelve lines above, which *does* carry `tenant` and `expires_at`. So:

- **No tenant column.** The day two tenants share a deployment with row-scoped data, a probe hash that does
  not encode the tenant scope predicate serves tenant A's aggregate frame to tenant B. The port signature
  bakes the omission in at the type level, so this is a refactor across every implementation, not a config
  change.
- **No TTL, no eviction, no purge.** PHI-derived aggregate frames accumulate in JSONB forever. In this
  domain "we keep every computed frame indefinitely with no deletion path" is a conversation with a privacy
  officer, and it also means there is no answer to a customer's right-to-delete request.
- The in-memory twin (`memory_stores.py:135-147`) is likewise an unbounded dict of full `EvidenceFrame`s.

The `revi_kernel/cohort.py:11` docstring already says the intent — *"tenant-scoped, access-controlled,
TTL-bound"* — the cache simply did not inherit it.

---

### 13. MEDIUM — 633 tests, zero concurrency tests, zero failure injection

`uv run pytest -q` → **633 passed, 16 deselected in 31.43s**. Verified, fast, and a real asset. What it does
not contain:

- **Concurrency:** `grep -rn "asyncio.gather|ThreadPool|threading\." packages/*/tests/*.py` → **one** hit,
  `packages/adapter-claude/tests/test_adapter.py:380`. Nothing exercises concurrent turns on a session,
  concurrent cohort materialisation, or the idempotency race in finding #5.
- **Failure injection:** `grep -rn "side_effect|raise ConnectionError|raise OperationalError|raise
  TimeoutError|_Failing|Broken"` across all test files → **zero** hits. No test makes the repository, the
  store, or the LLM fail. Every error path in the system is reached only by *validation* refusals, never by
  *infrastructure* failures. `SourceUnavailableError` has a translation table in the adapter docstring and
  no test that produces it from a real fault.
- **Large-cardinality data:** the warehouse is ~300K rows per fact table per snapshot (13.6M rows total
  including the cohort junk). Real 835 volume for a mid-size system is 1–2 orders of magnitude larger, and
  the 97 ms cold T1 tells you nothing about it.

I found the concurrency behaviour empirically instead: 8 concurrent identical refinements on one session
produced a **linear chain of 8 investigations**, each parented on the previous
(`inv_362c… → inv_ba3b… → inv_fc3a… → …`), and minted 24 distinct referent handles for the same three facts.
There is no session-version check or optimistic concurrency anywhere — a double-clicked refine button
fabricates a lineage of nested refinements that never happened.

---

### 14. LOW — Domain semantics leak into the UI as bare numbers

T3/T4 findings render CARC codes as if they were quantities:

```
F7  "204 denied dollars down $12,545 vs prior week"
F8  "2 denied dollars down $8,564 vs prior week"
F10 "16 denied dollars down $169,026 vs prior week"
```

"2 denied dollars down $8,564" is unparseable to a human. CARC codes need their group code and short
description from the pack (which exists — the DEFINITIONAL path returns "CARC 3 · Copay" correctly from
`base-rcm@1.0.0`). The finding formatter is not consulting it.

Also: the reference conversation's turn 2 ("Break that down by payer") returns the **identical plan hash**
(`5b18277f1caa42d1…`) and identical impacts as turn 1, minting F4/F5/F6 for facts already known as F1/F2/F3.
It is a semantic no-op that costs 3 LLM calls and inflates the referent namespace. The demo's second beat
adds no information.

---

## What I would want before writing a cheque

1. **An auth/authz milestone with a design.** Tenant as a first-class runtime dimension: request identity,
   tenant-scoped stores, tenant-scoped portfolio, tenant in the cache key, per-tenant model budget. Today
   there is no place to put any of it.
2. **An eval harness with a published pass rate.** Operator-choice accuracy on a real corpus against the
   real adapter. Until that number exists, every claim about "conversational investigation" is a hypothesis.
3. **Fix the cohort write path before any Snowflake conversation.** Content-addressed ids, a persisted
   registry, a sweep that actually sweeps — and a written answer to "why does your app need CREATE TABLE in
   my warehouse."
4. **A real load test.** 10x–100x warehouse volume, N concurrent sessions, measured p50/p95 with the real
   model. Right now the only latency numbers that exist are 13 ms of engine time and a five-trial LLM spike.
5. **Ship finding #1 today.** A DIRECT-graded finding that states the wrong comparison basis, on the demo
   turn, is the one bug that turns a credibility asset into a credibility liability in a room full of CFOs.

The determinism spine, the boundary discipline, and the intellectual honesty of the failure write-ups are
genuinely above the bar for this stage. The operational surface is roughly where a good prototype is at the
end of a build sprint — which is fine, as long as nobody tells a customer otherwise.
