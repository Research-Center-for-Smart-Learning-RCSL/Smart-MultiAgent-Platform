"""Wiring tier — agent Concept Map coverage (Phase 4α backend, R11.09).

Against real Postgres: ``list_coverage_for_agent`` is the agent-scoped, read-only
transparency view behind the agent Knowledge tab. Unlike the turn resolver it spans
all the agent's rooms and returns disabled wide maps too, flagged ``active=False``.
Asserts: the three owner kinds are covered and ordered narrow->wide; a chatroom map
is always active while wide maps are active only when their owner enables the opt-in;
and an unrelated agent (in no room/group) sees nothing.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contexts.agent_groups.application.group_service import AgentGroupService
from contexts.agents.domain.models import AgentModelHint, ContextMode
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.conversation.application.workspace_service import WorkspaceService
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    ChatroomRepository,
    WorkspaceRepository,
)
from contexts.identity.infrastructure.repositories import UserRepository
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.knowledge.application.graphrag_config_service import GraphRagConfigService
from contexts.knowledge.domain.graphrag import GraphRagConfigDraft
from contexts.knowledge.infrastructure.graphrag_repositories import GraphRagConfigRepository
from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from shared_kernel.db.session import async_session

pytestmark = pytest.mark.wiring


async def _seed_project(db) -> SimpleNamespace:
    u = uuid.uuid4().hex[:8]
    user = await UserRepository(db).insert(email=f"cov-{u}@smap.test", password_hash="x" * 16)
    org = await OrgRepository(db).create(name=f"org-{u}", creator_user_id=user.id)
    await OrgMemberRepository(db).add(
        org_id=org.id, user_id=user.id, role=OrgMemberRole.OWNER, is_original_creator=True
    )
    project = await ProjectRepository(db).create(
        name=f"proj-{u}", owner_user_id=None, owner_org_id=org.id, created_by_user_id=user.id
    )
    await ProjectMemberRepository(db).add(
        project_id=project.id, user_id=user.id, role=ProjectMemberRole.OWNER
    )
    workspace = await WorkspaceRepository(db).create(project_id=project.id, name=f"ws-{u}")
    return SimpleNamespace(user=user, project=project, workspace=workspace)


async def _seed_agent(db, project_id: uuid.UUID, key_group_id: uuid.UUID) -> uuid.UUID:
    agent = await AgentRepository(db).create(
        project_id=project_id,
        name=f"agent-{uuid.uuid4().hex[:8]}",
        model_hint=AgentModelHint.CLAUDE,
        model_id=None,
        effort=None,
        key_group_id=key_group_id,
        system_prompt="deterministic test agent",
        rag_config_id=None,
        knowmap_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        skill_index_token_cap=None,
        temperature=None,
        top_p=None,
        seed=None,
        a2a_enabled=False,
        wakeup_config={},
        workflow_capabilities={},
    )
    return agent.id


async def _group_with_member(db, *, project_id, user_id, agent_id) -> uuid.UUID:
    svc = AgentGroupService(db)
    gid = await svc.create_group(
        project_id=project_id, name=f"grp-{uuid.uuid4().hex[:8]}", actor_user_id=user_id, actor_ip=None
    )
    await svc.add_member(group_id=gid, agent_id=agent_id, actor_user_id=user_id, actor_ip=None)
    return gid


async def test_coverage_spans_owner_kinds_and_flags_active() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        consumer_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        agent_id = await _seed_agent(db, env.project.id, consumer_kg.id)
        other_id = await _seed_agent(db, env.project.id, consumer_kg.id)
        room = await ChatroomRepository(db).create(workspace_id=env.workspace.id, name="r")
        await ChatroomAgentRepository(db).add(chatroom_id=room.id, agent_id=agent_id)
        gid = await _group_with_member(db, project_id=env.project.id, user_id=env.user.id, agent_id=agent_id)
        await db.commit()

        svc = GraphRagConfigService(db)
        room_cfg = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="chatroom", owner_id=room.id, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        group_cfg = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="agent_group", owner_id=gid, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        ws_cfg = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="workspace", owner_id=env.workspace.id, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        repo = GraphRagConfigRepository(db)
        coverage = await repo.list_coverage_for_agent(agent_id)

        # All three owner kinds cover the agent, ordered narrow->wide.
        assert [e.config.id for e in coverage] == [room_cfg.id, group_cfg.id, ws_cfg.id]
        assert [e.config.owner_kind for e in coverage] == ["chatroom", "agent_group", "workspace"]
        # owner_name is surfaced for display.
        assert coverage[0].owner_name == "r"
        # Chatroom map always active (inherits ACL); wide maps disabled by default.
        assert [e.active for e in coverage] == [True, False, False]

        # Enable both wide layers — coverage now flags them active (still listed).
        await AgentGroupService(db).set_concept_map_enabled(
            group_id=gid, enabled=True, actor_user_id=env.user.id, actor_ip=None
        )
        await WorkspaceService(db).set_concept_map_enabled(
            workspace_id=env.workspace.id, enabled=True, actor_user_id=env.user.id, actor_ip=None
        )
        await db.commit()

        coverage = await repo.list_coverage_for_agent(agent_id)
        assert [e.active for e in coverage] == [True, True, True]

        # An unrelated agent (no room, no group) has no coverage.
        assert await repo.list_coverage_for_agent(other_id) == []


async def test_owner_name_populated_and_owner_options_exclude_configured() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        builder_kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="builder")
        room = await ChatroomRepository(db).create(workspace_id=env.workspace.id, name="room-x")
        # Two groups: one will get a map, one stays a free create-option.
        gid_mapped = await AgentGroupService(db).create_group(
            project_id=env.project.id, name="grp-mapped", actor_user_id=env.user.id, actor_ip=None
        )
        gid_free = await AgentGroupService(db).create_group(
            project_id=env.project.id, name="grp-free", actor_user_id=env.user.id, actor_ip=None
        )
        await db.commit()

        await GraphRagConfigService(db).create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="agent_group", owner_id=gid_mapped, builder_key_group_id=builder_kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        repo = GraphRagConfigRepository(db)

        # owner_name is coalesced from the owning group on the project-scoped read.
        configs = await repo.list_for_project(env.project.id)
        assert [c.owner_name for c in configs] == ["grp-mapped"]

        # Owner options exclude the already-mapped group; the free group + the
        # un-mapped room + workspace remain selectable.
        options = await repo.list_owner_options(env.project.id)
        kinds_ids = {(o.owner_kind, o.owner_id) for o in options}
        assert ("agent_group", gid_free) in kinds_ids
        assert ("agent_group", gid_mapped) not in kinds_ids
        assert ("chatroom", room.id) in kinds_ids
        assert ("workspace", env.workspace.id) in kinds_ids
        # Names are carried for the picker.
        assert all(o.owner_name for o in options)
