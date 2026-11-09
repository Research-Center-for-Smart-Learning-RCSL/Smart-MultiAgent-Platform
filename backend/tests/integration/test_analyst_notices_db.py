"""Analyst notices against a real PostgreSQL ([R34.04], [R34.05]).

The cap and the one-pending-notice-per-message rule hold only because the
teacher binding row is locked, and the answer is a conditional UPDATE; both are
properties of a real engine, not of the SQL text the unit tier sees.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.infrastructure import tables as agt
from contexts.conversation.application.notice_service import (
    AnalystNoticeService,
    NoticeCreated,
    NoticeRefusal,
    NoticeRefused,
)
from contexts.conversation.domain.models import NoticeDecision, NoticeErrorKind, SenderType
from contexts.conversation.infrastructure import tables as t
from contexts.keys.infrastructure.tables import key_groups as key_groups_t

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class Room:
    chatroom_id: uuid.UUID
    teacher: uuid.UUID
    peer: uuid.UUID
    analyst: uuid.UUID


@pytest.fixture
async def room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[Room]:
    project_id, user_id = project
    workspace_id, chatroom_id, key_group_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    agents = [uuid.uuid4() for _ in range(3)]
    roles = [("normal", "teacher"), ("normal", "peer"), ("observer", "analyst")]
    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="notice-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="notice-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(
            key_groups_t.insert().values(id=key_group_id, project_id=project_id, name="notice-itest-kg")
        )
        for n, (agent_id, (role, classroom_role)) in enumerate(zip(agents, roles, strict=True)):
            await session.execute(
                agt.agents.insert().values(
                    id=agent_id,
                    project_id=project_id,
                    name=f"notice-itest-{n}",
                    model_hint="claude",
                    key_group_id=key_group_id,
                )
            )
            await session.execute(
                t.chatroom_agents.insert().values(
                    chatroom_id=chatroom_id, agent_id=agent_id, role=role, classroom_role=classroom_role
                )
            )
        await session.commit()
    try:
        yield Room(chatroom_id, agents[0], agents[1], agents[2])
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(agt.agents.delete().where(agt.agents.c.id.in_(agents)))
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == key_group_id))
            await cleanup.commit()


async def _notify(
    session: AsyncSession, room: Room, *, about: uuid.UUID | None = None
) -> NoticeCreated | NoticeRefused:
    return await AnalystNoticeService(session).create(
        chatroom_id=room.chatroom_id,
        analyst_agent_id=room.analyst,
        error_kind=NoticeErrorKind.CONTENT,
        error="0.5 is not larger than 0.75.",
        suggestion="Correct the comparison.",
        about_message_id=about,
    )


async def _agent_message(session: AsyncSession, room: Room, sender: uuid.UUID) -> uuid.UUID:
    mid = uuid.uuid4()
    await session.execute(
        t.messages.insert().values(
            id=mid,
            chatroom_id=room.chatroom_id,
            sender_type=SenderType.AGENT.value,
            sender_id=sender,
            content_md="0.5 is larger than 0.75",
        )
    )
    await session.commit()
    return mid


async def _backdate(session: AsyncSession, notice_id: uuid.UUID, minutes: int) -> None:
    await session.execute(
        t.analyst_notices.update()
        .where(t.analyst_notices.c.id == notice_id)
        .values(created_at=sa.func.now() - timedelta(minutes=minutes))
    )
    await session.commit()


async def _answer(session: AsyncSession, room: Room, notice_id: uuid.UUID) -> object:
    return await AnalystNoticeService(session).respond(
        chatroom_id=room.chatroom_id,
        teacher_agent_id=room.teacher,
        notice_id=notice_id,
        decision=NoticeDecision.ACCEPTED,
        reason="Right, I will correct it.",
    )


class TestCreate:
    async def test_the_fourth_notice_in_ten_minutes_is_refused(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            for _ in range(3):
                assert isinstance(await _notify(session, room), NoticeCreated)
                await session.commit()
            assert await _notify(session, room) == NoticeRefused(NoticeRefusal.RATE_LIMITED)
            notices = await AnalystNoticeService(session).list_for_rooms(chatroom_ids=[room.chatroom_id])
            assert len(notices) == 3
            assert {n.teacher_agent_id for n in notices} == {room.teacher}

    async def test_concurrent_notices_cannot_race_past_the_cap(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            for _ in range(2):
                await _notify(session, room)
            await session.commit()

        async def _one() -> object:
            async with sessionmaker() as session:
                result = await _notify(session, room)
                # Hold the room's notice lock briefly so the other writer has to queue on it.
                await asyncio.sleep(0.1)
                await session.commit()
                return result

        results = await asyncio.gather(_one(), _one())

        assert sorted(type(r).__name__ for r in results) == ["NoticeCreated", "NoticeRefused"]
        async with sessionmaker() as session:
            assert (
                len(await AnalystNoticeService(session).list_for_rooms(chatroom_ids=[room.chatroom_id])) == 3
            )

    async def test_an_uncommitted_notice_does_not_block_the_teachers_binding_row(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        # The analyst's turn holds its notice lock until it commits, which can be
        # minutes; the teacher's wake-up writes to its binding row must not wait.
        async with sessionmaker() as analyst_turn:
            assert isinstance(await _notify(analyst_turn, room), NoticeCreated)
            async with sessionmaker() as other:
                await asyncio.wait_for(
                    other.execute(
                        t.chatroom_agents.update()
                        .where(
                            t.chatroom_agents.c.chatroom_id == room.chatroom_id,
                            t.chatroom_agents.c.agent_id == room.teacher,
                        )
                        .values(classroom_role="teacher")
                    ),
                    timeout=5,
                )
                await other.commit()
            await analyst_turn.commit()

    async def test_one_pending_notice_per_message(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            said = await _agent_message(session, room, room.peer)
            first = await _notify(session, room, about=said)
            await session.commit()
            assert isinstance(first, NoticeCreated)
            assert await _notify(session, room, about=said) == NoticeRefused(NoticeRefusal.DUPLICATE_SUBJECT)
            assert await _answer(session, room, first.notice.id) is not None
            await session.commit()
            # Once answered, the same message may be raised again.
            assert isinstance(await _notify(session, room, about=said), NoticeCreated)
            await session.commit()

    async def test_the_subject_must_be_a_teacher_or_peer_message(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            own = await _agent_message(session, room, room.analyst)
            assert await _notify(session, room, about=own) == NoticeRefused(NoticeRefusal.BAD_SUBJECT)
            assert await _notify(session, room, about=uuid.uuid4()) == NoticeRefused(
                NoticeRefusal.BAD_SUBJECT
            )


class TestAnswer:
    async def test_the_answer_is_recorded_once(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            created = await _notify(session, room)
            await session.commit()
            assert isinstance(created, NoticeCreated)
            service = AnalystNoticeService(session)
            assert [
                n.id
                for n in await service.open_for_teacher(chatroom_id=room.chatroom_id, agent_id=room.teacher)
            ] == [created.notice.id]
            first = await _answer(session, room, created.notice.id)
            await session.commit()
            second = await AnalystNoticeService(session).respond(
                chatroom_id=room.chatroom_id,
                teacher_agent_id=room.teacher,
                notice_id=created.notice.id,
                decision=NoticeDecision.DECLINED,
                reason="changed my mind",
            )
            await session.commit()
            assert first is not None
            assert second is None
            row = (
                await session.execute(
                    t.analyst_notices.select().where(t.analyst_notices.c.id == created.notice.id)
                )
            ).one()
            assert (row.decision, row.reason) == ("accepted", "Right, I will correct it.")
            assert await service.open_for_teacher(chatroom_id=room.chatroom_id, agent_id=room.teacher) == []

    async def test_an_expired_notice_is_neither_offered_nor_answerable(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            created = await _notify(session, room)
            await session.commit()
            assert isinstance(created, NoticeCreated)
            await _backdate(session, created.notice.id, minutes=16)
            service = AnalystNoticeService(session)
            assert await service.open_for_teacher(chatroom_id=room.chatroom_id, agent_id=room.teacher) == []
            assert await _answer(session, room, created.notice.id) is None

    async def test_only_the_teacher_answers(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            created = await _notify(session, room)
            await session.commit()
            assert isinstance(created, NoticeCreated)
            assert (
                await AnalystNoticeService(session).respond(
                    chatroom_id=room.chatroom_id,
                    teacher_agent_id=room.peer,
                    notice_id=created.notice.id,
                    decision=NoticeDecision.ACCEPTED,
                    reason="not mine to answer",
                )
                is None
            )

    async def test_a_half_written_answer_is_refused_by_the_database(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            created = await _notify(session, room)
            await session.commit()
            assert isinstance(created, NoticeCreated)
            with pytest.raises(sa.exc.IntegrityError):
                await session.execute(
                    t.analyst_notices.update()
                    .where(t.analyst_notices.c.id == created.notice.id)
                    .values(decision="accepted")
                )
            await session.rollback()
