"""Member Group use-cases (§13.2a, [R13.28]-[R13.31]).

A Member Group is a named subset of one project's members, used to narrow chat-room
visibility below project level. It is **not a role**: nothing here grants a
capability, and the only consumer of group membership is the chat-room access check
([R5.06], [R13.30]).

Two invariants live here rather than in the schema, because both need to explain
themselves to the caller:

- only a current member of the parent project may join its groups ([R13.28]);
- a non-owner may read the groups they belong to and no others ([R13.31]).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.domain.errors import (
    MemberGroupNotFound,
    NotAProjectMember,
)
from contexts.tenancy.domain.models import MemberGroup, MemberGroupMember
from contexts.tenancy.infrastructure.repositories import (
    MemberGroupRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from shared_kernel import audit


class MemberGroupService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._groups = MemberGroupRepository(db)
        self._members = ProjectMemberRepository(db)
        self._projects = ProjectRepository(db)

    # ---- reads -----------------------------------------------------------

    async def get(self, group_id: uuid.UUID) -> MemberGroup:
        group = await self._groups.get(group_id)
        if group is None:
            raise MemberGroupNotFound(str(group_id))
        return group

    async def list_for_project(
        self,
        *,
        project_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        caller_is_manager: bool,
    ) -> Sequence[MemberGroup]:
        """R13.31 — a manager sees the project's groups; anyone else sees their own.

        The narrowing is a confidentiality control, not a convenience: in a
        classroom, which groups exist and who is in them is exactly what one team
        should not be able to enumerate about another.
        """
        if caller_is_manager:
            return await self._groups.list_for_project(project_id)
        return await self._groups.list_for_user_in_project(project_id=project_id, user_id=caller_user_id)

    async def list_members(self, group_id: uuid.UUID) -> Sequence[MemberGroupMember]:
        return await self._groups.list_members(group_id)

    async def is_visible_to(self, *, group: MemberGroup, user_id: uuid.UUID) -> bool:
        """Whether a non-manager may see this group at all (R13.31)."""
        own = await self._groups.list_for_user_in_project(project_id=group.project_id, user_id=user_id)
        return any(g.id == group.id for g in own)

    # ---- commands --------------------------------------------------------

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        name: str,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> MemberGroup:
        group = await self._groups.create(
            project_id=project_id,
            name=name,
            created_by_user_id=actor_user_id,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.member_group_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="member_group",
                resource_id=group.id,
                metadata={"project_id": str(project_id), "name": name},
                request_id=request_id,
            ),
        )
        return group

    async def rename(
        self,
        *,
        group_id: uuid.UUID,
        new_name: str,
        expected_version: int,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> MemberGroup:
        group = await self._groups.rename(
            group_id=group_id,
            new_name=new_name,
            expected_version=expected_version,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.member_group_renamed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="member_group",
                resource_id=group_id,
                metadata={"project_id": str(group.project_id), "new_name": new_name},
                request_id=request_id,
            ),
        )
        return group

    async def delete(
        self,
        *,
        group_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        group = await self.get(group_id)
        if not await self._groups.soft_delete(group_id):
            raise MemberGroupNotFound(str(group_id))
        # The bindings are deliberately left in place. R13.29 makes a binding to a
        # deleted group grant nothing, so they are inert, and keeping them means a
        # group deleted by accident can be restored without re-binding every room.
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.member_group_deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="member_group",
                resource_id=group_id,
                metadata={"project_id": str(group.project_id)},
                request_id=request_id,
            ),
        )

    async def add_member(
        self,
        *,
        group_id: uuid.UUID,
        user_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        group = await self.get(group_id)
        if not await self._is_project_member(project_id=group.project_id, user_id=user_id):
            raise NotAProjectMember(str(user_id))
        await self._groups.add_member(group_id=group_id, user_id=user_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.member_group_member_added",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="member_group",
                resource_id=group_id,
                metadata={"project_id": str(group.project_id), "user_id": str(user_id)},
                request_id=request_id,
            ),
        )

    async def remove_member(
        self,
        *,
        group_id: uuid.UUID,
        user_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        group = await self.get(group_id)
        await self._groups.remove_member(group_id=group_id, user_id=user_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="project.member_group_member_removed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="member_group",
                resource_id=group_id,
                metadata={"project_id": str(group.project_id), "user_id": str(user_id)},
                request_id=request_id,
            ),
        )

    async def _is_project_member(self, *, project_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """R13.28's gate.

        A `project_members` row is not the only way to hold standing in a project:
        an Org Owner of the parent org is a Project Owner without one (R5.03), and
        so is the owner of a user-owned project. Either counts — refusing to put a
        teacher in a group they are entitled to moderate would be a rule invented
        here rather than one the SRS asks for.
        """
        if await self._members.get(project_id=project_id, user_id=user_id) is not None:
            return True
        project = await self._projects.get(project_id)
        if project is None:
            return False
        if project.owner_user_id == user_id:
            return True
        if project.owner_org_id is None:
            return False
        return project.owner_org_id in await self._org_owned_ids(user_id)

    async def _org_owned_ids(self, user_id: uuid.UUID) -> set[uuid.UUID]:
        from contexts.tenancy.infrastructure.repositories import OrgMemberRepository

        return await OrgMemberRepository(self._db).owned_org_ids(user_id)


__all__ = ["MemberGroupService"]
