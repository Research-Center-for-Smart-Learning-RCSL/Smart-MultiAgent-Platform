"""Audit facade — public query API for the admin web layer."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.audit.application.audit_query_service import AuditQueryService
from contexts.audit.domain.models import AuditFilter, AuditPage


class AuditFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._service = AuditQueryService(db)

    async def query(
        self,
        filters: AuditFilter,
        *,
        cursor: int | None = None,
        limit: int = 50,
    ) -> AuditPage:
        return await self._service.query(filters, cursor=cursor, limit=limit)

    async def export_csv(self, filters: AuditFilter) -> bytes:
        return await self._service.export_csv(filters)


__all__ = ["AuditFacade"]
