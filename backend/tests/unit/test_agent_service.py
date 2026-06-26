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

from contexts.agents.application.agent_service import _AGENT_CAP_PER_PROJECT, AgentService
from contexts.agents.domain.errors import (
    AgentCapExceeded,
    AgentNotFound,
    GraphRagConfigOutOfProject,
    KeyGroupOutOfProject,
    RagConfigOutOfProject,
)
from contexts.agents.domain.models import (
    Agent,
    AgentDraft,
    AgentModelHint,
    ContextMode,
    McpBinding,
    McpSource,
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
    binding_repo: AsyncMock | None = None,
    keys_facade: AsyncMock | None = None,
    knowledge_facade: AsyncMock | None = None,
) -> AgentService:
    db = AsyncMock()
    db.execute = AsyncMock()
    svc = AgentService(db)
    if agent_repo is not None:
        svc._agents = agent_repo
    if binding_repo is not None:
        svc._bindings = binding_repo
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
        bindings = AsyncMock()
        svc = _make_service(agent_repo=agents, keys_facade=keys, binding_repo=bindings)

        result = await svc.create(
            project_id=_PROJECT_ID,
            draft=_make_draft(),
            actor_user_id=_USER_ID,
            actor_ip="1.2.3.4",
        )

        assert result.id == agent.id
        agents.create.assert_awaited_once()
        # Opt-in default: new agents are seeded with web_search + file built-in
        # bindings only — code_exec is omitted so the Code Interpreter is off.
        seeded = {c.kwargs["reference"] for c in bindings.add.await_args_list}
        assert seeded == {"web_search", "file"}
        assert all(c.kwargs["source"] is McpSource.BUILTIN for c in bindings.add.await_args_list)

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_set_builtin_tools_reconciles(self, _audit) -> None:
        # Agent currently has one builtin binding for web_search (explicit mode).
        existing = McpBinding(
            id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            source=McpSource.BUILTIN,
            reference="web_search",
            allowed_tools=(),
            config={},
            created_at=_NOW,
        )
        agents = AsyncMock()
        agents.get.return_value = _make_agent()
        bindings = AsyncMock()
        bindings.list.return_value = [existing]
        svc = _make_service(agent_repo=agents, binding_repo=bindings)

        await svc.set_builtin_tools(
            agent_id=existing.agent_id,
            enabled={"web_search", "code_exec"},
            actor_user_id=_USER_ID,
            actor_ip=None,
        )
        # code_exec is newly added; web_search already exists so it is not re-added
        # and not removed (only non-target builtins would be).
        added_refs = {c.kwargs["reference"] for c in bindings.add.await_args_list}
        assert added_refs == {"code_exec"}
        assert bindings.remove.await_count == 0

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_set_builtin_tools_all_on_clears_bindings(self, _audit) -> None:
        existing = McpBinding(
            id=uuid.uuid4(),
            agent_id=uuid.uuid4(),
            source=McpSource.BUILTIN,
            reference="web_search",
            allowed_tools=(),
            config={},
            created_at=_NOW,
        )
        agents = AsyncMock()
        agents.get.return_value = _make_agent()
        bindings = AsyncMock()
        bindings.list.return_value = [existing]
        svc = _make_service(agent_repo=agents, binding_repo=bindings)

        await svc.set_builtin_tools(
            agent_id=existing.agent_id,
            enabled={"web_search", "code_exec", "file"},
            actor_user_id=_USER_ID,
            actor_ip=None,
        )
        # All three enabled -> remove the explicit binding, add none (legacy all-on).
        assert bindings.add.await_count == 0
        assert bindings.remove.await_count == 1

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


class TestMcpBindings:
    async def test_list_bindings_checks_agent_exists(self) -> None:
        agents = AsyncMock()
        agents.get.return_value = None
        svc = _make_service(agent_repo=agents)

        with pytest.raises(AgentNotFound):
            await svc.list_mcp_bindings(uuid.uuid4())

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_add_binding(self, _audit) -> None:
        agent = _make_agent()
        agents = AsyncMock()
        agents.get.return_value = agent
        binding = MagicMock(spec=McpBinding)
        binding.id = uuid.uuid4()
        bindings = AsyncMock()
        bindings.add.return_value = binding
        svc = _make_service(agent_repo=agents, binding_repo=bindings)

        result = await svc.add_mcp_binding(
            agent_id=agent.id,
            source=McpSource.URL,
            reference="https://example.com/mcp",
            allowed_tools=["tool1"],
            config={},
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        assert result.id == binding.id
        bindings.add.assert_awaited_once()

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_remove_binding(self, _audit) -> None:
        agent = _make_agent()
        agents = AsyncMock()
        agents.get.return_value = agent
        bindings = AsyncMock()
        svc = _make_service(agent_repo=agents, binding_repo=bindings)
        binding_id = uuid.uuid4()

        await svc.remove_mcp_binding(
            agent_id=agent.id,
            binding_id=binding_id,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        bindings.remove.assert_awaited_once_with(agent_id=agent.id, binding_id=binding_id)
