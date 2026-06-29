"""Tenancy facade — public surface for the web layer."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.application.account_deletion_service import AccountDeletionService
from contexts.tenancy.domain.models import Org, OrgMember, Project, ProjectMember
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)


class TenancyFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._orgs = OrgRepository(db)
        self._projects = ProjectRepository(db)
        self._org_members = OrgMemberRepository(db)
        self._project_members = ProjectMemberRepository(db)
        self._account_deletion = AccountDeletionService(db)

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

    async def is_project_member(self, user_id: uuid.UUID, project_id: uuid.UUID) -> bool:
        member = await self._project_members.get(project_id=project_id, user_id=user_id)
        return member is not None

    async def is_project_owner(self, user_id: uuid.UUID, project_id: uuid.UUID) -> bool:
        """R10.10 owner gate — one definition shared by the RAG multipart and
        tus upload routers so the rule can't drift between them."""
        from contexts.tenancy.domain.models import ProjectMemberRole

        member = await self._project_members.get(project_id=project_id, user_id=user_id)
        return member is not None and member.role is ProjectMemberRole.OWNER

    async def get_project(self, project_id: uuid.UUID, *, include_deleted: bool = False) -> Project | None:
        return await self._projects.get(project_id, include_deleted=include_deleted)

    async def get_projects(self, project_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Project]:
        """Batch-resolve projects by id, keyed by id, for N+1-free name lookups."""
        return {p.id: p for p in await self._projects.list_by_ids(project_ids)}

    async def member_project_ids(
        self, user_id: uuid.UUID, project_ids: Sequence[uuid.UUID]
    ) -> set[uuid.UUID]:
        """Subset of `project_ids` the user currently belongs to (one query)."""
        return await self._project_members.member_project_ids(user_id=user_id, project_ids=project_ids)

    # ----- account self-deletion (R8.14 / R8.18) --------------------------
    #
    # Called from the identity context (AuthService.delete_account) which owns
    # the user lifecycle but must not reach into tenancy tables directly. These
    # two methods are the public surface for that cascade; they share the
    # caller's session, so the whole self-delete runs in one transaction.

    async def orgs_blocking_self_delete(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """R8.18: Orgs where the user is Original Creator AND >= 2 active members."""
        return await self._account_deletion.orgs_blocking_self_delete(user_id)

    async def cascade_account_deletion(
        self,
        *,
        user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        """R8.14: tear down a self-deleting user's tenancy footprint."""
        return await self._account_deletion.cascade_account_deletion(
            user_id=user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def prepare_hard_delete(
        self,
        *,
        user_id: uuid.UUID,
        reassign_to_user_id: uuid.UUID,
    ) -> None:
        """Remove FK RESTRICT references before hard-deleting a user row."""
        await self._account_deletion.prepare_hard_delete(
            user_id=user_id,
            reassign_to_user_id=reassign_to_user_id,
        )


__all__ = ["TenancyFacade"]
