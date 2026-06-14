"""Wiring tier — account self-deletion cascade (R6.07 / R8.14 / R8.18).

Pre-existing infra (the ``OriginalCreatorSelfDeleteBlocked`` error + 409 map and
``orgs_blocking_self_delete``) was scoped but never wired to an endpoint; this
tier guards the real cascade end-to-end against **real** Postgres + Redis (no
service-boundary mocks), the gap the 2026-06-11 audit flagged.

Each test pins one behaviour and cites the requirement it guards (§0.3).
"""

from __future__ import annotations

import uuid

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.identity.application.factory import create_auth_service
from contexts.identity.domain.errors import (
    InvalidCredentials,
    OriginalCreatorSelfDeleteBlocked,
)
from contexts.identity.domain.models import User, UserStatus
from contexts.identity.infrastructure.repositories import UserRepository
from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from shared_kernel.auth.password import PasswordHasher
from shared_kernel.db.session import async_session

pytestmark = pytest.mark.wiring

_PASSWORD = "Str0ng-P@ssw0rd-1"  # test fixture credential, not a secret
_hasher = PasswordHasher()


async def _insert_active_user(db: AsyncSession) -> User:
    u = uuid.uuid4().hex[:8]
    return await UserRepository(db).insert(
        email=f"del-{u}@example.com",
        password_hash=_hasher.hash(_PASSWORD),
        status=UserStatus.ACTIVE,
    )


# --------------------------------------------------------------------------- #
# 1. Happy path — individual projects deleted, other memberships dropped.      #
# --------------------------------------------------------------------------- #


async def test_self_delete_cascades_projects_and_memberships() -> None:
    async with async_session() as db:
        user = await _insert_active_user(db)

        # (a) An Individual-owned project (soft-deleted by R8.14).
        own = await ProjectRepository(db).create(
            name=f"own-{uuid.uuid4().hex[:6]}",
            owner_user_id=user.id,
            owner_org_id=None,
            created_by_user_id=user.id,
        )
        await ProjectMemberRepository(db).add(
            project_id=own.id, user_id=user.id, role=ProjectMemberRole.OWNER
        )

        # (b) Another user's Org + project where our user is merely a member
        #     (membership dropped; the Org itself survives).
        other = await _insert_active_user(db)
        org = await OrgRepository(db).create(name=f"org-{uuid.uuid4().hex[:6]}", creator_user_id=other.id)
        await OrgMemberRepository(db).add(
            org_id=org.id, user_id=other.id, role=OrgMemberRole.OWNER, is_original_creator=True
        )
        await OrgMemberRepository(db).add(org_id=org.id, user_id=user.id, role=OrgMemberRole.MEMBER)
        org_proj = await ProjectRepository(db).create(
            name=f"op-{uuid.uuid4().hex[:6]}",
            owner_user_id=None,
            owner_org_id=org.id,
            created_by_user_id=other.id,
        )
        await ProjectMemberRepository(db).add(
            project_id=org_proj.id, user_id=user.id, role=ProjectMemberRole.MEMBER
        )
        await db.commit()

        svc = create_auth_service(db, public_origin="http://localhost")
        counts = await svc.delete_account(user_id=user.id, password=_PASSWORD, remote_ip=None)
        await db.commit()

        assert counts == {
            "projects_deleted": 1,
            "projects_left": 1,
            "orgs_left": 1,
            "solo_orgs_deleted": 0,
        }

        u2 = await UserRepository(db).get_by_id(user.id)
        assert u2 is not None
        assert u2.status is UserStatus.DELETED
        assert u2.deleted_at is not None

        # Individual project soft-deleted (gone from the live view, present with
        # include_deleted — it is recoverable for 60 days, not hard-deleted).
        assert await ProjectRepository(db).get(own.id) is None
        assert await ProjectRepository(db).get(own.id, include_deleted=True) is not None

        # Membership in the other Org's project is gone; the Org survives.
        assert await ProjectMemberRepository(db).get(project_id=org_proj.id, user_id=user.id) is None
        assert await OrgMemberRepository(db).get(org_id=org.id, user_id=user.id) is None
        assert await OrgRepository(db).get(org.id) is not None

        audited = (
            await db.execute(
                sa.text(
                    "SELECT count(*) FROM audit_logs "
                    "WHERE action = 'user.self_deleted' AND resource_id = :rid"
                ),
                {"rid": user.id},
            )
        ).scalar_one()
        assert audited == 1


