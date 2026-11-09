"""Analyst-to-teacher notices ([R34.04], [R34.05]).

Both writes are worker-side: an analyst turn creates a notice through its
``notify_teacher_agent`` tool and the teacher turn answers it through
``respond_to_notice``, each inside that turn's transaction. The table is the
record and the delivery: the teacher's turn reads its open notices from here,
so nothing depends on a queue that can lose them (D-4).
"""

from __future__ import annotations

import enum
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import (
    NOTICE_ANSWER_WINDOW,
    NOTICE_RATE_LIMIT,
    NOTICE_RATE_WINDOW,
    NOTICE_REASON_MAX_CHARS,
    NOTICE_TEXT_MAX_CHARS,
    AnalystNotice,
    ChatroomAgentRole,
    ClassroomRole,
    NoticeDecision,
    NoticeErrorKind,
    SenderType,
)
from contexts.conversation.infrastructure.repositories import (
    AnalystNoticeRepository,
    ChatroomAgentRepository,
    MessageRepository,
)
from shared_kernel import audit
from shared_kernel.auth.clients import now
from shared_kernel.db.advisory_lock import advisory_xact_lock


class NoticeRefusal(str, enum.Enum):
    """Why a notice was not written. Each is something the analyst can read and
    act on, so the tool hands it back rather than raising."""

    NOT_ANALYST = "not_analyst"
    NO_TEACHER = "no_teacher"
    RATE_LIMITED = "rate_limited"
    DUPLICATE_SUBJECT = "duplicate_subject"
    BAD_SUBJECT = "bad_subject"
    EMPTY_TEXT = "empty_text"


@dataclass(frozen=True, slots=True)
class NoticeCreated:
    notice: AnalystNotice


@dataclass(frozen=True, slots=True)
class NoticeRefused:
    reason: NoticeRefusal


_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clip(text: str, limit: int) -> str:
    # PostgreSQL text rejects NUL, and the write shares the turn's transaction:
    # a model coaxed into emitting one would otherwise fail the whole turn.
    return _CONTROL_CHARS.sub("", text).strip()[:limit]


