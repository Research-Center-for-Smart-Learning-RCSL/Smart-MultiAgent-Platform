"""Classroom roles and alert reaction times against a real PostgreSQL ([R34.01], [R34.02]).

The one-teacher rule and the role fit are database constraints (0110), and every
"first" timestamp is a conditional UPDATE that only a real engine executes; the
unit tier would assert the SQL text, not the row it leaves behind.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.infrastructure import tables as agt
from contexts.conversation.application.chatroom_service import ChatroomService
from contexts.conversation.application.observation_service import ObservationService
from contexts.conversation.domain.models import (
    AlertActionKind,
    AlertViewSurface,
    ChatroomAgentRole,
    ClassroomRole,
)
from contexts.conversation.infrastructure import tables as t
from contexts.keys.infrastructure.tables import key_groups as key_groups_t

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class Room:
    chatroom_id: uuid.UUID
    creator_id: uuid.UUID
    first: uuid.UUID
    second: uuid.UUID
    observer: uuid.UUID


@pytest.fixture
async def room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[Room]:
    project_id, user_id = project
    workspace_id, chatroom_id, key_group_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    agents = [uuid.uuid4() for _ in range(3)]
    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="collab-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="collab-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(
            key_groups_t.insert().values(id=key_group_id, project_id=project_id, name="collab-itest-kg")
        )
        for n, agent_id in enumerate(agents):
            await session.execute(
                agt.agents.insert().values(
                    id=agent_id,
                    project_id=project_id,
                    name=f"collab-itest-{n}",
                    model_hint="claude",
                    key_group_id=key_group_id,
                )
            )
            await session.execute(
                t.chatroom_agents.insert().values(
                    chatroom_id=chatroom_id,
                    agent_id=agent_id,
                    role="observer" if n == 2 else "normal",
                )
            )
        await session.commit()
    try:
        yield Room(chatroom_id, user_id, agents[0], agents[1], agents[2])
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(agt.agents.delete().where(agt.agents.c.id.in_(agents)))
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == key_group_id))
            await cleanup.commit()


async def _classroom_role(session: AsyncSession, room: Room, agent_id: uuid.UUID) -> str:
    return (
        await session.execute(
            sa.select(t.chatroom_agents.c.classroom_role).where(
                t.chatroom_agents.c.chatroom_id == room.chatroom_id, t.chatroom_agents.c.agent_id == agent_id
            )
        )
    ).scalar_one()


async def _set(session: AsyncSession, room: Room, agent_id: uuid.UUID, role: ClassroomRole) -> str:
    return await ChatroomService(session).set_agent_classroom_role(
        chatroom_id=room.chatroom_id,
        agent_id=agent_id,
        classroom_role=role,
        actor_user_id=room.creator_id,
        actor_ip=None,
    )


class TestClassroomRoles:
    async def test_a_room_has_at_most_one_teacher(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            assert await _set(session, room, room.first, ClassroomRole.TEACHER) == "written"
            await session.commit()
        async with sessionmaker() as session:
            assert await _set(session, room, room.second, ClassroomRole.TEACHER) == "teacher_taken"
            # The savepoint kept the transaction usable after the index refused the row.
            assert await _set(session, room, room.second, ClassroomRole.PEER) == "written"
            await session.commit()
        async with sessionmaker() as session:
            assert await _classroom_role(session, room, room.first) == "teacher"
            assert await _classroom_role(session, room, room.second) == "peer"

    async def test_the_role_must_fit_the_binding(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            assert await _set(session, room, room.first, ClassroomRole.ANALYST) == "mismatch"
            assert await _set(session, room, room.observer, ClassroomRole.PEER) == "mismatch"
            assert await _set(session, room, room.observer, ClassroomRole.ANALYST) == "written"
            await session.commit()

    async def test_the_database_refuses_a_misfit_written_around_the_service(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            with pytest.raises(sa.exc.IntegrityError):
                await session.execute(
                    t.chatroom_agents.update()
                    .where(t.chatroom_agents.c.agent_id == room.observer)
                    .values(classroom_role="teacher")
                )
            await session.rollback()

    async def test_a_binding_role_change_resets_the_classroom_role(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            assert await _set(session, room, room.first, ClassroomRole.TEACHER) == "written"
            await session.commit()
        async with sessionmaker() as session:
            changed = await ChatroomService(session).set_agent_role(
                chatroom_id=room.chatroom_id,
                agent_id=room.first,
                role=ChatroomAgentRole.OBSERVER,
                actor_user_id=room.creator_id,
                actor_ip=None,
            )
            assert changed
            await session.commit()
        async with sessionmaker() as session:
            assert await _classroom_role(session, room, room.first) == "none"


async def _alert(session: AsyncSession, room: Room, *, is_alert: bool = True) -> uuid.UUID:
    obs = await ObservationService(session).record(
        chatroom_id=room.chatroom_id,
        agent_id=room.observer,
        content_md="group B has been stuck for ten minutes",
        trigger="silence_minutes",
        is_alert=is_alert,
        alert_reason="stuck group" if is_alert else None,
    )
    await session.commit()
    return obs.id


async def _obs_row(session: AsyncSession, observation_id: uuid.UUID) -> sa.Row:
    return (
        await session.execute(
            t.agent_observations.select().where(t.agent_observations.c.id == observation_id)
        )
    ).one()


class TestAlertReactionTimes:
    async def test_only_the_first_view_is_kept(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            alert = await _alert(session, room)
            routine = await _alert(session, room, is_alert=False)
            service = ObservationService(session)
            assert (
                await service.record_views(
                    chatroom_id=room.chatroom_id,
                    actor_user_id=room.creator_id,
                    observation_ids=[alert, routine],
                    via=AlertViewSurface.PANEL,
                )
                == 1
            )
            await session.commit()
            first = (await _obs_row(session, alert)).first_viewed_at
        await asyncio.sleep(0.05)
        async with sessionmaker() as session:
            again = await ObservationService(session).record_views(
                chatroom_id=room.chatroom_id,
                actor_user_id=room.creator_id,
                observation_ids=[alert],
                via=AlertViewSurface.DASHBOARD,
            )
            await session.commit()
            row = await _obs_row(session, alert)
            assert again == 0
            assert row.first_viewed_at == first
            assert row.first_viewed_via == "panel"
            assert (await _obs_row(session, routine)).first_viewed_at is None

    async def test_a_view_cannot_reach_another_rooms_alert(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            alert = await _alert(session, room)
            marked = await ObservationService(session).record_views(
                chatroom_id=uuid.uuid4(),
                actor_user_id=room.creator_id,
                observation_ids=[alert],
                via=AlertViewSurface.PANEL,
            )
            await session.commit()
            assert marked == 0
            assert (await _obs_row(session, alert)).first_viewed_at is None

    async def test_a_view_by_someone_other_than_the_creator_records_nothing(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        # An admin passes the route's read gate; their view is not the teacher's.
        async with sessionmaker() as session:
            alert = await _alert(session, room)
            marked = await ObservationService(session).record_views(
                chatroom_id=room.chatroom_id,
                actor_user_id=uuid.uuid4(),
                observation_ids=[alert],
                via=AlertViewSurface.DASHBOARD,
            )
            await session.commit()
            assert marked == 0
            assert (await _obs_row(session, alert)).first_viewed_at is None

    async def test_the_creators_room_action_closes_earlier_alerts_once(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            alert = await _alert(session, room)
            service = ObservationService(session)
            assert (
                await service.record_room_action(
                    chatroom_id=room.chatroom_id,
                    actor_user_id=uuid.uuid4(),
                    kind=AlertActionKind.ROOM_MESSAGE,
                )
                == 0
            )
            assert (
                await service.record_room_action(
                    chatroom_id=room.chatroom_id,
                    actor_user_id=room.creator_id,
                    kind=AlertActionKind.ACTIVITY_STARTED,
                )
                == 1
            )
            await session.commit()
            later = await _alert(session, room)
            await service.record_room_action(
                chatroom_id=room.chatroom_id, actor_user_id=room.creator_id, kind=AlertActionKind.ROOM_MESSAGE
            )
            await session.commit()
            first, second = await _obs_row(session, alert), await _obs_row(session, later)
            assert first.first_action_kind == "activity_started"
            assert second.first_action_kind == "room_message"
            assert second.first_action_at > first.first_action_at

    async def test_deleting_an_alert_is_its_first_action(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            alert = await _alert(session, room)
            await ObservationService(session).delete(
                chatroom_id=room.chatroom_id,
                observation_id=alert,
                actor_user_id=room.creator_id,
                actor_ip=None,
            )
            await session.commit()
            row = await _obs_row(session, alert)
            assert row.deleted_at is not None
            assert row.first_action_kind == "deleted"
            exported = await ObservationService(session).list_alerts(
                chatroom_ids=[room.chatroom_id], include_deleted=True
            )
            listed = await ObservationService(session).list_alerts(chatroom_ids=[room.chatroom_id])
            assert [o.id for o in exported] == [alert]
            assert listed == []
