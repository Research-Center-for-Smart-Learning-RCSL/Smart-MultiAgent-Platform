"""Tenancy facade — public surface for the web layer."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.domain.models import Org, OrgMember, Project, ProjectMember
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)


class TenancyFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._orgs = OrgRepository(db)
        self._projects = ProjectRepository(db)
        self._org_members = OrgMemberRepository(db)
        self._project_members = ProjectMemberRepository(db)

    async def user_orgs(self, user_id: uuid.UUID) -> Sequence[Org]:
        return await self._orgs.list_for_user(user_id)

    async def user_owned_projects(self, user_id: uuid.UUID) -> Sequence[Project]:
        return await self._projects.list_by_user(user_id)

    async def org_projects(self, org_id: uuid.UUID) -> Sequence[Project]:
        return await self._projects.list_by_org(org_id)

    async def org_members(self, org_id: uuid.UUID) -> Sequence[OrgMember]:
        return await self._org_members.list(org_id)

    async def project_members(self, project_id: uuid.UUID) -> Sequence[ProjectMember]:
        return await self._project_members.list(project_id)

    async def get_project(
        self, project_id: uuid.UUID, *, include_deleted: bool = False
    ) -> Project | None:
        return await self._projects.get(project_id, include_deleted=include_deleted)


__all__ = ["TenancyFacade"]
