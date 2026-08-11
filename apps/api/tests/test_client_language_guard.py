"""The client-language guard: no platform vocabulary on a client surface.

``docs/client-language.md`` is the contract. This is the machinery that
makes it stick, and it has the same character as the naming guard in
``make lint``: cheap, total, and impossible to ignore.

WHAT IT CHECKS. Every string a client can read that this repo COMPOSES
server-side — §12 error copy, clarification copy, monitor threshold and
materiality sentences, the definition card's citation, the effective-context
header, pack prose that reaches a definition card, and the model
descriptions exported into ``contracts/openapi.json``. Each source is
registered in :func:`client_strings` and yields ``(where, text)`` pairs.

HOW IT CHECKS. Two families of banned pattern:

* **NEVER-SAY** — plumbing that gets no client noun at all (``spec``,
  ``frame``, ``recipe``, ``playbook``, ``turn``, snake_case ids, version
  pins, confidence numbers).
* **UNTRANSLATED TRANSLATE** — a real concept still wearing our name
  (``pack``, ``cohort``, ``watermark``, ``probe``, ``governed``,
  ``certified``, ``warehouse``).

Matching is word-boundary and case-insensitive, so ``overturned``,
``turnaround``, ``package`` and ``specific`` do not trip the ``turn``,
``pack`` or ``spec`` rules.

TWO THINGS ARE DELIBERATELY NOT CHECKED.

* **Machine prefixes.** A warning sentence opens with the code clients
  branch on (``findings_truncated: …``) and ``warning_codes.py``
  regex-matches it. :func:`_strip_machine_prefix` removes it before the
  scan, so the prefix stays byte-identical and the sentence after it is
  held to the contract.
* **``plan``.** It is KEEP vocabulary — a payer's plan — *and* a
  NEVER-SAY word for our compiler's plan. One spelling, two meanings, so no
  word-boundary rule can tell them apart. It is on the honour system;
  ``docs/client-language.md`` §3 says so.

ADDING A SOURCE. Append a collector to :func:`client_strings`. If a
composition site cannot be called cheaply, read its string literals with
:func:`literals_of` instead of skipping it — an unwalked source is how the
vocabulary comes back.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_ROOT = REPO_ROOT / "packs" / "base-rcm"
OPENAPI = REPO_ROOT / "contracts" / "openapi.json"


# --------------------------------------------------------------------------
# The banned vocabulary. Each entry is (label, compiled pattern).

#: Word-boundary, case-insensitive. ``\b`` is what keeps ``overturned``,
#: ``turnaround``, ``package`` and ``specific`` out of the results.
BANNED_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("playbook", re.compile(r"\bplaybooks?\b", re.I)),
    ("spec", re.compile(r"\bspecs?\b", re.I)),
    ("frame", re.compile(r"\bframes?\b", re.I)),
    ("recipe", re.compile(r"\brecipes?\b", re.I)),
    ("turn", re.compile(r"\bturns?\b", re.I)),
    ("grain", re.compile(r"\bgrains?\b", re.I)),
    ("pack", re.compile(r"\bpacks?\b", re.I)),
    ("cohort", re.compile(r"\bcohorts?\b", re.I)),
    ("watermark", re.compile(r"\bwatermarks?\b", re.I)),
    ("probe", re.compile(r"\bprobes?\b", re.I)),
    ("governed", re.compile(r"\bgoverned\b", re.I)),
    ("certified", re.compile(r"\bun-?certified\b|\bcertified\b", re.I)),
    ("warehouse", re.compile(r"\bwarehouses?\b", re.I)),
)

#: Shapes rather than words.
BANNED_SHAPES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # A watermark id, in the only spelling it has.
    ("watermark id", re.compile(r"\bwm_\d+\b")),
    # A snake_case governed identifier: metric id, dimension id, column.
    ("snake_case identifier", re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")),
    # A version pin: base-rcm@1.0.0, anomaly_priority@3.
    ("version pin", re.compile(r"\b[a-z][a-z0-9-]*@\d+(?:\.\d+)*\b", re.I)),
    # A model's numeric confidence.
    ("confidence number", re.compile(r"\bconfidence\s+\d*\.\d+", re.I)),
    # A machine key/value pair: status=not_applicable, options_dropped=2.
    ("machine key=value", re.compile(r"\b[a-z_]{3,}=\S+", re.I)),
)

#: Legitimate English collisions and machine handles that survive on
#: purpose. Keep this list SHORT and each entry commented — every addition
#: is a hole in the guard.
ALLOWLIST: tuple[re.Pattern[str], ...] = (
    # `{placeholder}` fields in a template are filled before a reader sees
    # them; the brace form is never itself rendered.
    re.compile(r"\{[^}]*\}"),
    # Referent handles (F1, T2) and anomaly ids (ANM-021) are LABELS the
    # reader is meant to quote back, not internal ids.
    re.compile(r"\b[FT]\d+\b"),
    re.compile(r"\bANM-\d+\b"),
    # RCM-native initialisms that happen to be all-caps.
    re.compile(r"\b(?:CARC|RARC|COB|DNFB|HMO|PPO|A/R|MCO|EOB|NPI)\b"),
    # "e.g." / "i.e." contain no ban but do contain dots that confuse the
    # key=value shape when they precede an equals sign in prose.
    re.compile(r"\b(?:e\.g\.|i\.e\.)"),
    # --- genuine regulatory English, in the knowledge cards -------------
    # "Certified IDR entity" is the STATUTORY name of the arbiter under the
    # No Surprises Act (45 CFR 149.510). It is a term of art an RCM analyst
    # reads on a federal notice, not this repo's `certified` grade, and
    # renaming it would misquote the regulation.
    re.compile(r"\bCertified IDR entit(?:y|ies)\b"),
    # "governed by this regulation", "governed by state contracts",
    # "ACA-governed plans" — the ordinary English verb, describing a law
    # that actually governs something. §2.1 bans `governed` as a stand-in
    # for an authority the reader cannot inspect ("the governed
    # threshold"), which is a different sentence shape entirely: nothing
    # here is redacted from "gated by the governed pack".
    re.compile(r"\bgoverned by\b", re.I),
    re.compile(r"\b(?:ACA|ERISA|CMS|HIPAA)-governed\b"),
)

#: A warning's leading machine code, which clients branch on and
#: ``warning_codes.py`` matches. Stripped before the scan; see the module
#: docstring.
_MACHINE_PREFIX = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*:\s*")


def _strip_machine_prefix(text: str) -> str:
    return _MACHINE_PREFIX.sub("", text, count=1)


def is_copy(text: str) -> bool:
    """Is this string a sentence a reader sees, or a value they branch on?

    Copy has whitespace. A bare single token — ``ratio_points``,
    ``warnings_v2``, ``governed_default`` — is a wire enum value or a
    payload field name: renaming one is a breaking contract change, not a
    translation, and a client renders it through the §2 table rather than
    printing it. This is the only rule separating the two, so it is stated
    once and used by every collector that reads raw literals.
    """
    stripped = text.strip()
    return len(stripped) >= 4 and any(ch.isspace() for ch in stripped)


def _redact_allowed(text: str) -> str:
    """Blank out the spans the allowlist protects, so bans cannot see them."""
    for pattern in ALLOWLIST:
        text = pattern.sub(" ", text)
    return text


def violations(text: str) -> list[str]:
    """Every contract violation in one client-visible string."""
    scanned = _redact_allowed(_strip_machine_prefix(text))
    found: list[str] = []
    for label, pattern in BANNED_WORDS:
        if pattern.search(scanned):
            found.append(label)
    for label, pattern in BANNED_SHAPES:
        match = pattern.search(scanned)
        if match is not None:
            found.append(f"{label} ({match.group(0)!r})")
    return found


# --------------------------------------------------------------------------
# Reading string literals out of a composition site that is awkward to call.


def literals_of(path: Path, function: str) -> Iterator[str]:
    """Every string literal inside one function, minus its docstring."""
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if node.name != function:
            continue
        body = node.body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            body = body[1:]
        for stmt in body:
            for inner in ast.walk(stmt):
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    yield inner.value


# --------------------------------------------------------------------------
# The registry. Every server-composed client-visible string source.


def _error_copy_strings() -> Iterator[tuple[str, str]]:
    from revi_api import error_copy

    for code, message in error_copy.PLAIN_MESSAGES.items():
        yield f"error_copy.PLAIN_MESSAGES[{code.value}]", message
    for sub, message in error_copy._SUBCODE_MESSAGES.items():
        yield f"error_copy._SUBCODE_MESSAGES[{sub}]", message
    yield (
        "error_copy.CLARIFICATION_OPTIONS_OFFERED_WARNING",
        error_copy.CLARIFICATION_OPTIONS_OFFERED_WARNING,
    )
    yield (
        "error_copy.CLARIFICATION_NO_OPTIONS_WARNING",
        error_copy.CLARIFICATION_NO_OPTIONS_WARNING,
    )
    for operator, phrase in error_copy._OPERATOR_PHRASES.items():
        yield f"error_copy._OPERATOR_PHRASES[{operator}]", phrase
    # Over representative reasons: the cleaner must not PUBLISH vocabulary
    # it was written to remove.
    for reason in (
        "CLARIFICATION_SOLE_SURVIVOR: only one option survived the pack's filters",
        "referent resolution confidence 0.40; drill_into takes exactly one referent id",
        "CLARIFICATION_AMBIGUOUS_METRIC: two metrics match; options_dropped=2",
    ):
        cleaned = error_copy.clarification_reason_copy(reason)
        if cleaned:
            yield f"error_copy.clarification_reason_copy({reason[:32]!r}…)", cleaned


def _monitor_threshold_strings() -> Iterator[tuple[str, str]]:
    from revi_api import monitor_intent
    from revi_api.monitors import pins
    from revi_api.monitors_policy import (
        MaterialityPolicy,
        UnitThreshold,
        recommended_gate_sentence,
        recommended_gate_text,
    )

    for unit, phrases in monitor_intent.LEGAL_THRESHOLD_PHRASES.items():
        for phrase in phrases:
            yield f"monitor_intent.LEGAL_THRESHOLD_PHRASES[{unit}]", phrase
    for phrase in monitor_intent.GENERIC_THRESHOLD_PHRASES:
        yield "monitor_intent.GENERIC_THRESHOLD_PHRASES", phrase

    policy = MaterialityPolicy(
        unit_kinds={
            "ratio": UnitThreshold(min_points=Decimal("0.005")),
            "money_cents": UnitThreshold(
                min_relative=Decimal("0.1"), min_absolute=Decimal("500000")
            ),
            "days": UnitThreshold(min_absolute=Decimal("2")),
            "count": UnitThreshold(min_relative=Decimal("0.1"), min_absolute=Decimal("10")),
        }
    )
    for unit in ("ratio", "money_cents", "days", "count"):
        yield f"recommended_gate_text[{unit}]", recommended_gate_text(unit, policy)
        yield f"recommended_gate_sentence[{unit}]", recommended_gate_sentence(unit, policy)
        # The sentence the analyst is shown when a monitor is created.
        yield (
            f"pins._threshold_statement[{unit}]",
            pins._threshold_statement(None, unit, recommended_gate_sentence(unit, policy)),
        )


def _materiality_note_strings() -> Iterator[tuple[str, str]]:
    """The gate verdicts — the owner's hot spot, exercised over real inputs."""
    from revi_api.monitors_policy import (
        MaterialityPolicy,
        UnitThreshold,
        assess_movement,
    )

    policy = MaterialityPolicy(
        unit_kinds={
            "ratio": UnitThreshold(min_points=Decimal("0.005")),
            "money_cents": UnitThreshold(
                min_relative=Decimal("0.1"), min_absolute=Decimal("500000")
            ),
            "days": UnitThreshold(min_absolute=Decimal("2")),
        }
    )
    cases = (
        ("ratio", Decimal("0.29"), Decimal("0.20")),  # material
        ("ratio", Decimal("0.201"), Decimal("0.20")),  # immaterial
        ("money_cents", Decimal("2000000"), Decimal("1000000")),
        ("money_cents", Decimal("1000100"), Decimal("1000000")),
        ("days", Decimal("40"), Decimal("30")),
        ("days", Decimal("30.5"), Decimal("30")),
        ("percent_of_something_unknown", Decimal("2"), Decimal("1")),  # ungated
    )
    for unit, current, prior in cases:
        verdict = assess_movement(
            unit=unit, current=current, prior=prior, monitor=None, policy=policy
        )
        yield f"assess_movement[{unit} {prior}->{current}].note", verdict.note


