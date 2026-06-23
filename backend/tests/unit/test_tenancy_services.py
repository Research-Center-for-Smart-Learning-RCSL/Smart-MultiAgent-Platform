"""Unit tests for OrgService and ProjectService.

Covers: atomic org creation (3-row), get/list, rename, soft_delete/restore,
member management (add, remove with OC protection, role changes),
orgs_blocking_self_delete, project creation (user/org owner), member removal
with key carry revocation (R7.04), reject_migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from contexts.tenancy.application.org_service import CreatedOrg, OrgService
from contexts.tenancy.application.project_service import ProjectService
from contexts.tenancy.domain.errors import (
    CannotMigrateIndividualProject,
    MemberNotFound,
    OrgNotFound,
    OriginalCreatorConflict,
    ProjectNotFound,
)
from contexts.tenancy.domain.models import (
    Org,
    OrgMember,
    OrgMemberRole,
    Project,
    ProjectMember,
    ProjectMemberRole,
    ProjectOwnerType,
)

_NOW = datetime(2026, 6, 22, 12, 0, 0)
_USER_ID = uuid.uuid4()
_ORG_ID = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()


def _make_org(*, org_id: uuid.UUID | None = None, version: int = 1) -> Org:
    return Org(
        id=org_id or _ORG_ID,
        name="Test Org",
        creator_user_id=_USER_ID,
        version=version,
        deleted_at=None,
        created_at=_NOW,
    )


def _make_project(
    *,
    project_id: uuid.UUID | None = None,
    owner_user_id: uuid.UUID | None = None,
    owner_org_id: uuid.UUID | None = None,
    version: int = 1,
) -> Project:
    return Project(
        id=project_id or _PROJECT_ID,
        owner_user_id=owner_user_id,
        owner_org_id=owner_org_id or _ORG_ID,
        name="Test Project",
        created_by_user_id=_USER_ID,
        version=version,
        deleted_at=None,
        created_at=_NOW,
    )


def _make_org_member(
    *,
    user_id: uuid.UUID | None = None,
    role: OrgMemberRole = OrgMemberRole.OWNER,
    is_original_creator: bool = False,
) -> OrgMember:
    return OrgMember(
        org_id=_ORG_ID,
        user_id=user_id or _USER_ID,
        role=role,
        is_original_creator=is_original_creator,
        joined_at=_NOW,
    )


def _make_project_member(
    *,
    user_id: uuid.UUID | None = None,
    role: ProjectMemberRole = ProjectMemberRole.OWNER,
) -> ProjectMember:
    return ProjectMember(
        project_id=_PROJECT_ID,
        user_id=user_id or _USER_ID,
        role=role,
        joined_at=_NOW,
    )


def _make_org_service(
    *,
    org_repo: AsyncMock | None = None,
    member_repo: AsyncMock | None = None,
    project_repo: AsyncMock | None = None,
    project_member_repo: AsyncMock | None = None,
) -> OrgService:
    db = AsyncMock()
    svc = OrgService(db)
    if org_repo is not None:
        svc._orgs = org_repo
    if member_repo is not None:
        svc._members = member_repo
    if project_repo is not None:
        svc._projects = project_repo
    if project_member_repo is not None:
        svc._project_members = project_member_repo
    return svc


def _make_project_service(
    *,
    project_repo: AsyncMock | None = None,
    member_repo: AsyncMock | None = None,
    org_member_repo: AsyncMock | None = None,
) -> ProjectService:
    db = AsyncMock()
    svc = ProjectService(db)
    if project_repo is not None:
        svc._projects = project_repo
    if member_repo is not None:
        svc._members = member_repo
    if org_member_repo is not None:
        svc._org_members = org_member_repo
    return svc


# ===========================================================================
# OrgService
# ===========================================================================


class TestOrgCreate:
    @patch("contexts.tenancy.application.org_service.audit.emit", new_callable=AsyncMock)
    async def test_creates_org_with_default_project(self, _audit) -> None:
        org = _make_org()
        project = _make_project()
        orgs = AsyncMock()
        orgs.create.return_value = org
        members = AsyncMock()
        projects = AsyncMock()
        projects.create.return_value = project
        project_members = AsyncMock()
        svc = _make_org_service(
            org_repo=orgs,
            member_repo=members,
            project_repo=projects,
            project_member_repo=project_members,
        )

        result = await svc.create(
            name="My Org",
            creator_user_id=_USER_ID,
            actor_ip="1.2.3.4",
        )

        assert isinstance(result, CreatedOrg)
        assert result.org.id == org.id
        assert result.default_project.id == project.id
        orgs.create.assert_awaited_once()
        members.add.assert_awaited_once()
        projects.create.assert_awaited_once()
        project_members.add.assert_awaited_once()
        add_call = members.add.call_args
        assert add_call.kwargs["is_original_creator"] is True
        assert add_call.kwargs["role"] == OrgMemberRole.OWNER


class TestOrgGet:
    async def test_found(self) -> None:
        org = _make_org()
        orgs = AsyncMock()
        orgs.get.return_value = org
        svc = _make_org_service(org_repo=orgs)

        result = await svc.get(_ORG_ID)
        assert result.id == org.id

    async def test_not_found(self) -> None:
        orgs = AsyncMock()
        orgs.get.return_value = None
        svc = _make_org_service(org_repo=orgs)

        with pytest.raises(OrgNotFound):
            await svc.get(uuid.uuid4())


class TestOrgRename:
    @patch("contexts.tenancy.application.org_service.audit.emit", new_callable=AsyncMock)
    async def test_rename(self, _audit) -> None:
        renamed = _make_org(version=2)
        orgs = AsyncMock()
        orgs.rename.return_value = renamed
        svc = _make_org_service(org_repo=orgs)

        result = await svc.rename(
            org_id=_ORG_ID,
            new_name="New Name",
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        assert result.version == 2


class TestOrgSoftDeleteRestore:
    @patch("contexts.tenancy.application.org_service.audit.emit", new_callable=AsyncMock)
    async def test_soft_delete(self, _audit) -> None:
        orgs = AsyncMock()
        svc = _make_org_service(org_repo=orgs)

        await svc.soft_delete(org_id=_ORG_ID, actor_user_id=_USER_ID, actor_ip=None)

        orgs.soft_delete.assert_awaited_once_with(_ORG_ID)

    @patch("contexts.tenancy.application.org_service.audit.emit", new_callable=AsyncMock)
    async def test_restore(self, _audit) -> None:
        orgs = AsyncMock()
        svc = _make_org_service(org_repo=orgs)

        await svc.restore(org_id=_ORG_ID, admin_user_id=_USER_ID, actor_ip=None)

        orgs.restore.assert_awaited_once_with(_ORG_ID)


class TestOrgMemberOps:
    @patch("contexts.tenancy.application.org_service.ProjectMemberRepository")
    @patch("contexts.keys.interfaces.facade.KeysFacade")
    @patch("contexts.tenancy.application.org_service.audit.emit", new_callable=AsyncMock)
    async def test_remove_member(self, _audit, mock_keys_cls, mock_pm_cls) -> None:
        target = uuid.uuid4()
        member = _make_org_member(user_id=target, is_original_creator=False)
        members = AsyncMock()
        members.get.return_value = member
        projects = AsyncMock()
        projects.list_by_org.return_value = []
        mock_keys_cls.return_value = AsyncMock()
        mock_pm_cls.return_value = AsyncMock()
        svc = _make_org_service(member_repo=members, project_repo=projects)

        await svc.remove_member(
            org_id=_ORG_ID,
            target_user_id=target,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        members.remove.assert_awaited_once()

    async def test_remove_member_not_found(self) -> None:
        members = AsyncMock()
        members.get.return_value = None
        svc = _make_org_service(member_repo=members)

        with pytest.raises(MemberNotFound):
            await svc.remove_member(
                org_id=_ORG_ID,
                target_user_id=uuid.uuid4(),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    async def test_remove_original_creator_blocked(self) -> None:
        oc = _make_org_member(is_original_creator=True)
        members = AsyncMock()
        members.get.return_value = oc
        svc = _make_org_service(member_repo=members)

        with pytest.raises(OriginalCreatorConflict):
            await svc.remove_member(
                org_id=_ORG_ID,
                target_user_id=oc.user_id,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    @patch("contexts.tenancy.application.org_service.audit.emit", new_callable=AsyncMock)
    async def test_change_role(self, _audit) -> None:
        target = uuid.uuid4()
        member = _make_org_member(user_id=target, role=OrgMemberRole.MEMBER)
        members = AsyncMock()
        members.get.return_value = member
        members.change_role.return_value = 1
        svc = _make_org_service(member_repo=members)

        await svc.change_member_role(
            org_id=_ORG_ID,
            target_user_id=target,
            new_role=OrgMemberRole.OWNER,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        members.change_role.assert_awaited_once()

    async def test_change_role_original_creator_blocked(self) -> None:
        oc = _make_org_member(is_original_creator=True, role=OrgMemberRole.OWNER)
        members = AsyncMock()
        members.get.return_value = oc
        svc = _make_org_service(member_repo=members)

        with pytest.raises(OriginalCreatorConflict):
            await svc.change_member_role(
                org_id=_ORG_ID,
                target_user_id=oc.user_id,
                new_role=OrgMemberRole.MEMBER,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )


class TestOrgIsOriginalCreator:
    async def test_true(self) -> None:
        member = _make_org_member(is_original_creator=True)
        members = AsyncMock()
        members.get.return_value = member
        svc = _make_org_service(member_repo=members)

        assert await svc.is_original_creator(user_id=_USER_ID, org_id=_ORG_ID) is True

    async def test_false_non_oc(self) -> None:
        member = _make_org_member(is_original_creator=False)
        members = AsyncMock()
        members.get.return_value = member
        svc = _make_org_service(member_repo=members)

        assert await svc.is_original_creator(user_id=_USER_ID, org_id=_ORG_ID) is False

    async def test_false_not_member(self) -> None:
        members = AsyncMock()
        members.get.return_value = None
        svc = _make_org_service(member_repo=members)

        assert await svc.is_original_creator(user_id=uuid.uuid4(), org_id=_ORG_ID) is False


class TestOrgsBlockingSelfDelete:
    async def test_returns_blocking_orgs(self) -> None:
        org = _make_org()
        orgs = AsyncMock()
        orgs.list_for_user.return_value = [org]
        oc_member = _make_org_member(is_original_creator=True)
        members = AsyncMock()
        members.original_creator.return_value = oc_member
        members.count_active_members.return_value = 3
        svc = _make_org_service(org_repo=orgs, member_repo=members)

        blocked = await svc.orgs_blocking_self_delete(_USER_ID)

        assert blocked == [org.id]

    async def test_solo_org_not_blocking(self) -> None:
        org = _make_org()
        orgs = AsyncMock()
        orgs.list_for_user.return_value = [org]
        oc_member = _make_org_member(is_original_creator=True)
        members = AsyncMock()
        members.original_creator.return_value = oc_member
        members.count_active_members.return_value = 1
        svc = _make_org_service(org_repo=orgs, member_repo=members)

        blocked = await svc.orgs_blocking_self_delete(_USER_ID)

        assert blocked == []


# ===========================================================================
# ProjectService
# ===========================================================================


class TestProjectCreate:
    @patch("contexts.tenancy.application.project_service.audit.emit", new_callable=AsyncMock)
    async def test_create_user_owned(self, _audit) -> None:
        project = _make_project(owner_user_id=_USER_ID, owner_org_id=None)
        projects = AsyncMock()
        projects.create.return_value = project
        members = AsyncMock()
        svc = _make_project_service(project_repo=projects, member_repo=members)

        result = await svc.create(
            owner_type=ProjectOwnerType.USER,
            owner_id=_USER_ID,
            name="My Project",
            created_by_user_id=_USER_ID,
            actor_ip=None,
        )

        assert result.id == project.id
        projects.create.assert_awaited_once()
        create_call = projects.create.call_args.kwargs
        assert create_call["owner_user_id"] == _USER_ID
        assert create_call["owner_org_id"] is None
        members.add.assert_awaited_once()

    @patch("contexts.tenancy.application.project_service.audit.emit", new_callable=AsyncMock)
    async def test_create_org_owned(self, _audit) -> None:
        project = _make_project()
        projects = AsyncMock()
        projects.create.return_value = project
        members = AsyncMock()
        svc = _make_project_service(project_repo=projects, member_repo=members)

        await svc.create(
            owner_type=ProjectOwnerType.ORG,
            owner_id=_ORG_ID,
            name="Org Project",
            created_by_user_id=_USER_ID,
            actor_ip=None,
        )

        create_call = projects.create.call_args.kwargs
        assert create_call["owner_org_id"] == _ORG_ID
        assert create_call["owner_user_id"] is None


class TestProjectGet:
    async def test_found(self) -> None:
        project = _make_project()
        projects = AsyncMock()
        projects.get.return_value = project
        svc = _make_project_service(project_repo=projects)

        result = await svc.get(_PROJECT_ID)
        assert result.id == project.id

    async def test_not_found(self) -> None:
        projects = AsyncMock()
        projects.get.return_value = None
        svc = _make_project_service(project_repo=projects)

        with pytest.raises(ProjectNotFound):
            await svc.get(uuid.uuid4())


class TestProjectRename:
    @patch("contexts.tenancy.application.project_service.audit.emit", new_callable=AsyncMock)
    async def test_rename(self, _audit) -> None:
        renamed = _make_project(version=2)
        projects = AsyncMock()
        projects.rename.return_value = renamed
        svc = _make_project_service(project_repo=projects)

        result = await svc.rename(
            project_id=_PROJECT_ID,
            new_name="Renamed",
            expected_version=1,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        assert result.version == 2


class TestProjectSoftDeleteRestore:
    @patch("contexts.tenancy.application.project_service.audit.emit", new_callable=AsyncMock)
    async def test_soft_delete(self, _audit) -> None:
        projects = AsyncMock()
        svc = _make_project_service(project_repo=projects)

        await svc.soft_delete(project_id=_PROJECT_ID, actor_user_id=_USER_ID, actor_ip=None)

        projects.soft_delete.assert_awaited_once_with(_PROJECT_ID)

    @patch("contexts.tenancy.application.project_service.audit.emit", new_callable=AsyncMock)
    async def test_restore(self, _audit) -> None:
        projects = AsyncMock()
        svc = _make_project_service(project_repo=projects)

        await svc.restore(project_id=_PROJECT_ID, admin_user_id=_USER_ID, actor_ip=None)

        projects.restore.assert_awaited_once_with(_PROJECT_ID)


class TestProjectMemberOps:
    @patch("contexts.keys.interfaces.facade.KeysFacade")
    @patch("contexts.tenancy.application.project_service.audit.emit", new_callable=AsyncMock)
    async def test_remove_member_revokes_carries(self, _audit, mock_keys_cls) -> None:
        target = uuid.uuid4()
        member = _make_project_member(user_id=target)
        members = AsyncMock()
        members.get.return_value = member
        mock_facade = AsyncMock()
        mock_facade.revoke_carries_for_user_leaving_project.return_value = [uuid.uuid4()]
        mock_keys_cls.return_value = mock_facade
        svc = _make_project_service(member_repo=members)

        await svc.remove_member(
            project_id=_PROJECT_ID,
            target_user_id=target,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        members.remove.assert_awaited_once()
        mock_facade.revoke_carries_for_user_leaving_project.assert_awaited_once()

    async def test_remove_member_not_found(self) -> None:
        members = AsyncMock()
        members.get.return_value = None
        svc = _make_project_service(member_repo=members)

        with pytest.raises(MemberNotFound):
            await svc.remove_member(
                project_id=_PROJECT_ID,
                target_user_id=uuid.uuid4(),
                actor_user_id=_USER_ID,
                actor_ip=None,
            )

    @patch("contexts.tenancy.application.project_service.audit.emit", new_callable=AsyncMock)
    async def test_change_member_role(self, _audit) -> None:
        members = AsyncMock()
        members.change_role.return_value = 1
        svc = _make_project_service(member_repo=members)

        await svc.change_member_role(
            project_id=_PROJECT_ID,
            target_user_id=uuid.uuid4(),
            new_role=ProjectMemberRole.OWNER,
            actor_user_id=_USER_ID,
            actor_ip=None,
        )

        members.change_role.assert_awaited_once()

    async def test_change_role_member_not_found(self) -> None:
        members = AsyncMock()
        members.change_role.return_value = 0
        svc = _make_project_service(member_repo=members)

        with pytest.raises(MemberNotFound):
            await svc.change_member_role(
                project_id=_PROJECT_ID,
                target_user_id=uuid.uuid4(),
                new_role=ProjectMemberRole.MEMBER,
                actor_user_id=_USER_ID,
                actor_ip=None,
            )


class TestRejectMigration:
    async def test_individual_project_raises(self) -> None:
        project = _make_project(owner_user_id=_USER_ID, owner_org_id=None)
        projects = AsyncMock()
        projects.get.return_value = project
        svc = _make_project_service(project_repo=projects)

        with pytest.raises(CannotMigrateIndividualProject):
            await svc.reject_migration(project_id=_PROJECT_ID)

    async def test_org_project_passes(self) -> None:
        project = _make_project(owner_org_id=_ORG_ID)
        projects = AsyncMock()
        projects.get.return_value = project
        svc = _make_project_service(project_repo=projects)

        await svc.reject_migration(project_id=_PROJECT_ID)

    async def test_nonexistent_project_passes(self) -> None:
        projects = AsyncMock()
        projects.get.return_value = None
        svc = _make_project_service(project_repo=projects)

        await svc.reject_migration(project_id=uuid.uuid4())
