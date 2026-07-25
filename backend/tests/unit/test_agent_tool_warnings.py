"""Unit tests for app.api.v1.agents._tool_warnings (R12.16 fix design part 3).

Before this fix, the warning channel (_function_warnings) early-returned for
every tool type except LOCAL_FUNCTION, so HOSTED_WEB_SEARCH -- enabled by
default on every agent -- and HOSTED_MCP got no config_warnings entry even
when their egress host was absent from the project's allowlist.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.api.v1.agents import _tool_warnings
from contexts.agents.domain.models import AgentToolType
from contexts.keys.domain.probe_status import ProbeStatus
from contexts.keys.domain.search import SearchKey, SearchProvider

_ALLOW = "contexts.agents.application.runtime.builtin_tools.function_egress_allowed"


def _search_key(project_id: uuid.UUID, *, is_active: bool) -> SearchKey:
    return SearchKey(
        id=uuid.uuid4(),
        project_id=project_id,
        provider=SearchProvider.TAVILY,
        masked_preview="****",
        test_status=ProbeStatus.OK,
        test_error=None,
        last_test_at=datetime.now(tz=UTC),
        is_active=is_active,
        config={},
        transit_key_version=1,
        hmac_key_version=1,
        created_at=datetime.now(tz=UTC),
        deleted_at=None,
    )


def _tool(tool_type: AgentToolType, config: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_type=tool_type, config=config)


async def test_web_search_tool_warns_when_provider_host_not_allowlisted(monkeypatch) -> None:
    project_id = uuid.uuid4()

    class _Repo:
        def __init__(self, _db) -> None: ...

        async def list_for_project(self, _project_id):
            return [_search_key(project_id, is_active=True)]

    monkeypatch.setattr("contexts.keys.infrastructure.search_repository.SearchKeyRepository", _Repo)
    monkeypatch.setattr(_ALLOW, AsyncMock(return_value=("api.tavily.com", False)))

    warnings = await _tool_warnings(AsyncMock(), project_id, _tool(AgentToolType.HOSTED_WEB_SEARCH, {}))

    assert warnings == ["host api.tavily.com is not on the project egress allowlist"]


async def test_web_search_tool_no_warning_when_host_allowlisted(monkeypatch) -> None:
    project_id = uuid.uuid4()

    class _Repo:
        def __init__(self, _db) -> None: ...

        async def list_for_project(self, _project_id):
            return [_search_key(project_id, is_active=True)]

    monkeypatch.setattr("contexts.keys.infrastructure.search_repository.SearchKeyRepository", _Repo)
    monkeypatch.setattr(_ALLOW, AsyncMock(return_value=("api.tavily.com", True)))

    warnings = await _tool_warnings(AsyncMock(), project_id, _tool(AgentToolType.HOSTED_WEB_SEARCH, {}))

    assert warnings == []


async def test_web_search_tool_no_warning_when_no_active_key(monkeypatch) -> None:
    project_id = uuid.uuid4()

    class _Repo:
        def __init__(self, _db) -> None: ...

        async def list_for_project(self, _project_id):
            return [_search_key(project_id, is_active=False)]

    monkeypatch.setattr("contexts.keys.infrastructure.search_repository.SearchKeyRepository", _Repo)

    warnings = await _tool_warnings(AsyncMock(), project_id, _tool(AgentToolType.HOSTED_WEB_SEARCH, {}))

    assert warnings == []


async def test_hosted_mcp_tool_warns_when_reference_host_not_allowlisted(monkeypatch) -> None:
    monkeypatch.setattr(_ALLOW, AsyncMock(return_value=("mcp.example.com", False)))

    tool = _tool(
        AgentToolType.HOSTED_MCP,
        {"source": "url", "reference": "https://mcp.example.com/sse"},
    )
    warnings = await _tool_warnings(AsyncMock(), uuid.uuid4(), tool)

    assert warnings == ["host mcp.example.com is not on the project egress allowlist"]


async def test_hosted_mcp_tool_no_warning_when_host_allowlisted(monkeypatch) -> None:
    monkeypatch.setattr(_ALLOW, AsyncMock(return_value=("mcp.example.com", True)))

    tool = _tool(
        AgentToolType.HOSTED_MCP,
        {"source": "url", "reference": "https://mcp.example.com/sse"},
    )
    warnings = await _tool_warnings(AsyncMock(), uuid.uuid4(), tool)

    assert warnings == []


async def test_hosted_mcp_tool_no_warning_for_package_source(monkeypatch) -> None:
    # No allowlist patch: a package reference has no single knowable host, so
    # the check must be skipped entirely rather than probing a bogus host.
    tool = _tool(AgentToolType.HOSTED_MCP, {"source": "package", "reference": "left-pad@1.0.0"})
    warnings = await _tool_warnings(AsyncMock(), uuid.uuid4(), tool)

    assert warnings == []
