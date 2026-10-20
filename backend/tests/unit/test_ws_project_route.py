"""Project dashboard sockets use live projects and the shared project-role resolver."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

import app.api.ws.project as ws_project_mod
import shared_kernel.auth.dependencies as auth_deps
from shared_kernel.auth.permissions import Principal, Role
from shared_kernel.realtime import AccessOutcome
from shared_kernel.realtime.ws_auth import WsAuth


class _NullCtx:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_exc):
        return False


class _FakeWs:
    def __init__(self) -> None:
        self.accepted: str | None = None
        self.closed: tuple[int, str | None] | None = None

    async def accept(self, subprotocol: str | None = None) -> None:
        self.accepted = subprotocol

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.closed = (code, reason)


class _Project:
    deleted_at = None


class _Resolver:
    def __init__(self, roles: frozenset[Role]) -> None:
        self.roles = roles

    async def roles_for(self, _principal: Principal, _scope: object) -> frozenset[Role]:
        return self.roles


def _auth(*, is_admin: bool = False) -> WsAuth:
    return WsAuth(
        principal=Principal(user_id=uuid.uuid4(), is_admin=is_admin, email_verified=True),
        subprotocol="ticket.abc",
        access_token="tok",
        expires_at=datetime(2026, 10, 20, tzinfo=UTC),
        jti=uuid.uuid4(),
    )


def _wire(monkeypatch, *, roles: frozenset[Role], project: object | None = None):
    auth = _auth()
    state = {"roles": roles, "project": _Project() if project is None else project}
    monkeypatch.setattr(ws_project_mod, "authenticate_subprotocol", lambda _ws: _async_value(auth))
    monkeypatch.setattr(ws_project_mod, "get_sessionmaker", lambda: _NullCtx)
    monkeypatch.setattr(
        ws_project_mod,
        "TenancyFacade",
        lambda _session: _ProjectFacade(state),
    )

    async def get_resolver(_db=None):
        return _Resolver(state["roles"])

    monkeypatch.setattr(auth_deps, "get_role_resolver", get_resolver)
    captured: dict = {}

    async def capture_loop(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(ws_project_mod, "connection_loop", capture_loop)
    return auth, state, captured


async def _async_value(value):
    return value


class _ProjectFacade:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def get_project(self, _project_id: uuid.UUID):
        return self.state["project"]


@pytest.mark.asyncio
async def test_org_owner_without_project_row_connects_and_rechecks(monkeypatch) -> None:
    _auth_value, _state, captured = _wire(monkeypatch, roles=frozenset({Role.PROJECT_OWNER}))
    ws = _FakeWs()
    project_id = uuid.uuid4()

    await ws_project_mod.ws_project(ws, project_id)

    assert ws.closed is None
    assert captured["channels"] == [f"ws:project:{project_id}"]
    assert captured["authorize_every_n_ticks"] == 1
    assert callable(captured["authorize"])


@pytest.mark.asyncio
async def test_bare_org_member_is_refused_with_reconnectable_close_code(monkeypatch) -> None:
    _wire(monkeypatch, roles=frozenset({Role.ORG_MEMBER}))
    ws = _FakeWs()

    await ws_project_mod.ws_project(ws, uuid.uuid4())

    assert ws.accepted == "ticket.abc"
    assert ws.closed is not None
    assert ws.closed[0] == 4403


@pytest.mark.asyncio
async def test_deleted_project_is_refused_even_for_admin(monkeypatch) -> None:
    auth = _auth(is_admin=True)
    monkeypatch.setattr(ws_project_mod, "authenticate_subprotocol", lambda _ws: _async_value(auth))
    monkeypatch.setattr(ws_project_mod, "get_sessionmaker", lambda: _NullCtx)
    monkeypatch.setattr(ws_project_mod, "TenancyFacade", lambda _session: _ProjectFacade({"project": None}))
    ws = _FakeWs()

    await ws_project_mod.ws_project(ws, uuid.uuid4())

    assert ws.closed is not None
    assert ws.closed[0] == 4404


@pytest.mark.asyncio
async def test_removed_member_is_revoked_and_deleted_project_is_gone(monkeypatch) -> None:
    _auth_value, state, captured = _wire(monkeypatch, roles=frozenset({Role.PROJECT_MEMBER}))
    await ws_project_mod.ws_project(_FakeWs(), uuid.uuid4())

    state["roles"] = frozenset()
    conn = type("Conn", (), {"principal": _auth_value.principal})()
    assert await captured["authorize"](conn) is AccessOutcome.FORBIDDEN

    state["project"] = None
    assert await captured["authorize"](conn) is AccessOutcome.GONE
