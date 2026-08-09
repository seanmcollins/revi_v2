"""Session settings and the debug trace over both transports.

The same suite the rest of the API contract gets: in-process and HTTP,
because a bound enforced in a route handler is a bound the in-process
embedding does not have. Settings are refused identically on both doors,
the debug block appears on both, and the trace route is tenant-scoped
like every other read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from revi_api.app import create_app
from revi_api.auth import Principal, TokenSigner
from revi_api.clients import HttpInvestigationClient, InProcessInvestigationClient
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation_contracts.api import (
    OpenSessionRequest,
    TurnAnswer,
    TurnError,
    TurnRequest,
)
from revi_investigation_contracts.settings import SessionSettingsModel
from revi_kernel.errors import PolicyDeniedError
from revi_testing.mock_llm import MockLanguageModel

AUTH_SECRET = "test-signing-secret"
AUTH_ENV = {"REVI_AUTH_SECRET": AUTH_SECRET}
TENANT = "demo"
T1 = "Why did cash decline last week?"

#: A live-model deployment: an allowlist, a ceiling, traces on.
SETTINGS_ENV = {
    "REVI_MODEL_PIN": "claude-opus-5",
    "REVI_MODEL_TIERS": "claude-opus-5,claude-sonnet-5",
    "REVI_LLM_MAX_BUDGET_USD": "0.50",
}


def token_for(tenant: str = TENANT) -> str:
    return TokenSigner(AUTH_SECRET).issue(tenant=tenant, subject="settings-suite")


def _canned_llm() -> MockLanguageModel:
    llm = MockLanguageModel()
    llm.text_chunks = ("Posted cash fell versus the prior week; the largest driver is F1.",)
    llm.respond(
        "classify_turn",
        {"turn_class": "new_investigation", "confidence": 0.94, "clarification_question": None},
    )
    llm.respond(
        "interpret_question",
        {
            "intent_summary": "cash decline",
            "metric_ids": [],
            "dimension_ids": ["payer"],
            "concept_ids": [],
            "playbook_id": "cash_decline",
            "window": {"quantity": "1", "unit": "week", "mode": "full_periods"},
            "basis": "post",
            "comparison": "prior_period",
            "scope": [],
            "clarification": None,
            "definitional_terms": [],
        },
    )
    return llm


@pytest.fixture(scope="module")
def small_warehouse_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from revi_warehouse.config import GeneratorConfig
    from revi_warehouse.generate import run_generation

    out = tmp_path_factory.mktemp("warehouse") / "revi_small.duckdb"
    return run_generation(GeneratorConfig.small(), out).db_path


@pytest.fixture(scope="module")
def llm() -> MockLanguageModel:
    return _canned_llm()


@pytest.fixture(scope="module")
def service(small_warehouse_path: Path, llm: MockLanguageModel) -> ApiService:
    components = build_components(
        {**SETTINGS_ENV, "REVI_WAREHOUSE_PATH": str(small_warehouse_path)}, llm=llm
    )
    return ApiService(components)


@pytest.fixture(params=["in_process", "http"])
async def client(
    request: pytest.FixtureRequest, service: ApiService
) -> AsyncIterator[InProcessInvestigationClient | HttpInvestigationClient]:
    if request.param == "in_process":
        yield InProcessInvestigationClient(service, Principal(tenant=TENANT, subject="tests"))
        return
    transport = httpx.ASGITransport(app=create_app(service, env=AUTH_ENV))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        yield HttpInvestigationClient(raw, token_for())


Client = InProcessInvestigationClient | HttpInvestigationClient


class TestSessionSettings:
    async def test_settings_are_echoed_as_resolved_values(self, client: Client) -> None:
        session = await client.open_session(
            OpenSessionRequest(
                settings=SessionSettingsModel(
                    model_tier="claude-sonnet-5",
                    max_turn_cost_usd="0.25",
                    narrative_depth="analyst",
                    evidence_depth="deep",
                    debug=True,
                )
            )
        )

        assert session.settings.model_tier == "claude-sonnet-5"
        assert session.settings.max_turn_cost_usd == "0.25"
        assert session.settings.narrative_depth == "analyst"
        assert session.settings.evidence_depth == "deep"
        assert session.settings.debug is True

    async def test_settings_survive_a_reconnect(self, client: Client) -> None:
        """Re-joining without settings must not reset them: a reconnect
        that silently restored the deployment defaults would be a
        downgrade nobody asked for and nobody would see."""
        opened = await client.open_session(
            OpenSessionRequest(settings=SessionSettingsModel(evidence_depth="deep"))
        )

        rejoined = await client.open_session(OpenSessionRequest(session_id=opened.session_id))

        assert rejoined.settings.evidence_depth == "deep"

    async def test_reopening_with_settings_re_applies_them(self, client: Client) -> None:
        opened = await client.open_session(
            OpenSessionRequest(settings=SessionSettingsModel(evidence_depth="deep"))
        )

        changed = await client.open_session(
            OpenSessionRequest(
                session_id=opened.session_id,
                settings=SessionSettingsModel(evidence_depth="standard"),
            )
        )

        assert changed.settings.evidence_depth == "standard"

    async def test_an_out_of_bounds_budget_is_refused_at_session_open(
        self, service: ApiService
    ) -> None:
        principal = Principal(tenant=TENANT, subject="tests")

        with pytest.raises(PolicyDeniedError) as caught:
            await service.open_session(
                principal,
                OpenSessionRequest(settings=SessionSettingsModel(max_turn_cost_usd="99")),
            )

        assert caught.value.details["ceiling"] == "0.50"

    async def test_an_out_of_bounds_budget_is_a_400_over_http(
        self, service: ApiService
    ) -> None:
        transport = httpx.ASGITransport(app=create_app(service, env=AUTH_ENV))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            response = await raw.post(
                "/v1/sessions",
                json={"settings": {"max_turn_cost_usd": "99"}},
                headers={"authorization": f"Bearer {token_for()}"},
            )

        assert response.status_code == 400
        assert response.json()["code"] == "POLICY_DENIED"


class TestPerTurnSettings:
    async def test_a_refused_turn_setting_is_a_turn_error_on_both_transports(
        self, client: Client
    ) -> None:
        session = await client.open_session(OpenSessionRequest())

        response = await client.submit_turn(
            session.session_id,
            TurnRequest(
                utterance=T1, settings=SessionSettingsModel(model_tier="not-on-the-list")
            ),
        )

        assert isinstance(response, TurnError), response
        assert response.error.code == "POLICY_DENIED"
        assert "allowlist" in response.error.message

    async def test_an_allowed_tier_reaches_the_model_call(
        self, service: ApiService, llm: MockLanguageModel
    ) -> None:
        """The whole point of the control: the session's tier is the model
        id the port is actually asked for."""
        client = InProcessInvestigationClient(service, Principal(tenant=TENANT, subject="tests"))
        session = await client.open_session(
            OpenSessionRequest(settings=SessionSettingsModel(model_tier="claude-sonnet-5"))
        )

        answer = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))

        assert isinstance(answer, TurnAnswer)
        assert llm.structured_calls[-1].policy.model == "claude-sonnet-5"
        assert llm.text_calls[-1].policy.model == "claude-sonnet-5"

    async def test_session_settings_and_a_turn_override_can_both_be_sent(
        self, service: ApiService, llm: MockLanguageModel
    ) -> None:
        """Both fields populated at once is a supported combination, not an
        untested one.

        The web client only ever sends ``TurnRequest.settings`` — settings
        apply per turn there — so ``OpenSessionRequest.settings`` is a
        surface nothing exercised end to end. The rule when both arrive is
        the same rule a turn override always had: the turn's settings
        govern the turn, and the session keeps the ones it was opened with.
        """
        client = InProcessInvestigationClient(service, Principal(tenant=TENANT, subject="tests"))
        session = await client.open_session(
            OpenSessionRequest(settings=SessionSettingsModel(model_tier="claude-sonnet-5"))
        )
        assert session.settings.model_tier == "claude-sonnet-5"

        answer = await client.submit_turn(
            session.session_id,
            TurnRequest(
                utterance=T1,
                settings=SessionSettingsModel(model_tier="claude-opus-5", debug=True),
            ),
        )

        assert isinstance(answer, TurnAnswer)
        # The turn ran under the turn's tier...
        assert llm.structured_calls[-1].policy.model == "claude-opus-5"
        assert answer.debug is not None
        # ...and the session still holds the one it was opened with.
        rejoined = await client.open_session(OpenSessionRequest(session_id=session.session_id))
        assert rejoined.settings.model_tier == "claude-sonnet-5"
        assert rejoined.settings.debug is False

    async def test_a_per_turn_override_does_not_rewrite_the_session(
        self, client: Client
    ) -> None:
        session = await client.open_session(OpenSessionRequest())

        answer = await client.submit_turn(
            session.session_id, TurnRequest(utterance=T1, settings=SessionSettingsModel(debug=True))
        )
        assert isinstance(answer, TurnAnswer) and answer.debug is not None

        rejoined = await client.open_session(OpenSessionRequest(session_id=session.session_id))
        assert rejoined.settings.debug is False


class TestDebugSurface:
    async def test_no_debug_block_unless_the_settings_asked_for_one(
        self, client: Client
    ) -> None:
        session = await client.open_session(OpenSessionRequest())

        answer = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))

        assert isinstance(answer, TurnAnswer)
        assert answer.debug is None

    async def test_the_debug_block_explains_the_turn_it_rides_on(
        self, client: Client
    ) -> None:
        session = await client.open_session(
            OpenSessionRequest(settings=SessionSettingsModel(debug=True))
        )

        answer = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))

        assert isinstance(answer, TurnAnswer)
        debug = answer.debug
        assert debug is not None
        assert debug.investigation_id == answer.investigation_id
        assert debug.plan_hash == answer.plan_hash
        assert debug.turn_class == "new_investigation"
        assert debug.interpretation is not None
        assert debug.interpretation.playbook_id == "cash_decline"
        assert debug.probes and all(p.rows is not None for p in debug.probes)
        # A first utterance is classified by construction, so the ledger
        # lists the calls that actually happened and no more.
        assert debug.llm_calls and {c.template for c in debug.llm_calls} >= {
            "interpret_question",
        }
        assert debug.weakest_grade is not None
        assert debug.watermark_id == answer.context_header.watermark_id  # type: ignore[union-attr]
        assert debug.redactions == []

    async def test_the_trace_route_serves_the_same_record(self, client: Client) -> None:
        """Recorded on every turn, published only when asked — so a turn
        nobody thought to debug is still explainable afterwards."""
        session = await client.open_session(OpenSessionRequest())
        answer = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))
        assert isinstance(answer, TurnAnswer)

        trace = await client.get_trace(answer.investigation_id)

        assert trace.investigation_id == answer.investigation_id
        assert trace.plan_hash == answer.plan_hash
        assert trace.probes

    async def test_a_clarification_carries_its_own_trace(self, client: Client) -> None:
        session = await client.open_session(
            OpenSessionRequest(settings=SessionSettingsModel(debug=True))
        )

        response = await client.submit_turn(
            session.session_id,
            TurnRequest(refinements=[]),  # a gesture with no parent answer
        )

        assert response.outcome == "clarification_required"
        assert response.debug is not None  # type: ignore[union-attr]
        assert response.debug.clarification_reason  # type: ignore[union-attr]

    async def test_another_tenant_cannot_read_a_trace(self, service: ApiService) -> None:
        owner = InProcessInvestigationClient(service, Principal(tenant=TENANT, subject="owner"))
        session = await owner.open_session(OpenSessionRequest())
        answer = await owner.submit_turn(session.session_id, TurnRequest(utterance=T1))
        assert isinstance(answer, TurnAnswer)

        transport = httpx.ASGITransport(app=create_app(service, env=AUTH_ENV))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
            response = await raw.get(
                f"/v1/investigations/{answer.investigation_id}/trace",
                headers={"authorization": f"Bearer {token_for('other-tenant')}"},
            )

        assert response.status_code == 403

    async def test_a_deployment_can_refuse_traces_outright(
        self, small_warehouse_path: Path
    ) -> None:
        components = build_components(
            {
                **SETTINGS_ENV,
                "REVI_DEBUG_TRACE": "0",
                "REVI_WAREHOUSE_PATH": str(small_warehouse_path),
            },
            llm=_canned_llm(),
        )
        client = InProcessInvestigationClient(
            ApiService(components), Principal(tenant=TENANT, subject="tests")
        )
        session = await client.open_session(OpenSessionRequest())
        answer = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))
        assert isinstance(answer, TurnAnswer)

        with pytest.raises(PolicyDeniedError):
            await client.get_trace(answer.investigation_id)
        with pytest.raises(PolicyDeniedError):
            await client.open_session(
                OpenSessionRequest(settings=SessionSettingsModel(debug=True))
            )


class TestNarrativeDepth:
    async def test_depth_selects_the_template_that_is_rendered(
        self, service: ApiService, llm: MockLanguageModel
    ) -> None:
        """A real composition parameter, not a post-hoc trim: the analyst
        depth sends a different prompt, so the model writes a different
        piece."""
        client = InProcessInvestigationClient(service, Principal(tenant=TENANT, subject="tests"))
        session = await client.open_session(
            OpenSessionRequest(settings=SessionSettingsModel(narrative_depth="analyst"))
        )

        answer = await client.submit_turn(session.session_id, TurnRequest(utterance=T1))

        assert isinstance(answer, TurnAnswer)
        prompt = llm.text_calls[-1].rendered_prompt
        assert "full analyst detail" in prompt
        assert "Cover EVERY certified finding" in prompt

    async def test_the_default_depth_keeps_the_summary_prompt(
        self, service: ApiService, llm: MockLanguageModel
    ) -> None:
        client = InProcessInvestigationClient(service, Principal(tenant=TENANT, subject="tests"))
        session = await client.open_session(OpenSessionRequest())

        await client.submit_turn(session.session_id, TurnRequest(utterance=T1))

        prompt = llm.text_calls[-1].rendered_prompt
        assert "full analyst detail" not in prompt
        assert "Two short paragraphs at most" in prompt


class TestPublishedCapabilities:
    async def test_capabilities_publish_the_bounds_a_client_must_respect(
        self, client: Client
    ) -> None:
        capabilities = await client.get_capabilities()

        bounds = capabilities.settings
        assert bounds.max_turn_cost_usd == "0.50"
        assert bounds.default_model_tier == "claude-opus-5"
        assert bounds.debug_available is True
        assert bounds.evidence_depth_deep_multiplier > 1
        assert bounds.model_tier_effective is True
        assert "claude-sonnet-5" in bounds.model_tiers
