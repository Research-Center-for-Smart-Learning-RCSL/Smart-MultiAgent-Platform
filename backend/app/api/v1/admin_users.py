"""`/api/admin/users` + `/api/admin/admins` — user & admin-role management."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from contexts.identity.application.admin_service import (
    AdminService,
    LastAdminError,
    SelfTargetError,
)
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.errors.problem import Problem, problem_type

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class UserSummaryOut(BaseModel):
    id: uuid.UUID
    email: str
    status: str
    email_verified: bool
    created_at: str


class UserDetailOut(BaseModel):
    id: uuid.UUID
    email: str
    status: str
    email_verified: bool
    is_admin: bool
    banned_reason: str | None
    banned_at: str | None
    deleted_at: str | None
    last_login_at: str | None
    created_at: str
    org_ids: list[uuid.UUID]
    project_ids: list[uuid.UUID]


class BanIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class AdminPromoteIn(BaseModel):
    user_id: uuid.UUID


class AdminEntryOut(BaseModel):
    user_id: uuid.UUID
    promoted_by_user_id: uuid.UUID | None
    promoted_at: str


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get("/users")
async def list_users(
    q: str | None = Query(None, max_length=200),
    user_status: str | None = Query(None, alias="status"),
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[UserSummaryOut]:
    service = AdminService(db)
    users = await service.search_users(q=q, status=user_status, cursor=cursor, limit=limit)
    return [
        UserSummaryOut(
            id=u.id,
            email=u.email,
            status=u.status.value,
            email_verified=u.email_verified,
            created_at=u.created_at.isoformat(),
        )
        for u in users
    ]


@router.get("/users/{user_id}")
async def get_user(
    user_id: uuid.UUID = Path(...),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> UserDetailOut:
    service = AdminService(db)
    detail = await service.get_user_detail(user_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="User not found")
    u = detail.user
    return UserDetailOut(
        id=u.id,
        email=u.email,
        status=u.status.value,
        email_verified=u.email_verified,
        is_admin=detail.is_admin,
        banned_reason=u.banned_reason,
        banned_at=u.banned_at.isoformat() if u.banned_at else None,
        deleted_at=u.deleted_at.isoformat() if u.deleted_at else None,
        last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
        created_at=u.created_at.isoformat(),
        org_ids=detail.org_ids,
        project_ids=detail.project_ids,
    )


@router.post("/users/{user_id}/ban", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def ban_user(
    user_id: uuid.UUID = Path(...),
    body: BanIn = Body(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = AdminService(db)
    try:
        await service.ban_user(
            target_user_id=user_id,
            reason=body.reason,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except SelfTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/{user_id}/unban", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def unban_user(
    user_id: uuid.UUID = Path(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = AdminService(db)
    try:
        await service.unban_user(
            target_user_id=user_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/{user_id}/delete", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def soft_delete_user(
    user_id: uuid.UUID = Path(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = AdminService(db)
    try:
        await service.soft_delete_user(
            target_user_id=user_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except SelfTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/{user_id}/hard-delete", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def hard_delete_user(
    user_id: uuid.UUID = Path(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = AdminService(db)
    try:
        await service.hard_delete_user(
            target_user_id=user_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except SelfTargetError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        msg = str(exc)
        code = 404 if "not found" in msg else 409
        raise HTTPException(status_code=code, detail=msg) from exc


# ---------------------------------------------------------------------------
# Admins CRUD
# ---------------------------------------------------------------------------


@router.get("/admins")
async def list_admins(
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[AdminEntryOut]:
    service = AdminService(db)
    admins = await service.list_admins()
    return [
        AdminEntryOut(
            user_id=a.user_id,
            promoted_by_user_id=a.promoted_by_user_id,
            promoted_at=a.promoted_at.isoformat(),
        )
        for a in admins
    ]


@router.post("/admins", status_code=status.HTTP_201_CREATED)
async def promote_admin(
    body: AdminPromoteIn = Body(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> AdminEntryOut:
    service = AdminService(db)
    try:
        entry = await service.promote_admin(
            target_user_id=body.user_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return AdminEntryOut(
        user_id=entry.user_id,
        promoted_by_user_id=entry.promoted_by_user_id,
        promoted_at=entry.promoted_at.isoformat(),
    )


@router.delete("/admins/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def demote_admin(
    user_id: uuid.UUID = Path(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    service = AdminService(db)
    try:
        await service.demote_admin(
            target_user_id=user_id,
            admin_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        )
    except LastAdminError as exc:
        raise HTTPException(
            status_code=409,
            detail=Problem(
                type=problem_type("admin/last-admin"),
                title="Last Admin cannot be demoted",
                status=409,
                extras={"error": "last_admin"},
            ).dump(),
        ) from exc
