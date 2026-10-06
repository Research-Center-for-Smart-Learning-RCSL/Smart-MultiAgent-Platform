"""Wiring tier -- `project_role_holders` against real Postgres ([R13.33]).

Spec: ``docs/tasks/2026-10-05-guest-sender-marking/spec.md`` §6. The unit tier pins
that the batch agrees with ``roles_for`` over fakes; this pins the two IN queries it
is built from against the real tables, so a wrong column or a missing filter cannot
hide behind a fake.
"""

from __future__ import annotations

import uuid

import pytest

from contexts.identity.infrastructure.repositories import UserRepository
from contexts.tenancy.domain.models import OrgMemberRole, ProjectMemberRole
from contexts.tenancy.infrastructure.repositories import (
    OrgMemberRepository,
    OrgRepository,
    ProjectMemberRepository,
    ProjectRepository,
)
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.db.session import async_session

pytestmark = pytest.mark.wiring


async def test_holders_are_project_members_and_parent_org_owners() -> None:
    async with async_session() as db:
        u = uuid.uuid4().hex[:8]
        users = UserRepository(db)
        creator, member, org_member, stranger, other_org_owner = [
            (await users.insert(email=f"prh-{n}-{u}@smap.test", password_hash="x" * 16)).id
            for n in ("creator", "member", "orgmember", "stranger", "otherowner")
        ]
        org = await OrgRepository(db).create(name=f"org-{u}", creator_user_id=creator)
        other_org = await OrgRepository(db).create(name=f"org2-{u}", creator_user_id=other_org_owner)
        org_members = OrgMemberRepository(db)
        await org_members.add(
            org_id=org.id, user_id=creator, role=OrgMemberRole.OWNER, is_original_creator=True
        )
        await org_members.add(org_id=org.id, user_id=org_member, role=OrgMemberRole.MEMBER)
        await org_members.add(
            org_id=other_org.id, user_id=other_org_owner, role=OrgMemberRole.OWNER, is_original_creator=True
        )
        project = await ProjectRepository(db).create(
            name=f"proj-{u}", owner_user_id=None, owner_org_id=org.id, created_by_user_id=creator
        )
        await ProjectMemberRepository(db).add(
            project_id=project.id, user_id=member, role=ProjectMemberRole.MEMBER
        )
        await db.commit()

        holders = await TenancyRoleResolver(db).project_role_holders(
            [creator, member, org_member, stranger, other_org_owner], project_id=project.id
        )

        # The creator holds no membership row: they hold the role by owning the
        # parent org. An owner of another org, a plain org member and a stranger
        # hold none here.
        assert holders == {creator, member}


async def test_the_owner_of_a_user_owned_project_holds_a_role() -> None:
    async with async_session() as db:
        u = uuid.uuid4().hex[:8]
        users = UserRepository(db)
        owner = (await users.insert(email=f"prh-owner-{u}@smap.test", password_hash="x" * 16)).id
        stranger = (await users.insert(email=f"prh-s-{u}@smap.test", password_hash="x" * 16)).id
        project = await ProjectRepository(db).create(
            name=f"proj-{u}", owner_user_id=owner, owner_org_id=None, created_by_user_id=owner
        )
        await db.commit()

        holders = await TenancyRoleResolver(db).project_role_holders([owner, stranger], project_id=project.id)

        assert holders == {owner}
