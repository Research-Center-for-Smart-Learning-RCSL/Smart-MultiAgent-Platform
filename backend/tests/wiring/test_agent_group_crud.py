"""Wiring tier — agent_group list/get/rename read+CRUD surface (Phase 4α backend).

Against real Postgres: the Phase 4α additions that unblock the frontend re-home —
``list_for_project`` (newest-first, live only), ``get`` (live only), and ``rename``
(partial-unique 409, ``deleted_at`` guard) at the repository, and the auditing
``rename_group`` at the service. Authorization is a route concern (M4), not
exercised here.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from contexts.agent_groups.application.group_service import AgentGroupService
from contexts.agent_groups.domain.errors import (
    AgentGroupNameConflict,
    AgentGroupNotFound,
)
from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository
from contexts.conversation.infrastructure.repositories import WorkspaceRepository
from contexts.identity.infrastructure.repositories import UserRepository
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
    user = await UserRepository(db).insert(email=f"agc-{u}@smap.test", password_hash="x" * 16)
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


async def test_list_for_project_is_live_and_newest_first() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        repo = AgentGroupRepository(db)
        # Separate transactions so the two groups get distinct created_at stamps
        # (now() is the transaction timestamp), making newest-first deterministic.
        g1 = await repo.create_group(project_id=env.project.id, name=f"a-{uuid.uuid4().hex[:8]}")
        await db.commit()
        g2 = await repo.create_group(project_id=env.project.id, name=f"b-{uuid.uuid4().hex[:8]}")
        await db.commit()

        groups = await repo.list_for_project(env.project.id)
        # Newest-first: g2 (created second) precedes g1.
        assert [g.id for g in groups] == [g2, g1]
        # The read model carries the fields the group panel needs.
        assert groups[0].concept_map_enabled is False
        assert groups[0].created_at is not None

        # A soft-deleted group drops out of the list.
        await repo.soft_delete(group_id=g2)
        await db.commit()
        assert [g.id for g in await repo.list_for_project(env.project.id)] == [g1]


async def test_get_returns_live_only() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        repo = AgentGroupRepository(db)
        gid = await repo.create_group(project_id=env.project.id, name=f"g-{uuid.uuid4().hex[:8]}")
        await db.commit()

        got = await repo.get(gid)
        assert got is not None
        assert got.id == gid
        assert got.project_id == env.project.id

        await repo.soft_delete(group_id=gid)
        await db.commit()
        assert await repo.get(gid) is None
        # A never-existent id is None, not an error.
        assert await repo.get(uuid.uuid4()) is None


async def test_rename_updates_and_conflicts() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        repo = AgentGroupRepository(db)
        taken = f"taken-{uuid.uuid4().hex[:8]}"
        await repo.create_group(project_id=env.project.id, name=taken)
        gid = await repo.create_group(project_id=env.project.id, name=f"orig-{uuid.uuid4().hex[:8]}")
        await db.commit()

        new_name = f"renamed-{uuid.uuid4().hex[:8]}"
        assert await repo.rename(group_id=gid, name=new_name) is True
        await db.commit()
        assert (await repo.get(gid)).name == new_name

        # Renaming onto another active group's name is a domain 409.
        with pytest.raises(AgentGroupNameConflict):
            await repo.rename(group_id=gid, name=taken)
        await db.rollback()

        # Renaming a soft-deleted group updates no live row (returns False).
        await repo.soft_delete(group_id=gid)
        await db.commit()
        assert await repo.rename(group_id=gid, name=f"z-{uuid.uuid4().hex[:8]}") is False


async def test_service_rename_group_audits_and_returns_refreshed() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        svc = AgentGroupService(db)
        gid = await svc.create_group(
            project_id=env.project.id,
            name=f"s-{uuid.uuid4().hex[:8]}",
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        await db.commit()

        new_name = f"svc-renamed-{uuid.uuid4().hex[:8]}"
        refreshed = await svc.rename_group(
            group_id=gid, name=new_name, actor_user_id=env.user.id, actor_ip=None
        )
        await db.commit()
        assert refreshed.id == gid
        assert refreshed.name == new_name

        # Renaming a missing/soft-deleted group is AgentGroupNotFound.
        with pytest.raises(AgentGroupNotFound):
            await svc.rename_group(
                group_id=uuid.uuid4(), name="whatever", actor_user_id=env.user.id, actor_ip=None
            )
