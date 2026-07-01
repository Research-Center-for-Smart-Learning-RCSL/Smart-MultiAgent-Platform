"""`/api/chatrooms/{id}/messages` + `/api/messages/*` — F.3 / F.4 / §22.10.

The room-scope ACL for send / read lives in
`contexts.conversation.application.access`. Moderator capability for edit /
delete comes from `Capability.MESSAGE_DELETE` + room/project role detection,
resolved here so the service stays free of AuthZ primitives.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.attachments import AttachmentOut, to_attachment_out
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
from contexts.conversation.application.triggers import (
    evaluate_message_wakeups,
    filter_mentioned_bound_agents,
    list_bound_agent_ids,
)
from contexts.conversation.domain.models import Message
from contexts.conversation.interfaces import room_channel
from contexts.knowledge.interfaces.facade import KnowledgeFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import (
    _raise_forbidden,
    current_context,
    current_principal,
)
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.queue import enqueue
from shared_kernel.realtime.pubsub import Publisher

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
# Upper bound on @mentioned agents per message — a chat message never names
# more than a handful, and each spawns a turn, so keep the fan-out bounded.
_MAX_MENTION_IDS = 50


class MessageSendIn(BaseModel):
    content_md: str = Field(default="", max_length=_MAX_CONTENT_MD)
    attachment_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_ATTACHMENT_IDS)
    # Agents the author explicitly summoned via @mention. Resolved client-side
    # against the room's bound agents; the server re-validates each is actually
    # bound before waking it. A mention is an explicit call, so it wakes the
    # agent regardless of its wakeup triggers (R15.01 is the auto path).
    mention_agent_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_MENTION_IDS)
    # `metadata` is system-populated (rag_chunks, tool_calls, compact_summary,
    # etc. — §21.1) and deliberately not accepted from clients.

    @model_validator(mode="after")
    def _require_content_or_attachments(self) -> MessageSendIn:
        if not self.content_md and not self.attachment_ids:
            raise ValueError("message must have content_md or attachment_ids")
        return self


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
    # Attachments bound to this message. Includes expired/quarantined rows so the
    # client can render `[attachment expired]` (R13.11) rather than a dead link;
    # the presigned download URL is fetched lazily via GET /api/attachments/{id}.
    attachments: list[AttachmentOut] = []


def _to_out(m: Message, attachments: Sequence[object] = ()) -> MessageOut:
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
        attachments=[to_attachment_out(a) for a in attachments],
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
    by_msg = await service.list_attachments_for([m.id for m in rows])
    return [_to_out(m, by_msg.get(m.id, [])) for m in rows]


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
    try:
        await Publisher(room_channel(chatroom_id)).emit(
            "message.created",
            {
                "message_id": str(msg.id),
                "sender_type": msg.sender_type.value,
                "sender_id": str(msg.sender_id) if msg.sender_id else None,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            },
        )
    except Exception:
        _log.error("realtime publish failed for message.created %s", msg.id, exc_info=True)
    # Fetch the room's agent binding once and share it across both wake-up
    # evaluations (every_n + @mention) so a mention send issues one query, not two.
    bound_ids = await _list_bound_agents_for_dispatch(db, chatroom_id)
    woken = await _dispatch_message_wakeups(db, chatroom_id, bound_ids, trigger_message_id=msg.id)
    await _dispatch_graphrag_builds(db, chatroom_id, bound_ids)
    await _dispatch_mention_wakeups(
        db,
        chatroom_id,
        body.mention_agent_ids,
        already_woken=woken,
        bound_agent_ids=bound_ids,
        trigger_message_id=msg.id,
    )
    await _dispatch_message_workflow_signal(chatroom_id, body.content_md)
    return _to_out(msg, await service.list_attachments(msg.id))


async def _rollback_quietly(db: AsyncSession) -> None:
    """Best-effort rollback that never raises. The message is already committed,
    so a failed post-commit wake-up dispatch must not surface to the caller."""
    with contextlib.suppress(Exception):
        await db.rollback()


async def _list_bound_agents_for_dispatch(db: AsyncSession, chatroom_id: uuid.UUID) -> list[uuid.UUID] | None:
    """Fetch the room binding once for the post-commit wake-up dispatch. Best-
    effort: on failure return None so each evaluator falls back to its own
    (try-protected) fetch rather than the send failing."""
    try:
        return await list_bound_agent_ids(db, chatroom_id)
    except Exception:  # pragma: no cover — defensive; exercised via wiring tier
        await _rollback_quietly(db)
        return None


async def _dispatch_message_wakeups(
    db: AsyncSession,
    chatroom_id: uuid.UUID,
    bound_agent_ids: list[uuid.UUID] | None = None,
    *,
    trigger_message_id: uuid.UUID,
) -> set[uuid.UUID]:
    """Evaluate every_n_messages for the room's bound agents and enqueue a
    ``wakeup_agent`` turn for each that fired. Returns the woken agent ids so the
    caller can skip re-waking the same agent via @mention. Best-effort: a Redis /
    dispatch hiccup must never fail the user's send (the message is committed)."""
    woken: set[uuid.UUID] = set()
    try:
        fired = await evaluate_message_wakeups(
            db, chatroom_id=chatroom_id, sender_is_user=True, bound_agent_ids=bound_agent_ids
        )
        for agent_id in fired:
            await enqueue(
                "wakeup_agent",
                str(agent_id),
                str(chatroom_id),
                "every_n_messages",
                str(trigger_message_id),
            )
            woken.add(agent_id)
    except Exception:  # pragma: no cover — defensive; exercised via wiring tier
        _log.warning(
            "wake-up dispatch failed for room %s; message persisted, no turn enqueued",
            chatroom_id,
            exc_info=True,
        )
        await _rollback_quietly(db)
    return woken


