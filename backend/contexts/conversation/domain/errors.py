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


class AttachmentExpired(ConversationError):
    """R13.11a — the object is past its TTL, so the bucket lifecycle has (or is
    about to have) deleted the bytes. Distinct from `AttachmentNotFound`: the
    attachment existed and the client renders `[attachment expired]` for it."""

    code = "conversation/attachment-expired"


class TusOffsetMismatch(ConversationError):
    """PATCH Upload-Offset didn't match the server's record (TUS 409 case)."""

    code = "conversation/tus-offset-mismatch"


class TusUploadNotFound(ConversationError):
    code = "conversation/tus-upload-not-found"


class AttachmentBindingFailed(ConversationError):
    """Some requested attachment_ids could not be bound (wrong room, wrong user, or expired)."""

    code = "conversation/attachment-binding-failed"


class TusMetadataInvalid(ConversationError):
    code = "conversation/tus-metadata-invalid"


# ---- §28 observer errors -------------------------------------------------- #


class NotRoomCreator(ConversationError):
    """R28.02 — observer surfaces are creator-only (moderator on legacy NULL rooms)."""

    code = "conversation/not-room-creator"


class ObservationNotFound(ConversationError):
    code = "conversation/observation-not-found"


class ObservationAlreadyReleased(ConversationError):
    """R28.08 — a release is single-shot; the CAS loser lands here."""

    code = "conversation/observation-already-released"


class InvalidReleaseTarget(ConversationError):
    """R28.07 — targets must be normal-role bindings of the same room."""

    code = "conversation/invalid-release-target"


# ---- F.10 export errors -------------------------------------------------- #


class ExportJobNotFound(ConversationError):
    code = "conversation/export-not-found"


class ExportJobNotReady(ConversationError):
    code = "conversation/export-not-ready"


__all__ = [
    "AttachmentBindingFailed",
    "AttachmentExpired",
    "AttachmentNotFound",
    "AttachmentQuarantined",
    "AttachmentTooLarge",
    "ChatroomNotFound",
    "ConversationError",
    "ExportJobNotFound",
    "ExportJobNotReady",
    "ForbiddenInRoom",
    "GuestTokenInvalid",
    "InvalidReleaseTarget",
    "MessageEditWindowExceeded",
    "MessageImmutable",
    "MessageNotFound",
    "NameTaken",
    "NotRoomCreator",
    "ObservationAlreadyReleased",
    "ObservationNotFound",
    "TusMetadataInvalid",
    "TusOffsetMismatch",
    "TusUploadNotFound",
    "VersionMismatch",
    "WorkspaceNotFound",
]
