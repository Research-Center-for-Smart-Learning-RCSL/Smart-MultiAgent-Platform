"""`/api/admin/users/{user_id}/impersonate` — impersonation start/stop (I.5)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from contexts.identity.application.admin_service import SelfTargetError
from contexts.identity.application.impersonation_service import ImpersonationService
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ImpersonateOut(BaseModel):
    session_id: uuid.UUID
    access_token: str


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/impersonate")
async def impersonate(
    user_id: uuid.UUID = Path(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> ImpersonateOut:
    service = ImpersonationService(db)
    try:
        session, access_token = await service.start(
            admin_user_id=admin.user_id,
            target_user_id=user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except SelfTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ImpersonateOut(session_id=session.id, access_token=access_token)


@router.post(
    "/users/{user_id}/end-impersonate",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def end_impersonate(
    user_id: uuid.UUID = Path(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = ImpersonationService(db)
    ended = await service.end(
        admin_user_id=admin.user_id,
        target_user_id=user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    if not ended:
        raise HTTPException(status_code=404, detail="No active impersonation session")
