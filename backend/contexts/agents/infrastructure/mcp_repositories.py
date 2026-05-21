"""Repositories for MCP egress allowlist (E.9)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.mcp import EgressAllowlistEntry
from contexts.agents.infrastructure.mcp_tables import mcp_egress_allowlist


def _row_to_entry(row: Any) -> EgressAllowlistEntry:
    return EgressAllowlistEntry(
        id=row.id,
        project_id=row.project_id,
        hostname=row.hostname,
        added_by_user_id=row.added_by_user_id,
        added_at=row.added_at,
        note=row.note,
    )


class EgressAllowlistRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[EgressAllowlistEntry]:
        rows = (
            await self._db.execute(
                mcp_egress_allowlist.select()
                .where(mcp_egress_allowlist.c.project_id == project_id)
                .order_by(mcp_egress_allowlist.c.hostname)
            )
        ).all()
        return [_row_to_entry(r) for r in rows]

    async def upsert(
        self,
        *,
        project_id: uuid.UUID,
        hostname: str,
        added_by_user_id: uuid.UUID | None,
        note: str | None,
    ) -> EgressAllowlistEntry:
        """Idempotent insert — if ``(project_id, hostname)`` exists, return it.

        DB-3: uses ``INSERT ... ON CONFLICT DO UPDATE ... RETURNING`` so a
        unique collision is resolved atomically inside a single statement. The
        previous ``try INSERT / except IntegrityError: SELECT`` form ran its
        recovery SELECT on a transaction Postgres had already marked failed, so
        it raised ``InFailedSqlTransaction`` instead of returning the row.

        ``DO UPDATE`` sets ``hostname`` to its own value — a no-op that changes
        no data but makes the conflicting row visible to ``RETURNING`` (a bare
        ``DO NOTHING`` returns nothing on conflict).
        """
        stmt = (
            pg_insert(mcp_egress_allowlist)
            .values(
                project_id=project_id,
                hostname=hostname,
                added_by_user_id=added_by_user_id,
                note=note,
            )
            .on_conflict_do_update(
                index_elements=["project_id", "hostname"],
                set_={"hostname": mcp_egress_allowlist.c.hostname},
            )
            .returning(mcp_egress_allowlist)
        )
        row = (await self._db.execute(stmt)).one()
        return _row_to_entry(row)

    async def delete(self, *, project_id: uuid.UUID, hostname: str) -> bool:
        result = await self._db.execute(
            mcp_egress_allowlist.delete().where(
                sa.and_(
                    mcp_egress_allowlist.c.project_id == project_id,
                    mcp_egress_allowlist.c.hostname == hostname,
                )
            )
        )
        return (result.rowcount or 0) > 0

    async def replace(
        self,
        *,
        project_id: uuid.UUID,
        hostnames: Sequence[str],
        added_by_user_id: uuid.UUID | None,
    ) -> Sequence[EgressAllowlistEntry]:
        """Replace the full set for a project in one transaction.

        Used by ``PUT /api/projects/{pid}/mcp/egress-allowlist``; the caller's
        session owns the TX so rollback cleans up both the delete and inserts.
        """
        normalised = tuple(sorted({h.strip().lower() for h in hostnames if h.strip()}))
        await self._db.execute(
            mcp_egress_allowlist.delete().where(
                mcp_egress_allowlist.c.project_id == project_id,
            )
        )
        for h in normalised:
            await self._db.execute(
                mcp_egress_allowlist.insert().values(
                    project_id=project_id,
                    hostname=h,
                    added_by_user_id=added_by_user_id,
                    note=None,
                )
            )
        return await self.list_for_project(project_id)

    async def is_allowed(self, *, project_id: uuid.UUID, hostname: str) -> bool:
        row = (
            await self._db.execute(
                sa.select(mcp_egress_allowlist.c.id).where(
                    sa.and_(
                        mcp_egress_allowlist.c.project_id == project_id,
                        mcp_egress_allowlist.c.hostname == hostname.lower(),
                    )
                )
            )
        ).first()
        return row is not None


__all__ = ["EgressAllowlistRepository"]
