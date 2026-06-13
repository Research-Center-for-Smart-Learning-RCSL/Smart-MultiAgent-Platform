"""Happy-path HTTP contract for `POST /api/auth/login` and `/api/auth/refresh`.

API-11 regression. The routers map the application-layer `TokenPair` (a
`slots=True` dataclass with no `__dict__`) onto the `TokenPairOut` response
model. The previous `TokenPairOut(**pair.__dict__)` raised `AttributeError`
-> unhandled 500 on *every* successful login/refresh; because no test
exercised the success path, the breakage went unnoticed.

The `AuthService` boundary is faked: constructing a real one needs Postgres,
Redis, and Vault Transit, which belong to the compose-based e2e tier. This
test pins exactly the slice the bug lived in -- the router's TokenPair ->
TokenPairOut mapping, the 200 status, and the refresh-cookie handling -- and
would have failed loudly (500) before the fix.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import auth
from app.api.v1.auth import _REFRESH_COOKIE
from contexts.identity.application.auth_service import LoginOutcome, TokenPair
from contexts.identity.domain.models import User
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session


def _pair(prefix: str) -> TokenPair:
    return TokenPair(
        access_token=f"{prefix}-access",
        refresh_token=f"{prefix}-refresh",
        token_type="Bearer",  # OAuth2 token-type label, not a credential
        expires_in=900,
    )


class _FakeAuthService:
    """Stands in for `AuthService` at the router boundary.

    The login/refresh handlers consume only the `TokenPair` the service hands
    back, so the fake returns exactly that -- no DB, Redis, or Vault. Distinct
    token prefixes (`login-*` vs `rotated-*`) let the test prove the response
    body and the rotated cookie carry the *service's* values.
    """

    def __init__(self) -> None:
        self.login_calls: list[dict[str, object]] = []
        self.refresh_calls: list[dict[str, object]] = []

    async def login(self, **kwargs: object) -> LoginOutcome:
        self.login_calls.append(kwargs)
        return LoginOutcome(
            user=cast(User, None),  # router never reads `.user`
            tokens=_pair("login"),
            session_id=uuid.uuid4(),
        )

    async def refresh(self, **kwargs: object) -> TokenPair:
        self.refresh_calls.append(kwargs)
        return _pair("rotated")


@pytest.fixture
def fake_service() -> _FakeAuthService:
    return _FakeAuthService()


@pytest.fixture
def minted_tickets() -> list[str]:
    """Records the raw access tokens handed to `mint_ws_ticket`."""
    return []


@pytest.fixture
def auth_client(
    fake_service: _FakeAuthService,
    minted_tickets: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    # Swap the service factory so the real handler runs against the fake.
    monkeypatch.setattr(auth, "_service", lambda _db: fake_service)

    # Fake the WS-ticket mint — the real one needs Redis. It records the token
    # so a test can prove the endpoint forwards exactly the presented JWT.
    async def _fake_mint(access_token: str) -> tuple[str, int]:
        minted_tickets.append(access_token)
        return "ws-ticket-xyz", 30

    monkeypatch.setattr(auth, "mint_ws_ticket", _fake_mint)

    async def _no_db() -> AsyncIterator[None]:
        # The handlers depend on `db_session`; the fake `_service` ignores the
        # value, so an inert override keeps the test off a live database.
        yield None

    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[db_session] = _no_db
    # `current_principal` gates `/ws-ticket`; override it so the test exercises
    # the handler body rather than the real JWT-verifying dependency.
    app.dependency_overrides[current_principal] = lambda: Principal(
        user_id=uuid.uuid4(), is_admin=False, email_verified=True
    )
    with TestClient(app) as c:
        yield c


def test_login_success_returns_token_pair(auth_client: TestClient, fake_service: _FakeAuthService) -> None:
    r = auth_client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "Sup3rSecret!"},
    )
    assert r.status_code == 200  # was an unhandled 500 before the API-11 fix
    assert r.json() == {
        "access_token": "login-access",
        "refresh_token": "login-refresh",
        "token_type": "Bearer",
        "expires_in": 900,
    }
    assert len(fake_service.login_calls) == 1
    assert fake_service.login_calls[0]["email"] == "user@example.com"


def test_login_sets_httponly_refresh_cookie(auth_client: TestClient) -> None:
    r = auth_client.post(
        "/api/auth/login",
        json={"email": "user@example.com", "password": "Sup3rSecret!"},
    )
    set_cookie = r.headers["set-cookie"]
    assert f"{_REFRESH_COOKIE}=login-refresh" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Path=/api/auth" in set_cookie


def test_refresh_success_from_request_body(auth_client: TestClient, fake_service: _FakeAuthService) -> None:
    r = auth_client.post("/api/auth/refresh", json={"refresh_token": "login-refresh"})
    assert r.status_code == 200  # was an unhandled 500 before the API-11 fix
    assert r.json() == {
        "access_token": "rotated-access",
        "refresh_token": "rotated-refresh",
        "token_type": "Bearer",
        "expires_in": 900,
    }
    assert fake_service.refresh_calls[0]["refresh_token"] == "login-refresh"


def test_refresh_reads_token_from_cookie(auth_client: TestClient, fake_service: _FakeAuthService) -> None:
    # A raw `Cookie` header is unambiguous — it avoids the httpx cookie jar
    # declining to replay a `Secure` cookie over the test's http:// transport.
    r = auth_client.post(
        "/api/auth/refresh",
        json={},
        headers={"Cookie": f"{_REFRESH_COOKIE}=cookie-refresh"},
    )
    assert r.status_code == 200
    assert fake_service.refresh_calls[0]["refresh_token"] == "cookie-refresh"
    # The rotated token is written back to the cookie.
    assert f"{_REFRESH_COOKIE}=rotated-refresh" in r.headers["set-cookie"]


def test_refresh_without_any_token_returns_401(auth_client: TestClient) -> None:
    r = auth_client.post("/api/auth/refresh", json={})
    assert r.status_code == 401


def test_ws_ticket_returns_single_use_ticket(auth_client: TestClient, minted_tickets: list[str]) -> None:
    # FE-7: the WS handshake authenticates with this opaque ticket, never the
    # raw JWT — so the JWT never reaches `Sec-WebSocket-Protocol`.
    r = auth_client.post(
        "/api/auth/ws-ticket",
        headers={"Authorization": "Bearer the-access-jwt"},
    )
    assert r.status_code == 200
    assert r.json() == {"ticket": "ws-ticket-xyz", "expires_in": 30}
    # The exact JWT the caller presented is what gets stashed for redemption.
    assert minted_tickets == ["the-access-jwt"]


def test_ws_ticket_without_bearer_header_returns_401(
    auth_client: TestClient, minted_tickets: list[str]
) -> None:
    # No `Authorization` header → the handler cannot recover the raw JWT and
    # must refuse rather than mint a ticket for an empty token.
    r = auth_client.post("/api/auth/ws-ticket")
    assert r.status_code == 401
    assert minted_tickets == []
