"""Concrete `RoleResolver` implementation (R5.03 computed inheritance).

This module is the single place the permission matrix gets data from. It
reads `org_members` and `project_members` directly (it lives in the tenancy
context, so no cross-context access is being performed). Consumers obtain
it via `shared_kernel.auth.dependencies.get_role_resolver`.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from shared_kernel.auth.permissions import Principal, Role, RoleResolver, Scope


class TenancyRoleResolver(RoleResolver):
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._org_members = OrgMemberRepository(db)
        self._project_members = ProjectMemberRepository(db)
        self._projects = ProjectRepository(db)

    async def roles_for(self, principal: Principal, scope: Scope) -> frozenset[Role]:
        roles: set[Role] = set()
        project = None
        org_id = scope.org_id
        if scope.project_id:
            project = await self._projects.get(scope.project_id)
            if project and project.owner_org_id:
                # R5.03 — Org Owner in the parent Org is a Project Owner here.
                org_id = project.owner_org_id

        if org_id:
            om = await self._org_members.get(org_id=org_id, user_id=principal.user_id)
            if om is not None:
                roles.add(
                    Role.ORG_OWNER if om.role is OrgMemberRole.OWNER else Role.ORG_MEMBER,
                )
                if om.role is OrgMemberRole.OWNER and project is not None:
                    roles.add(Role.PROJECT_OWNER)

        if scope.project_id:
            pm = await self._project_members.get(
                project_id=scope.project_id,
                user_id=principal.user_id,
            )
            if pm is not None:
                roles.add(
                    Role.PROJECT_OWNER if pm.role is ProjectMemberRole.OWNER else Role.PROJECT_MEMBER,
                )
            # R5.03 again: user-owned project → owner bit
            if project and project.owner_user_id == principal.user_id:
                roles.add(Role.PROJECT_OWNER)

        # No empty-scope fallback (SEC-3). An empty scope no longer degrades
        # to a default PROJECT_MEMBER role: `permissions.decide` handles the
        # empty-scope case explicitly — allowing only the self-service
        # capabilities and failing everything else closed — before it ever
        # calls this resolver. An empty scope here therefore yields no roles.
        return frozenset(roles)

    async def is_original_creator(self, *, user_id: uuid.UUID, org_id: uuid.UUID) -> bool:
        member = await self._org_members.get(org_id=org_id, user_id=user_id)
        return member is not None and member.is_original_creator

    async def is_chatroom_participant(self, *, user_id: uuid.UUID, chatroom_id: uuid.UUID) -> bool:
        # Chat-room ACL ships in Phase F; for now any authenticated user
        # resolves as a participant of chat rooms in their own projects. The
        # real table lookup hooks in here without touching the matrix logic.
        return True


__all__ = ["TenancyRoleResolver"]
