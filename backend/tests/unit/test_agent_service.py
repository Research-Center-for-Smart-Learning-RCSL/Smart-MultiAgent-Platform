"""Unit tests for AgentService — create, get, patch, soft_delete, MCP bindings.

All infrastructure (repos, facades, advisory locks) is mocked. Tests exercise
the service-layer guardrails: cap enforcement, project isolation checks,
optimistic locking, field mapping, and audit emission.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.agents.application.agent_service import (
    _AGENT_CAP_PER_PROJECT,
    AgentService,
    _validate_mcp_config,
)
from contexts.agents.domain.errors import (
    AgentCapExceeded,
    AgentNotFound,
    GraphRagConfigOutOfProject,
    KeyGroupOutOfProject,
    RagConfigOutOfProject,
    ToolNotAvailable,
)
from contexts.agents.domain.models import (
    Agent,
    AgentDraft,
    AgentModelHint,
    AgentTool,
    AgentToolType,
    ContextMode,
    PromptStrategy,
)

_NOW = datetime(2026, 6, 22, 12, 0, 0)
_PROJECT_ID = uuid.uuid4()
_KEY_GROUP_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _make_agent(
    *,
    agent_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    version: int = 1,
    rag_config_id: uuid.UUID | None = None,
    graphrag_config_id: uuid.UUID | None = None,
) -> Agent:
    return Agent(
        id=agent_id or uuid.uuid4(),
        project_id=project_id or _PROJECT_ID,
        name="Test Agent",
        model_hint=AgentModelHint.CLAUDE,
        model_id=None,
        key_group_id=_KEY_GROUP_ID,
        system_prompt="You are helpful.",
        prompt_strategy=PromptStrategy.FULL,
        rag_config_id=rag_config_id,
        graphrag_config_id=graphrag_config_id,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        a2a_enabled=False,
        wakeup_config={},
        wakeup_authored_snapshot=None,
        workflow_capabilities={},
        version=version,
        deleted_at=None,
        created_at=_NOW,
    )


def _make_draft(**overrides) -> AgentDraft:
    defaults = {
        "name": "New Agent",
        "model_hint": AgentModelHint.CLAUDE,
        "key_group_id": _KEY_GROUP_ID,
    }
    defaults.update(overrides)
    return AgentDraft(**defaults)


def _make_service(
    *,
    agent_repo: AsyncMock | None = None,
    tool_repo: AsyncMock | None = None,
    keys_facade: AsyncMock | None = None,
    knowledge_facade: AsyncMock | None = None,
) -> AgentService:
    db = AsyncMock()
    db.execute = AsyncMock()
    svc = AgentService(db)
    if agent_repo is not None:
        svc._agents = agent_repo
    if tool_repo is not None:
        svc._tools = tool_repo
    if keys_facade is not None:
        svc._keys = keys_facade
    if knowledge_facade is not None:
        svc._knowledge = knowledge_facade
    return svc


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------


class TestCreate:
    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_happy_path(self, _audit) -> None:
        agent = _make_agent()
        agents = AsyncMock()
        agents.count_active.return_value = 0
        agents.create.return_value = agent
        keys = AsyncMock()
        group = MagicMock()
        group.project_id = _PROJECT_ID
        keys.get_key_group.return_value = group
        tools = AsyncMock()
        svc = _make_service(agent_repo=agents, keys_facade=keys, tool_repo=tools)

        result = await svc.create(
            project_id=_PROJECT_ID,
            draft=_make_draft(),
            actor_user_id=_USER_ID,
            actor_ip="1.2.3.4",
        )

        assert result.id == agent.id
        agents.create.assert_awaited_once()
        # New agents are seeded with the four singleton hosted tools in one call.
        tools.provision_singletons.assert_awaited_once()

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_cap_exceeded_raises(self, _audit) -> None:
        agents = AsyncMock()
        agents.count_active.return_value = _AGENT_CAP_PER_PROJECT
        svc = _make_service(agent_repo=agents)

        with pytest.raises(AgentCapExceeded):
            await svc.create(
                project_id=_PROJECT_ID,
                draft=_make_draft(),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    async def test_missing_name_raises(self) -> None:
        agents = AsyncMock()
        agents.count_active.return_value = 0
        svc = _make_service(agent_repo=agents)

        with pytest.raises(ValueError, match="name"):
            await svc.create(
                project_id=_PROJECT_ID,
                draft=_make_draft(name=None),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    async def test_blank_name_raises(self) -> None:
        agents = AsyncMock()
        agents.count_active.return_value = 0
        svc = _make_service(agent_repo=agents)

        with pytest.raises(ValueError, match="name"):
            await svc.create(
                project_id=_PROJECT_ID,
                draft=_make_draft(name="   "),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    async def test_missing_model_hint_raises(self) -> None:
        agents = AsyncMock()
        agents.count_active.return_value = 0
        svc = _make_service(agent_repo=agents)

        with pytest.raises(ValueError, match="model_hint"):
            await svc.create(
                project_id=_PROJECT_ID,
                draft=_make_draft(model_hint=None),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    async def test_missing_key_group_raises(self) -> None:
        agents = AsyncMock()
        agents.count_active.return_value = 0
        svc = _make_service(agent_repo=agents)

        with pytest.raises(ValueError, match="key_group_id"):
            await svc.create(
                project_id=_PROJECT_ID,
                draft=_make_draft(key_group_id=None),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_key_group_wrong_project_raises(self, _audit) -> None:
        agents = AsyncMock()
        agents.count_active.return_value = 0
        keys = AsyncMock()
        wrong_group = MagicMock()
        wrong_group.project_id = uuid.uuid4()  # different project
        keys.get_key_group.return_value = wrong_group
        svc = _make_service(agent_repo=agents, keys_facade=keys)

        with pytest.raises(KeyGroupOutOfProject):
            await svc.create(
                project_id=_PROJECT_ID,
                draft=_make_draft(),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_rag_config_wrong_project_raises(self, _audit) -> None:
        agents = AsyncMock()
        agents.count_active.return_value = 0
        keys = AsyncMock()
        keys.get_key_group.return_value = MagicMock(project_id=_PROJECT_ID)
        knowledge = AsyncMock()
        wrong_cfg = MagicMock()
        wrong_cfg.project_id = uuid.uuid4()
        knowledge.get_rag_config.return_value = wrong_cfg
        svc = _make_service(agent_repo=agents, keys_facade=keys, knowledge_facade=knowledge)
        rag_id = uuid.uuid4()

        with pytest.raises(RagConfigOutOfProject):
            await svc.create(
                project_id=_PROJECT_ID,
                draft=_make_draft(rag_config_id=rag_id),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_graphrag_config_wrong_project_raises(self, _audit) -> None:
        agents = AsyncMock()
        agents.count_active.return_value = 0
        keys = AsyncMock()
        keys.get_key_group.return_value = MagicMock(project_id=_PROJECT_ID)
        knowledge = AsyncMock()
        wrong_cfg = MagicMock()
        wrong_cfg.project_id = uuid.uuid4()
        knowledge.get_graphrag_config.return_value = wrong_cfg
        svc = _make_service(agent_repo=agents, keys_facade=keys, knowledge_facade=knowledge)

        with pytest.raises(GraphRagConfigOutOfProject):
            await svc.create(
                project_id=_PROJECT_ID,
                draft=_make_draft(graphrag_config_id=uuid.uuid4()),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )


# ---------------------------------------------------------------------------
# get + list
# ---------------------------------------------------------------------------


class TestGetAndList:
    async def test_get_found(self) -> None:
        agent = _make_agent()
        agents = AsyncMock()
        agents.get.return_value = agent
        svc = _make_service(agent_repo=agents)

        result = await svc.get(agent.id)
        assert result.id == agent.id

    async def test_get_not_found_raises(self) -> None:
        agents = AsyncMock()
        agents.get.return_value = None
        svc = _make_service(agent_repo=agents)

        with pytest.raises(AgentNotFound):
            await svc.get(uuid.uuid4())

    async def test_list_for_project(self) -> None:
        a1, a2 = _make_agent(), _make_agent()
        agents = AsyncMock()
        agents.list_for_project.return_value = [a1, a2]
        svc = _make_service(agent_repo=agents)

        result = await svc.list_for_project(_PROJECT_ID)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# patch
# ---------------------------------------------------------------------------


class TestPatch:
    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_name(self, _audit) -> None:
        current = _make_agent(version=1)
        updated = _make_agent(version=2)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = updated
        svc = _make_service(agent_repo=agents)

        result = await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(name="Renamed"),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        assert result.version == 2
        agents.patch.assert_awaited_once()
        call_values = agents.patch.call_args.kwargs["values"]
        assert call_values["name"] == "Renamed"

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_empty_patch_no_audit(self, mock_audit) -> None:
        current = _make_agent(version=1)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = current
        svc = _make_service(agent_repo=agents)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        mock_audit.assert_not_awaited()

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_key_group_validates_project(self, _audit) -> None:
        current = _make_agent()
        agents = AsyncMock()
        agents.get.return_value = current
        keys = AsyncMock()
        keys.get_key_group.return_value = MagicMock(project_id=uuid.uuid4())
        svc = _make_service(agent_repo=agents, keys_facade=keys)
        new_kg = uuid.uuid4()

        with pytest.raises(KeyGroupOutOfProject):
            await svc.patch(
                agent_id=current.id,
                draft=AgentDraft(key_group_id=new_kg),
                expected_version=1,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_wakeup_system_actor_skips_snapshot(self, _audit) -> None:
        current = _make_agent()
        updated = _make_agent(version=2)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = updated
        svc = _make_service(agent_repo=agents)
        system_actor = uuid.UUID(int=0)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(wakeup_config={"enabled": True}),
            expected_version=1,
            actor_user_id=system_actor,
            actor_ip=None,
        )

        call_values = agents.patch.call_args.kwargs["values"]
        assert "wakeup_config" in call_values
        assert "wakeup_authored_snapshot" not in call_values

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_clear_rag_config(self, _audit) -> None:
        current = _make_agent(rag_config_id=uuid.uuid4())
        updated = _make_agent(rag_config_id=None, version=2)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = updated
        svc = _make_service(agent_repo=agents)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(clear_rag_config=True),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        call_values = agents.patch.call_args.kwargs["values"]
        assert call_values["rag_config_id"] is None


# ---------------------------------------------------------------------------
# soft_delete
# ---------------------------------------------------------------------------


class TestSoftDelete:
    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_soft_delete(self, _audit) -> None:
        agents = AsyncMock()
        svc = _make_service(agent_repo=agents)
        agent_id = uuid.uuid4()

        await svc.soft_delete(
            agent_id=agent_id,
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip="1.2.3.4",
        )

        agents.soft_delete.assert_awaited_once_with(
            agent_id=agent_id,
            expected_version=1,
        )


# ---------------------------------------------------------------------------
# MCP bindings
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# MCP config validation (shared by add_tool / patch_tool)
# ---------------------------------------------------------------------------


class TestValidateMcpConfig:
    def test_accepts_valid(self) -> None:
        _validate_mcp_config(
            {"source": "url", "reference": "https://mcp.example.com", "allowed_tools": ["a"]}
        )

    def test_rejects_bad_source(self) -> None:
        with pytest.raises(ValueError, match="source"):
            _validate_mcp_config(
                {"source": "ftp", "reference": "https://x", "allowed_tools": ["a"]}
            )

    def test_rejects_missing_reference(self) -> None:
        with pytest.raises(ValueError, match="reference"):
            _validate_mcp_config({"source": "url", "allowed_tools": ["a"]})

    def test_rejects_empty_allowed_tools(self) -> None:
        # H2: an empty allowlist yields zero runtime tools and must be rejected.
        with pytest.raises(ValueError, match="allowed_tools"):
            _validate_mcp_config(
                {"source": "url", "reference": "https://x", "allowed_tools": []}
            )

    def test_rejects_blank_allowed_tool_entry(self) -> None:
        with pytest.raises(ValueError, match="allowed_tools"):
            _validate_mcp_config(
                {"source": "url", "reference": "https://x", "allowed_tools": [""]}
            )


# ---------------------------------------------------------------------------
# add_tool
# ---------------------------------------------------------------------------


class TestAddTool:
    async def test_local_shell_rejected(self) -> None:
        """local_shell is not implemented yet; add_tool must raise ToolNotAvailable.

        This test is intentionally a failing-by-design marker: when Local Shell
        is implemented, flip this test to assert success instead.
        """
        agent = _make_agent()
        agents = AsyncMock()
        agents.get.return_value = agent
        tools = AsyncMock()
        svc = _make_service(agent_repo=agents)
        svc._tools = tools

        with pytest.raises(ToolNotAvailable, match="local_shell"):
            await svc.add_tool(
                agent_id=agent.id,
                tool_type=AgentToolType.LOCAL_SHELL,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        tools.add.assert_not_awaited()

    async def test_mcp_empty_allowed_tools_rejected(self) -> None:
        # H2: adding an MCP tool with no allowlist must fail before persisting.
        agent = _make_agent()
        agents = AsyncMock()
        agents.get.return_value = agent
        tools = AsyncMock()
        svc = _make_service(agent_repo=agents, tool_repo=tools)

        with pytest.raises(ValueError, match="allowed_tools"):
            await svc.add_tool(
                agent_id=agent.id,
                tool_type=AgentToolType.HOSTED_MCP,
                config={"source": "url", "reference": "https://x", "allowed_tools": []},
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        tools.add.assert_not_awaited()


# ---------------------------------------------------------------------------
# patch_tool
# ---------------------------------------------------------------------------


def _make_mcp_tool(*, agent_id: uuid.UUID, with_auth: bool = True) -> AgentTool:
    config: dict = {
        "source": "url",
        "reference": "https://mcp.example.com",
        "allowed_tools": ["alpha"],
    }
    if with_auth:
        config["auth"] = {"__sealed__": True, "ciphertext": "opaque"}
    return AgentTool(
        id=uuid.uuid4(),
        agent_id=agent_id,
        tool_type=AgentToolType.HOSTED_MCP,
        enabled=True,
        display_name=None,
        config=config,
        created_at=_NOW,
    )


class TestPatchTool:
    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_mcp_partial_patch_preserves_sealed_auth(self, _audit) -> None:
        # H1: editing allowed_tools without re-sending auth must not drop the secret.
        agent = _make_agent()
        existing = _make_mcp_tool(agent_id=agent.id, with_auth=True)
        agents = AsyncMock()
        agents.get.return_value = agent
        tools = AsyncMock()
        tools.get.return_value = existing
        tools.patch.return_value = existing
        svc = _make_service(agent_repo=agents, tool_repo=tools)

        await svc.patch_tool(
            agent_id=agent.id,
            tool_id=existing.id,
            config={"allowed_tools": ["alpha", "beta"]},
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        patched = tools.patch.await_args.kwargs["config"]
        assert patched["auth"] == existing.config["auth"]
        assert patched["allowed_tools"] == ["alpha", "beta"]
        # Immutable fields are carried over from the stored config.
        assert patched["source"] == "url"
        assert patched["reference"] == "https://mcp.example.com"

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_mcp_patch_revalidates_merged_config(self, _audit) -> None:
        # M2: a patch that makes the merged config invalid must raise, not persist.
        agent = _make_agent()
        existing = _make_mcp_tool(agent_id=agent.id, with_auth=True)
        agents = AsyncMock()
        agents.get.return_value = agent
        tools = AsyncMock()
        tools.get.return_value = existing
        svc = _make_service(agent_repo=agents, tool_repo=tools)

        with pytest.raises(ValueError, match="allowed_tools"):
            await svc.patch_tool(
                agent_id=agent.id,
                tool_id=existing.id,
                config={"allowed_tools": []},
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        tools.patch.assert_not_awaited()
