"""`/api/admin/projects` + `/api/admin/restore` — project listing & restore."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from contexts.identity.application.admin_service import AdminService
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class ProjectSummaryOut(BaseModel):
    id: uuid.UUID
    name: str
    owner_user_id: uuid.UUID | None
    owner_org_id: uuid.UUID | None
    deleted_at: str | None
    created_at: str


class RestoreOut(BaseModel):
    restored: bool


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@router.get("/projects")
async def list_projects(
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[ProjectSummaryOut]:
    import sqlalchemy as sa

    if cursor is not None:
        q = sa.text(
            "SELECT id, name, owner_user_id, owner_org_id, deleted_at, created_at "
            "FROM projects "
            "WHERE created_at < (SELECT created_at FROM projects WHERE id = :cursor) "
            "OR (created_at = (SELECT created_at FROM projects WHERE id = :cursor) AND id < :cursor) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        ).bindparams(limit=limit, cursor=cursor)
    else:
        q = sa.text(
            "SELECT id, name, owner_user_id, owner_org_id, deleted_at, created_at "
            "FROM projects ORDER BY created_at DESC, id DESC LIMIT :limit"
        ).bindparams(limit=limit)
    rows = (await db.execute(q)).all()
    return [
        ProjectSummaryOut(
            id=r[0],
            name=r[1],
            owner_user_id=r[2],
            owner_org_id=r[3],
            deleted_at=r[4].isoformat() if r[4] else None,
            created_at=r[5].isoformat(),
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Restore (R8.13)
# ---------------------------------------------------------------------------


@router.post("/restore/{resource_type}/{resource_id}")
async def restore_resource(
    resource_type: str = Path(...),
    resource_id: uuid.UUID = Path(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> RestoreOut:
    service = AdminService(db)
    restored = await service.restore_resource(
        resource_type=resource_type,
        resource_id=resource_id,
        admin_user_id=admin.user_id,
        actor_ip=ctx.actor_ip,
        request_id=ctx.request_id,
    )
    if not restored:
        raise HTTPException(status_code=404, detail="Resource not found or not soft-deleted")
    return RestoreOut(restored=True)
