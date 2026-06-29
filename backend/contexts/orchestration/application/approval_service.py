"""Approval gate service (G.6 — R15.10–R15.14).

Agent-only approval gates with single/majority/consensus modes.
Resolution rules:
- single: leader's vote decides; others are advisory.
- majority: >50% of approvers must approve; ties broken by leader.
- consensus: all must converge; timeout falls to leader's verdict.

SoC:
- Domain models/enums → ``domain.models``
- DB access → ``infrastructure.repositories``
- Pub/sub → ``shared_kernel.realtime.pubsub``
- Audit → ``shared_kernel.audit``
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.conversation.infrastructure.channels import room_channel
from contexts.orchestration.domain.models import (
    Approval,
    ApprovalGateConfig,
    ApprovalMode,
    ApprovalState,
    ApprovalVote,
)
from contexts.orchestration.infrastructure.metrics import (
    APPROVAL_RESOLUTIONS,
)
from contexts.orchestration.infrastructure.repositories import (
    ApprovalRepository,
    ApprovalVoteRepository,
)
from contexts.workflow.infrastructure.channels import workflow_channel
from shared_kernel import audit
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)

# Small delay so drive_approver_turn lands after create_gate's enclosing
# transaction commits (the executor commits just after the node returns). The
# worker also re-checks/retries when the row is not yet visible, so this only
# trims the first wasted attempt.
_APPROVER_TURN_DISPATCH_DELAY_S = 2


class ApprovalService:
    """Application-level approval gate orchestration (G.6)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._approvals = ApprovalRepository(db)
        self._votes = ApprovalVoteRepository(db)

    # ------------------------------------------------------------------
    # Create gate
    # ------------------------------------------------------------------

    async def create_gate(
        self,
        *,
        workflow_run_id: uuid.UUID,
        config: ApprovalGateConfig,
        chatroom_id: uuid.UUID | None = None,
    ) -> Approval:
        approval_id = uuid.uuid4()
        approval = await self._approvals.insert(
            id=approval_id,
            workflow_run_id=workflow_run_id,
            mode=config.mode,
            leader_agent_id=config.leader_agent_id,
            approver_agent_ids=list(config.approvers),
            timeout_seconds=config.timeout_seconds,
        )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="approval.requested",
                resource_type="approval",
                resource_id=approval_id,
                metadata={
                    "workflow_run_id": str(workflow_run_id),
                    "mode": config.mode.value,
                    "leader_agent_id": str(config.leader_agent_id),
                    "approver_count": len(config.approvers),
                    "timeout_seconds": config.timeout_seconds,
                },
            ),
        )

        if chatroom_id:
            pub = Publisher(room_channel(chatroom_id))
            await pub.emit(
                "approval.requested",
                {
                    "approval_id": str(approval_id),
                    "mode": config.mode.value,
                    "leader_agent_id": str(config.leader_agent_id),
                    "approver_agent_ids": [str(a) for a in config.approvers],
                    "timeout_seconds": config.timeout_seconds,
                    "question": config.question,
                },
            )

        wf_pub = Publisher(workflow_channel(workflow_run_id))
        await wf_pub.emit(
            "approval.requested",
            {
                "approval_id": str(approval_id),
            },
        )

        # K.3: notify approver agents so their next turn exposes cast_approval_vote,
        # and arm the timeout as a deferred job. Approver notifies are best-effort
        # (a missed one still resolves via timeout), but the timeout arm itself is
        # load-bearing for liveness and raises on failure (see _notify_and_arm).
        await self._notify_and_arm(
            approval_id=approval_id,
            config=config,
            workflow_run_id=workflow_run_id,
            chatroom_id=chatroom_id,
        )

        return approval

    async def _notify_and_arm(
        self,
        *,
        approval_id: uuid.UUID,
        config: ApprovalGateConfig,
        workflow_run_id: uuid.UUID,
        chatroom_id: uuid.UUID | None,
    ) -> None:
        from datetime import timedelta

        from contexts.orchestration.infrastructure import pending_notify
        from shared_kernel.queue import enqueue

        note = {
            "kind": "approval_request",
            "approval_id": str(approval_id),
            "mode": config.mode.value,
            "workflow_run_id": str(workflow_run_id),
            "chatroom_id": str(chatroom_id) if chatroom_id else None,
            # What is being voted on — without it the approver only sees an
            # opaque approval_id and cannot make an informed decision.
            "question": config.question,
        }
        for approver in config.approvers:
            try:
                await pending_notify.push(approver, dict(note))
                # Pending notifies are only drained at the approver's *next*
                # turn, and nothing else causes one for a headless approver —
                # without this the gate always falls to the timeout port.
                # Drive one headless turn per approver; the drained note
                # supplies the cast_approval_vote tool. Deferred so the job runs
                # AFTER this gate's transaction commits — create_gate runs inside
                # the caller's (executor's) transaction, so a non-deferred job
                # would observe no approval row and skip every approver.
                await enqueue(
                    "drive_approver_turn",
                    str(approver),
                    str(approval_id),
                    str(chatroom_id) if chatroom_id else None,
                    _defer_by=timedelta(seconds=_APPROVER_TURN_DISPATCH_DELAY_S),
                )
            except Exception:
                _log.warning(
                    "approval %s: approver %s notify/turn dispatch failed",
                    approval_id,
                    approver,
                    exc_info=True,
                )
        # The timeout job is the gate's liveness backstop: in MAJORITY/CONSENSUS
        # a single silent approver otherwise parks the run forever. It is NOT
        # best-effort — if it cannot be armed, fail gate creation so the caller
        # rolls back rather than creating a gate that may never resolve.
        await enqueue(
            "approval_timeout",
            str(approval_id),
            str(chatroom_id) if chatroom_id else None,
            _defer_by=timedelta(seconds=config.timeout_seconds),
        )

    # ------------------------------------------------------------------
    # Cast vote
    # ------------------------------------------------------------------

    async def cast_vote(
        self,
        *,
        approval_id: uuid.UUID,
        voter_agent_id: uuid.UUID,
        vote: bool,
        rationale: str | None = None,
        chatroom_id: uuid.UUID | None = None,
    ) -> ApprovalVote:
        approval = await self._approvals.get(approval_id)
        if approval is None:
            raise ValueError(f"approval {approval_id} not found")
        if approval.state != ApprovalState.PENDING:
            raise ValueError(f"approval {approval_id} already resolved: {approval.state.value}")
        # Only designated approvers may cast a ballot. Non-approver votes are
        # ignored by _evaluate_votes anyway, but persisting them is audit noise
        # and a foothold for tally-skewing — reject at the boundary.
        if voter_agent_id not in set(approval.approver_agent_ids):
            raise ValueError(f"agent {voter_agent_id} is not an approver of {approval_id}")

        ballot = await self._votes.cast(
            approval_id=approval_id,
            voter_agent_id=voter_agent_id,
            vote=vote,
            rationale=rationale,
        )

        await self._try_resolve(approval, chatroom_id=chatroom_id)
        return ballot

    # ------------------------------------------------------------------
    # Timeout (called by scheduled job)
    # ------------------------------------------------------------------

    async def handle_timeout(
        self,
        approval_id: uuid.UUID,
        *,
        chatroom_id: uuid.UUID | None = None,
    ) -> ApprovalState | None:
        """Resolve a still-pending gate to TIMEOUT_LEADER.

        Returns the resolved (or already-resolved) state, or None when the
        approval does not exist.
        """
        approval = await self._approvals.get(approval_id)
        if approval is None:
            return None
        if approval.state != ApprovalState.PENDING:
            return approval.state

        leader_votes = [
            v
            for v in await self._votes.list_for_approval(approval_id)
            if v.voter_agent_id == approval.leader_agent_id
        ]
        if leader_votes and leader_votes[-1].vote:
            leader_verdict = "approved"
        elif leader_votes:
            leader_verdict = "rejected"
        else:
            leader_verdict = "no_vote"

        resolved_state = ApprovalState.TIMEOUT_LEADER
        if not await self._approvals.update_state(approval_id, resolved_state):
            # A vote resolved the gate between our read and the CAS — that
            # resolution path owns the audit/publish/resume side effects.
            refreshed = await self._approvals.get(approval_id)
            return refreshed.state if refreshed else None

        APPROVAL_RESOLUTIONS.labels(
            mode=approval.mode.value,
            outcome=resolved_state.value,
        ).inc()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="approval.resolved",
                resource_type="approval",
                resource_id=approval_id,
                metadata={
                    "state": resolved_state.value,
                    "leader_verdict": leader_verdict,
                    "reason": "timeout",
                },
            ),
        )

        await self._publish_resolved(
            approval,
            resolved_state,
            chatroom_id=chatroom_id,
        )
        await self._enqueue_workflow_resume(approval_id)
        return resolved_state

    # ------------------------------------------------------------------
    # Resolution logic
    # ------------------------------------------------------------------

    async def _try_resolve(
        self,
        approval: Approval,
        *,
        chatroom_id: uuid.UUID | None = None,
    ) -> None:
        votes = await self._votes.list_for_approval(approval.id)
        resolved_state = self._evaluate_votes(approval, votes)
        if resolved_state is None:
            return

        if not await self._approvals.update_state(approval.id, resolved_state):
            # Already resolved by a concurrent vote or the timeout job — the
            # winning path owns the audit/publish/resume side effects.
            return

        APPROVAL_RESOLUTIONS.labels(
            mode=approval.mode.value,
            outcome=resolved_state.value,
        ).inc()

        meta: dict[str, Any] = {
            "state": resolved_state.value,
            "vote_count": len(votes),
            "approve_count": sum(1 for v in votes if v.vote),
            "reject_count": sum(1 for v in votes if not v.vote),
        }
        if resolved_state == ApprovalState.TIMEOUT_LEADER:
            leader_votes = [v for v in votes if v.voter_agent_id == approval.leader_agent_id]
            if leader_votes and leader_votes[-1].vote:
                meta["leader_verdict"] = "approved"
            elif leader_votes:
                meta["leader_verdict"] = "rejected"
            else:
                meta["leader_verdict"] = "no_vote"
            meta["reason"] = "consensus_diverged"
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="approval.resolved",
                resource_type="approval",
                resource_id=approval.id,
                metadata=meta,
            ),
        )

        await self._publish_resolved(
            approval,
            resolved_state,
            chatroom_id=chatroom_id,
        )
        await self._enqueue_workflow_resume(approval.id)

    async def _enqueue_workflow_resume(self, approval_id: uuid.UUID) -> None:
        """Ask the engine to resume a workflow run parked on this gate (K.4).

        Best-effort: a non-workflow (room-only) gate has no ``wf:approval:{id}``
        claim key, so the resume task no-ops. The task itself bridges the commit
        gap when the gate resolved inside a long agent turn (vote path)."""
        from shared_kernel.queue import enqueue

        try:
            await enqueue("workflow_resume_approval", str(approval_id))
        except Exception:
            _log.warning("approval %s: workflow resume dispatch failed", approval_id, exc_info=True)

    @staticmethod
    def _evaluate_votes(
        approval: Approval,
        votes: list[ApprovalVote],
    ) -> ApprovalState | None:
        """Pure resolution evaluation. Returns None if not yet decidable."""
        approver_set = set(approval.approver_agent_ids)
        approver_votes = [v for v in votes if v.voter_agent_id in approver_set]

        if approval.mode == ApprovalMode.SINGLE:
            leader_votes = [v for v in approver_votes if v.voter_agent_id == approval.leader_agent_id]
            if not leader_votes:
                return None
            return ApprovalState.APPROVED if leader_votes[-1].vote else ApprovalState.REJECTED

        if approval.mode == ApprovalMode.MAJORITY:
            # Count the latest ballot per approver so a re-vote does not skew the
            # tally. Last-wins relies on list_for_approval being cast-ordered.
            latest: dict[uuid.UUID, bool] = {}
            for v in approver_votes:
                latest[v.voter_agent_id] = v.vote
            n = len(approver_set)
            approves = sum(1 for vote in latest.values() if vote)
            rejects = len(latest) - approves
            # Early decision: once a strict majority of *all* approvers has voted
            # one way, remaining stragglers cannot change the outcome — resolve
            # immediately instead of stalling on a silent approver.
            if approves * 2 > n:
                return ApprovalState.APPROVED
            if rejects * 2 > n:
                return ApprovalState.REJECTED
            if len(latest) < n:
                return None
            # All voted, no strict majority either way (only possible for even n,
            # e.g. 2-2). Leader breaks the tie.
            leader_votes = [v for v in approver_votes if v.voter_agent_id == approval.leader_agent_id]
            if leader_votes:
                return ApprovalState.APPROVED if leader_votes[-1].vote else ApprovalState.REJECTED
            return None

        if approval.mode == ApprovalMode.CONSENSUS:
            if len(approver_votes) < len(approver_set):
                return None
            all_approve = all(v.vote for v in approver_votes)
            all_reject = all(not v.vote for v in approver_votes)
            if all_approve:
                return ApprovalState.APPROVED
            if all_reject:
                return ApprovalState.REJECTED
            # All voted but no consensus — agents cannot re-converge (notifications
            # are one-shot), so resolve immediately via the leader's verdict
            # instead of waiting for the full timeout period.
            return ApprovalState.TIMEOUT_LEADER

        return None  # type: ignore[unreachable]

    async def _publish_resolved(
        self,
        approval: Approval,
        state: ApprovalState,
        *,
        chatroom_id: uuid.UUID | None,
    ) -> None:
        payload = {
            "approval_id": str(approval.id),
            "state": state.value,
            "mode": approval.mode.value,
        }
        if chatroom_id:
            await Publisher(room_channel(chatroom_id)).emit(
                "approval.resolved",
                payload,
            )
        await Publisher(workflow_channel(approval.workflow_run_id)).emit(
            "approval.resolved",
            payload,
        )

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def get_approval(self, approval_id: uuid.UUID) -> Approval | None:
        return await self._approvals.get(approval_id)

    async def resolve_project(self, approval_id: uuid.UUID) -> uuid.UUID | None:
        """Project owning an approval (via its workflow run) — authz helper (API-2)."""
        return await self._approvals.get_project_id(approval_id)

    async def resolve_run_project(self, workflow_run_id: uuid.UUID) -> uuid.UUID | None:
        """Project owning a workflow run — authz helper (API-2)."""
        return await self._approvals.project_for_run(workflow_run_id)

    async def get_votes(self, approval_id: uuid.UUID) -> list[ApprovalVote]:
        return await self._votes.list_for_approval(approval_id)

    async def list_for_run(self, workflow_run_id: uuid.UUID) -> list[Approval]:
        return await self._approvals.list_for_workflow_run(workflow_run_id)


__all__ = ["ApprovalService"]
