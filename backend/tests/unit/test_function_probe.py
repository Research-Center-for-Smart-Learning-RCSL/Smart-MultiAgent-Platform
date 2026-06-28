"""Unit tests for the local_function reachability probe (AgentService._probe_function).

Exercises the probe with the egress allowlist repo and proxy client faked out.
A 2xx/3xx is a pass; a 4xx/5xx is reachable but a failure; allowlist rejection,
unsealable credentials, and transport errors are reported as ok=False.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from contexts.agents.application.agent_service import AgentService
from contexts.agents.domain.models import AgentTool, AgentToolType

_NOW = datetime(2026, 6, 22, 12, 0, 0)


def _agent() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())


def _service() -> AgentService:
    return AgentService(AsyncMock())


def _fn_tool(*, with_auth: bool = False) -> AgentTool:
    config: dict = {
        "name": "lookup",
        "description": "d",
        "parameters": {"type": "object", "properties": {}},
        "http": {"method": "POST", "url": "https://api.example.com/x", "headers": {}},
    }
    if with_auth:
        config["auth"] = {"__sealed__": True, "ciphertext": "opaque"}
    return AgentTool(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tool_type=AgentToolType.LOCAL_FUNCTION,
        enabled=True,
        display_name=None,
        config=config,
        created_at=_NOW,
    )


class _AllowRepo:
    def __init__(self, _db) -> None: ...

    async def is_allowed(self, *, project_id, hostname) -> bool:
        return True


class _DenyRepo:
    def __init__(self, _db) -> None: ...

    async def is_allowed(self, *, project_id, hostname) -> bool:
        return False


class _FakeProxy:
    def __init__(self, status: int) -> None:
        self._status = status

    async def request(self, **_kw):
        return self._status, {}, b""


def _patch_allowlist(monkeypatch, repo) -> None:
    monkeypatch.setattr(
        "contexts.agents.infrastructure.mcp_repositories.EgressAllowlistRepository", repo
    )


def _patch_proxy(monkeypatch, status: int) -> None:
    monkeypatch.setattr(
        "contexts.agents.infrastructure.egress_client.egress_proxy_client_from_settings",
        lambda: _FakeProxy(status),
    )


async def test_rejects_host_not_on_allowlist(monkeypatch) -> None:
    _patch_allowlist(monkeypatch, _DenyRepo)
    res = await _service()._probe_function(_agent(), _fn_tool())
    assert res.ok is False
    assert "allowlist" in (res.error or "")


async def test_2xx_is_a_pass(monkeypatch) -> None:
    _patch_allowlist(monkeypatch, _AllowRepo)
    _patch_proxy(monkeypatch, 204)
    res = await _service()._probe_function(_agent(), _fn_tool())
    assert res.ok is True
    assert res.status == 204


async def test_4xx_is_reachable_but_fails(monkeypatch) -> None:
    # A 401 means the configured credential is being rejected — not a passing test.
    _patch_allowlist(monkeypatch, _AllowRepo)
    _patch_proxy(monkeypatch, 401)
    res = await _service()._probe_function(_agent(), _fn_tool())
    assert res.ok is False
    assert res.status == 401
    assert "401" in (res.error or "")


async def test_fails_closed_when_unseal_fails(monkeypatch) -> None:
    _patch_allowlist(monkeypatch, _AllowRepo)
    monkeypatch.setattr(
        "contexts.agents.application.runtime.builtin_tools._unseal_tool_auth_safe",
        lambda _tool: None,
    )
    res = await _service()._probe_function(_agent(), _fn_tool(with_auth=True))
    assert res.ok is False
    assert "unseal" in (res.error or "").lower()


async def test_transport_failure_reported(monkeypatch) -> None:
    _patch_allowlist(monkeypatch, _AllowRepo)

    class _BoomProxy:
        async def request(self, **_kw):
            raise RuntimeError("egress proxy unreachable")

    monkeypatch.setattr(
        "contexts.agents.infrastructure.egress_client.egress_proxy_client_from_settings",
        lambda: _BoomProxy(),
    )
    res = await _service()._probe_function(_agent(), _fn_tool())
    assert res.ok is False
    assert "unreachable" in (res.error or "")


async def test_probe_tool_audits_and_dispatches(monkeypatch) -> None:
    # probe_tool emits an agent.tool_tested audit event regardless of outcome.
    _patch_allowlist(monkeypatch, _AllowRepo)
    _patch_proxy(monkeypatch, 200)
    emitted: list = []

    async def _fake_emit(_db, event):
        emitted.append(event)

    monkeypatch.setattr("contexts.agents.application.agent_service.audit.emit", _fake_emit)

    res = await _service().probe_tool(
        agent=_agent(),
        tool=_fn_tool(),
        runner=AsyncMock(),
        actor_user_id=uuid.uuid4(),
        actor_ip=None,
    )
    assert res.ok is True
    assert len(emitted) == 1
    assert emitted[0].action == "agent.tool_tested"
