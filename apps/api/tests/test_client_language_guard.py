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
    sites = (
        (
            REPO_ROOT
            / "packages/investigation-contracts/src/revi_investigation_contracts/header.py",
            "build_header_payload",
        ),
        (REPO_ROOT / "apps/api/src/revi_api/monitors_policy.py", "_pack_gate"),
        (REPO_ROOT / "apps/api/src/revi_api/monitors/common.py", "_monitors_warnings"),
        (REPO_ROOT / "apps/api/src/revi_api/service.py", "_monitor_refused"),
    )
    for path, function in sites:
        if not path.exists():  # pragma: no cover - defensive
            continue
        for text in literals_of(path, function):
            # Format specifiers, lone punctuation and wire enum values are
            # not sentences — see :func:`is_copy`.
            if not is_copy(text) or text.strip().startswith(("%", "{")):
                continue
            yield f"{path.name}::{function}", text


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
