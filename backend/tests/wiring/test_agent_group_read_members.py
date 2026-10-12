"""Per-member read filter persistence against real Postgres (R11.22, AC-2).

The member-removal cleanup is ``array_remove`` on a ``uuid[]`` column matched
with ``@>``: PostgreSQL-specific SQL, so it needs a real database (see
backend/CLAUDE.md on the unit tier's literal binds).
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from contexts.agent_groups.application.group_service import AgentGroupService
from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository
from shared_kernel.db.session import async_session
from tests.wiring.test_graphrag_delta_union import _seed_agent, _seed_project

pytestmark = pytest.mark.wiring


async def test_set_list_and_strip_on_removal() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        from contexts.keys.infrastructure.group_repository import KeyGroupRepository

        kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        a, b, c = [await _seed_agent(db, env.project.id, kg.id) for _ in range(3)]
        repo = AgentGroupRepository(db)
        gid = await repo.create_group(project_id=env.project.id, name=f"grp-{uuid.uuid4().hex[:8]}")
        for agent in (a, b, c):
            await repo.add_member(group_id=gid, agent_id=agent)
        await db.commit()

        service = AgentGroupService(db)
        with patch("shared_kernel.audit.emit", AsyncMock()):
            await service.set_read_members(
                group_id=gid, agent_id=a, member_ids=[b], actor_user_id=env.user.id, actor_ip=None
            )
            await service.set_read_members(
                group_id=gid, agent_id=c, member_ids=[a, b], actor_user_id=env.user.id, actor_ip=None
            )
        await db.commit()

        reads = {m.agent_id: m.read_member_ids for m in await repo.list_members_with_reads(gid)}
        assert reads == {a: (b,), b: None, c: tuple(sorted((a, b)))}

        # Removing b strips it everywhere in the same transaction. a's list named
        # only b, so it is left empty (reads nothing), never widened to NULL.
        with patch("shared_kernel.audit.emit", AsyncMock()):
            await service.remove_member(group_id=gid, agent_id=b, actor_user_id=env.user.id, actor_ip=None)
        await db.commit()

        reads = {m.agent_id: m.read_member_ids for m in await repo.list_members_with_reads(gid)}
        assert reads == {a: (), c: (a,)}

        # Back to the whole group.
        with patch("shared_kernel.audit.emit", AsyncMock()):
            await service.set_read_members(
                group_id=gid, agent_id=a, member_ids=None, actor_user_id=env.user.id, actor_ip=None
            )
        await db.commit()
        reads = {m.agent_id: m.read_member_ids for m in await repo.list_members_with_reads(gid)}
        assert reads[a] is None
