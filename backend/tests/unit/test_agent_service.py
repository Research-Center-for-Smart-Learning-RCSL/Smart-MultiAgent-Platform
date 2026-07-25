"""Unit tests for AgentService ??create, get, patch, soft_delete, MCP bindings.

All infrastructure (repos, facades, advisory locks) is mocked. Tests exercise
the service-layer guardrails: cap enforcement, project isolation checks,
optimistic locking, field mapping, and audit emission.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from contexts.agents.application.agent_service import (
    _AGENT_CAP_PER_PROJECT,
    AgentService,
    _sanitize_captured_description,
    _sanitize_captured_tools,
    _validate_captured_schema,
    _validate_function_config,
    _validate_mcp_config,
)
from contexts.agents.domain.errors import (
    AgentCapExceeded,
    AgentNotFound,
    KeyGroupOutOfProject,
    KnowmapBuilderKeyGroupConflict,
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
    McpToolSpec,
)
from shared_kernel.db.restore import RestoreConflict

_NOW = datetime(2026, 6, 22, 12, 0, 0)
_PROJECT_ID = uuid.uuid4()
_KEY_GROUP_ID = uuid.uuid4()
_USER_ID = uuid.uuid4()


def _function_config(name: str) -> dict:
    return {
        "name": name,
        "description": "d",
        "parameters": {"type": "object"},
        "http": {"method": "GET", "url": "https://api.example.com/v1/do"},
    }


@pytest.mark.parametrize("name", ["cast_approval_vote", "web_search", "code_exec", "update_wakeup", "mcp__x"])
def test_validate_function_config_rejects_reserved_names(name: str) -> None:
    with pytest.raises(ValueError, match="reserved"):
        _validate_function_config(_function_config(name))


def test_validate_function_config_allows_normal_name() -> None:
    _validate_function_config(_function_config("my_custom_tool"))  # no raise


def _make_agent(
    *,
    agent_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    version: int = 1,
    rag_config_id: uuid.UUID | None = None,
    knowmap_config_id: uuid.UUID | None = None,
) -> Agent:
    return Agent(
        id=agent_id or uuid.uuid4(),
        project_id=project_id or _PROJECT_ID,
        name="Test Agent",
        model_hint=AgentModelHint.CLAUDE,
        model_id=None,
        effort=None,
        key_group_id=_KEY_GROUP_ID,
        system_prompt="You are helpful.",
        rag_config_id=rag_config_id,
        knowmap_config_id=knowmap_config_id,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        skill_index_token_cap=None,
        temperature=None,
        top_p=None,
        seed=None,
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
    skills_facade: AsyncMock | None = None,
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
    # Always stubbed, unlike the facades above: this file mocks all infrastructure, and
    # the real SkillsFacade over an AsyncMock db would run live queries.
    if skills_facade is not None:
        svc._skills = skills_facade
    else:
        # `cascade_agent_deleted` is iterated for the audit metadata, so the default needs
        # a real list rather than a Mock. Only defaulted when we own the mock — writing it
        # unconditionally would silently overwrite what a caller configured.
        svc._skills = AsyncMock()
        svc._skills.cascade_agent_deleted.return_value = []
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
        tools.provision_singletons.assert_awaited_once_with(
            agent_id=agent.id,
            web_search=True,
            code_interpreter=False,
            file_workspace=True,
            file_search_enabled=False,
        )

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_create_forwards_sampling(self, _audit) -> None:
        agent = _make_agent()
        agents = AsyncMock()
        agents.count_active.return_value = 0
        agents.create.return_value = agent
        keys = AsyncMock()
        keys.get_key_group.return_value = MagicMock(project_id=_PROJECT_ID)
        tools = AsyncMock()
        svc = _make_service(agent_repo=agents, keys_facade=keys, tool_repo=tools)

        # temperature=0.0 must be persisted as a value, not confused with "unset".
        await svc.create(
            project_id=_PROJECT_ID,
            draft=_make_draft(temperature=0.0, top_p=1.0, seed=42),
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        kwargs = agents.create.call_args.kwargs
        assert kwargs["temperature"] == 0.0
        assert kwargs["top_p"] == 1.0
        assert kwargs["seed"] == 42

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_rag_create_enables_file_search_singleton(self, _audit) -> None:
        rag_id = uuid.uuid4()
        agent = _make_agent(rag_config_id=rag_id)
        agents = AsyncMock()
        agents.count_active.return_value = 0
        agents.create.return_value = agent
        keys = AsyncMock()
        keys.get_key_group.return_value = MagicMock(project_id=_PROJECT_ID)
        knowledge = AsyncMock()
        knowledge.get_rag_config.return_value = MagicMock(project_id=_PROJECT_ID)
        tools = AsyncMock()
        svc = _make_service(
            agent_repo=agents,
            keys_facade=keys,
            knowledge_facade=knowledge,
            tool_repo=tools,
        )

        await svc.create(
            project_id=_PROJECT_ID,
            draft=_make_draft(rag_config_id=rag_id),
            actor_user_id=_USER_ID,
            actor_ip="1.2.3.4",
        )

        tools.provision_singletons.assert_awaited_once_with(
            agent_id=agent.id,
            web_search=True,
            code_interpreter=False,
            file_workspace=True,
            file_search_enabled=True,
        )

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
    async def test_knowmap_builder_key_group_conflict_raises(self, _audit) -> None:
        # R11.01 ??the config's own builder Key Group is the agent's Key
        # Group: build jobs and real-time chat inference would silently
        # share one Key Group's rate limit/budget.
        agents = AsyncMock()
        agents.count_active.return_value = 0
        keys = AsyncMock()
        keys.get_key_group.return_value = MagicMock(project_id=_PROJECT_ID)
        knowledge = AsyncMock()
        knowledge.get_knowmap_config.return_value = MagicMock(
            project_id=_PROJECT_ID, builder_key_group_id=_KEY_GROUP_ID
        )
        svc = _make_service(agent_repo=agents, keys_facade=keys, knowledge_facade=knowledge)
        knowmap_id = uuid.uuid4()

        with pytest.raises(KnowmapBuilderKeyGroupConflict):
            await svc.create(
                project_id=_PROJECT_ID,
                draft=_make_draft(knowmap_config_id=knowmap_id),
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
    async def test_patch_checks_a_lowered_skill_index_cap_against_the_bound_set(self, _audit) -> None:
        """AC-6's third path. The DB CHECK only bounds the value at 16000; it cannot see
        what the agent has bound, and there is no runtime truncation to absorb a cap the
        index already exceeds."""
        current = _make_agent(version=1)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = _make_agent(version=2)
        skills = AsyncMock()
        svc = _make_service(agent_repo=agents, skills_facade=skills)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(skill_index_token_cap=100),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        skills.ensure_index_cap_fits.assert_awaited_once_with(current.id, 100)

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_checks_the_default_when_the_cap_is_cleared(self, _audit) -> None:
        """Clearing the override is not a way out: the 3000 default is a cap like any
        other, and the agent's index may already exceed it."""
        current = _make_agent(version=1)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = _make_agent(version=2)
        skills = AsyncMock()
        svc = _make_service(agent_repo=agents, skills_facade=skills)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(clear_skill_index_token_cap=True),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        skills.ensure_index_cap_fits.assert_awaited_once_with(current.id, None)

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_does_not_check_the_cap_when_it_is_untouched(self, _audit) -> None:
        current = _make_agent(version=1)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = _make_agent(version=2)
        skills = AsyncMock()
        svc = _make_service(agent_repo=agents, skills_facade=skills)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(name="Renamed"),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        skills.ensure_index_cap_fits.assert_not_awaited()

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_aborts_the_write_when_the_cap_does_not_fit(self, _audit) -> None:
        from contexts.skills.domain.errors import SkillIndexBudgetExceeded

        current = _make_agent(version=1)
        agents = AsyncMock()
        agents.get.return_value = current
        skills = AsyncMock()
        skills.ensure_index_cap_fits.side_effect = SkillIndexBudgetExceeded(
            required=2900, cap=100, agent_ids=(current.id,)
        )
        svc = _make_service(agent_repo=agents, skills_facade=skills)

        with pytest.raises(SkillIndexBudgetExceeded):
            await svc.patch(
                agent_id=current.id,
                draft=AgentDraft(skill_index_token_cap=100),
                expected_version=1,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        # The check runs before the write, so a rejected cap leaves no trace.
        agents.patch.assert_not_awaited()

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_sets_sampling(self, _audit) -> None:
        current = _make_agent(version=1)
        updated = _make_agent(version=2)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = updated
        svc = _make_service(agent_repo=agents)

        # temperature=0.0 is a real value (the reproducible-scoring setting), not a clear.
        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(temperature=0.0, top_p=0.9, seed=7),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        values = agents.patch.call_args.kwargs["values"]
        assert values["temperature"] == 0.0
        assert values["top_p"] == 0.9
        assert values["seed"] == 7

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_clears_sampling(self, _audit) -> None:
        current = _make_agent(version=1)
        updated = _make_agent(version=2)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = updated
        svc = _make_service(agent_repo=agents)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(clear_temperature=True, clear_top_p=True, clear_seed=True),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        values = agents.patch.call_args.kwargs["values"]
        assert values["temperature"] is None
        assert values["top_p"] is None
        assert values["seed"] is None

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
    async def test_patch_knowmap_attach_key_group_conflict_raises(self, _audit) -> None:
        current = _make_agent()
        agents = AsyncMock()
        agents.get.return_value = current
        knowledge = AsyncMock()
        knowledge.get_knowmap_config.return_value = MagicMock(
            project_id=_PROJECT_ID, builder_key_group_id=_KEY_GROUP_ID
        )
        svc = _make_service(agent_repo=agents, knowledge_facade=knowledge)
        knowmap_id = uuid.uuid4()

        with pytest.raises(KnowmapBuilderKeyGroupConflict):
            await svc.patch(
                agent_id=current.id,
                draft=AgentDraft(knowmap_config_id=knowmap_id),
                expected_version=1,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_key_group_change_rechecks_knowmap_conflict(self, _audit) -> None:
        # The agent already has a Knowledge Map attached; the patch doesn't
        # touch it, but moves key_group_id onto the config's own builder
        # group ??this must be caught by the implicit recheck, not just an
        # explicit (re)attach.
        knowmap_id = uuid.uuid4()
        current = _make_agent(knowmap_config_id=knowmap_id)
        agents = AsyncMock()
        agents.get.return_value = current
        new_kg = uuid.uuid4()
        keys = AsyncMock()
        keys.get_key_group.return_value = MagicMock(project_id=_PROJECT_ID)
        knowledge = AsyncMock()
        knowledge.get_knowmap_config.return_value = MagicMock(
            project_id=_PROJECT_ID, builder_key_group_id=new_kg
        )
        svc = _make_service(agent_repo=agents, keys_facade=keys, knowledge_facade=knowledge)

        with pytest.raises(KnowmapBuilderKeyGroupConflict):
            await svc.patch(
                agent_id=current.id,
                draft=AgentDraft(key_group_id=new_kg),
                expected_version=1,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_key_group_change_skips_recheck_for_soft_deleted_knowmap(self, _audit) -> None:
        # The attached Knowledge Map was soft-deleted out from under the
        # agent; an unrelated key_group_id edit must not turn into a 404.
        knowmap_id = uuid.uuid4()
        current = _make_agent(knowmap_config_id=knowmap_id)
        updated = _make_agent(knowmap_config_id=knowmap_id, version=2)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = updated
        new_kg = uuid.uuid4()
        keys = AsyncMock()
        keys.get_key_group.return_value = MagicMock(project_id=_PROJECT_ID)
        knowledge = AsyncMock()
        knowledge.get_knowmap_config.return_value = None
        svc = _make_service(agent_repo=agents, keys_facade=keys, knowledge_facade=knowledge)

        result = await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(key_group_id=new_kg),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        assert result.version == 2

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

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_soft_delete_cascades_into_skills(self, _audit) -> None:
        """AC-38. The FK CASCADE in 0056 cannot do this: it fires only on a physical
        DELETE, never on the soft-delete UPDATE, so without an explicit call every
        binding stays live and points at a dead agent (F-18)."""
        agents = AsyncMock()
        skills = AsyncMock()
        skills.cascade_agent_deleted.return_value = [uuid.uuid4()]
        svc = _make_service(agent_repo=agents, skills_facade=skills)
        agent_id = uuid.uuid4()

        await svc.soft_delete(agent_id=agent_id, expected_version=1, actor_user_id=_USER_ID, actor_ip=None)

        skills.cascade_agent_deleted.assert_awaited_once_with(agent_id)

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_soft_delete_records_which_skills_it_unbound(self, _audit) -> None:
        agents = AsyncMock()
        skills = AsyncMock()
        skill_id = uuid.uuid4()
        skills.cascade_agent_deleted.return_value = [skill_id]
        svc = _make_service(agent_repo=agents, skills_facade=skills)

        await svc.soft_delete(
            agent_id=uuid.uuid4(), expected_version=1, actor_user_id=_USER_ID, actor_ip=None
        )

        event = _audit.await_args.args[1]
        assert event.metadata["unbound_skill_ids"] == [str(skill_id)]


class TestAdminRestore:
    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_admin_restore_success(self, audit_emit) -> None:
        agents = AsyncMock()
        agents.restore.return_value = True
        svc = _make_service(agent_repo=agents)
        agent_id = uuid.uuid4()

        ok = await svc.admin_restore(agent_id=agent_id, admin_user_id=_USER_ID, actor_ip=None)

        assert ok is True
        agents.restore.assert_awaited_once_with(agent_id)
        audit_emit.assert_awaited_once()
        assert audit_emit.await_args.args[1].resource_type == "agent"

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_admin_restore_not_soft_deleted_returns_false(self, audit_emit) -> None:
        agents = AsyncMock()
        agents.restore.return_value = False
        svc = _make_service(agent_repo=agents)

        ok = await svc.admin_restore(agent_id=uuid.uuid4(), admin_user_id=_USER_ID, actor_ip=None)

        assert ok is False
        audit_emit.assert_not_awaited()

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_admin_restore_name_reuse_raises_restore_conflict(self, audit_emit) -> None:
        agents = AsyncMock()
        agents.restore.side_effect = IntegrityError(
            "UPDATE ...", {}, Exception('violates unique constraint "uq_agents_project_name_active"')
        )
        svc = _make_service(agent_repo=agents)

        with pytest.raises(RestoreConflict) as info:
            await svc.admin_restore(agent_id=uuid.uuid4(), admin_user_id=_USER_ID, actor_ip=None)
        assert info.value.resource_type == "agent"
        audit_emit.assert_not_awaited()


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
            _validate_mcp_config({"source": "ftp", "reference": "https://x", "allowed_tools": ["a"]})

    def test_rejects_missing_reference(self) -> None:
        with pytest.raises(ValueError, match="reference"):
            _validate_mcp_config({"source": "url", "allowed_tools": ["a"]})

    def test_rejects_empty_allowed_tools(self) -> None:
        # H2: an empty allowlist yields zero runtime tools and must be rejected.
        with pytest.raises(ValueError, match="allowed_tools"):
            _validate_mcp_config({"source": "url", "reference": "https://x", "allowed_tools": []})

    def test_rejects_blank_allowed_tool_entry(self) -> None:
        with pytest.raises(ValueError, match="allowed_tools"):
            _validate_mcp_config({"source": "url", "reference": "https://x", "allowed_tools": [""]})

    def test_accepts_dot_and_colon_in_tool_name(self) -> None:
        # Q-3: `.` and `:` are legal MCP characters and common in the wild (e.g.
        # "filesystem.read_file", "ns:tool") -- rejecting them at bind time would
        # make legitimate MCP servers unbindable. Composition-time sanitisation
        # (builtin_tools.py) is what makes these provider-legal, not this gate.
        _validate_mcp_config(
            {
                "source": "url",
                "reference": "https://x",
                "allowed_tools": ["filesystem.read_file", "ns:tool"],
            }
        )

    def test_rejects_illegal_charset_entry(self) -> None:
        # A raw control character is not legal MCP under any interpretation.
        with pytest.raises(ValueError, match="allowed_tools"):
            _validate_mcp_config(
                {"source": "url", "reference": "https://x", "allowed_tools": ["read\x00file"]}
            )

    def test_rejects_whitespace_in_tool_name(self) -> None:
        with pytest.raises(ValueError, match="allowed_tools"):
            _validate_mcp_config({"source": "url", "reference": "https://x", "allowed_tools": ["read file"]})

    def test_rejects_overlong_allowed_tool_entry(self) -> None:
        with pytest.raises(ValueError, match="allowed_tools"):
            _validate_mcp_config({"source": "url", "reference": "https://x", "allowed_tools": ["a" * 501]})

    def test_accepts_51_char_entry(self) -> None:
        # Legal MCP but unusable after the mcp__{id8}__ prefix (14 chars) -- still
        # bindable; composition-time truncation + digest is what handles the fit,
        # not a bind-time reject (Q-3).
        _validate_mcp_config({"source": "url", "reference": "https://x", "allowed_tools": ["a" * 51]})

    def test_patch_grandfathers_preexisting_illegal_entry(self) -> None:
        # Q-5: editing an unrelated field on a binding whose stored allowed_tools
        # already contains an illegal entry must not 422 -- the exact
        # allow_empty_allowlist regression class (:188-189).
        _validate_mcp_config(
            {"source": "url", "reference": "https://x", "allowed_tools": ["read\x00file"]},
            preexisting_allowed_tools=frozenset({"read\x00file"}),
        )

    def test_patch_rejects_newly_added_illegal_entry(self) -> None:
        with pytest.raises(ValueError, match="allowed_tools"):
            _validate_mcp_config(
                {
                    "source": "url",
                    "reference": "https://x",
                    "allowed_tools": ["read\x00file", "new bad name"],
                },
                preexisting_allowed_tools=frozenset({"read\x00file"}),
            )


# ---------------------------------------------------------------------------
# Captured MCP tool contract (2026-07-22-mcp-tool-contract)
# ---------------------------------------------------------------------------


class TestValidateCapturedSchema:
    def test_accepts_plain_object_schema(self) -> None:
        _validate_captured_schema({"type": "object", "properties": {"path": {"type": "string"}}})

    def test_captured_schema_rejects_ref(self) -> None:
        with pytest.raises(ValueError, match="\\$ref"):
            _validate_captured_schema({"type": "object", "properties": {"x": {"$ref": "#/defs/x"}}})

    def test_captured_schema_rejects_oversize(self) -> None:
        huge_props = {f"p{i}": {"type": "string", "description": "x" * 200} for i in range(100)}
        with pytest.raises(ValueError, match="10 KB"):
            _validate_captured_schema({"type": "object", "properties": huge_props})

    def test_captured_schema_rejects_too_many_properties(self) -> None:
        props = {f"p{i}": {"type": "string"} for i in range(51)}
        with pytest.raises(ValueError, match="properties"):
            _validate_captured_schema({"type": "object", "properties": props})


class TestSanitizeCapturedDescription:
    def test_strips_control_characters(self) -> None:
        assert _sanitize_captured_description("hi\x00\x1fthere\n") == "hithere\n"

    def test_bounds_length(self) -> None:
        assert len(_sanitize_captured_description("x" * 5000)) == 1000


class TestSanitizeCapturedTools:
    def test_valid_tool_passes_through_unchanged(self) -> None:
        specs = (McpToolSpec(name="read_file", description="Read a file.", input_schema={"type": "object"}),)
        result, warnings = _sanitize_captured_tools(specs)
        assert result == specs
        assert warnings == []

    def test_oversized_schema_degrades_to_fallback_with_warning(self) -> None:
        huge_props = {f"p{i}": {"type": "string", "description": "x" * 200} for i in range(100)}
        specs = (McpToolSpec(name="bad_tool", input_schema={"type": "object", "properties": huge_props}),)
        result, warnings = _sanitize_captured_tools(specs)
        assert len(result) == 1
        assert result[0].input_schema == {"type": "object", "additionalProperties": True}
        assert "bad_tool" in warnings[0]

    def test_ref_schema_degrades_to_fallback_with_warning(self) -> None:
        specs = (McpToolSpec(name="bad_tool", input_schema={"$ref": "#/x"}),)
        result, warnings = _sanitize_captured_tools(specs)
        assert result[0].input_schema == {"type": "object", "additionalProperties": True}
        assert len(warnings) == 1

    def test_one_bad_tool_does_not_drop_the_others(self) -> None:
        good = McpToolSpec(name="good_tool", input_schema={"type": "object"})
        bad = McpToolSpec(name="bad_tool", input_schema={"$ref": "#/x"})
        result, warnings = _sanitize_captured_tools((good, bad))
        assert {r.name for r in result} == {"good_tool", "bad_tool"}
        assert len(warnings) == 1

    def test_over_200_tools_truncated_with_warning(self) -> None:
        specs = tuple(McpToolSpec(name=f"t{i}") for i in range(250))
        result, warnings = _sanitize_captured_tools(specs)
        assert len(result) == 200
        assert any("250" in w for w in warnings)


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

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_grandfathers_preexisting_illegal_entry(self, _audit) -> None:
        # Q-5, end-to-end through patch_tool (not just the validator directly):
        # a legacy row already carries an illegal stored entry (predates this
        # fix); editing an unrelated field must not 422 on it.
        agent = _make_agent()
        existing = _make_mcp_tool(agent_id=agent.id, with_auth=False)
        existing.config["allowed_tools"] = ["read\x00file"]
        agents = AsyncMock()
        agents.get.return_value = agent
        tools = AsyncMock()
        tools.get.return_value = existing
        tools.patch.return_value = existing
        svc = _make_service(agent_repo=agents, tool_repo=tools)

        await svc.patch_tool(
            agent_id=agent.id,
            tool_id=existing.id,
            display_name="renamed",
            config={"allowed_tools": ["read\x00file"]},
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        tools.patch.assert_awaited_once()

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_rejects_newly_added_illegal_entry(self, _audit) -> None:
        agent = _make_agent()
        existing = _make_mcp_tool(agent_id=agent.id, with_auth=False)
        existing.config["allowed_tools"] = ["read\x00file"]
        agents = AsyncMock()
        agents.get.return_value = agent
        tools = AsyncMock()
        tools.get.return_value = existing
        svc = _make_service(agent_repo=agents, tool_repo=tools)

        with pytest.raises(ValueError, match="allowed_tools"):
            await svc.patch_tool(
                agent_id=agent.id,
                tool_id=existing.id,
                config={"allowed_tools": ["read\x00file", "new bad name"]},
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

        tools.patch.assert_not_awaited()

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_legacy_empty_allowlist_stays_editable(self, _audit) -> None:
        # A migrated binding backfilled with allowed_tools=[] must remain patchable.
        agent = _make_agent()
        existing = AgentTool(
            id=uuid.uuid4(),
            agent_id=agent.id,
            tool_type=AgentToolType.HOSTED_MCP,
            enabled=True,
            display_name=None,
            config={"source": "url", "reference": "https://x", "allowed_tools": []},
            created_at=_NOW,
        )
        agents = AsyncMock()
        agents.get.return_value = agent
        tools = AsyncMock()
        tools.get.return_value = existing
        tools.patch.return_value = existing
        svc = _make_service(agent_repo=agents, tool_repo=tools)

        await svc.patch_tool(
            agent_id=agent.id,
            tool_id=existing.id,
            config={"advanced": "x"},
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        tools.patch.assert_awaited_once()

    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_clear_auth_drops_stored_credential(self, _audit) -> None:
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
            clear_auth=True,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        patched = tools.patch.await_args.kwargs["config"]
        assert "auth" not in patched
        # Other config is left intact.
        assert patched["reference"] == "https://mcp.example.com"


# ---------------------------------------------------------------------------
# Knowledge Map builder/consumer reconciliation (F-14 / R11.25)
# ---------------------------------------------------------------------------


class TestKnowmapBuilderReconciliation:
    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_detach_clears_colliding_agents_and_audits_each(self, emit) -> None:
        config_id = uuid.uuid4()
        new_group = uuid.uuid4()
        a1, a2 = uuid.uuid4(), uuid.uuid4()
        agents = AsyncMock()
        agents.detach_from_knowmap_config.return_value = [a1, a2]
        svc = _make_service(agent_repo=agents)

        detached = await svc.detach_agents_colliding_with_knowmap_builder(
            knowmap_config_id=config_id,
            new_builder_key_group_id=new_group,
            project_id=_PROJECT_ID,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        assert detached == [a1, a2]
        # Repo query is project-scoped on (config, new builder group).
        agents.detach_from_knowmap_config.assert_awaited_once_with(
            knowmap_config_id=config_id,
            key_group_id=new_group,
            project_id=_PROJECT_ID,
        )
        # One audit per detached agent; metadata carries ids only (no key secret).
        assert emit.await_count == 2
        actions = {c.args[1].action for c in emit.await_args_list}
        assert actions == {"agent.knowmap_detached"}
        meta = emit.await_args_list[0].args[1].metadata
        assert meta["knowmap_config_id"] == str(config_id)
        assert meta["builder_key_group_id"] == str(new_group)

    async def test_detach_no_collision_emits_nothing(self) -> None:
        agents = AsyncMock()
        agents.detach_from_knowmap_config.return_value = []
        svc = _make_service(agent_repo=agents)
        with patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock) as emit:
            detached = await svc.detach_agents_colliding_with_knowmap_builder(
                knowmap_config_id=uuid.uuid4(),
                new_builder_key_group_id=uuid.uuid4(),
                project_id=_PROJECT_ID,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )
        assert detached == []
        emit.assert_not_awaited()

    @patch("contexts.agents.application.agent_service.advisory_xact_lock", new_callable=AsyncMock)
    async def test_detach_acquires_config_lock(self, lock) -> None:
        config_id = uuid.uuid4()
        agents = AsyncMock()
        agents.detach_from_knowmap_config.return_value = []
        svc = _make_service(agent_repo=agents)
        with patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock):
            await svc.detach_agents_colliding_with_knowmap_builder(
                knowmap_config_id=config_id,
                new_builder_key_group_id=uuid.uuid4(),
                project_id=_PROJECT_ID,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )
        lock.assert_awaited_once()
        assert str(config_id) in lock.await_args.args[1]

    @patch("contexts.agents.application.agent_service.advisory_xact_lock", new_callable=AsyncMock)
    @patch("contexts.agents.application.agent_service.audit.emit", new_callable=AsyncMock)
    async def test_patch_attach_acquires_config_lock(self, _audit, lock) -> None:
        # The attach path serialises against a concurrent builder-group change on
        # the same map before validating the collision (AC-9).
        knowmap_id = uuid.uuid4()
        current = _make_agent()
        updated = _make_agent(version=2)
        agents = AsyncMock()
        agents.get.return_value = current
        agents.patch.return_value = updated
        knowledge = AsyncMock()
        # Non-colliding builder group so the attach succeeds past the guard.
        knowledge.get_knowmap_config.return_value = MagicMock(
            project_id=_PROJECT_ID, builder_key_group_id=uuid.uuid4()
        )
        svc = _make_service(agent_repo=agents, knowledge_facade=knowledge)

        await svc.patch(
            agent_id=current.id,
            draft=AgentDraft(knowmap_config_id=knowmap_id),
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        assert any(str(knowmap_id) in c.args[1] for c in lock.await_args_list)
