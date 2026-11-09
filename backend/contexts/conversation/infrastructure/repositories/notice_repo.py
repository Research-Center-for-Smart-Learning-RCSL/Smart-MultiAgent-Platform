"""Analyst notice repository ([R34.04], [R34.05]).

Notices live in their own table like observations, so no message read path
(REST list, WS fan-out, model history, search) can reach them.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import AnalystNotice, NoticeDecision, NoticeErrorKind
from contexts.conversation.infrastructure import tables as t

_n = t.analyst_notices


def _row_to_notice(row: Any) -> AnalystNotice:
    return AnalystNotice(
        id=row.id,
        chatroom_id=row.chatroom_id,
        analyst_agent_id=row.analyst_agent_id,
        analyst_binding_id=row.analyst_binding_id,
        teacher_agent_id=row.teacher_agent_id,
        teacher_binding_id=row.teacher_binding_id,
        error_kind=NoticeErrorKind(row.error_kind),
        error=row.error,
        suggestion=row.suggestion,
        created_at=row.created_at,
        about_message_id=row.about_message_id,
        decision=NoticeDecision(row.decision) if row.decision else None,
        reason=row.reason,
        responded_at=row.responded_at,
    )


class AnalystNoticeRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        chatroom_id: uuid.UUID,
        analyst_agent_id: uuid.UUID,
        analyst_binding_id: uuid.UUID,
        teacher_agent_id: uuid.UUID,
        teacher_binding_id: uuid.UUID,
        error_kind: NoticeErrorKind,
        error: str,
        suggestion: str,
        about_message_id: uuid.UUID | None,
    ) -> AnalystNotice:
        row = (
            await self._db.execute(
                _n.insert()
                .values(
                    chatroom_id=chatroom_id,
                    analyst_agent_id=analyst_agent_id,
                    analyst_binding_id=analyst_binding_id,
                    teacher_agent_id=teacher_agent_id,
                    teacher_binding_id=teacher_binding_id,
                    error_kind=error_kind.value,
                    error=error,
                    suggestion=suggestion,
                    about_message_id=about_message_id,
                )
                .returning(_n)
            )
        ).one()
        return _row_to_notice(row)

    async def count_since(self, *, chatroom_id: uuid.UUID, since: datetime) -> int:
        return int(
            (
                await self._db.execute(
                    sa.select(sa.func.count())
                    .select_from(_n)
                    .where(_n.c.chatroom_id == chatroom_id, _n.c.created_at >= since)
                )
            ).scalar_one()
        )

    async def has_open_about(self, *, about_message_id: uuid.UUID, since: datetime) -> bool:
        """Whether a notice about this message is still awaiting an answer."""
        return (
            await self._db.execute(
                sa.select(_n.c.id)
                .where(
                    _n.c.about_message_id == about_message_id,
                    _n.c.decision.is_(None),
                    _n.c.created_at >= since,
                )
                .limit(1)
            )
        ).first() is not None

    async def open_for_teacher(
        self,
        *,
        chatroom_id: uuid.UUID,
        teacher_agent_id: uuid.UUID,
        teacher_binding_id: uuid.UUID,
        since: datetime,
    ) -> Sequence[AnalystNotice]:
        """Unanswered notices to this teacher incarnation created after ``since``,
        oldest first. A notice addressed to an earlier incarnation is not offered:
        a rebound teacher is a different binding ([R15.09])."""
        rows = (
            await self._db.execute(
                sa.select(_n)
                .where(
                    _n.c.chatroom_id == chatroom_id,
                    _n.c.teacher_agent_id == teacher_agent_id,
                    _n.c.teacher_binding_id == teacher_binding_id,
                    _n.c.decision.is_(None),
                    _n.c.created_at >= since,
                )
                .order_by(_n.c.created_at, _n.c.id)
            )
        ).all()
        return [_row_to_notice(r) for r in rows]

    async def respond(
        self,
        *,
        chatroom_id: uuid.UUID,
        notice_id: uuid.UUID,
        teacher_agent_id: uuid.UUID,
        teacher_binding_id: uuid.UUID,
        decision: NoticeDecision,
        reason: str,
        since: datetime,
        responded_at: datetime,
    ) -> AnalystNotice | None:
        """Record the answer once: matches only an unanswered notice to this
        teacher incarnation that is still inside its answer window."""
        row = (
            await self._db.execute(
                _n.update()
                .where(
                    _n.c.id == notice_id,
                    _n.c.chatroom_id == chatroom_id,
                    _n.c.teacher_agent_id == teacher_agent_id,
                    _n.c.teacher_binding_id == teacher_binding_id,
                    _n.c.decision.is_(None),
                    _n.c.created_at >= since,
                )
                .values(decision=decision.value, reason=reason, responded_at=responded_at)
                .returning(_n)
            )
        ).first()
        return _row_to_notice(row) if row else None

    async def list_for_rooms(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[AnalystNotice]:
        """Newest first."""
        if not chatroom_ids:
            return []
        stmt = sa.select(_n).where(_n.c.chatroom_id.in_(list(chatroom_ids)))
        if created_after is not None:
            stmt = stmt.where(_n.c.created_at >= created_after)
        if created_before is not None:
            stmt = stmt.where(_n.c.created_at < created_before)
        stmt = stmt.order_by(_n.c.created_at.desc(), _n.c.id.desc()).offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return [_row_to_notice(r) for r in (await self._db.execute(stmt)).all()]


__all__ = ["AnalystNoticeRepository"]
