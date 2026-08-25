"""The draft-read grant against a real PostgreSQL (§32, [R32.03]).

WHY THIS CANNOT BE A UNIT TEST
------------------------------
The unit tier compiles statements with ``literal_binds`` and never executes one
(``backend/CLAUDE.md``), so it cannot see what these three reads and one write
actually *do*. Two things here need the real engine:

- **The shared grantor column.** 0082 deliberately reuses ``granted_by_user_id``
  rather than adding a second one, so `set_draft_grant`'s revoke has to clear it
  only when the *other* grant is also off. That is a second UPDATE with a predicate
  on a sibling column; a unit test would assert the SQL it composed, not the row it
  produced, and the interesting case is precisely the row.
- **The fail-closed null-grantor arm**, which exists because 0082 ships no CHECK for
  it — and it ships none because such a constraint would abort an admin's GDPR
  hard-delete. That claim is only worth anything if `ON DELETE SET NULL` is what the
  schema really does, which is a database fact.

`room_has_draft_reader` is here for the same reason: it is the predicate that decides
whether a room stores unsent text at all, and "the query returns nothing" and "the
query is wrong" are indistinguishable without a row to read.
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
    """``(chatroom_id, agent_id, user_id)`` for one ungranted binding.

    The agent and its key group are torn down explicitly for the reason
    ``test_activity_grant_constraints`` records: ``agents`` holds an ``ON DELETE
    RESTRICT`` FK to ``key_groups``, so leaving them to the project cascade fails the
    whole teardown.
    """
    project_id, user_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    agent_id, key_group_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="draft-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="draft-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(
            key_groups_t.insert().values(id=key_group_id, project_id=project_id, name="draft-itest-kg")
        )
        await session.execute(
            agt.agents.insert().values(
                id=agent_id,
                project_id=project_id,
                name="draft-itest-agent",
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


class TestTheDefaultIsNoAuthority:
    async def test_a_fresh_binding_holds_no_draft_grant(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """[R32.03] default-deny, as the column default rather than as a code path:
        a binding created by any route, now or later, starts with no authority."""
        chatroom_id, agent_id, _ = binding
        async with sessionmaker() as session:
            row = await _row(session, chatroom_id, agent_id)

            assert row.may_read_drafts is False
            assert (
                await ChatroomAgentRepository(session).draft_read_grant(
                    chatroom_id=chatroom_id, agent_id=agent_id
                )
                is None
            )

    async def test_a_room_with_no_granted_binding_has_no_reader(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """AC-1's storage half: this predicate is what makes "a room nobody may read
        stores nothing" true before any Redis key is composed."""
        chatroom_id, _, _ = binding
        async with sessionmaker() as session:
            assert await ChatroomAgentRepository(session).room_has_draft_reader(chatroom_id) is False


class TestWritingAndRevoking:
    async def test_granting_records_the_switch_and_the_grantor(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)

            assert await repo.set_draft_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            grant = await repo.draft_read_grant(chatroom_id=chatroom_id, agent_id=agent_id)
            assert grant is not None
            assert grant.agent_id == agent_id
            assert grant.granted_by_user_id == user_id
            assert await repo.room_has_draft_reader(chatroom_id) is True

    async def test_writing_to_an_unbound_agent_reports_false_and_writes_nothing(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """The contract the route turns into a 404 rather than reporting a grant it
        never wrote."""
        chatroom_id, _, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)

            assert (
                await repo.set_draft_grant(
                    chatroom_id=chatroom_id,
                    agent_id=uuid.uuid4(),
                    granted=True,
                    granted_by_user_id=user_id,
                )
                is False
            )
            await session.commit()

            assert await repo.room_has_draft_reader(chatroom_id) is False

    async def test_revoking_clears_the_grantor_when_no_other_grant_holds_it(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """Unlike the activity grant, a revoke here leaves no residue: there is no
        remembered selection to preserve, so a retained grantor could only keep a
        person named as answerable for an authority nobody holds."""
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            await repo.set_draft_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            await repo.set_draft_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=False, granted_by_user_id=user_id
            )
            await session.commit()

            row = await _row(session, chatroom_id, agent_id)
            assert row.may_read_drafts is False
            assert row.granted_by_user_id is None

    async def test_revoking_leaves_a_live_activity_grant_intact(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """**The reason the clear is conditional.** The two grants share one grantor
        column (0082), so an unconditional clear on a draft revoke would silently make
        the room's activity-control grant inert — `activity_control_grant` returns
        ``None`` on a null grantor — and the teacher would find their facilitator
        agent had lost `start_activity` by revoking something else entirely.
        """
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
            await repo.set_draft_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            await repo.set_draft_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=False, granted_by_user_id=user_id
            )
            await session.commit()

            assert await repo.draft_read_grant(chatroom_id=chatroom_id, agent_id=agent_id) is None
            activity = await repo.activity_control_grant(chatroom_id=chatroom_id, agent_id=agent_id)
            assert activity is not None, "revoking draft reading revoked activity control too"
            assert activity.granted_by_user_id == user_id


