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
        self._db = db
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

    async def is_project_member(self, user_id: uuid.UUID, project_id: uuid.UUID) -> bool:
        member = await self._project_members.get(project_id=project_id, user_id=user_id)
        return member is not None

    async def get_project(self, project_id: uuid.UUID, *, include_deleted: bool = False) -> Project | None:
        return await self._projects.get(project_id, include_deleted=include_deleted)

    # ----- account self-deletion (R8.14 / R8.18) --------------------------
    #
    # Called from the identity context (AuthService.delete_account) which owns
    # the user lifecycle but must not reach into tenancy tables directly. These
    # two methods are the public surface for that cascade; they share the
    # caller's session, so the whole self-delete runs in one transaction.

    async def orgs_blocking_self_delete(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        """R8.18: Orgs where the user is Original Creator AND ≥ 2 active members.

        Delegates to the canonical implementation on ``OrgService`` rather than
        re-deriving the rule, so the block list can never drift from it.
        """
        from contexts.tenancy.application.org_service import OrgService

        return await OrgService(self._db).orgs_blocking_self_delete(user_id)

    async def cascade_account_deletion(
        self,
        *,
        user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> dict[str, int]:
        """R8.14: tear down a self-deleting user's tenancy footprint.

        The R8.18 block check (:meth:`orgs_blocking_self_delete`) MUST have
        already returned empty — this method assumes no Org the user is OC of
        has other active members. It:

        * soft-deletes every Individual-owned Project;
        * removes the user from every *other* project membership (which also
          revokes the keys they had carried into it, R7.04, and audits each);
        * for Orgs the user solely owns as OC, soft-deletes the Org and its
          projects so no Org outlives its only member (R8.14: "or the Org is
          deleted"); for all other Orgs, just drops the membership.

        Returns counts for the aggregate ``user.self_deleted`` audit record.
        """
        from contexts.tenancy.application.project_service import ProjectService

        proj_svc = ProjectService(self._db)

        owned = await self._projects.list_by_user(user_id)
        owned_ids = {p.id for p in owned}
        for project in owned:
            await proj_svc.soft_delete(
                project_id=project.id,
                actor_user_id=user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        projects_left = 0
        for project_id in await self._project_members.list_project_ids_for_user(user_id):
            if project_id in owned_ids:
                continue
            await proj_svc.remove_member(
                project_id=project_id,
                target_user_id=user_id,
                actor_user_id=user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            projects_left += 1

        orgs_left = 0
        solo_orgs_deleted = 0
        for org in await self._orgs.list_for_user(user_id):
            oc = await self._org_members.original_creator(org.id)
            if oc is not None and oc.user_id == user_id:
                for project in await self._projects.list_by_org(org.id):
                    await proj_svc.soft_delete(
                        project_id=project.id,
                        actor_user_id=user_id,
                        actor_ip=actor_ip,
                        request_id=request_id,
                    )
                await self._orgs.soft_delete(org.id)
                solo_orgs_deleted += 1
            else:
                await self._org_members.remove(org_id=org.id, user_id=user_id)
                orgs_left += 1

        return {
            "projects_deleted": len(owned),
            "projects_left": projects_left,
            "orgs_left": orgs_left,
            "solo_orgs_deleted": solo_orgs_deleted,
        }


__all__ = ["TenancyFacade"]
