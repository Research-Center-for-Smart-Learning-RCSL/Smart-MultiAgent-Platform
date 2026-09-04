"""Guest JWT branch in AuthMiddleware (AC-6).

Verifies that the middleware constructs a guest Principal with is_guest=True
and chatroom_id when it encounters a guest_access JWT, without calling the
identity context.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from app.api.middleware.auth import AuthMiddleware
from shared_kernel.auth import jwt as jwt_module
from shared_kernel.auth.jwt import GuestClaims, peek_token_use


def _make_guest_bearer(chatroom_id: str = "00000000-0000-0000-0000-000000000001") -> str:
    """Build a fake JWT whose payload has token_use=guest_access."""
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"token_use": "guest_access", "chatroom_id": chatroom_id}).encode()
    ).rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.fakesig"


@pytest.fixture
def guest_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/probe")
    async def probe(request: Request) -> JSONResponse:
        ctx = getattr(request.state, "auth_ctx", None)
        if ctx and ctx.principal:
            p = ctx.principal
            return JSONResponse({
                "user_id": str(p.user_id),
                "is_guest": p.is_guest,
                "chatroom_id": str(p.chatroom_id) if p.chatroom_id else None,
                "is_admin": p.is_admin,
                "email_verified": p.email_verified,
                "session_id": str(ctx.session_id) if ctx.session_id else None,
            })
        return JSONResponse({"user_id": None})

    return app


@pytest.fixture
def guest_client(guest_app: FastAPI) -> TestClient:
    return TestClient(guest_app)


# -- peek_token_use --


def test_peek_guest_token_use() -> None:
    token = _make_guest_bearer()
    assert peek_token_use(token) == "guest_access"


def test_peek_access_token_use() -> None:
    header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"token_use": "access"}).encode()
    ).rstrip(b"=")
    token = f"{header.decode()}.{payload.decode()}.sig"
    assert peek_token_use(token) == "access"


def test_peek_garbage_returns_none() -> None:
    assert peek_token_use("not-a-jwt") is None


# -- middleware guest branch --


def test_guest_jwt_constructs_guest_principal(guest_client: TestClient) -> None:
    gs_id = uuid.uuid4()
    cr_id = uuid.uuid4()
    fake_claims = GuestClaims(
        guest_session_id=gs_id,
        chatroom_id=cr_id,
        display_name="TestGuest",
        jti=uuid.uuid4(),
        exp=datetime.now(UTC) + timedelta(hours=4),
        iat=datetime.now(UTC),
    )

    with (
        patch.object(jwt_module, "verify_guest_token", return_value=fake_claims),
        patch("app.api.middleware.auth.tokens.is_denied", new_callable=AsyncMock, return_value=False),
    ):
        r = guest_client.get(
            "/probe",
            headers={"Authorization": f"Bearer {_make_guest_bearer(str(cr_id))}"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["is_guest"] is True
    assert body["chatroom_id"] == str(cr_id)
    assert body["user_id"] == str(gs_id)
    assert body["is_admin"] is False
    assert body["email_verified"] is False
    assert body["session_id"] is None


def test_guest_jwt_failure_returns_401(guest_client: TestClient) -> None:
    def _raise(_token: str) -> GuestClaims:
        raise jwt_module.JwtError("guest token expired")

    with patch.object(jwt_module, "verify_guest_token", side_effect=_raise):
        r = guest_client.get(
            "/probe",
            headers={"Authorization": f"Bearer {_make_guest_bearer()}"},
        )

    assert r.status_code == 401
    body = r.json()
    assert body["type"].endswith("/auth/token-expired")


def test_identity_context_not_called_for_guest(guest_client: TestClient) -> None:
    gs_id = uuid.uuid4()
    cr_id = uuid.uuid4()
    fake_claims = GuestClaims(
        guest_session_id=gs_id,
        chatroom_id=cr_id,
        display_name="TestGuest",
        jti=uuid.uuid4(),
        exp=datetime.now(UTC) + timedelta(hours=4),
        iat=datetime.now(UTC),
    )

    with (
        patch.object(jwt_module, "verify_guest_token", return_value=fake_claims),
        patch("app.api.middleware.auth.tokens.is_denied", new_callable=AsyncMock, return_value=False),
        patch("app.api.middleware.auth.IdentityFacade") as mock_facade,
    ):
        r = guest_client.get(
            "/probe",
            headers={"Authorization": f"Bearer {_make_guest_bearer(str(cr_id))}"},
        )

    assert r.status_code == 200
    mock_facade.assert_not_called()


def test_guest_jwt_denied_jti_returns_401(guest_client: TestClient) -> None:
    gs_id = uuid.uuid4()
    cr_id = uuid.uuid4()
    fake_claims = GuestClaims(
        guest_session_id=gs_id,
        chatroom_id=cr_id,
        display_name="TestGuest",
        jti=uuid.uuid4(),
        exp=datetime.now(UTC) + timedelta(hours=4),
        iat=datetime.now(UTC),
    )

    with (
        patch.object(jwt_module, "verify_guest_token", return_value=fake_claims),
        patch("app.api.middleware.auth.tokens.is_denied", new_callable=AsyncMock, return_value=True),
    ):
        r = guest_client.get(
            "/probe",
            headers={"Authorization": f"Bearer {_make_guest_bearer(str(cr_id))}"},
        )

    assert r.status_code == 401
    body = r.json()
    assert body["type"].endswith("/auth/token-revoked")
