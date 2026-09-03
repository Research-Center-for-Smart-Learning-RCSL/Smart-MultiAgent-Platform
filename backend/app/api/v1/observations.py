"""`/api/chatrooms/{id}/observations` — SRS §28 observer output surface.

Creator-only (R28.02/R28.03): every handler resolves room access, applies the
read gate, then the creator gate. Guests are excluded inside
``ensure_room_creator`` even when a guest link grants room read.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.application.access import (
    ensure_can_read,
    ensure_room_creator,
    resolve_room_access,
)
from contexts.conversation.application.observation_service import (
    RELEASED_OBSERVATION_TYPE,
    ObservationService,
    ReleaseResult,
)
from contexts.conversation.application.triggers import evaluate_message_wakeups
from contexts.conversation.domain.models import AgentObservation
from contexts.conversation.interfaces import room_channel
from contexts.identity.interfaces import user_channel
from contexts.orchestration.infrastructure import pending_notify
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.queue import enqueue
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

observation_router = APIRouter(prefix="/api/chatrooms", tags=["observations"])

# Mirrors messages.py bounds: same body cap, same bounded wake fan-out.
_MAX_CONTENT_MD = 100_000
_MAX_TARGET_IDS = 50


class ReleaseIn(BaseModel):
    target: Literal["room", "agents"]
    agent_ids: list[uuid.UUID] = Field(default_factory=list, max_length=_MAX_TARGET_IDS)
    wake: bool = False
    content_override: str | None = Field(default=None, min_length=1, max_length=_MAX_CONTENT_MD)

    @field_validator("content_override", mode="before")
    @classmethod
    def _strip_override(cls, v: Any) -> Any:
        # Mirrors agents.py's model_id strip: a whitespace-only override would
        # otherwise become the released message body verbatim (O-9).
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("content_override must not be blank")
        return v

    @model_validator(mode="after")
    def _agents_target_needs_ids(self) -> ReleaseIn:
        if self.target == "agents" and not self.agent_ids:
            raise ValueError("target 'agents' requires at least one agent id")
        if self.target == "room" and self.agent_ids:
            raise ValueError("target 'room' does not take agent ids")
        return self


class ObservationOut(BaseModel):
    id: uuid.UUID
    chatroom_id: uuid.UUID
    agent_id: uuid.UUID
    content_md: str
    metadata: dict[str, Any]
    # [R28.15]. A typed column rather than another `metadata` key: the client
    # switches on `kind` to pick a renderer, and an unknown kind must survive a
    # frontend rollback, so the shape is part of the contract.
    blocks: list[dict[str, Any]]
    trigger: str
    trigger_message_id: uuid.UUID | None
    released_at: str | None
    release_target: dict[str, Any] | None
    released_by_user_id: uuid.UUID | None
    created_at: str | None


def _to_out(o: AgentObservation) -> ObservationOut:
    return ObservationOut(
        id=o.id,
        chatroom_id=o.chatroom_id,
        agent_id=o.agent_id,
        content_md=o.content_md,
        metadata=o.metadata,
        blocks=o.blocks,
        trigger=o.trigger,
        trigger_message_id=o.trigger_message_id,
        released_at=o.released_at.isoformat() if o.released_at else None,
        release_target=o.release_target,
        released_by_user_id=o.released_by_user_id,
        created_at=o.created_at.isoformat() if o.created_at else None,
    )


async def _notify_creator(
    service: ObservationService,
    chatroom_id: uuid.UUID,
    event: str,
    payload: dict[str, Any],
) -> None:
    """Publish one observation.* frame to the room creator's own user channel.

    Never the room channel: `_pubsub_fanin` delivers every room frame to every
    subscriber including guests, and an observation's existence is exactly what
    [R28.10] withholds. Best-effort throughout — the write is already committed
    by the time this runs, so a Redis hiccup must not surface as a failure.

    Shared by release and delete because F-14 was precisely the two drifting:
    one announced itself and the other did not.
    """
    try:
        recipient = await service.recipient_user_id(chatroom_id)
        if recipient is not None:
            await Publisher(user_channel(recipient)).emit(event, {"chatroom_id": str(chatroom_id), **payload})
    except Exception:
        _log.error("realtime publish failed for %s", event, exc_info=True)


async def _require_creator(
    db: AsyncSession,
    *,
    principal: Principal,
    chatroom_id: uuid.UUID,
) -> None:
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    ensure_room_creator(access, principal=principal)


@observation_router.get("/{chatroom_id}/observations")
async def list_observations(
    chatroom_id: uuid.UUID = Path(...),
    before: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[ObservationOut]:
    await _require_creator(db, principal=principal, chatroom_id=chatroom_id)
    try:
        rows = await ObservationService(db).list(chatroom_id=chatroom_id, before=before, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return [_to_out(o) for o in rows]


@observation_router.post("/{chatroom_id}/observations/{observation_id}/release")
async def release_observation(
    body: ReleaseIn,
    chatroom_id: uuid.UUID = Path(...),
    observation_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ObservationOut:
    await _require_creator(db, principal=principal, chatroom_id=chatroom_id)
    service = ObservationService(db)
    result = await service.release(
        chatroom_id=chatroom_id,
        observation_id=observation_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        target_room=body.target == "room",
        target_agent_ids=body.agent_ids,
        wake=body.wake,
        content_override=body.content_override,
        request_id=ctx.request_id,
    )
    # Durable-commit before any dispatch, mirroring send_message: the worker's
    # turn and the clients' delta refetch must see the committed rows.
    await db.commit()
    await _dispatch_release(db, chatroom_id=chatroom_id, service=service, result=result)
    return _to_out(result.observation)


@observation_router.delete(
    "/{chatroom_id}/observations/{observation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_observation(
    chatroom_id: uuid.UUID = Path(...),
    observation_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    await _require_creator(db, principal=principal, chatroom_id=chatroom_id)
    service = ObservationService(db)
    await service.delete(
        chatroom_id=chatroom_id,
        observation_id=observation_id,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    # F-14. Release announced itself and delete did not, so a second session of
    # the same creator kept rendering — and offering to act on — a row the
    # server no longer had. Commit before publishing for the reason the release
    # path does: `db_session` commits only after the handler returns, so a frame
    # sent from here would tell the other tab to re-read a write a later
    # rollback could still undo.
    await db.commit()
    await _notify_creator(
        service, chatroom_id, "observation.deleted", {"observation_id": str(observation_id)}
    )


async def _dispatch_release(
    db: AsyncSession,
    *,
    chatroom_id: uuid.UUID,
    service: ObservationService,
    result: ReleaseResult,
) -> None:
    """Post-commit fan-out. Best-effort throughout: the release is committed,
    so a Redis/queue hiccup must never surface as a failed release."""
    msg = result.message
    if msg is not None:
        # R28.06: a released room message is an ordinary message from the
        # wake-up system's point of view — same emit, same every_n dispatch.
        try:
            await Publisher(room_channel(chatroom_id)).emit(
                "message.created",
                {
                    "message_id": str(msg.id),
                    "sender_type": msg.sender_type.value,
                    "sender_id": None,
                    "created_at": msg.created_at.isoformat() if msg.created_at else None,
                },
            )
        except Exception:
            _log.error("realtime publish failed for released observation %s", msg.id, exc_info=True)
        try:
            fired = await evaluate_message_wakeups(db, chatroom_id=chatroom_id, sender_is_user=True)
            for agent_id in fired:
                await enqueue(
                    "wakeup_agent", str(agent_id), str(chatroom_id), "every_n_messages", str(msg.id)
                )
        except Exception:
            _log.warning("wake-up dispatch failed after release in room %s", chatroom_id, exc_info=True)
            with contextlib.suppress(Exception):
                await db.rollback()
    else:
        # R28.07: direct queue push, deliberately NOT A2AService.notify — the
        # A2A scope evaluator vetoes on either party's a2a_enabled flag, and
        # agent-level A2A policy must not block a creator-authorized release.
        # Best-effort per target: the release is committed, so one target's
        # Redis failure must neither surface as an error nor block the rest.
        for agent_id in result.target_agent_ids:
            try:
                await pending_notify.push(
                    agent_id,
                    {
                        "kind": RELEASED_OBSERVATION_TYPE,
                        "chatroom_id": str(chatroom_id),
                        "content": result.content,
                    },
                )
            except Exception:
                _log.error("pending-notify push failed for agent %s", agent_id, exc_info=True)
        if result.wake:
            # R28.07: explicit creator wake — trigger literal "release" bypasses
            # autostop like a mention (orchestration worker special-case).
            for agent_id in result.target_agent_ids:
                try:
                    await enqueue("wakeup_agent", str(agent_id), str(chatroom_id), "release", None)
                except Exception:
                    _log.warning("release wake enqueue failed for agent %s", agent_id, exc_info=True)
    await _notify_creator(
        service,
        chatroom_id,
        "observation.released",
        {
            "observation_id": str(result.observation.id),
            "target": result.observation.release_target,
        },
    )


__all__ = ["observation_router"]
