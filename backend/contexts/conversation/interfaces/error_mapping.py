"""Conversation domain errors → RFC 7807 registration."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from contexts.conversation.domain import errors
from shared_kernel.errors.problem import Problem, problem_type

_MEDIA = "application/problem+json"

_MAP: dict[type[errors.ConversationError], tuple[str, int, str]] = {
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
    errors.TusMetadataInvalid: (
        "conversation/tus-metadata-invalid",
        400,
        "Upload metadata invalid",
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


async def _handler(
    request: Request,
    exc: errors.ConversationError,
) -> JSONResponse:
    slug, status, title = _MAP.get(
        type(exc),
        ("conversation/generic", 400, "Conversation error"),
    )
    problem = Problem(
        type=problem_type(slug),
        title=title,
        status=status,
        detail=str(exc),
    )
    body = problem.dump()
    body["instance"] = str(request.url.path)
    return JSONResponse(status_code=status, content=body, media_type=_MEDIA)


def register(app: FastAPI) -> None:
    app.add_exception_handler(errors.ConversationError, _handler)  # type: ignore[arg-type]


__all__ = ["register"]
