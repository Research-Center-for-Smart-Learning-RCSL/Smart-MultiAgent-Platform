"""R15.24 against real rows, real roles and real FKs.

Dossier `docs/tasks/2026-08-20-orchestration-room-scoped-reads/spec.md` §12:
AC-1 and AC-4 asked for a real owners-only room and a real non-member; AC-5
asked for the room to actually be deleted so the `ON DELETE SET NULL` FK — not a
Python branch — produces the room-less record.

D-2: this module also carries AC-8's substitute. The regression this change is
most likely to cause is an ordinary room member losing the in-room approval card,
whose data path is `getApproval` -> this route
(`frontend/src/slices/conversation/composables/useChatroomSocket.ts:138`). The
approved plan verified that in a browser; the user chose a real-stack backend
test plus a frontend unit test instead, so `test_ac8_*` below drives the exact
call the card makes, as the exact principal that makes it.

Marked `db`: real datastore, not the fake-backed `backend-integration` tier.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import orchestration
from app.api.v1.deps import PaginationParams
from contexts.agents.infrastructure import tables as agent_t
from contexts.conversation.infrastructure import tables as conv_t
from contexts.identity.infrastructure.tables import users as users_t
from contexts.keys.infrastructure import tables as key_t
from contexts.orchestration.infrastructure import tables as orch_t
from contexts.tenancy.infrastructure import tables as ten_t
from contexts.tenancy.interfaces.role_resolver import TenancyRoleResolver
from shared_kernel.auth.permissions import Principal

pytestmark = pytest.mark.db


@dataclass(frozen=True)
class Scenario:
    org_id: uuid.UUID
    project_id: uuid.UUID
    workspace_id: uuid.UUID
    open_room: uuid.UUID
    owners_room: uuid.UUID
    owner: uuid.UUID
    member: uuid.UUID
    outsider: uuid.UUID
    agent_id: uuid.UUID
    run_id: uuid.UUID
    approval_open: uuid.UUID
    approval_owners: uuid.UUID
    approval_roomless: uuid.UUID
    root_instance: uuid.UUID
    instance_open: uuid.UUID
    instance_owners: uuid.UUID


def _principal(user_id: uuid.UUID) -> Principal:
    return Principal(user_id=user_id, is_admin=False, email_verified=True)


@pytest.fixture
async def scenario(sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncIterator[Scenario]:
    s = Scenario(
        org_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        open_room=uuid.uuid4(),
        owners_room=uuid.uuid4(),
        owner=uuid.uuid4(),
        member=uuid.uuid4(),
        outsider=uuid.uuid4(),
        agent_id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        approval_open=uuid.uuid4(),
        approval_owners=uuid.uuid4(),
        approval_roomless=uuid.uuid4(),
        root_instance=uuid.uuid4(),
        instance_open=uuid.uuid4(),
        instance_owners=uuid.uuid4(),
    )
    key_group_id = uuid.uuid4()
    async with sessionmaker() as session:
        for uid in (s.owner, s.member, s.outsider):
            await session.execute(
                users_t.insert().values(id=uid, email=f"orr-{uid}@test.invalid", password_hash="x")
            )
        await session.execute(
            ten_t.orgs.insert().values(id=s.org_id, name="orr-org", creator_user_id=s.owner)
        )
        await session.execute(
            ten_t.org_members.insert().values(
                org_id=s.org_id, user_id=s.owner, role="owner", is_original_creator=True
            )
        )
        await session.execute(
            ten_t.projects.insert().values(
                id=s.project_id, name="orr-p", owner_org_id=s.org_id, created_by_user_id=s.owner
            )
        )
        await session.execute(
            ten_t.project_members.insert().values(project_id=s.project_id, user_id=s.member, role="member")
        )
        await session.execute(
            conv_t.workspaces.insert().values(id=s.workspace_id, project_id=s.project_id, name="orr-w")
        )
        for room_id, owners_only, name in (
            (s.open_room, False, "orr-open"),
            (s.owners_room, True, "orr-owners"),
        ):
            await session.execute(
                conv_t.chatrooms.insert().values(
                    id=room_id,
                    workspace_id=s.workspace_id,
                    name=name,
                    allow_org_members=False,
                    allow_project_members=not owners_only,
                    allow_project_owners_only=owners_only,
                    allow_guest_links=False,
                    guest_token=f"tok-{room_id}",
                    created_by_user_id=s.owner,
                )
            )

        await session.execute(
            key_t.key_groups.insert().values(id=key_group_id, project_id=s.project_id, name="orr-kg")
        )
        await session.execute(
            agent_t.agents.insert().values(
                id=s.agent_id,
                project_id=s.project_id,
                name="orr-agent",
                model_hint="claude",
                key_group_id=key_group_id,
            )
        )
        await session.execute(
            orch_t.workflow_runs.insert().values(id=s.run_id, project_id=s.project_id, trigger_type="manual")
        )
        for approval_id, room_id in (
            (s.approval_open, s.open_room),
            (s.approval_owners, s.owners_room),
            (s.approval_roomless, None),
        ):
            await session.execute(
                orch_t.approvals.insert().values(
                    id=approval_id,
                    workflow_run_id=s.run_id,
                    mode="single",
                    leader_agent_id=s.agent_id,
                    approver_agent_ids=[s.agent_id],
                    timeout_seconds=300,
                    chatroom_id=room_id,
                )
            )
        # `list_for_workflow_run` reaches the sub-agents through the synthetic
        # depth-0 root that carries the run id in `run_context`
        # (`repositories.py:573-580`), so the shape has to be real: root first,
        # children hung off it. The two children sit in different rooms, which is
        # the case the per-row filter exists for.
        await session.execute(
            orch_t.agent_instances.insert().values(
                id=s.root_instance,
                agent_id=s.agent_id,
                parent_id=None,
                chatroom_id=s.open_room,
                run_context={"workflow_run_id": str(s.run_id)},
            )
        )
        for instance_id, room_id in (
            (s.instance_open, s.open_room),
            (s.instance_owners, s.owners_room),
        ):
            await session.execute(
                orch_t.agent_instances.insert().values(
                    id=instance_id,
                    agent_id=s.agent_id,
                    parent_id=s.root_instance,
                    chatroom_id=room_id,
                    run_context={},
                )
            )
        await session.commit()

    try:
        yield s
    finally:
        async with sessionmaker() as cleanup:
            # The run goes first, and it has to: `approvals.leader_agent_id` is
            # ON DELETE RESTRICT (`tables.py:63`), so cascading the org down to
            # `agents` aborts while any approval still points at one. Dropping
            # the run cascades its approvals out of the way. Then orgs ->
            # projects -> {workspaces -> chatrooms, agents, key_groups} cascade,
            # and users follow last, since `orgs.creator_user_id` is RESTRICT.
            await cleanup.execute(
                orch_t.agent_instances.delete().where(orch_t.agent_instances.c.agent_id == s.agent_id)
            )
            await cleanup.execute(orch_t.workflow_runs.delete().where(orch_t.workflow_runs.c.id == s.run_id))
            await cleanup.execute(ten_t.orgs.delete().where(ten_t.orgs.c.id == s.org_id))
            await cleanup.execute(users_t.delete().where(users_t.c.id.in_([s.owner, s.member, s.outsider])))
            await cleanup.commit()


async def _get_approval(session: AsyncSession, *, approval_id: uuid.UUID, user_id: uuid.UUID) -> object:
    return await orchestration.get_approval(
        approval_id=approval_id,
        db=session,
        principal=_principal(user_id),
        resolver=TenancyRoleResolver(session),
    )


async def _list_run_subagents(session: AsyncSession, *, run_id: uuid.UUID, user_id: uuid.UUID) -> list[str]:
    out = await orchestration.list_run_subagents(
        workflow_run_id=run_id,
        pagination=PaginationParams(limit=100, offset=0),
        db=session,
        principal=_principal(user_id),
        resolver=TenancyRoleResolver(session),
    )
    return [i.id for i in out]


# --------------------------------------------------------------------------- #
# AC-1 — a room-bound approval follows that room's ACL
# --------------------------------------------------------------------------- #


async def test_ac1_member_reads_an_approval_in_a_room_they_can_open(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        out = await _get_approval(session, approval_id=scenario.approval_open, user_id=scenario.member)
    assert out.id == str(scenario.approval_open)  # type: ignore[attr-defined]


async def test_ac1_member_cannot_read_an_owners_only_rooms_approval(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    # The live hole (Q-4): before this change the same call returned 200.
    async with sessionmaker() as session:
        with pytest.raises(HTTPException) as exc:
            await _get_approval(session, approval_id=scenario.approval_owners, user_id=scenario.member)
    assert exc.value.status_code == 404
    assert exc.value.detail == "approval not found"


async def test_ac1_owner_reads_the_owners_only_rooms_approval(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        out = await _get_approval(session, approval_id=scenario.approval_owners, user_id=scenario.owner)
    assert out.id == str(scenario.approval_owners)  # type: ignore[attr-defined]


async def test_ac1_outsider_reads_nothing(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        for approval_id in (scenario.approval_open, scenario.approval_owners):
            with pytest.raises(HTTPException) as exc:
                await _get_approval(session, approval_id=approval_id, user_id=scenario.outsider)
            assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# AC-4 — agent instances take the same track
# --------------------------------------------------------------------------- #


async def test_ac4_run_subagents_are_filtered_by_room(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        as_member = await _list_run_subagents(session, run_id=scenario.run_id, user_id=scenario.member)
        as_owner = await _list_run_subagents(session, run_id=scenario.run_id, user_id=scenario.owner)
        as_outsider = await _list_run_subagents(session, run_id=scenario.run_id, user_id=scenario.outsider)

    assert as_member == [str(scenario.instance_open)]
    assert set(as_owner) == {str(scenario.instance_open), str(scenario.instance_owners)}
    assert as_outsider == []


async def test_ac4_children_of_a_readable_parent_are_still_filtered(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    # The root sits in the open room, so the member clears the parent gate — and
    # still must not see the child running in the owners-only room.
    async with sessionmaker() as session:
        out = await orchestration.list_subagent_children(
            parent_instance_id=scenario.root_instance,
            pagination=PaginationParams(limit=100, offset=0),
            db=session,
            principal=_principal(scenario.member),
            resolver=TenancyRoleResolver(session),
        )

    assert [i.id for i in out] == [str(scenario.instance_open)]


# --------------------------------------------------------------------------- #
# AC-5 — a deleted room tightens the record, it does not widen it
# --------------------------------------------------------------------------- #


async def test_ac5_deleting_the_room_makes_its_approval_backstage(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        await session.execute(conv_t.chatrooms.delete().where(conv_t.chatrooms.c.id == scenario.open_room))
        await session.commit()

        # The FK, not any Python branch, is what nulls the column.
        room_id = (
            await session.execute(
                sa.select(orch_t.approvals.c.chatroom_id).where(
                    orch_t.approvals.c.id == scenario.approval_open
                )
            )
        ).scalar_one()
        assert room_id is None

    async with sessionmaker() as session:
        # The member could read this a moment ago through the room tier.
        with pytest.raises(HTTPException) as exc:
            await _get_approval(session, approval_id=scenario.approval_open, user_id=scenario.member)
        assert exc.value.status_code == 403

    async with sessionmaker() as session:
        out = await _get_approval(session, approval_id=scenario.approval_open, user_id=scenario.owner)
    assert out.id == str(scenario.approval_open)  # type: ignore[attr-defined]


async def test_ac5_soft_deleting_the_room_denies_rather_than_falling_back(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    # A soft delete leaves `chatroom_id` set (the FK never fires), so this is the
    # other half of §8's fail-closed rule: an unresolvable room must deny.
    async with sessionmaker() as session:
        await session.execute(
            conv_t.chatrooms.update()
            .where(conv_t.chatrooms.c.id == scenario.open_room)
            .values(deleted_at=sa.func.now())
        )
        await session.commit()

    async with sessionmaker() as session:
        with pytest.raises(HTTPException) as exc:
            await _get_approval(session, approval_id=scenario.approval_open, user_id=scenario.member)
        assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# AC-8 substitute (D-2) — the in-room approval card's own call
# --------------------------------------------------------------------------- #


async def test_ac8_the_card_reconcile_call_still_works_for_an_ordinary_member(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    async with sessionmaker() as session:
        card = await _get_approval(session, approval_id=scenario.approval_open, user_id=scenario.member)

    assert card.id == str(scenario.approval_open)  # type: ignore[attr-defined]
    assert card.votes == []  # type: ignore[attr-defined]


async def test_ac8_the_room_scoped_listing_is_untouched(
    sessionmaker: async_sessionmaker[AsyncSession], scenario: Scenario
) -> None:
    # The card's *other* source (`chatrooms.list_chatroom_approvals`) goes through
    # the room ACL directly and this dossier does not touch it — pinned here so a
    # later narrowing of the orchestration side cannot quietly take it with it.
    from app.api.v1 import chatrooms

    async with sessionmaker() as session:
        out = await chatrooms.list_chatroom_approvals(
            chatroom_id=scenario.open_room,
            pagination=PaginationParams(limit=100, offset=0),
            db=session,
            principal=_principal(scenario.member),
        )

    assert [a.id for a in out] == [str(scenario.approval_open)]
