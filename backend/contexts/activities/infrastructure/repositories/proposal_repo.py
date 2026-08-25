"""Async repositories for group proposals and their votes (§30, [R30.41], [R30.42]).

Two repositories in one module because neither is meaningful without the other: a
proposal's terminal status is a function of its votes, and a vote outside a
proposal is nothing. Caller owns commit, as everywhere else in this context.

THE PINNED SET IS STORED AS TEXT, READ AS UUIDs. ``voter_user_ids`` is a JSONB
array, so it round-trips through JSON's only scalar for an id. The conversion
lives here rather than in the service, so nothing above this layer ever sees a
voter as a string and compares it to a ``uuid.UUID`` that will never equal it.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.activities.domain.errors import GroupProposalAlreadyOpen
from contexts.activities.domain.models import (
    GroupProposal,
    ProposalStatus,
    ProposalVote,
    VoteChoice,
)
from contexts.activities.infrastructure import tables as t
from shared_kernel.auth.clients import now
from shared_kernel.db.rowcount import rowcount

_PROP = t.activity_group_proposals
_VOTE = t.activity_group_proposal_votes

_PROP_COLS = (
    _PROP.c.id,
    _PROP.c.chatroom_id,
    _PROP.c.activation_id,
    _PROP.c.activity_type_id,
    _PROP.c.member_group_id,
    _PROP.c.proposer_user_id,
    _PROP.c.payload,
    _PROP.c.voter_user_ids,
    _PROP.c.required_approvals,
    _PROP.c.status,
    _PROP.c.created_at,
    _PROP.c.expires_at,
    _PROP.c.resolved_at,
    _PROP.c.submission_id,
)


def _voters(raw: Any) -> tuple[uuid.UUID, ...]:
    """The pinned set as UUIDs.

    A malformed entry is dropped rather than raised on. The set is written by
    this application from ids it read out of the database, so a bad value means
    the row was tampered with -- and the failure mode that matters there is a
    person silently gaining a ballot, which dropping the entry cannot cause.
    """
    out: list[uuid.UUID] = []
    for item in raw or []:
        try:
            out.append(uuid.UUID(str(item)))
        except (ValueError, AttributeError, TypeError):
            continue
    return tuple(out)


def _row_to_proposal(row: Any) -> GroupProposal:
    return GroupProposal(
        id=row.id,
        chatroom_id=row.chatroom_id,
        activation_id=row.activation_id,
        activity_type_id=row.activity_type_id,
        member_group_id=row.member_group_id,
        proposer_user_id=row.proposer_user_id,
        payload=dict(row.payload or {}),
        voter_user_ids=_voters(row.voter_user_ids),
        required_approvals=int(row.required_approvals),
        status=ProposalStatus(row.status),
        created_at=row.created_at,
        expires_at=row.expires_at,
        resolved_at=row.resolved_at,
        submission_id=row.submission_id,
    )


def _row_to_vote(row: Any) -> ProposalVote:
    return ProposalVote(
        proposal_id=row.proposal_id,
        user_id=row.user_id,
        choice=VoteChoice(row.choice),
        created_at=row.created_at,
    )


class GroupProposalRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self,
        *,
        chatroom_id: uuid.UUID,
        activation_id: uuid.UUID,
        activity_type_id: uuid.UUID,
        member_group_id: uuid.UUID,
        proposer_user_id: uuid.UUID,
        payload: dict[str, Any],
        voter_user_ids: Sequence[uuid.UUID],
        required_approvals: int,
        expires_at: dt.datetime,
    ) -> uuid.UUID:
        """Insert an open proposal and return its id.

        A concurrent second proposal for the same (activation, group) lands on
        ``uq_activity_group_proposals_open`` and is lifted into the domain 409.
        The index, not a preceding read, is what decides the loser: two racers
        both pass a read-then-write guard.

        Any *other* IntegrityError is re-raised as its true cause rather than
        mismapped to "already open".
        """
        try:
            row = await self._db.execute(
                _PROP.insert()
                .values(
                    chatroom_id=chatroom_id,
                    activation_id=activation_id,
                    activity_type_id=activity_type_id,
                    member_group_id=member_group_id,
                    proposer_user_id=proposer_user_id,
                    payload=payload,
                    voter_user_ids=[str(u) for u in voter_user_ids],
                    required_approvals=required_approvals,
                    status=ProposalStatus.OPEN.value,
                    expires_at=expires_at,
                )
                .returning(_PROP.c.id)
            )
        except IntegrityError as exc:
            if "uq_activity_group_proposals_open" in str(exc.orig or exc).lower():
                raise GroupProposalAlreadyOpen(
                    f"group {member_group_id} already has an open proposal for {activation_id}"
                ) from exc
            raise
        return uuid.UUID(str(row.scalar_one()))

    async def get(self, proposal_id: uuid.UUID) -> GroupProposal | None:
        row = (await self._db.execute(sa.select(*_PROP_COLS).where(_PROP.c.id == proposal_id))).first()
        return _row_to_proposal(row) if row is not None else None

    async def lock_for_update(self, proposal_id: uuid.UUID) -> GroupProposal | None:
        """Load a proposal under ``SELECT ... FOR UPDATE``.

        Every vote takes this lock before counting, so two votes arriving
        together cannot both read "one short of the bar" and both decide the
        proposal is still open -- which would either lose the acceptance or
        produce two submissions for one group.
        """
        row = (
            await self._db.execute(sa.select(*_PROP_COLS).where(_PROP.c.id == proposal_id).with_for_update())
        ).first()
        return _row_to_proposal(row) if row is not None else None

    async def get_open_for_group(
        self, *, activation_id: uuid.UUID, member_group_id: uuid.UUID
    ) -> GroupProposal | None:
        """The group's live proposal for this round, or ``None``.

        At most one can exist, by the partial unique -- so this is a lookup, not
        a listing that happens to be short.
        """
        row = (
            await self._db.execute(
                sa.select(*_PROP_COLS).where(
                    sa.and_(
                        _PROP.c.activation_id == activation_id,
                        _PROP.c.member_group_id == member_group_id,
                        _PROP.c.status == ProposalStatus.OPEN.value,
                    )
                )
            )
        ).first()
        return _row_to_proposal(row) if row is not None else None

    async def list_open_for_groups(
        self, *, activation_id: uuid.UUID, member_group_ids: Sequence[uuid.UUID]
    ) -> Sequence[GroupProposal]:
        """Every live proposal of this round belonging to one of ``member_group_ids``.

        The participant's read: a student may belong to more than one bound group
        in a room, and the panel has to show whichever of them is deciding. An
        empty id list returns nothing rather than everything -- the degenerate
        case of a caller in no group must not widen into the whole room.
        """
        groups = list(member_group_ids)
        if not groups:
            return []
        rows = (
            await self._db.execute(
                sa.select(*_PROP_COLS)
                .where(
                    sa.and_(
                        _PROP.c.activation_id == activation_id,
                        _PROP.c.member_group_id.in_(groups),
                        _PROP.c.status == ProposalStatus.OPEN.value,
                    )
                )
                .order_by(_PROP.c.created_at.desc(), _PROP.c.id.desc())
            )
        ).all()
        return [_row_to_proposal(r) for r in rows]

    async def resolve(
        self,
        proposal_id: uuid.UUID,
        *,
        status: ProposalStatus,
        submission_id: uuid.UUID | None = None,
        resolved_at: dt.datetime | None = None,
    ) -> bool:
        """Move an open proposal to a terminal status; returns whether it moved.

        The ``status = 'open'`` guard is what makes every resolution path
        idempotent and mutually exclusive: an expiry sweep racing an acceptance
        cannot un-accept it, and a repeat call is a no-op rather than a second
        broadcast claiming the vote just finished.
        """
        if status is ProposalStatus.OPEN:  # pragma: no cover -- caller error
            raise ValueError("resolve() moves a proposal to a terminal status")
        result = await self._db.execute(
            _PROP.update()
            .where(sa.and_(_PROP.c.id == proposal_id, _PROP.c.status == ProposalStatus.OPEN.value))
            .values(
                status=status.value,
                submission_id=submission_id,
                resolved_at=resolved_at or now(),
            )
        )
        return bool(rowcount(result))

    async def attach_submission(self, proposal_id: uuid.UUID, *, submission_id: uuid.UUID) -> bool:
        """Stamp the submission an accepted proposal produced.

        Separate from :meth:`resolve` rather than a second argument to it,
        because the two run at different moments and under different guards: the
        status flip has to happen BEFORE the submission exists (it is the mutual
        exclusion that makes one accepted proposal produce one submission), and
        this stamp has to happen after. Guarded on ``accepted`` so it cannot
        attach a submission to a proposal that was expired or withdrawn.
        """
        result = await self._db.execute(
            _PROP.update()
            .where(
                sa.and_(
                    _PROP.c.id == proposal_id,
                    _PROP.c.status == ProposalStatus.ACCEPTED.value,
                    _PROP.c.submission_id.is_(None),
                )
            )
            .values(submission_id=submission_id)
        )
        return bool(rowcount(result))

    async def expire_open_for_activation(self, activation_id: uuid.UUID) -> Sequence[uuid.UUID]:
        """Expire every open proposal of one round, returning the ids that moved.

        Correctness, not housekeeping (AC-9): [R30.22] closes a round's sessions,
        and a proposal that outlived its activation would otherwise accept later
        and write a submission into a finished round.
        """
        result = await self._db.execute(
            _PROP.update()
            .where(
                sa.and_(
                    _PROP.c.activation_id == activation_id,
                    _PROP.c.status == ProposalStatus.OPEN.value,
                )
            )
            .values(status=ProposalStatus.EXPIRED.value, resolved_at=now())
            .returning(_PROP.c.id)
        )
        return [row.id for row in result]

    async def expire_due(
        self, *, cutoff: dt.datetime, limit: int
    ) -> Sequence[tuple[uuid.UUID, uuid.UUID, uuid.UUID]]:
        """Expire open proposals past their deadline (the worker sweep).

        Returns ``(proposal_id, chatroom_id, member_group_id)`` per expired row so
        the worker can broadcast per room. Bounded by ``limit`` so one sweep
        cannot hold a lock over an unbounded set; the next tick takes the rest.
        """
        due = (
            sa.select(_PROP.c.id)
            .where(sa.and_(_PROP.c.status == ProposalStatus.OPEN.value, _PROP.c.expires_at <= cutoff))
            .order_by(_PROP.c.expires_at)
            .limit(limit)
            .scalar_subquery()
        )
        result = await self._db.execute(
            _PROP.update()
            .where(_PROP.c.id.in_(due))
            .values(status=ProposalStatus.EXPIRED.value, resolved_at=now())
            .returning(_PROP.c.id, _PROP.c.chatroom_id, _PROP.c.member_group_id)
        )
        return [(row.id, row.chatroom_id, row.member_group_id) for row in result]


class GroupProposalVoteRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def cast(self, *, proposal_id: uuid.UUID, user_id: uuid.UUID, choice: VoteChoice) -> None:
        """Record one voter's decision, replacing their previous one.

        An upsert on the ``(proposal_id, user_id)`` primary key rather than an
        insert: a person changing their mind while the vote is open is a change
        to their one ballot, and a second row would let a single voter carry the
        proposal on their own.
        """
        stmt = pg_insert(_VOTE).values(proposal_id=proposal_id, user_id=user_id, choice=choice.value)
        await self._db.execute(
            stmt.on_conflict_do_update(
                index_elements=[_VOTE.c.proposal_id, _VOTE.c.user_id],
                set_={"choice": stmt.excluded.choice, "created_at": now()},
            )
        )

    async def counts(self, proposal_id: uuid.UUID) -> tuple[int, int]:
        """``(approvals, rejections)`` from a single grouped read.

        An aggregate ``FILTER`` clause, which is PostgreSQL-specific and
        therefore carries a ``db``-tier test (backend/CLAUDE.md).
        """
        row = (
            await self._db.execute(
                sa.select(
                    sa.func.count().filter(_VOTE.c.choice == VoteChoice.APPROVE.value).label("approvals"),
                    sa.func.count().filter(_VOTE.c.choice == VoteChoice.REJECT.value).label("rejections"),
                ).where(_VOTE.c.proposal_id == proposal_id)
            )
        ).one()
        return int(row.approvals or 0), int(row.rejections or 0)

    async def list_for_proposal(self, proposal_id: uuid.UUID) -> Sequence[ProposalVote]:
        """Every vote on one proposal, oldest first.

        The per-person record ([R30.42]): readable only by the pinned voters and
        the room creator, which the service enforces -- this method answers what
        was voted, never who may see it.
        """
        rows = (
            await self._db.execute(
                sa.select(_VOTE.c.proposal_id, _VOTE.c.user_id, _VOTE.c.choice, _VOTE.c.created_at)
                .where(_VOTE.c.proposal_id == proposal_id)
                .order_by(_VOTE.c.created_at, _VOTE.c.user_id)
            )
        ).all()
        return [_row_to_vote(r) for r in rows]


__all__ = ["GroupProposalRepository", "GroupProposalVoteRepository"]
