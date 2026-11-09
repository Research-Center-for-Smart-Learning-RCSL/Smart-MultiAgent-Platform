"""Peer reply review against a real PostgreSQL ([R34.06]).

At most one message per review holds only because every way out of
``pending`` is a conditional UPDATE in the transaction that posts; that, and
the decision-consistency constraints, are properties of a real engine.
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
from contexts.conversation.application.peer_review_service import (
    PeerReviewService,
    ReviewClosed,
    ReviewDeferred,
    ReviewPosted,
    ReviewRefusal,
    ReviewRefused,
)
from contexts.conversation.domain.models import (
    PeerReplyReview,
    PeerReviewDecision,
    PeerReviewExpiry,
    PeerReviewStatus,
)
from contexts.conversation.infrastructure import tables as t
from contexts.keys.infrastructure.tables import key_groups as key_groups_t

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class Room:
    chatroom_id: uuid.UUID
    teacher: uuid.UUID
    peer: uuid.UUID


@pytest.fixture
async def room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[Room]:
    project_id, user_id = project
    workspace_id, chatroom_id, key_group_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    agents = [uuid.uuid4() for _ in range(2)]
    async with sessionmaker() as session:
        await session.execute(
            t.workspaces.insert().values(id=workspace_id, project_id=project_id, name="review-itest")
        )
        await session.execute(
            t.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name="review-itest",
                guest_token=str(uuid.uuid4()),
                created_by_user_id=user_id,
            )
        )
        await session.execute(
            key_groups_t.insert().values(id=key_group_id, project_id=project_id, name="review-itest-kg")
        )
        for n, (agent_id, classroom_role) in enumerate(zip(agents, ("teacher", "peer"), strict=True)):
            await session.execute(
                agt.agents.insert().values(
                    id=agent_id,
                    project_id=project_id,
                    name=f"review-itest-{n}",
                    model_hint="claude",
                    key_group_id=key_group_id,
                )
            )
            await session.execute(
                t.chatroom_agents.insert().values(
                    chatroom_id=chatroom_id, agent_id=agent_id, role="normal", classroom_role=classroom_role
                )
            )
        await session.commit()
    try:
        yield Room(chatroom_id, agents[0], agents[1])
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(agt.agents.delete().where(agt.agents.c.id.in_(agents)))
            await cleanup.execute(key_groups_t.delete().where(key_groups_t.c.id == key_group_id))
            await cleanup.commit()


async def _hold(session: AsyncSession, room: Room) -> PeerReplyReview:
    review = await PeerReviewService(session).hold(
        chatroom_id=room.chatroom_id,
        peer_agent_id=room.peer,
        draft="It is 12.",
        trigger_message_id=None,
        reply_metadata={"tool_rounds": 2},
    )
    await session.commit()
    assert review is not None
    return review


async def _decide(
    session: AsyncSession,
    room: Room,
    review: PeerReplyReview,
    decision: PeerReviewDecision,
    *,
    reason: str = "Clear.",
    revised_text: str | None = None,
    defer_minutes: int | None = None,
) -> object:
    return await PeerReviewService(session).decide(
        review_id=review.id,
        teacher_agent_id=room.teacher,
        decision=decision,
        reason=reason,
        revised_text=revised_text,
        defer_minutes=defer_minutes,
    )


async def _messages(session: AsyncSession, room: Room) -> list[sa.Row]:
    return list(
        (await session.execute(t.messages.select().where(t.messages.c.chatroom_id == room.chatroom_id))).all()
    )


class TestDuplicateJobs:
    async def test_two_concurrent_approvals_post_one_message(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        """AC-15."""
        async with sessionmaker() as session:
            review = await _hold(session, room)

        async def _one() -> object:
            async with sessionmaker() as session:
                outcome = await _decide(session, room, review, PeerReviewDecision.APPROVE)
                # Keep the row locked so the other writer has to wait on it.
                await asyncio.sleep(0.1)
                await session.commit()
                return outcome

        results = await asyncio.gather(_one(), _one())

        assert sorted(type(r).__name__ for r in results) == ["ReviewPosted", "ReviewRefused"]
        async with sessionmaker() as session:
            [msg] = await _messages(session, room)
            assert msg.sender_id == room.peer
            assert msg.content_md == "It is 12."
            assert msg._mapping["metadata"]["peer_review_id"] == str(review.id)
            assert msg._mapping["metadata"]["tool_rounds"] == 2
            row = (
                await session.execute(
                    t.peer_reply_reviews.select().where(t.peer_reply_reviews.c.id == review.id)
                )
            ).one()
            assert (row.status, row.posted_message_id) == ("approved", msg.id)

    async def test_a_rolled_back_decision_posts_nothing_and_stays_decidable(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            review = await _hold(session, room)
            assert isinstance(await _decide(session, room, review, PeerReviewDecision.APPROVE), ReviewPosted)
            await session.rollback()
            assert await _messages(session, room) == []
            modified = await _decide(
                session, room, review, PeerReviewDecision.MODIFY, revised_text="What is 3 times 4?"
            )
            await session.commit()
            assert isinstance(modified, ReviewPosted)
            [msg] = await _messages(session, room)
            assert msg.content_md == "What is 3 times 4?"


class TestTransitions:
    async def test_a_rejection_is_told_once_to_the_same_peer(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            review = await _hold(session, room)
            assert isinstance(
                await _decide(session, room, review, PeerReviewDecision.REJECT, reason="Gives it away."),
                ReviewClosed,
            )
            await session.commit()
            service = PeerReviewService(session)
            untold = await service.untold_rejections(chatroom_id=room.chatroom_id, agent_id=room.peer)
            assert [(r.id, r.reason) for r in untold] == [(review.id, "Gives it away.")]
            await service.mark_told([review.id])
            await session.commit()
            assert await service.untold_rejections(chatroom_id=room.chatroom_id, agent_id=room.peer) == []
            assert await _messages(session, room) == []

    async def test_deferral_is_bounded_by_the_attempt(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            review = await _hold(session, room)
            for attempt in (2, 3):
                deferred = await _decide(session, room, review, PeerReviewDecision.DEFER, defer_minutes=1)
                await session.commit()
                assert isinstance(deferred, ReviewDeferred)
                assert deferred.review.attempt == attempt
            last = await _decide(session, room, review, PeerReviewDecision.DEFER, defer_minutes=1)
            await session.commit()
            assert isinstance(last, ReviewClosed)
            assert last.review.expire_reason is PeerReviewExpiry.DEFER_LIMIT

    async def test_a_review_past_its_window_cannot_be_decided(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            review = await _hold(session, room)
            await session.execute(
                t.peer_reply_reviews.update()
                .where(t.peer_reply_reviews.c.id == review.id)
                .values(due_at=sa.func.now() - timedelta(seconds=1))
            )
            await session.commit()
            assert await _decide(session, room, review, PeerReviewDecision.APPROVE) == ReviewRefused(
                ReviewRefusal.CLOSED
            )
            assert await PeerReviewService(session).open_for_review(review.id) is None
            await session.commit()
            row = (
                await session.execute(
                    t.peer_reply_reviews.select().where(t.peer_reply_reviews.c.id == review.id)
                )
            ).one()
            assert (row.status, row.expire_reason) == ("expired", "no_decision")
            assert await _messages(session, room) == []

    async def test_a_room_without_a_teacher_holds_an_expired_review(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            await session.execute(
                t.chatroom_agents.update()
                .where(t.chatroom_agents.c.agent_id == room.teacher)
                .values(classroom_role="none")
            )
            await session.commit()
            review = await _hold(session, room)
            assert review.status is PeerReviewStatus.EXPIRED
            assert review.expire_reason is PeerReviewExpiry.NO_TEACHER
            assert review.teacher_agent_id is None

    async def test_listing_is_newest_first(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room
    ) -> None:
        async with sessionmaker() as session:
            first = await _hold(session, room)
            second = await _hold(session, room)
            listed = await PeerReviewService(session).list_for_rooms(chatroom_ids=[room.chatroom_id])
            # Each hold committed in its own transaction, so now() orders them.
            assert [r.id for r in listed] == [second.id, first.id]


class TestConstraints:
    @pytest.mark.parametrize(
        "values",
        [
            {"status": "approved"},
            {"status": "rejected", "decided_at": sa.func.now(), "reason": ""},
            {"status": "modified", "decided_at": sa.func.now(), "reason": "r"},
            {"status": "expired", "decided_at": sa.func.now()},
            {"attempt": 4},
        ],
    )
    async def test_an_inconsistent_row_is_refused(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: Room, values: dict[str, object]
    ) -> None:
        async with sessionmaker() as session:
            review = await _hold(session, room)
            with pytest.raises(sa.exc.IntegrityError):
                await session.execute(
                    t.peer_reply_reviews.update()
                    .where(t.peer_reply_reviews.c.id == review.id)
                    .values(**values)
                )
            await session.rollback()
