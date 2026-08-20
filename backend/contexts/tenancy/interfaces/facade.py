"""Tenancy facade — public surface for the web layer."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.application.account_deletion_service import AccountDeletionService
from contexts.tenancy.application.invite_service import InvitableMember, InviteService
from contexts.tenancy.domain.models import MemberGroup, Org, OrgMember, Project, ProjectMember
from contexts.tenancy.infrastructure.repositories import (
    MemberGroupRepository,
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
        self._member_groups = MemberGroupRepository(db)
        self._account_deletion = AccountDeletionService(db)

    async def get_member_group(self, group_id: uuid.UUID) -> MemberGroup | None:
        """One live Member Group, or None.

        Exists so the conversation route that binds groups to a room can check
        each id belongs to that room's project without importing this context's
        application layer.
        """
        return await self._member_groups.get(group_id)

    async def live_member_group_ids(
        self, group_ids: Sequence[uuid.UUID], *, project_id: uuid.UUID
    ) -> set[uuid.UUID]:
        """Which of `group_ids` are live groups of `project_id` (R13.29).

        The single answer both room-binding routes need: the PUT rejects anything
        outside it, and the GET hides anything outside it. Splitting that question
        across the two — validating on write while reading back raw rows — let a
        deleted group's binding survive in the read and then be rejected on the
        next write, wedging the picker with no way out.
        """
        return await self._member_groups.live_ids_in_project(group_ids, project_id=project_id)

    async def member_group_ids_for_user(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        """Every live Member Group this user belongs to, across every project.

        The conversation context's room ACL intersects this with a room's bound
        groups. Deliberately shaped as "who is this user" rather than "may this
        user read that room": the room question belongs to the room ACL, and this
        context is not going to answer half of it (§13.2a, R13.30).
        """
        return await self._member_groups.group_ids_for_user(user_id)

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

    async def invitable_project_members(
        self,
        project_id: uuid.UUID,
        *,
        caller_user_id: uuid.UUID,
        caller_is_admin: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> Sequence[InvitableMember]:
        """Parent-Org members still invitable to this project (R6.10, Q-6).

        Empty for a user-owned project, and empty for a caller who is not a member
        of the parent Org — see ``InviteService.invitable_org_members`` for why the
        second case is a disclosure boundary and not a convenience.
        """
        return await InviteService(self._db).invitable_org_members(
            project_id,
            caller_user_id=caller_user_id,
            caller_is_admin=caller_is_admin,
            limit=limit,
            offset=offset,
        )

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

    async def get_org(self, org_id: uuid.UUID, *, include_deleted: bool = False) -> Org | None:
        """Read an org by id; soft-deleted rows are filtered unless requested.

        The admin-restore ancestor-liveness guard uses the default (filtered)
        form to check a project's parent-org is still live before restoring."""
        return await self._orgs.get(org_id, include_deleted=include_deleted)

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
    ) -> set[uuid.UUID]:
        """Remove FK RESTRICT references before hard-deleting a user row.

        Returns the erased project ids; the caller commits, then hands them to
        ``purge_hard_deleted_project_sources`` (F-7).
        """
        return await self._account_deletion.prepare_hard_delete(
            user_id=user_id,
            reassign_to_user_id=reassign_to_user_id,
        )

    async def purge_hard_deleted_project_sources(self, project_ids: set[uuid.UUID]) -> int:
        """Erase source infra for projects whose hard delete already committed (F-7)."""
        return await self._account_deletion.purge_hard_deleted_project_sources(project_ids)

    # ----- admin restore (R8.13) ------------------------------------------

    async def restore_org(
        self,
        *,
        resource_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Admin restore of a soft-deleted org (pure clear, no project cascade)."""
        from contexts.tenancy.application.org_service import OrgService

        return await OrgService(self._db).admin_restore(
            org_id=resource_id,
            admin_user_id=admin_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def restore_project(
        self,
        *,
        resource_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Admin restore of a soft-deleted project."""
        from contexts.tenancy.application.project_service import ProjectService

        return await ProjectService(self._db).admin_restore(
            project_id=resource_id,
            admin_user_id=admin_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )


__all__ = ["TenancyFacade"]