class TestTheGrantFailsClosedWithoutAnAnswerablePerson:
    async def test_a_grant_whose_granter_was_erased_confers_nothing(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """The invariant 0082 enforces at read rather than with a CHECK.

        `ON DELETE SET NULL` is simulated directly rather than by deleting the user,
        which the `project` fixture owns: what is under test is the *read*'s treatment
        of a null grantor, and the FK's own behaviour is 0078's territory.
        """
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            await repo.set_draft_grant(
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

            assert await repo.draft_read_grant(chatroom_id=chatroom_id, agent_id=agent_id) is None

    async def test_such_a_room_also_stops_storing_and_stops_disclosing(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """`room_has_draft_reader` has to agree with `draft_read_grant` by
        construction. Deriving it from `may_read_drafts` alone would leave a room
        storing unsent text for a tool that is never offered, and showing a chip that
        claims a reader which does not exist."""
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            await repo.set_draft_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.execute(
                t.chatroom_agents.update()
                .where(t.chatroom_agents.c.chatroom_id == chatroom_id)
                .values(granted_by_user_id=None)
            )
            await session.commit()

            assert await repo.room_has_draft_reader(chatroom_id) is False


class TestTheGrantIsScopedToOneRoom:
    async def test_the_same_agent_bound_elsewhere_holds_nothing_there(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        project: tuple[uuid.UUID, uuid.UUID],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """AC-2's third clause, and the property that makes a per-binding grant
        meaningfully narrower than an agent-level switch: a teacher who trusts this
        agent with their own class's drafts has said nothing about anyone else's."""
        project_id, _ = project
        chatroom_id, agent_id, user_id = binding
        other_workspace, other_room = uuid.uuid4(), uuid.uuid4()
        async with sessionmaker() as session:
            await session.execute(
                t.workspaces.insert().values(id=other_workspace, project_id=project_id, name="draft-itest-2")
            )
            await session.execute(
                t.chatrooms.insert().values(
                    id=other_room,
                    workspace_id=other_workspace,
                    name="draft-itest-2",
                    guest_token=str(uuid.uuid4()),
                    created_by_user_id=user_id,
                )
            )
            await session.execute(
                t.chatroom_agents.insert().values(chatroom_id=other_room, agent_id=agent_id)
            )
            repo = ChatroomAgentRepository(session)
            await repo.set_draft_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            assert await repo.draft_read_grant(chatroom_id=other_room, agent_id=agent_id) is None
            assert await repo.room_has_draft_reader(other_room) is False
            assert await repo.room_has_draft_reader(chatroom_id) is True

    async def test_unbinding_the_agent_takes_the_grant_with_it(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """ "The grant dies with the binding" ([R32.03]) is a property of where the
        column lives, not of a cleanup path someone has to remember to run."""
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            await repo.set_draft_grant(
                chatroom_id=chatroom_id, agent_id=agent_id, granted=True, granted_by_user_id=user_id
            )
            await session.commit()

            await session.execute(
                t.chatroom_agents.delete().where(
                    sa.and_(
                        t.chatroom_agents.c.chatroom_id == chatroom_id,
                        t.chatroom_agents.c.agent_id == agent_id,
                    )
                )
            )
            await session.commit()

            assert await repo.draft_read_grant(chatroom_id=chatroom_id, agent_id=agent_id) is None
            assert await repo.room_has_draft_reader(chatroom_id) is False