def _composition_literal_strings() -> Iterator[tuple[str, str]]:
    """Sites that are awkward to call, read as literals instead."""
    sites = _COMPOSITION_SITES
    for path, function in sites:
        if not path.exists():  # pragma: no cover - defensive
            continue
        for text in literals_of(path, function):
            # Format specifiers, lone punctuation and wire enum values are
            # not sentences — see :func:`is_copy`.
            if not is_copy(text) or text.strip().startswith(("%", "{")):
                continue
            yield f"{path.name}::{function}", text


#: Every composition site read literal-by-literal. Named rather than inline
#: so ``test_every_site_that_writes_a_clarification_question_is_walked`` can
#: assert against the REGISTRY: a composer whose copy is entirely f-string
#: fragments yields no sentence, and a coverage test that checked the yield
#: would call it unregistered and be un-satisfiable.
_COMPOSITION_SITES: tuple[tuple[Path, str], ...] = (
        (
            REPO_ROOT
            / "packages/investigation-contracts/src/revi_investigation_contracts/header.py",
            "build_header_payload",
        ),
        (REPO_ROOT / "apps/api/src/revi_api/monitors_policy.py", "_pack_gate"),
        (REPO_ROOT / "apps/api/src/revi_api/monitors/common.py", "_monitors_warnings"),
        (REPO_ROOT / "apps/api/src/revi_api/service.py", "_monitor_refused"),
        # EVERY SITE THAT COMPOSES A CLARIFICATION QUESTION.
        #
        # `ClarificationRequest` is one object split between a guarded
        # field and an unguarded one: `reason` goes through
        # `clarification_reason_copy`, which this file already exercises,
        # and `question` reaches the reader byte for byte from
        # `assembly.py` and the SSE frame alike. The field with no runtime
        # filter was also the field with no test-time collector, and the
        # sentence a reader saw when their question dead-ended said
        # "survives what this data holds at this watermark".
        #
        # These are methods on stateful services that need a session, a
        # pack port and a warehouse to call — exactly the "awkward to call"
        # shape `literals_of` exists for, and exactly the shape nobody had
        # registered.
        *(
            (
                REPO_ROOT
                / "packages/investigation/src/revi_investigation/application/submit_turn"
                / "clarification.py",
                name,
            )
            for name in ("_state_the_survivor", "_no_replay", "drop_refuted_options")
        ),
        *(
            (
                REPO_ROOT
                / "packages/investigation/src/revi_investigation/application/submit_turn"
                / "clarifying.py",
                name,
            )
            for name in (
                "_validated_options",
                "_bounded_clarification",
                "_budget_stop",
                "_pending_clarification",
            )
        ),
        *(
            (
                REPO_ROOT
                / "packages/investigation/src/revi_investigation/application/validation.py",
                name,
            )
            for name in (
                "_playbook_transform_alternative",
                "_basis_alternative",
                "_grain_alternative",
                "_near_miss_dimension",
                "_near_miss_metric",
                "_value_clarification",
            )
        ),
        # …and the other three composition sites the clarification hunt
        # turned up, each writing client copy from a file this registry
        # already knew about under a different function name.
        (REPO_ROOT / "apps/api/src/revi_api/portfolio.py", "build_portfolio"),
        (REPO_ROOT / "apps/api/src/revi_api/service.py", "_anomaly_reconciliation"),
        (REPO_ROOT / "apps/api/src/revi_api/assembly.py", "_restoration_notes"),
    )


