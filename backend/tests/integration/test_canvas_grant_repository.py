"""The canvas-read grant against a real PostgreSQL ([R13.47]).

Mirrors test_draft_grant_repository.py: the shared ``granted_by_user_id``
column, the fail-closed null-grantor arm, and the room-scoped isolation all
need a real engine to prove, not ``literal_binds`` compilation.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.infrastructure import tables as agt
from contexts.conversation.infrastructure import tables as t
from contexts.conversation.infrastructure.repositories.chatroom_repo import ChatroomAgentRepository
from contexts.keys.infrastructure.tables import key_groups as key_groups_t

pytestmark = pytest.mark.db


@pytest.fixture
async def binding(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """``(chatroom_id, agent_id, user_id)`` for one ungranted binding."""
    project_id, user_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    agent_id, key_group_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="canvas-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="canvas-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(
            key_groups_t.insert().values(id=key_group_id, project_id=project_id, name="canvas-itest-kg")
        )
        await session.execute(
            agt.agents.insert().values(
                id=agent_id,
                project_id=project_id,
                name="canvas-itest-agent",
                model_hint="claude",
                key_group_id=key_group_id,
            )
        )
        await session.execute(t.chatroom_agents.insert().values(chatroom_id=chatroom_id, agent_id=agent_id))
        await session.commit()
    try:
        yield chatroom_id, agent_id, user_id
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(agt.agents.delete().where(agt.agents.c.id == agent_id))
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == key_group_id))
            await cleanup.commit()


async def _row(session: AsyncSession, chatroom_id: uuid.UUID, agent_id: uuid.UUID) -> sa.Row:
    return (
        await session.execute(
            t.chatroom_agents.select().where(
                sa.and_(
                    t.chatroom_agents.c.chatroom_id == chatroom_id,
                    t.chatroom_agents.c.agent_id == agent_id,
                )
            )
        )
    ).one()


class TestDefaultIsNoCanvasGrant:
    async def test_fresh_binding_has_no_canvas_grant(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """[R13.47] default-deny: a binding starts with may_read_canvas=False."""
        chatroom_id, agent_id, _ = binding
        async with sessionmaker() as session:
            row = await _row(session, chatroom_id, agent_id)
            assert row.may_read_canvas is False

            grant = await ChatroomAgentRepository(session).canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id
            )
            assert grant is None


class TestGrantAndRevoke:
    async def test_granting_records_switch_and_grantor(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            assert await repo.set_canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            grant = await repo.canvas_read_grant(chatroom_id=chatroom_id, agent_id=agent_id)
            assert grant is not None
            assert grant.agent_id == agent_id
            assert grant.granted_by_user_id == user_id

    async def test_unbound_agent_reports_false(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        chatroom_id, _, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            assert (
                await repo.set_canvas_read_grant(
                    chatroom_id=chatroom_id,
                    agent_id=uuid.uuid4(),
                    granted=True,
                    granted_by_user_id=user_id,
                )
                is False
            )

    async def test_revoking_clears_grantor_when_no_other_grant_holds(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            await repo.set_canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            await repo.set_canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=False, granted_by_user_id=user_id
            )
            await session.commit()

            row = await _row(session, chatroom_id, agent_id)
            assert row.may_read_canvas is False
            assert row.granted_by_user_id is None

    async def test_revoking_canvas_leaves_draft_grant_intact(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """The two grants share one grantor column; revoking canvas must not
        clear the grantor when a draft grant still holds it."""
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            await repo.set_draft_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await repo.set_canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            await repo.set_canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=False, granted_by_user_id=user_id
            )
            await session.commit()

            assert await repo.canvas_read_grant(chatroom_id=chatroom_id, agent_id=agent_id) is None
            draft = await repo.draft_read_grant(chatroom_id=chatroom_id, agent_id=agent_id)
            assert draft is not None, "revoking canvas grant revoked draft grant too"
            assert draft.granted_by_user_id == user_id

    async def test_revoking_canvas_leaves_activity_grant_intact(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        chatroom_id, agent_id, user_id = binding
        type_id = uuid.uuid4()
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            await repo.set_activity_grant(
                chatroom_id=chatroom_id,
                agent_id=agent_id,
                granted=True,
                activity_type_ids=[type_id],
                granted_by_user_id=user_id,
            )
            await repo.set_canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            await repo.set_canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=False, granted_by_user_id=user_id
            )
            await session.commit()

            assert await repo.canvas_read_grant(chatroom_id=chatroom_id, agent_id=agent_id) is None
            activity = await repo.activity_control_grant(chatroom_id=chatroom_id, agent_id=agent_id)
            assert activity is not None, "revoking canvas grant revoked activity control too"


class TestFailClosedWithoutGrantor:
    async def test_null_grantor_confers_nothing(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            await repo.set_canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.execute(
                t.chatroom_agents.update()
                .where(
                    sa.and_(
                        t.chatroom_agents.c.chatroom_id == chatroom_id,
                        t.chatroom_agents.c.agent_id == agent_id,
                    )
                )
                .values(granted_by_user_id=None)
            )
            await session.commit()

            assert await repo.canvas_read_grant(chatroom_id=chatroom_id, agent_id=agent_id) is None


class TestCanvasGrantScopedToRoom:
    async def test_same_agent_bound_elsewhere_holds_nothing(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        project_id, _ = project
        chatroom_id, agent_id, user_id = binding
        other_workspace, other_room = uuid.uuid4(), uuid.uuid4()
        async with sessionmaker() as session:
            await session.execute(
                t.workspaces.insert().values(id=other_workspace, project_id=project_id, name="canvas-itest-2")
            )
            await session.execute(
                t.chatrooms.insert().values(
                    id=other_room,
                    workspace_id=other_workspace,
                    name="canvas-itest-2",
                    guest_token=str(uuid.uuid4()),
                    created_by_user_id=user_id,
                )
            )
            await session.execute(
                t.chatroom_agents.insert().values(chatroom_id=other_room, agent_id=agent_id)
            )
            repo = ChatroomAgentRepository(session)
            await repo.set_canvas_read_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            assert await repo.canvas_read_grant(chatroom_id=other_room, agent_id=agent_id) is None
