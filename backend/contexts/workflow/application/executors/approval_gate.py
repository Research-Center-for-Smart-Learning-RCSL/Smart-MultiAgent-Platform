"""approval_gate executor — block until approvers vote."""

from __future__ import annotations

import json
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors.registry import register
from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)
from contexts.workflow.sel.template import interpolate
from shared_kernel import audit


@register(NodeType.APPROVAL_GATE)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config

    variables = {
        **ctx.variables,
        "__trigger__": ctx.trigger_payload,
        "__ctx__": {"run_id": str(ctx.run_id), "workflow_id": str(ctx.workflow_id)},
    }
    question = interpolate(config.get("question_template", ""), variables)

    try:
        from contexts.orchestration.domain.models import ApprovalGateConfig, ApprovalMode
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        facade = OrchestrationFacade(db)

        # The dataclass field is ``approvers`` (not ``approver_agent_ids``) and
        # ``mode`` is an ``ApprovalMode`` enum, not a raw string — passing either
        # wrongly raised before this node could ever park (the bug the deleted
        # ``# type: ignore[call-arg]`` was masking). ``__post_init__`` also
        # requires the leader to be among the approvers, so fold it in: a schema
        # may legitimately list the leader only in ``leader_agent_id``.
        raw_leader = config.get("leader_agent_id")
        if not raw_leader:
            raise ValueError("approval_gate requires leader_agent_id in config")
        leader_id = uuid.UUID(raw_leader)
        approvers = [uuid.UUID(a) for a in config.get("approvers", [])]
        if leader_id not in approvers:
            approvers.append(leader_id)
        raw_mode = config.get("mode")
        if not raw_mode:
            raise ValueError("approval_gate requires mode in config")
        timeout_seconds = config.get("timeout_seconds", 1800)
        gate_config = ApprovalGateConfig(
            mode=ApprovalMode(raw_mode),
            leader_agent_id=leader_id,
            approvers=tuple(approvers),
            timeout_seconds=timeout_seconds,
            # Thread the interpolated question through to the approvers'
            # pending-notify payloads so voters know what they decide on.
            question=question or None,
        )

        room_supplied = False
        raw_room = None
        if "chatroom_id" in config:
            raw_room = config["chatroom_id"]
            room_supplied = True
        elif "chatroom_id" in ctx.trigger_payload:
            raw_room = ctx.trigger_payload["chatroom_id"]
            room_supplied = True

        room_id = None
        if room_supplied:
            rejection_reason: str | None
            try:
                room_id = uuid.UUID(raw_room)
            except (ValueError, AttributeError, TypeError):
                rejection_reason = "approval_gate chatroom_id must be a valid UUID"
            else:
                from contexts.conversation.interfaces.facade import ConversationFacade

                room_project_id = await ConversationFacade(db).lock_live_chatroom_scope(room_id)
                if ctx.project_id is None:
                    rejection_reason = "approval_gate run project could not be resolved"
                elif room_project_id is None:
                    rejection_reason = "approval_gate chatroom_id is unknown or deleted"
                elif room_project_id != ctx.project_id:
                    rejection_reason = "approval_gate chatroom_id is outside the run project"
                else:
                    rejection_reason = None

            if rejection_reason is not None:
                await audit.emit(
                    db,
                    audit.AuditEvent(
                        action="approval.gate_room_rejected",
                        resource_type="workflow_run",
                        resource_id=ctx.run_id,
                        metadata={
                            "workflow_run_id": str(ctx.run_id),
                            "node_id": node.id,
                            "requested_chatroom_id": str(raw_room),
                            "project_id": str(ctx.project_id) if ctx.project_id else None,
                            "reason": rejection_reason,
                        },
                    ),
                )
                raise ValueError(rejection_reason)

        approval = await facade.create_approval_gate(
            workflow_run_id=ctx.run_id,
            config=gate_config,
            chatroom_id=room_id,
            node_id=node.id,
        )

        # Register the resume claim key so approval resolution (vote or timeout)
        # can find this parked node and drive ``resume_at_port`` (K.4). Mirrors
        # the wait_for_event ``wf:wait:*`` contract; the value carries the
        # (run_id, node_id) the resolver resumes. TTL outlives the gate timeout
        # plus a grace window so a late resolution can still claim it.
        from shared_kernel.auth.clients import get_redis

        redis = get_redis()
        await redis.set(
            f"wf:approval:{approval.id}",
            json.dumps({"run_id": str(ctx.run_id), "node_id": node.id}),
            ex=int(timeout_seconds) + 300,
        )

        # The approval.requested workflow-channel event (carrying node_id and
        # question) is now published post-commit by the announce job (Q-5), so
        # the executor no longer emits its own duplicate here.

        # Park — the dispatcher will resume this node when the approval resolves,
        # setting the actual port (approved/rejected/timeout) at that time.
        return StepOutcome(
            state=StepState.RUNNING,
            output={"approval_id": str(approval.id), "question": question},
            port="default",
            park=True,
        )

    except Exception as exc:
        return StepOutcome(
            state=StepState.FAILED,
            output={},
            port="failure",
            error=str(exc),
        )