def _definitional_strings() -> Iterator[tuple[str, str]]:
    from revi_api.assembly import DEFINITION_SOURCE

    yield "assembly.DEFINITION_SOURCE", DEFINITION_SOURCE


def _metric_display_strings() -> Iterator[tuple[str, str]]:
    path = PACK_ROOT / "metric_display.yaml"
    document = yaml.safe_load(path.read_text()) or {}
    for metric_id, entry in (document.get("metrics") or {}).items():
        for field in ("display_name", "caveat"):
            value = entry.get(field)
            if isinstance(value, str):
                yield f"metric_display.yaml::{metric_id}.{field}", value


def _pack_prose_strings() -> Iterator[tuple[str, str]]:
    """Pack prose that reaches a definition card verbatim."""
    for path in sorted((PACK_ROOT / "metrics").glob("*.yaml")):
        document = yaml.safe_load(path.read_text()) or {}
        for field in ("description", "definition_paraphrase"):
            value = document.get(field)
            if isinstance(value, str):
                yield f"metrics/{path.name}::{field}", value


# --------------------------------------------------------------------------
# The rest of the pack that reaches a reader.
#
# The metric contracts above were the first pack file walked here, and for a
# while the only one — which left ~220 other pack prose values, most of the
# words on a definition card, unguarded. Each collector below names the
# fields that reach a CLIENT surface and no others; the operator-only
# neighbours are listed in :func:`test_the_operator_only_pack_fields_are_a_
# deliberate_omission` so the line between them is visible rather than
# implied.


