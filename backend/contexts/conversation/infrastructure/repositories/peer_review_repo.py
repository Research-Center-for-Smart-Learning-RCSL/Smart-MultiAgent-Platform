"""Peer reply review repository ([R34.06]).

Every transition out of ``pending`` is a conditional UPDATE that also requires
the review to be inside its window, so a duplicated or late job cannot decide a
review twice or decide one that has already read as expired.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import (
    PEER_REVIEW_TOLD_PER_TURN,
    PeerReplyReview,
    PeerReviewExpiry,
    PeerReviewStatus,
)
from contexts.conversation.infrastructure import tables as t

_r = t.peer_reply_reviews
_PENDING = PeerReviewStatus.PENDING.value


def _row_to_review(row: Any) -> PeerReplyReview:
    return PeerReplyReview(
        id=row.id,
        chatroom_id=row.chatroom_id,
        peer_agent_id=row.peer_agent_id,
        peer_binding_id=row.peer_binding_id,
        draft=row.draft,
        status=PeerReviewStatus(row.status),
        attempt=row.attempt,
        due_at=row.due_at,
        created_at=row.created_at,
        teacher_agent_id=row.teacher_agent_id,
        teacher_binding_id=row.teacher_binding_id,
        trigger_message_id=row.trigger_message_id,
        reason=row.reason,
        revised_text=row.revised_text,
        expire_reason=PeerReviewExpiry(row.expire_reason) if row.expire_reason else None,
        decided_at=row.decided_at,
        posted_message_id=row.posted_message_id,
        reply_metadata=dict(row.reply_metadata or {}),
    )


class PeerReviewRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        chatroom_id: uuid.UUID,
        peer_agent_id: uuid.UUID,
        peer_binding_id: uuid.UUID,
        teacher_agent_id: uuid.UUID | None,
        teacher_binding_id: uuid.UUID | None,
        draft: str,
        trigger_message_id: uuid.UUID | None,
        due_at: datetime,
        expired: PeerReviewExpiry | None = None,
        decided_at: datetime | None = None,
        reply_metadata: dict[str, Any] | None = None,
    ) -> PeerReplyReview:
        values: dict[str, Any] = {
            "reply_metadata": reply_metadata or {},
            "chatroom_id": chatroom_id,
            "peer_agent_id": peer_agent_id,
            "peer_binding_id": peer_binding_id,
            "teacher_agent_id": teacher_agent_id,
            "teacher_binding_id": teacher_binding_id,
            "draft": draft,
            "trigger_message_id": trigger_message_id,
            "due_at": due_at,
        }
        if expired is not None:
            values.update(
                status=PeerReviewStatus.EXPIRED.value, expire_reason=expired.value, decided_at=decided_at
            )
        row = (await self._db.execute(_r.insert().values(**values).returning(_r))).one()
        return _row_to_review(row)

    async def get(self, review_id: uuid.UUID) -> PeerReplyReview | None:
        row = (await self._db.execute(sa.select(_r).where(_r.c.id == review_id))).first()
        return _row_to_review(row) if row else None

    def _open(self, review_id: uuid.UUID, now: datetime) -> list[Any]:
        return [_r.c.id == review_id, _r.c.status == _PENDING, _r.c.due_at > now]

    async def decide(
        self,
        *,
        review_id: uuid.UUID,
        status: PeerReviewStatus,
        reason: str,
        revised_text: str | None,
        now: datetime,
    ) -> PeerReplyReview | None:
        """``pending`` to approved, modified or rejected, once."""
        row = (
            await self._db.execute(
                _r.update()
                .where(*self._open(review_id, now))
                .values(status=status.value, reason=reason, revised_text=revised_text, decided_at=now)
                .returning(_r)
            )
        ).first()
        return _row_to_review(row) if row else None

    async def set_posted(self, *, review_id: uuid.UUID, message_id: uuid.UUID) -> None:
        await self._db.execute(_r.update().where(_r.c.id == review_id).values(posted_message_id=message_id))

    async def defer(
        self, *, review_id: uuid.UUID, attempt: int, due_at: datetime, now: datetime
    ) -> PeerReplyReview | None:
        """Move the attempt on by one, only from the attempt the caller reviewed."""
        row = (
            await self._db.execute(
                _r.update()
                .where(*self._open(review_id, now), _r.c.attempt == attempt)
                .values(attempt=attempt + 1, due_at=due_at)
                .returning(_r)
            )
        ).first()
        return _row_to_review(row) if row else None

    async def expire(
        self, *, review_id: uuid.UUID, reason: PeerReviewExpiry, now: datetime
    ) -> PeerReplyReview | None:
        """Close a still-pending review unposted. Not bounded by ``due_at``: a
        review past its window is already expired to readers, and this records why."""
        row = (
            await self._db.execute(
                _r.update()
                .where(_r.c.id == review_id, _r.c.status == _PENDING)
                .values(status=PeerReviewStatus.EXPIRED.value, expire_reason=reason.value, decided_at=now)
                .returning(_r)
            )
        ).first()
        return _row_to_review(row) if row else None

    async def untold_rejections(
        self, *, chatroom_id: uuid.UUID, peer_agent_id: uuid.UUID, peer_binding_id: uuid.UUID
    ) -> Sequence[PeerReplyReview]:
        """Rejections this peer incarnation has not been told about, oldest first."""
        rows = (
            await self._db.execute(
                sa.select(_r)
                .where(
                    _r.c.chatroom_id == chatroom_id,
                    _r.c.peer_agent_id == peer_agent_id,
                    _r.c.peer_binding_id == peer_binding_id,
                    _r.c.status == PeerReviewStatus.REJECTED.value,
                    _r.c.peer_told_at.is_(None),
                )
                .order_by(_r.c.created_at, _r.c.id)
                .limit(PEER_REVIEW_TOLD_PER_TURN)
            )
        ).all()
        return [_row_to_review(r) for r in rows]

    async def mark_told(self, *, review_ids: Sequence[uuid.UUID], now: datetime) -> None:
        if review_ids:
            await self._db.execute(
                _r.update()
                .where(_r.c.id.in_(list(review_ids)), _r.c.peer_told_at.is_(None))
                .values(peer_told_at=now)
            )

    async def list_for_rooms(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[PeerReplyReview]:
        """Newest first."""
        if not chatroom_ids:
            return []
        stmt = sa.select(_r).where(_r.c.chatroom_id.in_(list(chatroom_ids)))
        if created_after is not None:
            stmt = stmt.where(_r.c.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(_r.c.created_at < created_before)
        stmt = stmt.order_by(_r.c.created_at.desc(), _r.c.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return [_row_to_review(r) for r in (await self._db.execute(stmt)).all()]


__all__ = ["PeerReviewRepository"]
