"""`/api/invites/*` — inbound view (§22.2a)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.interfaces.facade import IdentityFacade

# Re-export pattern: `invite_service` re-exports InviteState so routers may
# reach it without touching `contexts.tenancy.domain.*`.
from contexts.tenancy.application.invite_service import InviteService, InviteState
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/invites", tags=["invites"])


class InviteOut(BaseModel):
    id: uuid.UUID
    scope_type: str
    scope_id: uuid.UUID
    scope_name: str
    role: str
    invitee_email: str
    state: str
    expires_at: str
    created_at: str


def _to_out(inv, scope_name: str = "") -> InviteOut:
    return InviteOut(
        id=inv.id,
        scope_type=inv.scope_type.value,
        scope_id=inv.scope_id,
        scope_name=scope_name,
        role=inv.role,
        invitee_email=inv.invitee_email,
        state=inv.state.value,
        expires_at=inv.expires_at.isoformat(),
        created_at=inv.created_at.isoformat(),
    )


async def _resolve_profile(db: AsyncSession, user_id: uuid.UUID):
    """Fetch caller profile or raise 401 if the JWT principal has no row.

    Replaces `assert profile is not None` (BUG 2) and covers the corrupted-JWT
    case (BUG 3): a token signed for a user that no longer exists yields 401
    rather than crashing on `profile.email`.
    """
    profile = await IdentityFacade(db).get_profile(user_id)
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="authenticated principal has no user profile",
        )
    return profile


@router.get("")
async def list_inbox(
    state: Literal["pending", "accepted", "rejected"] = Query(default="pending"),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> list[InviteOut]:
    profile = await _resolve_profile(db, principal.user_id)
    service = InviteService(db)
    invites = await service.list_inbound(
        caller_email=profile.email,
        caller_user_id=principal.user_id,
        states=[InviteState(state)],
    )
    names = await service.scope_names(invites)
    return [_to_out(i, names.get((i.scope_type.value, i.scope_id), str(i.scope_id))) for i in invites]


@router.post("/{invite_id}/accept", status_code=status.HTTP_200_OK)
async def accept(
    invite_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> InviteOut:
    if not principal.email_verified:
        from shared_kernel.auth.dependencies import _raise_forbidden

        _raise_forbidden("email verification required (R6.11)")
    profile = await _resolve_profile(db, principal.user_id)
    service = InviteService(db)
    updated = await service.accept(
        invite_id=invite_id,
        caller_email=profile.email,
        caller_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(updated)


class AcceptByTokenIn(BaseModel):
    token: str


@router.post("/accept-by-token", status_code=status.HTTP_200_OK)
async def accept_by_token(
    body: AcceptByTokenIn,
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> InviteOut:
    """Redeem an invite from its emailed token link (R6.09).

    The token authorises acceptance (it proves the holder received the invite
    mail), so no email match is required — but the caller must still be logged
    in AND email-verified (R6.11), same as the by-id accept path.
    """
    if not principal.email_verified:
        from shared_kernel.auth.dependencies import _raise_forbidden

        _raise_forbidden("email verification required (R6.11)")
    service = InviteService(db)
    updated = await service.accept_by_token(
        token=body.token,
        caller_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(updated)


@router.post("/{invite_id}/reject", status_code=status.HTTP_200_OK)
async def reject(
    invite_id: uuid.UUID = Path(...),
    ctx: RequestContext = Depends(current_context),
    principal: Principal = Depends(current_principal),
    db: AsyncSession = Depends(db_session),
) -> InviteOut:
    profile = await _resolve_profile(db, principal.user_id)
    service = InviteService(db)
    updated = await service.reject(
        invite_id=invite_id,
        caller_email=profile.email,
        caller_user_id=principal.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    return _to_out(updated)


__all__ = ["router"]
