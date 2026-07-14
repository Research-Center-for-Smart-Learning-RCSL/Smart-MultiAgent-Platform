"""make_config_scoped_ws_router — the shared scaffold behind /ws/graphrag,
/ws/rag-configs, and /ws/knowmap (code review, 2026-07-10: collapses what used
to be three hand-copied route bodies into one).

Structured like the former test_ws_knowmap.py / test_ws_prompt_assistant.py:
fakes each collaborator (auth, session, facade config lookup, role resolver,
connection_loop) and asserts the route's branch outcome, extracting the actual
route function from the built APIRouter (``router.routes[0].endpoint``) since
the factory no longer exposes a standalone, importable route function.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import contexts.knowledge.interfaces.ws_config_route as ws_mod
from shared_kernel.auth.permissions import Principal
from shared_kernel.realtime import WsAuthError
from shared_kernel.realtime.ws_auth import WsAuth


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return _NullCtx()


def _fake_sessionmaker():
    return lambda: _NullCtx()


class _FakeWs:
    def __init__(self) -> None:
        self.closed_with: int | None = None

    async def close(self, code: int) -> None:
        self.closed_with = code


class _FakeCfg:
    def __init__(self, project_id: uuid.UUID) -> None:
        self.project_id = project_id


def _auth(*, is_admin: bool = False) -> WsAuth:
    return WsAuth(
        principal=Principal(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True),
        subprotocol="ticket.abc",
        access_token="tok",
        expires_at=datetime(2026, 7, 5, tzinfo=UTC),
        jti=uuid.uuid4(),
    )


def _endpoint(router):
    """Pull the actual websocket handler out of the router the factory built."""
    return router.routes[0].endpoint


@pytest.mark.asyncio
async def test_auth_failure_closes_4401_without_config_lookup(monkeypatch) -> None:
    async def _boom(_ws):
        raise WsAuthError("bad ticket")

    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", _boom)

    looked_up = False

    async def _get_config(_facade, _config_id):
        nonlocal looked_up
        looked_up = True
        return _FakeCfg(uuid.uuid4())

    router = ws_mod.make_config_scoped_ws_router(
        path="/ws/fake/{config_id}", get_config=_get_config, channel_fn=lambda cid: f"ws:fake:{cid}"
    )
    ws = _FakeWs()
    await _endpoint(router)(ws, uuid.uuid4())

    assert ws.closed_with == 4401
    assert looked_up is False


@pytest.mark.asyncio
async def test_missing_config_closes_4404(monkeypatch) -> None:
    auth = _auth()

    async def _fake_auth(_ws):
        return auth

    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", _fake_auth)
    monkeypatch.setattr(ws_mod, "get_sessionmaker", _fake_sessionmaker)

    config_id = uuid.uuid4()
    looked_up_with: uuid.UUID | None = None

    async def _get_config(_facade, cid):
        nonlocal looked_up_with
        looked_up_with = cid
        return

    called = False

    async def _fake_loop(**_kw):
        nonlocal called
        called = True

    monkeypatch.setattr(ws_mod, "connection_loop", _fake_loop)

    router = ws_mod.make_config_scoped_ws_router(
        path="/ws/fake/{config_id}", get_config=_get_config, channel_fn=lambda cid: f"ws:fake:{cid}"
    )
    ws = _FakeWs()
    await _endpoint(router)(ws, config_id)

    assert ws.closed_with == 4404
    assert looked_up_with == config_id
    assert called is False


@pytest.mark.asyncio
async def test_non_member_closes_4403(monkeypatch) -> None:
    auth = _auth()

    async def _fake_auth(_ws):
        return auth

    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", _fake_auth)
    monkeypatch.setattr(ws_mod, "get_sessionmaker", _fake_sessionmaker)

    async def _deny(_session, *, principal, cfg):
        return False

    monkeypatch.setattr(ws_mod, "has_config_read_access", _deny)

    async def _get_config(_facade, _cid):
        return _FakeCfg(uuid.uuid4())

    called = False

    async def _fake_loop(**_kw):
        nonlocal called
        called = True

    monkeypatch.setattr(ws_mod, "connection_loop", _fake_loop)

    router = ws_mod.make_config_scoped_ws_router(
        path="/ws/fake/{config_id}", get_config=_get_config, channel_fn=lambda cid: f"ws:fake:{cid}"
    )
    ws = _FakeWs()
    await _endpoint(router)(ws, uuid.uuid4())

    assert ws.closed_with == 4403
    assert called is False


@pytest.mark.asyncio
async def test_member_subscribes_to_the_given_channel(monkeypatch) -> None:
    auth = _auth()

    async def _fake_auth(_ws):
        return auth

    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", _fake_auth)
    monkeypatch.setattr(ws_mod, "get_sessionmaker", _fake_sessionmaker)

    async def _allow(_session, *, principal, cfg):
        return True

    monkeypatch.setattr(ws_mod, "has_config_read_access", _allow)

    async def _get_config(_facade, _cid):
        return _FakeCfg(uuid.uuid4())

    captured = {}

    async def _fake_loop(**kw):
        captured.update(kw)

    monkeypatch.setattr(ws_mod, "connection_loop", _fake_loop)

    router = ws_mod.make_config_scoped_ws_router(
        path="/ws/fake/{config_id}", get_config=_get_config, channel_fn=lambda cid: f"ws:fake:{cid}"
    )
    ws = _FakeWs()
    config_id = uuid.uuid4()
    await _endpoint(router)(ws, config_id)

    assert ws.closed_with is None
    assert captured["channels"] == [f"ws:fake:{config_id}"]
    assert captured["principal"] is auth.principal


@pytest.mark.asyncio
async def test_admin_skips_config_lookup_and_membership_check(monkeypatch) -> None:
    auth = _auth(is_admin=True)

    async def _fake_auth(_ws):
        return auth

    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", _fake_auth)

    looked_up = False

    async def _get_config(_facade, _cid):
        nonlocal looked_up
        looked_up = True
        return  # would 404 a non-admin — admin must bypass the lookup entirely

    captured = {}

    async def _fake_loop(**kw):
        captured.update(kw)

    monkeypatch.setattr(ws_mod, "connection_loop", _fake_loop)

    router = ws_mod.make_config_scoped_ws_router(
        path="/ws/fake/{config_id}", get_config=_get_config, channel_fn=lambda cid: f"ws:fake:{cid}"
    )
    ws = _FakeWs()
    config_id = uuid.uuid4()
    await _endpoint(router)(ws, config_id)

    assert ws.closed_with is None
    assert looked_up is False
    assert captured["channels"] == [f"ws:fake:{config_id}"]


# --------------------------------------------------------------------------- #
# F-25 — mid-socket re-authorization callback.                                 #
# --------------------------------------------------------------------------- #


class _FakeConn:
    def __init__(self, *, is_admin: bool) -> None:
        self.principal = Principal(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True)


async def _capture_loop_kwargs(monkeypatch, *, get_config=None) -> dict:
    """Build the router (admin handshake -> reaches connection_loop) and return
    the kwargs the loop was called with, so the ``authorize`` callback can be
    exercised directly."""
    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", AsyncMock(return_value=_auth(is_admin=True)))
    monkeypatch.setattr(ws_mod, "get_sessionmaker", _fake_sessionmaker)
    monkeypatch.setattr(ws_mod, "KnowledgeFacade", lambda _s: object())

    captured: dict = {}

    async def _fake_loop(**kw):
        captured.update(kw)

    monkeypatch.setattr(ws_mod, "connection_loop", _fake_loop)

    if get_config is None:

        async def get_config(_facade, _cid):
            return _FakeCfg(uuid.uuid4())

    router = ws_mod.make_config_scoped_ws_router(
        path="/ws/fake/{config_id}", get_config=get_config, channel_fn=lambda cid: f"ws:fake:{cid}"
    )
    await _endpoint(router)(_FakeWs(), uuid.uuid4())
    return captured


@pytest.mark.asyncio
async def test_authorize_callback_is_wired_into_connection_loop(monkeypatch) -> None:
    # Red-first: before F-25 the factory passed no ``authorize`` kwarg, so the
    # watchdog never re-checked access for these channels.
    captured = await _capture_loop_kwargs(monkeypatch)
    assert callable(captured.get("authorize"))


@pytest.mark.asyncio
async def test_authorize_denies_when_predicate_denies(monkeypatch) -> None:
    captured = await _capture_loop_kwargs(monkeypatch)
    monkeypatch.setattr(ws_mod, "has_config_read_access", AsyncMock(return_value=False))
    assert await captured["authorize"](_FakeConn(is_admin=False)) is False


@pytest.mark.asyncio
async def test_authorize_allows_when_predicate_allows(monkeypatch) -> None:
    captured = await _capture_loop_kwargs(monkeypatch)
    monkeypatch.setattr(ws_mod, "has_config_read_access", AsyncMock(return_value=True))
    assert await captured["authorize"](_FakeConn(is_admin=False)) is True


@pytest.mark.asyncio
async def test_authorize_denies_deleted_config(monkeypatch) -> None:
    async def _get_none(_facade, _cid):
        return None

    captured = await _capture_loop_kwargs(monkeypatch, get_config=_get_none)
    # Predicate must not even be consulted once the config is gone.
    monkeypatch.setattr(
        ws_mod, "has_config_read_access", AsyncMock(side_effect=AssertionError("must not run"))
    )
    assert await captured["authorize"](_FakeConn(is_admin=False)) is False


@pytest.mark.asyncio
async def test_authorize_admin_bypass(monkeypatch) -> None:
    # Real predicate: an admin principal short-circuits to True before any
    # room/role resolution, so the socket survives across its lifetime.
    captured = await _capture_loop_kwargs(monkeypatch)
    assert await captured["authorize"](_FakeConn(is_admin=True)) is True
