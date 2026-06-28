"""Unit tests for fail-closed auth handling in agent tool execution (M3).

When a tool stores sealed credentials but unsealing fails (e.g. a Vault Transit
hiccup or AAD/key-version mismatch), the tool must surface an error rather than
silently falling back to an unauthenticated upstream call.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from contexts.agents.application.runtime import builtin_tools as bt
from contexts.agents.domain.models import AgentTool, AgentToolType

_NOW = datetime(2026, 6, 22, 12, 0, 0)


def _agent() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), project_id=uuid.uuid4())


def _deps(**over) -> bt.BuiltinToolDeps:
    base = {
        "runner": AsyncMock(),
        "proxy": AsyncMock(),
        "adapters": {},
        "cache": object(),
        "rate_limiter": object(),
    }
    base.update(over)
    return bt.BuiltinToolDeps(**base)  # type: ignore[arg-type]


def _mcp_tool(*, with_auth: bool) -> AgentTool:
    config: dict = {
        "source": "url",
        "reference": "https://mcp.example.com",
        "allowed_tools": ["alpha"],
    }
    if with_auth:
        config["auth"] = {"__sealed__": True, "ciphertext": "opaque"}
    return AgentTool(
        id=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        tool_type=AgentToolType.HOSTED_MCP,
        enabled=True,
        display_name=None,
        config=config,
        created_at=_NOW,
    )


async def test_mcp_fails_closed_when_unseal_raises(monkeypatch) -> None:
    from contexts.agents.application import tool_auth

    def _boom(*_a, **_k):
        raise RuntimeError("vault transit unavailable")

    monkeypatch.setattr(tool_auth, "unseal_tool_auth", _boom)

    runner = AsyncMock()
    tool = bt._build_mcp_tool_from_agent_tool(
        AsyncMock(),
        agent=_agent(),
        tool=_mcp_tool(with_auth=True),
        mcp_tool="alpha",
        deps=_deps(runner=runner),
    )

    result = await tool.invoke({})

    assert result.is_error
    assert "credentials" in result.content
    # Never reached the upstream with a missing credential.
    runner.invoke_mcp_tool.assert_not_awaited()


async def test_mcp_invokes_when_no_auth_configured(monkeypatch) -> None:
    # A tool without stored auth is unaffected by the fail-closed guard.
    runner = AsyncMock()
    runner.invoke_mcp_tool.return_value = SimpleNamespace(
        ok=True, stdout="ok", stderr=""
    )
    tool = bt._build_mcp_tool_from_agent_tool(
        AsyncMock(),
        agent=_agent(),
        tool=_mcp_tool(with_auth=False),
        mcp_tool="alpha",
        deps=_deps(runner=runner),
    )

    result = await tool.invoke({})

    assert not result.is_error
    runner.invoke_mcp_tool.assert_awaited_once()
    assert runner.invoke_mcp_tool.await_args.kwargs["auth"] is None


def test_unseal_safe_returns_none_on_failure(monkeypatch) -> None:
    # The function-tool guard relies on this returning None (not raising) so the
    # caller can fail closed; a sealed blob that cannot be unsealed yields None.
    from contexts.agents.application import tool_auth

    monkeypatch.setattr(
        tool_auth,
        "unseal_tool_auth",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    tool = _mcp_tool(with_auth=True)
    assert bt._unseal_tool_auth_safe(tool) is None


def test_unseal_safe_returns_none_when_no_auth() -> None:
    tool = _mcp_tool(with_auth=False)
    assert bt._unseal_tool_auth_safe(tool) is None
