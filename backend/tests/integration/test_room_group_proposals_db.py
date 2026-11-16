"""Guests propose and vote in a room group, against PostgreSQL -- AC-7 and AC-9.

Spec: ``docs/tasks/2026-11-10-crat-lab-trial-fixes/spec.md``, milestone 3.

WHY THESE ARE DB-TIER TESTS
---------------------------
The trial defect was a foreign key: a guest's principal id is a
``guest_sessions.id``, so the ``users`` FKs on the proposal tables refused every
guest proposal and vote. Only a real database raises that, and the mocked unit
tier (``test_room_group_proposals.py``) cannot see it. Presence is the one double
here: it lives in Redis, and the ballot it feeds is pinned by the unit tier.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.v1 import activities as activities_route
from contexts.activities.application.platform_events import PlatformActivityEvents
from contexts.activities.domain.models import VoteChoice
from contexts.activities.infrastructure import tables as at
from contexts.activities.infrastructure.repositories.proposal_repo import (
    GroupProposalRepository,
    GroupProposalVoteRepository,
)
from contexts.activities.interfaces.error_mapping import register as register_activities_errors
from contexts.activities.interfaces.facade import ActivitiesFacade
from contexts.conversation.infrastructure import tables as ct
from shared_kernel.auth.clients import now
from shared_kernel.auth.dependencies import current_principal
from shared_kernel.auth.permissions import Principal
from shared_kernel.db.session import db_session

pytestmark = pytest.mark.db

_ROOM_NAME = "CRAT room-group itest"


@dataclass(frozen=True)
class RoomGroupRoom:
    project_id: uuid.UUID
    owner_user_id: uuid.UUID
    chatroom_id: uuid.UUID
    activity_type_id: uuid.UUID
    activation_id: uuid.UUID
    guest_a: uuid.UUID
    guest_b: uuid.UUID

    def guest(self, guest_id: uuid.UUID) -> Principal:
        return Principal(
            user_id=guest_id,
            is_admin=False,
            email_verified=False,
            is_guest=True,
            chatroom_id=self.chatroom_id,
        )

    @property
    def owner(self) -> Principal:
        return Principal(user_id=self.owner_user_id, is_admin=False, email_verified=True)


@pytest.fixture
async def room(
    sessionmaker: async_sessionmaker[AsyncSession],
    project: tuple[uuid.UUID, uuid.UUID],
) -> AsyncIterator[RoomGroupRoom]:
    """A guest-link room with no Member Group, a live 2/3 group round, two guests."""
    project_id, owner_id = project
    workspace_id, chatroom_id = uuid.uuid4(), uuid.uuid4()
    type_id, activation_id = uuid.uuid4(), uuid.uuid4()
    guest_a, guest_b = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        await session.execute(
            ct.workspaces.insert().values(id=workspace_id, project_id=project_id, name="room-group-itest")
        )
        await session.execute(
            ct.chatrooms.insert().values(
                id=chatroom_id,
                workspace_id=workspace_id,
                name=_ROOM_NAME,
                guest_token=str(uuid.uuid4()),
                created_by_user_id=owner_id,
                allow_guest_links=True,
            )
        )
        for guest_id, name in ((guest_a, "Guest A"), (guest_b, "Guest B")):
            await session.execute(
                ct.guest_sessions.insert().values(
                    id=guest_id,
                    chatroom_id=chatroom_id,
                    display_name=name,
                    refresh_token_hash=f"itest-{guest_id}",
                )
            )
        await session.execute(
            at.activity_types.insert().values(
                id=type_id,
                project_id=project_id,
                key="room-group-itest-type",
                name="room group itest type",
                validator_kind="in_process",
                validator_config={"validator_id": "filled_count", "min_filled": 1},
                group_config={"consent": {"numerator": 2, "denominator": 3}},
            )
        )
        await session.execute(
            at.activity_activations.insert().values(
                id=activation_id,
                chatroom_id=chatroom_id,
                activity_type_id=type_id,
                started_by_user_id=owner_id,
            )
        )
        await session.commit()
    try:
        yield RoomGroupRoom(project_id, owner_id, chatroom_id, type_id, activation_id, guest_a, guest_b)
    finally:
        async with sessionmaker() as cleanup:
            await cleanup.execute(text("SET ROLE smap_audit_retention"))
            try:
                await cleanup.execute(
                    text("DELETE FROM audit_logs WHERE actor_user_id IN (:a, :b)"),
                    {"a": guest_a, "b": guest_b},
                )
            finally:
                await cleanup.execute(text("RESET ROLE"))
            await cleanup.commit()


def _app(sessionmaker: async_sessionmaker[AsyncSession], principal: Principal) -> FastAPI:
    app = FastAPI()
    app.include_router(activities_route.chatroom_router)
    register_activities_errors(app)

    async def _db() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    app.dependency_overrides[db_session] = _db
    app.dependency_overrides[current_principal] = lambda: principal
    return app


async def _call(
    sessionmaker: async_sessionmaker[AsyncSession],
    principal: Principal,
    method: str,
    url: str,
    body: dict[str, object] | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=_app(sessionmaker, principal))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, json=body)


def _connected(*ids: uuid.UUID) -> object:
    return patch.object(
        PlatformActivityEvents, "connected_participants", new=AsyncMock(return_value=frozenset(ids))
    )


def _proposal_body(room: RoomGroupRoom) -> dict[str, object]:
    return {
        "member_group_id": str(room.chatroom_id),
        "activity_type_id": str(room.activity_type_id),
        "payload": {"answer": "cake"},
        "group_kind": "room",
    }


class TestGuestsDecideAsTheRoom:
    async def test_two_guests_propose_vote_and_submit_as_the_room_group(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: RoomGroupRoom
    ) -> None:
        """AC-7 end to end: the FK the trial hit is gone, kinds are stored, and
        the accepted answer is the room group's submission."""
        base = f"/api/chatrooms/{room.chatroom_id}/activity-proposals"
        with _connected(room.guest_a, room.guest_b):
            created = await _call(sessionmaker, room.guest(room.guest_a), "POST", base, _proposal_body(room))
            assert created.status_code == 200, created.text
            proposal = created.json()
            # 2/3 of two pinned guests is two: the proposer's approval alone is not enough.
            assert proposal["status"] == "open"
            assert proposal["voter_count"] == 2
            assert proposal["group_kind"] == "room"

            listed = await _call(
                sessionmaker,
                room.guest(room.guest_b),
                "GET",
                f"{base}?activation_id={room.activation_id}",
            )
            assert listed.status_code == 200, listed.text
            view = listed.json()
            assert view["eligible_groups"] == [
                {"id": str(room.chatroom_id), "name": _ROOM_NAME, "kind": "room"}
            ]
            assert [p["payload"] for p in view["items"]] == [{"answer": "cake"}]

            voted = await _call(
                sessionmaker,
                room.guest(room.guest_b),
                "POST",
                f"{base}/{proposal['id']}/votes",
                {"approve": True},
            )
        assert voted.status_code == 200, voted.text
        assert voted.json()["status"] == "accepted"

        async with sessionmaker() as read:
            stored = (
                await read.execute(
                    sa.select(at.activity_group_proposals).where(
                        at.activity_group_proposals.c.id == uuid.UUID(proposal["id"])
                    )
                )
            ).one()
            votes = (
                await read.execute(
                    sa.select(
                        at.activity_group_proposal_votes.c.user_id,
                        at.activity_group_proposal_votes.c.voter_kind,
                    ).where(at.activity_group_proposal_votes.c.proposal_id == stored.id)
                )
            ).all()
            submission = (
                await read.execute(
                    sa.select(at.activity_submissions).where(
                        at.activity_submissions.c.id == stored.submission_id
                    )
                )
            ).one()
            session_row = (
                await read.execute(
                    sa.select(at.activity_sessions).where(at.activity_sessions.c.id == submission.session_id)
                )
            ).one()
        assert (stored.proposer_user_id, stored.proposer_kind, stored.group_kind) == (
            room.guest_a,
            "guest",
            "room",
        )
        assert sorted(votes) == sorted([(room.guest_a, "guest"), (room.guest_b, "guest")])
        assert (submission.producer_user_id, submission.producer_kind) == (room.guest_a, "guest")
        assert session_row.subject_member_group_id == room.chatroom_id
        assert session_row.group_kind == "room"

    async def test_the_room_creator_may_not_propose(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: RoomGroupRoom
    ) -> None:
        with _connected(room.guest_a, room.guest_b):
            response = await _call(
                sessionmaker,
                room.owner,
                "POST",
                f"/api/chatrooms/{room.chatroom_id}/activity-proposals",
                _proposal_body(room),
            )
        assert response.status_code == 403, response.text
        assert response.json()["type"].endswith("activities/room-creator-not-in-room-group")

    async def test_the_state_block_names_the_room_group(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: RoomGroupRoom
    ) -> None:
        """AC-9: the facade reports a room-group round, and the room's id
        resolves to the room's name for the legend."""
        async with sessionmaker() as session:
            facade = ActivitiesFacade(session)
            state = await facade.room_activity_state(room.chatroom_id)
            names = await facade.resolve_member_group_names([room.chatroom_id])
        assert state.room_group is True
        assert names == {room.chatroom_id: _ROOM_NAME}


