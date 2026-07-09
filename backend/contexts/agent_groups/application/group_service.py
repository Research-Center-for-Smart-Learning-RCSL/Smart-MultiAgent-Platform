"""Agent-group membership use-cases (Phase 2b WS2, R24.06/R11.07/R11.08).

Owns group creation and member add/remove — the surface the GraphRAG
owner-centric create (WS2) and the layered resolver (WS4) build on — with audit
emission and the per-project trust-boundary check (a member must live in the
group's project). Authorization (Project-Owner) is enforced at the route
boundary via the tenancy facade, matching the codebase convention; this service
performs the mutation and records the audit trail in the caller's transaction.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agent_groups.domain.errors import (
    AgentGroupMemberProjectMismatch,
    AgentGroupNotFound,
)
from contexts.agent_groups.domain.models import AgentGroup
from contexts.agent_groups.infrastructure.group_repository import AgentGroupRepository
from contexts.agents.infrastructure import tables as agents_t
from shared_kernel import audit


class AgentGroupService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._repo = AgentGroupRepository(db)

    async def create_group(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        group_id = await self._repo.create_group(project_id=project_id, name=name)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent_group.created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_group",
                resource_id=group_id,
                metadata={"project_id": str(project_id), "name": name},
                request_id=request_id,
            ),
        )
        return group_id

    async def list_groups(self, project_id: uuid.UUID) -> Sequence[AgentGroup]:
        """Live groups in a project for the list view (Phase 4α). Read-only."""
        return await self._repo.list_for_project(project_id)

    async def get_group(self, group_id: uuid.UUID) -> AgentGroup | None:
        """A single live group, or ``None`` if missing/soft-deleted (Phase 4α)."""
        return await self._repo.get(group_id)

    async def rename_group(
        self,
        *,
        group_id: uuid.UUID,
        name: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> AgentGroup:
        """Rename a group and audit it (Phase 4α); returns the refreshed group.

        The project id is resolved (and existence enforced) first so a rename of a
        missing/soft-deleted group is :class:`AgentGroupNotFound`, and the audit row
        carries the owning project. A duplicate active name surfaces as
        :class:`AgentGroupNameConflict` from the repository's partial-unique guard.
        """
        project_id = await self._require_group_project(group_id)
        renamed = await self._repo.rename(group_id=group_id, name=name)
        if not renamed:
            # Concurrently soft-deleted between the project resolve and the write.
            raise AgentGroupNotFound(str(group_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent_group.renamed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_group",
                resource_id=group_id,
                metadata={"project_id": str(project_id), "name": name},
                request_id=request_id,
            ),
        )
        group = await self._repo.get(group_id)
        if group is None:
            raise AgentGroupNotFound(str(group_id))
        return group

    async def add_member(
        self,
        *,
        group_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        project_id = await self._require_group_project(group_id)
        # Trust boundary: an agent may only join a group in its own project.
        agent_row = (
            await self._db.execute(
                sa.select(agents_t.agents.c.project_id).where(
                    sa.and_(
                        agents_t.agents.c.id == agent_id,
                        agents_t.agents.c.deleted_at.is_(None),
                    )
                )
            )
        ).first()
        if agent_row is None or agent_row.project_id != project_id:
            raise AgentGroupMemberProjectMismatch(
                f"agent {agent_id} is not in group {group_id}'s project {project_id}"
            )
        await self._repo.add_member(group_id=group_id, agent_id=agent_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent_group.member_added",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_group",
                resource_id=group_id,
                metadata={"project_id": str(project_id), "agent_id": str(agent_id)},
                request_id=request_id,
            ),
        )

    async def remove_member(
        self,
        *,
        group_id: uuid.UUID,
        agent_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        project_id = await self._require_group_project(group_id)
        removed = await self._repo.remove_member(group_id=group_id, agent_id=agent_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent_group.member_removed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_group",
                resource_id=group_id,
                metadata={"project_id": str(project_id), "agent_id": str(agent_id), "removed": removed},
                request_id=request_id,
            ),
        )
        return removed

    async def set_concept_map_enabled(
        self,
        *,
        group_id: uuid.UUID,
        enabled: bool,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        """Toggle the group's Concept Map privacy opt-in and audit it (R11.10).

        Enabling exposes the shared group map to retrieval for every member, so
        the route restricts this to a strict Project Owner; the change is
        audit-logged unconditionally.
        """
        project_id = await self._require_group_project(group_id)
        updated = await self._repo.set_concept_map_enabled(group_id=group_id, enabled=enabled)
        if not updated:
            # Concurrently soft-deleted between the project resolve and the write.
            raise AgentGroupNotFound(str(group_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent_group.concept_map_toggled",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_group",
                resource_id=group_id,
                metadata={"project_id": str(project_id), "concept_map_enabled": enabled},
                request_id=request_id,
            ),
        )

    async def soft_delete(
        self,
        *,
        group_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        """Tombstone a group and audit it (WS6 R11.20); returns its project id.

        The project id is resolved (and existence enforced) before the write so
        the route can scope the follow-up GraphRAG purge audit. The repository's
        ``deleted_at IS NULL`` guard maps a concurrent double-delete to
        :class:`AgentGroupNotFound` rather than a spurious second audit row.
        """
        project_id = await self._require_group_project(group_id)
        deleted = await self._repo.soft_delete(group_id=group_id)
        if not deleted:
            raise AgentGroupNotFound(str(group_id))
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent_group.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_group",
                resource_id=group_id,
                metadata={"project_id": str(project_id)},
                request_id=request_id,
            ),
        )
        return project_id

    async def list_members(self, group_id: uuid.UUID) -> Sequence[uuid.UUID]:
        return await self._repo.list_member_agent_ids(group_id)

    async def group_project_id(self, group_id: uuid.UUID) -> uuid.UUID | None:
        """The group's project id, for the route's Project-Owner authz gate."""
        return await self._repo.project_id_of(group_id)

    async def _require_group_project(self, group_id: uuid.UUID) -> uuid.UUID:
        project_id = await self._repo.project_id_of(group_id)
        if project_id is None:
            raise AgentGroupNotFound(str(group_id))
        return project_id


__all__ = ["AgentGroupService"]
