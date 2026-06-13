"""`/api/chatrooms/{id}/messages` + `/api/messages/*` — F.3 / F.4 / §22.10.

The room-scope ACL for send / read lives in
`contexts.conversation.application.access`. Moderator capability for edit /
delete comes from `Capability.MESSAGE_DELETE` + room/project role detection,
resolved here so the service stays free of AuthZ primitives.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.access import (
    RoomAccess,
    ensure_can_read,
    ensure_can_send,
    resolve_room_access,
)
from contexts.conversation.application.message_service import (
    EditAuthority,
    MessageService,
)
from contexts.conversation.application.triggers import evaluate_message_wakeups
from contexts.conversation.domain.models import Message
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
)
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.queue import enqueue

_log = logging.getLogger(__name__)

chatroom_router = APIRouter(prefix="/api/chatrooms", tags=["messages"])
message_router = APIRouter(prefix="/api/messages", tags=["messages"])


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #


# Upper bound on a single message body (API-7) — keeps oversized payloads from
# driving memory / DB load. A markdown chat message never approaches this.
_MAX_CONTENT_MD = 100_000
# Upper bound on attachments referenced by one message (API-7).
_MAX_ATTACHMENT_IDS = 100


class MessageSendIn(BaseModel):
    content_md: str = Field(min_length=1, max_length=_MAX_CONTENT_MD)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_ATTACHMENT_IDS)
    # `metadata` is system-populated (rag_chunks, tool_calls, compact_summary,
    # etc. — §21.1) and deliberately not accepted from clients.


class MessagePatchIn(BaseModel):
    content_md: str = Field(min_length=1, max_length=_MAX_CONTENT_MD)


class MessageOut(BaseModel):
    id: uuid.UUID
    chatroom_id: uuid.UUID
    sender_type: str
    sender_id: uuid.UUID | None
    content_md: str
    metadata: dict[str, Any]
    version: int
    created_at: str | None
    edited_at: str | None
    deleted_at: str | None


def _to_out(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        chatroom_id=m.chatroom_id,
        sender_type=m.sender_type.value,
        sender_id=m.sender_id,
        content_md=m.content_md,
        metadata=m.metadata,
        version=m.version,
        created_at=m.created_at.isoformat() if m.created_at else None,
        edited_at=m.edited_at.isoformat() if m.edited_at else None,
        deleted_at=m.deleted_at.isoformat() if m.deleted_at else None,
    )


def _parse_if_match(header: str) -> int:
    try:
        return int(header.strip().strip('"'))
    except ValueError as exc:
        raise HTTPException(
            status_code=412,
            detail=f"invalid If-Match: {header!r}",
        ) from exc


def _authority_from(
    access: RoomAccess,
    principal: Principal,
) -> EditAuthority:
    return EditAuthority(
        actor_user_id=principal.user_id,
        is_admin=principal.is_admin,
        is_moderator=access.is_moderator,
    )


# --------------------------------------------------------------------------- #
# Room-scoped: list + send
# --------------------------------------------------------------------------- #


@chatroom_router.get("/{chatroom_id}/messages")
async def list_messages(
    chatroom_id: uuid.UUID = Path(...),
    before: uuid.UUID | None = Query(default=None),
    since: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[MessageOut]:
    access = await resolve_room_access(
        db,
        principal=principal,
        chatroom_id=chatroom_id,
    )
    ensure_can_read(access, is_admin=principal.is_admin)
    service = MessageService(db)
    try:
        rows = await service.list(
            chatroom_id=chatroom_id,
            before=before,
            since=since,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [_to_out(m) for m in rows]


@chatroom_router.post(
    "/{chatroom_id}/messages",
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    body: MessageSendIn,
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> MessageOut:
    access = await resolve_room_access(
        db,
        principal=principal,
        chatroom_id=chatroom_id,
    )
    ensure_can_send(access, is_admin=principal.is_admin)
    service = MessageService(db)
    msg = await service.send(
        chatroom_id=chatroom_id,
        sender_user_id=principal.user_id,
        content_md=body.content_md,
        metadata=None,
        attachment_ids=list(body.attachment_ids) if body.attachment_ids else None,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # Durable-commit the message *before* dispatching wake-ups: the worker's
    # turn loads room history on a separate connection and must see this row
    # (db_session's trailing commit is then a no-op). K.3 link (a).
    await db.commit()
    await _dispatch_message_wakeups(db, chatroom_id)
    await _dispatch_message_workflow_signal(chatroom_id, body.content_md)
    return _to_out(msg)


async def _dispatch_message_wakeups(db: AsyncSession, chatroom_id: uuid.UUID) -> None:
    """Evaluate every_n_messages for the room's bound agents and enqueue a
    ``wakeup_agent`` turn for each that fired. Best-effort: a Redis / dispatch
    hiccup must never fail the user's send (the message is already committed)."""
    try:
        woken = await evaluate_message_wakeups(db, chatroom_id=chatroom_id, sender_is_user=True)
        for agent_id in woken:
            await enqueue(
                "wakeup_agent",
                str(agent_id),
                str(chatroom_id),
                "every_n_messages",
            )
    except Exception:  # pragma: no cover — defensive; exercised via wiring tier
        _log.warning(
            "wake-up dispatch failed for room %s; message persisted, no turn enqueued",
            chatroom_id,
            exc_info=True,
        )


