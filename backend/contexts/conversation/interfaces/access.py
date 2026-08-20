"""Public room-access surface for API routes and other interface consumers.

The room-ACL denial error types are re-exported here so consumers in *other*
contexts (e.g. the knowledge config-access predicate) can catch them without
reaching past this facade into ``conversation.domain`` — they are part of the
``resolve_room_access`` / ``ensure_can_read`` contract.

``can_read_orchestration_record`` / ``filter_readable_by_room`` are the same
room ACL applied to a *foreign* context's rows (R15.24): orchestration approvals
and agent instances carry a nullable ``chatroom_id``, and a record naming a room
is readable by exactly that room's readers. They live here rather than in
``orchestration`` because the predicate they must agree with lives here — a
second copy over there is how a room tier stops applying to half the platform.
"""

from contexts.conversation.application.access import (
    can_read_orchestration_record,
    ensure_can_read,
    ensure_can_send,
    ensure_room_creator,
    filter_readable_by_room,
    is_moderator_roles,
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
    "can_read_orchestration_record",
    "ensure_can_read",
    "ensure_can_send",
    "ensure_room_creator",
    "filter_readable_by_room",
    "is_moderator_roles",
    "resolve_room_access",
]
