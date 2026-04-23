"""Conversation domain errors — mapped to RFC 7807 problem slugs by routers."""

from __future__ import annotations


class ConversationError(Exception):
    code: str = "conversation.generic"


class WorkspaceNotFound(ConversationError):
    code = "conversation/workspace-not-found"


class ChatroomNotFound(ConversationError):
    code = "conversation/chatroom-not-found"


class MessageNotFound(ConversationError):
    code = "conversation/message-not-found"


class NameTaken(ConversationError):
    code = "conversation/name-taken"


class VersionMismatch(ConversationError):
    """If-Match header did not match current resource `version`."""
    code = "conversation/version-mismatch"


class MessageEditWindowExceeded(ConversationError):
    """R13.21 — non-moderator tried to edit past the 5-minute window."""
    code = "conversation/message-edit-window"


class MessageImmutable(ConversationError):
    """R13.22 — agents cannot edit their own past messages."""
    code = "conversation/message-immutable"


class ForbiddenInRoom(ConversationError):
    """Caller has no send/view rights in this room (per §21.1 flags)."""
    code = "conversation/forbidden-in-room"


class GuestTokenInvalid(ConversationError):
    code = "conversation/guest-token-invalid"


# ---- F.5 attachment / tus errors ----------------------------------------- #


class AttachmentNotFound(ConversationError):
    code = "conversation/attachment-not-found"


class AttachmentTooLarge(ConversationError):
    """Single-shot attachment exceeded the 32 MB cap (§22.15 switch-to-tus)."""
    code = "conversation/attachment-too-large"


class AttachmentQuarantined(ConversationError):
    """R22.15.07 — scan flagged the file, download is refused."""
    code = "conversation/attachment-quarantined"


class TusOffsetMismatch(ConversationError):
    """PATCH Upload-Offset didn't match the server's record (TUS 409 case)."""
    code = "conversation/tus-offset-mismatch"


class TusUploadNotFound(ConversationError):
    code = "conversation/tus-upload-not-found"


class TusMetadataInvalid(ConversationError):
    code = "conversation/tus-metadata-invalid"


# ---- F.10 export errors -------------------------------------------------- #


class ExportJobNotFound(ConversationError):
    code = "conversation/export-not-found"


class ExportJobNotReady(ConversationError):
    code = "conversation/export-not-ready"


__all__ = [
    "AttachmentNotFound",
    "AttachmentQuarantined",
    "AttachmentTooLarge",
    "ChatroomNotFound",
    "ConversationError",
    "ExportJobNotFound",
    "ExportJobNotReady",
    "ForbiddenInRoom",
    "GuestTokenInvalid",
    "MessageEditWindowExceeded",
    "MessageImmutable",
    "MessageNotFound",
    "NameTaken",
    "TusMetadataInvalid",
    "TusOffsetMismatch",
    "TusUploadNotFound",
    "VersionMismatch",
    "WorkspaceNotFound",
]
