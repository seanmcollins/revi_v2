"""Authentication and tenant isolation on the ``/v1`` surface.

Written against an exploit reproduced end to end: open a session as one
tenant, then — as an unrelated caller presenting no credential at all —
read that session's full lineage and findings and POST a new turn into it.
Every step of that returned 200.

The tests are ordered the way the exploit ran, so a regression fails at the
step it reintroduces.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from revi_api.app import create_app
from revi_api.auth import AuthPolicy, Principal, TokenSigner, auth_policy_from_env
from revi_api.clients import HttpInvestigationClient
from revi_api.service import ApiService
from revi_api.wiring import build_components
from revi_investigation_contracts.api import TurnRequest, TypedInvestigationSpec
from revi_investigation_contracts.refinements import WindowSpecModel
from revi_testing.mock_llm import MockLanguageModel

SECRET = "auth-suite-signing-secret"
AUTH_ENV = {"REVI_AUTH_SECRET": SECRET}


def _token(tenant: str, subject: str = "auth-suite") -> str:
    return TokenSigner(SECRET).issue(tenant=tenant, subject=subject)


def _typed_turn() -> TurnRequest:
    return TurnRequest(
        spec=TypedInvestigationSpec(
            metric_ids=["cash_posted"],
            dimensions=["payer"],
            window=WindowSpecModel(quantity="1", unit="week", mode="full_periods"),
            basis="post",
        )
    )


@pytest.fixture(scope="module")
def small_warehouse_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    from revi_warehouse.config import GeneratorConfig
    from revi_warehouse.generate import run_generation

    out = tmp_path_factory.mktemp("warehouse") / "revi_small.duckdb"
    return run_generation(GeneratorConfig.small(), out).db_path


@pytest.fixture(scope="module")
def service(small_warehouse_path: Path) -> ApiService:
    components = build_components(
        {"REVI_WAREHOUSE_PATH": str(small_warehouse_path)}, llm=MockLanguageModel()
    )
    return ApiService(components)


@pytest.fixture
async def http(service: ApiService) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app(service, env=AUTH_ENV))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as raw:
        yield raw


class TestTheCredentialIsRequired:
    async def test_every_v1_route_refuses_an_unauthenticated_caller(
        self, http: httpx.AsyncClient
    ) -> None:
        gets = (
            "/v1/capabilities",
            "/v1/portfolio/latest",
            "/v1/sessions",
            "/v1/sessions/anything/lineage",
        )
        for path in gets:
            assert (await http.get(path)).status_code == 401, path
        assert (await http.get("/v1/investigations/anything")).status_code == 401
        assert (await http.post("/v1/sessions", json={"tenant": "acme"})).status_code == 401
        turn = await http.post("/v1/sessions/anything/turns", json={"utterance": "hi"})
        assert turn.status_code == 401

    async def test_health_stays_open_and_declares_the_auth_mode(
        self, http: httpx.AsyncClient
    ) -> None:
        """A liveness probe that needs a credential makes the credential a
        single point of failure for restarts — but it must not hide which
        auth mode is in effect."""
        response = await http.get("/v1/health")
        assert response.status_code == 200
        assert response.json()["auth_mode"] == "bearer-token"

    async def test_a_forged_token_does_not_verify(self, http: httpx.AsyncClient) -> None:
        forged = TokenSigner("not-the-secret").issue(tenant="acme", subject="attacker")
        response = await http.get(
            "/v1/capabilities", headers={"authorization": f"Bearer {forged}"}
        )
        assert response.status_code == 401
        assert response.json()["code"] == "POLICY_DENIED"

    async def test_an_expired_token_does_not_verify(self, http: httpx.AsyncClient) -> None:
        stale = TokenSigner(SECRET).issue(tenant="acme", subject="x", ttl_seconds=-1)
        response = await http.get(
            "/v1/capabilities", headers={"authorization": f"Bearer {stale}"}
        )
        assert response.status_code == 401

    async def test_the_scheme_is_published_in_the_spec(self, service: ApiService) -> None:
        """A generated client codes against the spec, not against 401s it
        discovers at runtime. Before this, `securitySchemes` was null and
        all seven routes carried `security: null`."""
        spec = create_app(service, env=AUTH_ENV).openapi()
        assert spec["components"]["securitySchemes"]
        for path, operations in spec["paths"].items():
            if path == "/v1/health":
                continue
            for method, operation in operations.items():
                assert operation.get("security"), f"{method.upper()} {path} declares no security"


class TestTenantIsolation:
    async def test_the_review_exploit_end_to_end(self, http: httpx.AsyncClient) -> None:
        """One tenant opens a session and answers a turn; a *different*
        authenticated tenant then tries to read and write it."""
        owner = {"authorization": f"Bearer {_token('acme-health')}"}
        intruder = {"authorization": f"Bearer {_token('other-health')}"}

        opened = await http.post("/v1/sessions", json={"tenant": "acme-health"}, headers=owner)
        assert opened.status_code == 200
        session_id = opened.json()["session_id"]

        answered = await http.post(
            f"/v1/sessions/{session_id}/turns",
            json=_typed_turn().model_dump(mode="json"),
            headers={**owner, "accept": "application/json"},
        )
        assert answered.status_code == 200
        investigation_id = answered.json()["investigation_id"]

        # read: lineage and the investigation itself
        assert (
            await http.get(f"/v1/sessions/{session_id}/lineage", headers=intruder)
        ).status_code == 403
        assert (
            await http.get(f"/v1/investigations/{investigation_id}", headers=intruder)
        ).status_code == 403
        # write: a turn posted into somebody else's session
        written = await http.post(
            f"/v1/sessions/{session_id}/turns",
            json=_typed_turn().model_dump(mode="json"),
            headers={**intruder, "accept": "application/json"},
        )
        assert written.status_code == 403

        # ...and the owner's session is untouched: still exactly one turn
        lineage = await http.get(f"/v1/sessions/{session_id}/lineage", headers=owner)
        assert lineage.status_code == 200
        assert len(lineage.json()["investigations"]) == 1

    async def test_the_session_list_never_shows_another_tenants_sessions(
        self, http: httpx.AsyncClient
    ) -> None:
        """The list route has no tenant parameter at all, and the store call
        is scoped by the token — so this is the exploit's read step applied
        to the surface that enumerates sessions rather than reads one."""
        owner = {"authorization": f"Bearer {_token('list-owner')}"}
        intruder = {"authorization": f"Bearer {_token('list-intruder')}"}

        opened = await http.post("/v1/sessions", json={}, headers=owner)
        assert opened.status_code == 200
        session_id = opened.json()["session_id"]
        answered = await http.post(
            f"/v1/sessions/{session_id}/turns",
            json=_typed_turn().model_dump(mode="json"),
            headers={**owner, "accept": "application/json"},
        )
        assert answered.status_code == 200

        mine = await http.get("/v1/sessions", headers=owner)
        assert mine.status_code == 200
        body = mine.json()
        assert body["tenant"] == "list-owner"
        assert session_id in {row["session_id"] for row in body["sessions"]}

        theirs = await http.get("/v1/sessions", headers=intruder)
        assert theirs.status_code == 200
        assert theirs.json()["tenant"] == "list-intruder"
        assert session_id not in {row["session_id"] for row in theirs.json()["sessions"]}
        # ...and not merely filtered out of the rows: the count must not
        # betray that another tenant's session exists either.
        assert theirs.json()["total"] == 0

    async def test_a_listed_row_carries_its_first_question_and_turn_count(
        self, http: httpx.AsyncClient
    ) -> None:
        """The title is the turn's own question text, not a generated
        label — and a session that has answered nothing says so.

        A typed turn names no question, so the engine's own placeholder for
        one (``(typed investigation)``) is what the row shows: honest about
        there being no sentence rather than inventing one from the spec."""
        headers = {"authorization": f"Bearer {_token('list-titles')}", "accept": "application/json"}
        answered = await http.post("/v1/sessions", json={}, headers=headers)
        answered_id = answered.json()["session_id"]
        turn = await http.post(
            f"/v1/sessions/{answered_id}/turns",
            json=_typed_turn().model_dump(mode="json"),
            headers=headers,
        )
        assert turn.status_code == 200
        empty_id = (await http.post("/v1/sessions", json={}, headers=headers)).json()[
            "session_id"
        ]

        rows = {
            row["session_id"]: row
            for row in (await http.get("/v1/sessions", headers=headers)).json()["sessions"]
        }

        assert rows[answered_id]["title"] == "(typed investigation)"
        assert rows[answered_id]["turn_count"] == 1
        assert rows[empty_id]["title"] == "New session"
        assert rows[empty_id]["turn_count"] == 0

    async def test_the_limit_is_bounded_by_the_deployment(
        self, http: httpx.AsyncClient
    ) -> None:
        """An unbounded page size is a handle a client can pull by
        accident; a refused one is a 422 request-shape error, the same
        status every other malformed request gets."""
        headers = {"authorization": f"Bearer {_token('list-limits')}"}
        assert (await http.get("/v1/sessions?limit=1", headers=headers)).status_code == 200
        assert (await http.get("/v1/sessions?limit=0", headers=headers)).status_code == 422
        assert (await http.get("/v1/sessions?limit=100000", headers=headers)).status_code == 422

    async def test_the_body_cannot_name_another_tenant(self, http: httpx.AsyncClient) -> None:
        """`tenant` used to be an unvalidated client-asserted string; it is
        now decided by the signature and only cross-checked against the
        body."""
        headers = {"authorization": f"Bearer {_token('acme-health')}"}
        response = await http.post("/v1/sessions", json={"tenant": "somebody-else"}, headers=headers)
        assert response.status_code == 403

    async def test_a_turn_executes_under_the_tokens_tenant(
        self, http: httpx.AsyncClient, service: ApiService
    ) -> None:
        """It used to execute under the literal string "api" for everyone."""
        headers = {"authorization": f"Bearer {_token('acme-health')}"}
        opened = await http.post("/v1/sessions", json={}, headers=headers)
        session_id = opened.json()["session_id"]
        assert opened.json()["tenant"] == "acme-health"
        session = await service.components.sessions.get(session_id)
        assert session is not None and session.tenant == "acme-health"

    async def test_the_portfolio_names_the_tenant_it_was_built_for(
        self, http: httpx.AsyncClient
    ) -> None:
        client = HttpInvestigationClient(http, _token("acme-health"))
        portfolio = await client.get_portfolio()
        assert portfolio.tenant == "acme-health"

    async def test_idempotency_keys_do_not_cross_tenants(
        self, http: httpx.AsyncClient
    ) -> None:
        """The idempotency map was keyed on (session_id, key) alone."""
        owner = {"authorization": f"Bearer {_token('acme-health')}", "accept": "application/json"}
        opened = await http.post("/v1/sessions", json={}, headers=owner)
        session_id = opened.json()["session_id"]
        body = _typed_turn().model_dump(mode="json") | {"idempotency_key": "shared-key"}
        first = await http.post(f"/v1/sessions/{session_id}/turns", json=body, headers=owner)
        assert first.status_code == 200
        intruder = {
            "authorization": f"Bearer {_token('other-health')}",
            "accept": "application/json",
        }
        replay = await http.post(f"/v1/sessions/{session_id}/turns", json=body, headers=intruder)
        assert replay.status_code == 403, "a stored response must not be served to another tenant"


class TestAuthPolicyResolution:
    def test_unconfigured_resolves_to_closed_not_open(self) -> None:
        policy = auth_policy_from_env({})
        assert policy.mode.startswith("closed")
        with pytest.raises(Exception, match="no authentication is configured"):
            policy.authenticate(None)

    def test_the_development_bypass_is_explicit_and_labelled(self) -> None:
        policy = auth_policy_from_env({"REVI_AUTH_DEV_TENANT": "demo"})
        principal = policy.authenticate(None)
        assert principal == Principal(tenant="demo", subject="dev-bypass", development=True)
        assert "dev-open" in policy.mode

    def test_a_secret_beats_the_development_bypass(self) -> None:
        policy = auth_policy_from_env(
            {"REVI_AUTH_SECRET": SECRET, "REVI_AUTH_DEV_TENANT": "demo"}
        )
        assert policy.mode == "bearer-token"
        with pytest.raises(Exception, match="requires an Authorization"):
            policy.authenticate(None)

    def test_round_trip(self) -> None:
        signer = TokenSigner(SECRET)
        principal = signer.verify(signer.issue(tenant="acme", subject="alice"))
        assert principal.tenant == "acme" and principal.subject == "alice"
        assert principal.development is False

    def test_a_tampered_payload_is_rejected(self) -> None:
        signer = TokenSigner(SECRET)
        signature = signer.issue(tenant="acme", subject="alice").partition(".")[2]
        forged_body = signer.issue(tenant="evil", subject="alice").partition(".")[0]
        policy = AuthPolicy(signer=signer, development_tenant=None)
        with pytest.raises(Exception, match="signature does not verify"):
            policy.authenticate(f"Bearer {forged_body}.{signature}")
