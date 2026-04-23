"""`/api/admin/ip-bans` — minimal CRUD so C.0 scope line "CRUD present" is
satisfied. The broader `/api/admin/*` surface lands in Phase I; only this
corner belongs to Phase C because it partners with the earliest-layer
middleware.
"""

from __future__ import annotations

import ipaddress
import uuid

from fastapi import Response, APIRouter, Body, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.application.ip_ban_service import IpBanService
from contexts.identity.interfaces.facade import IdentityFacade
from shared_kernel.auth import ip_bans as ip_ban_cache
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(prefix="/api/admin/ip-bans", tags=["admin"])


class IpBanIn(BaseModel):
    cidr: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=1024)


class IpBanOut(BaseModel):
    id: uuid.UUID
    cidr: str
    reason: str
    banned_at: str
    created_by_user_id: uuid.UUID | None


async def _require_admin(
    principal: Principal = Depends(current_principal),
) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return principal


@router.get("")
async def list_bans(
    _: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[IpBanOut]:
    facade = IdentityFacade(db)
    bans = await facade.list_ip_bans()
    return [
        IpBanOut(
            id=b.id, cidr=b.cidr, reason=b.reason,
            banned_at=b.banned_at.isoformat(),
            created_by_user_id=b.created_by_user_id,
        )
        for b in bans
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_ban(
    body: IpBanIn = Body(...),
    admin: Principal = Depends(_require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> IpBanOut:
    try:
        ipaddress.ip_network(body.cidr, strict=False)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=f"invalid CIDR: {body.cidr!r}",
        ) from exc
    service = IpBanService(db)
    created = await service.add(
        cidr=body.cidr, reason=body.reason, admin_user_id=admin.user_id,
        actor_ip=ctx.actor_ip, request_id=ctx.request_id,
    )
    # Invalidate the middleware cache so the next request sees the new CIDR.
    ip_ban_cache.invalidate()
    return IpBanOut(
        id=created.id, cidr=created.cidr, reason=created.reason,
        banned_at=created.banned_at.isoformat(),
        created_by_user_id=created.created_by_user_id,
    )


@router.delete("/{ban_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def remove_ban(
    ban_id: uuid.UUID = Path(...),
    admin: Principal = Depends(_require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = IpBanService(db)
    await service.remove(
        ban_id=ban_id, admin_user_id=admin.user_id,
        actor_ip=ctx.actor_ip, request_id=ctx.request_id,
    )
    ip_ban_cache.invalidate()


__all__ = ["router"]