def _yaml(path: Path) -> dict[str, object]:
    if not path.exists():  # pragma: no cover - a pack without this artifact
        return {}
    document = yaml.safe_load(path.read_text())
    return document if isinstance(document, dict) else {}


def _strings(value: object) -> Iterator[str]:
    """A field that is one string, or a list of them."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                yield item


def _source_titles(entry: object) -> Iterator[str]:
    """A `sources:` block's titles — printed beside a definition as its
    citation. Publisher and URL stay verbatim and are not walked: an
    attribution is the source's own words, not ours."""
    if not isinstance(entry, dict):
        return
    for source in entry.get("sources") or ():
        if isinstance(source, dict):
            yield from _strings(source.get("title"))


def _deep_research_strings() -> Iterator[tuple[str, str]]:
    """Every sentence a deep-research report says out loud.

    The module holds the report's copy as constants and pure formatters, so
    both are walked: the constants directly, and the formatters INVOKED over
    representative figures. Invoking them is the point — a template that
    reads cleanly and interpolates a snake_case label or a raw ratio only
    fails once it is called.
    """
    from decimal import Decimal

    from revi_investigation.application.deep_research import copy as words

    for name in dir(words):
        if name.startswith("_"):
            continue
        value = getattr(words, name)
        if isinstance(value, str):
            yield f"deep_research.copy.{name}", value

    yield (
        "deep_research.copy.headline_statement",
        words.headline_statement(
            expected=116_766_888,
            low=87_405_242,
            high=151_869_369,
            open_dollars=574_949_512,
            open_denials=2865,
        ),
    )
    yield (
        "deep_research.copy.split_statement",
        words.split_statement(
            catchable=283_100_286, passed=291_849_226, unknown=1_000_000
        ),
    )
    yield (
        "deep_research.copy.unpriced_statement",
        words.unpriced_statement(
            unpriced=243_076_530, share=Decimal("0.4227"), populations=26
        ),
    )
    yield (
        "deep_research.copy.thin_rollup_statement",
        words.thin_rollup_statement(
            populations=6, denials=41, cents=8_871_252, floor=11
        ),
    )
    yield (
        "deep_research.copy.contrast_statement",
        words.contrast_statement(
            subject="Payer",
            strong_label="Northbridge Commercial",
            strong_rate=Decimal("0.5675"),
            strong_n=148,
            weak_label="Lakewood Medicaid MCO",
            weak_rate=Decimal("0.2905"),
            weak_n=117,
            difference=Decimal("0.2770"),
        ),
    )
    for p_value in (Decimal("0.0000066"), Decimal("0.004"), Decimal("0.03"), Decimal("0.4")):
        yield (
            f"deep_research.copy.separation_statement[{p_value}]",
            words.separation_statement(
                p_value=p_value, low=Decimal("0.158"), high=Decimal("0.384")
            ),
        )
    yield (
        "deep_research.copy.contrast_refused_statement",
        words.contrast_refused_statement(
            subject="Payer",
            floor_sentence=(
                "A rate is published only where at least 30 of these denials have a "
                "final answer from the payer — Revi's recommended level for recovery "
                "rates. You can change this anytime."
            ),
        ),
    )
    yield (
        "deep_research.copy.timeliness_statement",
        words.timeliness_statement(
            fast_band="0-14",
            fast_rate=Decimal("0.5435"),
            slow_band="61+",
            slow_rate=Decimal("0.1424"),
        ),
    )
    yield (
        "deep_research.copy.timeliness_implication",
        words.timeliness_implication(fast_band="0-14", drop=Decimal("0.4011")),
    )
    yield (
        "deep_research.copy.median_delay_statement",
        words.median_delay_statement(label="Coding", median=Decimal("9")),
    )
    yield (
        "deep_research.copy.deadline_statement",
        words.deadline_statement(
            within_rate=Decimal("0.4419"),
            within_n=2454,
            past_rate=Decimal("0.0379"),
            past_n=79,
        ),
    )
    yield (
        "deep_research.copy.zero_rate_bound_statement",
        words.zero_rate_bound_statement(high=Decimal("0.0896"), n=39),
    )
    yield (
        "deep_research.copy.pursuit_statement",
        words.pursuit_statement(label="Coding", rate=Decimal("0.768"), n=697),
    )
    yield (
        "deep_research.copy.angle_refused_statement",
        words.angle_refused_statement(
            title="Speed and what it is worth",
            reason="the content defines no delay bands to read a curve along",
        ),
    )
    for statement in words.censoring_statements(
        considered=5398,
        in_denominator=2533,
        open_undecided=212,
        not_pursued=2653,
        immature=583,
        data_edge="Aug 2, 2026",
    ):
        yield "deep_research.copy.censoring_statements", statement
    for kind, values in (
        ("all_open", ()),
        ("payer", ("Northbridge Commercial",)),
        ("recovery_class", ("CODING",)),
        ("facility", ("Northgate Regional Hospital",)),
    ):
        yield (
            f"deep_research.copy.population_label[{kind}]",
            words.population_label(kind, values),
        )
    yield (
        "deep_research.copy.data_load_label",
        words.data_load_label("Aug 2, 2026"),
    )
    yield (
        "deep_research.copy.header_display",
        words.header_display(
            population="every open denial",
            floor_sentence=(
                "A rate is published only where at least 30 of these denials have a "
                "final answer from the payer — Revi's recommended level for recovery "
                "rates. You can change this anytime."
            ),
            load="the load through Aug 2, 2026",
        ),
    )


