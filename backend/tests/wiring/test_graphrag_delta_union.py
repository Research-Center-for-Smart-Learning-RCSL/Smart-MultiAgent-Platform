"""Wiring tier — multi-member delta union + provenance (Phase 2b WS1, R11.08/R11.22).

Exercises ``_DbDeltaLoader`` against **real** Postgres: an agent_group build
feed is the DISTINCT union of every member agent's room messages (AC-1) — a
message co-present to two members' rooms is ingested exactly once — and each
message is tagged with a deterministic ``source_member_id`` (the smallest member
id sharing it) so extracted relations carry stable member provenance (R11.22).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.workers.tasks.graphrag import _DbDeltaLoader
from contexts.agents.domain.models import AgentModelHint, ContextMode, PromptStrategy
from contexts.agents.infrastructure.repositories import AgentRepository
from contexts.conversation.domain.models import SenderType
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    ChatroomRepository,
    MessageRepository,
    WorkspaceRepository,
)
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
    user = await UserRepository(db).insert(email=f"du-{u}@smap.test", password_hash="x" * 16)
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
    ws = await WorkspaceRepository(db).create(project_id=project.id, name=f"ws-{u}")
    return SimpleNamespace(user=user, project=project, workspace=ws)


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
        context_mode=ContextMode.GENERAL,
        context_token_cap=None,
        a2a_enabled=False,
        wakeup_config={},
        workflow_capabilities={},
    )
    return agent.id


async def _collect(member_ids: list[uuid.UUID]) -> list:
    loader = _DbDeltaLoader(member_agent_ids=member_ids)
    out: list = []
    async for window in loader.iter_windows(config_id=uuid.uuid4(), since=None, mode="delta"):
        out.extend(window)
    return out


async def test_delta_is_distinct_union_with_deterministic_provenance() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        a1 = await _seed_agent(db, env.project.id, kg.id)
        a2 = await _seed_agent(db, env.project.id, kg.id)

        rooms = ChatroomRepository(db)
        agents_in_room = ChatroomAgentRepository(db)
        msgs = MessageRepository(db)

        room1 = await rooms.create(workspace_id=env.workspace.id, name="r1")
        room2 = await rooms.create(workspace_id=env.workspace.id, name="r2")
        shared = await rooms.create(workspace_id=env.workspace.id, name="shared")
        await agents_in_room.add(chatroom_id=room1.id, agent_id=a1)
        await agents_in_room.add(chatroom_id=room2.id, agent_id=a2)
        # Both members participate in the shared room — the co-presence case.
        await agents_in_room.add(chatroom_id=shared.id, agent_id=a1)
        await agents_in_room.add(chatroom_id=shared.id, agent_id=a2)

        m1 = await msgs.create(
            chatroom_id=room1.id, sender_type=SenderType.USER, sender_id=env.user.id, content_md="only a1"
        )
        m2 = await msgs.create(
            chatroom_id=room2.id, sender_type=SenderType.USER, sender_id=env.user.id, content_md="only a2"
        )
        m_shared = await msgs.create(
            chatroom_id=shared.id, sender_type=SenderType.USER, sender_id=env.user.id, content_md="shared"
        )
        await db.commit()

        collected = await _collect([a1, a2])
        by_id = {m.id: m for m in collected}

        # AC-1: the co-present message is ingested exactly once — the union has
        # no duplicate row for m_shared.
        assert len(collected) == 3
        assert set(by_id) == {m1.id, m2.id, m_shared.id}

        # Provenance: a single-room message is tagged with its only member; the
        # shared message is tagged deterministically with the smallest member id.
        assert by_id[m1.id].source_member_id == a1
        assert by_id[m2.id].source_member_id == a2
        assert by_id[m_shared.id].source_member_id == min(a1, a2)


async def test_removed_member_drops_out_of_the_feed() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        a1 = await _seed_agent(db, env.project.id, kg.id)
        a2 = await _seed_agent(db, env.project.id, kg.id)

        rooms = ChatroomRepository(db)
        agents_in_room = ChatroomAgentRepository(db)
        msgs = MessageRepository(db)
        room2 = await rooms.create(workspace_id=env.workspace.id, name="r2")
        await agents_in_room.add(chatroom_id=room2.id, agent_id=a2)
        await msgs.create(
            chatroom_id=room2.id, sender_type=SenderType.USER, sender_id=env.user.id, content_md="a2 only"
        )
        await db.commit()

        # With a2 in the member set, a2's room message is ingested; dropping a2
        # from the set (the WS4 live-membership revocation) removes it.
        assert len(await _collect([a1, a2])) == 1
        assert await _collect([a1]) == []
