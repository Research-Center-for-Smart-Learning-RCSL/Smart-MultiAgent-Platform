"""`moderated_project_ids` must agree with `roles_for` on every project.

Found by a post-close `/code-review` of
`docs/tasks/2026-08-20-orchestration-room-scoped-reads/spec.md` (FU-10): the
client decided ownership by scanning `project_members` for its own `owner` row,
which is not what any server gate decides. Ownership is inherited (R5.03), so an
Org Owner moderates every project of that org while holding no membership row,
and the workflow guard this dossier added therefore redirected those people off
pages the server was admitting them to.

The fix serializes the server's own verdict. These tests pin that the batch form
and the per-project form give the same answer, because the moment they diverge
the bug comes back in the other direction: a project that lists as yours whose
owner-only surfaces refuse you.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from contexts.conversation.application.access import is_moderator_roles
from contexts.tenancy.domain.models import Project
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.permissions import Principal, Scope

_NOW = datetime(2026, 8, 21, tzinfo=UTC)


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


class _Fixture:
    """The three tables the resolver reads, as in-memory sets."""

    def __init__(
        self,
        *,
        projects: list[Project],
        owned_orgs: set[uuid.UUID],
        owner_memberships: set[uuid.UUID],
        member_memberships: set[uuid.UUID],
        org_memberships: set[uuid.UUID],
    ) -> None:
        self.projects = {p.id: p for p in projects}
        self.owned_orgs = owned_orgs
        self.owner_memberships = owner_memberships
        self.member_memberships = member_memberships
        self.org_memberships = org_memberships

    def install(self, resolver: TenancyRoleResolver, user_id: uuid.UUID) -> None:
        from contexts.tenancy.domain.models import OrgMember, OrgMemberRole, ProjectMember, ProjectMemberRole

        async def get_project(pid: uuid.UUID, *, include_deleted: bool = False) -> Project | None:
            return self.projects.get(pid)

        async def get_org_member(*, org_id: uuid.UUID, user_id: uuid.UUID) -> OrgMember | None:
            if org_id in self.owned_orgs:
                return OrgMember(
                    org_id=org_id,
                    user_id=user_id,
                    role=OrgMemberRole.OWNER,
                    is_original_creator=False,
                    joined_at=_NOW,
                )
            if org_id in self.org_memberships:
                return OrgMember(
                    org_id=org_id,
                    user_id=user_id,
                    role=OrgMemberRole.MEMBER,
                    is_original_creator=False,
                    joined_at=_NOW,
                )
            return None

        async def get_project_member(*, project_id: uuid.UUID, user_id: uuid.UUID) -> ProjectMember | None:
            if project_id in self.owner_memberships:
                return ProjectMember(
                    project_id=project_id,
                    user_id=user_id,
                    role=ProjectMemberRole.OWNER,
                    joined_at=_NOW,
                )
            if project_id in self.member_memberships:
                return ProjectMember(
                    project_id=project_id,
                    user_id=user_id,
                    role=ProjectMemberRole.MEMBER,
                    joined_at=_NOW,
                )
            return None

        async def owned_org_ids(_uid: uuid.UUID) -> set[uuid.UUID]:
            return set(self.owned_orgs)

        async def owned_project_ids_for_user(_uid: uuid.UUID) -> set[uuid.UUID]:
            return set(self.owner_memberships)

        resolver._projects.get = get_project  # type: ignore[method-assign]
        resolver._org_members.get = get_org_member  # type: ignore[method-assign]
        resolver._org_members.owned_org_ids = owned_org_ids  # type: ignore[method-assign]
        resolver._project_members.get = get_project_member  # type: ignore[method-assign]
        resolver._project_members.owned_project_ids_for_user = (  # type: ignore[method-assign]
            owned_project_ids_for_user
        )


async def _both_forms(fixture: _Fixture, user_id: uuid.UUID) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    """(batch answer, per-project answer) over the same fixture."""
    principal = Principal(user_id=user_id, is_admin=False, email_verified=True)
    resolver = TenancyRoleResolver(db=None)  # type: ignore[arg-type]
    fixture.install(resolver, user_id)
    projects = list(fixture.projects.values())

    batch = await resolver.moderated_project_ids(principal, projects)
    one_by_one = {
        p.id
        for p in projects
        if is_moderator_roles(await resolver.roles_for(principal, Scope(project_id=p.id)))
    }
    return batch, one_by_one


async def test_org_owner_moderates_without_a_membership_row() -> None:
    # The exact case the review found. Before the fix the client answered "no"
    # here while every server gate answered "yes".
    org = uuid.uuid4()
    project = _project(owner_org_id=org)
    user = uuid.uuid4()
    fixture = _Fixture(
        projects=[project],
        owned_orgs={org},
        owner_memberships=set(),
        member_memberships=set(),
        org_memberships=set(),
    )

    batch, one_by_one = await _both_forms(fixture, user)

    assert batch == {project.id}
    assert batch == one_by_one


async def test_plain_project_member_does_not_moderate() -> None:
    org = uuid.uuid4()
    project = _project(owner_org_id=org)
    user = uuid.uuid4()
    fixture = _Fixture(
        projects=[project],
        owned_orgs=set(),
        owner_memberships=set(),
        member_memberships={project.id},
        org_memberships={org},
    )

    batch, one_by_one = await _both_forms(fixture, user)

    assert batch == set()
    assert batch == one_by_one


@pytest.mark.parametrize(
    "ground",
    ["owned_org", "owner_membership", "user_owned"],
)
async def test_each_ground_for_ownership_is_honoured(ground: str) -> None:
    org = uuid.uuid4()
    user = uuid.uuid4()
    project = _project(owner_user_id=user) if ground == "user_owned" else _project(owner_org_id=org)
    fixture = _Fixture(
        projects=[project],
        owned_orgs={org} if ground == "owned_org" else set(),
        owner_memberships={project.id} if ground == "owner_membership" else set(),
        member_memberships=set(),
        org_memberships=set(),
    )

    batch, one_by_one = await _both_forms(fixture, user)

    assert batch == {project.id}
    assert batch == one_by_one


async def test_the_two_forms_agree_over_a_mixed_page() -> None:
    # SEC/UX: the listing's answer and the gate's answer must be the same set,
    # or a project lists as yours while its owner-only surfaces refuse you.
    owned_org, other_org = uuid.uuid4(), uuid.uuid4()
    user = uuid.uuid4()
    inherited = _project(owner_org_id=owned_org)
    explicit_owner = _project(owner_org_id=other_org)
    plain_member = _project(owner_org_id=other_org)
    self_owned = _project(owner_user_id=user)
    stranger = _project(owner_user_id=uuid.uuid4())
    fixture = _Fixture(
        projects=[inherited, explicit_owner, plain_member, self_owned, stranger],
        owned_orgs={owned_org},
        owner_memberships={explicit_owner.id},
        member_memberships={plain_member.id},
        org_memberships={other_org},
    )

    batch, one_by_one = await _both_forms(fixture, user)

    assert batch == {inherited.id, explicit_owner.id, self_owned.id}
    assert batch == one_by_one


async def test_no_projects_asks_the_database_nothing() -> None:
    resolver = TenancyRoleResolver(db=None)  # type: ignore[arg-type]
    principal = Principal(user_id=uuid.uuid4(), is_admin=False, email_verified=True)

    # No stubs installed: touching either repository would raise.
    assert await resolver.moderated_project_ids(principal, []) == set()
