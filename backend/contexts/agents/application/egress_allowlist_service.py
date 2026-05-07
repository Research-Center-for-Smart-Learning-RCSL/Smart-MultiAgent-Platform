"""Egress allowlist CRUD (E.9 / R12.02).

Project-Owner-gated writes (enforced by the router). This service owns the
``mcp.egress_*`` audit surface.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.mcp import EgressAllowlistEntry
from contexts.agents.infrastructure.mcp_repositories import EgressAllowlistRepository
from shared_kernel import audit

# RFC 1123 hostname form (case-insensitive); caller normalises to lower-case.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*" r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)


def _normalise(hostname: str) -> str:
    h = hostname.strip().lower()
    if not _HOSTNAME_RE.match(h):
        raise ValueError(f"invalid hostname: {hostname!r}")
    return h


class EgressAllowlistService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = EgressAllowlistRepository(db)

    async def list_for_project(self, project_id: uuid.UUID) -> Sequence[EgressAllowlistEntry]:
        return await self._repo.list_for_project(project_id)

    async def replace(
        self,
        *,
        project_id: uuid.UUID,
        hostnames: Sequence[str],
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Sequence[EgressAllowlistEntry]:
        normalised = [_normalise(h) for h in hostnames]
        result = await self._repo.replace(
            project_id=project_id,
            hostnames=normalised,
            added_by_user_id=actor_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="mcp.egress_allowlist_replaced",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="project",
                resource_id=project_id,
                metadata={"hostnames": list(normalised)},
                request_id=request_id,
            ),
        )
        return result

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        hostname: str,
        note: str | None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> EgressAllowlistEntry:
        normalised = _normalise(hostname)
        entry = await self._repo.upsert(
            project_id=project_id,
            hostname=normalised,
            added_by_user_id=actor_user_id,
            note=note,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="mcp.egress_allowlist_added",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="project",
                resource_id=project_id,
                metadata={"hostname": normalised, "note": note},
                request_id=request_id,
            ),
        )
        return entry

    async def remove(
        self,
        *,
        project_id: uuid.UUID,
        hostname: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        normalised = _normalise(hostname)
        removed = await self._repo.delete(
            project_id=project_id,
            hostname=normalised,
        )
        if removed:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="mcp.egress_allowlist_removed",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="project",
                    resource_id=project_id,
                    metadata={"hostname": normalised},
                    request_id=request_id,
                ),
            )
        return removed


__all__ = ["EgressAllowlistService"]
