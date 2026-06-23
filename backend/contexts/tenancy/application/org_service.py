"""Org use-cases (§22.2) with the R8.01/R8.05 atomic-creation rule.

Creating an Org *always* produces three rows in one transaction:
1. `orgs` row with the caller as `creator_user_id`.
2. `org_members` row with Owner + is_original_creator=true.
3. `projects` row named "Default Project" owned by the new Org.

The default project name is hard-coded per R8.05. Operators can rename it
afterwards via `PATCH /api/projects/{id}`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.domain.errors import (
    MemberNotFound,
    OrgNotFound,
    OriginalCreatorConflict,
)
from contexts.tenancy.domain.models import (
    Org,
    OrgMember,
    Project,
    ProjectMemberRole,
)
from contexts.tenancy.domain.models import (
    OrgMemberRole as OrgMemberRole,
)
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from shared_kernel import audit

DEFAULT_PROJECT_NAME = "Default Project"


@dataclass(frozen=True, slots=True)
class CreatedOrg:
    org: Org
    default_project: Project


class OrgService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._orgs = OrgRepository(db)
        self._members = OrgMemberRepository(db)
        self._projects = ProjectRepository(db)
        self._project_members = ProjectMemberRepository(db)

    async def create(
        self,
        *,
        name: str,
        creator_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> CreatedOrg:
        org = await self._orgs.create(name=name, creator_user_id=creator_user_id)
        await self._members.add(
            org_id=org.id,
            user_id=creator_user_id,
            role=OrgMemberRole.OWNER,
            is_original_creator=True,
        )
        project = await self._projects.create(
            name=DEFAULT_PROJECT_NAME,
            owner_user_id=None,
            owner_org_id=org.id,
            created_by_user_id=creator_user_id,
        )
        await self._project_members.add(
            project_id=project.id,
            user_id=creator_user_id,
            role=ProjectMemberRole.OWNER,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.created",
                actor_user_id=creator_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=org.id,
                metadata={"name": name, "default_project_id": str(project.id)},
                request_id=request_id,
            ),
        )
        return CreatedOrg(org=org, default_project=project)

    async def list_for_user(self, user_id: uuid.UUID) -> Sequence[Org]:
        return await self._orgs.list_for_user(user_id)

    async def get(self, org_id: uuid.UUID) -> Org:
        org = await self._orgs.get(org_id)
        if org is None:
            raise OrgNotFound(str(org_id))
        return org

    async def rename(
        self,
        *,
        org_id: uuid.UUID,
        new_name: str,
        expected_version: int,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Org:
        org = await self._orgs.rename(
            org_id=org_id,
            new_name=new_name,
            expected_version=expected_version,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.renamed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=org.id,
                metadata={"new_name": new_name},
                request_id=request_id,
            ),
        )
        return org

    async def soft_delete(
        self,
        *,
        org_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._orgs.soft_delete(org_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=org_id,
                request_id=request_id,
            ),
        )

    async def restore(
        self,
        *,
        org_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._orgs.restore(org_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.restored",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="org",
                resource_id=org_id,
                request_id=request_id,
            ),
        )

    async def list_members(self, org_id: uuid.UUID) -> Sequence[OrgMember]:
        return await self._members.list(org_id)

    async def remove_member(
        self,
        *,
        org_id: uuid.UUID,
        target_user_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        member = await self._members.get(org_id=org_id, user_id=target_user_id)
        if member is None:
            raise MemberNotFound(f"{target_user_id} in org {org_id}")
        if member.is_original_creator:
            raise OriginalCreatorConflict("Original Creator cannot be removed")
        await self._members.remove(org_id=org_id, user_id=target_user_id)
        org_projects = await self._projects.list_by_org(org_id)
        proj_members = ProjectMemberRepository(self._db)
        from contexts.keys.interfaces.facade import KeysFacade

        keys_facade = KeysFacade(self._db)
        for proj in org_projects:
            await keys_facade.revoke_carries_for_user_leaving_project(
                user_id=target_user_id,
                project_id=proj.id,
            )
            await proj_members.remove(project_id=proj.id, user_id=target_user_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="org.member_removed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="org_member",
                resource_id=org_id,
                metadata={
                    "target_user_id": str(target_user_id),
                    "cascade_projects": len(org_projects),
                },
                request_id=request_id,
            ),
        )

    async def change_member_role(
        self,
        *,
        org_id: uuid.UUID,
        target_user_id: uuid.UUID,
        new_role: OrgMemberRole,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        member = await self._members.get(org_id=org_id, user_id=target_user_id)
        if member is None:
            raise MemberNotFound(f"{target_user_id} in org {org_id}")
        if member.is_original_creator:
            raise OriginalCreatorConflict("Original Creator role cannot be changed")
        rowcount = await self._members.change_role(
            org_id=org_id,
            user_id=target_user_id,
            new_role=new_role,
        )
        if rowcount == 0:
            raise OriginalCreatorConflict("role change blocked")

        action = "org.owner_promoted" if new_role is OrgMemberRole.OWNER else "org.owner_demoted"
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="org_member",
                resource_id=org_id,
                metadata={
                    "target_user_id": str(target_user_id),
                    "new_role": new_role.value,
                },
                request_id=request_id,
            ),
        )

    async def is_original_creator(self, *, user_id: uuid.UUID, org_id: uuid.UUID) -> bool:
        member = await self._members.get(org_id=org_id, user_id=user_id)
        return member is not None and member.is_original_creator

    async def orgs_blocking_self_delete(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """R8.18: Orgs where the user is OC AND there are ≥ 2 active members."""
        orgs = await self._orgs.list_for_user(user_id)
        blocked: list[uuid.UUID] = []
        for org in orgs:
            oc = await self._members.original_creator(org.id)
            if oc and oc.user_id == user_id:
                count = await self._members.count_active_members(org.id)
                if count >= 2:
                    blocked.append(org.id)
        return blocked


__all__ = ["CreatedOrg", "DEFAULT_PROJECT_NAME", "OrgService"]
