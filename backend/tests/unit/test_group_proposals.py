"""Group activity submissions by proposal and vote — AC-3, AC-5, AC-6, AC-8 to AC-13.

DB is mocked (repository instances replaced), as in ``test_activities_services``:
these pin the gates, the pinning, the reachability rule and the accept path's
reuse of the individual submit flow — no PostgreSQL required. The two properties
that only a real database can arbitrate (the one-subject CHECK and the
one-open-proposal-per-group index) live in
``tests/integration/test_group_activity_constraints_db.py``.
"""

from __future__ import annotations

import datetime as dt
import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from contexts.activities.application import group_proposal_service as gps
from contexts.activities.application.group_proposal_service import GroupProposalService
from contexts.activities.domain.errors import (
    ActivityNotActive,
    ActivityTypeNotGroupSubmittable,
    GroupProposalNotFound,
    GroupProposalResolved,
    MemberGroupNotBoundToRoom,
    NotAGroupMember,
    SubmissionPayloadInvalid,
)
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityActivation,
    ActivitySubmission,
    ActivityType,
    GroupProposal,
    ProposalStatus,
    ProposalVote,
    ValidationStatus,
    ValidatorKind,
    VoteChoice,
)

_NOW = dt.datetime(2026, 8, 25, tzinfo=dt.UTC)
_LATER = dt.datetime(2026, 8, 25, 4, tzinfo=dt.UTC)
_SCHEMA = {"type": "object", "properties": {"case": {"type": "string"}}, "required": ["case"]}
_TWO_THIRDS: dict[str, Any] = {"consent": {"numerator": 2, "denominator": 3}}
_PAYLOAD: dict[str, Any] = {"case": "the class trip"}

