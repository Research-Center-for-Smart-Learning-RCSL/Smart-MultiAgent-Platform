"""The room system message an activation start or end writes ([R30.45]).

Every participant sees it as a divider and every agent reads it in the transcript,
so it states only that an activity began or ended and which one. It never names
who started or ended it: an observer agent holding the activity grant must not be
disclosed by the one message the whole class sees ([R28.10]).
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.models import ActivationNotice, ActivityType
from contexts.conversation.interfaces.facade import ConversationFacade

MESSAGE_TYPE: Final = "activity_activation"
EVENT_STARTED: Final = "started"
EVENT_ENDED: Final = "ended"

# Owner-authored and shown inside quotes on one transcript line, so it is bounded
# and cannot open a second line or close its own quote.
_NAME_MAX = 120


def display_name(activity_type: ActivityType | None) -> str | None:
    if activity_type is None:
        return None
    name = " ".join(activity_type.name.replace('"', "").split())[:_NAME_MAX]
    return name or None


def notice_text(activity_type: ActivityType | None, event: str) -> str:
    """The canonical English sentence agents and exports read; clients localise."""
    name = display_name(activity_type)
    if name is None:
        return f"An activity {event}."
    return f'Activity "{name}" {event}.'


class ConversationActivationNotices:
    """Writes the notice through the conversation facade, in the caller's transaction."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def write(
        self,
        *,
        chatroom_id: uuid.UUID,
        activation_id: uuid.UUID,
        activity_type: ActivityType | None,
        event: str,
    ) -> ActivationNotice:
        metadata: dict[str, object] = {"event": event, "activation_id": str(activation_id)}
        if activity_type is not None:
            metadata["activity_type_key"] = activity_type.key
        name = display_name(activity_type)
        if name is not None:
            metadata["activity_type_name"] = name
        message = await ConversationFacade(self._db).insert_system_message(
            chatroom_id=chatroom_id,
            content_md=notice_text(activity_type, event),
            message_type=MESSAGE_TYPE,
            metadata=metadata,
        )
        return ActivationNotice(message_id=message.id, chatroom_id=chatroom_id, created_at=message.created_at)


__all__ = [
    "EVENT_ENDED",
    "EVENT_STARTED",
    "MESSAGE_TYPE",
    "ConversationActivationNotices",
    "display_name",
    "notice_text",
]
