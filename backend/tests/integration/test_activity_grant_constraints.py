"""0078's grant invariants, against a real PostgreSQL (AC-3, [R30.37]).

WHY THIS CANNOT BE A UNIT TEST
------------------------------
``ck_chatroom_agents_activity_grant`` is written in terms of
``jsonb_array_length``, which only PostgreSQL can evaluate. The unit tier compiles
statements with ``literal_binds`` and never executes one, so "the constraint was
created" and "the constraint was never created" are indistinguishable there
(``backend/CLAUDE.md``). The route-level refusal has its own unit test; this is the
half that proves a direct write cannot get around it.

WHAT IS ENFORCED WHERE
----------------------
The grant lives on ``chatroom_agents`` as three loose columns rather than in a
companion table, which admits three inconsistent states. They are closed in three
different places, and which place matters:

- *granted with an empty allowlist* — closed by ``ck_chatroom_agents_activity_grant``.
  Authority over nothing still reads as authority in every listing.
- *granted with no grantor* — closed at **read time**, not by a constraint. A CHECK
  here would abort an admin's GDPR hard-delete of any user who had ever granted
  activity control; see the test below, which is the regression for it.
- *not granted, allowlist left behind* — deliberately legal, and asserted here as
  such, because it is what preserves a teacher's selection across a revoke and
  re-grant. An accidental "tidy-up" CHECK on it would be a regression, not a
  hardening.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.agents.infrastructure import tables as agt
from contexts.conversation.infrastructure import tables as t
from contexts.keys.infrastructure.tables import key_groups as key_groups_t

pytestmark = pytest.mark.db


@pytest.fixture
async def binding(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
    """``(chatroom_id, agent_id, user_id)`` for one ungranted binding.

    The agent and its key group are torn down explicitly: ``agents`` holds an
    ``ON DELETE RESTRICT`` FK to ``key_groups``, so leaving them to the project
    cascade fails the whole teardown (the same reason
    ``test_agent_tools_singleton_upsert`` does it by hand). The workspace,
    chatroom and binding do ride the cascade.
    """
    project_id, user_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    agent_id, key_group_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="grant-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="grant-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(
            key_groups_t.insert().values(id=key_group_id, project_id=project_id, name="grant-itest-kg")
        )
        await session.execute(
            agt.agents.insert().values(
                id=agent_id,
                project_id=project_id,
                name="grant-itest-agent",
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


async def _grant(
    session: AsyncSession,
    chatroom_id: uuid.UUID,
    agent_id: uuid.UUID,
    **values: object,
) -> None:
    await session.execute(
        t.chatroom_agents.update()
        .where(
            sa.and_(
                t.chatroom_agents.c.chatroom_id == chatroom_id,
                t.chatroom_agents.c.agent_id == agent_id,
            )
        )
        .values(**values)
    )


class TestActivityGrantConstraints:
    async def test_a_grant_with_an_empty_allowlist_is_rejected(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """AC-3's storage half — the state the route also refuses with a 422."""
        chatroom_id, agent_id, user_id = binding
        async with sessionmaker() as session:
            # `session.execute` on a Core UPDATE issues the statement immediately,
            # so the violation surfaces here rather than at a later flush.
            with pytest.raises(IntegrityError, match="ck_chatroom_agents_activity_grant"):
                await _grant(
                    session,
                    chatroom_id,
                    agent_id,
                    may_control_activities=True,
                    activity_type_allowlist=[],
                    granted_by_user_id=user_id,
                )
            await session.rollback()

    async def test_deleting_the_granter_makes_the_grant_inert_rather_than_blocking(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """The regression test for the constraint 0078 deliberately does **not** ship.

        A CHECK requiring a live grant to name its grantor is the obvious partner to
        the allowlist one, and it makes ``ON DELETE SET NULL`` unsafe:
        ``AdminService.hard_delete_user`` issues ``DELETE FROM users``, the SET NULL
        fires, and the constraint aborts the whole GDPR erasure — for any user who
        ever granted activity control, with no way to clear the grant first, because
        revoking is the room creator's act and that account is already soft-deleted.

        So the delete must succeed, and the grant must go inert. Both halves are
        asserted: the second is what makes the first safe.
        """
        from contexts.conversation.infrastructure.repositories import ChatroomAgentRepository
        from contexts.identity.infrastructure.tables import users as users_t

        chatroom_id, agent_id, _user_id = binding
        granter = uuid.uuid4()
        async with sessionmaker() as session:
            await session.execute(
                users_t.insert().values(
                    id=granter, email=f"granter-{granter}@test.invalid", password_hash="x"
                )
            )
            repo = ChatroomAgentRepository(session)
            assert await repo.set_activity_grant(
                chatroom_id=chatroom_id,
                agent_id=agent_id,
                granted=True,
                activity_type_ids=[uuid.uuid4()],
                granted_by_user_id=granter,
            )
            await session.commit()
            assert (await repo.activity_control_grant(chatroom_id=chatroom_id, agent_id=agent_id)) is not None

            # The step that used to raise.
            await session.execute(users_t.delete().where(users_t.c.id == granter))
            await session.commit()

            rows = await repo.list(chatroom_id)
            # The binding survives — erasing a user must not unbind an agent — and
            # the switch is still on, because nothing rewrote it...
            assert rows[0].may_control_activities is True
            assert rows[0].granted_by_user_id is None
            # ...but the grant no longer resolves, so it supplies no tools.
            assert (await repo.activity_control_grant(chatroom_id=chatroom_id, agent_id=agent_id)) is None

    async def test_a_revoked_grant_may_keep_its_allowlist(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """The state the schema deliberately permits: the teacher's selection
        survives a revoke, and only ``may_control_activities`` confers authority.

        The read side is what makes this safe, and it is asserted here rather than
        assumed — ``activity_control_grant`` returns ``None`` for this row.
        """
        from contexts.conversation.infrastructure.repositories import ChatroomAgentRepository

        chatroom_id, agent_id, user_id = binding
        type_id = uuid.uuid4()
        async with sessionmaker() as session:
            await _grant(
                session,
                chatroom_id,
                agent_id,
                may_control_activities=False,
                activity_type_allowlist=[str(type_id)],
                granted_by_user_id=user_id,
            )
            await session.commit()

            repo = ChatroomAgentRepository(session)
            assert (await repo.activity_control_grant(chatroom_id=chatroom_id, agent_id=agent_id)) is None
            # The residue is still there — that is the point of permitting it.
            rows = await repo.list(chatroom_id)
            assert rows[0].may_control_activities is False
            assert rows[0].activity_type_allowlist == (type_id,)

    async def test_a_live_grant_round_trips(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        binding: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """The positive case, through the repository the whole feature reads with —
        so the ``jsonb`` round trip of the allowlist is exercised, not assumed."""
        from contexts.conversation.infrastructure.repositories import ChatroomAgentRepository

        chatroom_id, agent_id, user_id = binding
        type_ids = [uuid.uuid4(), uuid.uuid4()]
        async with sessionmaker() as session:
            repo = ChatroomAgentRepository(session)
            assert await repo.set_activity_grant(
                chatroom_id=chatroom_id,
                agent_id=agent_id,
                granted=True,
                activity_type_ids=type_ids,
                granted_by_user_id=user_id,
            )
            await session.commit()

            grant = await repo.activity_control_grant(chatroom_id=chatroom_id, agent_id=agent_id)
            assert grant is not None
            assert grant.agent_id == agent_id
            assert grant.granted_by_user_id == user_id
            assert list(grant.activity_type_ids) == type_ids
