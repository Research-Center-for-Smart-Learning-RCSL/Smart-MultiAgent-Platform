"""MAJORITY approval early-decision + voter-membership validation (G.6)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from contexts.orchestration.application.approval_service import ApprovalService
from contexts.orchestration.domain.models import (
    Approval,
    ApprovalMode,
    ApprovalState,
    ApprovalVote,
)


def _approval(approvers: list[uuid.UUID], leader: uuid.UUID) -> Approval:
    return Approval(
        id=uuid.uuid4(),
        workflow_run_id=uuid.uuid4(),
        mode=ApprovalMode.MAJORITY,
        leader_agent_id=leader,
        approver_agent_ids=tuple(approvers),
        timeout_seconds=300,
        state=ApprovalState.PENDING,
        started_at=datetime.now(UTC),
        ended_at=None,
    )


def _vote(approval_id: uuid.UUID, voter: uuid.UUID, v: bool) -> ApprovalVote:
    return ApprovalVote(
        approval_id=approval_id,
        voter_agent_id=voter,
        vote=v,
        rationale=None,
        cast_at=datetime.now(UTC),
    )


def test_majority_resolves_early_on_approve() -> None:
    approvers = [uuid.uuid4() for _ in range(5)]
    appr = _approval(approvers, approvers[0])
    # 3 of 5 approve, 2 have not voted -> already a strict majority -> APPROVED.
    votes = [_vote(appr.id, approvers[i], True) for i in range(3)]
    assert ApprovalService._evaluate_votes(appr, votes) == ApprovalState.APPROVED


def test_majority_resolves_early_on_reject() -> None:
    approvers = [uuid.uuid4() for _ in range(5)]
    appr = _approval(approvers, approvers[0])
    votes = [_vote(appr.id, approvers[i], False) for i in range(3)]
    assert ApprovalService._evaluate_votes(appr, votes) == ApprovalState.REJECTED


def test_majority_waits_when_undecided() -> None:
    approvers = [uuid.uuid4() for _ in range(5)]
    appr = _approval(approvers, approvers[0])
    # 2 approve, 1 reject, 2 outstanding -> not yet locked.
    votes = [
        _vote(appr.id, approvers[0], True),
        _vote(appr.id, approvers[1], True),
        _vote(appr.id, approvers[2], False),
    ]
    assert ApprovalService._evaluate_votes(appr, votes) is None


def test_majority_even_tie_broken_by_leader() -> None:
    approvers = [uuid.uuid4() for _ in range(4)]
    leader = approvers[0]
    appr = _approval(approvers, leader)
    # 2-2 split, all voted; leader voted approve -> APPROVED.
    votes = [
        _vote(appr.id, approvers[0], True),  # leader
        _vote(appr.id, approvers[1], True),
        _vote(appr.id, approvers[2], False),
        _vote(appr.id, approvers[3], False),
    ]
    assert ApprovalService._evaluate_votes(appr, votes) == ApprovalState.APPROVED


def test_revote_does_not_double_count() -> None:
    approvers = [uuid.uuid4() for _ in range(3)]
    appr = _approval(approvers, approvers[0])
    # Approver 0 flips their vote; last ballot wins, so only 1 approve so far.
    votes = [
        _vote(appr.id, approvers[0], True),
        _vote(appr.id, approvers[0], False),
    ]
    assert ApprovalService._evaluate_votes(appr, votes) is None


@pytest.mark.asyncio
async def test_cast_vote_rejects_non_approver() -> None:
    approvers = [uuid.uuid4() for _ in range(3)]
    appr = _approval(approvers, approvers[0])
    svc = ApprovalService(db=AsyncMock())
    svc._approvals = AsyncMock()
    svc._approvals.get.return_value = appr
    svc._votes = AsyncMock()
    with pytest.raises(ValueError, match="not an approver"):
        await svc.cast_vote(approval_id=appr.id, voter_agent_id=uuid.uuid4(), vote=True)
    svc._votes.cast.assert_not_called()
