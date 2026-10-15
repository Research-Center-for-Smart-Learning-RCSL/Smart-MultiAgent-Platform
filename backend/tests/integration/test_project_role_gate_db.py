"""[R5.07] project-role resolution against a real PostgreSQL.

Dossier `docs/tasks/2026-10-13-agent-config-visibility-and-project-reads/spec.md`,
AC-6 and AC-8. The unit tier fakes the role resolver; what only real rows show is
whether `roles_for` and its batch form `project_role_project_ids` agree on every
ground for a project role, and whether a bare Org Member, the one caller the gate
exists to refuse, is refused by both.

Marked `db`: it needs the provisioned datastore of the `backend-db` CI job.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1.deps import has_project_role
from contexts.identity.infrastructure.tables import users as users_t
from contexts.tenancy.infrastructure import tables as ten_t
from contexts.tenancy.infrastructure.repositories import ProjectRepository
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.permissions import Principal, Role, Scope, holds_project_role

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class Scenario:
    org_id: uuid.UUID
    other_org_id: uuid.UUID
    org_project: uuid.UUID
    personal_project: uuid.UUID
    org_owner: uuid.UUID
    org_member: uuid.UUID
    project_member: uuid.UUID
    project_owner: uuid.UUID
    org_member_and_project_member: uuid.UUID
    other_org_member: uuid.UUID


def _principal(user_id: uuid.UUID, *, is_admin: bool = False) -> Principal:
    return Principal(user_id=user_id, is_admin=is_admin, email_verified=True)


@pytest.fixture
async def scenario(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[Scenario]:
    s = Scenario(
        org_id=uuid.uuid4(),
        other_org_id=uuid.uuid4(),
        org_project=uuid.uuid4(),
        personal_project=uuid.uuid4(),
        org_owner=uuid.uuid4(),
        org_member=uuid.uuid4(),
        project_member=uuid.uuid4(),
        project_owner=uuid.uuid4(),
        org_member_and_project_member=uuid.uuid4(),
        other_org_member=uuid.uuid4(),
    )
    users = [
        s.org_owner,
        s.org_member,
        s.project_member,
        s.project_owner,
        s.org_member_and_project_member,
        s.other_org_member,
    ]
    async with sessionmaker() as session:
        for uid in users:
            await session.execute(
                users_t.insert().values(id=uid, email=f"role-{uid}@test.invalid", password_hash="x")
            )
        for oid, creator in ((s.org_id, s.org_owner), (s.other_org_id, s.other_org_member)):
            await session.execute(
                ten_t.orgs.insert().values(id=oid, name=f"role-{oid}", creator_user_id=creator)
            )
        for oid, uid, role, oc in (
            (s.org_id, s.org_owner, "owner", True),
            (s.org_id, s.org_member, "member", False),
            (s.org_id, s.org_member_and_project_member, "member", False),
            (s.other_org_id, s.other_org_member, "owner", True),
        ):
            await session.execute(
                ten_t.org_members.insert().values(org_id=oid, user_id=uid, role=role, is_original_creator=oc)
            )
        await session.execute(
            ten_t.projects.insert().values(
                id=s.org_project,
                name="role-org-project",
                owner_org_id=s.org_id,
                created_by_user_id=s.org_owner,
            )
        )
        await session.execute(
            ten_t.projects.insert().values(
                id=s.personal_project,
                name="role-personal-project",
                owner_user_id=s.project_owner,
                created_by_user_id=s.project_owner,
            )
        )
        for uid, role in (
            (s.project_member, "member"),
            (s.project_owner, "owner"),
            (s.org_member_and_project_member, "member"),
        ):
            await session.execute(
                ten_t.project_members.insert().values(project_id=s.org_project, user_id=uid, role=role)
            )
        await session.commit()

    try:
        yield s
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(ten_t.projects.delete().where(ten_t.projects.c.id == s.personal_project))
            await cleanup.execute(ten_t.orgs.delete().where(ten_t.orgs.c.id.in_([s.org_id, s.other_org_id])))
            await cleanup.execute(users_t.delete().where(users_t.c.id.in_(users)))
            await cleanup.commit()


def _expected(s: Scenario) -> dict[uuid.UUID, set[uuid.UUID]]:
    """Which projects each user holds a project role in."""
    return {
        s.org_owner: {s.org_project},  # inherited PROJECT_OWNER (R5.03)
        s.org_member: set(),  # the bare Org Member: refused
        s.project_member: {s.org_project},
        s.project_owner: {s.org_project, s.personal_project},  # row, and owner of a user-owned project
        s.org_member_and_project_member: {s.org_project},
        s.other_org_member: set(),  # another tenant
    }


async def test_the_per_project_gate_and_its_batch_form_agree(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        resolver = TenancyRoleResolver(session)
        projects = [
            await ProjectRepository(session).get(pid, include_deleted=True)
            for pid in (scenario.org_project, scenario.personal_project)
        ]
        assert all(p is not None for p in projects)
        for user_id, expected in _expected(scenario).items():
            principal = _principal(user_id)
            per_project = {
                pid
                for pid in (scenario.org_project, scenario.personal_project)
                if holds_project_role(await resolver.roles_for(principal, Scope(project_id=pid)))
            }
            batch = await resolver.project_role_project_ids(principal, [p for p in projects if p])
            assert per_project == expected, user_id
            assert batch == expected, user_id


async def test_a_bare_org_member_resolves_to_org_member_and_is_refused(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """The room ACL still sees the role (`allow_org_members` rooms rely on it);
    the project-role gate does not count it."""
    async with sessionmaker() as session:
        principal = _principal(scenario.org_member)
        roles = await TenancyRoleResolver(session).roles_for(
            principal, Scope(project_id=scenario.org_project)
        )
        assert roles == frozenset({Role.ORG_MEMBER})
        assert (
            await has_project_role(db=session, principal=principal, project_id=scenario.org_project) is False
        )


async def test_admin_passes_the_helper_without_any_role(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        admin = _principal(uuid.uuid4(), is_admin=True)
        assert await has_project_role(db=session, principal=admin, project_id=scenario.org_project) is True
