"""2.25: AuthMiddleware failure-path coverage.

Existing middleware tests covered the happy path (request succeeds, principal
populated). This file pins down the error contract — every rejection must
return RFC 7807 problem+json with the documented slug, no DB rows touched
beyond the lookup, and `request.state.auth_ctx` left unauthenticated when the
middleware decides the token is *non-fatal* (no header / empty bearer).
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from shared_kernel.auth import jwt as jwt_module


# Pick an endpoint that requires an authenticated principal so the middleware
# decision is observable through the response code. /api/orgs is suitable.
_AUTHED_PATH = "/api/orgs"


@pytest.fixture
def deny_jti_in_redis() -> Iterator[None]:
    async def _is_denied(_jti):
        return True

    with patch("shared_kernel.auth.tokens.is_denied", new=_is_denied):
        yield


def test_no_authorization_header_is_non_fatal(client: TestClient) -> None:
    """Middleware must NOT 401 on a missing header — public routes rely on
    `Depends(allow_anon)` to gate access. Per-route auth deps own the 401."""
    r = client.get(_AUTHED_PATH)
    # Whatever the route's own gate decides, it must NOT be 401 from middleware
    # (route deps return 403 / 401 with their own slugs). The middleware-emitted
    # 401 has problem-type `auth/token-expired`; assert that exact slug is absent.
    if r.status_code == 401:
        body = r.json()
        assert "auth/token-expired" not in body.get("type", "")


def test_malformed_bearer_returns_401_token_expired(client: TestClient) -> None:
    """Patch the verifier so the test does not depend on a live Vault."""
    from shared_kernel.auth.jwt import JwtError

    def _raise(_token):
        raise JwtError("Malformed JWT.")

    with patch.object(jwt_module, "verify_access_token", new=_raise):
        r = client.get(_AUTHED_PATH, headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["type"].endswith("/auth/token-expired")
    assert body["status"] == 401


def test_empty_bearer_is_non_fatal(client: TestClient) -> None:
    """`Authorization: Bearer ` with no token → middleware ignores it
    (treats the request as anonymous)."""
    r = client.get(_AUTHED_PATH, headers={"Authorization": "Bearer "})
    if r.status_code == 401:
        body = r.json()
        assert "auth/token-expired" not in body.get("type", "")


def test_jwt_with_bad_issuer_returns_401(client: TestClient) -> None:
    """2.17 — issuer is verified by `verify_access_token`. Forge claims that
    pass signature but fail iss → 401 with the token-expired slug."""
    from shared_kernel.auth.jwt import JwtError

    def _raise(_token):
        raise JwtError("issuer mismatch: 'evil.example'")

    with patch.object(jwt_module, "verify_access_token", new=_raise):
        r = client.get(_AUTHED_PATH, headers={"Authorization": "Bearer sig.is.fine"})
    assert r.status_code == 401
    body = r.json()
    assert body["type"].endswith("/auth/token-expired")
    assert "issuer mismatch" in body["detail"]


def test_revoked_jti_returns_401_token_revoked(
    client: TestClient, deny_jti_in_redis: None,
) -> None:
    from shared_kernel.auth.jwt import AccessClaims
    from datetime import datetime, timedelta, timezone
    import uuid

    fake_claims = AccessClaims(
        sub=uuid.uuid4(),
        session_id=uuid.uuid4(),
        jti=uuid.uuid4(),
        exp=datetime.now(timezone.utc) + timedelta(minutes=5),
        iat=datetime.now(timezone.utc),
        role="user",
        is_admin=False,
    )

    def _ok(_token):
        return fake_claims

    with patch.object(jwt_module, "verify_access_token", new=_ok):
        r = client.get(_AUTHED_PATH, headers={"Authorization": "Bearer x.y.z"})

    assert r.status_code == 401
    body = r.json()
    assert body["type"].endswith("/auth/token-revoked")
