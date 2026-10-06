"""`project_role_holders` must agree with `roles_for` on every user.

Spec: ``docs/tasks/2026-10-05-guest-sender-marking/spec.md`` §6, Q-3. A registered
guest holding no project role is marked as a guest to classmates and the agent; a
batch answer that drifted from the gate would badge a member, or unbadge a guest
presenting as one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from contexts.tenancy.domain.models import OrgMember, OrgMemberRole, Project, ProjectMember, ProjectMemberRole
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.permissions import Principal, Role, Scope

_NOW = datetime(2026, 10, 6, tzinfo=UTC)
_PROJECT_ROLES = {Role.PROJECT_OWNER, Role.PROJECT_MEMBER}


def _project(*, owner_org_id: uuid.UUID | None = None, owner_user_id: uuid.UUID | None = None) -> Project:
    return Project(
        id=uuid.uuid4(),
        name="p",
        owner_user_id=owner_user_id,
        owner_org_id=owner_org_id,
        created_by_user_id=uuid.uuid4(),
        version=1,
        created_at=_NOW,
        deleted_at=None,
    )


def _resolver(
    project: Project,
    *,
    org_roles: dict[uuid.UUID, OrgMemberRole],
    project_roles: dict[uuid.UUID, ProjectMemberRole],
) -> TenancyRoleResolver:
    resolver = TenancyRoleResolver(db=None)  # type: ignore[arg-type]

    async def get_project(pid: uuid.UUID, *, include_deleted: bool = False) -> Project | None:
        return project if pid == project.id else None

    async def get_org_member(*, org_id: uuid.UUID, user_id: uuid.UUID) -> OrgMember | None:
        if org_id != project.owner_org_id or user_id not in org_roles:
            return None
        return OrgMember(
            org_id=org_id, user_id=user_id, role=org_roles[user_id], is_original_creator=False, joined_at=_NOW
        )

    async def owner_user_ids(*, org_id: uuid.UUID, user_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        if org_id != project.owner_org_id:
            return set()
        return {u for u in user_ids if org_roles.get(u) is OrgMemberRole.OWNER}

    async def get_project_member(*, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectMember | None:
        if project_id != project.id or user_id not in project_roles:
            return None
        return ProjectMember(
            project_id=project_id, user_id=user_id, role=project_roles[user_id], joined_at=_NOW
        )

    async def member_user_ids(*, project_id: uuid.UUID, user_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
        return {u for u in user_ids if project_id == project.id and u in project_roles}

    resolver._projects.get = get_project  # type: ignore[method-assign]
    resolver._org_members.get = get_org_member  # type: ignore[method-assign]
    resolver._org_members.owner_user_ids = owner_user_ids  # type: ignore[method-assign]
    resolver._project_members.get = get_project_member  # type: ignore[method-assign]
    resolver._project_members.member_user_ids = member_user_ids  # type: ignore[method-assign]
    return resolver


async def _one_by_one(
    resolver: TenancyRoleResolver, users: list[uuid.UUID], project: Project
) -> set[uuid.UUID]:
    holders = set()
    for u in users:
        roles = await resolver.roles_for(
            Principal(user_id=u, is_admin=False, email_verified=True), Scope(project_id=project.id)
        )
        if roles & _PROJECT_ROLES:
            holders.add(u)
    return holders


async def test_the_two_forms_agree_over_a_mixed_roster() -> None:
    org = uuid.uuid4()
    project = _project(owner_org_id=org)
    org_owner, org_member, project_owner, project_member, stranger = (uuid.uuid4() for _ in range(5))
    resolver = _resolver(
        project,
        org_roles={org_owner: OrgMemberRole.OWNER, org_member: OrgMemberRole.MEMBER},
        project_roles={project_owner: ProjectMemberRole.OWNER, project_member: ProjectMemberRole.MEMBER},
    )
    users = [org_owner, org_member, project_owner, project_member, stranger]

    batch = await resolver.project_role_holders(users, project_id=project.id)

    # A plain org member holds no project role: enrolled through the link, they
    # are a guest of this room.
    assert batch == {org_owner, project_owner, project_member}
    assert batch == await _one_by_one(resolver, users, project)


async def test_the_owner_of_a_user_owned_project_holds_a_role() -> None:
    owner, stranger = uuid.uuid4(), uuid.uuid4()
    project = _project(owner_user_id=owner)
    resolver = _resolver(project, org_roles={}, project_roles={})

    batch = await resolver.project_role_holders([owner, stranger], project_id=project.id)

    assert batch == {owner}
    assert batch == await _one_by_one(resolver, [owner, stranger], project)


@pytest.mark.parametrize("users", [[], ()])
async def test_no_users_asks_the_database_nothing(users: Sequence[uuid.UUID]) -> None:
    resolver = TenancyRoleResolver(db=None)  # type: ignore[arg-type]

    # No stubs installed: touching any repository would raise.
    assert await resolver.project_role_holders(users, project_id=uuid.uuid4()) == set()
