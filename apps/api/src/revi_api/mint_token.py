"""Mint a tenant bearer token (and, with ``--new-secret``, a signing key).

    uv run python -m revi_api.mint_token --new-secret
    REVI_AUTH_SECRET=... uv run python -m revi_api.mint_token --tenant acme

There is no user store and no issuer service yet; whoever holds the secret
signs for any tenant. This CLI exists so that is an explicit operator act
with a visible expiry, rather than a curl command in somebody's history.
"""

from __future__ import annotations

import argparse
import os
import sys

from revi_api.auth import TokenSigner, generate_secret


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mint a Revi API bearer token.")
    parser.add_argument("--tenant", default="demo", help="tenant the token authorizes")
    parser.add_argument("--subject", default="cli", help="who the token identifies")
    parser.add_argument("--ttl", type=int, default=12 * 3600, help="lifetime in seconds")
    parser.add_argument(
        "--new-secret",
        action="store_true",
        help="print a fresh REVI_AUTH_SECRET and exit (mints nothing)",
    )
    args = parser.parse_args(argv)

    if args.new_secret:
        print(f"REVI_AUTH_SECRET={generate_secret()}")
        return 0

    secret = os.environ.get("REVI_AUTH_SECRET", "").strip()
    if not secret:
        print(
            "REVI_AUTH_SECRET is not set. Generate one with --new-secret, export it for "
            "both this command and the API process, then mint.",
            file=sys.stderr,
        )
        return 2
    token = TokenSigner(secret).issue(
        tenant=args.tenant, subject=args.subject, ttl_seconds=args.ttl
    )
    print(token)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
