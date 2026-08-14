"""ws_prompt_assistant — ownership gate before fanning out the session channel (§29).

Regression: the route used to import SessionStore from prompt_studio's
infrastructure directly and hand-roll the ownership check, diverging from
(and unable to benefit from future fixes to) SessionService's collapsed
not-found/wrong-owner semantics. It must now go through PromptStudioFacade,
the same seam every other WS route uses for its context.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

import app.api.ws.prompt_assistant as ws_mod
from shared_kernel.auth.permissions import Principal
from shared_kernel.realtime import WsAuthError
from shared_kernel.realtime.ws_auth import WsAuth


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _fake_sessionmaker():
    return _NullCtx


class _FakeWs:
    def __init__(self) -> None:
        self.closed_with: int | None = None

    async def close(self, code: int) -> None:
        self.closed_with = code


class _FakeFacade:
    def __init__(self, owned: bool) -> None:
        self._owned = owned
        self.checked_with: tuple | None = None

    def __call__(self, _db):
        return self

    async def verify_session_owner(self, session_id, actor_user_id):
        self.checked_with = (session_id, actor_user_id)
        return self._owned


def _auth() -> WsAuth:
    return WsAuth(
        principal=Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True),
        subprotocol="ticket.abc",
        access_token="tok",
        expires_at=datetime(2026, 7, 5, tzinfo=UTC),
        jti=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_auth_failure_closes_4401_without_checking_ownership(monkeypatch) -> None:
    async def _boom(_ws):
        raise WsAuthError("bad ticket")

    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", _boom)
    facade = _FakeFacade(owned=True)
    monkeypatch.setattr(ws_mod, "PromptStudioFacade", facade)

    ws = _FakeWs()
    await ws_mod.ws_prompt_assistant(ws, uuid.uuid4())

    assert ws.closed_with == 4401
    assert facade.checked_with is None


@pytest.mark.asyncio
async def test_unowned_session_closes_4404(monkeypatch) -> None:
    auth = _auth()

    async def _fake_auth(_ws):
        return auth

    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", _fake_auth)
    monkeypatch.setattr(ws_mod, "get_sessionmaker", _fake_sessionmaker)
    facade = _FakeFacade(owned=False)
    monkeypatch.setattr(ws_mod, "PromptStudioFacade", facade)

    called = False

    async def _fake_loop(**_kw):
        nonlocal called
        called = True

    monkeypatch.setattr(ws_mod, "connection_loop", _fake_loop)

    ws = _FakeWs()
    session_id = uuid.uuid4()
    await ws_mod.ws_prompt_assistant(ws, session_id)

    assert ws.closed_with == 4404
    assert facade.checked_with == (session_id, auth.principal.user_id)
    assert called is False


@pytest.mark.asyncio
async def test_owned_session_starts_connection_loop(monkeypatch) -> None:
    auth = _auth()

    async def _fake_auth(_ws):
        return auth

    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", _fake_auth)
    monkeypatch.setattr(ws_mod, "get_sessionmaker", _fake_sessionmaker)
    facade = _FakeFacade(owned=True)
    monkeypatch.setattr(ws_mod, "PromptStudioFacade", facade)

    captured = {}

    async def _fake_loop(**kw):
        captured.update(kw)

    monkeypatch.setattr(ws_mod, "connection_loop", _fake_loop)

    ws = _FakeWs()
    session_id = uuid.uuid4()
    await ws_mod.ws_prompt_assistant(ws, session_id)

    assert ws.closed_with is None
    assert captured["channels"] == [ws_mod.prompt_assistant_channel(session_id)]
    assert captured["principal"] is auth.principal