_USER_FKS = text(
    """
    SELECT con.conname FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = ANY (con.conkey)
    WHERE con.contype = 'f' AND rel.relname = :table AND att.attname = :column
    """
)


class TestSchema:
    @pytest.mark.parametrize(
        ("table", "column"),
        [
            ("activity_group_proposals", "proposer_user_id"),
            ("activity_group_proposal_votes", "user_id"),
        ],
    )
    async def test_no_foreign_key_to_users_remains(
        self, sessionmaker: async_sessionmaker[AsyncSession], table: str, column: str
    ) -> None:
        async with sessionmaker() as read:
            names = (await read.execute(_USER_FKS, {"table": table, "column": column})).scalars().all()
        assert names == []

    async def test_user_erasure_removes_the_users_proposals_and_votes_only(
        self, sessionmaker: async_sessionmaker[AsyncSession], room: RoomGroupRoom
    ) -> None:
        """What the dropped CASCADE did, now done by ``purge_user_rows``."""
        async with sessionmaker() as session:
            proposals = GroupProposalRepository(session)
            votes = GroupProposalVoteRepository(session)
            users_proposal = await proposals.create(
                chatroom_id=room.chatroom_id,
                activation_id=room.activation_id,
                activity_type_id=room.activity_type_id,
                member_group_id=uuid.uuid4(),
                proposer_user_id=room.owner_user_id,
                payload={"answer": "a"},
                voter_user_ids=(room.owner_user_id, room.guest_a),
                required_approvals=2,
                expires_at=now(),
            )
            guests_proposal = await proposals.create(
                chatroom_id=room.chatroom_id,
                activation_id=room.activation_id,
                activity_type_id=room.activity_type_id,
                member_group_id=room.chatroom_id,
                proposer_user_id=room.guest_a,
                payload={"answer": "b"},
                voter_user_ids=(room.owner_user_id, room.guest_a),
                required_approvals=2,
                expires_at=now(),
                proposer_is_guest=True,
            )
            await votes.cast(
                proposal_id=guests_proposal, user_id=room.owner_user_id, choice=VoteChoice.APPROVE
            )
            await votes.cast(
                proposal_id=guests_proposal,
                user_id=room.guest_a,
                choice=VoteChoice.APPROVE,
                voter_is_guest=True,
            )
            await session.commit()

        async with sessionmaker() as session:
            await ActivitiesFacade(session).purge_user_activity_rows(room.owner_user_id)
            await session.commit()

        async with sessionmaker() as read:
            remaining = (
                (
                    await read.execute(
                        sa.select(at.activity_group_proposals.c.id).where(
                            at.activity_group_proposals.c.chatroom_id == room.chatroom_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            voters = (
                (
                    await read.execute(
                        sa.select(at.activity_group_proposal_votes.c.user_id).where(
                            at.activity_group_proposal_votes.c.proposal_id == guests_proposal
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert users_proposal not in remaining
        assert remaining == [guests_proposal]
        assert voters == [room.guest_a]