async def _dispatch_message_workflow_signal(chatroom_id: uuid.UUID, content: str) -> None:
    """Fan a ``message_received`` signal to workflows (K.4): resume parked
    ``message_in_room`` waits and start dormant ``message_received`` triggers.
    Best-effort and post-commit — never fails the user's send."""
    try:
        await enqueue(
            "workflow_signal",
            "message",
            {
                "chatroom_id": str(chatroom_id),
                "sender_type": "user",
                "content": content,
            },
        )
    except Exception:  # pragma: no cover — defensive
        _log.warning("workflow message-signal dispatch failed for room %s", chatroom_id, exc_info=True)


# --------------------------------------------------------------------------- #
# Message-scoped: permalink / edit / delete
# --------------------------------------------------------------------------- #


async def _load_message_with_access(
    db: AsyncSession,
    principal: Principal,
    message_id: uuid.UUID,
) -> tuple[Message, RoomAccess]:
    service = MessageService(db)
    msg = await service.get(message_id)  # raises MessageNotFound → 404
    access = await resolve_room_access(
        db,
        principal=principal,
        chatroom_id=msg.chatroom_id,
    )
    return msg, access


@message_router.get("/{message_id}")
async def read_message(
    message_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> MessageOut:
    msg, access = await _load_message_with_access(db, principal, message_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    return _to_out(msg)


@message_router.patch("/{message_id}")
async def edit_message(
    body: MessagePatchIn,
    message_id: uuid.UUID = Path(...),
    if_match: str = Header(..., alias="If-Match"),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> MessageOut:
    msg, access = await _load_message_with_access(db, principal, message_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    expected = _parse_if_match(if_match)
    service = MessageService(db)
    updated = await service.edit(
        message_id=message_id,
        expected_version=expected,
        new_content_md=body.content_md,
        authority=_authority_from(access, principal),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    _ = msg  # preserved for clarity; service re-reads before updating
    return _to_out(updated)


@message_router.delete(
    "/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_message(
    message_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    msg, access = await _load_message_with_access(db, principal, message_id)
    ensure_can_read(access, is_admin=principal.is_admin)

    # Matrix row 20 MESSAGE_DELETE: own-only for members/guests, ALLOW for
    # project owners and org owners, plus Admin bypass. Evaluated inline so
    # the service stays AuthZ-agnostic (it only hard-deletes + audits).
    is_author = msg.sender_id == principal.user_id and msg.sender_type.value == "user"
    if not (principal.is_admin or access.is_moderator or is_author):
        _raise_forbidden("cannot delete a message you do not own")

    service = MessageService(db)
    await service.delete(
        message_id=message_id,
        authority=_authority_from(access, principal),
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


__all__ = ["chatroom_router", "message_router"]
