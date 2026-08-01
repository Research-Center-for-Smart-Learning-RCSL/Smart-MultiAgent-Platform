"""Conversation domain errors → RFC 7807 registration.

Dispatch + fallback live in `shared_kernel.errors.context_handler` (API-3).
"""

from __future__ import annotations

from fastapi import FastAPI

from contexts.conversation.domain import errors
from shared_kernel.errors.context_handler import ErrorMap, register_context_handler

_MAP: ErrorMap = {
    errors.WorkspaceNotFound: (
        "conversation/workspace-not-found",
        404,
        "Workspace not found",
    ),
    errors.ChatroomNotFound: (
        "conversation/chatroom-not-found",
        404,
        "Chatroom not found",
    ),
    errors.MessageNotFound: (
        "conversation/message-not-found",
        404,
        "Message not found",
    ),
    errors.NameTaken: (
        "conversation/name-taken",
        409,
        "Name already in use",
    ),
    errors.VersionMismatch: (
        "conversation/version-mismatch",
        412,
        "Version mismatch (If-Match)",
    ),
    errors.MessageEditWindowExceeded: (
        "conversation/message-edit-window",
        403,
        "Self-edit window exceeded (5 minutes)",
    ),
    errors.MessageImmutable: (
        "conversation/message-immutable",
        403,
        "Message cannot be edited",
    ),
    errors.ForbiddenInRoom: (
        "conversation/forbidden-in-room",
        403,
        "Forbidden in this chatroom",
    ),
    errors.GuestTokenInvalid: (
        "conversation/guest-token-invalid",
        404,
        "Guest token invalid",
    ),
    errors.AttachmentBindingFailed: (
        "conversation/attachment-binding-failed",
        422,
        "Some attachments could not be bound to the message",
    ),
    errors.AttachmentNotFound: (
        "conversation/attachment-not-found",
        404,
        "Attachment not found",
    ),
    errors.AttachmentTooLarge: (
        "conversation/attachment-too-large",
        413,
        "Attachment too large",
    ),
    errors.AttachmentQuarantined: (
        "conversation/attachment-quarantined",
        403,
        "Attachment quarantined",
    ),
    # 410 rather than 404: the resource existed and was deliberately removed, and
    # the client renders that differently from a bad id (R13.11).
    errors.AttachmentExpired: (
        "conversation/attachment-expired",
        410,
        "Attachment expired",
    ),
    errors.TusOffsetMismatch: (
        "conversation/tus-offset-mismatch",
        409,
        "Upload offset mismatch",
    ),
    errors.TusUploadNotFound: (
        "conversation/tus-upload-not-found",
        404,
        "Upload not found",
    ),
    # 409 so a TUS client resyncs with HEAD; the upload record is already gone
    # by then, so the 404 it gets back sends it into a clean restart.
    errors.TusSizeMismatch: (
        "conversation/tus-size-mismatch",
        409,
        "Staged upload size does not match the declared length",
    ),
    errors.TusMetadataInvalid: (
        "conversation/tus-metadata-invalid",
        400,
        "Upload metadata invalid",
    ),
    errors.TusUploadCapacityExceeded: (
        "conversation/tus-upload-capacity-exceeded",
        429,
        "Upload capacity exceeded",
    ),
    errors.TusStagingUnavailable: (
        "conversation/tus-staging-unavailable",
        503,
        "Upload staging is temporarily unavailable",
    ),
    errors.NotRoomCreator: (
        "conversation/not-room-creator",
        403,
        "Only the chatroom creator may access observer surfaces",
    ),
    errors.ObservationNotFound: (
        "conversation/observation-not-found",
        404,
        "Observation not found",
    ),
    errors.ObservationAlreadyReleased: (
        "conversation/observation-already-released",
        409,
        "Observation already released",
    ),
    errors.InvalidReleaseTarget: (
        "conversation/invalid-release-target",
        422,
        "Release targets must be normal-role agents bound to this chatroom",
    ),
    errors.ExportJobNotFound: (
        "conversation/export-not-found",
        404,
        "Export job not found",
    ),
    errors.ExportJobNotReady: (
        "conversation/export-not-ready",
        409,
        "Export not ready",
    ),
}


def register(app: FastAPI) -> None:
    register_context_handler(app, errors.ConversationError, _MAP)


__all__ = ["register"]
