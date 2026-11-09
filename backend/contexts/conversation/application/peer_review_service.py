"""Peer reply review ([R34.06]).

A peer binding's reply is held here instead of being posted, and only the room's
teacher binding can let it through. Every way out of ``pending`` other than an
approve or a modify posts nothing, so a review that fails, times out or loses
its teacher fails closed (Q-2, Q-9).

The posted message is written in the same transaction as the compare-and-set
that decided the review, so a duplicated job finds the review already decided
and posts nothing.
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.message_service import MessageService
from contexts.conversation.application.notice_service import clip
from contexts.conversation.domain.models import (
    PEER_REVIEW_DEFER_MINUTES,
    PEER_REVIEW_GRACE,
    PEER_REVIEW_MAX_ATTEMPTS,
    PEER_REVIEW_REASON_MAX_CHARS,
    PEER_REVIEW_REVISED_MAX_CHARS,
    ClassroomRole,
    Message,
    PeerReplyReview,
    PeerReviewDecision,
    PeerReviewExpiry,
    PeerReviewStatus,
)
from contexts.conversation.infrastructure.repositories import (
    ChatroomAgentRepository,
    PeerReviewRepository,
)
from shared_kernel import audit
from shared_kernel.auth.clients import now


class ReviewRefusal(str, enum.Enum):
    """Why a ``review_peer_reply`` call changed nothing. The teacher model reads it."""

    CLOSED = "closed"
    EMPTY_REASON = "empty_reason"
    EMPTY_REVISION = "empty_revision"
    BAD_DEFER = "bad_defer"


@dataclass(frozen=True, slots=True)
class ReviewPosted:
    """Approved or modified: ``message`` is committed with the caller's commit."""

    review: PeerReplyReview
    message: Message


@dataclass(frozen=True, slots=True)
class ReviewClosed:
    """Rejected or expired: nothing was posted."""

    review: PeerReplyReview


@dataclass(frozen=True, slots=True)
class ReviewDeferred:
    """Still pending; the caller owes a re-review after ``delay``."""

    review: PeerReplyReview
    delay: timedelta


@dataclass(frozen=True, slots=True)
class ReviewRefused:
    reason: ReviewRefusal


ReviewDecided = ReviewPosted | ReviewClosed | ReviewDeferred
ReviewOutcome = ReviewDecided | ReviewRefused


