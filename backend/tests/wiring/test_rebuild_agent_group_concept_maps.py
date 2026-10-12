"""The rebuild command's listing against real Postgres (AC-4).

Lists every live agent_group Concept Map whose group is live, and counts the
messages a full build would read: the live members' rooms, each message once.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from contexts.agent_groups.application.group_service import AgentGroupService
from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository
from contexts.conversation.domain.models import SenderType
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    ChatroomRepository,
    MessageRepository,
)
from contexts.keys.infrastructure.group_repository import KeyGroupRepository
from contexts.knowledge.application.graphrag_config_service import GraphRagConfigService
from contexts.knowledge.domain.graphrag import GraphRagConfigDraft
from shared_kernel.db.session import async_session
from smap.maintenance import rebuild_agent_group_concept_maps as cmd
from tests.wiring.test_graphrag_delta_union import _seed_agent, _seed_project

pytestmark = pytest.mark.wiring


async def test_lists_live_group_maps_with_their_message_counts() -> None:
    async with async_session() as db:
        env = await _seed_project(db)
        kg = await KeyGroupRepository(db).create(project_id=env.project.id, name="consumer")
        a1 = await _seed_agent(db, env.project.id, kg.id)
        a2 = await _seed_agent(db, env.project.id, kg.id)
        rooms = ChatroomRepository(db)
        shared = await rooms.create(workspace_id=env.workspace.id, name="shared")
        own = await rooms.create(workspace_id=env.workspace.id, name="own")
        bindings = ChatroomAgentRepository(db)
        await bindings.add(chatroom_id=shared.id, agent_id=a1)
        await bindings.add(chatroom_id=shared.id, agent_id=a2)
        await bindings.add(chatroom_id=own.id, agent_id=a1)
        msgs = MessageRepository(db)
        for room in (shared, shared, own):
            await msgs.create(
                chatroom_id=room.id, sender_type=SenderType.USER, sender_id=env.user.id, content_md="x"
            )

        groups = AgentGroupRepository(db)
        live_gid = await groups.create_group(project_id=env.project.id, name=f"live-{uuid.uuid4().hex[:8]}")
        gone_gid = await groups.create_group(project_id=env.project.id, name=f"gone-{uuid.uuid4().hex[:8]}")
        for gid in (live_gid, gone_gid):
            await groups.add_member(group_id=gid, agent_id=a1)
        await groups.add_member(group_id=live_gid, agent_id=a2)
        await db.commit()

        svc = GraphRagConfigService(db)
        live = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="agent_group", owner_id=live_gid, builder_key_group_id=kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        gone = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(
                owner_kind="agent_group", owner_id=gone_gid, builder_key_group_id=kg.id
            ),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        room_map = await svc.create(
            project_id=env.project.id,
            draft=GraphRagConfigDraft(owner_kind="chatroom", owner_id=own.id, builder_key_group_id=kg.id),
            actor_user_id=env.user.id,
            actor_ip=None,
        )
        with patch("shared_kernel.audit.emit", AsyncMock()):
            await AgentGroupService(db).soft_delete(
                group_id=gone_gid, actor_user_id=env.user.id, actor_ip=None
            )
        await db.commit()

    by_config = {gm.config_id: gm for gm in await cmd.list_group_maps()}
    # The live group's map, with 3 messages: the shared room's two counted once
    # each although both members are bound there, and a1's own room.
    assert by_config[live.id].messages == 3
    assert by_config[live.id].agent_group_id == live_gid
    # Neither a deleted group's map nor a chatroom map is a rebuild target.
    assert gone.id not in by_config
    assert room_map.id not in by_config
