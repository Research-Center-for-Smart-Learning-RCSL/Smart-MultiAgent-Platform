"""`/api/admin/rate-limits` — rate-limit policy CRUD (R19.04)."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Path
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.admin_deps import require_admin
from app.api.v1.deps import PaginationParams
from shared_kernel import audit
from shared_kernel.auth.context import RequestContext
from shared_kernel.auth.dependencies import current_context
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

router = APIRouter(tags=["admin"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class RateLimitPolicyOut(BaseModel):
    key: str
    window_sec: int
    max_count: int
    scope: str
    updated_at: str


class RateLimitPatchIn(BaseModel):
    # API-7: `ge=1` on both numerics — a 0 / negative `max_count` would disable
    # rate limiting platform-wide; a non-positive window is meaningless.
    window_sec: int | None = Field(default=None, ge=1, le=86_400)
    max_count: int | None = Field(default=None, ge=1)
    scope: str | None = None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


@router.get("/rate-limits")
async def list_rate_limits(
    pagination: PaginationParams = Depends(),
    _: Principal = Depends(require_admin),
    db: AsyncSession = Depends(db_session),
) -> list[RateLimitPolicyOut]:
    import sqlalchemy as sa

    rows = (
        await db.execute(
            sa.text(
                "SELECT key, window_sec, max_count, scope, updated_at "
                "FROM rate_limit_policies ORDER BY key "
                "LIMIT :limit OFFSET :offset"
            ).bindparams(limit=pagination.limit, offset=pagination.offset)
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
    admin: Principal = Depends(require_admin),
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

    # Persist FIRST (the DB row is authoritative), THEN mirror into Redis so the
    # live limiter picks it up. If we mirrored before committing and the commit
    # later failed, Redis would enforce a policy Postgres rolled back. Commit here
    # explicitly (the db_session trailing commit then no-ops).
    from shared_kernel.auth.ratelimit import mirror_policy

    await db.commit()
    await mirror_policy(row[0], window_sec=int(row[1]), max_count=int(row[2]), scope=row[3])
    return RateLimitPolicyOut(
        key=row[0],
        window_sec=row[1],
        max_count=row[2],
        scope=row[3],
        updated_at=row[4].isoformat(),
    )