class PeerReviewService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._reviews = PeerReviewRepository(db)
        self._bindings = ChatroomAgentRepository(db)

    async def hold(
        self,
        *,
        chatroom_id: uuid.UUID,
        peer_agent_id: uuid.UUID,
        draft: str,
        trigger_message_id: uuid.UUID | None,
        reply_metadata: dict[str, Any] | None = None,
    ) -> PeerReplyReview | None:
        """Hold a peer's draft. Born expired when the room has no teacher (Q-9);
        None when the peer is no longer bound."""
        peer_binding = await self._bindings.binding_id_of(chatroom_id=chatroom_id, agent_id=peer_agent_id)
        if peer_binding is None:
            return None
        teacher = await self._bindings.classroom_binding(
            chatroom_id=chatroom_id, classroom_role=ClassroomRole.TEACHER
        )
        current = now()
        review = await self._reviews.create(
            chatroom_id=chatroom_id,
            peer_agent_id=peer_agent_id,
            peer_binding_id=peer_binding,
            teacher_agent_id=teacher[0] if teacher else None,
            teacher_binding_id=teacher[1] if teacher else None,
            draft=draft,
            trigger_message_id=trigger_message_id,
            due_at=current + PEER_REVIEW_GRACE,
            expired=None if teacher else PeerReviewExpiry.NO_TEACHER,
            decided_at=None if teacher else current,
            reply_metadata=reply_metadata,
        )
        if teacher is None:
            await self._audit(review)
        return review

    async def open_for_review(self, review_id: uuid.UUID) -> PeerReplyReview | None:
        """The review a teacher turn may decide now, or None after recording why not.

        Both incarnations are checked here and again at decision time: a peer or
        teacher rebound since the hold is a different participant ([R15.09])."""
        review = await self._reviews.get(review_id)
        if review is None or review.status_at(now()) is not PeerReviewStatus.PENDING:
            if review is not None and review.status is PeerReviewStatus.PENDING:
                await self.expire(review_id, PeerReviewExpiry.NO_DECISION)
            return None
        if not await self._bindings_unchanged(review):
            await self.expire(review_id, PeerReviewExpiry.BINDING_CHANGED)
            return None
        return review

    async def decide(
        self,
        *,
        review_id: uuid.UUID,
        teacher_agent_id: uuid.UUID,
        decision: PeerReviewDecision,
        reason: str,
        revised_text: str | None = None,
        defer_minutes: int | None = None,
    ) -> ReviewOutcome:
        reason = clip(reason, PEER_REVIEW_REASON_MAX_CHARS)
        if not reason:
            return ReviewRefused(ReviewRefusal.EMPTY_REASON)
        revised = clip(revised_text or "", PEER_REVIEW_REVISED_MAX_CHARS)
        if decision is PeerReviewDecision.MODIFY and not revised:
            return ReviewRefused(ReviewRefusal.EMPTY_REVISION)
        lo, hi = PEER_REVIEW_DEFER_MINUTES
        delay: timedelta | None = None
        if decision is PeerReviewDecision.DEFER:
            if defer_minutes is None or not lo <= defer_minutes <= hi:
                return ReviewRefused(ReviewRefusal.BAD_DEFER)
            delay = timedelta(minutes=defer_minutes)

        review = await self._reviews.get(review_id)
        if review is None or review.teacher_agent_id != teacher_agent_id:
            return ReviewRefused(ReviewRefusal.CLOSED)
        if not await self._bindings_unchanged(review):
            expired = await self.expire(review_id, PeerReviewExpiry.BINDING_CHANGED)
            return ReviewClosed(expired) if expired else ReviewRefused(ReviewRefusal.CLOSED)

        current = now()
        if delay is not None:
            if review.attempt >= PEER_REVIEW_MAX_ATTEMPTS:
                expired = await self.expire(review_id, PeerReviewExpiry.DEFER_LIMIT)
                return ReviewClosed(expired) if expired else ReviewRefused(ReviewRefusal.CLOSED)
            deferred = await self._reviews.defer(
                review_id=review_id,
                attempt=review.attempt,
                due_at=current + delay + PEER_REVIEW_GRACE,
                now=current,
            )
            return ReviewDeferred(deferred, delay) if deferred else ReviewRefused(ReviewRefusal.CLOSED)

        status = {
            PeerReviewDecision.APPROVE: PeerReviewStatus.APPROVED,
            PeerReviewDecision.MODIFY: PeerReviewStatus.MODIFIED,
            PeerReviewDecision.REJECT: PeerReviewStatus.REJECTED,
        }[decision]
        decided = await self._reviews.decide(
            review_id=review_id,
            status=status,
            reason=reason,
            revised_text=revised if status is PeerReviewStatus.MODIFIED else None,
            now=current,
        )
        if decided is None:
            return ReviewRefused(ReviewRefusal.CLOSED)
        await self._audit(decided)
        if status is PeerReviewStatus.REJECTED:
            return ReviewClosed(decided)
        message = await MessageService(self._db).send_agent(
            chatroom_id=decided.chatroom_id,
            agent_id=decided.peer_agent_id,
            content_md=decided.revised_text or decided.draft,
            metadata={
                **decided.reply_metadata,
                "peer_review_id": str(decided.id),
                "peer_review_modified": status is PeerReviewStatus.MODIFIED,
            },
        )
        await self._reviews.set_posted(review_id=decided.id, message_id=message.id)
        return ReviewPosted(decided, message)

    async def expire(self, review_id: uuid.UUID, reason: PeerReviewExpiry) -> PeerReplyReview | None:
        expired = await self._reviews.expire(review_id=review_id, reason=reason, now=now())
        if expired is not None:
            await self._audit(expired)
        return expired

    async def untold_rejections(
        self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Sequence[PeerReplyReview]:
        binding_id = await self._bindings.binding_id_of(chatroom_id=chatroom_id, agent_id=agent_id)
        if binding_id is None:
            return []
        return await self._reviews.untold_rejections(
            chatroom_id=chatroom_id, peer_agent_id=agent_id, peer_binding_id=binding_id
        )

    async def mark_told(self, review_ids: Sequence[uuid.UUID]) -> None:
        await self._reviews.mark_told(review_ids=review_ids, now=now())

    async def list_for_rooms(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int | None = None,
    ) -> Sequence[PeerReplyReview]:
        return await self._reviews.list_for_rooms(
            chatroom_ids=chatroom_ids, created_after=created_after, created_before=created_before, limit=limit
        )

    async def _bindings_unchanged(self, review: PeerReplyReview) -> bool:
        peer = await self._bindings.binding_id_of(
            chatroom_id=review.chatroom_id, agent_id=review.peer_agent_id
        )
        teacher = await self._bindings.classroom_binding(
            chatroom_id=review.chatroom_id, classroom_role=ClassroomRole.TEACHER
        )
        return (
            peer == review.peer_binding_id
            and teacher is not None
            and teacher == (review.teacher_agent_id, review.teacher_binding_id)
        )

    async def _audit(self, review: PeerReplyReview) -> None:
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="peer_review.decided",
                resource_type="chatroom",
                resource_id=review.chatroom_id,
                metadata={
                    "review_id": str(review.id),
                    "peer_agent_id": str(review.peer_agent_id),
                    "status": review.status.value,
                    "expire_reason": review.expire_reason.value if review.expire_reason else None,
                },
            ),
        )


__all__ = [
    "PeerReviewService",
    "ReviewClosed",
    "ReviewDecided",
    "ReviewDeferred",
    "ReviewOutcome",
    "ReviewPosted",
    "ReviewRefusal",
    "ReviewRefused",
]
