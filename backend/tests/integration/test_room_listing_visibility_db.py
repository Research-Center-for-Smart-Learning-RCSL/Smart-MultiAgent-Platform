"""The R13.32 listing filters, executed against a real PostgreSQL (FU-5).

Stage 1 of the member-groups dossier shipped covered only by the unit tier, and
the unit tier cannot see any of what this module checks: the join from
`chatrooms` to `workspaces`, the two soft-delete predicates on it, the batch
guest lookup, or the role resolver's real queries. `backend/CLAUDE.md` is
explicit that unit-tier statements compile with `literal_binds` and therefore
prove only that the SQL text would work if pasted into psql.

The subject here is confidentiality, so the thing worth executing is not "does
the filter run" but "does the same principal, against real rows, get exactly the
rooms the open path would let them open".

Marked `db`: it needs the provisioned datastore of the `backend-db` CI job, not
the fake-backed `backend-integration` tier.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
    visible_room_ids,
)
from contexts.conversation.domain.errors import ForbiddenInRoom
from contexts.conversation.infrastructure import tables as conv_t
from contexts.conversation.infrastructure.repositories import ChatroomRepository
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.identity.infrastructure.tables import users as users_t
from contexts.tenancy.application.project_service import ProjectService
from contexts.tenancy.infrastructure import tables as ten_t
from shared_kernel.auth.permissions import Principal

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class Scenario:
    """One org, two projects, four rooms, four people with different standing."""

    org_id: uuid.UUID
    project_id: uuid.UUID
    sibling_project_id: uuid.UUID
    workspace_id: uuid.UUID
    sibling_workspace_id: uuid.UUID
    room_project: uuid.UUID
    room_org: uuid.UUID
    room_owners_only: uuid.UUID
    room_guest: uuid.UUID
    sibling_room: uuid.UUID
    org_owner: uuid.UUID
    org_member: uuid.UUID
    project_member: uuid.UUID
    guest: uuid.UUID


def _principal(user_id: uuid.UUID, *, is_admin: bool = False) -> Principal:
    return Principal(user_id=user_id, is_admin=is_admin, email_verified=True)


async def _add_user(session: AsyncSession, uid: uuid.UUID) -> None:
    await session.execute(users_t.insert().values(id=uid, email=f"vis-{uid}@test.invalid", password_hash="x"))


async def _add_room(
    session: AsyncSession,
    *,
    room_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
    creator: uuid.UUID,
    allow_org_members: bool = False,
    allow_project_members: bool = True,
    allow_project_owners_only: bool = False,
    allow_guest_links: bool = False,
) -> None:
    await session.execute(
        conv_t.chatrooms.insert().values(
            id=room_id,
            workspace_id=workspace_id,
            name=name,
            allow_org_members=allow_org_members,
            allow_project_members=allow_project_members,
            allow_project_owners_only=allow_project_owners_only,
            allow_guest_links=allow_guest_links,
            guest_token=f"tok-{room_id}",
            created_by_user_id=creator,
        )
    )


@pytest.fixture
async def scenario(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[Scenario]:
    s = Scenario(
        org_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        sibling_project_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        sibling_workspace_id=uuid.uuid4(),
        room_project=uuid.uuid4(),
        room_org=uuid.uuid4(),
        room_owners_only=uuid.uuid4(),
        room_guest=uuid.uuid4(),
        sibling_room=uuid.uuid4(),
        org_owner=uuid.uuid4(),
        org_member=uuid.uuid4(),
        project_member=uuid.uuid4(),
        guest=uuid.uuid4(),
    )
    async with sessionmaker() as session:
        for uid in (s.org_owner, s.org_member, s.project_member, s.guest):
            await _add_user(session, uid)

        await session.execute(
            ten_t.orgs.insert().values(id=s.org_id, name="vis-org", creator_user_id=s.org_owner)
        )
        await session.execute(
            ten_t.org_members.insert().values(
                org_id=s.org_id, user_id=s.org_owner, role="owner", is_original_creator=True
            )
        )
        await session.execute(
            ten_t.org_members.insert().values(
                org_id=s.org_id, user_id=s.org_member, role="member", is_original_creator=False
            )
        )

        for pid, name in ((s.project_id, "vis-p1"), (s.sibling_project_id, "vis-p2")):
            await session.execute(
                ten_t.projects.insert().values(
                    id=pid, name=name, owner_org_id=s.org_id, created_by_user_id=s.org_owner
                )
            )
        # The user invited straight into a project without being invited to the
        # org — the case D-6 fixed. Deliberately holds no `org_members` row.
        await session.execute(
            ten_t.project_members.insert().values(
                project_id=s.project_id, user_id=s.project_member, role="member"
            )
        )

        for wid, pid, name in (
            (s.workspace_id, s.project_id, "vis-w1"),
            (s.sibling_workspace_id, s.sibling_project_id, "vis-w2"),
        ):
            await session.execute(conv_t.workspaces.insert().values(id=wid, project_id=pid, name=name))

        await _add_room(
            session,
            room_id=s.room_project,
            workspace_id=s.workspace_id,
            name="project-only",
            creator=s.org_owner,
        )
        await _add_room(
            session,
            room_id=s.room_org,
            workspace_id=s.workspace_id,
            name="org-open",
            creator=s.org_owner,
            allow_org_members=True,
            allow_project_members=False,
        )
        await _add_room(
            session,
            room_id=s.room_owners_only,
            workspace_id=s.workspace_id,
            name="owners-only",
            creator=s.org_owner,
            allow_project_owners_only=True,
        )
        await _add_room(
            session,
            room_id=s.room_guest,
            workspace_id=s.workspace_id,
            name="guest-link",
            creator=s.org_owner,
            allow_project_members=False,
            allow_guest_links=True,
        )
        await _add_room(
            session,
            room_id=s.sibling_room,
            workspace_id=s.sibling_workspace_id,
            name="sibling-project-only",
            creator=s.org_owner,
        )
        await session.execute(
            conv_t.chatroom_guests.insert().values(
                chatroom_id=s.room_guest, user_id=s.guest, joined_via_token=f"tok-{s.room_guest}"
            )
        )
        await session.commit()

    try:
        yield s
    finally:
        async with sessionmaker() as cleanup:
            # Deleting the org cascades projects -> workspaces -> chatrooms ->
            # chatroom_guests; users must follow, since `orgs.creator_user_id`
            # is ON DELETE RESTRICT.
            await cleanup.execute(ten_t.orgs.delete().where(ten_t.orgs.c.id == s.org_id))
            await cleanup.execute(
                users_t.delete().where(
                    users_t.c.id.in_([s.org_owner, s.org_member, s.project_member, s.guest])
                )
            )
            await cleanup.commit()


async def _visible_names(session: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID) -> set[str]:
    rooms = await ConversationFacade(session).visible_rooms_in_workspace(
        principal=_principal(user_id), workspace_id=workspace_id
    )
    return {r.name for r in rooms}


# --------------------------------------------------------------------------- #
# AC-1 — the chatroom listing, against real rows
# --------------------------------------------------------------------------- #


async def test_org_member_sees_only_the_org_open_room(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """The defect this whole change exists for.

    Before, this caller received every room in the workspace — names, flags and
    observer status — while being refused on open for all but one.
    """
    async with sessionmaker() as session:
        assert await _visible_names(
            session, user_id=scenario.org_member, workspace_id=scenario.workspace_id
        ) == {"org-open"}


async def test_project_member_sees_the_project_room_and_not_the_org_room(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """A project member who is not an org member holds no ORG_MEMBER role, so an
    `allow_org_members`-only room is not theirs either."""
    async with sessionmaker() as session:
        assert await _visible_names(
            session, user_id=scenario.project_member, workspace_id=scenario.workspace_id
        ) == {"project-only"}


async def test_org_owner_sees_every_room_including_owners_only(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """R8.08 / R5.03: an org owner moderates every project of the org."""
    async with sessionmaker() as session:
        assert await _visible_names(
            session, user_id=scenario.org_owner, workspace_id=scenario.workspace_id
        ) == {"project-only", "org-open", "owners-only", "guest-link"}


async def test_a_guest_sees_only_the_room_their_guest_row_names(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """Exercises the new batch `guest_room_ids` query for real. The guest holds no
    org or project role at all."""
    async with sessionmaker() as session:
        assert await _visible_names(session, user_id=scenario.guest, workspace_id=scenario.workspace_id) == {
            "guest-link"
        }


async def test_the_listing_agrees_with_the_open_path_room_for_room(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """The property the whole design rests on, checked end to end against the DB.

    For every principal and every room in the workspace, being listed and being
    openable must be the same answer. The unit tier asserts this over a synthetic
    matrix; here both sides run their real queries.
    """
    all_rooms = {
        "project-only": scenario.room_project,
        "org-open": scenario.room_org,
        "owners-only": scenario.room_owners_only,
        "guest-link": scenario.room_guest,
    }
    for user_id in (scenario.org_owner, scenario.org_member, scenario.project_member, scenario.guest):
        async with sessionmaker() as session:
            listed = await _visible_names(session, user_id=user_id, workspace_id=scenario.workspace_id)
            openable: set[str] = set()
            for name, room_id in all_rooms.items():
                access = await resolve_room_access(
                    session, principal=_principal(user_id), chatroom_id=room_id
                )
                try:
                    ensure_can_read(access, is_admin=False)
                except ForbiddenInRoom:
                    continue
                openable.add(name)
            assert listed == openable, f"listing and open path disagree for {user_id}"


# --------------------------------------------------------------------------- #
# AC-3 / AC-4 — the workspace and project listings
# --------------------------------------------------------------------------- #


async def test_workspace_and_project_listings_drop_the_sibling(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """The sibling project holds only a project-members room, and this caller is a
    member of neither project — so neither the project nor its workspace is theirs
    to see, while the first project stays reachable through its org-open room."""
    async with sessionmaker() as session:
        facade = ConversationFacade(session)
        principal = _principal(scenario.org_member)

        assert await facade.project_ids_with_visible_room(
            principal=principal,
            project_ids=[scenario.project_id, scenario.sibling_project_id],
        ) == {scenario.project_id}

        assert await facade.workspace_ids_with_visible_room(
            principal=principal,
            workspace_ids=[scenario.workspace_id, scenario.sibling_workspace_id],
        ) == {scenario.workspace_id}


async def test_an_invited_project_member_is_a_candidate_and_needs_no_room(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """D-6, proven against real rows: membership alone puts the project in the
    list, with no `org_members` row anywhere and no room lookup involved."""
    async with sessionmaker() as session:
        candidates = await ProjectService(session).list_candidates_for_user(scenario.project_member)

    assert [p.id for p in candidates.projects] == [scenario.project_id]
    assert candidates.directly_visible_ids == {scenario.project_id}


async def test_an_org_member_project_is_a_candidate_but_undecided(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        candidates = await ProjectService(session).list_candidates_for_user(scenario.org_member)

    assert {p.id for p in candidates.projects} == {scenario.project_id, scenario.sibling_project_id}
    assert candidates.directly_visible_ids == set()


async def test_an_org_owner_needs_no_room_lookup_for_any_project(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        candidates = await ProjectService(session).list_candidates_for_user(scenario.org_owner)

    assert candidates.directly_visible_ids == {scenario.project_id, scenario.sibling_project_id}


# --------------------------------------------------------------------------- #
# The candidate query itself — the SQL no unit test can execute
# --------------------------------------------------------------------------- #


async def test_a_soft_deleted_room_is_not_a_candidate(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        await session.execute(
            conv_t.chatrooms.update()
            .where(conv_t.chatrooms.c.id == scenario.room_org)
            .values(deleted_at=sa.func.now())
        )
        await session.commit()

    async with sessionmaker() as session:
        assert (
            await _visible_names(session, user_id=scenario.org_member, workspace_id=scenario.workspace_id)
            == set()
        )


async def test_a_soft_deleted_workspace_takes_its_rooms_with_it(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """The second `deleted_at` predicate, on the joined table. A live room in a
    deleted workspace must not be a candidate."""
    async with sessionmaker() as session:
        await session.execute(
            conv_t.workspaces.update()
            .where(conv_t.workspaces.c.id == scenario.workspace_id)
            .values(deleted_at=sa.func.now())
        )
        await session.commit()

    async with sessionmaker() as session:
        rooms, truncated = await ChatroomRepository(session).list_candidates(
            project_ids=[scenario.project_id], limit=100
        )
        assert rooms == []
        assert truncated is False


async def test_candidates_carry_their_parent_project_and_report_truncation(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """The labelled join column and the limit+1 probe, both against real rows."""
    async with sessionmaker() as session:
        repo = ChatroomRepository(session)

        rooms, truncated = await repo.list_candidates(workspace_ids=[scenario.workspace_id], limit=100)
        assert truncated is False
        assert len(rooms) == 4
        assert {project_id for project_id, _ in rooms} == {scenario.project_id}

        rooms, truncated = await repo.list_candidates(workspace_ids=[scenario.workspace_id], limit=2)
        assert truncated is True
        assert len(rooms) == 2


async def test_the_project_scope_spans_workspaces_and_keeps_projects_apart(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        rooms, _ = await ChatroomRepository(session).list_candidates(
            project_ids=[scenario.project_id, scenario.sibling_project_id], limit=100
        )

    by_project: dict[uuid.UUID, set[str]] = {}
    for project_id, room in rooms:
        by_project.setdefault(project_id, set()).add(room.name)
    assert by_project[scenario.sibling_project_id] == {"sibling-project-only"}
    assert len(by_project[scenario.project_id]) == 4


async def test_admin_sees_every_room_without_a_role_anywhere(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    """Admin holds no org or project row in this scenario at all."""
    admin_id = uuid.uuid4()
    async with sessionmaker() as session:
        rooms, _ = await ChatroomRepository(session).list_candidates(
            workspace_ids=[scenario.workspace_id], limit=100
        )
        visible = await visible_room_ids(session, principal=_principal(admin_id, is_admin=True), rooms=rooms)
    assert visible == {room.id for _, room in rooms}
