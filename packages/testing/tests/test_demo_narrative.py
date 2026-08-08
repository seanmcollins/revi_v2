"""The scripted demo narrative, checked against the grounding validator.

Demo mode streams a narrative composed by ``ScriptedLanguageModel``; the
same validator that polices a real model's prose polices it
(``validate_narrative`` — every claim-bearing sentence must cite a
referent, every number must match a certified value, every proper name
must come from the findings). A fixed sentence cannot pass that check,
because referent handles are session-monotonic: the reference
conversation certifies F1..F3 on turn 1 and F4..F6 on turn 2, so a
fixture citing F1 is redacted from turn 2 onwards. The composer therefore
reads the handles back out of the rendered prompt.

Two layers here: the composer against synthetic findings (degradation for
one, two, three and more findings, and grades weaker than direct), and
the real demo conversation end to end — every turn of the reference five,
the COB anchor and the definitional anchor — asserting zero redactions.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
from pathlib import Path

import pytest

from revi_api.clients import InProcessInvestigationClient
from revi_api.scripted_llm import (
    COB_QUESTION,
    DEFINITIONAL_QUESTION,
    REFERENCE_QUESTIONS,
    compose_demo_narrative,
    demo_language_model,
    parse_certified_findings,
)
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation.application.ports import TextLlmRequest
from revi_investigation_contracts.api import (
    FindingPayload,
    FindingValue,
    OpenSessionRequest,
    TurnAnswer,
    TurnRequest,
)
from revi_investigation_contracts.header import ContextHeaderPayload
from revi_investigation_contracts.narrative import NarrativeValidation
from revi_presentation.narrative import (
    NARRATIVE_TEMPLATE_ID,
    NARRATIVE_TEMPLATE_VERSION,
    REDACTION_NOTE,
    build_narrative_facts,
    build_narrative_prompt,
    validate_narrative,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
WAREHOUSE = REPO_ROOT / "data" / "revi_warehouse.duckdb"

REDACTION_WARNING = "narrative sentence redacted"
_CITED = re.compile(r"\b[FD]\d+\b")

HEADER = ContextHeaderPayload(
    window_start=date(2026, 7, 27),
    window_end=date(2026, 8, 2),
    basis="post",
    watermark_id="wm_003",
    display="2026-07-27..2026-08-02 (post) · watermark wm_003",
)


def _finding(referent: str, payer: str, *, grade: str = "direct") -> FindingPayload:
    """A finding in the shape the engine emits — a proper name in the
    title, real cents in the values, so the validator has teeth."""
    return FindingPayload(
        referent=referent,
        title=f"{payer} cash posted down $99,093 vs prior week",
        statement=f"{payer}: cash posted moved from 18722151 to 8812843 cents.",
        values=[
            FindingValue(name="current_cents", value=8812843),
            FindingValue(name="delta_cents", value=-9909308),
        ],
        grade=grade,
    )


def _findings(count: int, *, grade: str = "direct") -> list[FindingPayload]:
    payers = ["State Medicaid", "Atlas Commercial", "Meridian Health",
              "Summit Peak", "Bluestone Mutual"]
    return [
        _finding(f"F{index + 4}", payers[index % len(payers)], grade=grade)
        for index in range(count)
    ]


def _narrate(findings: list[FindingPayload]) -> tuple[tuple[str, ...], NarrativeValidation]:
    """Run the composer over the real rendered template and validate it."""
    prompt = build_narrative_prompt(
        findings=findings, header=HEADER, reconciliation=None, benchmarks=()
    )
    chunks = compose_demo_narrative(
        TextLlmRequest(
            template_id=NARRATIVE_TEMPLATE_ID,
            template_version=NARRATIVE_TEMPLATE_VERSION,
            rendered_prompt=prompt,
        )
    )
    text = "".join(chunks).strip()
    facts = build_narrative_facts(findings=findings, header=HEADER)
    return chunks, validate_narrative(text, facts)


class TestComposer:
    def test_reads_referents_and_grades_out_of_the_prompt(self) -> None:
        findings = [_finding("F4", "State Medicaid"), _finding("F5", "Atlas Commercial",
                                                               grade="proxy")]
        prompt = build_narrative_prompt(
            findings=findings, header=HEADER, reconciliation=None, benchmarks=()
        )
        assert parse_certified_findings(prompt) == (("F4", "direct"), ("F5", "proxy"))

    def test_nothing_certified_narrates_nothing(self) -> None:
        chunks, validation = _narrate([])
        assert chunks == ()
        assert validation.text == ""

    @pytest.mark.parametrize("count", [1, 2, 3, 4, 7])
    def test_every_population_size_validates_clean(self, count: int) -> None:
        findings = _findings(count)
        chunks, validation = _narrate(findings)
        text = "".join(chunks).strip()
        assert text
        assert len(chunks) >= 3  # several SSE narrative_delta frames stay meaningful
        assert validation.redactions == [] and validation.warnings == []
        assert validation.text == text  # nothing dropped, nothing rewritten
        assert REDACTION_NOTE not in validation.text
        # it cites this turn's handles and invents none
        cited = set(_CITED.findall(text))
        assert cited and cited <= {f.referent for f in findings}
        assert findings[0].referent in cited  # the top-ranked finding is named

    def test_handles_are_this_turns_handles_not_a_fixture(self) -> None:
        """The exact defect: a fixed sentence citing F1/F2/F3 is redacted on
        every turn whose findings are F4 and up."""
        text = "".join(_narrate(_findings(3))[0])
        assert "F4" in text and "F5" in text and "F6" in text
        assert not re.search(r"\bF[123]\b", text)

    def test_a_grade_weaker_than_direct_is_stated_plainly(self) -> None:
        findings = [
            _finding("F4", "State Medicaid"),
            _finding("F5", "Atlas Commercial", grade="proxy"),
            _finding("F6", "Meridian Health", grade="discovery"),
        ]
        chunks, validation = _narrate(findings)
        text = "".join(chunks)
        assert "F5 (proxy) and F6 (discovery)" in text
        assert "weaker than direct" in text
        assert validation.redactions == [] and validation.warnings == []

    def test_all_direct_says_so(self) -> None:
        text = "".join(_narrate(_findings(2))[0])
        assert "graded direct" in text

    def test_it_states_no_free_numbers(self) -> None:
        """The safe route, and the reason this survives every turn: the
        figures live on the finding cards, which carry their own provenance."""
        text = "".join(_narrate(_findings(5))[0])
        bare = re.sub(r"\b[FD]\d+\b", "", text)
        assert not re.search(r"\d", bare)


# ---------------------------------------------------------------------------
# the real demo conversation


async def _collect() -> dict[str, list[TurnAnswer]]:
    """Every demo turn the script serves, through the real engine."""

    async def conversation(questions: list[str]) -> list[TurnAnswer]:
        service = ApiService(
            build_components({"REVI_WAREHOUSE_PATH": str(WAREHOUSE)}, llm=demo_language_model())
        )
        client = InProcessInvestigationClient(service)
        session = await client.open_session(OpenSessionRequest(tenant="demo"))
        answers: list[TurnAnswer] = []
        for question in questions:
            response = await client.submit_turn(
                session.session_id, TurnRequest(utterance=question)
            )
            assert isinstance(response, TurnAnswer), (question, response)
            answers.append(response)
        return answers

    return {
        "reference": await conversation(list(REFERENCE_QUESTIONS)),
        "cob": await conversation([COB_QUESTION]),
        "definitional": await conversation([DEFINITIONAL_QUESTION]),
    }


@pytest.fixture(scope="module")
def demo_turns() -> dict[str, list[TurnAnswer]]:
    if not WAREHOUSE.is_file():
        pytest.skip("generated warehouse missing")
    return asyncio.run(_collect())


@pytest.mark.reference
class TestDemoConversation:
    def test_no_turn_is_ever_redacted(
        self, demo_turns: dict[str, list[TurnAnswer]]
    ) -> None:
        for label, answers in demo_turns.items():
            for turn, answer in enumerate(answers, start=1):
                where = f"{label} turn {turn}"
                assert not [w for w in answer.warnings if REDACTION_WARNING in w], where
                assert REDACTION_NOTE not in (answer.narrative or ""), where

    def test_every_turn_with_findings_narrates_its_own_findings(
        self, demo_turns: dict[str, list[TurnAnswer]]
    ) -> None:
        narrated = 0
        for label, answers in demo_turns.items():
            for turn, answer in enumerate(answers, start=1):
                where = f"{label} turn {turn}"
                referents = {f.referent for f in answer.findings}
                if not referents:
                    # META and DEFINITIONAL certify no findings, and the
                    # assembly layer refuses to narrate what it cannot cite
                    assert answer.narrative is None, where
                    continue
                narrated += 1
                assert answer.narrative, where
                cited = set(_CITED.findall(answer.narrative))
                assert cited, where
                assert cited <= referents, (where, cited, referents)
        assert narrated == 5  # four reference turns + the COB anchor

    def test_the_refinement_turns_cite_their_own_monotonic_handles(
        self, demo_turns: dict[str, list[TurnAnswer]]
    ) -> None:
        """Turn 2 is where the stale fixture failed: same three payers, new
        handles, because referent ids are monotonic across the session."""
        turn_1, turn_2 = demo_turns["reference"][0], demo_turns["reference"][1]
        assert turn_1.narrative is not None and turn_2.narrative is not None
        assert set(_CITED.findall(turn_1.narrative)) == {"F1", "F2", "F3"}
        assert set(_CITED.findall(turn_2.narrative)) == {"F4", "F5", "F6"}
        assert turn_2.narrative.startswith("F4 leads the certified findings")

    def test_the_meta_turn_stays_silent(
        self, demo_turns: dict[str, list[TurnAnswer]]
    ) -> None:
        assert demo_turns["reference"][4].narrative is None
        assert demo_turns["definitional"][0].narrative is None