def _deep_research_content_strings() -> Iterator[tuple[str, str]]:
    """The governed deep-research content a reader sees: titles, progress
    lines, population labels and the sentences the report reuses."""
    document = _yaml(PACK_ROOT / "deep_research.yaml")
    estimation = document.get("estimation") or {}
    for field in ("min_cohort_label", "min_cohort_recommender"):
        for text in _strings(estimation.get(field)):
            yield f"deep_research.yaml::estimation.{field}", text
    for text in _strings((document.get("population") or {}).get("description")):
        yield "deep_research.yaml::population.description", text
    for group in ("stratifier_labels", "class_context", "copy"):
        for key, value in (document.get(group) or {}).items():
            for text in _strings(value):
                yield f"deep_research.yaml::{group}.{key}", text
    for stratifier, values in (document.get("value_labels") or {}).items():
        for key, value in (values or {}).items():
            for text in _strings(value):
                yield f"deep_research.yaml::value_labels.{stratifier}.{key}", text
    for name, node in (document.get("angles") or {}).items():
        if not isinstance(node, dict):
            continue
        for field in ("title", "progress", "purpose"):
            for text in _strings(node.get(field)):
                yield f"deep_research.yaml::angles.{name}.{field}", text


def _pack_concept_strings() -> Iterator[tuple[str, str]]:
    """concepts.yaml — the concept lookup ("what is COB?").

    ``name`` titles the definition card and ``definition`` is its body;
    both travel through ``PackSnapshotPort.resolve_term``. ``description``
    is NOT here: no surface reads it (see the omissions test).
    """
    for entry in _yaml(PACK_ROOT / "concepts.yaml").get("concepts") or ():
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id", "?")
        for field in ("name", "definition"):
            for text in _strings(entry.get(field)):
                yield f"concepts.yaml::{cid}.{field}", text
        for title in _source_titles(entry):
            yield f"concepts.yaml::{cid}.sources[].title", title


def _pack_code_strings() -> Iterator[tuple[str, str]]:
    """codes.yaml — the group-code and CARC panels on a definition card."""
    for entry in _yaml(PACK_ROOT / "codes.yaml").get("codes") or ():
        if not isinstance(entry, dict):
            continue
        code = entry.get("code", "?")
        for field in ("title", "definition_paraphrase"):
            for text in _strings(entry.get(field)):
                yield f"codes.yaml::{code}.{field}", text
        for title in _source_titles(entry):
            yield f"codes.yaml::{code}.sources[].title", title


def _pack_knowledge_strings() -> Iterator[tuple[str, str]]:
    """knowledge.yaml — the card body.

    ``summary`` is what ``resolve_term`` serves today. ``key_points`` and
    ``cautions`` are the same authored card and ship the moment one is
    rendered in full, so they are held to the same bar: splitting a card's
    body between a guarded field and unguarded ones is how the vocabulary
    comes back.
    """
    for entry in _yaml(PACK_ROOT / "knowledge.yaml").get("knowledge_cards") or ():
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id", "?")
        for field in ("title", "summary", "key_points", "cautions"):
            for text in _strings(entry.get(field)):
                yield f"knowledge.yaml::{cid}.{field}", text
        for title in _source_titles(entry):
            yield f"knowledge.yaml::{cid}.sources[].title", title