_PROJECT = uuid.uuid4()
_ROOM = uuid.uuid4()
_GROUP = uuid.uuid4()
_TYPE = uuid.uuid4()
_ACTIVATION = uuid.uuid4()
_PROPOSAL = uuid.uuid4()
# Sorted, because the service pins a sorted tuple and the expectations below
# name the members positionally.
_ALICE, _BOB, _CARA = sorted([uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
_STRANGER = uuid.uuid4()


def _type(*, group_config: dict[str, Any] | None = None) -> ActivityType:
    return ActivityType(
        id=_TYPE,
        project_id=_PROJECT,
        key="six-hats-shared-case",
        name="Shared case",
        payload_schema=_SCHEMA,
        validator_kind=ValidatorKind.IN_PROCESS,
        validator_config={"validator_id": "filled_count", "min_filled": 1},
        retention_days=None,
        version=1,
        created_at=_NOW,
        group_config=group_config,
    )


def _activation(status: ActivationStatus = ActivationStatus.ACTIVE) -> ActivityActivation:
    return ActivityActivation(
        id=_ACTIVATION,
        chatroom_id=_ROOM,
        activity_type_id=_TYPE,
        started_by_user_id=_ALICE,
        status=status,
        created_at=_NOW,
    )


def _proposal(
    *,
    status: ProposalStatus = ProposalStatus.OPEN,
    voters: tuple[uuid.UUID, ...] = (_ALICE, _BOB, _CARA),
    required: int = 2,
) -> GroupProposal:
    return GroupProposal(
        id=_PROPOSAL,
        chatroom_id=_ROOM,
        activation_id=_ACTIVATION,
        activity_type_id=_TYPE,
        member_group_id=_GROUP,
        proposer_user_id=_ALICE,
        payload=_PAYLOAD,
        voter_user_ids=voters,
        required_approvals=required,
        status=status,
        created_at=_NOW,
        expires_at=_LATER,
    )


def _submission() -> ActivitySubmission:
    return ActivitySubmission(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        activity_type_id=_TYPE,
        chatroom_id=_ROOM,
        producer_user_id=_ALICE,
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


def _service(
    *,
    activity_type: ActivityType | None = None,
    activation: ActivityActivation | None = None,
    live_groups: set[uuid.UUID] | None = None,
    bound_groups: set[uuid.UUID] | None = None,
    caller_groups: set[uuid.UUID] | None = None,
    members: set[uuid.UUID] | None = None,
    room: SimpleNamespace | None = None,
) -> GroupProposalService:
    """A service whose every collaborator is a double, wired to the happy path.

    The cross-context reads are patched at the two facade classes rather than
    stubbed one method at a time: the service reaches tenancy and conversation
    through their facades by design, and patching the class is what proves it
    holds no other route to those tables.
    """
    resolved_type = activity_type if activity_type is not None else _type(group_config=_TWO_THIRDS)
    activation_repo = MagicMock(
        get_active_for_update=AsyncMock(return_value=activation if activation is not None else _activation())
    )
    svc = GroupProposalService(MagicMock(), activation_repo=activation_repo)
    svc._type_repo = MagicMock(get=AsyncMock(return_value=resolved_type))  # type: ignore[assignment]
    svc._optin_repo = MagicMock(exists=AsyncMock(return_value=True))  # type: ignore[assignment]
    svc._proposals = MagicMock(  # type: ignore[assignment]
        create=AsyncMock(return_value=_PROPOSAL),
        get=AsyncMock(return_value=_proposal()),
        lock_for_update=AsyncMock(return_value=_proposal()),
        resolve=AsyncMock(return_value=True),
        attach_submission=AsyncMock(return_value=True),
        list_open_for_groups=AsyncMock(return_value=[_proposal()]),
    )
    svc._votes = MagicMock(  # type: ignore[assignment]
        cast=AsyncMock(return_value=None),
        counts=AsyncMock(return_value=(1, 0)),
        list_for_proposal=AsyncMock(return_value=[]),
    )
    svc._submissions = MagicMock(  # type: ignore[assignment]
        submit_for_group=AsyncMock(return_value=(_submission(), {"submission_id": "x"}))
    )
    svc._tenancy_double = MagicMock(  # type: ignore[attr-defined]
        live_member_group_ids=AsyncMock(return_value=live_groups if live_groups is not None else {_GROUP}),
        member_group_ids_for_user=AsyncMock(
            return_value=caller_groups if caller_groups is not None else {_GROUP}
        ),
        member_group_user_ids=AsyncMock(
            return_value=members if members is not None else {_ALICE, _BOB, _CARA}
        ),
        member_group_names=AsyncMock(return_value={_GROUP: "Group A"}),
    )
    svc._conversation_double = MagicMock(  # type: ignore[attr-defined]
        chatroom_member_group_ids=AsyncMock(
            return_value=bound_groups if bound_groups is not None else {_GROUP}
        ),
        get_chatroom=AsyncMock(return_value=room if room is not None else _room()),
    )
    return svc


def _room(*, member_groups: bool = True, owners_only: bool = False) -> SimpleNamespace:
    """Only the two access flags the proposal gate reads."""
    return SimpleNamespace(allow_member_groups=member_groups, allow_project_owners_only=owners_only)


def _wired(svc: GroupProposalService) -> Any:
    """Patch the two facades and the audit emit for one test body."""
    return (
        patch.object(gps, "TenancyFacade", return_value=svc._tenancy_double),  # type: ignore[attr-defined]
        patch.object(gps, "ConversationFacade", return_value=svc._conversation_double),  # type: ignore[attr-defined]
        patch.object(gps.audit, "emit", new=AsyncMock()),
    )


async def _create(svc: GroupProposalService, *, proposer: uuid.UUID = _ALICE) -> Any:
    tenancy, conversation, emit = _wired(svc)
    with tenancy, conversation, emit:
        return await svc.create(
            project_id=_PROJECT,
            chatroom_id=_ROOM,
            member_group_id=_GROUP,
            activity_type_id=_TYPE,
            proposer_user_id=proposer,
            payload=_PAYLOAD,
            actor_ip=None,
        )


async def _vote(svc: GroupProposalService, *, voter: uuid.UUID, approve: bool = True) -> Any:
    tenancy, conversation, emit = _wired(svc)
    with tenancy, conversation, emit:
        return await svc.vote(
            project_id=_PROJECT,
            chatroom_id=_ROOM,
            proposal_id=_PROPOSAL,
            voter_user_id=voter,
            approve=approve,
            actor_ip=None,
        )


class TestCreation:
    """AC-5 and AC-6."""

    async def test_a_proposal_pins_the_group_and_computes_the_bar(self) -> None:
        svc = _service()

        resolution = await _create(svc)

        pinned = svc._proposals.create.await_args.kwargs  # type: ignore[attr-defined]
        assert pinned["voter_user_ids"] == (_ALICE, _BOB, _CARA)
        # 2/3 over three people is two, by exact integer arithmetic.
        assert pinned["required_approvals"] == 2
        assert resolution.tally.proposal.member_group_id == _GROUP

    async def test_the_proposers_own_approval_is_recorded_as_a_vote(self) -> None:
        """So the tally is the whole story: a reader never has to add one to a
        count for a person whose position is only implied by the row existing."""
        svc = _service()

        await _create(svc)

        cast = svc._votes.cast.await_args.kwargs  # type: ignore[attr-defined]
        assert cast["user_id"] == _ALICE
        assert cast["choice"] is VoteChoice.APPROVE

    async def test_a_type_with_no_group_config_is_refused(self) -> None:
        """AC-2's other side: an individual-only type behaves exactly as today,
        and there is no way to make it collective from the client."""
        svc = _service(activity_type=_type(group_config=None))

        with pytest.raises(ActivityTypeNotGroupSubmittable):
            await _create(svc)

    async def test_a_malformed_stored_fraction_refuses_rather_than_defaults(self) -> None:
        """There is no safe default for a consent rule, so a fraction that no
        longer parses must not fall back to one."""
        svc = _service(activity_type=_type(group_config={"consent": {"numerator": 0}}))

        with pytest.raises(ActivityTypeNotGroupSubmittable):
            await _create(svc)

    async def test_a_dead_round_is_refused(self) -> None:
        svc = _service()
        svc._activation_repo = MagicMock(get_active_for_update=AsyncMock(return_value=None))  # type: ignore[assignment]

        with pytest.raises(ActivityNotActive):
            await _create(svc)

    async def test_a_group_of_another_project_is_refused(self) -> None:
        """A group id from another project is indistinguishable here from one
        that exists but was never bound — telling them apart would confirm
        another tenant's group exists."""
        svc = _service(live_groups=set())

        with pytest.raises(MemberGroupNotBoundToRoom):
            await _create(svc)

    async def test_a_live_group_not_bound_to_this_room_is_refused(self) -> None:
        svc = _service(bound_groups=set())

        with pytest.raises(MemberGroupNotBoundToRoom):
            await _create(svc)

    async def test_a_non_member_cannot_propose_for_the_group(self) -> None:
        svc = _service(caller_groups=set())

        with pytest.raises(NotAGroupMember):
            await _create(svc)

    async def test_an_owners_only_room_cannot_host_a_group_submission(self) -> None:
        """§5.2's "intersected with those who can read the room", expressed over
        the room rather than per member.

        `allow_project_owners_only` is exclusive and no other tier widens it
        (`_satisfies_room_flags`), so in such a room the group's members cannot
        read it at all. Pinning them anyway would put people on a ballot they can
        never cast and raise the bar for everyone else until the TTL expired it.
        """
        svc = _service(room=_room(owners_only=True))

        with pytest.raises(MemberGroupNotBoundToRoom):
            await _create(svc)

    async def test_a_room_with_the_group_tier_switched_off_is_refused_too(self) -> None:
        """A binding can outlive the flag that made it mean anything."""
        svc = _service(room=_room(member_groups=False))

        with pytest.raises(MemberGroupNotBoundToRoom):
            await _create(svc)

    async def test_a_payload_that_fails_the_schema_is_refused_at_proposal_time(self) -> None:
        """A proposal nobody could accept should fail now, not after three people
        have voted for it."""
        svc = _service()
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit, pytest.raises(SubmissionPayloadInvalid):
            await svc.create(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                member_group_id=_GROUP,
                activity_type_id=_TYPE,
                proposer_user_id=_ALICE,
                payload={},
                actor_ip=None,
            )
        svc._proposals.create.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_the_proposer_is_on_the_ballot_even_if_the_two_reads_disagree(self) -> None:
        """The membership check and the member listing are separate queries; a
        change landing between them must not produce a ballot the proposer is
        missing from — that would be a proposal nobody can accept."""
        svc = _service(members={_BOB, _CARA})

        await _create(svc)

        assert _ALICE in svc._proposals.create.await_args.kwargs["voter_user_ids"]  # type: ignore[attr-defined]

    async def test_a_proposal_already_at_its_bar_is_accepted_at_creation(self) -> None:
        """The proposer's own approval can be enough, and then nothing else will
        ever happen to the proposal.

        `required_approvals` is 1 whenever the fraction over the pinned set
        rounds down to one person -- 2/3 over a group whose only standing member
        is the proposer, 1/2 over two, 1/3 over three. Casting the implicit
        approval and returning without settling leaves such a proposal `open`
        until the four-hour TTL expires it, and the group's answer is lost with
        no error anywhere.
        """
        svc = _service(members={_ALICE})
        svc._proposals.get = AsyncMock(  # type: ignore[attr-defined]
            return_value=_proposal(voters=(_ALICE,), required=1)
        )
        svc._votes.counts = AsyncMock(return_value=(1, 0))  # type: ignore[attr-defined]

        resolution = await _create(svc)

        assert resolution.transitioned is True
        assert resolution.submission is not None
        svc._submissions.submit_for_group.assert_awaited_once()  # type: ignore[attr-defined]

    async def test_an_unmet_bar_still_leaves_the_proposal_open(self) -> None:
        svc = _service()

        resolution = await _create(svc)

        assert resolution.transitioned is False
        assert resolution.submission is None
        svc._submissions.submit_for_group.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_the_audit_event_carries_no_payload_content(self) -> None:
        svc = _service()
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit as emitted:
            await svc.create(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                member_group_id=_GROUP,
                activity_type_id=_TYPE,
                proposer_user_id=_ALICE,
                payload=_PAYLOAD,
                actor_ip=None,
            )

        metadata = emitted.await_args.args[1].metadata
        assert "the class trip" not in repr(metadata)
        assert metadata["required_approvals"] == "2"


class TestVoting:
    """AC-6, AC-8, AC-12."""

    async def test_a_non_pinned_user_cannot_vote_and_learns_nothing(self) -> None:
        """The pin is what is checked, not membership today. A 404 rather than a
        403: a vote record names people, so a non-voter must not be able to
        confirm one exists."""
        svc = _service()

        with pytest.raises(GroupProposalNotFound):
            await _vote(svc, voter=_STRANGER)

    async def test_a_proposal_in_another_room_is_not_found(self) -> None:
        svc = _service()
        svc._proposals.lock_for_update = AsyncMock(  # type: ignore[attr-defined]
            return_value=_proposal()
        )

        tenancy, conversation, emit = _wired(svc)
        with tenancy, conversation, emit, pytest.raises(GroupProposalNotFound):
            await svc.vote(
                project_id=_PROJECT,
                chatroom_id=uuid.uuid4(),
                proposal_id=_PROPOSAL,
                voter_user_id=_BOB,
                approve=True,
                actor_ip=None,
            )

    async def test_voting_on_a_resolved_proposal_is_a_conflict(self) -> None:
        svc = _service()
        svc._proposals.lock_for_update = AsyncMock(  # type: ignore[attr-defined]
            return_value=_proposal(status=ProposalStatus.ACCEPTED)
        )

        with pytest.raises(GroupProposalResolved):
            await _vote(svc, voter=_BOB)

    async def test_one_rejection_under_two_thirds_leaves_the_vote_open(self) -> None:
        """AC-8. Treating the first dissent as fatal would silently implement
        unanimity and make the configured fraction mean something else."""
        svc = _service()
        # Three voters, bar of two: one approval, one rejection, one undecided.
        svc._votes.counts = AsyncMock(return_value=(1, 1))  # type: ignore[attr-defined]

        resolution = await _vote(svc, voter=_BOB, approve=False)

        assert resolution.transitioned is False
        svc._proposals.resolve.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_the_second_rejection_makes_the_bar_unreachable(self) -> None:
        svc = _service()
        svc._votes.counts = AsyncMock(return_value=(1, 2))  # type: ignore[attr-defined]

        resolution = await _vote(svc, voter=_CARA, approve=False)

        assert resolution.transitioned is True
        assert (
            svc._proposals.resolve.await_args.kwargs["status"]  # type: ignore[attr-defined]
            is ProposalStatus.REJECTED
        )
        svc._submissions.submit_for_group.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_under_unanimity_the_first_rejection_ends_it(self) -> None:
        svc = _service()
        svc._proposals.lock_for_update = AsyncMock(  # type: ignore[attr-defined]
            return_value=_proposal(required=3)
        )
        svc._proposals.get = AsyncMock(return_value=_proposal(required=3))  # type: ignore[attr-defined]
        svc._votes.counts = AsyncMock(return_value=(1, 1))  # type: ignore[attr-defined]

        resolution = await _vote(svc, voter=_BOB, approve=False)

        assert resolution.transitioned is True
        assert (
            svc._proposals.resolve.await_args.kwargs["status"]  # type: ignore[attr-defined]
            is ProposalStatus.REJECTED
        )

    async def test_the_vote_audit_event_does_not_record_which_way_it_went(self) -> None:
        """AC-13's audit half. An audit row is readable by an org admin, and
        [R30.42] confines the per-person record to the pinned voters and the room
        creator."""
        svc = _service()
        svc._votes.counts = AsyncMock(return_value=(1, 1))  # type: ignore[attr-defined]
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit as emitted:
            await svc.vote(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                proposal_id=_PROPOSAL,
                voter_user_id=_BOB,
                approve=False,
                actor_ip=None,
            )

        metadata = emitted.await_args_list[0].args[1].metadata
        assert "reject" not in repr(metadata)
        assert "approve" not in repr(metadata)


class TestAcceptance:
    """AC-10."""

    async def test_reaching_the_bar_produces_one_submission_for_the_group(self) -> None:
        svc = _service()
        svc._votes.counts = AsyncMock(return_value=(2, 0))  # type: ignore[attr-defined]

        resolution = await _vote(svc, voter=_BOB)

        assert resolution.transitioned is True
        assert resolution.submission is not None
        kwargs = svc._submissions.submit_for_group.await_args.kwargs  # type: ignore[attr-defined]
        assert kwargs["member_group_id"] == _GROUP
        # The proposer wrote the text; the others approved it. The record says so.
        assert kwargs["proposer_user_id"] == _ALICE
        assert kwargs["payload"] == _PAYLOAD
        # The echo names the GROUP, never a member ([R30.08], §5.4).
        assert kwargs["group_name"] == "Group A"

    async def test_the_status_flip_happens_before_the_submission(self) -> None:
        """It is the mutual exclusion: a concurrent expiry or a second vote that
        also reached the bar finds zero rows and returns without submitting, so
        one accepted proposal produces exactly one submission."""
        svc = _service()
        svc._votes.counts = AsyncMock(return_value=(2, 0))  # type: ignore[attr-defined]
        svc._proposals.resolve = AsyncMock(return_value=False)  # type: ignore[attr-defined]

        resolution = await _vote(svc, voter=_BOB)

        assert resolution.transitioned is False
        assert resolution.submission is None
        svc._submissions.submit_for_group.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_a_round_ended_between_the_last_two_votes_refuses(self) -> None:
        """AC-9's other half: no proposal may produce a submission after its
        round finished, and the last vote is exactly when that race is live."""
        svc = _service()
        svc._votes.counts = AsyncMock(return_value=(2, 0))  # type: ignore[attr-defined]
        svc._activation_repo = MagicMock(get_active_for_update=AsyncMock(return_value=None))  # type: ignore[assignment]

        with pytest.raises(ActivityNotActive):
            await _vote(svc, voter=_BOB)
        svc._submissions.submit_for_group.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_the_submission_id_is_stamped_on_the_proposal(self) -> None:
        svc = _service()
        svc._votes.counts = AsyncMock(return_value=(2, 0))  # type: ignore[attr-defined]

        resolution = await _vote(svc, voter=_BOB)

        assert resolution.submission is not None
        svc._proposals.attach_submission.assert_awaited_once()  # type: ignore[attr-defined]
        assert (
            svc._proposals.attach_submission.await_args.kwargs["submission_id"]  # type: ignore[attr-defined]
            == resolution.submission.id
        )


class TestWithdrawal:
    async def test_only_the_proposer_may_withdraw(self) -> None:
        svc = _service()
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit, pytest.raises(GroupProposalNotFound):
            await svc.withdraw(
                chatroom_id=_ROOM,
                proposal_id=_PROPOSAL,
                caller_user_id=_BOB,
                actor_ip=None,
            )

    async def test_the_proposer_may(self) -> None:
        svc = _service()
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit:
            await svc.withdraw(
                chatroom_id=_ROOM,
                proposal_id=_PROPOSAL,
                caller_user_id=_ALICE,
                actor_ip=None,
            )

        assert (
            svc._proposals.resolve.await_args.kwargs["status"]  # type: ignore[attr-defined]
            is ProposalStatus.WITHDRAWN
        )


class TestVoteVisibility:
    """AC-12."""

    async def test_a_pinned_voter_sees_the_per_person_record(self) -> None:
        svc = _service()
        svc._votes.list_for_proposal = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                ProposalVote(proposal_id=_PROPOSAL, user_id=_BOB, choice=VoteChoice.REJECT, created_at=_NOW)
            ]
        )

        tally = await svc.get_tally(
            chatroom_id=_ROOM,
            proposal_id=_PROPOSAL,
            caller_user_id=_BOB,
            caller_is_room_creator=False,
        )

        assert [v.user_id for v in tally.votes] == [_BOB]

    async def test_the_room_creator_sees_it_too(self) -> None:
        """The vote record is an accountability record for the group AND the
        teacher, which is why the creator is the one non-voter who may read it."""
        svc = _service()
        svc._votes.list_for_proposal = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                ProposalVote(proposal_id=_PROPOSAL, user_id=_BOB, choice=VoteChoice.REJECT, created_at=_NOW)
            ]
        )

        tally = await svc.get_tally(
            chatroom_id=_ROOM,
            proposal_id=_PROPOSAL,
            caller_user_id=_STRANGER,
            caller_is_room_creator=True,
        )

        assert len(tally.votes) == 1

    async def test_anyone_else_gets_a_404_rather_than_a_redacted_body(self) -> None:
        """Confirming a proposal exists is itself a disclosure about who is
        grouped with whom."""
        svc = _service()

        with pytest.raises(GroupProposalNotFound):
            await svc.get_tally(
                chatroom_id=_ROOM,
                proposal_id=_PROPOSAL,
                caller_user_id=_STRANGER,
                caller_is_room_creator=False,
            )

    async def test_a_participant_listing_is_narrowed_to_their_own_groups(self) -> None:
        svc = _service(caller_groups={_GROUP, uuid.uuid4()}, bound_groups={_GROUP})
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit:
            await svc.list_round_for_caller(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                activation_id=_ACTIVATION,
                caller_user_id=_BOB,
                caller_is_room_creator=False,
            )

        # The intersection of "my groups" and "this room's groups", never either
        # alone: my group in another room is not this room's business, and this
        # room's other group is not mine.
        assert svc._proposals.list_open_for_groups.await_args.kwargs[  # type: ignore[attr-defined]
            "member_group_ids"
        ] == [_GROUP]

    async def test_a_room_creator_listing_covers_every_bound_group(self) -> None:
        other = uuid.uuid4()
        svc = _service(bound_groups={_GROUP, other}, live_groups={_GROUP, other})
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit:
            await svc.list_round_for_caller(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                activation_id=_ACTIVATION,
                caller_user_id=_STRANGER,
                caller_is_room_creator=True,
            )

        assert set(
            svc._proposals.list_open_for_groups.await_args.kwargs["member_group_ids"]  # type: ignore[attr-defined]
        ) == {_GROUP, other}


