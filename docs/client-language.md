# Client language — the words Revi says out loud

This is the product's language contract. It governs **every string a client can
read**: narrative prose, warnings, clarifications, error copy, monitor tiles and
briefs, definition cards, chart annotations, labels, and the model descriptions
that ship in `contracts/openapi.json`.

It does **not** govern internal identifiers, traces, `debug=true` output, the
Evidence rail's raw records, or exports. Those keep full fidelity forever —
[truth relocates, never deletes](../AGENTS.md). This document is only about the
**default surface**: what a first-time reader sees without clicking anything.

The reader we write for is a career RCM analyst. They have twenty years of
revenue-cycle vocabulary and zero of ours. They know what a CARC is; they do not
know what a pack is. Every rule below follows from that one asymmetry.

> **The bar.** A boomer RCM analyst clicking through for the first time
> understands what happened, without a glossary and without asking anyone.

---

## 1. KEEP — the analyst's own vocabulary

This is RCM-native language the reader owns. Using the plain-English
"simplification" of these words makes the product *less* clear, not more, and
signals we don't know the domain. Never soften them.

| Keep | Never "simplify" to |
|---|---|
| denial, denied, denied dollars | rejected charges |
| remit, remittance | payment file |
| payer, plan, product type, financial class | insurer, insurance type |
| adjudication, adjudicated, unadjudicated | processing / processed |
| COB (coordination of benefits) | secondary insurance handling |
| CARC, RARC, group code, denial category | reason code |
| filing deadline, timely filing, filing runway | submission cutoff |
| A/R, aging, over-90, over-120, days in A/R | outstanding balances |
| clean claim, first-pass yield | error-free claim |
| underpayment, variance, expected reimbursement | short payment |
| write-off, contractual adjustment, allowance | reduction |
| appeal, overturn, overturned | dispute, reversal |
| DNFB, unbilled, discharged not final billed | *(expand once, then keep)* |
| charge lag, bill lag, service date, submission date, posting date | — |
| credit balance, refund, patient responsibility | — |

**Rule.** If the term appears in an ANSI 835/837 companion guide, an HFMA
glossary, or on a remittance advice, it is KEEP. Spell out an initialism the
first time on a surface that has room (`DNFB (discharged not final billed)`),
then use it plainly.

---

## 2. TRANSLATE — platform-native → the one client rendering

These are real concepts the reader needs, wearing names only we understand. Each
gets **exactly one** client rendering, used identically everywhere. Do not invent
a second synonym: two words for one thing is how a reader concludes they are two
things.

