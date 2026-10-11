"""`/api/admin/audit` — audit log querying and CSV export (I.2)."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from contexts.audit.domain.models import AuditFilter
from contexts.audit.interfaces.facade import AuditFacade
from shared_kernel import audit
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session
from shared_kernel.storage import export_key, file_tickets, get_minio_client
from shared_kernel.storage.file_tickets import content_disposition

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


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


class AuditExportOut(BaseModel):
    url: str
    job_id: uuid.UUID


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

_MAX_EXPORT_ROWS = 100_000


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
    _: Principal = Depends(require_admin),
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
    resource_id: uuid.UUID | None = Query(None),
    action: str | None = Query(None),
    from_ts: datetime | None = Query(None, alias="from"),
    to_ts: datetime | None = Query(None, alias="to"),
    ip_prefix: str | None = Query(None),
    session_id: uuid.UUID | None = Query(None),
    request_id: uuid.UUID | None = Query(None),
    admin: Principal = Depends(require_admin),
    ctx: RequestContext = Depends(current_context),
    db: AsyncSession = Depends(db_session),
) -> AuditExportOut:
    """Build the audit CSV into the `exports` bucket and return its download link."""
    # Require a date range to prevent full-table dumps.
    if from_ts is None or to_ts is None:
        raise HTTPException(
            status_code=422,
            detail="Both 'from' and 'to' date filters are required for audit export.",
        )
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
    facade = AuditFacade(db)
    csv_bytes = await facade.export_csv(filters, max_rows=_MAX_EXPORT_ROWS)

    client = get_minio_client()
    job_id = uuid.uuid4()
    key = export_key(job_id=job_id, filename="audit_export.csv")
    await client.put_object(
        bucket=client.exports_bucket,
        key=key,
        data=csv_bytes,
        content_type="text/csv",
    )
    url = await file_tickets.issue(
        bucket=client.exports_bucket,
        key=key,
        content_type="text/csv; charset=utf-8",
        disposition=content_disposition("attachment", f"audit-{from_ts:%Y%m%d}-{to_ts:%Y%m%d}.csv"),
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
    return AuditExportOut(url=url, job_id=job_id)
