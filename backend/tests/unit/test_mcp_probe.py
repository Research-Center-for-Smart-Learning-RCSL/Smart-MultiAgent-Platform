"""Unit tests for the hosted_mcp reachability probe (AgentService._probe_mcp).

Pins the allowlist pre-check added alongside _tool_warnings (R12.16 fix
design part 3): a url-sourced binding whose host is not on the project's
egress allowlist must fail before ``runner.probe`` is ever called, mirroring
``_probe_function``'s existing behaviour. A package-sourced binding has no
single knowable host and must reach the runner unconditionally.
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


def _mcp_tool(*, source: str = "url", reference: str = "https://mcp.example.com/sse") -> AgentTool:
    return AgentTool(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tool_type=AgentToolType.HOSTED_MCP,
        enabled=True,
        display_name=None,
        config={"source": source, "reference": reference, "allowed_tools": ["x"]},
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


def _patch_allowlist(monkeypatch, repo) -> None:
    monkeypatch.setattr("contexts.agents.infrastructure.mcp_repositories.EgressAllowlistRepository", repo)


async def test_probe_mcp_reports_allowlist_miss_without_calling_runner(monkeypatch) -> None:
    _patch_allowlist(monkeypatch, _DenyRepo)
    runner = AsyncMock()

    res = await _service()._probe_mcp(_agent(), _mcp_tool(), runner)

    assert res.ok is False
    assert "allowlist" in (res.error or "")
    runner.probe.assert_not_called()


async def test_probe_mcp_calls_runner_when_host_allowed(monkeypatch) -> None:
    _patch_allowlist(monkeypatch, _AllowRepo)
    runner = AsyncMock()
    runner.probe.return_value = SimpleNamespace(
        ok=True, tool_names=["x"], tools=(), duration_ms=1, error=None
    )

    res = await _service()._probe_mcp(_agent(), _mcp_tool(), runner)

    assert res.ok is True
    runner.probe.assert_awaited_once()


async def test_probe_mcp_skips_allowlist_check_for_package_source(monkeypatch) -> None:
    # No repo patched: a package-sourced reference is not a URL, so the
    # pre-check must not run at all (patching nothing proves it was skipped
    # rather than passed, since an unpatched EgressAllowlistRepository would
    # error on a real DB call from the AsyncMock session).
    runner = AsyncMock()
    runner.probe.return_value = SimpleNamespace(
        ok=True, tool_names=["x"], tools=(), duration_ms=1, error=None
    )

    res = await _service()._probe_mcp(
        _agent(), _mcp_tool(source="package", reference="left-pad@1.0.0"), runner
    )

    assert res.ok is True
    runner.probe.assert_awaited_once()
