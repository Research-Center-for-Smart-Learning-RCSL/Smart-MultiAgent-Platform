"""Conversation repositories -- backward-compatible re-exports.

All repositories were split from a single ``repositories.py`` into
domain-specific modules. This package re-exports every public name so
existing ``from contexts.conversation.infrastructure.repositories import X``
statements continue to work without modification.
"""

from contexts.conversation.infrastructure.repositories.attachment_repo import (
    MessageAttachmentRepository,
)
from contexts.conversation.infrastructure.repositories.chatroom_repo import (
    ChatroomAgentRepository,
    ChatroomGuestRepository,
    ChatroomRepository,
    _new_guest_token,
)
from contexts.conversation.infrastructure.repositories.message_repo import (
    MessageEditRepository,
    MessageRepository,
)
from contexts.conversation.infrastructure.repositories.workspace_repo import (
    WorkspaceRepository,
)

__all__ = [
    "ChatroomAgentRepository",
    "ChatroomGuestRepository",
    "ChatroomRepository",
    "MessageAttachmentRepository",
    "MessageEditRepository",
    "MessageRepository",
    "WorkspaceRepository",
    "_new_guest_token",
]