def _pack_benchmark_strings() -> Iterator[tuple[str, str]]:
    """benchmarks.yaml — every field ``BenchmarkPayload`` puts on the wire.

    A benchmark quoted without its population, period and authority is a
    different claim from the one its source made, so all three ride along
    and all three are read. ``authority`` here is the SOURCE's description
    of itself ("national provider survey"), which is why it is walked as
    prose rather than treated as an enum.
    """
    for entry in _yaml(PACK_ROOT / "benchmarks.yaml").get("benchmarks") or ():
        if not isinstance(entry, dict):
            continue
        bid = entry.get("id", "?")
        for field in ("cohort_label", "unit", "period", "authority", "cautions"):
            for text in _strings(entry.get(field)):
                yield f"benchmarks.yaml::{bid}.{field}", text
        for title in _source_titles(entry):
            yield f"benchmarks.yaml::{bid}.sources[].title", title


def _pack_playbook_strings() -> Iterator[tuple[str, str]]:
    """playbooks/*.yaml — the prose behind a routed answer.

    ``description`` is clipped into the interpretation model's vocabulary
    and travels on ``PlaybookSpec``; ``triggers`` are the pack author's own
    phrasings, shown to the model beside it; ``scope_note`` becomes a
    probe node's stated purpose and reaches the reader through evidence.
    """
    for path in sorted((PACK_ROOT / "playbooks").glob("*.yaml")):
        document = _yaml(path)
        for field in ("description", "triggers"):
            for text in _strings(document.get(field)):
                yield f"playbooks/{path.name}::{field}", text
        for probe in document.get("probes") or ():
            if isinstance(probe, dict):
                for text in _strings(probe.get("scope_note")):
                    yield f"playbooks/{path.name}::{probe.get('id', '?')}.scope_note", text


def _pack_actionability_strings() -> Iterator[tuple[str, str]]:
    """anomaly_actionability.yaml — the recoverability rationales.

    Every one of these is published verbatim: on a worklist card as
    ``actionability_rationale`` / ``drill_repoint_rationale``, and inside
    the turn-level repoint disclosure. The pack author's words are the
    point — a substitution stated in the platform's own words would be a
    second explanation of a decision the pack already made — which is
    exactly why they have to be the reader's words too.
    """
    document = _yaml(PACK_ROOT / "anomaly_actionability.yaml")
    for text in _strings((document.get("default") or {}).get("rationale")):
        yield "anomaly_actionability.yaml::default.rationale", text
    for block in ("categories", "drill_repoints", "drill_dimension_repoints"):
        entries = document.get(block)
        if not isinstance(entries, dict):
            continue
        for key, rule in entries.items():
            if isinstance(rule, dict):
                for text in _strings(rule.get("rationale")):
                    yield f"anomaly_actionability.yaml::{block}.{key}.rationale", text


def _pack_worklist_strings() -> Iterator[tuple[str, str]]:
    """worklist.yaml — the label and blurb on the ranked worklist itself."""
    document = _yaml(PACK_ROOT / "worklist.yaml")
    for field in ("label", "description"):
        for text in _strings(document.get(field)):
            yield f"worklist.yaml::{field}", text


def _openapi_client_strings() -> Iterator[tuple[str, str]]:
    """Wire fixture strings: schema DATA that can reach a screen.

    ``default`` and ``example`` values are copy — a client may render them
    straight into a field or an empty state — so they are held to the
    contract.

    Schema ``description`` text is NOT, and the exemption is deliberate.
    Those strings are this repo's model docstrings, and their audience is a
    developer integrating against ``contracts/openapi.json``: they exist to
    explain why a field is shaped the way it is, which requires naming the
    machinery. An analyst never sees them — no surface renders an OpenAPI
    description, and the web app's ``pnpm gen:types`` turns them into JSDoc
    comments read only in an editor. Translating them would make the
    integration surface worse to serve a reader who is not there. If that
    call is wrong, delete this docstring and this exemption together: 72 of
    the 208 descriptions currently name internals.
    """
    if not OPENAPI.exists():  # pragma: no cover - generated artifact
        return
    document = json.loads(OPENAPI.read_text())

    def walk(node: object, path: str) -> Iterator[tuple[str, str]]:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("default", "example") and isinstance(value, str):
                    if is_copy(value):
                        yield f"openapi.json::{path}.{key}", value
                elif key == "examples" and isinstance(value, list):
                    for index, item in enumerate(value):
                        if isinstance(item, str) and is_copy(item):
                            yield f"openapi.json::{path}.examples[{index}]", item
                elif key != "description":
                    yield from walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                yield from walk(value, f"{path}[{index}]")

    yield from walk(document, "$")


def client_strings() -> list[tuple[str, str]]:
    """Every server-composed client-visible string this repo can enumerate."""
    collectors = (
        _error_copy_strings,
        _monitor_threshold_strings,
        _materiality_note_strings,
        _composition_literal_strings,
        _definitional_strings,
        _metric_display_strings,
        _pack_prose_strings,
        _pack_concept_strings,
        _pack_code_strings,
        _pack_knowledge_strings,
        _pack_benchmark_strings,
        _pack_playbook_strings,
        _pack_actionability_strings,
        _pack_worklist_strings,
        _deep_research_strings,
        _deep_research_content_strings,
    )
    out: list[tuple[str, str]] = []
    for collector in collectors:
        out.extend((where, text) for where, text in collector() if text and text.strip())
    return out


