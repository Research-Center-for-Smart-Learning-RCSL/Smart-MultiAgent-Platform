"""Wiring tier — AgentGroupService membership use-cases (Phase 2b WS2).

Against real Postgres: create emits audit and returns an id; add_member enforces
the per-project trust boundary (a cross-project agent is rejected) and is
idempotent; remove reports deletion; list reflects live state. Authorization is
a route concern (M4), so it is not exercised here.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contexts.agent_groups.application.group_service import AgentGroupService
from contexts.agent_groups.domain.errors import (
    AgentGroupMemberProjectMismatch,
    AgentGroupNameConflict,
    AgentGroupNotFound,
)
from contexts.agents.domain.models import AgentModelHint, ContextMode, PromptStrategy
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.conversation.infrastructure.repositories import WorkspaceRepository
from contexts.identity.infrastructure.repositories import UserRepository
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
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
    user = await UserRepository(db).insert(email=f"gs-{u}@smap.test", password_hash="x" * 16)
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
    await WorkspaceRepository(db).create(project_id=project.id, name=f"ws-{u}")
    return SimpleNamespace(user=user, project=project)


async def _seed_agent(db, project_id: uuid.UUID, kg_id: uuid.UUID) -> uuid.UUID:
    agent = await AgentRepository(db).create(
        project_id=project_id,
        name=f"agent-{uuid.uuid4().hex[:8]}",
        model_hint=AgentModelHint.CLAUDE,
        model_id=None,
        effort=None,
        key_group_id=kg_id,
        system_prompt="deterministic test agent",
        prompt_strategy=PromptStrategy.FULL,
        rag_config_id=None,
        knowmap_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        temperature=None,
        top_p=None,
        seed=None,
        a2a_enabled=False,
        wakeup_config={},
        workflow_capabilities={},
    )
    return agent.id


async def test_create_add_remove_with_tenant_isolation() -> None:
    async with async_session() as db:
        p1 = await _seed_project(db)
        p2 = await _seed_project(db)
        kg1 = await KeyGroupRepository(db).create(project_id=p1.project.id, name="c1")
        kg2 = await KeyGroupRepository(db).create(project_id=p2.project.id, name="c2")
        a1 = await _seed_agent(db, p1.project.id, kg1.id)
        a_other = await _seed_agent(db, p2.project.id, kg2.id)
        await db.commit()

        svc = AgentGroupService(db)
        gid = await svc.create_group(
            project_id=p1.project.id,
            name=f"g-{uuid.uuid4().hex[:8]}",
            actor_user_id=p1.user.id,
            actor_ip=None,
        )
        await db.commit()

        await svc.add_member(group_id=gid, agent_id=a1, actor_user_id=p1.user.id, actor_ip=None)
        # idempotent re-add
        await svc.add_member(group_id=gid, agent_id=a1, actor_user_id=p1.user.id, actor_ip=None)
        await db.commit()
        assert list(await svc.list_members(gid)) == [a1]

        # Trust boundary: an agent from another project cannot join.
        with pytest.raises(AgentGroupMemberProjectMismatch):
            await svc.add_member(group_id=gid, agent_id=a_other, actor_user_id=p1.user.id, actor_ip=None)

        assert (
            await svc.remove_member(group_id=gid, agent_id=a1, actor_user_id=p1.user.id, actor_ip=None)
            is True
        )
        assert (
            await svc.remove_member(group_id=gid, agent_id=a1, actor_user_id=p1.user.id, actor_ip=None)
            is False
        )
        await db.commit()
        assert list(await svc.list_members(gid)) == []


async def test_mutating_missing_group_raises_not_found() -> None:
    async with async_session() as db:
        p1 = await _seed_project(db)
        kg1 = await KeyGroupRepository(db).create(project_id=p1.project.id, name="c1")
        a1 = await _seed_agent(db, p1.project.id, kg1.id)
        await db.commit()

        svc = AgentGroupService(db)
        with pytest.raises(AgentGroupNotFound):
            await svc.add_member(group_id=uuid.uuid4(), agent_id=a1, actor_user_id=p1.user.id, actor_ip=None)


async def test_duplicate_active_group_name_conflicts() -> None:
    async with async_session() as db:
        p1 = await _seed_project(db)
        svc = AgentGroupService(db)
        name = f"dup-{uuid.uuid4().hex[:8]}"
        await svc.create_group(project_id=p1.project.id, name=name, actor_user_id=p1.user.id, actor_ip=None)
        await db.commit()

        # A second active group with the same name in the project is a 409.
        with pytest.raises(AgentGroupNameConflict):
            await svc.create_group(
                project_id=p1.project.id, name=name, actor_user_id=p1.user.id, actor_ip=None
            )