class TestEligibleGroups:
    """The picker's source: what this caller could propose FOR (AC-5).

    Distinct from what they may READ, which is why the round view carries both.
    """

    async def test_it_is_the_callers_own_bound_groups_with_their_names(self) -> None:
        svc = _service(caller_groups={_GROUP, uuid.uuid4()}, bound_groups={_GROUP})
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit:
            view = await svc.list_round_for_caller(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                activation_id=_ACTIVATION,
                caller_user_id=_BOB,
                caller_is_room_creator=False,
            )

        assert [(g.id, g.name) for g in view.eligible_groups] == [(_GROUP, "Group A")]

    async def test_a_room_creator_in_no_group_may_read_every_vote_and_propose_for_none(
        self,
    ) -> None:
        """A teacher is not part of a student group (§4.3). Offering them a
        picker would produce ``NotAGroupMember`` in front of the class."""
        other = uuid.uuid4()
        svc = _service(
            bound_groups={_GROUP, other},
            live_groups={_GROUP, other},
            caller_groups=set(),
        )
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit:
            view = await svc.list_round_for_caller(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                activation_id=_ACTIVATION,
                caller_user_id=_STRANGER,
                caller_is_room_creator=True,
            )

        assert view.eligible_groups == ()
        assert set(
            svc._proposals.list_open_for_groups.await_args.kwargs["member_group_ids"]  # type: ignore[attr-defined]
        ) == {_GROUP, other}

    async def test_a_group_this_room_is_not_bound_to_is_not_offered(self) -> None:
        """The same three gates the proposal itself runs, so the picker cannot
        offer a choice the create call would refuse."""
        mine_only = uuid.uuid4()
        svc = _service(caller_groups={mine_only}, bound_groups={_GROUP}, live_groups={_GROUP})
        tenancy, conversation, emit = _wired(svc)

        with tenancy, conversation, emit:
            view = await svc.list_round_for_caller(
                project_id=_PROJECT,
                chatroom_id=_ROOM,
                activation_id=_ACTIVATION,
                caller_user_id=_BOB,
                caller_is_room_creator=False,
            )

        assert view.eligible_groups == ()