class AnalystNoticeService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._notices = AnalystNoticeRepository(db)
        self._bindings = ChatroomAgentRepository(db)
        self._messages = MessageRepository(db)

    async def create(
        self,
        *,
        chatroom_id: uuid.UUID,
        analyst_agent_id: uuid.UUID,
        error_kind: NoticeErrorKind,
        error: str,
        suggestion: str,
        about_message_id: uuid.UUID | None = None,
    ) -> NoticeCreated | NoticeRefused:
        error, suggestion = clip(error, NOTICE_TEXT_MAX_CHARS), clip(suggestion, NOTICE_TEXT_MAX_CHARS)
        if not error or not suggestion:
            return NoticeRefused(NoticeRefusal.EMPTY_TEXT)
        # Re-read rather than trusted from turn start: an unbind or a role change
        # mid-turn must not leave a notice from a binding that is no longer one.
        roles = await self._bindings.roles_of(chatroom_id=chatroom_id, agent_id=analyst_agent_id)
        if roles != (ChatroomAgentRole.OBSERVER, ClassroomRole.ANALYST):
            return NoticeRefused(NoticeRefusal.NOT_ANALYST)
        analyst_binding = await self._bindings.binding_id_of(
            chatroom_id=chatroom_id, agent_id=analyst_agent_id
        )
        # Serialises this room's notice writers so the cap and the one-per-subject
        # rule cannot be raced past. An advisory key rather than a row lock: it is
        # held until the analyst's turn commits, and a lock on the teacher's
        # binding row would stall that teacher's own wake-up writes meanwhile.
        await advisory_xact_lock(self._db, f"analyst_notices:{chatroom_id}")
        teacher = await self._bindings.classroom_binding(
            chatroom_id=chatroom_id, classroom_role=ClassroomRole.TEACHER
        )
        if teacher is None or analyst_binding is None:
            return NoticeRefused(NoticeRefusal.NO_TEACHER)
        teacher_agent_id, teacher_binding_id = teacher
        current = now()
        if (
            await self._notices.count_since(chatroom_id=chatroom_id, since=current - NOTICE_RATE_WINDOW)
            >= NOTICE_RATE_LIMIT
        ):
            return NoticeRefused(NoticeRefusal.RATE_LIMITED)
        if about_message_id is not None:
            if not await self._subject_is_a_classroom_agents(chatroom_id, about_message_id):
                return NoticeRefused(NoticeRefusal.BAD_SUBJECT)
            if await self._notices.has_open_about(
                about_message_id=about_message_id, since=current - NOTICE_ANSWER_WINDOW
            ):
                return NoticeRefused(NoticeRefusal.DUPLICATE_SUBJECT)
        notice = await self._notices.create(
            chatroom_id=chatroom_id,
            analyst_agent_id=analyst_agent_id,
            analyst_binding_id=analyst_binding,
            teacher_agent_id=teacher_agent_id,
            teacher_binding_id=teacher_binding_id,
            error_kind=error_kind,
            error=error,
            suggestion=suggestion,
            about_message_id=about_message_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="analyst_notice.created",
                resource_type="chatroom",
                resource_id=chatroom_id,
                metadata={
                    "notice_id": str(notice.id),
                    "analyst_agent_id": str(analyst_agent_id),
                    "teacher_agent_id": str(teacher_agent_id),
                    "error_kind": error_kind.value,
                },
            ),
        )
        return NoticeCreated(notice)

    async def _subject_is_a_classroom_agents(self, chatroom_id: uuid.UUID, message_id: uuid.UUID) -> bool:
        """[R34.04]: a notice may only be about something the teacher or a peer said here."""
        msg = await self._messages.get(message_id)
        if msg is None or msg.chatroom_id != chatroom_id or msg.sender_type is not SenderType.AGENT:
            return False
        if msg.sender_id is None:
            return False
        roles = await self._bindings.roles_of(chatroom_id=chatroom_id, agent_id=msg.sender_id)
        return roles is not None and roles[1] in (ClassroomRole.TEACHER, ClassroomRole.PEER)

    async def open_for_teacher(
        self, *, chatroom_id: uuid.UUID, agent_id: uuid.UUID
    ) -> Sequence[AnalystNotice]:
        """The notices this agent's current teacher binding still owes an answer."""
        roles = await self._bindings.roles_of(chatroom_id=chatroom_id, agent_id=agent_id)
        if roles is None or roles[1] is not ClassroomRole.TEACHER:
            return []
        binding_id = await self._bindings.binding_id_of(chatroom_id=chatroom_id, agent_id=agent_id)
        if binding_id is None:
            return []
        return await self._notices.open_for_teacher(
            chatroom_id=chatroom_id,
            teacher_agent_id=agent_id,
            teacher_binding_id=binding_id,
            since=now() - NOTICE_ANSWER_WINDOW,
        )

    async def respond(
        self,
        *,
        chatroom_id: uuid.UUID,
        teacher_agent_id: uuid.UUID,
        notice_id: uuid.UUID,
        decision: NoticeDecision,
        reason: str,
    ) -> AnalystNotice | None:
        """Record the teacher's answer once. None when the notice is not this
        teacher binding's to answer, already answered, or past its window."""
        reason = clip(reason, NOTICE_REASON_MAX_CHARS)
        if not reason:
            return None
        roles = await self._bindings.roles_of(chatroom_id=chatroom_id, agent_id=teacher_agent_id)
        if roles is None or roles[1] is not ClassroomRole.TEACHER:
            return None
        binding_id = await self._bindings.binding_id_of(chatroom_id=chatroom_id, agent_id=teacher_agent_id)
        if binding_id is None:
            return None
        current = now()
        notice = await self._notices.respond(
            chatroom_id=chatroom_id,
            notice_id=notice_id,
            teacher_agent_id=teacher_agent_id,
            teacher_binding_id=binding_id,
            decision=decision,
            reason=reason,
            since=current - NOTICE_ANSWER_WINDOW,
            responded_at=current,
        )
        if notice is None:
            return None
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="analyst_notice.responded",
                resource_type="chatroom",
                resource_id=chatroom_id,
                metadata={"notice_id": str(notice.id), "decision": decision.value},
            ),
        )
        return notice

    async def list_for_rooms(
        self,
        *,
        chatroom_ids: Sequence[uuid.UUID],
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[AnalystNotice]:
        return await self._notices.list_for_rooms(
            chatroom_ids=chatroom_ids,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
        )


__all__ = ["AnalystNoticeService", "NoticeCreated", "NoticeRefusal", "NoticeRefused"]
