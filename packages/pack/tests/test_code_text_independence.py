"""Licensing guard: no governed code definition may be the official X12 text.

X12 maintains the claim-adjustment group codes and Claim Adjustment Reason
Codes. The *codes* are facts and we cite them freely; the *descriptions* X12
publishes alongside them are licensed content, and this repository is public.
Every ``definition_paraphrase`` in ``packs/**/codes.yaml`` must therefore be an
independently written explanation, not a copy and not a mechanical de-slashing
of one ("claim/service" respelled as "the claim or service").

WHY HASHES AND NOT THE STRINGS THEMSELVES: a test that asserted
``paraphrase != "<official X12 sentence>"`` would put the official sentence in
the repository — the exact thing the test exists to prevent, and it would ship
in every clone forever. So the denylist below holds only SHA-256 digests of the
*normalized* official sentences. A digest is a one-way commitment: it proves a
candidate string is not the licensed one without ever containing it. The
digests were produced once, offline, by the normalizer in this file; nothing in
this repository can reconstruct the source text from them.

Scope, stated honestly: this pins **verbatimness**, not paraphrase distance.
It catches a copied sentence (and, via the de-slash transform, the "expand X12's
slashes into 'or'" trick that reads as original but is not). It cannot judge
whether a genuinely reworded sentence is reworded *enough* — that stays a human
call. When adding a code, write the definition from the governed meaning rather
than from X12's wording, and this test will have nothing to say.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKS_DIR = REPO_ROOT / "packs"

# SHA-256 of the normalized official X12 descriptions for every group code and
# CARC this pack governs (plus the per-sentence split of the multi-sentence
# ones). Labels name the code only — never its text.
OFFICIAL_TEXT_DIGESTS: frozenset[str] = frozenset(
    {
        "dba766c78e94f4b85cac572aeda1c0ad43a1fc536adb58d95e2342ad5b6f788f",  # CAGC CO
        "d882d1f903a9726098def3e1e8278c607c469035196c9bc7cd93927885c099f1",  # CAGC PR
        "0b0e0f65065bcd553655497720b3038e6e11f52e340d76efcbe42c20a7e53789",  # CAGC OA
        "6a4dc379aa9f91a822d3e7c102234fad2f0a3514c9bf6087f54ba4121dd3efa1",  # CAGC PI
        "a6c2047f19328928cba429c7a6afeac93534458510690d3d3207872a8f373cde",  # CAGC CR
        "f67f43bf50a9c1aab28ad178c7e25840eb7bead2d8252c89ddf84b22efd723e5",  # CARC 1
        "2010454406b89bdb3fe49ebde059e1dd7e60d5b65e88457d0c74f835fcd967ed",  # CARC 2
        "a2bb87d32f54949b511c387ac1464b26e922f190da8fd8a4d05f6564c2350ad0",  # CARC 3
        "b10dc8731d6ecab907a28614a5626799c5cca02cf5438f35a96d34e088e77261",  # CARC 4
        "a0afb9cac0c3728629d64cc04eee50b107c0c56c2d1a0a5062e029a3de637fdd",  # CARC 11
        "c8a3c349754e092894e7179b0d149d024901602710405927970e7a469cc14f90",  # CARC 16
        "d61f0a63d65f5504d0313dabc9ba105dbaf993b9fc6b18ca3e2e337e208b75b5",  # CARC 18
        "5e38df5e88e9a14240018d531bef42655dbbd9f58959b029e8ce916a1cf61f58",  # CARC 22
        "4a854497e51fb30d2b1e0c989451c3bf99f530ddf92bcd22d886d4a4f0ef0a46",  # CARC 23
        "c9d0aa5e0dd75c2431ea422d571e4f5f83c55c72128922d0491b8329bfb4face",  # CARC 27
        "31ed5dca135e69333776b3974573427574610aecb79a0206b99e32fb23004744",  # CARC 29
        "4cf6463fe7225c07ffdbbdc1c84b983d68c1e522aa848f7b8ebc15f4cc979e45",  # CARC 45
        "19490083f5f5fbaad4c62513da58c833ef3b65807b4fa24e7af78a017bf30ec3",  # CARC 50
        "1b25b686348330a2010ad7ad689ffe64a545508c075980d1952a0c991793091c",  # CARC 96
        "22183d3f35518c9c645c057995a58505350705dfed1b5684e75c3742bc7c2dfd",  # CARC 97
        "d87d66779580df7f74034722070eef9f3ce48f4c1a9bc7a92fa9b47edb5dde0f",  # CARC 109 (whole)
        "d2de9c14414972d2779e28edd57fb2ab20b0be6c29cb3a232ecb77aa5192962e",  # CARC 109 (s1)
        "7a437c43c812b04636401ae14bf8076df22604fbe31534ee6875117fafd8e42d",  # CARC 109 (s2)
        "3a02371ef255b886313a5e3fc6163458a64e335ec24be98af12e98e6c63050e8",  # CARC 151
        "01c7d518bb253e487c919540545712655e828ee7a3811035fd36fe360ea6fc39",  # CARC 197
        "ccbd55ceb605ab19aca5e7df4f3d3d8b2a571bcba622d0c06dd26553dd4ef552",  # CARC 204
        "81611518e29e9d7bc755d4fc3f70c7916dbb44910b9a0522be0f291a7c0ec965",  # CARC 253
    }
)

# Sentence/clause boundaries. Our definitions carry analytic framing after an
# em dash or semicolon; splitting there is what lets the guard see a copied
# leading clause rather than only a copied whole paragraph.
_FRAGMENT_SPLIT = re.compile(r"[.;:—]")

# Bound on the combinatorial " or " toggling below (2**n forms per fragment).
_MAX_OR_TOGGLES = 8


def normalize(text: str) -> str:
    """Lowercase, delete punctuation, collapse whitespace. "/" is KEPT.

    Punctuation is deleted rather than replaced with a space so that X12's
    plural parentheses land on the same string as an ordinary plural:
    "error(s)" and "errors" both normalize to "errors". The slash survives on
    purpose — X12 writes "claim/service" and "payer/contractor", and keeping it
    is what lets :func:`candidate_digests` reverse a mechanical respelling.
    """
    lowered = text.lower()
    kept = "".join(c if (c.isalnum() or c.isspace() or c == "/") else "" for c in lowered)
    return " ".join(kept.split())


def _or_slash_variants(normalized: str) -> set[str]:
    """Every way of reading each " or " as either a real "or" or a de-slashed "/".

    The cheapest way to make X12's text look original is to expand its slashes
    into the word "or" ("claim/service" -> "the claim or service"). Reversing
    that is not a single replace: a sentence can contain both a genuine "or"
    and a de-slashed one, so each occurrence is toggled independently.
    """
    pieces = normalized.split(" or ")
    if len(pieces) == 1 or len(pieces) > _MAX_OR_TOGGLES + 1:
        return {normalized}
    variants = {pieces[0]}
    for piece in pieces[1:]:
        variants = {prefix + joiner + piece for prefix in variants for joiner in (" or ", "/")}
    return variants


def candidate_digests(paraphrase: str) -> dict[str, str]:
    """Digest -> the normalized form it came from, for every way to read this text."""
    fragments = [paraphrase, *_FRAGMENT_SPLIT.split(paraphrase)]
    out: dict[str, str] = {}
    for fragment in fragments:
        normalized = normalize(fragment)
        if not normalized:
            continue
        for form in _or_slash_variants(normalized):
            out[hashlib.sha256(form.encode()).hexdigest()] = form
    return out


def _governed_codes() -> list[tuple[Path, dict[str, object]]]:
    entries: list[tuple[Path, dict[str, object]]] = []
    for path in sorted(PACKS_DIR.rglob("codes.yaml")):
        document = yaml.safe_load(path.read_text()) or {}
        for entry in document.get("codes", ()):
            entries.append((path, entry))
    return entries


GOVERNED_CODES = _governed_codes()


def test_pack_codes_were_actually_found() -> None:
    """Guard the guard: a glob that matches nothing would pass vacuously."""
    assert len(GOVERNED_CODES) >= 20
    systems = {entry.get("code_system") for _, entry in GOVERNED_CODES}
    assert {"carc", "group_code"} <= systems


@pytest.mark.parametrize(
    ("path", "entry"),
    GOVERNED_CODES,
    ids=[f"{e.get('code_system')}-{e.get('code')}" for _, e in GOVERNED_CODES],
)
def test_definition_is_not_official_x12_text(path: Path, entry: dict[str, object]) -> None:
    paraphrase = entry.get("definition_paraphrase")
    assert isinstance(paraphrase, str) and paraphrase.strip(), (
        f"{path}: {entry.get('code')} has no definition_paraphrase"
    )

    hit = OFFICIAL_TEXT_DIGESTS & candidate_digests(paraphrase).keys()
    assert not hit, (
        f"{path.relative_to(REPO_ROOT)}: definition_paraphrase for "
        f"{entry.get('code_system')} {entry.get('code')} reproduces the official X12 "
        "description (matched by digest). Rewrite it from the governed meaning in your "
        "own sentence structure — do not de-slash or lightly reword the official text. "
        "The official wording must not be pasted into this repository at any point, "
        "including into this test."
    )


def test_the_digest_guard_actually_bites() -> None:
    """The mechanism works, demonstrated on invented text rather than licensed text.

    If ``normalize`` or ``candidate_digests`` ever stopped agreeing with how the
    denylist above was generated, every real assertion would pass vacuously.
    This pins the machinery using a sentence nobody licenses.
    """
    pretend_official = "Widget/gadget lacks calibration or has alignment error(s)."
    denylist = {hashlib.sha256(normalize(pretend_official).encode()).hexdigest()}

    verbatim_copy = "Widget/gadget lacks calibration or has alignment errors."
    assert denylist & candidate_digests(verbatim_copy).keys()

    # The de-slashing trick: same sentence with "/" respelled as " or ".
    de_slashed = "Widget or gadget lacks calibration or has alignment errors."
    assert denylist & candidate_digests(de_slashed).keys()

    # A copied clause hiding behind our own trailing commentary.
    buried = "Widget/gadget lacks calibration or has alignment errors — usually a rework."
    assert denylist & candidate_digests(buried).keys()

    # Genuinely independent wording is not flagged.
    independent = "The unit shipped uncalibrated, or its parts do not line up."
    assert not denylist & candidate_digests(independent).keys()
