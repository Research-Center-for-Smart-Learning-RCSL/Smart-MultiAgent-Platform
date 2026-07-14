"""Public room-access surface for API routes and other interface consumers.

The room-ACL denial error types are re-exported here so consumers in *other*
contexts (e.g. the knowledge config-access predicate) can catch them without
reaching past this facade into ``conversation.domain`` — they are part of the
``resolve_room_access`` / ``ensure_can_read`` contract.
"""

from contexts.conversation.application.access import (
    ensure_can_read,
    ensure_can_send,
    ensure_room_creator,
    resolve_room_access,
)
from contexts.conversation.domain.errors import (
    ChatroomNotFound,
    ForbiddenInRoom,
    WorkspaceNotFound,
)

__all__ = [
    "ChatroomNotFound",
    "ForbiddenInRoom",
    "WorkspaceNotFound",
    "ensure_can_read",
    "ensure_can_send",
    "ensure_room_creator",
    "resolve_room_access",
]
