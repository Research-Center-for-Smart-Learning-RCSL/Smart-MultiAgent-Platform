"""Audit facade — public query API for the admin web layer."""

from __future__ import annotations

from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.audit.application.audit_query_service import AuditQueryService
from contexts.audit.domain.models import AuditFilter, AuditPage
from shared_kernel.auth.clients import now


class AuditFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._service = AuditQueryService(db)

    async def query(
        self,
        filters: AuditFilter,
        *,
        cursor: int | None = None,
        limit: int = 50,
    ) -> AuditPage:
        return await self._service.query(filters, cursor=cursor, limit=limit)

    async def export_csv(
        self,
        filters: AuditFilter,
        *,
        max_rows: int | None = None,
    ) -> bytes:
        return await self._service.export_csv(filters, max_rows=max_rows)

    # -- Retention helpers (H4) ------------------------------------------------

    async def purge_old_logs(self, *, retention_days: int = 365) -> int:
        """Hard-delete audit_logs older than *retention_days*.

        Requires SET ROLE to bypass the append-only trigger. The caller must
        ensure ``smap_audit_retention`` role membership has been granted
        (migration 0027).
        """
        cutoff = now() - timedelta(days=retention_days)
        await self._db.execute(sa.text("SET ROLE smap_audit_retention"))
        try:
            result = await self._db.execute(
                sa.text(
                    "DELETE FROM audit_logs WHERE created_at < :cutoff "
                    "AND id IN (SELECT id FROM audit_logs WHERE created_at < :cutoff LIMIT 1000)"
                ).bindparams(cutoff=cutoff)
            )
            count = result.rowcount or 0  # type: ignore[attr-defined]
        finally:
            await self._db.execute(sa.text("RESET ROLE"))
        return count


__all__ = ["AuditFacade"]
