"""0081's two database-enforced invariants — AC-1 and AC-7.

WHY NEITHER CAN BE A UNIT TEST
------------------------------
The unit tier compiles statements with ``literal_binds`` and never executes one
(``backend/CLAUDE.md``), so a CHECK constraint and a partial unique index are
both invisible to it: "the constraint was created" and "the constraint was never
created" produce identical SQL text. AC-7 needs more than execution — it needs
two writers contending, which only a real database can arbitrate.

WHAT THEY PIN
-------------
AC-1: a session has exactly one subject. Relaxing ``subject_user_id``'s NOT NULL
is the riskiest part of this migration, and ``ck_activity_sessions_one_subject``
is what replaces it. Three states are asserted — both set, neither set, and each
one alone — because a CHECK that only rejects "neither" would let a session claim
to belong to a person *and* a group at once, and every reader would pick a
different one.

AC-7: at most one open proposal per (activation, group). This is prevented by
``uq_activity_group_proposals_open``, **not** by the application: two competing
proposals split a group's votes so neither can pass, and a read-then-write guard
is one both racers pass.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.activities.infrastructure import tables as at
from contexts.activities.infrastructure.repositories.proposal_repo import GroupProposalRepository
from contexts.conversation.infrastructure import tables as t
from shared_kernel.auth.clients import now

pytestmark = pytest.mark.db


@pytest.fixture
async def room_round(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """``(activation_id, activity_type_id, user_id)`` for one live round.

    Everything rides the project cascade except the activity type, which is
    project-scoped and therefore does too. Nothing here holds a RESTRICT FK, so
    no explicit teardown is needed beyond the project fixture's own.
    """
    project_id, user_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    type_id, activation_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="group-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="group-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(
            at.activity_types.insert().values(
                id=type_id,
                project_id=project_id,
                key="group-itest-type",
                name="group itest type",
                validator_kind="in_process",
                validator_config={"validator_id": "filled_count", "min_filled": 1},
                group_config={"consent": {"numerator": 2, "denominator": 3}},
            )
        )
        await session.execute(
            at.activity_activations.insert().values(
                id=activation_id,
                chatroom_id=chatroom_id,
                activity_type_id=type_id,
                started_by_user_id=user_id,
            )
        )
        await session.commit()
    return activation_id, type_id, user_id


def _session_values(
    *, activation_id: uuid.UUID, type_id: uuid.UUID, chatroom_id: uuid.UUID
) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "activity_type_id": type_id,
        "chatroom_id": chatroom_id,
        "activation_id": activation_id,
    }


async def _chatroom_of(session: AsyncSession, activation_id: uuid.UUID) -> uuid.UUID:
    row = (
        await session.execute(
            sa.select(at.activity_activations.c.chatroom_id).where(
                at.activity_activations.c.id == activation_id
            )
        )
    ).one()
    return uuid.UUID(str(row.chatroom_id))


class TestOneSubjectPerSession:
    """AC-1."""

    async def test_a_session_with_neither_subject_is_rejected(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        activation_id, type_id, _ = room_round
        async with sessionmaker() as session:
            chatroom_id = await _chatroom_of(session, activation_id)
            with pytest.raises(IntegrityError, match="ck_activity_sessions_one_subject"):
                await session.execute(
                    at.activity_sessions.insert().values(
                        **_session_values(
                            activation_id=activation_id, type_id=type_id, chatroom_id=chatroom_id
                        )
                    )
                )
            await session.rollback()

    async def test_a_session_with_both_subjects_is_rejected(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """The half a "must have a subject" NOT NULL could never have caught."""
        activation_id, type_id, user_id = room_round
        async with sessionmaker() as session:
            chatroom_id = await _chatroom_of(session, activation_id)
            with pytest.raises(IntegrityError, match="ck_activity_sessions_one_subject"):
                await session.execute(
                    at.activity_sessions.insert().values(
                        **_session_values(
                            activation_id=activation_id, type_id=type_id, chatroom_id=chatroom_id
                        ),
                        subject_user_id=user_id,
                        subject_member_group_id=uuid.uuid4(),
                    )
                )
            await session.rollback()

    async def test_either_subject_alone_is_accepted(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """Both populations coexist under one activation, which is [R30.39]: a
        member's own session and their group's are separate rows."""
        activation_id, type_id, user_id = room_round
        async with sessionmaker() as session:
            chatroom_id = await _chatroom_of(session, activation_id)
            await session.execute(
                at.activity_sessions.insert().values(
                    **_session_values(activation_id=activation_id, type_id=type_id, chatroom_id=chatroom_id),
                    subject_user_id=user_id,
                )
            )
            await session.execute(
                at.activity_sessions.insert().values(
                    **_session_values(activation_id=activation_id, type_id=type_id, chatroom_id=chatroom_id),
                    subject_member_group_id=uuid.uuid4(),
                )
            )
            await session.commit()

        async with sessionmaker() as read:
            count = (
                await read.execute(
                    sa.select(sa.func.count()).where(at.activity_sessions.c.activation_id == activation_id)
                )
            ).scalar_one()
        assert count == 2

    async def test_one_group_holds_one_session_per_round(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """``uq_activity_sessions_activation_group`` is the group-side twin of
        0077's per-subject unique, and it is what makes a group's attempt
        sequence continuous instead of restarting on a concurrent open."""
        activation_id, type_id, _ = room_round
        group_id = uuid.uuid4()
        async with sessionmaker() as session:
            chatroom_id = await _chatroom_of(session, activation_id)
            await session.execute(
                at.activity_sessions.insert().values(
                    **_session_values(activation_id=activation_id, type_id=type_id, chatroom_id=chatroom_id),
                    subject_member_group_id=group_id,
                )
            )
            await session.commit()

        async with sessionmaker() as second:
            with pytest.raises(IntegrityError, match="uq_activity_sessions_activation_group"):
                await second.execute(
                    at.activity_sessions.insert().values(
                        **_session_values(
                            activation_id=activation_id, type_id=type_id, chatroom_id=chatroom_id
                        ),
                        subject_member_group_id=group_id,
                    )
                )
            await second.rollback()


def _proposal_values(
    *,
    activation_id: uuid.UUID,
    type_id: uuid.UUID,
    chatroom_id: uuid.UUID,
    group_id: uuid.UUID,
    user_id: uuid.UUID,
    status: str = "open",
) -> dict[str, object]:
    return {
        "id": uuid.uuid4(),
        "chatroom_id": chatroom_id,
        "activation_id": activation_id,
        "activity_type_id": type_id,
        "member_group_id": group_id,
        "proposer_user_id": user_id,
        "payload": {"answer": "x"},
        "voter_user_ids": [str(user_id)],
        "required_approvals": 1,
        "status": status,
        "expires_at": now() + timedelta(hours=1),
    }


class TestOneOpenProposalPerGroupAndRound:
    """AC-7."""

    async def test_two_concurrent_opens_leave_exactly_one(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """Both transactions are open at once, so the loser is decided by the
        index rather than by whichever read happened first."""
        activation_id, type_id, user_id = room_round
        group_id = uuid.uuid4()
        async with sessionmaker() as first, sessionmaker() as second:
            chatroom_id = await _chatroom_of(first, activation_id)
            values = {
                "activation_id": activation_id,
                "type_id": type_id,
                "chatroom_id": chatroom_id,
                "group_id": group_id,
                "user_id": user_id,
            }
            await first.execute(at.activity_group_proposals.insert().values(**_proposal_values(**values)))
            await first.commit()

            with pytest.raises(IntegrityError, match="uq_activity_group_proposals_open"):
                await second.execute(
                    at.activity_group_proposals.insert().values(**_proposal_values(**values))
                )
            await second.rollback()

        async with sessionmaker() as read:
            count = (
                await read.execute(
                    sa.select(sa.func.count()).where(
                        sa.and_(
                            at.activity_group_proposals.c.activation_id == activation_id,
                            at.activity_group_proposals.c.member_group_id == group_id,
                        )
                    )
                )
            ).scalar_one()
        assert count == 1

    async def test_a_resolved_proposal_does_not_block_the_next_one(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """The index is partial on purpose: a group whose first proposal was
        rejected must be able to try again in the same round."""
        activation_id, type_id, user_id = room_round
        group_id = uuid.uuid4()
        async with sessionmaker() as session:
            chatroom_id = await _chatroom_of(session, activation_id)
            values = {
                "activation_id": activation_id,
                "type_id": type_id,
                "chatroom_id": chatroom_id,
                "group_id": group_id,
                "user_id": user_id,
            }
            await session.execute(
                at.activity_group_proposals.insert().values(**_proposal_values(**values, status="rejected"))
            )
            await session.execute(at.activity_group_proposals.insert().values(**_proposal_values(**values)))
            await session.commit()

    async def test_the_listing_will_not_cross_a_room(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """/code-review. The caller's group set is derived from the bindings of
        the room in the URL, but the round is a query parameter -- and one group
        can be bound to several rooms of the same project. A `db`-tier test
        because the missing predicate is in the SQL, which the unit tier compiles
        without executing (backend/CLAUDE.md).
        """
        activation_id, type_id, user_id = room_round
        group_id = uuid.uuid4()
        other_room = uuid.uuid4()
        async with sessionmaker() as session:
            chatroom_id = await _chatroom_of(session, activation_id)
            # The same group and the same round, in two rooms. Only the FK on
            # `activation_id` ties a row to a round; nothing tied it to a room.
            await session.execute(
                at.activity_group_proposals.insert().values(
                    **_proposal_values(
                        activation_id=activation_id,
                        type_id=type_id,
                        chatroom_id=chatroom_id,
                        group_id=group_id,
                        user_id=user_id,
                    )
                )
            )
            await session.commit()

            repo = GroupProposalRepository(session)
            crossed = await repo.list_open_for_groups(
                chatroom_id=other_room,
                activation_id=activation_id,
                member_group_ids=[group_id],
            )
            own = await repo.list_open_for_groups(
                chatroom_id=chatroom_id,
                activation_id=activation_id,
                member_group_ids=[group_id],
            )

        # Without the room predicate the first call returned the row, payload and
        # all. The second is what keeps this from passing vacuously: the row is
        # there and readable, and the room is the only thing withholding it.
        assert crossed == []
        assert [p.id for p in own] == [p.id for p in own if p.chatroom_id == chatroom_id]
        assert len(own) == 1

    async def test_the_expiry_sweep_cannot_un_accept_a_proposal_it_raced(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """/code-review. `expire_due` put `status = 'open'` only in its subquery,
        leaving correctness under a concurrent accept to how PostgreSQL
        re-evaluates a `LIMIT`-bearing subplan under EvalPlanQual.

        The race is the whole finding, so the test stages it rather than asserting
        selectivity on quiescent rows -- which passes with or without the guard and
        would have vouched for nothing. The accepting transaction takes the row
        lock and holds it; the sweep's UPDATE blocks on that lock; the accept
        commits; the sweep wakes and re-evaluates. A proposal stamped `expired`
        while its submission exists and `submission_id` points at it is not a
        state any reader can interpret.
        """
        activation_id, type_id, user_id = room_round
        proposal_id = uuid.uuid4()
        async with sessionmaker() as setup:
            chatroom_id = await _chatroom_of(setup, activation_id)
            values = _proposal_values(
                activation_id=activation_id,
                type_id=type_id,
                chatroom_id=chatroom_id,
                group_id=uuid.uuid4(),
                user_id=user_id,
            )
            values["id"] = proposal_id
            values["expires_at"] = now() - timedelta(hours=1)
            await setup.execute(at.activity_group_proposals.insert().values(**values))
            await setup.commit()

        async with sessionmaker() as accepting, sessionmaker() as sweeping:
            # The last vote lands: the row is accepted but not yet committed, so
            # the sweep that follows must wait on this lock rather than read
            # around it.
            await accepting.execute(
                at.activity_group_proposals.update()
                .where(at.activity_group_proposals.c.id == proposal_id)
                .values(status="accepted", resolved_at=now())
            )

            sweep = asyncio.create_task(GroupProposalRepository(sweeping).expire_due(cutoff=now(), limit=50))
            # Long enough for the sweep to reach the UPDATE and block on the lock;
            # if it has not, the race this test exists for never happened.
            await asyncio.sleep(0.5)
            assert not sweep.done()

            await accepting.commit()
            swept = await sweep
            await sweeping.commit()

        assert proposal_id not in [row[0] for row in swept]
        async with sessionmaker() as read:
            status = (
                await read.execute(
                    sa.select(at.activity_group_proposals.c.status).where(
                        at.activity_group_proposals.c.id == proposal_id
                    )
                )
            ).scalar_one()
        assert status == "accepted"

    async def test_an_unknown_status_is_refused(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        activation_id, type_id, user_id = room_round
        async with sessionmaker() as session:
            chatroom_id = await _chatroom_of(session, activation_id)
            with pytest.raises(IntegrityError, match="ck_activity_group_proposals_status"):
                await session.execute(
                    at.activity_group_proposals.insert().values(
                        **_proposal_values(
                            activation_id=activation_id,
                            type_id=type_id,
                            chatroom_id=chatroom_id,
                            group_id=uuid.uuid4(),
                            user_id=user_id,
                            status="maybe",
                        )
                    )
                )
            await session.rollback()

    async def test_a_vote_is_one_row_per_person(
        self,
        sessionmaker: async_sessionmaker[AsyncSession],
        room_round: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    ) -> None:
        """A ballot cannot be stuffed by re-POSTing: the primary key is the
        pair, so a change of mind is an UPDATE and never a second vote."""
        activation_id, type_id, user_id = room_round
        proposal_id = uuid.uuid4()
        async with sessionmaker() as session:
            chatroom_id = await _chatroom_of(session, activation_id)
            values = _proposal_values(
                activation_id=activation_id,
                type_id=type_id,
                chatroom_id=chatroom_id,
                group_id=uuid.uuid4(),
                user_id=user_id,
            )
            values["id"] = proposal_id
            await session.execute(at.activity_group_proposals.insert().values(**values))
            await session.execute(
                at.activity_group_proposal_votes.insert().values(
                    proposal_id=proposal_id, user_id=user_id, choice="approve"
                )
            )
            await session.commit()

        async with sessionmaker() as second:
            with pytest.raises(IntegrityError, match="pk_activity_group_proposal_votes"):
                await second.execute(
                    at.activity_group_proposal_votes.insert().values(
                        proposal_id=proposal_id, user_id=user_id, choice="reject"
                    )
                )
            await second.rollback()
