"""Project use-cases (§22.3)."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.domain.errors import (
    CannotMigrateIndividualProject,
    MemberNotFound,
    ProjectNotFound,
)
from contexts.tenancy.domain.models import (
    Project,
    ProjectMember,
)
from contexts.tenancy.domain.models import (
    ProjectMemberRole as ProjectMemberRole,
)
from contexts.tenancy.domain.models import (
    ProjectOwnerType as ProjectOwnerType,
)
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from shared_kernel import audit


class ProjectService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._projects = ProjectRepository(db)
        self._members = ProjectMemberRepository(db)
        self._org_members = OrgMemberRepository(db)

    async def create(
        self,
        *,
        owner_type: ProjectOwnerType,
        owner_id: uuid.UUID,
        name: str,
        created_by_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Project:
        owner_user_id: uuid.UUID | None = None
        owner_org_id: uuid.UUID | None = None
        if owner_type is ProjectOwnerType.USER:
            owner_user_id = owner_id
        else:
            owner_org_id = owner_id

        project = await self._projects.create(
            name=name,
            owner_user_id=owner_user_id,
            owner_org_id=owner_org_id,
            created_by_user_id=created_by_user_id,
        )
        # The creator becomes a Project Owner (R5.03).
        await self._members.add(
            project_id=project.id,
            user_id=created_by_user_id,
            role=ProjectMemberRole.OWNER,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.created",
                actor_user_id=created_by_user_id,
                actor_ip=actor_ip,
                resource_type="project",
                resource_id=project.id,
                metadata={
                    "owner_type": owner_type.value,
                    "owner_id": str(owner_id),
                    "name": name,
                },
                request_id=request_id,
            ),
        )
        return project

    async def get(self, project_id: uuid.UUID) -> Project:
        project = await self._projects.get(project_id)
        if project is None:
            raise ProjectNotFound(str(project_id))
        return project

    async def list_by_user(self, user_id: uuid.UUID) -> Sequence[Project]:
        return await self._projects.list_by_user(user_id)

    async def list_by_org(self, org_id: uuid.UUID) -> Sequence[Project]:
        return await self._projects.list_by_org(org_id)

    async def list_visible_for_user(self, user_id: uuid.UUID) -> Sequence[Project]:
        """Own projects + all projects whose orgs the user belongs to."""
        own = await self._projects.list_by_user(user_id)
        orgs = await OrgRepository(self._db).list_for_user(user_id)
        seen = {p.id for p in own}
        result = list(own)
        for org in orgs:
            for project in await self._projects.list_by_org(org.id):
                if project.id not in seen:
                    seen.add(project.id)
                    result.append(project)
        return result

    async def rename(
        self,
        *,
        project_id: uuid.UUID,
        new_name: str,
        expected_version: int,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Project:
        project = await self._projects.rename(
            project_id=project_id,
            new_name=new_name,
            expected_version=expected_version,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.renamed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="project",
                resource_id=project_id,
                metadata={"new_name": new_name},
                request_id=request_id,
            ),
        )
        return project

    async def soft_delete(
        self,
        *,
        project_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._projects.soft_delete(project_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="project",
                resource_id=project_id,
                request_id=request_id,
            ),
        )

    async def restore(
        self,
        *,
        project_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._projects.restore(project_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.restored",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="project",
                resource_id=project_id,
                request_id=request_id,
            ),
        )

    async def list_members(self, project_id: uuid.UUID) -> Sequence[ProjectMember]:
        return await self._members.list(project_id)

    async def remove_member(
        self,
        *,
        project_id: uuid.UUID,
        target_user_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        member = await self._members.get(project_id=project_id, user_id=target_user_id)
        if member is None:
            raise MemberNotFound(f"{target_user_id} in project {project_id}")
        await self._members.remove(project_id=project_id, user_id=target_user_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.member_removed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="project_member",
                resource_id=project_id,
                metadata={"target_user_id": str(target_user_id)},
                request_id=request_id,
            ),
        )

    async def change_member_role(
        self,
        *,
        project_id: uuid.UUID,
        target_user_id: uuid.UUID,
        new_role: ProjectMemberRole,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        rowcount = await self._members.change_role(
            project_id=project_id,
            user_id=target_user_id,
            new_role=new_role,
        )
        if rowcount == 0:
            raise MemberNotFound(f"{target_user_id} in project {project_id}")
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.member_role_changed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="project_member",
                resource_id=project_id,
                metadata={
                    "target_user_id": str(target_user_id),
                    "new_role": new_role.value,
                },
                request_id=request_id,
            ),
        )

    async def reject_migration(self, *, project_id: uuid.UUID) -> None:
        """R8.07 safety net for a `PATCH` that tries to move an Individual
        project into an Org; routers call this before executing the change."""
        project = await self._projects.get(project_id)
        if project and project.owner_user_id is not None:
            raise CannotMigrateIndividualProject(str(project_id))


__all__ = ["ProjectService"]
