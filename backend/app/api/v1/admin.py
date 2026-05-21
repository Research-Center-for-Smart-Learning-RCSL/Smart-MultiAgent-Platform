"""`/api/admin/*` — full admin surface (I.1, I.2, I.5).

Consolidates all admin-only endpoints except IP bans (already in
`admin_ip_bans.py`) and GraphRAG reset (already in `graphrag.py`).

AuthZ: every handler calls `_require_admin` which checks `principal.is_admin`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.audit.domain.models import AuditFilter
from contexts.audit.interfaces.facade import AuditFacade
from contexts.identity.application.admin_service import (
    AdminService,
    LastAdminError,
)
from contexts.identity.application.impersonation_service import ImpersonationService
from shared_kernel import audit
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context, current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.errors.problem import Problem, problem_type

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# AuthZ dependency
# ---------------------------------------------------------------------------


async def _require_admin(
    principal: Principal = Depends(current_principal),
) -> Principal:
    if not principal.is_admin:
        raise HTTPException(status_code=403, detail="Admin only")
    return principal


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


class ForceTransferIn(BaseModel):
    target_user_id: uuid.UUID


class RestoreOut(BaseModel):
    restored: bool


class AuditEntryOut(BaseModel):
    id: int
    actor_user_id: uuid.UUID | None
    actor_ip: str | None
    action: str
    resource_type: str | None
    resource_id: uuid.UUID | None
    metadata: dict
    session_id: uuid.UUID | None
    request_id: uuid.UUID | None
    created_at: str


class AuditPageOut(BaseModel):
    items: list[AuditEntryOut]
    next_cursor: int | None


class RateLimitPatchIn(BaseModel):
    # API-7: `ge=1` on both numerics — a 0 / negative `max_count` would disable
    # rate limiting platform-wide; a non-positive window is meaningless.
    window_sec: int | None = Field(default=None, ge=1, le=86_400)
    max_count: int | None = Field(default=None, ge=1)
    scope: str | None = None


class ImpersonateOut(BaseModel):
    session_id: uuid.UUID
    access_token: str


class MetricsOut(BaseModel):
    total_users: int
    total_orgs: int
    total_projects: int
    total_audit_entries: int


class OrgSummaryOut(BaseModel):
    id: uuid.UUID
    name: str
    creator_user_id: uuid.UUID
    deleted_at: str | None
    created_at: str


class ProjectSummaryOut(BaseModel):
    id: uuid.UUID
    name: str
    owner_user_id: uuid.UUID | None
    owner_org_id: uuid.UUID | None
    deleted_at: str | None
    created_at: str


class RateLimitPolicyOut(BaseModel):
    key: str
    window_sec: int
    max_count: int
    scope: str
    updated_at: str


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@router.get("/users")
async def list_users(
    q: str | None = Query(None, max_length=200),
    user_status: str | None = Query(None, alias="status"),
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(_require_admin),
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
    _: Principal = Depends(_require_admin),
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
    admin: Principal = Depends(_require_admin),
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
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/{user_id}/unban", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def unban_user(
    user_id: uuid.UUID = Path(...),
    admin: Principal = Depends(_require_admin),
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
    admin: Principal = Depends(_require_admin),
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
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/users/{user_id}/hard-delete", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def hard_delete_user(
    user_id: uuid.UUID = Path(...),
    admin: Principal = Depends(_require_admin),
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
    except ValueError as exc:
        msg = str(exc)
        code = 404 if "not found" in msg else 409
        raise HTTPException(status_code=code, detail=msg) from exc


# ---------------------------------------------------------------------------
# Impersonation (I.5)
# ---------------------------------------------------------------------------


@router.post("/users/{user_id}/impersonate")
async def impersonate(
    user_id: uuid.UUID = Path(...),
    admin: Principal = Depends(_require_admin),
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
    admin: Principal = Depends(_require_admin),
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


# ---------------------------------------------------------------------------
# Admins CRUD
# ---------------------------------------------------------------------------


@router.get("/admins")
async def list_admins(
    _: Principal = Depends(_require_admin),
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
    admin: Principal = Depends(_require_admin),
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
    admin: Principal = Depends(_require_admin),
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


# ---------------------------------------------------------------------------
# Orgs (admin view)
# ---------------------------------------------------------------------------


@router.get("/orgs")
async def list_orgs(
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[OrgSummaryOut]:
    import sqlalchemy as sa

    if cursor is not None:
        q = sa.text(
            "SELECT id, name, creator_user_id, deleted_at, created_at "
            "FROM orgs "
            "WHERE created_at < (SELECT created_at FROM orgs WHERE id = :cursor) "
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
    admin: Principal = Depends(_require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> None:
    import sqlalchemy as sa

    from shared_kernel.auth.clients import now as _now

    await db.execute(
        sa.text("UPDATE orgs SET deleted_at = :now WHERE id = :id AND deleted_at IS NULL").bindparams(
            now=_now(), id=org_id
        )
    )
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
    admin: Principal = Depends(_require_admin),
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


# ---------------------------------------------------------------------------
# Projects (admin view)
# ---------------------------------------------------------------------------


@router.get("/projects")
async def list_projects(
    cursor: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(_require_admin),
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
# Audit (I.2)
# ---------------------------------------------------------------------------


@router.get("/audit")
async def query_audit(
    actor_user_id: uuid.UUID | None = Query(None),
    resource_type: str | None = Query(None),
    resource_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    ip_prefix: str | None = Query(None),
    session_id: uuid.UUID | None = Query(None),
    request_id: uuid.UUID | None = Query(None),
    cursor: int | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(db_session),
) -> AuditPageOut:
    facade = AuditFacade(db)
    filters = AuditFilter(
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        resource_id=resource_id,
        action=action,
        from_ts=from_ts,
        to_ts=to_ts,
        ip_prefix=ip_prefix,
        session_id=session_id,
        request_id=request_id,
    )
    page = await facade.query(filters, cursor=cursor, limit=limit)
    return AuditPageOut(
        items=[
            AuditEntryOut(
                id=e.id,
                actor_user_id=e.actor_user_id,
                actor_ip=e.actor_ip,
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                metadata=e.metadata,
                session_id=e.session_id,
                request_id=e.request_id,
                created_at=e.created_at.isoformat(),
            )
            for e in page.items
        ],
        next_cursor=page.next_cursor,
    )


@router.post("/audit/export")
async def export_audit(
    actor_user_id: uuid.UUID | None = Query(None),
    resource_type: str | None = Query(None),
    action: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    admin: Principal = Depends(_require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> dict:
    """Kick off audit CSV export → MinIO `exports/` bucket."""
    filters = AuditFilter(
        actor_user_id=actor_user_id,
        resource_type=resource_type,
        action=action,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    facade = AuditFacade(db)
    csv_bytes = await facade.export_csv(filters)

    from shared_kernel.storage import export_key, get_minio_client

    client = get_minio_client()
    job_id = uuid.uuid4()
    key = export_key(job_id=job_id, filename="audit_export.csv")
    await client.put_object(
        bucket=client.exports_bucket,
        key=key,
        data=csv_bytes,
        content_type="text/csv",
    )
    from datetime import timedelta as _td

    signed_url = await client.presigned_get(
        bucket=client.exports_bucket,
        key=key,
        expires=_td(hours=1),
    )
    await audit.emit(
        db,
        audit.AuditEvent(
            action="admin.audit_exported",
            actor_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            request_id=ctx.request_id,
        ),
    )
    return {"url": signed_url, "job_id": str(job_id)}


# ---------------------------------------------------------------------------
# Restore (R8.13)
# ---------------------------------------------------------------------------


@router.post("/restore/{resource_type}/{resource_id}")
async def restore_resource(
    resource_type: str = Path(...),
    resource_id: uuid.UUID = Path(...),
    admin: Principal = Depends(_require_admin),
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


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


@router.get("/metrics")
async def admin_metrics(
    _: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(db_session),
) -> MetricsOut:
    import sqlalchemy as sa

    users_count = (await db.execute(sa.text("SELECT count(*) FROM users"))).scalar_one()
    orgs_count = (await db.execute(sa.text("SELECT count(*) FROM orgs"))).scalar_one()
    projects_count = (await db.execute(sa.text("SELECT count(*) FROM projects"))).scalar_one()
    audit_count = (await db.execute(sa.text("SELECT count(*) FROM audit_logs"))).scalar_one()
    return MetricsOut(
        total_users=users_count,
        total_orgs=orgs_count,
        total_projects=projects_count,
        total_audit_entries=audit_count,
    )


# ---------------------------------------------------------------------------
# Rate-limit policies (R19.04)
# ---------------------------------------------------------------------------


@router.get("/rate-limits")
async def list_rate_limits(
    _: Principal = Depends(_require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[RateLimitPolicyOut]:
    import sqlalchemy as sa

    rows = (
        await db.execute(
            sa.text(
                "SELECT key, window_sec, max_count, scope, updated_at "
                "FROM rate_limit_policies ORDER BY key"
            )
        )
    ).all()
    return [
        RateLimitPolicyOut(
            key=r[0],
            window_sec=r[1],
            max_count=r[2],
            scope=r[3],
            updated_at=r[4].isoformat(),
        )
        for r in rows
    ]


@router.patch("/rate-limits/{key}")
async def patch_rate_limit(
    key: str = Path(...),
    body: RateLimitPatchIn = Body(...),
    admin: Principal = Depends(_require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> RateLimitPolicyOut:
    import sqlalchemy as sa

    from shared_kernel.auth.clients import now as _now

    updates: dict = {"updated_at": _now()}
    if body.window_sec is not None:
        updates["window_sec"] = body.window_sec
    if body.max_count is not None:
        updates["max_count"] = body.max_count
    if body.scope is not None:
        updates["scope"] = body.scope

    # set_clause keys are drawn from a fixed allowlist (window_sec, max_count,
    # scope); values are bound parameters. Not user-controlled identifiers.
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    result = await db.execute(
        sa.text(
            f"UPDATE rate_limit_policies SET {set_clause} WHERE key = :key RETURNING *"  # noqa: S608
        ).bindparams(key=key, **updates)
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=404, detail="Rate-limit policy not found")

    await audit.emit(
        db,
        audit.AuditEvent(
            action="admin.rate_limit_patched",
            actor_user_id=admin.user_id,
            actor_ip=ctx.actor_ip,
            resource_type="rate_limit_policy",
            metadata={"key": key, "updates": {k: str(v) for k, v in updates.items()}},
            request_id=ctx.request_id,
        ),
    )
    return RateLimitPolicyOut(
        key=row[0],
        window_sec=row[1],
        max_count=row[2],
        scope=row[3],
        updated_at=row[4].isoformat(),
    )


__all__ = ["router"]
