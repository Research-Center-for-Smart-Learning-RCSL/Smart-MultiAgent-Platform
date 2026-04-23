"""Audit query + export (I.2)."""

from __future__ import annotations

import csv
import io

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.audit.domain.models import AuditFilter, AuditPage
from contexts.audit.infrastructure.repositories import AuditRepository


class AuditQueryService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = AuditRepository(db)

    async def query(
        self,
        filters: AuditFilter,
        *,
        cursor: int | None = None,
        limit: int = 50,
    ) -> AuditPage:
        return await self._repo.query(filters, cursor=cursor, limit=limit)

    async def export_csv(self, filters: AuditFilter) -> bytes:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([
            "id", "actor_user_id", "actor_ip", "action",
            "resource_type", "resource_id", "metadata",
            "session_id", "request_id", "created_at",
        ])
        cursor: int | None = None
        while True:
            page = await self._repo.query(filters, cursor=cursor, limit=500)
            for entry in page.items:
                writer.writerow([
                    entry.id,
                    str(entry.actor_user_id) if entry.actor_user_id else "",
                    entry.actor_ip or "",
                    entry.action,
                    entry.resource_type or "",
                    str(entry.resource_id) if entry.resource_id else "",
                    str(entry.metadata),
                    str(entry.session_id) if entry.session_id else "",
                    str(entry.request_id) if entry.request_id else "",
                    entry.created_at.isoformat(),
                ])
            if page.next_cursor is None:
                break
            cursor = page.next_cursor
        return buf.getvalue().encode("utf-8")


__all__ = ["AuditQueryService"]
