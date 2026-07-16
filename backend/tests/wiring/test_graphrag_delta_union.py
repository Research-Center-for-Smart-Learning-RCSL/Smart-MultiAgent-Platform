"""Wiring tier — per-owner-kind delta scoping + member provenance
(Phase 2b WS1/WS2, R11.08/R11.22).

Exercises ``_resolve_delta_scope`` + ``_DbDeltaLoader`` against **real** Postgres:
- agent_group — DISTINCT union of every member agent's room messages (AC-1), a
  message co-present to two members ingested exactly once, each tagged with a
  deterministic ``source_member_id`` (smallest member id sharing it, R11.22);
- chatroom — that room's messages only, no member provenance;
- workspace — every room in the workspace, no member provenance (WS2 scoping).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.workers.tasks.graphrag import _DbDeltaLoader, _resolve_delta_scope
from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository
from contexts.agents.domain.models import AgentModelHint, ContextMode
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


def _owner(kind: str, **ids: uuid.UUID | None) -> SimpleNamespace:
    return SimpleNamespace(
        owner_kind=kind,
        owner_agent_group_id=ids.get("agent_group"),
        owner_chatroom_id=ids.get("chatroom"),
        owner_workspace_id=ids.get("workspace"),
    )


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


async def _collect(db, owner: SimpleNamespace) -> list:
    scope = await _resolve_delta_scope(db, owner)
    loader = _DbDeltaLoader(scope=scope)
    out: list = []
    async for window in loader.iter_windows(config_id=uuid.uuid4(), since=None, mode="delta"):
        out.extend(window)
    return out


async def test_agent_group_delta_is_distinct_union_with_provenance() -> None:
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

        ag = AgentGroupRepository(db)
        gid = await ag.create_group(project_id=env.project.id, name=f"grp-{uuid.uuid4().hex[:8]}")
        await ag.add_member(group_id=gid, agent_id=a1)
        await ag.add_member(group_id=gid, agent_id=a2)

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

        owner = _owner("agent_group", agent_group=gid)
        collected = await _collect(db, owner)
        by_id = {m.id: m for m in collected}

        # AC-1: the co-present message is ingested exactly once.
        assert len(collected) == 3
        assert set(by_id) == {m1.id, m2.id, m_shared.id}
        assert by_id[m1.id].source_member_id == a1
        assert by_id[m2.id].source_member_id == a2
        assert by_id[m_shared.id].source_member_id == min(a1, a2)

        # Removing a2 revokes its room feed on the next resolve (live membership).
        await ag.remove_member(group_id=gid, agent_id=a2)
        await db.commit()
        remaining = await _collect(db, owner)
        assert {m.id for m in remaining} == {m1.id, m_shared.id}


async def test_chatroom_and_workspace_scopes() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        rooms = ChatroomRepository(db)
        msgs = MessageRepository(db)
        room_a = await rooms.create(workspace_id=env.workspace.id, name="a")
        room_b = await rooms.create(workspace_id=env.workspace.id, name="b")
        ma = await msgs.create(
            chatroom_id=room_a.id, sender_type=SenderType.USER, sender_id=env.user.id, content_md="a"
        )
        mb = await msgs.create(
            chatroom_id=room_b.id, sender_type=SenderType.USER, sender_id=env.user.id, content_md="b"
        )
        await db.commit()

        # A chatroom owner ingests only its own room, with no member provenance.
        chat = await _collect(db, _owner("chatroom", chatroom=room_a.id))
        assert {m.id for m in chat} == {ma.id}
        assert chat[0].source_member_id is None

        # A workspace owner ingests every room in the workspace.
        ws = await _collect(db, _owner("workspace", workspace=env.workspace.id))
        assert {m.id for m in ws} == {ma.id, mb.id}
        assert all(m.source_member_id is None for m in ws)
