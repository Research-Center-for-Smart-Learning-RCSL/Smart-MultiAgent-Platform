"""Member Groups against a real PostgreSQL (§13.2a, AC-7 to AC-17).

What only an executed test can show here: the partial unique index on
`lower(name)`, the cascades from a deleted project, that removing a project
member really drops their group rows, and that the room ACL's
bindings-intersect-live-groups shape makes a deleted group grant nothing.

Marked `db`: needs the provisioned datastore of the `backend-db` CI job.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from contexts.conversation.application.access import (
    ensure_can_read,
    resolve_room_access,
)
from contexts.conversation.domain.errors import ForbiddenInRoom
from contexts.conversation.infrastructure import tables as conv_t
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.identity.infrastructure.tables import users as users_t
from contexts.tenancy.infrastructure import tables as ten_t
from contexts.tenancy.infrastructure.repositories import MemberGroupRepository
from shared_kernel.auth.permissions import Principal

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class GroupScenario:
    org_id: uuid.UUID
    project_id: uuid.UUID
    workspace_id: uuid.UUID
    owner: uuid.UUID
    member_a: uuid.UUID
    member_b: uuid.UUID
    ungrouped: uuid.UUID
    group_a: uuid.UUID
    group_b: uuid.UUID
    room_a: uuid.UUID
    room_b: uuid.UUID
    room_project: uuid.UUID
    room_owners_only: uuid.UUID


def _principal(user_id: uuid.UUID) -> Principal:
    return Principal(user_id=user_id, is_admin=False, email_verified=True)


async def _room(
    session: AsyncSession,
    *,
    room_id: uuid.UUID,
    workspace_id: uuid.UUID,
    name: str,
    creator: uuid.UUID,
    allow_project_members: bool = True,
    allow_member_groups: bool = False,
    allow_project_owners_only: bool = False,
) -> None:
    await session.execute(
        conv_t.chatrooms.insert().values(
            id=room_id,
            workspace_id=workspace_id,
            name=name,
            allow_org_members=False,
            allow_project_members=allow_project_members,
            allow_project_owners_only=allow_project_owners_only,
            allow_guest_links=False,
            allow_member_groups=allow_member_groups,
            guest_token=f"tok-{room_id}",
            created_by_user_id=creator,
        )
    )


@pytest.fixture
async def scenario(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[GroupScenario]:
    s = GroupScenario(
        org_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        owner=uuid.uuid4(),
        member_a=uuid.uuid4(),
        member_b=uuid.uuid4(),
        ungrouped=uuid.uuid4(),
        group_a=uuid.uuid4(),
        group_b=uuid.uuid4(),
        room_a=uuid.uuid4(),
        room_b=uuid.uuid4(),
        room_project=uuid.uuid4(),
        room_owners_only=uuid.uuid4(),
    )
    async with sessionmaker() as session:
        for uid in (s.owner, s.member_a, s.member_b, s.ungrouped):
            await session.execute(
                users_t.insert().values(id=uid, email=f"mg-{uid}@test.invalid", password_hash="x")
            )
        await session.execute(ten_t.orgs.insert().values(id=s.org_id, name="mg-org", creator_user_id=s.owner))
        await session.execute(
            ten_t.org_members.insert().values(
                org_id=s.org_id, user_id=s.owner, role="owner", is_original_creator=True
            )
        )
        await session.execute(
            ten_t.projects.insert().values(
                id=s.project_id, name="mg-p", owner_org_id=s.org_id, created_by_user_id=s.owner
            )
        )
        for uid in (s.member_a, s.member_b, s.ungrouped):
            await session.execute(
                ten_t.project_members.insert().values(project_id=s.project_id, user_id=uid, role="member")
            )
        await session.execute(
            conv_t.workspaces.insert().values(id=s.workspace_id, project_id=s.project_id, name="mg-w")
        )

        for gid, name, uid in ((s.group_a, "team-a", s.member_a), (s.group_b, "team-b", s.member_b)):
            await session.execute(
                ten_t.member_groups.insert().values(
                    id=gid, project_id=s.project_id, name=name, created_by_user_id=s.owner
                )
            )
            await session.execute(
                ten_t.member_group_members.insert().values(member_group_id=gid, user_id=uid)
            )

        await _room(
            session,
            room_id=s.room_a,
            workspace_id=s.workspace_id,
            name="a-only",
            creator=s.owner,
            allow_project_members=False,
            allow_member_groups=True,
        )
        await _room(
            session,
            room_id=s.room_b,
            workspace_id=s.workspace_id,
            name="b-only",
            creator=s.owner,
            allow_project_members=False,
            allow_member_groups=True,
        )
        await _room(
            session,
            room_id=s.room_project,
            workspace_id=s.workspace_id,
            name="everyone",
            creator=s.owner,
        )
        await _room(
            session,
            room_id=s.room_owners_only,
            workspace_id=s.workspace_id,
            name="owners-only",
            creator=s.owner,
            allow_project_members=False,
            allow_project_owners_only=True,
            allow_member_groups=True,
        )
        for room_id, gid in (
            (s.room_a, s.group_a),
            (s.room_b, s.group_b),
            # A binding on an owners-only room: inert, and the ACL must keep it so.
            (s.room_owners_only, s.group_a),
        ):
            await session.execute(
                conv_t.chatroom_member_groups.insert().values(chatroom_id=room_id, member_group_id=gid)
            )
        await session.commit()

    try:
        yield s
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(ten_t.orgs.delete().where(ten_t.orgs.c.id == s.org_id))
            await cleanup.execute(
                users_t.delete().where(users_t.c.id.in_([s.owner, s.member_a, s.member_b, s.ungrouped]))
            )
            await cleanup.commit()


async def _visible(session: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID) -> set[str]:
    rooms = await ConversationFacade(session).visible_rooms_in_workspace(
        principal=_principal(user_id), workspace_id=workspace_id
    )
    return {r.name for r in rooms}


async def _can_open(session: AsyncSession, *, user_id: uuid.UUID, room_id: uuid.UUID) -> bool:
    access = await resolve_room_access(session, principal=_principal(user_id), chatroom_id=room_id)
    try:
        ensure_can_read(access, is_admin=False)
    except ForbiddenInRoom:
        return False
    return True


# --------------------------------------------------------------------------- #
# AC-8, AC-9, AC-10 — the tier itself
# --------------------------------------------------------------------------- #


async def test_each_group_sees_its_own_room_and_not_the_others(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """AC-9, the whole point: A cannot see B's room, in the listing or on open."""
    async with sessionmaker() as session:
        assert await _visible(session, user_id=scenario.member_a, workspace_id=scenario.workspace_id) == {
            "a-only",
            "everyone",
        }
        assert await _visible(session, user_id=scenario.member_b, workspace_id=scenario.workspace_id) == {
            "b-only",
            "everyone",
        }
        assert await _can_open(session, user_id=scenario.member_a, room_id=scenario.room_b) is False
        assert await _can_open(session, user_id=scenario.member_b, room_id=scenario.room_a) is False


