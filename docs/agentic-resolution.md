# Agentic resolution — design addendum (v2.1)

Status: approved direction (owner, 2026-08-11). This document governs the agentic
resolution mode and the generalized deep-research planner. Where it conflicts with
the v2 design doc, the v2 invariants win; this addendum extends the *resolution*
architecture without moving the computation architecture.

## The bar this exists to meet

**The completeness bar (owner, verbatim intent):** if any question — initial or
follow-up — has a path supported by the semantic layer and the available data,
Revi finds it, no matter how complicated. Only two honest non-answers exist:

1. **No certified path exists** → refuse, naming the specific data gap — a
   statement about the data, never about the engine.
2. **Materially different paths exist** → clarify by offering the runnable paths.

And the experience bar: a fleet of fast, accurate, *flexible* analysts with the
brains of RCM consultants. Flexibility is identity, not a feature.

## The line that does not move

The LLM never computes a number, never writes SQL, never touches data directly.
Every figure a reader sees is produced by the deterministic plane from governed
semantics, carries provenance, and is independently rederivable. This is the
product's differentiation and it is non-negotiable. Agentic resolution changes
*how paths are found*, never *how numbers are made*.

## The architecture: free routing over certified roads

The resolution loop replaces one-shot compilation with iteration. In a turn (or
a research run), the model sees:

- the full semantic catalog (metrics with their scope dimensions and improvement
  directions, certified dimensions, window forms, transforms, the statistics
  functions, the playbook library);
- the conversation's standing context (previous question, window, scope,
  subject, referents — per the conversational-context wave);
- the pack's RCM knowledge (what matters and why, payer mechanics, regulatory
  context) — consulted, quotable as context, never a source of numbers;
- the certified results of its own previous steps in this loop.

It then selects its next operation from **closed tool families**, or answers.

### Two tool families

**Compute operations answer questions.** Governed metric evaluations, cuts,
comparisons, transforms, statistical estimators — the existing operator algebra
and the statistics plane. Each call is validated (§6.6), executed
deterministically, and returns certified findings.

**Discovery operations choose approaches.** Cheap, deterministic, certified
*orientation* reads: concept-to-path resolution (which certified paths could
express "COB" here), dimension value censuses, coverage and population
profiling, benchmark availability. These unify the existing primitives
(capability negotiation, selection census, subject-presence probing) into one
governed discovery API. The agent orients before it computes, and **discloses
path choices in the answer**: "Your data carries COB mainly in remit codes —
the category field is sparsely populated here, so I read the codes."

Discovery results appear in the trace like everything else: debug shows *why*
the path was chosen, not just which path ran.

### The recorded path is the plan

Every step the loop takes is recorded. The recorded path serves exactly the
role the compiled plan serves today: it is what permalinks restore, what replay
re-executes, what the warehouse-diff harness audits, and what the trace
explains. Plan-hash caching keys on it.

**Named trade (accepted):** path variance. Two cold sessions may route the same
question differently once their conversational contexts differ. Mitigation:
identical question + identical context replays the cached path; reproducibility
formally attaches to the recorded path, which is what provenance has always
promised. "Same question, same answer" holds within a tenant's working reality.

### Budgets scale with depth, not caps on intelligence

Iteration budgets scale with the question's composition depth. A budget
exhausted before an answerable question resolves is a defect class, not an
acceptable outcome — the completeness bar treats it as a scored failure.

## Two consumers, one architecture

**Generalized deep research (ships first).** The research planner accepts any
research question, orients with discovery, consults pack knowledge to decide
what deserves checking, plans angles across every domain the question touches,
executes, *reads its own results and iterates* — chasing significant contrasts
inward, dropping dead angles, respending budget — then synthesizes under
evidence tiers (measured / not-estimable; priors never blended). The v1 closed
recovery grammar survives as the recovery domain's angle library. Deep research
is the agentic loop's natural home: minutes-long, quality-first, plan-confirmed
before spending, with the run surface already built.

**Chat-turn agentic mode (gated experiment).** The same loop bounded for
conversational latency, behind the internal settings profile beside strict
compile. It promotes to default only by winning its A/B.

## Promotion criteria (the A/B)

Judged on the conversational-flexibility corpus plus an answerability oracle
(for each question, an independent determination of whether a certified path
exists):

- **Path-found rate over path-exists questions** — refusal or needless
  clarification on an answerable question is a scored failure.
- **Zero wrong-reading regressions** — a confidently wrong answer is worse than
  any clarification; any increase fails the candidate outright.
- Honesty surface intact: bounds, premise verdicts, suppression, refusal
  quality — full parity required.
- Cost and latency reported, not gating for deep research; gating for the
  chat-turn mode per its latency budget.

Terminal judgment at a Fable gate, per the model-economics policy.

## What this retires, eventually

Playbooks remain governed content: authored angle libraries, materiality
policies, and domain knowledge. What the loop retires is the playbook as a
*rigid execution shape* — the dead-ends where an authored route hits an
unimplemented step and refuses instead of routing around. Under the completeness
bar, an authored route is a head start, never a wall.