# --------------------------------------------------------------------------- #
# 2. R8.18 — blocked while OC of an Org that has other active members.         #
# --------------------------------------------------------------------------- #


async def test_self_delete_blocked_for_oc_with_other_members() -> None:
    async with async_session() as db:
        oc = await _insert_active_user(db)
        org = await OrgRepository(db).create(name=f"org-{uuid.uuid4().hex[:6]}", creator_user_id=oc.id)
        await OrgMemberRepository(db).add(
            org_id=org.id, user_id=oc.id, role=OrgMemberRole.OWNER, is_original_creator=True
        )
        member = await _insert_active_user(db)
        await OrgMemberRepository(db).add(org_id=org.id, user_id=member.id, role=OrgMemberRole.OWNER)
        await db.commit()

        svc = create_auth_service(db, public_origin="http://localhost")
        with pytest.raises(OriginalCreatorSelfDeleteBlocked) as excinfo:
            await svc.delete_account(user_id=oc.id, password=_PASSWORD, remote_ip=None)

        assert str(org.id) in excinfo.value.blocked_orgs

        # Nothing was mutated — the block fires before any write.
        u = await UserRepository(db).get_by_id(oc.id)
        assert u is not None
        assert u.status is UserStatus.ACTIVE
        assert await OrgRepository(db).get(org.id) is not None


# --------------------------------------------------------------------------- #
# 3. R6.07 — wrong password is rejected and nothing is touched.               #
# --------------------------------------------------------------------------- #


async def test_self_delete_rejects_wrong_password() -> None:
    async with async_session() as db:
        user = await _insert_active_user(db)
        own = await ProjectRepository(db).create(
            name=f"own-{uuid.uuid4().hex[:6]}",
            owner_user_id=user.id,
            owner_org_id=None,
            created_by_user_id=user.id,
        )
        await db.commit()

        svc = create_auth_service(db, public_origin="http://localhost")
        with pytest.raises(InvalidCredentials):
            await svc.delete_account(user_id=user.id, password="wrong-password", remote_ip=None)

        u = await UserRepository(db).get_by_id(user.id)
        assert u is not None
        assert u.status is UserStatus.ACTIVE
        assert await ProjectRepository(db).get(own.id) is not None


# --------------------------------------------------------------------------- #
# 4. Solo OC Org (no other members) is reaped along with the account.         #
# --------------------------------------------------------------------------- #


async def test_self_delete_reaps_solo_oc_org() -> None:
    async with async_session() as db:
        user = await _insert_active_user(db)
        org = await OrgRepository(db).create(name=f"org-{uuid.uuid4().hex[:6]}", creator_user_id=user.id)
        await OrgMemberRepository(db).add(
            org_id=org.id, user_id=user.id, role=OrgMemberRole.OWNER, is_original_creator=True
        )
        proj = await ProjectRepository(db).create(
            name=f"op-{uuid.uuid4().hex[:6]}",
            owner_user_id=None,
            owner_org_id=org.id,
            created_by_user_id=user.id,
        )
        await ProjectMemberRepository(db).add(
            project_id=proj.id, user_id=user.id, role=ProjectMemberRole.OWNER
        )
        await db.commit()

        svc = create_auth_service(db, public_origin="http://localhost")
        counts = await svc.delete_account(user_id=user.id, password=_PASSWORD, remote_ip=None)
        await db.commit()

        assert counts["solo_orgs_deleted"] == 1

        # Org + its project soft-deleted (recoverable, not hard-deleted).
        assert await OrgRepository(db).get(org.id) is None
        assert await OrgRepository(db).get(org.id, include_deleted=True) is not None
        assert await ProjectRepository(db).get(proj.id) is None

        u = await UserRepository(db).get_by_id(user.id)
        assert u is not None
        assert u.status is UserStatus.DELETED
