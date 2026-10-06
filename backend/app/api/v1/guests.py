"""Guest-link endpoints (F.9, R5.04, R13.06).

Two paths:

1. **Legacy enrollment** (registered users):
   ``POST /api/guest/{chatroom_id}/{guest_token}/enroll``
   Requires ``require_registered_principal`` -- a logged-in account, never an
   anonymous guest token, whose id would fail ``chatroom_guests.user_id``'s FK.

2. **Anonymous guest session** (R13.06, R13.06a, R13.06b):
   ``POST /api/guest/{chatroom_id}/{guest_token}/session`` -- public.
   ``POST /api/guest/{chatroom_id}/refresh`` -- public (reads refresh cookie).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Request, Response, status
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from contexts.conversation.application.guest_service import GuestService
from contexts.conversation.domain.errors import GuestTokenInvalid
from contexts.conversation.interfaces import room_channel
from contexts.conversation.interfaces.access import ensure_can_read, resolve_room_access
from contexts.conversation.interfaces.facade import ConversationFacade
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal, require_registered_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.realtime.pubsub import Publisher

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
    principal: Principal = Depends(require_registered_principal),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = GuestService(db)
    enrolled = await service.enroll(
        chatroom_id=chatroom_id,
        token=guest_token,
        principal=principal,
        display_name=body.display_name,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    if enrolled:
        await _emit_members_changed(db, chatroom_id)


# -- Anonymous guest session endpoints (R13.06) --


class GuestSessionIn(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    browser_id: str | None = Field(default=None, max_length=512)


class GuestSessionOut(BaseModel):
    access_token: str
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
    body: GuestSessionIn,
    response: Response,
    chatroom_id: uuid.UUID = Path(...),
    guest_token: str = Path(..., min_length=16, max_length=128),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
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
    if result.roster_changed:
        await _emit_members_changed(db, chatroom_id)

    settings = get_settings()
    response.set_cookie(
        key=_refresh_cookie_name(chatroom_id),
        value=result.refresh_token,
        httponly=True,
        secure=settings.security.session_cookie_secure,
        samesite=settings.security.session_cookie_samesite,
        max_age=settings.jwt.guest_refresh_ttl_seconds,
        path=f"/api/guest/{chatroom_id}",
    )

    return GuestSessionOut(
        access_token=result.access_token,
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
    response: Response,
    chatroom_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(db_session),
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
        secure=settings.security.session_cookie_secure,
        samesite=settings.security.session_cookie_samesite,
        max_age=settings.jwt.guest_refresh_ttl_seconds,
        path=f"/api/guest/{chatroom_id}",
    )

    return GuestRefreshOut(access_token=result.access_token)


# -- Guest display-name update (AC-21) --


class GuestDisplayNameIn(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)


class GuestDisplayNameOut(BaseModel):
    # The stored, normalised name, so the client shows what others will see
    # rather than what was typed.
    display_name: str


@router.put("/session/{guest_session_id}/display-name", response_model=GuestDisplayNameOut)
async def update_guest_display_name(
    body: GuestDisplayNameIn,
    guest_session_id: uuid.UUID = Path(...),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GuestDisplayNameOut:
    if not principal.is_guest or principal.user_id != guest_session_id or principal.chatroom_id is None:
        raise GuestTokenInvalid("principal does not match session")
    # The token outlives the room's guest links being turned off; a guest the
    # room no longer admits must not keep changing what its members see.
    access = await resolve_room_access(db, principal=principal, chatroom_id=principal.chatroom_id)
    ensure_can_read(access, is_admin=False)
    facade = ConversationFacade(db)
    result = await facade.update_guest_display_name(
        guest_session_id=guest_session_id,
        display_name=body.display_name,
    )
    if result.changed:
        await _emit_members_changed(db, principal.chatroom_id)
    return GuestDisplayNameOut(display_name=result.display_name)


async def _emit_members_changed(db: AsyncSession, chatroom_id: uuid.UUID) -> None:
    """Tell the room's open clients to re-read the participant roster ([R13.19]).

    Ids only, like ``chatroom.updated``: the room channel has no per-recipient
    filtering, and each client's re-read answers for that client. Commits first so
    the frame never announces a write a later rollback could undo, and swallows
    transport failure: the change is durable, and a missed refresh is reconciled
    by the client's reconnect re-read.
    """
    await db.commit()
    try:
        await Publisher(room_channel(chatroom_id)).emit(
            "chatroom.members_changed", {"chatroom_id": str(chatroom_id)}
        )
    except Exception:
        logger.bind(room_id=str(chatroom_id)).opt(exception=True).warning(
            "chatroom.members_changed emit failed"
        )


# -- Guest WS ticket (AC-7) --


class GuestWsTicketOut(BaseModel):
    ticket: str
    expires_in: int


@router.post("/ws-ticket", response_model=GuestWsTicketOut)
async def guest_ws_ticket(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> GuestWsTicketOut:
    """Mint a WS ticket for a guest. Requires a valid guest JWT in Bearer."""
    if not principal.is_guest or principal.chatroom_id is None:
        raise GuestTokenInvalid("not a guest principal")
    # A socket refused before accept reaches the browser as 1006, so this is the
    # one place a reconnecting guest can learn that links were turned off.
    await ConversationFacade(db).ensure_guest_room_open(principal.chatroom_id)

    from shared_kernel.realtime import mint_ws_ticket

    auth_header = request.headers.get("authorization", "")
    scheme, _, raw_token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        raise GuestTokenInvalid("missing bearer token")

    ticket, ttl = await mint_ws_ticket(raw_token)
    return GuestWsTicketOut(ticket=ticket, expires_in=ttl)


__all__ = ["router"]
