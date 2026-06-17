"""Orchestration facade — public surface for other contexts.

Workflow (H) and Conversation (F) contexts use this facade to:
- Send A2A messages
- Evaluate wake-up triggers
- Modify wake-up config (on behalf of agents)
- Create approval gates and cast votes (G.6)
- Issue instruct messages (G.7)
- Spawn and destroy sub-agents (G.8)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.orchestration.application.a2a_service import A2AService
from contexts.orchestration.application.approval_service import ApprovalService
from contexts.orchestration.application.instruct_service import InstructService
from contexts.orchestration.application.subagent_service import SubagentService
from contexts.orchestration.application.wakeup_service import WakeupService
from contexts.orchestration.domain.models import (
    A2AEnvelope,
    AgentInstance,
    Approval,
    ApprovalGateConfig,
    ApprovalState,
    ApprovalVote,
    Instruction,
    WakeupConfig,
)


class OrchestrationFacade:
    def __init__(self, db: AsyncSession) -> None:
        self._a2a = A2AService(db)
        self._wakeup = WakeupService(db)
        self._approval = ApprovalService(db)
        self._instruct = InstructService(db)
        self._subagent = SubagentService(db)

    # -- A2A --

    async def send_a2a(
        self,
        *,
        envelope: A2AEnvelope,
        caller_invocation_context_id: uuid.UUID | None = None,
        callee_attached_context_ids: frozenset[uuid.UUID] | None = None,
    ) -> str:
        return await self._a2a.send(
            envelope=envelope,
            caller_invocation_context_id=caller_invocation_context_id,
            callee_attached_context_ids=callee_attached_context_ids,
        )

    async def a2a_call(
        self,
        *,
        from_agent_id: uuid.UUID | None,
        to_agent_id: uuid.UUID,
        payload: dict[str, Any],
        workflow_run_id: uuid.UUID | None = None,
        timeout_seconds: float = 60.0,
        caller_invocation_context_id: uuid.UUID | None = None,
        callee_attached_context_ids: frozenset[uuid.UUID] | None = None,
    ) -> dict[str, Any]:
        return await self._a2a.call(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            payload=payload,
            workflow_run_id=workflow_run_id,
            timeout_seconds=timeout_seconds,
            caller_invocation_context_id=caller_invocation_context_id,
            callee_attached_context_ids=callee_attached_context_ids,
        )

    async def a2a_notify(
        self,
        *,
        from_agent_id: uuid.UUID,
        to_agent_id: uuid.UUID,
        payload: dict[str, Any],
        workflow_run_id: uuid.UUID | None = None,
        caller_invocation_context_id: uuid.UUID | None = None,
        callee_attached_context_ids: frozenset[uuid.UUID] | None = None,
    ) -> str:
        return await self._a2a.notify(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            payload=payload,
            workflow_run_id=workflow_run_id,
            caller_invocation_context_id=caller_invocation_context_id,
            callee_attached_context_ids=callee_attached_context_ids,
        )

    async def a2a_reply(
        self,
        *,
        from_agent_id: uuid.UUID,
        to_agent_id: uuid.UUID,
        correlation_id: uuid.UUID,
        payload: dict[str, Any],
        workflow_run_id: uuid.UUID | None = None,
    ) -> str:
        return await self._a2a.reply(
            from_agent_id=from_agent_id,
            to_agent_id=to_agent_id,
            correlation_id=correlation_id,
            payload=payload,
            workflow_run_id=workflow_run_id,
        )

    # -- Wake-up --

    async def on_message_created(
        self,
        *,
        room_id: uuid.UUID,
        sender_is_user: bool,
        agent_ids: list[uuid.UUID],
    ) -> list[uuid.UUID]:
        return await self._wakeup.on_message_created(
            room_id=room_id,
            sender_is_user=sender_is_user,
            agent_ids=agent_ids,
        )

    async def on_presence_changed(
        self,
        *,
        room_id: uuid.UUID,
        agent_ids: list[uuid.UUID],
        has_live_users: bool,
    ) -> None:
        await self._wakeup.on_presence_changed(
            room_id=room_id,
            agent_ids=agent_ids,
            has_live_users=has_live_users,
        )

    async def on_agent_message_sent(
        self,
        *,
        agent_id: uuid.UUID,
        room_id: uuid.UUID,
    ) -> int:
        """Track a completed agent reply for autostop (R15.03/R15.04).

        Called by the turn-trigger wiring (K.3) after an agent turn persists a
        reply. Returns the new consecutive agent-only round count; a user
        message resets it via :meth:`on_message_created`.
        """
        return await self._wakeup.on_agent_message_sent(
            agent_id=agent_id,
            room_id=room_id,
        )

    async def update_wakeup(
        self,
        *,
        agent_id: uuid.UUID,
        every_n_messages: int | None = None,
        silence_minutes: int | None = None,
        actor_agent_id: uuid.UUID | None = None,
    ) -> WakeupConfig:
        return await self._wakeup.update_wakeup(
            agent_id=agent_id,
            every_n_messages=every_n_messages,
            silence_minutes=silence_minutes,
            actor_agent_id=actor_agent_id,
        )

    async def refresh_wakeup_config(self, agent_id: uuid.UUID) -> bool:
        return await self._wakeup.refresh_wakeup_config(agent_id)

    # -- Approval (G.6) --

    async def create_approval_gate(
        self,
        *,
        workflow_run_id: uuid.UUID,
        config: ApprovalGateConfig,
        chatroom_id: uuid.UUID | None = None,
    ) -> Approval:
        return await self._approval.create_gate(
            workflow_run_id=workflow_run_id,
            config=config,
            chatroom_id=chatroom_id,
        )

    async def cast_approval_vote(
        self,
        *,
        approval_id: uuid.UUID,
        voter_agent_id: uuid.UUID,
        vote: bool,
        rationale: str | None = None,
        chatroom_id: uuid.UUID | None = None,
    ) -> ApprovalVote:
        return await self._approval.cast_vote(
            approval_id=approval_id,
            voter_agent_id=voter_agent_id,
            vote=vote,
            rationale=rationale,
            chatroom_id=chatroom_id,
        )

    async def handle_approval_timeout(
        self,
        approval_id: uuid.UUID,
        *,
        chatroom_id: uuid.UUID | None = None,
    ) -> ApprovalState | None:
        return await self._approval.handle_timeout(
            approval_id,
            chatroom_id=chatroom_id,
        )

    async def get_approval(self, approval_id: uuid.UUID) -> Approval | None:
        return await self._approval.get_approval(approval_id)

    async def get_approval_votes(self, approval_id: uuid.UUID) -> list[ApprovalVote]:
        return await self._approval.get_votes(approval_id)

    async def list_approvals_for_run(
        self,
        workflow_run_id: uuid.UUID,
    ) -> list[Approval]:
        return await self._approval.list_for_run(workflow_run_id)

    # -- Instruct (G.7) --

    async def issue_instruct(
        self,
        *,
        issuer_agent_id: uuid.UUID,
        target_agent_id: uuid.UUID,
        payload: dict[str, Any],
        chain_id: uuid.UUID | None = None,
        parent_path: tuple[uuid.UUID, ...] = (),
        wakeup_started_at: datetime | None = None,
        max_chain_depth: int = 5,
        max_per_wakeup: int = 5,
        max_chain_seconds: int = 120,
    ) -> Instruction:
        return await self._instruct.issue(
            issuer_agent_id=issuer_agent_id,
            target_agent_id=target_agent_id,
            payload=payload,
            chain_id=chain_id,
            parent_path=parent_path,
            wakeup_started_at=wakeup_started_at,
            max_chain_depth=max_chain_depth,
            max_per_wakeup=max_per_wakeup,
            max_chain_seconds=max_chain_seconds,
        )

    async def mark_instruct_delivered(self, instruction_id: uuid.UUID) -> None:
        await self._instruct.mark_delivered(instruction_id)

    async def mark_instruct_completed(self, instruction_id: uuid.UUID) -> None:
        await self._instruct.mark_completed(instruction_id)

    async def mark_instruct_timeout(self, instruction_id: uuid.UUID) -> None:
        await self._instruct.mark_timeout(instruction_id)

    async def get_instruction(self, instruction_id: uuid.UUID) -> Instruction | None:
        return await self._instruct.get_instruction(instruction_id)

    async def list_instructions_for_chain(
        self,
        chain_id: uuid.UUID,
    ) -> list[Instruction]:
        return await self._instruct.list_for_chain(chain_id)

    # -- Sub-agent (G.8) --

    async def ensure_subagent_root(
        self,
        *,
        parent_agent_id: uuid.UUID,
        workflow_run_id: uuid.UUID,
    ) -> AgentInstance:
        """Get-or-create the synthetic root instance for a workflow run.

        Used by the workflow ``subagent_spawn`` executor, which has a parent
        agent definition but no parent agent *instance* to spawn under.
        """
        return await self._subagent.ensure_root_instance(
            agent_id=parent_agent_id,
            workflow_run_id=workflow_run_id,
        )

    async def spawn_subagent(
        self,
        *,
        parent_instance_id: uuid.UUID,
        parent_agent_id: uuid.UUID,
        task_description: str,
        chatroom_id: uuid.UUID | None = None,
        max_concurrent: int = 3,
    ) -> AgentInstance:
        return await self._subagent.spawn(
            parent_instance_id=parent_instance_id,
            parent_agent_id=parent_agent_id,
            task_description=task_description,
            chatroom_id=chatroom_id,
            max_concurrent=max_concurrent,
        )

    async def destroy_subagent(
        self,
        instance_id: uuid.UUID,
        *,
        state: str = "completed",
    ) -> None:
        await self._subagent.destroy(instance_id, state=state)

    async def list_subagent_children(
        self,
        parent_id: uuid.UUID,
    ) -> list[AgentInstance]:
        return await self._subagent.list_children(parent_id)

    async def cleanup_expired_instances(self, retention_days: int = 30) -> int:
        return await self._subagent.cleanup_expired(retention_days)


__all__ = ["OrchestrationFacade"]
