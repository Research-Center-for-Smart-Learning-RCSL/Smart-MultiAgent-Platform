"""ws_graphrag router wiring — path, facade method, and channel.

The AuthZ branches are covered generically by test_ws_config_route.py, which
this route is now built from (code review, 2026-07-10 — was previously a
hand-rolled duplicate of ws/rag_configs.py's scaffold, untested). This file
exercises the actual built router to assert the graphrag-specific wiring:
the right path, the right KnowledgeFacade method (not a sibling's), and the
right channel.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

import contexts.knowledge.interfaces.ws_config_route as ws_mod
from app.api.ws.graphrag import router
from contexts.knowledge.interfaces import graphrag_channel
from shared_kernel.auth.permissions import Principal
from shared_kernel.realtime.ws_auth import WsAuth


class _NullCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def begin(self):
        return _NullCtx()


class _FakeWs:
    def __init__(self) -> None:
        self.closed_with: int | None = None

    async def close(self, code: int) -> None:
        self.closed_with = code


def test_router_registers_the_graphrag_path() -> None:
    assert [r.path for r in router.routes] == ["/ws/graphrag/{config_id}"]


@pytest.mark.asyncio
async def test_member_lookup_calls_get_graphrag_config_and_subscribes_its_channel(monkeypatch) -> None:
    auth = WsAuth(
        principal=Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True),
        subprotocol="ticket.abc",
        access_token="tok",
        expires_at=datetime(2026, 7, 5, tzinfo=UTC),
        jti=uuid.uuid4(),
    )

    async def _fake_auth(_ws):
        return auth

    monkeypatch.setattr(ws_mod, "authenticate_subprotocol", _fake_auth)
    monkeypatch.setattr(ws_mod, "get_sessionmaker", lambda: _NullCtx)

    facade = AsyncMock()
    project_id = uuid.uuid4()
    facade.get_graphrag_config.return_value = type("Cfg", (), {"project_id": project_id})()
    monkeypatch.setattr(ws_mod, "KnowledgeFacade", lambda _session: facade)
    monkeypatch.setattr(ws_mod, "has_config_read_access", AsyncMock(return_value=True))

    captured = {}

    async def _fake_loop(**kw):
        captured.update(kw)

    monkeypatch.setattr(ws_mod, "connection_loop", _fake_loop)

    ws = _FakeWs()
    config_id = uuid.uuid4()
    await router.routes[0].endpoint(ws, config_id)

    assert ws.closed_with is None
    facade.get_graphrag_config.assert_awaited_once_with(config_id)
    facade.get_knowmap_config.assert_not_awaited()
    facade.get_rag_config.assert_not_awaited()
    assert captured["channels"] == [graphrag_channel(config_id)]


def test_channel_fn_is_graphrag_channel() -> None:
    config_id = uuid.uuid4()
    assert graphrag_channel(config_id) == f"ws:graphrag:{config_id}"
