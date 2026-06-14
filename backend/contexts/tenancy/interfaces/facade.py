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

    async def is_project_owner(self, user_id: uuid.UUID, project_id: uuid.UUID) -> bool:
        """R10.10 owner gate — one definition shared by the RAG multipart and
        tus upload routers so the rule can't drift between them."""
        from contexts.tenancy.domain.models import ProjectMemberRole

        member = await self._project_members.get(project_id=project_id, user_id=user_id)
        return member is not None and member.role is ProjectMemberRole.OWNER

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
        * for Orgs the user solely owns as OC, soft-deletes the Org (via
          ``OrgService.soft_delete`` so it emits ``org.deleted`` like every other
          org deletion) and its projects, so no Org outlives its only member
          (R8.14: "or the Org is deleted"); for all other Orgs, just drops the
          membership;
        * removes the user from every *other* project membership (revoking the
          keys they had carried in, R7.04, and auditing each) — but skips
          projects of a reaped solo Org, which are being deleted wholesale, to
          avoid wasted carry-revocation and contradictory member-removed /
          project-deleted audit pairs on the same resource.

        Returns counts for the aggregate ``user.self_deleted`` audit record.
        """
        from contexts.tenancy.application.org_service import OrgService
        from contexts.tenancy.application.project_service import ProjectService

        proj_svc = ProjectService(self._db)
        org_svc = OrgService(self._db)

        owned = await self._projects.list_by_user(user_id)
        owned_ids = {p.id for p in owned}
        for project in owned:
            await proj_svc.soft_delete(
                project_id=project.id,
                actor_user_id=user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        # Classify orgs first so the project-membership loop can skip the
        # projects of solo-OC orgs that are about to be reaped.
        solo_orgs: list[uuid.UUID] = []
        surviving_orgs: list[uuid.UUID] = []
        reaped_project_ids: set[uuid.UUID] = set()
        for org in await self._orgs.list_for_user(user_id):
            oc = await self._org_members.original_creator(org.id)
            if oc is not None and oc.user_id == user_id:
                solo_orgs.append(org.id)
                for project in await self._projects.list_by_org(org.id):
                    reaped_project_ids.add(project.id)
            else:
                surviving_orgs.append(org.id)

        projects_left = 0
        for project_id in await self._project_members.list_project_ids_for_user(user_id):
            if project_id in owned_ids or project_id in reaped_project_ids:
                continue
            await proj_svc.remove_member(
                project_id=project_id,
                target_user_id=user_id,
                actor_user_id=user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
            projects_left += 1

        for org_id in solo_orgs:
            for project in await self._projects.list_by_org(org_id):
                await proj_svc.soft_delete(
                    project_id=project.id,
                    actor_user_id=user_id,
                    actor_ip=actor_ip,
                    request_id=request_id,
                )
            await org_svc.soft_delete(
                org_id=org_id,
                actor_user_id=user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )

        for org_id in surviving_orgs:
            await self._org_members.remove(org_id=org_id, user_id=user_id)

        return {
            "projects_deleted": len(owned),
            "projects_left": projects_left,
            "orgs_left": len(surviving_orgs),
            "solo_orgs_deleted": len(solo_orgs),
        }


__all__ = ["TenancyFacade"]
