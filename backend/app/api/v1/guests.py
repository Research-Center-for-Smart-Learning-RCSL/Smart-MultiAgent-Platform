"""Guest-link endpoints (F.9, R5.04, R13.06).

Two paths:

1. **Legacy enrollment** (registered users):
   ``POST /api/guest/{chatroom_id}/{guest_token}/enroll``
   Requires ``current_principal`` -- the user must already be logged in.

2. **Anonymous guest session** (R13.06, R13.06a, R13.06b):
   ``POST /api/guest/{chatroom_id}/{guest_token}/session`` -- public.
   ``POST /api/guest/{chatroom_id}/refresh`` -- public (reads refresh cookie).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.conversation.application.guest_service import GuestService
from contexts.conversation.domain.errors import GuestTokenInvalid
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/guest", tags=["guests"])


class GuestEnrollIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)


@router.post(
    "/{chatroom_id}/{guest_token}/enroll",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def enroll_guest(
    chatroom_id: uuid.UUID = Path(...),
    guest_token: str = Path(..., min_length=16, max_length=128),
    body: GuestEnrollIn = GuestEnrollIn(),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = GuestService(db)
    await service.enroll(
        chatroom_id=chatroom_id,
        token=guest_token,
        user_id=principal.user_id,
        display_name=body.display_name,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )


# -- Anonymous guest session endpoints (R13.06) --


class GuestSessionIn(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    browser_id: str | None = Field(default=None, max_length=512)


class GuestSessionOut(BaseModel):
    access_token: str
    refresh_token: str
    guest_session_id: uuid.UUID
    display_name: str
    is_resuming: bool


class GuestRefreshOut(BaseModel):
    access_token: str


def _refresh_cookie_name(chatroom_id: uuid.UUID) -> str:
    return f"smap_guest_refresh_{chatroom_id}"


@router.post(
    "/{chatroom_id}/{guest_token}/session",
    status_code=status.HTTP_200_OK,
    response_model=GuestSessionOut,
)
async def create_guest_session(
    chatroom_id: uuid.UUID = Path(...),
    guest_token: str = Path(..., min_length=16, max_length=128),
    body: GuestSessionIn = ...,
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
    response: Response = ...,
) -> GuestSessionOut:
    facade = ConversationFacade(db)
    result = await facade.create_or_resume_guest_session(
        chatroom_id=chatroom_id,
        guest_token=guest_token,
        display_name=body.display_name,
        browser_id=body.browser_id,
        remote_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )

    settings = get_settings()
    response.set_cookie(
        key=_refresh_cookie_name(chatroom_id),
        value=result.refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=settings.jwt.guest_refresh_ttl_seconds,
        path=f"/api/guest/{chatroom_id}",
    )

    return GuestSessionOut(
        access_token=result.access_token,
        refresh_token=result.refresh_token,
        guest_session_id=result.guest_session_id,
        display_name=result.display_name,
        is_resuming=result.is_resuming,
    )


@router.post(
    "/{chatroom_id}/refresh",
    status_code=status.HTTP_200_OK,
    response_model=GuestRefreshOut,
)
async def refresh_guest_session(
    request: Request,
    chatroom_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
    response: Response = ...,
) -> GuestRefreshOut:
    cookie_name = _refresh_cookie_name(chatroom_id)
    refresh_token = request.cookies.get(cookie_name)
    if not refresh_token:
        raise GuestTokenInvalid(str(chatroom_id))

    facade = ConversationFacade(db)
    result = await facade.refresh_guest_session(
        chatroom_id=chatroom_id,
        refresh_token=refresh_token,
    )

    settings = get_settings()
    response.set_cookie(
        key=cookie_name,
        value=result.refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=settings.jwt.guest_refresh_ttl_seconds,
        path=f"/api/guest/{chatroom_id}",
    )

    return GuestRefreshOut(access_token=result.access_token)


__all__ = ["router"]
