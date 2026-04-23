"""Audit-log read-only repository for admin query + export."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.audit.domain.models import AuditEntry, AuditFilter, AuditPage
from shared_kernel.audit import audit_logs


def _row_to_entry(row: Any) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        actor_user_id=row.actor_user_id,
        actor_ip=str(row.actor_ip) if row.actor_ip else None,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        metadata=row.metadata or {},
        session_id=row.session_id,
        request_id=row.request_id,
        created_at=row.created_at,
    )


class AuditRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def query(
        self,
        filters: AuditFilter,
        *,
        cursor: int | None = None,
        limit: int = 50,
    ) -> AuditPage:
        q = audit_logs.select().order_by(audit_logs.c.id.desc()).limit(limit + 1)

        if cursor is not None:
            q = q.where(audit_logs.c.id < cursor)
        if filters.actor_user_id is not None:
            q = q.where(audit_logs.c.actor_user_id == filters.actor_user_id)
        if filters.resource_type is not None:
            q = q.where(audit_logs.c.resource_type == filters.resource_type)
        if filters.resource_id is not None:
            q = q.where(audit_logs.c.resource_id == filters.resource_id)
        if filters.action is not None:
            q = q.where(audit_logs.c.action == filters.action)
        if filters.from_ts is not None:
            q = q.where(audit_logs.c.created_at >= filters.from_ts)
        if filters.to_ts is not None:
            q = q.where(audit_logs.c.created_at <= filters.to_ts)
        if filters.ip_prefix is not None:
            q = q.where(
                sa.cast(audit_logs.c.actor_ip, sa.Text).startswith(filters.ip_prefix)
            )
        if filters.session_id is not None:
            q = q.where(audit_logs.c.session_id == filters.session_id)
        if filters.request_id is not None:
            q = q.where(audit_logs.c.request_id == filters.request_id)

        rows = (await self._db.execute(q)).all()
        entries = [_row_to_entry(r) for r in rows[: limit]]
        next_cursor = entries[-1].id if len(rows) > limit else None
        return AuditPage(items=entries, next_cursor=next_cursor)

    async def count(self) -> int:
        result = await self._db.execute(
            sa.select(sa.func.count()).select_from(audit_logs)
        )
        return result.scalar_one()


__all__ = ["AuditRepository"]
