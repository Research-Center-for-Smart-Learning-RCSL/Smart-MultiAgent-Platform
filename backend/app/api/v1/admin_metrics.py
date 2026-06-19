"""`/api/admin/metrics` — admin dashboard metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class MetricsOut(BaseModel):
    total_users: int
    total_orgs: int
    total_projects: int
    total_audit_entries: int


# ---------------------------------------------------------------------------
# Cache + handler
# ---------------------------------------------------------------------------

_metrics_cache: dict[str, tuple[float, "MetricsOut"]] = {}
_METRICS_TTL_SECONDS = 60.0


@router.get("/metrics")
async def admin_metrics(
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> MetricsOut:
    import time

    import sqlalchemy as sa

    now = time.monotonic()
    cached = _metrics_cache.get("v1")
    if cached is not None:
        cached_at, cached_result = cached
        if now - cached_at < _METRICS_TTL_SECONDS:
            return cached_result

    users_count = (await db.execute(sa.text("SELECT count(*) FROM users WHERE deleted_at IS NULL"))).scalar_one()
    orgs_count = (await db.execute(sa.text("SELECT count(*) FROM orgs WHERE deleted_at IS NULL"))).scalar_one()
    projects_count = (await db.execute(sa.text("SELECT count(*) FROM projects WHERE deleted_at IS NULL"))).scalar_one()
    audit_count = (await db.execute(sa.text(
        "SELECT COALESCE(reltuples, 0)::bigint FROM pg_class WHERE relname = 'audit_logs'"
    ))).scalar_one_or_none() or 0
    result = MetricsOut(
        total_users=users_count,
        total_orgs=orgs_count,
        total_projects=projects_count,
        total_audit_entries=audit_count,
    )
    _metrics_cache["v1"] = (now, result)
    return result
