"""Authentication and tenant scoping for the ``/v1`` surface.

Before this module, ``tenant`` was a string the client asserted in a request
body and nothing checked, and both store implementations looked sessions and
investigations up **by id alone** — so any caller could read and extend
another tenant's session. The hole was structural, not a missing check in
one handler.

Two concerns, deliberately separated, because they fail differently:

**Authentication** is a transport concern and lives at the FastAPI edge.
A caller presents ``Authorization: Bearer <token>``; the token is an
HMAC-SHA256-signed compact envelope carrying the tenant, a subject, and an
expiry. There is no user database, so the issuer is whatever mints tokens
with the shared secret. What the token *does* guarantee is that ``tenant``
is no longer client-assertable — the only way to name a tenant is to hold
the key that signs for it.

**Authorization** is a domain concern and lives in :class:`ApiService`,
not in the routes. Every ``{session_id}`` and ``{investigation_id}``
lookup resolves the owning session and compares its tenant to the
principal's before returning anything, so the in-process client is bound
by the same rule as the HTTP one. Middleware would have protected exactly
one of the two transports.

The token is hand-rolled rather than a JWT because none of the JWT features
are needed: no asymmetric keys, no JWKS rotation, no third-party issuers.
``hmac.compare_digest`` over a canonical JSON payload is auditable in one
sitting and is replaceable by a real IdP integration behind the same
:class:`Principal` seam. Its limits are real and must not be papered over:
tokens are bearer credentials, they are not revocable before expiry, and
the secret is symmetric.

Operating modes
===============
``REVI_AUTH_SECRET`` set → tokens are required on every ``/v1`` route
except ``/v1/health``.

``REVI_AUTH_DEV_TENANT`` set (and no secret) → an explicit, loudly logged
development bypass that treats every unauthenticated request as that
tenant, so local runs and the test suite need not mint credentials. It is
reported in ``/v1/health`` and ``/v1/capabilities`` so no environment can be
in it without saying so.

Neither set → the app refuses every ``/v1`` request with 401. "Unconfigured"
resolves to closed, never to open.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass

from revi_kernel.errors import ErrorCode, ReviError

logger = logging.getLogger("revi.api.auth")

_ALG = "HS256"
_DEFAULT_TTL_SECONDS = 12 * 3600


class AuthenticationError(ReviError):
    """No credential, or a credential that does not verify (HTTP 401)."""

    code = ErrorCode.POLICY_DENIED


class AuthorizationError(ReviError):
    """A valid credential for the wrong tenant (HTTP 403).

    Deliberately distinct from :class:`revi_api.service.NotFoundError`:
    a caller who is authenticated and asks for a resource in another
    tenant is told they may not have it, not that it does not exist. The
    id space is not a secret — session ids appear in URLs, logs, and
    support tickets — so hiding existence buys nothing and costs the
    operator a clear audit signal.
    """

    code = ErrorCode.POLICY_DENIED


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is calling, and for which tenant. The only identity the service
    layer knows about."""

    tenant: str
    subject: str
    #: True when this principal came from the development bypass rather
    #: than a verified token — surfaced, never silently equivalent.
    development: bool = False

    def __post_init__(self) -> None:
        if not self.tenant:
            raise ValueError("Principal.tenant must be non-empty")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64url(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass(frozen=True, slots=True)
class TokenSigner:
    """Mints and verifies the tenant-bearing bearer token."""

    secret: str

    def issue(
        self, *, tenant: str, subject: str, ttl_seconds: int = _DEFAULT_TTL_SECONDS
    ) -> str:
        payload = {
            "alg": _ALG,
            "tenant": tenant,
            "sub": subject,
            "exp": int(time.time()) + ttl_seconds,
        }
        body = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
        return f"{body}.{_b64url(self._signature(body))}"

    def verify(self, token: str) -> Principal:
        body, _, signature = token.partition(".")
        if not body or not signature:
            raise AuthenticationError("malformed bearer token")
        if not hmac.compare_digest(_b64url(self._signature(body)), signature):
            raise AuthenticationError("bearer token signature does not verify")
        try:
            payload = json.loads(_unb64url(body))
        except (ValueError, json.JSONDecodeError) as exc:  # pragma: no cover - defensive
            raise AuthenticationError("bearer token payload is not readable") from exc
        if not isinstance(payload, dict) or payload.get("alg") != _ALG:
            raise AuthenticationError("unsupported bearer token algorithm")
        if int(payload.get("exp", 0)) <= int(time.time()):
            raise AuthenticationError("bearer token has expired")
        tenant = str(payload.get("tenant", ""))
        if not tenant:
            raise AuthenticationError("bearer token names no tenant")
        return Principal(tenant=tenant, subject=str(payload.get("sub", "unknown")))

    def _signature(self, body: str) -> bytes:
        return hmac.new(self.secret.encode(), body.encode(), hashlib.sha256).digest()


@dataclass(frozen=True, slots=True)
class AuthPolicy:
    """How this deployment authenticates — resolved once, at app build."""

    signer: TokenSigner | None
    development_tenant: str | None

    @property
    def mode(self) -> str:
        if self.signer is not None:
            return "bearer-token"
        if self.development_tenant is not None:
            return f"dev-open (tenant={self.development_tenant})"
        return "closed (no credential accepted)"

    def authenticate(self, authorization: str | None) -> Principal:
        """Resolve a request's ``Authorization`` header to a principal."""
        if self.signer is not None:
            if authorization is None:
                raise AuthenticationError("this API requires an Authorization: Bearer token")
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                raise AuthenticationError("expected an Authorization: Bearer <token> header")
            return self.signer.verify(token.strip())
        if self.development_tenant is not None:
            return Principal(
                tenant=self.development_tenant, subject="dev-bypass", development=True
            )
        raise AuthenticationError(
            "no authentication is configured for this deployment: set REVI_AUTH_SECRET to "
            "accept bearer tokens, or REVI_AUTH_DEV_TENANT to run open for local development"
        )


def auth_policy_from_env(env: Mapping[str, str]) -> AuthPolicy:
    secret = env.get("REVI_AUTH_SECRET", "").strip()
    dev_tenant = env.get("REVI_AUTH_DEV_TENANT", "").strip() or None
    if secret:
        if dev_tenant is not None:
            logger.warning(
                "REVI_AUTH_SECRET and REVI_AUTH_DEV_TENANT are both set — the secret wins "
                "and the development bypass is IGNORED"
            )
        logger.info("auth: bearer tokens required on every /v1 route")
        return AuthPolicy(signer=TokenSigner(secret), development_tenant=None)
    if dev_tenant is not None:
        logger.warning(
            "auth: DEVELOPMENT BYPASS active — every /v1 request is treated as tenant %r "
            "with no credential. Never run this outside local development.",
            dev_tenant,
        )
        return AuthPolicy(signer=None, development_tenant=dev_tenant)
    logger.error(
        "auth: neither REVI_AUTH_SECRET nor REVI_AUTH_DEV_TENANT is set — every /v1 request "
        "will be refused with 401"
    )
    return AuthPolicy(signer=None, development_tenant=None)


def cors_origins_from_env(env: Mapping[str, str]) -> list[str]:
    """Allowed browser origins, comma-separated, default local dev only."""
    raw = env.get("REVI_CORS_ORIGINS", "").strip()
    if not raw:
        return ["http://localhost:3000"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def generate_secret() -> str:
    """A fresh signing secret (for `python -m revi_api.mint_token --new-secret`)."""
    return secrets.token_urlsafe(32)
