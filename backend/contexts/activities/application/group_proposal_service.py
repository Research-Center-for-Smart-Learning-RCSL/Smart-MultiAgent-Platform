"""Group activity submissions by proposal and vote (§30, [R30.41], [R30.42]).

A group submission is never a direct write. One member proposes a payload, the
group's other members vote, and the submission is recorded the moment the type's
declared consent fraction is met. Routing it through a proposal is what makes the
consent check STRUCTURALLY unavoidable: ``SubmissionService.submit_for_group``
has exactly one caller, and it is the accept path below.

WHY ``_ensure_subject_is_caller`` IS NOT WEAKENED. That guard says a caller may
only act on their own subject, and it is still exactly true for every personal
session. A group session simply never reaches it -- this service is the only way
to write into one, and it has its own three gates (live group of the room's
project, bound to this room, caller is a pinned voter) which answer a different
question. The dossier's §9 asks for that rather than a third importer of a
module-private helper.

WHAT THE ROOM LEARNS AND WHAT IT DOES NOT. Every broadcast this service produces
carries ids and counts. It never carries the payload and never carries a
per-person vote ([R30.42]): the room learns that a group is deciding, and only
the group sees what and who.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.application.ports import ActivityActivationRepository
from contexts.activities.application.reachability import resolve_reachable_type
from contexts.activities.application.submission_service import SubmissionService
from contexts.activities.application.validators.schema import payload_errors
from contexts.activities.domain.errors import (
    ActivityNotActive,
    ActivityTypeNotGroupSubmittable,
    GroupProposalNotFound,
    GroupProposalResolved,
    MemberGroupNotBoundToRoom,
    NotAGroupMember,
    SubmissionPayloadInvalid,
)
from contexts.activities.domain.group_consent import (
    is_unreachable,
    parse_group_config,
    required_approvals,
)
from contexts.activities.domain.models import (
    ActivationStatus,
    ActivityType,
    GroupProposal,
    GroupProposalResolution,
    GroupProposalTally,
    ProposalStatus,
    VoteChoice,
)
from contexts.activities.infrastructure.repositories.optin_repo import (
    ProjectActivityTypeOptInRepository,
)
from contexts.activities.infrastructure.repositories.proposal_repo import (
    GroupProposalRepository,
    GroupProposalVoteRepository,
)
from contexts.activities.infrastructure.repositories.type_repo import ActivityTypeRepository
from contexts.conversation.interfaces.facade import ConversationFacade
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel import audit
from shared_kernel.auth.clients import now

#: How long an unresolved proposal stays open. A lesson-length window: long
#: enough that a group can talk it over, short enough that a proposal nobody
#: returned to does not sit across two classes waiting to become a submission.
#: The activation ending expires it sooner in practice (AC-9); this is the bound
#: for a round that runs long.
PROPOSAL_TTL = timedelta(hours=4)

#: Cap on the errors echoed back when a proposed payload fails its schema,
#: matching ``SubmissionService``'s so the two refusals read the same.
_MAX_ERRORS = 5


class GroupProposalService:
    def __init__(
        self,
        db: AsyncSession,
        *,
        activation_repo: ActivityActivationRepository,
    ) -> None:
        self._db = db
        self._proposals = GroupProposalRepository(db)
        self._votes = GroupProposalVoteRepository(db)
        self._type_repo = ActivityTypeRepository(db)
        self._optin_repo = ProjectActivityTypeOptInRepository(db)
        self._activation_repo = activation_repo
        self._submissions = SubmissionService(db, activation_repo=activation_repo)

    # -- Creation -----------------------------------------------------------

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        member_group_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        proposer_user_id: uuid.UUID,
        payload: dict[str, Any],
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GroupProposalResolution:
        """Open a proposal for this group's answer to the live round (AC-5).

        Five gates, in an order chosen so a probe learns the least:

        1. the type is reachable from the room's project ([R30.33]) -- a
           cross-tenant id 404s here, before anything about the room is revealed;
        2. the type declares ``group_config``, or it is not a group task;
        3. the round is live and is this type's;
        4. the group is a live group of this project, bound to this room, the
           room actually admits its members, and the proposer belongs to it;
        5. the payload already satisfies the schema.

        Gate 5 is last and is not an optimisation: a proposal nobody could accept
        should fail at proposal time rather than after three people have voted for
        it.

        IT SETTLES BEFORE IT RETURNS, and that is not an optimisation either.
        ``required_approvals`` is 1 whenever the fraction over the pinned set
        rounds down to one person -- 2/3 over a group whose only standing member
        is the proposer, 1/2 over two. Such a proposal is satisfied by the
        implicit approval below the moment it is written, and a ``create`` that
        only wrote it would leave the group's answer sitting `open` until the TTL
        expired it, with no error anywhere to say so.
        """
        activity_type = await resolve_reachable_type(
            type_reader=self._type_repo,
            optin_reader=self._optin_repo,
            activity_type_id=activity_type_id,
            project_id=project_id,
        )
        fraction = _group_fraction(activity_type)

        activation = await self._activation_repo.get_active_for_update(chatroom_id)
        if activation is None or activation.activity_type_id != activity_type_id:
            raise ActivityNotActive(str(activity_type_id))

        voters = await self._pin_voters(
            project_id=project_id,
            chatroom_id=chatroom_id,
            member_group_id=member_group_id,
            proposer_user_id=proposer_user_id,
        )

        errors = payload_errors(activity_type.payload_schema, dict(payload))
        if errors:
            raise SubmissionPayloadInvalid("; ".join(errors[:_MAX_ERRORS]))

        numerator, denominator = fraction
        required = required_approvals(numerator=numerator, denominator=denominator, group_size=len(voters))
        proposal_id = await self._proposals.create(
            chatroom_id=chatroom_id,
            activation_id=activation.id,
            activity_type_id=activity_type_id,
            member_group_id=member_group_id,
            proposer_user_id=proposer_user_id,
            payload=dict(payload),
            voter_user_ids=voters,
            required_approvals=required,
            expires_at=now() + PROPOSAL_TTL,
        )
        # The proposer's approval is implicit and recorded as a vote row, so the
        # tally is the whole story: a reader never has to add one to a count to
        # account for a person whose position is only implied by the row's
        # existence.
        await self._votes.cast(proposal_id=proposal_id, user_id=proposer_user_id, choice=VoteChoice.APPROVE)
        await self._audit(
            action="activity.proposal_created",
            proposal_id=proposal_id,
            actor_user_id=proposer_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
            metadata={
                "chatroom_id": str(chatroom_id),
                "activation_id": str(activation.id),
                "member_group_id": str(member_group_id),
                "voters": str(len(voters)),
                "required_approvals": str(required),
            },
        )
        created = await self._proposals.get(proposal_id)
        if created is None:  # pragma: no cover -- just inserted in this txn
            raise GroupProposalNotFound(str(proposal_id))
        return await self._settle(
            project_id=project_id,
            proposal=created,
            actor_user_id=proposer_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def _pin_voters(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        member_group_id: uuid.UUID,
        proposer_user_id: uuid.UUID,
    ) -> tuple[uuid.UUID, ...]:
        """The group's members at this moment, pinned as the ballot ([R30.41]).

        Four checks, all server-side, and each answered by the context that owns
        the fact. "Live group of this project" and "who is in it" are tenancy's;
        "bound to this room" and "does the room admit them" are conversation's.
        No cross-context SQL join is used for any of them, per [R30.09].

        Liveness and binding collapse into one refusal on purpose: a group id
        from another project is indistinguishable here from one that exists but
        was never bound, and telling them apart would confirm another tenant's
        group exists.

        THE ROOM-TIER CHECK IS THE SPEC'S "intersected with those who can read
        the room" (§5.2), expressed once over the room instead of once per
        member. Every pinned voter is a member of a group this room is bound to,
        so `_satisfies_room_flags` grants each of them exactly when the room
        grants the member-group tier at all -- which it does not when
        `allow_project_owners_only` is set, because that flag is exclusive and no
        other tier may widen it. A per-user intersection would answer the same
        question N times and then silently shrink the ballot; refusing the whole
        proposal says the true thing, which is that this room cannot host a group
        submission.

        The proposer is always in the returned set -- they are a member by the
        membership gate -- so the set is never empty and ``required_approvals``
        always has a positive size to work from.
        """
        tenancy = TenancyFacade(self._db)
        conversation = ConversationFacade(self._db)
        live = await tenancy.live_member_group_ids([member_group_id], project_id=project_id)
        bound = await conversation.chatroom_member_group_ids(chatroom_id)
        if member_group_id not in live or member_group_id not in bound:
            raise MemberGroupNotBoundToRoom(str(member_group_id))
        room = await conversation.get_chatroom(chatroom_id)
        if room is None or not room.allow_member_groups or room.allow_project_owners_only:
            raise MemberGroupNotBoundToRoom(str(member_group_id))
        if member_group_id not in await tenancy.member_group_ids_for_user(proposer_user_id):
            raise NotAGroupMember(str(member_group_id))

        members = await tenancy.member_group_user_ids(member_group_id)
        # The union with the proposer is not redundant with gate 3 even though
        # both reads apply the same standing filter: the two are separate
        # queries, and a membership change landing between them would otherwise
        # produce a ballot the proposer is not on -- a proposal nobody can
        # accept, made by someone who was a member when they made it.
        #
        # Sorted so the stored order is deterministic, which makes a stored
        # proposal diffable and a test's expectation stable. Order carries no
        # meaning otherwise -- membership of the set is the whole rule.
        return tuple(sorted(set(members) | {proposer_user_id}))

    # -- Voting -------------------------------------------------------------

    async def vote(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        proposal_id: uuid.UUID,
        voter_user_id: uuid.UUID,
        approve: bool,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GroupProposalResolution:
        """Record one pinned voter's decision and resolve if it settles the vote.

        The proposal row is locked ``FOR UPDATE`` first, so two votes arriving
        together cannot both read "one short of the bar" and both conclude the
        proposal is still open -- which would lose an acceptance, or produce two
        submissions for one group.

        A vote on a proposal that has already been decided is a 409 and the client
        refetches: the request was legal when it was rendered, and what changed is
        the group's state.
        """
        proposal = await self._locked(
            chatroom_id=chatroom_id, proposal_id=proposal_id, caller_user_id=voter_user_id
        )
        if proposal.status is not ProposalStatus.OPEN:
            raise GroupProposalResolved(str(proposal_id))

        await self._votes.cast(
            proposal_id=proposal_id,
            user_id=voter_user_id,
            choice=VoteChoice.APPROVE if approve else VoteChoice.REJECT,
        )
        await self._audit(
            action="activity.proposal_voted",
            proposal_id=proposal_id,
            actor_user_id=voter_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
            # The choice is NOT recorded here. An audit row is readable by an
            # org admin, and [R30.42] confines the per-person record to the
            # pinned voters and the room creator. That a vote happened is the
            # accountable fact; which way it went is the group's.
            metadata={
                "chatroom_id": str(chatroom_id),
                "member_group_id": str(proposal.member_group_id),
            },
        )
        return await self._settle(
            project_id=project_id,
            proposal=proposal,
            actor_user_id=voter_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )

    async def withdraw(
        self,
        *,
        chatroom_id: uuid.UUID,
        proposal_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> GroupProposalTally:
        """The proposer takes back their own open proposal.

        Only the proposer, and a 404 rather than a 403 for anyone else who can
        see it: a voter learning "you may not withdraw this" would learn nothing
        useful, and the partial unique means withdrawing is how a group gets to
        propose something different in the same round.
        """
        proposal = await self._locked(
            chatroom_id=chatroom_id, proposal_id=proposal_id, caller_user_id=caller_user_id
        )
        if proposal.proposer_user_id != caller_user_id:
            raise GroupProposalNotFound(str(proposal_id))
        if proposal.status is not ProposalStatus.OPEN:
            raise GroupProposalResolved(str(proposal_id))

        # Guarded on the return rather than assumed: the status check above ran
        # under this call's row lock, so the write must succeed -- and auditing a
        # withdrawal that did not happen would put a false fact on a trail whose
        # whole value is that it does not carry any.
        if await self._proposals.resolve(proposal_id, status=ProposalStatus.WITHDRAWN):
            await self._audit(
                action="activity.proposal_resolved",
                proposal_id=proposal_id,
                actor_user_id=caller_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
                metadata={
                    "chatroom_id": str(chatroom_id),
                    "member_group_id": str(proposal.member_group_id),
                    "status": ProposalStatus.WITHDRAWN.value,
                },
            )
        return await self._tally(proposal_id, include_votes_for=caller_user_id)

    # -- Resolution ---------------------------------------------------------

    async def _settle(
        self,
        *,
        project_id: uuid.UUID,
        proposal: GroupProposal,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> GroupProposalResolution:
        """Accept, reject, or leave open, from the votes cast so far.

        Rejection is "the threshold can no longer be reached", not "somebody
        voted no" (Q-4). Under a 2/3 rule those are different events, and
        treating the first dissent as fatal would silently implement unanimity.
        Under 1/1 they coincide, which is correct.
        """
        approvals, rejections = await self._votes.counts(proposal.id)
        undecided = max(0, len(proposal.voter_user_ids) - approvals - rejections)

        if approvals >= proposal.required_approvals:
            return await self._accept(
                project_id=project_id,
                proposal=proposal,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                request_id=request_id,
            )
        if is_unreachable(approvals=approvals, undecided=undecided, required=proposal.required_approvals):
            transitioned = await self._proposals.resolve(proposal.id, status=ProposalStatus.REJECTED)
            if transitioned:
                await self._audit(
                    action="activity.proposal_resolved",
                    proposal_id=proposal.id,
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    request_id=request_id,
                    metadata={
                        "chatroom_id": str(proposal.chatroom_id),
                        "member_group_id": str(proposal.member_group_id),
                        "status": ProposalStatus.REJECTED.value,
                    },
                )
            return GroupProposalResolution(
                tally=await self._tally(proposal.id, include_votes_for=actor_user_id),
                transitioned=transitioned,
            )
        return GroupProposalResolution(
            tally=await self._tally(proposal.id, include_votes_for=actor_user_id),
            transitioned=False,
        )

    async def _accept(
        self,
        *,
        project_id: uuid.UUID,
        proposal: GroupProposal,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
    ) -> GroupProposalResolution:
        """Turn an approved proposal into the group's submission (AC-10).

        Ordered so that nothing observable happens unless the whole thing does.
        The status flip runs FIRST and its ``status = 'open'`` guard is the
        mutual exclusion: a concurrent expiry sweep or a second vote that also
        reached the bar finds zero rows and returns without submitting, so one
        accepted proposal produces exactly one submission. Everything after it
        rides the caller's transaction, which the route commits once.

        The activation is re-read under ``FOR UPDATE`` and re-checked for
        liveness rather than trusted from creation time: a facilitator may have
        ended the round between the last vote and this one, and a submission into
        a finished round is precisely what AC-9 forbids.
        """
        activation = await self._activation_repo.get_active_for_update(proposal.chatroom_id)
        if (
            activation is None
            or activation.id != proposal.activation_id
            or activation.status is not ActivationStatus.ACTIVE
        ):
            raise ActivityNotActive(str(proposal.activity_type_id))

        if not await self._proposals.resolve(proposal.id, status=ProposalStatus.ACCEPTED):
            # Somebody else already resolved it in this instant. Report the
            # current state rather than raising: the caller's vote was recorded,
            # which is all they asked for.
            return GroupProposalResolution(
                tally=await self._tally(proposal.id, include_votes_for=actor_user_id),
                transitioned=False,
            )

        activity_type = await resolve_reachable_type(
            type_reader=self._type_repo,
            optin_reader=self._optin_repo,
            activity_type_id=proposal.activity_type_id,
            project_id=project_id,
        )
        group_names = await TenancyFacade(self._db).member_group_names([proposal.member_group_id])
        submission, signal_payload = await self._submissions.submit_for_group(
            activity_type=activity_type,
            activation=activation,
            member_group_id=proposal.member_group_id,
            group_name=group_names.get(proposal.member_group_id),
            proposer_user_id=proposal.proposer_user_id,
            payload=proposal.payload,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
        )
        await self._proposals.attach_submission(proposal.id, submission_id=submission.id)
        await self._audit(
            action="activity.proposal_resolved",
            proposal_id=proposal.id,
            actor_user_id=actor_user_id,
            actor_ip=actor_ip,
            request_id=request_id,
            metadata={
                "chatroom_id": str(proposal.chatroom_id),
                "member_group_id": str(proposal.member_group_id),
                "status": ProposalStatus.ACCEPTED.value,
                "submission_id": str(submission.id),
            },
        )
        return GroupProposalResolution(
            tally=await self._tally(proposal.id, include_votes_for=actor_user_id),
            transitioned=True,
            submission=submission,
            signal_payload=signal_payload,
        )

    async def expire_due_now(self, *, limit: int) -> Sequence[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
        """Expire proposals past their deadline (the worker sweep).

        The activation ending is the primary expiry and runs in the same
        transaction as the end (AC-9). This is the backstop for a room whose
        round nobody ever ended -- without it such a proposal would stay
        acceptable indefinitely.

        Not audited per row: nobody performed this act, and an audit trail of
        several hundred rows attributed to no actor is noise. The count is the
        worker's log line.
        """
        return await self._proposals.expire_due(cutoff=now(), limit=limit)

    # -- Reads --------------------------------------------------------------

    async def list_open_for_caller(
        self,
        *,
        project_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        activation_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        caller_is_room_creator: bool,
    ) -> Sequence[GroupProposalTally]:
        """The live proposals this caller may see for one round ([R30.42]).

        A participant sees their own groups' proposals; the room creator sees
        every group's, because the vote record is an accountability record for
        the group AND the teacher. Nobody else sees any, and an agent never calls
        this at all.

        Votes are attached only for a caller entitled to them, by the same rule
        the single read uses -- so a listing can never be the looser surface.
        """
        if caller_is_room_creator:
            bound = await ConversationFacade(self._db).chatroom_member_group_ids(chatroom_id)
            visible = await TenancyFacade(self._db).live_member_group_ids(list(bound), project_id=project_id)
        else:
            mine = await TenancyFacade(self._db).member_group_ids_for_user(caller_user_id)
            bound = await ConversationFacade(self._db).chatroom_member_group_ids(chatroom_id)
            visible = mine & bound
        proposals = await self._proposals.list_open_for_groups(
            activation_id=activation_id, member_group_ids=sorted(visible)
        )
        return [
            await self._tally(
                p.id,
                include_votes_for=caller_user_id,
                caller_is_room_creator=caller_is_room_creator,
                proposal=p,
            )
            for p in proposals
        ]

    async def get_tally(
        self,
        *,
        chatroom_id: uuid.UUID,
        proposal_id: uuid.UUID,
        caller_user_id: uuid.UUID,
        caller_is_room_creator: bool,
    ) -> GroupProposalTally:
        """One proposal with its counts, and its votes if this caller may see them.

        AC-12. A caller who is neither a pinned voter nor the room creator gets
        ``GroupProposalNotFound`` rather than a redacted body -- confirming a
        proposal exists is itself a disclosure about who is grouped with whom.
        """
        proposal = await self._proposals.get(proposal_id)
        if proposal is None or proposal.chatroom_id != chatroom_id:
            raise GroupProposalNotFound(str(proposal_id))
        if not caller_is_room_creator and not proposal.may_vote(caller_user_id):
            raise GroupProposalNotFound(str(proposal_id))
        return await self._tally(
            proposal_id,
            include_votes_for=caller_user_id,
            caller_is_room_creator=caller_is_room_creator,
            proposal=proposal,
        )

    # -- Internals ----------------------------------------------------------

    async def _locked(
        self, *, chatroom_id: uuid.UUID, proposal_id: uuid.UUID, caller_user_id: uuid.UUID
    ) -> GroupProposal:
        """A proposal this caller holds a ballot on, locked for update.

        Wrong room, missing, and "you were not pinned" all collapse into the same
        404 (AC-6, AC-12). The pin is what is checked, not membership today:
        adding someone to the group mid-vote must not hand them a ballot, and
        removing someone must not take back a vote they already cast.
        """
        proposal = await self._proposals.lock_for_update(proposal_id)
        if proposal is None or proposal.chatroom_id != chatroom_id:
            raise GroupProposalNotFound(str(proposal_id))
        if not proposal.may_vote(caller_user_id):
            raise GroupProposalNotFound(str(proposal_id))
        return proposal

    async def _tally(
        self,
        proposal_id: uuid.UUID,
        *,
        include_votes_for: uuid.UUID,
        caller_is_room_creator: bool = False,
        proposal: GroupProposal | None = None,
    ) -> GroupProposalTally:
        """Counts always; the per-person votes only for an entitled caller.

        One read model for both audiences ([R30.42]) rather than two shapes that
        could drift: ``votes`` is simply empty for a caller who may not see them,
        and the entitlement rule lives here so no caller can assemble a tally
        without passing it.
        """
        # A caller passes ``proposal`` only when it has just read the row itself
        # (the two read paths); everything on the write side passes nothing and
        # gets a fresh read, which is what makes a just-resolved proposal report
        # its new status rather than the pre-lock snapshot the writer still
        # holds. Re-reading a supplied row on top of that would cost the listing
        # one query per proposal and answer the same thing.
        current = proposal if proposal is not None else await self._proposals.get(proposal_id)
        if current is None:  # pragma: no cover -- read inside the writing txn
            raise GroupProposalNotFound(str(proposal_id))

        approvals, rejections = await self._votes.counts(proposal_id)
        undecided = max(0, len(current.voter_user_ids) - approvals - rejections)
        may_see_votes = caller_is_room_creator or current.may_vote(include_votes_for)
        votes = tuple(await self._votes.list_for_proposal(proposal_id)) if may_see_votes else ()
        return GroupProposalTally(
            proposal=current,
            approvals=approvals,
            rejections=rejections,
            undecided=undecided,
            votes=votes,
        )

    async def _audit(
        self,
        *,
        action: str,
        proposal_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
        metadata: dict[str, str],
    ) -> None:
        """Emit one proposal audit event on the caller's transaction.

        No payload content ever enters this metadata (§7 of the dossier). The
        answer a group agreed on lives in the submission row; an audit trail
        readable by an org admin is not where it belongs.
        """
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action=action,
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="activity_group_proposal",
                resource_id=proposal_id,
                metadata=dict(metadata),
                request_id=request_id,
            ),
        )


def _group_fraction(activity_type: ActivityType) -> tuple[int, int]:
    """The type's consent fraction, or a refusal that it is not a group task.

    A type with no ``group_config`` is individual-only ([R30.40]), which is a 409
    rather than a 404: the round is running and its schema is on the caller's
    screen, so the type is legitimately visible -- what it is not is something a
    group may answer together.
    """
    try:
        fraction = parse_group_config(activity_type.group_config)
    except ValueError as exc:
        # A stored fraction that no longer parses must not fall back to some
        # default threshold; there is no safe default for a consent rule.
        raise ActivityTypeNotGroupSubmittable(str(activity_type.id)) from exc
    if fraction is None:
        raise ActivityTypeNotGroupSubmittable(str(activity_type.id))
    return fraction


__all__ = ["PROPOSAL_TTL", "GroupProposalService"]
