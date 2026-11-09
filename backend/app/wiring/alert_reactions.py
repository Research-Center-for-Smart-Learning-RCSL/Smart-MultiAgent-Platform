"""Record the room creator's first action after an alert ([R34.02]).

Called by the message, activity start and activity end routes after their own
commit. Best-effort: the action already stands, so a failure here loses only
the reaction-time record and is logged, not raised.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.domain.models import AlertActionKind
from contexts.conversation.interfaces.facade import ConversationFacade

_log = logging.getLogger(__name__)


async def record_alert_reaction(
    db: AsyncSession, *, chatroom_id: uuid.UUID, actor_user_id: uuid.UUID, kind: AlertActionKind
) -> None:
    try:
        closed = await ConversationFacade(db).record_alert_room_action(
            chatroom_id=chatroom_id, actor_user_id=actor_user_id, kind=kind
        )
        if closed:
            await db.commit()
    except Exception:
        await _rollback_quietly(db)
        _log.warning("alert reaction record failed for room %s", chatroom_id, exc_info=True)


async def _rollback_quietly(db: AsyncSession) -> None:
    try:
        await db.rollback()
    except Exception:
        _log.debug("rollback after alert reaction failure also failed", exc_info=True)


__all__ = ["AlertActionKind", "record_alert_reaction"]
