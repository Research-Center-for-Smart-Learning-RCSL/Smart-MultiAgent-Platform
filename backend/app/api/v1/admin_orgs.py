"""`/api/admin/orgs` — org listing, force-delete, OC transfer."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from shared_kernel import audit
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class OrgSummaryOut(BaseModel):
    id: uuid.UUID
    name: str
    creator_user_id: uuid.UUID
    deleted_at: str | None
    created_at: str


class ForceTransferIn(BaseModel):
    target_user_id: uuid.UUID


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@router.get("/orgs")
async def list_orgs(
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[OrgSummaryOut]:
    import sqlalchemy as sa

    if cursor is not None:
        q = sa.text(
            "SELECT id, name, creator_user_id, deleted_at, created_at "
            "FROM orgs "
            "WHERE created_at < COALESCE("
            "  (SELECT created_at FROM orgs WHERE id = :cursor), '1970-01-01'::timestamptz"
            ") "
            "OR (created_at = (SELECT created_at FROM orgs WHERE id = :cursor) AND id < :cursor) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        ).bindparams(limit=limit, cursor=cursor)
    else:
        q = sa.text(
            "SELECT id, name, creator_user_id, deleted_at, created_at "
            "FROM orgs ORDER BY created_at DESC, id DESC LIMIT :limit"
        ).bindparams(limit=limit)
    rows = (await db.execute(q)).all()
    return [
        OrgSummaryOut(
            id=r[0],
            name=r[1],
            creator_user_id=r[2],
            deleted_at=r[3].isoformat() if r[3] else None,
            created_at=r[4].isoformat(),
        )
        for r in rows
    ]


@router.post("/orgs/{org_id}/force-delete", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def force_delete_org(
    org_id: uuid.UUID = Path(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    import sqlalchemy as sa

    from shared_kernel.auth.clients import now as _now

    result = await db.execute(
        sa.text("UPDATE orgs SET deleted_at = :now WHERE id = :id AND deleted_at IS NULL").bindparams(
            now=_now(), id=org_id
        )
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail="Org not found or already deleted")
    await audit.emit(
        db,
        audit.AuditEvent(
            action="admin.force_delete_org",
            actor_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="org",
            resource_id=org_id,
            request_id=ctx.request_id,
        ),
    )


@router.post("/orgs/{org_id}/force-transfer-original-creator")
async def force_transfer_oc(
    org_id: uuid.UUID = Path(...),
    body: ForceTransferIn = Body(...),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> dict:
    import sqlalchemy as sa

    await db.execute(
        sa.text(
            "UPDATE org_members SET is_original_creator = false "
            "WHERE org_id = :oid AND is_original_creator = true"
        ).bindparams(oid=org_id)
    )
    result = await db.execute(
        sa.text(
            "UPDATE org_members SET is_original_creator = true " "WHERE org_id = :oid AND user_id = :uid"
        ).bindparams(oid=org_id, uid=body.target_user_id)
    )
    if result.rowcount == 0:  # type: ignore[attr-defined]
        raise HTTPException(status_code=404, detail="Target user not a member of this org")
    await audit.emit(
        db,
        audit.AuditEvent(
            action="org.original_creator_force_transferred",
            actor_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="org",
            resource_id=org_id,
            metadata={"target_user_id": str(body.target_user_id)},
            request_id=ctx.request_id,
        ),
    )
    return {"transferred": True}
