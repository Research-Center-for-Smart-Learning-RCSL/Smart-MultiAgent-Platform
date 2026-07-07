"""Wiring tier — agent_group multi-member repository (Phase 2b WS1, R11.07/R11.08).

Exercises :class:`AgentGroupRepository` against **real** Postgres: a group can
hold more than one member (the Phase 1 singleton assumption is relaxed), add is
idempotent on the (group, agent) PK, remove reports whether it deleted, and the
member list is read live and ordered. These are the primitives the owner-centric
create (WS2), the build delta-feed fan-out (WS1), and the layered resolver (WS4)
build on.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository
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
    user = await UserRepository(db).insert(email=f"ag-{u}@smap.test", password_hash="x" * 16)
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


async def _seed_agent(db, project_id: uuid.UUID, key_group_id: uuid.UUID) -> uuid.UUID:
    agent = await AgentRepository(db).create(
        project_id=project_id,
        name=f"agent-{uuid.uuid4().hex[:8]}",
        model_hint=AgentModelHint.CLAUDE,
        model_id=None,
        effort=None,
        key_group_id=key_group_id,
        system_prompt="deterministic test agent",
        prompt_strategy=PromptStrategy.FULL,
        rag_config_id=None,
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        a2a_enabled=False,
        wakeup_config={},
        workflow_capabilities={},
    )
    return agent.id


async def test_group_holds_multiple_members_ordered_and_live() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        a1 = await _seed_agent(db, env.project.id, kg.id)
        a2 = await _seed_agent(db, env.project.id, kg.id)
        repo = AgentGroupRepository(db)
        gid = await repo.create_group(project_id=env.project.id, name=f"grp-{uuid.uuid4().hex[:8]}")
        await db.commit()

        # Phase 1 singleton relaxed: two members coexist.
        await repo.add_member(group_id=gid, agent_id=a1)
        await repo.add_member(group_id=gid, agent_id=a2)
        # add is idempotent on the (group, agent) PK — re-adding is a no-op.
        await repo.add_member(group_id=gid, agent_id=a1)
        await db.commit()

        members = await repo.list_member_agent_ids(gid)
        assert sorted(members) == sorted([a1, a2])
        # ordered by agent_id for a deterministic build feed.
        assert list(members) == sorted(members)

        assert await repo.project_id_of(gid) == env.project.id


async def test_remove_member_reports_deletion_and_revokes_live() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        a1 = await _seed_agent(db, env.project.id, kg.id)
        a2 = await _seed_agent(db, env.project.id, kg.id)
        repo = AgentGroupRepository(db)
        gid = await repo.create_group(project_id=env.project.id, name=f"grp-{uuid.uuid4().hex[:8]}")
        await repo.add_member(group_id=gid, agent_id=a1)
        await repo.add_member(group_id=gid, agent_id=a2)
        await db.commit()

        assert await repo.remove_member(group_id=gid, agent_id=a1) is True
        # Removing a non-member reports False rather than erroring.
        assert await repo.remove_member(group_id=gid, agent_id=a1) is False
        await db.commit()

        # The list is read live, so the removed agent is gone on the next call —
        # the property the WS4 resolver relies on for revoking read access.
        assert list(await repo.list_member_agent_ids(gid)) == [a2]