# --------------------------------------------------------------------------
# The assertions.


def test_the_guard_has_something_to_guard() -> None:
    """A collector that silently yields nothing would pass this file vacuously."""
    strings = client_strings()
    assert len(strings) > 100, f"only {len(strings)} client strings collected"
    sources = {where.split("::")[0].split("[")[0] for where, _ in strings}
    assert len(sources) >= 6, f"only {sorted(sources)} sources walked"


def test_every_pack_file_that_reaches_a_reader_is_walked() -> None:
    """The pack half of the registry, named file by file.

    The metric contracts were guarded alone for a while, which read as "the
    pack is covered" while the concept dictionary, the code paraphrases, the
    knowledge cards, the benchmark context, the playbook prose and the
    worklist rationales — most of the words on a definition card — were not.
    Naming the files here makes an unwalked one a failure rather than an
    oversight.
    """
    walked = {where.split("::")[0].split("/")[0] for where, _ in client_strings()}
    for filename in (
        "concepts.yaml",
        "codes.yaml",
        "knowledge.yaml",
        "benchmarks.yaml",
        "metrics",
        "metric_display.yaml",
        "playbooks",
        "anomaly_actionability.yaml",
        "worklist.yaml",
    ):
        assert filename in walked, f"{filename} reaches a reader and nothing reads it here"


def test_every_site_that_writes_a_clarification_question_is_walked() -> None:
    """A clarification's QUESTION is client copy, and nothing filtered it.

    ``ClarificationRequest`` is one object split between a guarded field
    and an unguarded one. ``reason`` goes through
    ``clarification_reason_copy`` at the API boundary — a runtime filter
    this file already exercises — while ``question`` reaches the reader
    byte for byte from both ``assembly.py`` and the SSE frame. So the field
    with no runtime filter was also the field with no collector, and the
    sentence a reader saw when their question dead-ended said *"survives
    what this data holds at this watermark"*.

    Registering the four composition sites fixed the ones that exist. This
    test is what stops the fifth: it finds every function in the engine
    that assigns ``question=`` on a clarification and fails if the registry
    does not walk it. Grep-shaped on purpose — a new composer is a new
    function name, and the failure has to fire on a file nobody thought to
    add here.
    """
    walked = {f"{path.name}::{function}" for path, function in _COMPOSITION_SITES}
    sources = (
        REPO_ROOT
        / "packages/investigation/src/revi_investigation/application/submit_turn"
        / "clarification.py",
        REPO_ROOT
        / "packages/investigation/src/revi_investigation/application/submit_turn"
        / "clarifying.py",
        REPO_ROOT / "packages/investigation/src/revi_investigation/application/validation.py",
    )
    composers: set[tuple[str, str]] = set()
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.keyword) and inner.arg == "question":
                    composers.add((path.name, node.name))
    assert composers, "no clarification composer found — this test has stopped testing"
    for filename, function in sorted(composers):
        assert f"{filename}::{function}" in walked, (
            f"{filename}::{function} composes a clarification question and no collector "
            "reads it. Add it to _composition_literal_strings — an unwalked source is how "
            "the vocabulary comes back."
        )


def test_the_operator_only_pack_fields_are_a_deliberate_omission() -> None:
    """The pack fields this guard does NOT walk, and why — checked, not assumed.

    Each field below is pack prose written for whoever maintains the pack:
    it is loaded, and it stops at the domain model. Nothing serializes it
    onto a payload and no surface renders it, so holding it to the client
    contract would cost the maintainer their own vocabulary for no reader's
    benefit. The test is here because that argument is only true while the
    field stays unserialized — the day one of these is published, this
    fails and the field moves into a collector above.

    ``concepts[].description`` — a one-line gloss beside the definition;
    ``resolve_term`` sends ``name`` and ``definition``, never this.
    ``bindings[].rationale`` — why a concept maps to a field.
    ``conclusion_policies[].claim`` / ``ranking_policies[].description`` /
    ``detector_policies[].description`` — only ids, grades and thresholds
    are read off these.
    ``recipes[].notes`` — ``RecipeSpec`` carries it and nothing reads it.
    ``metric_display[].rationale`` — the authoring note recording why an
    entry exists, so a later reader can retire it; the client renders
    ``display_name`` and ``caveat`` only.
    """
    #: ``(pack field, the attribute access that would publish it)``. A pack
    #: artifact becomes a payload in the composition root and in the
    #: presentation layer; an access appearing in either is the moment the
    #: field stops being operator-only.
    operator_only = {
        "bindings[].rationale": "binding.rationale",
        "conclusion_policies[].claim": "policy.claim",
        "ranking_policies[].description": "ranking_policy.description",
        "recipes[].notes": "recipe.notes",
        "metric_display[].rationale": "entry.rationale",
    }
    consumers = "\n".join(
        path.read_text()
        for root in (
            REPO_ROOT / "apps/api/src/revi_api",
            REPO_ROOT / "packages/presentation/src",
            REPO_ROOT / "packages/investigation/src",
        )
        for path in sorted(root.rglob("*.py"))
    )
    leaked = [
        pack_field
        for pack_field, access in operator_only.items()
        if access in consumers
    ]
    assert not leaked, (
        f"{leaked} now reach a reader. Add each to a collector above and write "
        "it for the reader — see docs/client-language.md."
    )
    # ``concepts[].description`` needs a sharper look than a substring: the
    # definitional path reads ``.description`` off a METRIC contract in the
    # branch next door. Read the concept branch alone.
    adapters = (REPO_ROOT / "apps/api/src/revi_api/adapters.py").read_text()
    concept_branch = adapters.split("isinstance(match, Concept)")[1].split("elif")[0]
    assert ".description" not in concept_branch, (
        "concepts[].description now reaches a definition card; add it to "
        "_pack_concept_strings and hold it to docs/client-language.md."
    )


