"""FU-6: the register route rejects an mcp validator whose agent/binding lives
in another project (R30.24). The activities context can't see agents, so the
guard runs at the route via AgentsFacade; here it is exercised directly with a
mocked facade.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import activities
from contexts.activities.domain.errors import ValidatorConfigInvalid
from contexts.agents.domain.models import AgentToolType


def _config(agent_id: uuid.UUID, binding_id: uuid.UUID) -> dict[str, str]:
    return {"agent_id": str(agent_id), "binding_id": str(binding_id), "tool_name": "score"}


def _patch_facade(monkeypatch: pytest.MonkeyPatch, facade: MagicMock) -> None:
    monkeypatch.setattr("contexts.agents.interfaces.facade.AgentsFacade", lambda _db: facade)


async def test_foreign_agent_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id, agent_id, binding_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    facade = MagicMock()
    # Agent exists but belongs to a different project.
    facade.get_agent = AsyncMock(return_value=SimpleNamespace(project_id=uuid.uuid4()))
    facade.list_agent_tools = AsyncMock(return_value=[])
    _patch_facade(monkeypatch, facade)

    with pytest.raises(ValidatorConfigInvalid):
        await activities._assert_mcp_binding_in_project(
            MagicMock(), project_id, _config(agent_id, binding_id)
        )
    facade.list_agent_tools.assert_not_awaited()


async def test_binding_not_a_hosted_mcp_tool_on_the_agent_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, agent_id, binding_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    facade = MagicMock()
    facade.get_agent = AsyncMock(return_value=SimpleNamespace(project_id=project_id))
    # A tool with a different id, and one with the right id but the wrong type.
    facade.list_agent_tools = AsyncMock(
        return_value=[
            SimpleNamespace(id=uuid.uuid4(), tool_type=AgentToolType.HOSTED_MCP),
            SimpleNamespace(id=binding_id, tool_type=AgentToolType.HOSTED_WEB_SEARCH),
        ]
    )
    _patch_facade(monkeypatch, facade)

    with pytest.raises(ValidatorConfigInvalid):
        await activities._assert_mcp_binding_in_project(
            MagicMock(), project_id, _config(agent_id, binding_id)
        )


async def test_agent_and_hosted_mcp_binding_in_project_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id, agent_id, binding_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    facade = MagicMock()
    facade.get_agent = AsyncMock(return_value=SimpleNamespace(project_id=project_id))
    facade.list_agent_tools = AsyncMock(
        return_value=[SimpleNamespace(id=binding_id, tool_type=AgentToolType.HOSTED_MCP)]
    )
    _patch_facade(monkeypatch, facade)

    # No raise.
    await activities._assert_mcp_binding_in_project(MagicMock(), project_id, _config(agent_id, binding_id))


async def test_malformed_config_defers_to_type_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    facade = MagicMock()
    facade.get_agent = AsyncMock()
    _patch_facade(monkeypatch, facade)

    # A non-UUID binding_id returns early (the precise 422 is type_service's job)
    # — the guard never touches the agents facade.
    await activities._assert_mcp_binding_in_project(
        MagicMock(), uuid.uuid4(), {"agent_id": str(uuid.uuid4()), "binding_id": "not-a-uuid"}
    )
    facade.get_agent.assert_not_awaited()