async def _dispatch_graphrag_builds(
    db: AsyncSession,
    chatroom_id: uuid.UUID,
    bound_agent_ids: list[uuid.UUID] | None = None,
) -> None:
    """Evaluate GraphRAG message triggers and enqueue builds post-commit.

    GraphRAG builds are background maintenance; a Redis / queue / DB hiccup must
    never turn a successfully persisted chat message into a client-visible error.
    """
    if not bound_agent_ids:
        return
    try:
        fired = await KnowledgeFacade(db).evaluate_graphrag_message_triggers(agent_ids=bound_agent_ids)
        for trigger in fired:
            await enqueue(
                "graphrag_build",
                config_id=str(trigger.config_id),
                triggered_by=trigger.triggered_by,
            )
    except Exception:  # pragma: no cover - defensive; exercised via wiring tier
        _log.warning(
            "GraphRAG build dispatch failed for room %s; message persisted",
            chatroom_id,
            exc_info=True,
        )
        await _rollback_quietly(db)


async def _dispatch_mention_wakeups(
    db: AsyncSession,
    chatroom_id: uuid.UUID,
    mention_agent_ids: list[uuid.UUID],
    *,
    already_woken: set[uuid.UUID],
    bound_agent_ids: list[uuid.UUID] | None = None,
    trigger_message_id: uuid.UUID,
) -> None:
    """Wake each @mentioned agent that is bound to the room and wasn't already
    woken by every_n_messages. A mention is an explicit call, so the turn runs
    regardless of the agent's wakeup config (an inert / call-only agent still
    replies when summoned). Best-effort and post-commit — never fails the send."""
    if not mention_agent_ids:
        return
    try:
        bound = await filter_mentioned_bound_agents(
            db,
            chatroom_id=chatroom_id,
            mention_agent_ids=mention_agent_ids,
            bound_agent_ids=bound_agent_ids,
        )
        for agent_id in bound:
            if agent_id in already_woken:
                continue
            await enqueue(
                "wakeup_agent",
                str(agent_id),
                str(chatroom_id),
                "mention",
                str(trigger_message_id),
            )
    except Exception:  # pragma: no cover — defensive; exercised via wiring tier
        _log.warning(
            "mention wake-up dispatch failed for room %s; message persisted",
            chatroom_id,
            exc_info=True,
        )
        await _rollback_quietly(db)


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
    return _to_out(msg, await MessageService(db).list_attachments(msg.id))


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
    authority = _authority_from(access, principal)
    service = MessageService(db)
    updated = await service.edit(
        message_id=message_id,
        expected_version=expected,
        new_content_md=body.content_md,
        authority=authority,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    by_moderator = (authority.is_admin or authority.is_moderator) and authority.actor_user_id != msg.sender_id
    await db.commit()
    try:
        await Publisher(room_channel(msg.chatroom_id)).emit(
            "message.updated",
            {
                "message_id": str(message_id),
                "version": updated.version,
                "edited_at": updated.edited_at.isoformat() if updated.edited_at else None,
                "by_moderator": by_moderator,
            },
        )
    except Exception:
        _log.error("realtime publish failed for message.updated %s", message_id, exc_info=True)
    return _to_out(updated, await service.list_attachments(updated.id))


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
    await db.commit()
    try:
        await Publisher(room_channel(msg.chatroom_id)).emit(
            "message.deleted",
            {"message_id": str(message_id)},
        )
    except Exception:
        _log.error("realtime publish failed for message.deleted %s", message_id, exc_info=True)


__all__ = ["chatroom_router", "message_router"]
