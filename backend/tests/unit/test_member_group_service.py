"""Member Group invariants that live in the service, not the schema (§13.2a).

Two rules are enforced here rather than by a constraint, because both have to be
able to explain themselves to the caller: only a current member of the parent
project may join its groups ([R13.28]), and a non-manager may read the groups they
belong to and no others ([R13.31]).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from contexts.tenancy.application.member_group_service import MemberGroupService
from contexts.tenancy.domain.errors import MemberGroupNotFound, NotAProjectMember
from contexts.tenancy.domain.models import MemberGroup, Project

_PROJECT_ID = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_ORG_ID = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
_GROUP_ID = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
_USER_ID = uuid.UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
_ACTOR_ID = uuid.UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")


def _group(group_id: uuid.UUID = _GROUP_ID, name: str = "team-a") -> MemberGroup:
    return MemberGroup(
        id=group_id,
        project_id=_PROJECT_ID,
        name=name,
        created_by_user_id=_ACTOR_ID,
        version=1,
        created_at=datetime.now(UTC),
        deleted_at=None,
    )


def _project(*, owner_org_id: uuid.UUID | None = _ORG_ID, owner_user_id: uuid.UUID | None = None):
    return Project(
        id=_PROJECT_ID,
        owner_user_id=owner_user_id,
        owner_org_id=owner_org_id,
        name="p",
        created_by_user_id=_ACTOR_ID,
        version=1,
        deleted_at=None,
        created_at=datetime.now(UTC),
    )


def _service(
    *,
    groups: AsyncMock | None = None,
    members: AsyncMock | None = None,
    projects: AsyncMock | None = None,
) -> MemberGroupService:
    svc = MemberGroupService(AsyncMock())
    if groups is not None:
        svc._groups = groups
    if members is not None:
        svc._members = members
    if projects is not None:
        svc._projects = projects
    return svc


def _org_owner_of(*org_ids: uuid.UUID):
    """Patch the org-owner lookup the project-membership gate falls back to."""
    return patch(
        "contexts.tenancy.infrastructure.repositories.OrgMemberRepository",
        return_value=SimpleNamespace(owned_org_ids=AsyncMock(return_value=set(org_ids))),
    )


_AUDIT = patch("contexts.tenancy.application.member_group_service.audit.emit", new_callable=AsyncMock)


class TestMembershipGate:
    """[R13.28] — only a current member of the parent project may join a group."""

    @pytest.mark.asyncio
    async def test_a_stranger_to_the_project_is_refused(self) -> None:
        groups = AsyncMock()
        groups.get.return_value = _group()
        members = AsyncMock()
        members.get.return_value = None
        projects = AsyncMock()
        projects.get.return_value = _project()
        svc = _service(groups=groups, members=members, projects=projects)

        with _AUDIT, _org_owner_of(), pytest.raises(NotAProjectMember):
            await svc.add_member(
                group_id=_GROUP_ID,
                user_id=_USER_ID,
                actor_user_id=_ACTOR_ID,
                actor_ip=None,
            )
        groups.add_member.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_project_member_is_admitted(self) -> None:
        groups = AsyncMock()
        groups.get.return_value = _group()
        members = AsyncMock()
        members.get.return_value = SimpleNamespace(user_id=_USER_ID)
        svc = _service(groups=groups, members=members, projects=AsyncMock())

        with _AUDIT:
            await svc.add_member(
                group_id=_GROUP_ID,
                user_id=_USER_ID,
                actor_user_id=_ACTOR_ID,
                actor_ip=None,
            )
        groups.add_member.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_an_org_owner_is_admitted_without_a_project_members_row(self) -> None:
        """R5.03: an Org Owner is a Project Owner on every project of the org.

        Refusing to put a teacher in a group they already moderate would be a rule
        invented in this service rather than one the SRS asks for.
        """
        groups = AsyncMock()
        groups.get.return_value = _group()
        members = AsyncMock()
        members.get.return_value = None
        projects = AsyncMock()
        projects.get.return_value = _project()
        svc = _service(groups=groups, members=members, projects=projects)

        with _AUDIT, _org_owner_of(_ORG_ID):
            await svc.add_member(
                group_id=_GROUP_ID,
                user_id=_USER_ID,
                actor_user_id=_ACTOR_ID,
                actor_ip=None,
            )
        groups.add_member.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_the_owner_of_a_user_owned_project_is_admitted(self) -> None:
        groups = AsyncMock()
        groups.get.return_value = _group()
        members = AsyncMock()
        members.get.return_value = None
        projects = AsyncMock()
        projects.get.return_value = _project(owner_org_id=None, owner_user_id=_USER_ID)
        svc = _service(groups=groups, members=members, projects=projects)

        with _AUDIT, _org_owner_of():
            await svc.add_member(
                group_id=_GROUP_ID,
                user_id=_USER_ID,
                actor_user_id=_ACTOR_ID,
                actor_ip=None,
            )
        groups.add_member.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_missing_group_is_not_found_rather_than_a_membership_error(self) -> None:
        groups = AsyncMock()
        groups.get.return_value = None
        svc = _service(groups=groups, members=AsyncMock(), projects=AsyncMock())

        with pytest.raises(MemberGroupNotFound):
            await svc.add_member(
                group_id=_GROUP_ID,
                user_id=_USER_ID,
                actor_user_id=_ACTOR_ID,
                actor_ip=None,
            )


class TestVisibilityNarrowing:
    """[R13.31] — a non-manager must not learn that other groups exist."""

    @pytest.mark.asyncio
    async def test_a_manager_sees_every_group_in_the_project(self) -> None:
        groups = AsyncMock()
        groups.list_for_project.return_value = [_group(name="a"), _group(uuid.uuid4(), "b")]
        svc = _service(groups=groups)

        result = await svc.list_for_project(
            project_id=_PROJECT_ID, caller_user_id=_ACTOR_ID, caller_is_manager=True
        )

        assert [g.name for g in result] == ["a", "b"]
        groups.list_for_user_in_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_a_plain_member_sees_only_their_own_groups(self) -> None:
        groups = AsyncMock()
        groups.list_for_user_in_project.return_value = [_group(name="mine")]
        svc = _service(groups=groups)

        result = await svc.list_for_project(
            project_id=_PROJECT_ID, caller_user_id=_USER_ID, caller_is_manager=False
        )

        assert [g.name for g in result] == ["mine"]
        groups.list_for_project.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_visible_to_is_false_for_a_group_the_user_is_not_in(self) -> None:
        groups = AsyncMock()
        groups.list_for_user_in_project.return_value = [_group(uuid.uuid4(), "someone-elses")]
        svc = _service(groups=groups)

        assert await svc.is_visible_to(group=_group(), user_id=_USER_ID) is False

    @pytest.mark.asyncio
    async def test_is_visible_to_is_true_for_a_group_the_user_is_in(self) -> None:
        groups = AsyncMock()
        groups.list_for_user_in_project.return_value = [_group()]
        svc = _service(groups=groups)

        assert await svc.is_visible_to(group=_group(), user_id=_USER_ID) is True


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_soft_deletes_and_leaves_bindings_alone(self) -> None:
        """R13.29 makes a binding to a deleted group inert, so the bindings are
        kept: an accidental delete can be undone without re-binding every room."""
        groups = AsyncMock()
        groups.get.return_value = _group()
        groups.soft_delete.return_value = True
        svc = _service(groups=groups)

        with _AUDIT:
            await svc.delete(group_id=_GROUP_ID, actor_user_id=_ACTOR_ID, actor_ip=None)

        groups.soft_delete.assert_awaited_once_with(_GROUP_ID)

    @pytest.mark.asyncio
    async def test_deleting_an_already_deleted_group_is_not_found(self) -> None:
        groups = AsyncMock()
        groups.get.return_value = _group()
        groups.soft_delete.return_value = False
        svc = _service(groups=groups)

        with _AUDIT, pytest.raises(MemberGroupNotFound):
            await svc.delete(group_id=_GROUP_ID, actor_user_id=_ACTOR_ID, actor_ip=None)
