"""A room's autonomous class flow settings ([R15.27], [R30.46]).

- ``GET   /api/chatrooms/{id}/class-start``            the setting and this class's state
- ``PATCH /api/chatrooms/{id}/class-start``            change it; ``reset`` re-arms another class
- ``GET   /api/chatrooms/{id}/activity-time-limits``   the room's per-type overrides
- ``PUT   /api/chatrooms/{id}/activity-time-limits``   replace them

Room-creator only, like the wake-up and grant routes: each changes when the
class moves on for every participant.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.chatrooms import _assert_activity_types_in_project
from app.wiring.platform_wakes import TRIGGER_CLASS_START, enqueue_platform_wake
from contexts.activities.interfaces.facade import TIME_LIMIT_MINUTES_RANGE, ActivitiesFacade
from contexts.conversation.application.access import (
    RoomAccess,
    ensure_can_read,
    ensure_room_creator,
    resolve_room_access,
)
from contexts.conversation.interfaces.facade import (
    CLASS_START_MAX_WAIT_MINUTES_RANGE,
    CLASS_START_MIN_PARTICIPANTS_RANGE,
    ClassStartConfig,
    ClassStartState,
    ConversationFacade,
)
from contexts.orchestration.interfaces.facade import OrchestrationFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

_log = logging.getLogger(__name__)

chatroom_router = APIRouter(prefix="/api/chatrooms", tags=["chatrooms"])

#: Bounds one PUT; a room reaches far fewer types than this.
_MAX_TIME_LIMIT_ROWS = 200


class ClassStartOut(BaseModel):
    configured: bool
    enabled: bool
    min_participants: int | None
    max_wait_minutes: int | None
    first_arrival_at: datetime | None
    started_at: datetime | None


class ClassStartPatchIn(BaseModel):
    """Omitted fields keep their stored value; a room with no setting needs all three."""

    enabled: bool | None = None
    min_participants: int | None = Field(
        default=None, ge=CLASS_START_MIN_PARTICIPANTS_RANGE[0], le=CLASS_START_MIN_PARTICIPANTS_RANGE[1]
    )
    max_wait_minutes: int | None = Field(
        default=None, ge=CLASS_START_MAX_WAIT_MINUTES_RANGE[0], le=CLASS_START_MAX_WAIT_MINUTES_RANGE[1]
    )
    reset: bool = False


class ActivityTimeLimitItem(BaseModel):
    activity_type_id: uuid.UUID
    #: Null is an explicit "no limit" that overrides the type default.
    time_limit_minutes: int | None = Field(ge=TIME_LIMIT_MINUTES_RANGE[0], le=TIME_LIMIT_MINUTES_RANGE[1])


class ActivityTimeLimitsBody(BaseModel):
    limits: list[ActivityTimeLimitItem] = Field(max_length=_MAX_TIME_LIMIT_ROWS)


def _class_start_out(state: ClassStartState) -> ClassStartOut:
    config = state.config
    return ClassStartOut(
        configured=config is not None,
        enabled=bool(config and config.enabled),
        min_participants=config.min_participants if config else None,
        max_wait_minutes=config.max_wait_minutes if config else None,
        first_arrival_at=state.first_arrival_at,
        started_at=state.started_at,
    )


async def _creator_access(db: AsyncSession, principal: Principal, chatroom_id: uuid.UUID) -> RoomAccess:
    """403 for a caller who cannot read the room or is not its creator; 404 for a missing room (D-4)."""
    access = await resolve_room_access(db, principal=principal, chatroom_id=chatroom_id)
    ensure_can_read(access, is_admin=principal.is_admin)
    ensure_room_creator(access, principal=principal)
    return access


@chatroom_router.get("/{chatroom_id}/class-start", response_model=ClassStartOut)
async def get_class_start(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ClassStartOut:
    await _creator_access(db, principal, chatroom_id)
    state = await ConversationFacade(db).class_start_state(chatroom_id)
    if state is None:
        raise HTTPException(status_code=404, detail="chatroom not found")
    return _class_start_out(state)


@chatroom_router.patch("/{chatroom_id}/class-start", response_model=ClassStartOut)
async def patch_class_start(
    body: ClassStartPatchIn,
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ClassStartOut:
    await _creator_access(db, principal, chatroom_id)
    facade = ConversationFacade(db)
    current = await facade.class_start_state(chatroom_id)
    if current is None:
        raise HTTPException(status_code=404, detail="chatroom not found")
    config = ClassStartConfig.patched(
        current.config,
        enabled=body.enabled,
        min_participants=body.min_participants,
        max_wait_minutes=body.max_wait_minutes,
    )
    if config is None:
        raise HTTPException(
            status_code=422,
            detail="enabled, min_participants and max_wait_minutes are required the first time",
        )
    state = await facade.write_class_start(
        chatroom_id,
        config=config,
        reset=body.reset,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    if state is None:
        raise HTTPException(status_code=404, detail="chatroom not found")
    await db.commit()
    if state.armed:
        # Students who connected before the room was armed started no wait, and
        # no later connection may come to start it or to open the class.
        state = await _check_class_start_now(db, chatroom_id) or state
    return _class_start_out(state)


async def _check_class_start_now(db: AsyncSession, chatroom_id: uuid.UUID) -> ClassStartState | None:
    """Best-effort: the setting is saved either way, and the sweep or the next
    connection retries the check."""
    try:
        wake = await OrchestrationFacade(db).on_class_start_check(room_id=chatroom_id)
        await db.commit()
        if wake is not None:
            await enqueue_platform_wake(chatroom_id, TRIGGER_CLASS_START, wake)
        return await ConversationFacade(db).class_start_state(chatroom_id)
    except Exception:
        await db.rollback()
        _log.warning(
            "class start check after a setting change failed for room %s", chatroom_id, exc_info=True
        )
        return None


@chatroom_router.get("/{chatroom_id}/activity-time-limits", response_model=ActivityTimeLimitsBody)
async def get_activity_time_limits(
    chatroom_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityTimeLimitsBody:
    await _creator_access(db, principal, chatroom_id)
    limits = await ActivitiesFacade(db).room_time_limits(chatroom_id)
    return ActivityTimeLimitsBody(
        limits=[
            ActivityTimeLimitItem(activity_type_id=type_id, time_limit_minutes=minutes)
            for type_id, minutes in sorted(limits.items(), key=lambda kv: str(kv[0]))
        ]
    )


@chatroom_router.put("/{chatroom_id}/activity-time-limits", response_model=ActivityTimeLimitsBody)
async def put_activity_time_limits(
    body: ActivityTimeLimitsBody,
    chatroom_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> ActivityTimeLimitsBody:
    """Replace the overrides. A type left out falls back to its default.

    Every type id is resolved for the room's own project first, as the grant
    route does, so another tenant's type id is a 422 and never a stored row.
    """
    access = await _creator_access(db, principal, chatroom_id)
    ids = [item.activity_type_id for item in body.limits]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="each activity type may appear once")
    await _assert_activity_types_in_project(db, access.project_id, ids)
    limits = {item.activity_type_id: item.time_limit_minutes for item in body.limits}
    await ActivitiesFacade(db).replace_room_time_limits(
        chatroom_id,
        limits,
        actor_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    await db.commit()
    return body


__all__ = ["chatroom_router"]
