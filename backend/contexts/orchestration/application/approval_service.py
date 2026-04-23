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

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.orchestration.domain.errors import ApprovalTimeoutLeader
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
from shared_kernel import audit
from shared_kernel.realtime.pubsub import Publisher, room_channel, workflow_channel


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
            await pub.emit("approval.requested", {
                "approval_id": str(approval_id),
                "mode": config.mode.value,
                "leader_agent_id": str(config.leader_agent_id),
                "approver_agent_ids": [str(a) for a in config.approvers],
                "timeout_seconds": config.timeout_seconds,
            })

        wf_pub = Publisher(workflow_channel(workflow_run_id))
        await wf_pub.emit("approval.requested", {
            "approval_id": str(approval_id),
        })

        return approval

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
    ) -> ApprovalState:
        approval = await self._approvals.get(approval_id)
        if approval is None or approval.state != ApprovalState.PENDING:
            return approval.state if approval else ApprovalState.PENDING

        leader_votes = [
            v for v in await self._votes.list_for_approval(approval_id)
            if v.voter_agent_id == approval.leader_agent_id
        ]
        if leader_votes and leader_votes[-1].vote:
            leader_verdict = "approved"
        elif leader_votes:
            leader_verdict = "rejected"
        else:
            leader_verdict = "no_vote"

        resolved_state = ApprovalState.TIMEOUT_LEADER
        await self._approvals.update_state(approval_id, resolved_state)

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
            approval, resolved_state, chatroom_id=chatroom_id,
        )
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

        await self._approvals.update_state(approval.id, resolved_state)

        APPROVAL_RESOLUTIONS.labels(
            mode=approval.mode.value,
            outcome=resolved_state.value,
        ).inc()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="approval.resolved",
                resource_type="approval",
                resource_id=approval.id,
                metadata={
                    "state": resolved_state.value,
                    "vote_count": len(votes),
                    "approve_count": sum(1 for v in votes if v.vote),
                    "reject_count": sum(1 for v in votes if not v.vote),
                },
            ),
        )

        await self._publish_resolved(
            approval, resolved_state, chatroom_id=chatroom_id,
        )

    @staticmethod
    def _evaluate_votes(
        approval: Approval,
        votes: list[ApprovalVote],
    ) -> ApprovalState | None:
        """Pure resolution evaluation. Returns None if not yet decidable."""
        approver_set = set(approval.approver_agent_ids)
        approver_votes = [v for v in votes if v.voter_agent_id in approver_set]

        if approval.mode == ApprovalMode.SINGLE:
            leader_votes = [
                v for v in approver_votes
                if v.voter_agent_id == approval.leader_agent_id
            ]
            if not leader_votes:
                return None
            return ApprovalState.APPROVED if leader_votes[-1].vote else ApprovalState.REJECTED

        if approval.mode == ApprovalMode.MAJORITY:
            if len(approver_votes) < len(approver_set):
                return None
            approves = sum(1 for v in approver_votes if v.vote)
            rejects = len(approver_votes) - approves
            if approves > rejects:
                return ApprovalState.APPROVED
            if rejects > approves:
                return ApprovalState.REJECTED
            # Tie: leader breaks it.
            leader_votes = [
                v for v in approver_votes
                if v.voter_agent_id == approval.leader_agent_id
            ]
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
            return None

        return None

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
                "approval.resolved", payload,
            )
        await Publisher(workflow_channel(approval.workflow_run_id)).emit(
            "approval.resolved", payload,
        )

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    async def get_approval(self, approval_id: uuid.UUID) -> Approval | None:
        return await self._approvals.get(approval_id)

    async def get_votes(self, approval_id: uuid.UUID) -> list[ApprovalVote]:
        return await self._votes.list_for_approval(approval_id)

    async def list_for_run(self, workflow_run_id: uuid.UUID) -> list[Approval]:
        return await self._approvals.list_for_workflow_run(workflow_run_id)


__all__ = ["ApprovalService"]