| Platform-native | The ONE client rendering |
|---|---|
| pack, the pack, pack content | **your definitions library** |
| governed (as a modifier: governed contract, governed content) | **standard** — *but see §2.1, which bans it in the defaults position* |
| governed pack / base-rcm@1.0.0 cited as a source | **Standard definition — from your definitions library** |
| cohort (a pinned population) | **population** |
| watermark, `wm_003` | **data load** ("this load", "the Aug 1 load") |
| warehouse, the warehouse | **your data** |
| probe, probes | **check, checks** |
| certified value / uncertified | **measured value** / **not measured here** |
| certified dimension / uncertified dimension | **standard** / **not standardized here** |
| evidence frame | **evidence** |
| grade `direct` | **measured directly** |
| grade `derived` | **calculated from measured values** |
| grade `proxy` | **estimated** |
| grade `discovery` | **exploratory** |
| grade `unavailable` | **not measured** |
| turn (the unit of conversation) | see §3 — this is NEVER-SAY, not TRANSLATE |
| spec / typed spec / stored spec | **what this monitor measures** (describe it; don't name the object) |
| materiality gate / threshold | see §2.1 — state the rule, not the authority |

**Honesty survives translation exactly.** A translation that loses a bound, a
refusal, or a qualification is a bug, not a simplification.

> ✅ "Gated by the governed pack" → "Held back by your organization's materiality
> rules"
> ❌ "Gated by the governed pack" → "Not shown"

The second is shorter and *wrong*: it drops who decided and why, which is the
whole content of the sentence.

### 2.1 Defaults show their rule, not a name

**This is the load-bearing rule of this document.** It generalizes past
thresholds to every place the platform applies a default on the reader's behalf.

The cautionary tale, verbatim from the field:

> "use the pack's threshold"
> "when it moves more than the governed threshold for this measure"

This is nonsense that *sounds* like governance. It asks the reader to accept — or
choose — a number while describing it in words they cannot evaluate. There is no
pack in their world, "governed" asserts an authority they cannot inspect, and the
sentence never says what the number **is**.

A client-facing default is stated as **three things, always**:

1. **the concrete rule, with value and unit** — "when it moves more than 0.5
   percentage points";
2. **attributed to a named recommender** — "Revi's recommended level for denial
   rates";
3. **with changeability stated** — "You can change this anytime."

> ✅ "I'll brief you when it moves more than 0.5 percentage points — Revi's
> recommended level for denial rates. You can change this anytime."
> ❌ "I'll brief you when it moves more than the standard threshold."
> ❌ "…more than the governed threshold for this measure."
> ❌ "…more than the default threshold."

**Banned in the defaults position: `standard`, `governed`, `default`, `policy`,
`the pack's`** — every adjective that asserts authority the reader cannot
inspect. (`standard` remains correct elsewhere, e.g. "Standard definition" on a
definition card, where it describes a *shared, inspectable* definition rather
than substituting for a number.)

**Corollary for payloads.** If a composition site cannot state the number because
the payload does not carry it, **extend the payload** (additively). The number
reaching the reader is the point; a sentence that gestures at a threshold it
cannot name has failed regardless of how well it is worded.

**Where a gate withheld something**, name the rule and the number, and say who
can change it — never "below the governed gate":

> ✅ "The rate moved 0.2 points — under the 0.5-point level Revi recommends for
> rates, so it is counted here but not briefed."

---

## 3. NEVER-SAY — plumbing that gets no client noun

These have **no client rendering at all**. They are not translated; the sentence
is rewritten so the concept never needs naming. They live in Evidence, the trace,
`debug=true`, and exports — all of which keep them verbatim.

| Never say | Why | Write instead |
|---|---|---|
| **turn** | chat-industry vocabulary, not analyst vocabulary | **your question** / **the question** (user side); **the answer** / **this answer** (system side); **the conversation** (the thread) |
| **playbook** | our routing internals | name what it did: "a full A/R aging review" |
| **spec**, typed spec, stored spec | our object model | "what this monitor measures" |
| **plan**, plan hash | our compiler | — (omit; it explains nothing to the reader) |
| **frame**, evidence frame, frame id | our storage unit | "evidence" |
| **recipe**, recipe id | our chart config | "the standard chart for this" / omit |
| **probe** | see §2 — translate to **check** where the count matters, omit otherwise | |
| **operator names** (`drill_into`, `set_dimensions`, `add_filter`, …) | schema tokens | the English: "drilling in", "changing the breakdown", "narrowing the scope" |
| **any snake_case identifier** (`denied_dollars`, `group_code`, `claim_line`, `money_cents`, `prior_period`) | internal ids | the display name from `packs/base-rcm/metric_display.yaml`, or the dimension's label |
| **any ALL_CAPS enum token** (`RECONCILIATION_FAILED`, `UNSUPPORTED_CONCEPT`, `POPULATION_CAVEAT`) | branch handles | the sentence itself; the code rides in `warnings_v2[].code` where clients branch on it |
| **version pins** (`base-rcm@1.0.0`, `anomaly_priority@3`, `wm_003`, `v2`) | provenance | "your definitions library", "this load", "the Aug 1 load" |
| **confidence numbers** ("confidence 0.78") | a fact about our internals | omit — the reader is never asked to weigh a probability mid-investigation |
| **machine key/value pairs** (`status=not_applicable`, `options_dropped=2`) | log format | a sentence |
| **entity**, **grain**, **basis** *as bare tokens* | modeling words | "claim-level", "line-level"; "on the service date" |
| **schema words**: contract, binding, predicate, cardinality, adapter, port | our architecture | describe the effect |
| **pack**, **cohort**, **watermark**, **certified** | translated, never raw — see §2 | |

### Legitimate English collisions

These words are fine in ordinary English and are **not** violations. The
enforcement test allowlists them narrowly:

- **"planned"**, **"planning"** — "planning defaults", "a planned discharge".
- **"specific"**, **"specify"**, **"specified"** — contains `spec` but is not it.
- **"framework"**, **"timeframe"**, **"frame of reference"** — contains `frame`.
- **"packing slip"**, **"package"** — contains `pack`.
- **"turnaround"**, **"return"**, **"overturn"**, **"overturned"** —
  contain `turn`; **"overturn" is KEEP vocabulary** (appeals).
- **"probe"** in a clinical sense — does not arise here.
- **"in turn"** — banned anyway; it reads as our jargon to a reader primed by the
  rest of the surface. Rewrite.

The test matches on **word boundaries, case-insensitively**, so `overturned` and
`turnaround` do not trip the `turn` rule, but `this turn` and `the turn` do.

---

## 4. Register — how the sentence is shaped

These rules come from the product's own history and are as binding as the word
lists.

**Sentence case.** Headlines, tile titles, labels, buttons: "Denied dollars by
payer", not "Denied Dollars By Payer". Only proper nouns capitalize.

**Marks on the data, notes below it.** A qualification belongs on the figure it
qualifies — a ≤, a dashed mark, a hollow bar — with the explaining sentence
*below* the data, not in front of it. The warning register (amber, loud) is
reserved for **verdict-class content only**: premise corrections, refusals,
regressions, "nothing is being monitored". A clarification offering options is
not a warning.

**Efficiency: a sentence that repeats a mark dies.** If the figure already renders
as `≤ $176,112`, the prose does not also say "this is an upper bound". The mark
carries it. Prose exists for what the marks *cannot* say.

> **Intuitive, efficient prose > excessive detail, technical terms.**

**One fact per sentence.** Two facts joined by a semicolon are two sentences the
reader has to hold at once.

**Name the actor.** "Revi re-ran your monitors" beats "the monitors were
re-evaluated". Passive voice hides who decided, which is exactly what an honesty
surface must not do.

**First person for the platform's own choices.** "I used July because the question
named no period" — the platform owns its assumptions out loud. Not "the window
was assumed".

**No parenthetical plurals.** "3 sentences" or "1 sentence", never "1
sentence(s)". Compute the plural.

**Numbers are formatted, not raw.** `$176,112.25`, `29.5%`, `0.5 percentage
points` — never `17611225`, `0.295082`, or `money_cents`.

**Dates are readable.** "the Aug 1 load", "July 2026", "Jul 1 – Aug 2, 2026" —
never `2026-07-01..2026-07-31` on a default surface. ISO ranges belong in
Evidence.

---

## 5. Applying this

**When you write a client-visible string:**

1. Is every noun in it KEEP vocabulary or plain English? If not, translate (§2)
   or rewrite so the concept needs no name (§3).
2. Does it state a default or a threshold? Then it states the **number, the unit,
   the recommender, and that it can be changed** (§2.1).
3. Does it repeat a mark already on the data? Delete that clause (§4).
4. Did the meaning survive exactly? A translation that drops a bound or a
   refusal is a bug.

**When you add a new warning or error:** put the code in the ALL_CAPS/snake_case
prefix (clients branch on it, and `warning_codes.py` strips it), and write the
message for the reader. The prefix is machine-facing; everything after the colon
is not.

**The enforcement test** lives at
`apps/api/tests/test_client_language_guard.py`. It walks every server-composed
client-visible string source and fails on a NEVER-SAY term or an untranslated
TRANSLATE term. It is cheap, total, and runs in `make lint`'s character: if you
are fighting it, the copy is wrong, not the test. Add an allowlist entry only for
a genuine English collision (§3), with a comment saying which word and why.

**The web half** (static UI labels, tooltips, chips, settings copy) is governed by
this same document. Server-composed strings and web-composed strings must use the
*same* rendering for the same concept — that is the point of there being exactly
one.
