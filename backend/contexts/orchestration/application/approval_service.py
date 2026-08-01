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

from contexts.agents.interfaces.facade import AgentsFacade
from contexts.conversation.interfaces import room_channel
from contexts.orchestration.application.agent_capability import agent_has_capability
from contexts.orchestration.domain.errors import ApprovalCapabilityDenied
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
from contexts.workflow.interfaces import workflow_channel
from shared_kernel import audit
from shared_kernel.realtime.pubsub import Publisher

_log = logging.getLogger(__name__)


class ApprovalService:
    """Application-level approval gate orchestration (G.6)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._approvals = ApprovalRepository(db)
        self._votes = ApprovalVoteRepository(db)
        self._agents = AgentsFacade(db)

    # ------------------------------------------------------------------
    # Create gate
    # ------------------------------------------------------------------

    async def create_gate(
        self,
        *,
        workflow_run_id: uuid.UUID,
        config: ApprovalGateConfig,
        chatroom_id: uuid.UUID | None = None,
        node_id: str | None = None,
    ) -> Approval:
        """Create a gate using a room already validated against the run's project.

        The room is intentionally transient: approvals do not persist it, so this
        context cannot independently re-derive or re-check its scope.

        Every externally-visible effect (WS publishes, approver notifies, timeout
        arm, approver-turn dispatch) is deferred to a single post-commit
        ``approval_gate_announce`` job that re-reads the row first, so nothing
        escapes before the row is durable (F-18). ``node_id`` and
        ``config.question`` are not persisted on the row, so they ride on the
        enqueue for the announce payloads.

        Raises ``ApprovalCapabilityDenied`` (R15.10a) if any named approver —
        the leader included, since the executor folds it into ``config.approvers``
        — lacks ``workflow_capabilities.can_approve``, before this method's insert
        or the post-commit announce enqueue that leads to approver-turn spend.
        Rejects the whole gate rather than dropping ineligible approvers, since a
        reduced approver list silently changes the majority/consensus tally
        denominator (Q-6).
        """
        ineligible_ids: list[uuid.UUID] = []
        for approver_id in config.approvers:
            agent = await self._agents.get_agent(approver_id)
            if not agent_has_capability(agent, "can_approve"):
                ineligible_ids.append(approver_id)

        if ineligible_ids:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="approval.forbidden",
                    resource_type="workflow_run",
                    resource_id=workflow_run_id,
                    metadata={
                        "workflow_run_id": str(workflow_run_id),
                        "ineligible_agent_ids": [str(a) for a in ineligible_ids],
                        "reason": "missing_can_approve_capability",
                    },
                ),
            )
            raise ApprovalCapabilityDenied(
                "approval gate rejected: agent(s) "
                f"{', '.join(str(a) for a in ineligible_ids)} lack "
                "workflow_capabilities.can_approve"
            )

        approval_id = uuid.uuid4()
        approval = await self._approvals.insert(
            id=approval_id,
            workflow_run_id=workflow_run_id,
            mode=config.mode,
            leader_agent_id=config.leader_agent_id,
            approver_agent_ids=list(config.approvers),
            timeout_seconds=config.timeout_seconds,
            chatroom_id=chatroom_id,
        )

        # Audit is a DB write in this same transaction, so it commits (or rolls
        # back) atomically with the row — it is not a pre-commit escape.
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

        # Load-bearing enqueue: if it fails, gate creation fails and the caller's
        # transaction rolls back (``shared_kernel.queue.enqueue`` raises). An
        # orphaned enqueue after a rollback is a harmless no-op — the announce
        # job re-reads, finds no row, and gives up without any effect.
        from shared_kernel.queue import enqueue

        await enqueue(
            "approval_gate_announce",
            str(approval_id),
            str(chatroom_id) if chatroom_id else None,
            node_id,
            config.question,
        )

        return approval

    async def announce_gate(
        self,
        approval_id: uuid.UUID,
        *,
        chatroom_id: uuid.UUID | None = None,
        node_id: str | None = None,
        question: str | None = None,
    ) -> bool:
        """Post-commit announcement of a created gate (F-18).

        Re-reads the row so no effect fires unless the row is durable. Returns
        ``False`` when the row is not (yet) visible so the caller can retry
        within budget; ``True`` once announced (or already resolved by a fast
        vote, in which case there is nothing left to announce).
        """
        approval = await self._approvals.get(approval_id)
        if approval is None:
            return False
        if approval.state != ApprovalState.PENDING:
            return True

        if chatroom_id:
            await Publisher(room_channel(chatroom_id)).emit(
                "approval.requested",
                {
                    "approval_id": str(approval.id),
                    "workflow_run_id": str(approval.workflow_run_id),
                    "mode": approval.mode.value,
                    "leader_agent_id": str(approval.leader_agent_id),
                    "approver_agent_ids": [str(a) for a in approval.approver_agent_ids],
                    "timeout_seconds": approval.timeout_seconds,
                    "question": question,
                },
            )

        # Single workflow-channel event carrying node_id and question (Q-5 folds
        # in the executor's former duplicate publish).
        await Publisher(workflow_channel(approval.workflow_run_id)).emit(
            "approval.requested",
            {
                "approval_id": str(approval.id),
                "node_id": node_id,
                "question": question,
            },
        )

        await self._notify_and_arm(approval=approval, chatroom_id=chatroom_id, question=question)
        return True

    async def _notify_and_arm(
        self,
        *,
        approval: Approval,
        chatroom_id: uuid.UUID | None,
        question: str | None,
    ) -> None:
        from datetime import timedelta

        from contexts.orchestration.infrastructure import pending_notify
        from shared_kernel.queue import enqueue

        note = {
            "kind": "approval_request",
            "approval_id": str(approval.id),
            "mode": approval.mode.value,
            "workflow_run_id": str(approval.workflow_run_id),
            "chatroom_id": str(chatroom_id) if chatroom_id else None,
            # What is being voted on — without it the approver only sees an
            # opaque approval_id and cannot make an informed decision.
            "question": question,
        }
        # Arm the timeout FIRST. It is the gate's liveness backstop (in
        # MAJORITY/CONSENSUS a single silent approver otherwise parks the run
        # forever) and is NOT best-effort — if it cannot be armed, the announce
        # job fails and arq retries it. Doing it before the approver jobs means a
        # failed arm leaves no orphaned drive_approver_turn jobs behind.
        await enqueue(
            "approval_timeout",
            str(approval.id),
            str(chatroom_id) if chatroom_id else None,
            _defer_by=timedelta(seconds=approval.timeout_seconds),
        )
        for approver in approval.approver_agent_ids:
            try:
                await pending_notify.push(approver, dict(note))
                # Pending notifies are only drained at the approver's *next*
                # turn, and nothing else causes one for a headless approver —
                # without this the gate always falls to the timeout port. Drive
                # one headless turn per approver; the drained note supplies the
                # cast_approval_vote tool. No dispatch delay is needed: the
                # announce job already runs post-commit, so the row is visible.
                await enqueue(
                    "drive_approver_turn",
                    str(approver),
                    str(approval.id),
                    str(chatroom_id) if chatroom_id else None,
                )
            except Exception:
                _log.warning(
                    "approval %s: approver %s notify/turn dispatch failed",
                    approval.id,
                    approver,
                    exc_info=True,
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

        state = await self._resolve_state(approval)
        await self._db.commit()
        if state is not None:
            await self._emit_resolution_effects(approval, state, chatroom_id=chatroom_id)
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
            refreshed = await self._approvals.get(approval_id)
            return refreshed.state if refreshed else None

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
        await self._db.commit()

        APPROVAL_RESOLUTIONS.labels(
            mode=approval.mode.value,
            outcome=resolved_state.value,
        ).inc()
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

    async def _resolve_state(
        self,
        approval: Approval,
    ) -> ApprovalState | None:
        """Evaluate votes and CAS the gate to a resolved state (DB only).

        Returns the resolved state, or None if no resolution condition is met
        or the gate was already resolved by a concurrent path.
        """
        votes = await self._votes.list_for_approval(approval.id)
        resolved_state = self._evaluate_votes(approval, votes)
        if resolved_state is None:
            return None

        if not await self._approvals.update_state(approval.id, resolved_state):
            return None

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
        return resolved_state

    async def _emit_resolution_effects(
        self,
        approval: Approval,
        state: ApprovalState,
        *,
        chatroom_id: uuid.UUID | None = None,
    ) -> None:
        """Post-commit side effects: metrics, WS publish, workflow resume."""
        APPROVAL_RESOLUTIONS.labels(
            mode=approval.mode.value,
            outcome=state.value,
        ).inc()
        # Best-effort (code-review finding): the ballot is already committed
        # by the time this runs, so a publish failure here must not propagate
        # past cast_vote — a caller like ToolRegistry's cast_approval_vote
        # tool relies on cast_vote returning normally to mark the ballot as
        # cast. Mirrors _enqueue_workflow_resume's own best-effort posture.
        try:
            await self._publish_resolved(approval, state, chatroom_id=chatroom_id)
        except Exception:
            _log.warning("approval %s: resolved-event publish failed", approval.id, exc_info=True)
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

    async def list_for_chatroom(self, chatroom_id: uuid.UUID) -> list[Approval]:
        return await self._approvals.list_for_chatroom(chatroom_id)

    async def list_for_chatroom_with_votes(
        self,
        chatroom_id: uuid.UUID,
    ) -> list[tuple[Approval, list[ApprovalVote]]]:
        """Room-scoped approvals paired with their votes, one batched votes
        query for the whole page rather than one per approval (F-13)."""
        approvals = await self._approvals.list_for_chatroom(chatroom_id)
        votes_by_approval = await self._votes.list_for_approvals([a.id for a in approvals])
        return [(a, votes_by_approval.get(a.id, [])) for a in approvals]


__all__ = ["ApprovalService"]
