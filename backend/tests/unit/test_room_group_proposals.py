"""The room group ([R30.49]): AC-7 and AC-9 of the CRAT lab trial fixes.

A room with no live bound Member Group runs its group work as one group whose id
is the chatroom id. These pin which rooms take that source, who may propose, how
the ballot is pinned from presence, and that guests carry their kind through to
the votes, the audit rows and the accepted submission. The Member Group source is
pinned in ``test_group_proposals.py`` and is unchanged (AC-8).
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.application import group_proposal_service as gps
from contexts.activities.application.group_proposal_service import GroupProposalService
from contexts.activities.domain.errors import (
    MemberGroupNotBoundToRoom,
    NotAGroupMember,
    RoomCreatorNotInRoomGroup,
    RoomGroupBallotUnavailable,
)
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivitySubmission,
    ActivityType,
    GroupKind,
    GroupProposal,
    MemberGroupRef,
    ProposalStatus,
    ValidationStatus,
    ValidatorKind,
)

_NOW = dt.datetime(2026, 11, 10, tzinfo=dt.UTC)
_LATER = dt.datetime(2026, 11, 10, 4, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}
_TWO_THIRDS: dict[str, Any] = {"consent": {"numerator": 2, "denominator": 3}}
_PAYLOAD: dict[str, Any] = {"answer": "cake"}

_PROJECT = uuid.uuid4()
_ROOM = uuid.uuid4()
_TYPE = uuid.uuid4()
_ACTIVATION = uuid.uuid4()
_PROPOSAL = uuid.uuid4()
_CREATOR = uuid.uuid4()
_GUEST_A, _GUEST_B, _USER_C = sorted([uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
_MEMBER_GROUP = uuid.uuid4()


def _type() -> ActivityType:
    return ActivityType(
        id=_TYPE,
        project_id=_PROJECT,
        key="crat-word-puzzle",
        name="CRAT",
        payload_schema=_SCHEMA,
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "filled_count", "min_filled": 1},
        retention_days=None,
        version=1,
        created_at=_NOW,
        group_config=_TWO_THIRDS,
    )


def _activation() -> ActivityActivation:
    return ActivityActivation(
        id=_ACTIVATION,
        chatroom_id=_ROOM,
        activity_type_id=_TYPE,
        started_by_user_id=_CREATOR,
        status=ActivationStatus.ACTIVE,
        created_at=_NOW,
    )


def _proposal(*, voters: tuple[uuid.UUID, ...] = (_GUEST_A, _GUEST_B, _USER_C)) -> GroupProposal:
    return GroupProposal(
        id=_PROPOSAL,
        chatroom_id=_ROOM,
        activation_id=_ACTIVATION,
        activity_type_id=_TYPE,
        member_group_id=_ROOM,
        proposer_user_id=_GUEST_A,
        payload=_PAYLOAD,
        voter_user_ids=voters,
        required_approvals=2,
        status=ProposalStatus.OPEN,
        created_at=_NOW,
        expires_at=_LATER,
        proposer_is_guest=True,
        group_kind=GroupKind.ROOM,
    )


def _submission() -> ActivitySubmission:
    return ActivitySubmission(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        activity_type_id=_TYPE,
        chatroom_id=_ROOM,
        producer_user_id=_GUEST_A,
        payload=_PAYLOAD,
        attempt_no=1,
        validation_status=ValidationStatus.VALIDATED,
        is_valid=True,
        error_class=None,
        sub_scores={},
        latency_ms=1,
        retain_until=None,
        created_at=_NOW,
    )


def _room(*, member_groups: bool = True, owners_only: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=_ROOM,
        name="CRAT lab",
        created_by_user_id=_CREATOR,
        allow_member_groups=member_groups,
        allow_project_owners_only=owners_only,
    )


class _Harness:
    def __init__(
        self,
        *,
        room: SimpleNamespace | None = None,
        bound: set[uuid.UUID] | None = None,
        live: set[uuid.UUID] | None = None,
        connected: frozenset[uuid.UUID] | None = frozenset({_GUEST_A, _GUEST_B, _USER_C}),
        counts: tuple[int, int] = (1, 0),
    ) -> None:
        activation_repo = MagicMock(get_active_for_update=AsyncMock(return_value=_activation()))
        svc = GroupProposalService(MagicMock(), activation_repo=activation_repo)
        svc._type_repo = MagicMock(get=AsyncMock(return_value=_type()))
        svc._optin_repo = MagicMock(exists=AsyncMock(return_value=True))
        self.proposals = MagicMock(
            create=AsyncMock(return_value=_PROPOSAL),
            get=AsyncMock(return_value=_proposal()),
            lock_for_update=AsyncMock(return_value=_proposal()),
            resolve=AsyncMock(return_value=True),
            attach_submission=AsyncMock(return_value=True),
            list_open_for_groups=AsyncMock(return_value=[_proposal()]),
        )
        self.votes = MagicMock(
            cast=AsyncMock(return_value=None),
            counts=AsyncMock(return_value=counts),
            list_for_proposal=AsyncMock(return_value=[]),
        )
        self.submissions = MagicMock(
            submit_for_group=AsyncMock(return_value=(_submission(), {"submission_id": "x"}))
        )
        svc._proposals = self.proposals
        svc._votes = self.votes
        svc._submissions = self.submissions
        self.svc = svc
        self.tenancy = MagicMock(
            live_member_group_ids=AsyncMock(return_value=live if live is not None else set()),
            member_group_ids_for_user=AsyncMock(return_value=set()),
            member_group_user_ids=AsyncMock(return_value=set()),
            member_group_names=AsyncMock(return_value={}),
        )
        self.conversation = MagicMock(
            chatroom_member_group_ids=AsyncMock(return_value=bound if bound is not None else set()),
            get_chatroom=AsyncMock(return_value=room if room is not None else _room()),
        )
        self.presence = MagicMock(connected_participants=AsyncMock(return_value=connected))
        self.emit = AsyncMock()

    def wired(self) -> ExitStack:
        stack = ExitStack()
        stack.enter_context(patch.object(gps, "TenancyFacade", return_value=self.tenancy))
        stack.enter_context(patch.object(gps, "ConversationFacade", return_value=self.conversation))
        stack.enter_context(patch.object(gps, "PlatformActivityEvents", return_value=self.presence))
        stack.enter_context(patch.object(gps.audit, "emit", new=self.emit))
        return stack

    async def create(
        self,
        *,
        proposer: uuid.UUID = _GUEST_A,
        is_guest: bool = True,
        group_id: uuid.UUID = _ROOM,
        kind: GroupKind = GroupKind.ROOM,
    ) -> Any:
        with self.wired():
            return await self.svc.create(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                member_group_id=group_id,
                activity_type_id=_TYPE,
                proposer_user_id=proposer,
                payload=_PAYLOAD,
                actor_ip=None,
                group_kind=kind,
                proposer_is_guest=is_guest,
            )

    async def vote(self, *, voter: uuid.UUID, is_guest: bool = True) -> Any:
        with self.wired():
            return await self.svc.vote(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                proposal_id=_PROPOSAL,
                voter_user_id=voter,
                approve=True,
                actor_ip=None,
                voter_is_guest=is_guest,
            )

    async def applies(self) -> bool:
        with self.wired():
            return await self.svc.room_group_applies(project_id=_PROJECT, chatroom_id=_ROOM)

    async def listing(self, *, caller: uuid.UUID, creator: bool) -> Any:
        with self.wired():
            return await self.svc.list_round_for_caller(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                activation_id=_ACTIVATION,
                caller_user_id=caller,
                caller_is_room_creator=creator,
            )

    def audit_events(self) -> list[Any]:
        return [c.args[1] for c in self.emit.await_args_list]


class TestWhichRoomsAreOneGroup:
    async def test_a_room_with_no_bound_group_is_one_group(self) -> None:
        assert await _Harness().applies() is True

    async def test_a_room_that_does_not_admit_member_groups_is_one_group(self) -> None:
        h = _Harness(room=_room(member_groups=False), bound={_MEMBER_GROUP}, live={_MEMBER_GROUP})
        assert await h.applies() is True

    async def test_an_owners_only_room_is_one_group(self) -> None:
        """Owners-only excludes the member-group tier, so no Member Group can
        answer there; the room group is the only group that can."""
        h = _Harness(room=_room(owners_only=True), bound={_MEMBER_GROUP}, live={_MEMBER_GROUP})
        assert await h.applies() is True

    async def test_a_room_whose_bound_groups_are_all_gone_is_one_group(self) -> None:
        assert await _Harness(bound={_MEMBER_GROUP}, live=set()).applies() is True

    async def test_a_room_with_a_live_bound_group_keeps_member_groups(self) -> None:
        """AC-8: such a room behaves exactly as before."""
        assert await _Harness(bound={_MEMBER_GROUP}, live={_MEMBER_GROUP}).applies() is False


class TestRoomGroupCreation:
    async def test_the_ballot_is_everyone_connected_plus_the_proposer(self) -> None:
        h = _Harness(connected=frozenset({_GUEST_B, _USER_C}))

        await h.create()

        pinned = h.proposals.create.await_args.kwargs
        assert pinned["voter_user_ids"] == tuple(sorted({_GUEST_A, _GUEST_B, _USER_C}))
        assert pinned["required_approvals"] == 2
        assert pinned["member_group_id"] == _ROOM
        assert pinned["group_kind"] is GroupKind.ROOM
        assert pinned["proposer_is_guest"] is True

    async def test_a_guest_proposers_implicit_vote_is_stored_as_a_guest_vote(self) -> None:
        h = _Harness()

        await h.create()

        cast = h.votes.cast.await_args.kwargs
        assert cast["user_id"] == _GUEST_A
        assert cast["voter_is_guest"] is True

    async def test_a_guest_proposers_audit_row_names_the_room_not_a_user(self) -> None:
        h = _Harness()

        await h.create()

        created = next(e for e in h.audit_events() if e.action == "activity.proposal_created")
        assert created.actor_guest_room_id == _ROOM
        assert created.metadata["group_kind"] == "room"

    async def test_a_registered_proposers_audit_row_carries_no_guest_room(self) -> None:
        h = _Harness()

        await h.create(proposer=_USER_C, is_guest=False)

        created = next(e for e in h.audit_events() if e.action == "activity.proposal_created")
        assert created.actor_guest_room_id is None

    async def test_the_room_creator_may_not_propose(self) -> None:
        with pytest.raises(RoomCreatorNotInRoomGroup):
            await _Harness().create(proposer=_CREATOR, is_guest=False)

    async def test_unknown_presence_refuses_rather_than_pinning_the_proposer_alone(self) -> None:
        """A one-person ballot would accept at once and skip the vote entirely."""
        h = _Harness(connected=None)

        with pytest.raises(RoomGroupBallotUnavailable):
            await h.create()
        h.proposals.create.assert_not_awaited()

    async def test_a_room_group_id_other_than_the_room_is_refused(self) -> None:
        with pytest.raises(MemberGroupNotBoundToRoom):
            await _Harness().create(group_id=uuid.uuid4())

    async def test_a_room_group_proposal_in_a_member_group_room_is_refused(self) -> None:
        """The two sources never coexist: where a live Member Group is bound,
        nobody may answer as the whole room instead."""
        h = _Harness(bound={_MEMBER_GROUP}, live={_MEMBER_GROUP})

        with pytest.raises(MemberGroupNotBoundToRoom):
            await h.create()

    async def test_a_guest_cannot_propose_for_a_member_group(self) -> None:
        """A guest belongs to no Member Group, so the existing gate refuses it."""
        h = _Harness(room=_room(), bound={_MEMBER_GROUP}, live={_MEMBER_GROUP})

        with pytest.raises(NotAGroupMember):
            await h.create(group_id=_MEMBER_GROUP, kind=GroupKind.MEMBER_GROUP)


class TestRoomGroupAcceptance:
    async def test_the_deciding_guest_vote_records_a_room_group_submission(self) -> None:
        h = _Harness(counts=(2, 0))

        resolution = await h.vote(voter=_GUEST_B)

        assert resolution.transitioned is True
        submit = h.submissions.submit_for_group.await_args.kwargs
        assert submit["group_kind"] is GroupKind.ROOM
        assert submit["member_group_id"] == _ROOM
        assert submit["group_name"] == "CRAT lab"
        assert submit["proposer_is_guest"] is True
        assert submit["actor_is_guest"] is True
        assert h.votes.cast.await_args.kwargs["voter_is_guest"] is True

    async def test_the_creator_holds_no_ballot(self) -> None:
        from contexts.activities.domain.errors import GroupProposalNotFound

        with pytest.raises(GroupProposalNotFound):
            await _Harness().vote(voter=_CREATOR, is_guest=False)


class TestRoomGroupListing:
    async def test_a_participant_is_offered_the_room_as_their_one_group(self) -> None:
        h = _Harness()

        view = await h.listing(caller=_GUEST_B, creator=False)

        assert view.eligible_groups == (MemberGroupRef(id=_ROOM, name="CRAT lab", kind=GroupKind.ROOM),)
        assert [t.proposal.id for t in view.proposals] == [_PROPOSAL]
        h.proposals.list_open_for_groups.assert_awaited_once_with(
            chatroom_id=_ROOM, activation_id=_ACTIVATION, member_group_ids=[_ROOM]
        )
        h.tenancy.member_group_ids_for_user.assert_not_awaited()

    async def test_the_creator_sees_the_proposals_but_may_not_propose(self) -> None:
        view = await _Harness().listing(caller=_CREATOR, creator=True)

        assert view.eligible_groups == ()
        assert len(view.proposals) == 1