@pytest.mark.parametrize(
    ("where", "text"), [pytest.param(w, t, id=w[:80]) for w, t in client_strings()]
)
def test_client_strings_speak_the_client_s_language(where: str, text: str) -> None:
    found = violations(text)
    assert not found, (
        f"{where} publishes platform vocabulary {found} to a client.\n"
        f"  {text!r}\n"
        f"See docs/client-language.md for the rendering to use instead."
    )


def test_openapi_fixture_strings_speak_the_client_s_language() -> None:
    """The exported schema ships copy too — see :func:`_openapi_client_strings`.

    Kept separate from the parametrised sweep because it is a generated
    artifact: regenerate with ``make openapi`` after changing any model
    description or default.
    """
    strings = list(_openapi_client_strings())
    offenders = [(w, found, t) for w, t in strings if (found := violations(t))]
    assert not offenders, "\n".join(
        f"{where}: {found}\n  {text!r}" for where, found, text in offenders[:20]
    )


def test_the_openapi_description_exemption_is_visible_not_forgotten() -> None:
    """The one place this guard deliberately does not look, counted out loud.

    ``_openapi_client_strings`` skips schema ``description`` text because
    its audience is a developer integrating against the contract, not an
    analyst — see that function's docstring. An exemption nobody can see the
    size of is an exemption nobody revisits, so this pins the number: it
    fails when the exempted surface grows or shrinks materially, which is
    the moment to ask whether the call still holds.

    If the exemption is overturned, delete this test and the ``key !=
    "description"`` branch together.
    """
    document = json.loads(OPENAPI.read_text())
    described = [
        value
        for schema in document["components"]["schemas"].values()
        if isinstance(value := schema.get("description"), str)
    ]
    offenders = [text for text in described if violations(text)]
    assert len(described) > 40, "the contract stopped documenting its schemas"
    # Held loosely on purpose: this is a tripwire on the exemption's size,
    # not a target to drive to zero.
    assert 25 <= len(offenders) <= 60, (
        f"{len(offenders)} of {len(described)} schema descriptions name internals. "
        "That is a big move in the surface this guard exempts — re-read "
        "_openapi_client_strings' docstring and decide whether the exemption "
        "still holds."
    )


# --------------------------------------------------------------------------
# The guard's own guard: these prove the matcher works, so a future edit
# that neuters it fails here rather than silently passing everything.


@pytest.mark.parametrize(
    "text",
    [
        "Gated by the governed pack",
        "when it moves more than the governed threshold for this measure",
        "this monitor uses the pack's governed gate for days",
        "this is a first turn; there is no parent answer to reconcile to",
        "the remaining 9 are in the chart and the evidence frame",
        "2026-07-01..2026-07-31 (remit) · watermark wm_003",
        "not the line the denial_rate_trend recipe asks for",
        "figure '$18,000' matches no certified value",
        "a pinned cohort materialized at one watermark",
        # The live leak this registry did not walk: the survivor
        # clarification's own question, verbatim as a reader saw it. Kept
        # here so the fix cannot regress silently — the matcher always
        # caught this sentence, and nothing ever showed it one.
        "Only one of the options I could offer survives what this data holds at this "
        "watermark",
        "each named a metric, cut or value this data does not hold at this watermark",
        "no detected anomalies at this watermark",
        "the window, scope, cohort and watermark below are rebuilt from this turn's "
        "stored investigation spec at watermark wm_003",
        "turn classification confidence 0.78",
        "status=not_applicable; reason=no parent",
        "read from base-rcm@1.0.0",
    ],
)
def test_the_matcher_catches_real_regressions(text: str) -> None:
    assert violations(text), f"the guard let {text!r} through"


@pytest.mark.parametrize(
    "text",
    [
        # Word-boundary collisions the guard must NOT report.
        "the appeal was overturned on the second submission",
        "average turnaround was 14 days",
        "a packing slip is not a remittance",
        "specific to this payer's plan",
        "the framework for coordination of benefits",
        "planned discharges are excluded",
        # Correct renderings, which must survive.
        "Standard definition — from your definitions library",
        "when it moves more than 0.5 percentage points — Revi's recommended "
        "level for rates. You can change this anytime.",
        "Held back by your organization's materiality rules",
        "Since the Aug 1 load: 3 monitors moved.",
        "denied dollars by payer, on the remittance date",
    ],
)
def test_the_matcher_does_not_cry_wolf(text: str) -> None:
    assert not violations(text), f"the guard wrongly flagged {text!r}: {violations(text)}"