async def test_a_project_member_in_no_group_sees_only_the_project_room(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    async with sessionmaker() as session:
        assert await _visible(session, user_id=scenario.ungrouped, workspace_id=scenario.workspace_id) == {
            "everyone"
        }


async def test_an_org_owner_still_reaches_every_room(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """AC-10 — R8.08 inheritance is not narrowed by grouping."""
    async with sessionmaker() as session:
        assert await _visible(session, user_id=scenario.owner, workspace_id=scenario.workspace_id) == {
            "a-only",
            "b-only",
            "everyone",
            "owners-only",
        }


async def test_an_owners_only_room_ignores_its_binding(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """AC-14 — `allow_project_owners_only` stays exclusive against a live binding."""
    async with sessionmaker() as session:
        assert await _can_open(session, user_id=scenario.member_a, room_id=scenario.room_owners_only) is False


# --------------------------------------------------------------------------- #
# AC-12, AC-14 — revocation
# --------------------------------------------------------------------------- #


async def test_removing_a_user_from_the_group_revokes_the_room(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """AC-12's mechanism.

    The chatroom WebSocket's mid-socket re-auth re-runs exactly this call
    (`app/api/ws/chatroom.py`), so a revocation that lands here lands on the live
    socket at the next window. Asserted at `resolve_room_access` rather than
    through a socket because the socket's own loop is unchanged code.
    """
    async with sessionmaker() as session:
        assert await _can_open(session, user_id=scenario.member_a, room_id=scenario.room_a) is True

    async with sessionmaker() as session:
        await MemberGroupRepository(session).remove_member(
            group_id=scenario.group_a, user_id=scenario.member_a
        )
        await session.commit()

    async with sessionmaker() as session:
        assert await _can_open(session, user_id=scenario.member_a, room_id=scenario.room_a) is False


async def test_a_soft_deleted_group_grants_nothing(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """AC-14. The binding row survives the delete on purpose; it is the
    intersection with the caller's *live* groups that makes it inert."""
    async with sessionmaker() as session:
        assert await MemberGroupRepository(session).soft_delete(scenario.group_a) is True
        await session.commit()

    async with sessionmaker() as session:
        assert await _can_open(session, user_id=scenario.member_a, room_id=scenario.room_a) is False
        # The binding is still there — inert, not cleaned up.
        bound = (
            await session.execute(
                sa.select(conv_t.chatroom_member_groups.c.member_group_id).where(
                    conv_t.chatroom_member_groups.c.chatroom_id == scenario.room_a
                )
            )
        ).all()
        assert [r.member_group_id for r in bound] == [scenario.group_a]


async def test_leaving_the_project_drops_the_group_membership(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """R13.28. Without this the ex-member keeps reading every room bound to a
    group they were in — the ACL asks only whether the group row still says yes."""
    async with sessionmaker() as session:
        removed = await MemberGroupRepository(session).remove_user_from_project_groups(
            user_id=scenario.member_a, project_id=scenario.project_id
        )
        await session.commit()
    assert removed == 1

    async with sessionmaker() as session:
        assert await _can_open(session, user_id=scenario.member_a, room_id=scenario.room_a) is False


async def test_leaving_one_project_does_not_empty_groups_in_another(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    async with sessionmaker() as session:
        removed = await MemberGroupRepository(session).remove_user_from_project_groups(
            user_id=scenario.member_a, project_id=uuid.uuid4()
        )
        await session.commit()
    assert removed == 0

    async with sessionmaker() as session:
        assert await _can_open(session, user_id=scenario.member_a, room_id=scenario.room_a) is True


# --------------------------------------------------------------------------- #
# AC-7, AC-17 — the schema
# --------------------------------------------------------------------------- #


async def test_a_duplicate_live_name_is_rejected_case_insensitively(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """AC-7 — the partial unique index on `lower(name)`. Not visible to the unit
    tier at all: it is an index, and the unit tier never creates one."""
    async with sessionmaker() as session:
        duplicate = ten_t.member_groups.insert().values(
            project_id=scenario.project_id,
            name="TEAM-A",
            created_by_user_id=scenario.owner,
        )
        with pytest.raises(IntegrityError):
            await session.execute(duplicate)


async def test_the_same_name_is_free_in_another_project(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    other_project = uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            ten_t.projects.insert().values(
                id=other_project,
                name="mg-p2",
                owner_org_id=scenario.org_id,
                created_by_user_id=scenario.owner,
            )
        )
        await session.execute(
            ten_t.member_groups.insert().values(
                project_id=other_project, name="team-a", created_by_user_id=scenario.owner
            )
        )
        await session.commit()


async def test_a_name_is_free_again_after_a_soft_delete(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """The index is partial on `deleted_at IS NULL`, so re-creating a deleted
    group's name is not a collision."""
    async with sessionmaker() as session:
        await MemberGroupRepository(session).soft_delete(scenario.group_a)
        await session.execute(
            ten_t.member_groups.insert().values(
                project_id=scenario.project_id, name="team-a", created_by_user_id=scenario.owner
            )
        )
        await session.commit()


async def test_deleting_the_project_cascades_groups_bindings_and_memberships(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """AC-17. Hard delete, not the soft delete the API performs — the point is
    that the FKs are wired so nothing is orphaned when a row really goes."""
    async with sessionmaker() as session:
        await session.execute(ten_t.projects.delete().where(ten_t.projects.c.id == scenario.project_id))
        await session.commit()

    async with sessionmaker() as session:
        for table, column, value in (
            (ten_t.member_groups, ten_t.member_groups.c.project_id, scenario.project_id),
            (
                ten_t.member_group_members,
                ten_t.member_group_members.c.member_group_id,
                scenario.group_a,
            ),
            (
                conv_t.chatroom_member_groups,
                conv_t.chatroom_member_groups.c.member_group_id,
                scenario.group_a,
            ),
        ):
            rows = (await session.execute(table.select().where(column == value))).all()
            assert rows == [], f"{table.name} was not cascaded"


async def test_hard_deleting_a_user_does_not_abort_on_a_group_row(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """The GDPR-erasure path. `member_groups.created_by_user_id` is SET NULL and
    `member_group_members.user_id` is CASCADE, so a raw DELETE FROM users
    succeeds rather than tripping over a table the admin never named."""
    async with sessionmaker() as session:
        await session.execute(users_t.delete().where(users_t.c.id == scenario.member_a))
        await session.commit()

    async with sessionmaker() as session:
        rows = (
            await session.execute(
                ten_t.member_group_members.select().where(
                    ten_t.member_group_members.c.user_id == scenario.member_a
                )
            )
        ).all()
        assert rows == []


# --------------------------------------------------------------------------- #
# AC-15 — a project that uses no groups is unchanged
# --------------------------------------------------------------------------- #


async def test_a_room_with_the_tier_off_ignores_its_bindings(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    """R13.29 — bindings on a room whose flag is off grant nothing."""
    async with sessionmaker() as session:
        await session.execute(
            conv_t.chatrooms.update()
            .where(conv_t.chatrooms.c.id == scenario.room_a)
            .values(allow_member_groups=False)
        )
        await session.commit()

    async with sessionmaker() as session:
        assert await _can_open(session, user_id=scenario.member_a, room_id=scenario.room_a) is False


async def test_the_tier_on_with_nothing_bound_admits_nobody(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: GroupScenario
) -> None:
    async with sessionmaker() as session:
        await session.execute(
            conv_t.chatroom_member_groups.delete().where(
                conv_t.chatroom_member_groups.c.chatroom_id == scenario.room_a
            )
        )
        await session.commit()

    async with sessionmaker() as session:
        assert await _can_open(session, user_id=scenario.member_a, room_id=scenario.room_a) is False
        assert await _can_open(session, user_id=scenario.owner, room_id=scenario.room_a) is True
