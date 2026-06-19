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
from contexts.workflow.infrastructure.channels import workflow_channel
from shared_kernel.realtime.pubsub import Publisher


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
        leader_id = uuid.UUID(config["leader_agent_id"])
        approvers = [uuid.UUID(a) for a in config.get("approvers", [])]
        if leader_id not in approvers:
            approvers.append(leader_id)
        timeout_seconds = config.get("timeout_seconds", 1800)
        gate_config = ApprovalGateConfig(
            mode=ApprovalMode(config["mode"]),
            leader_agent_id=leader_id,
            approvers=tuple(approvers),
            timeout_seconds=timeout_seconds,
            # Thread the interpolated question through to the approvers'
            # pending-notify payloads so voters know what they decide on.
            question=question or None,
        )

        approval = await facade.create_approval_gate(
            workflow_run_id=ctx.run_id,
            config=gate_config,
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

        pub = Publisher(workflow_channel(ctx.run_id))
        await pub.emit(
            "approval.requested",
            {
                "approval_id": str(approval.id),
                "node_id": node.id,
                "question": question,
            },
        )

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
            port="timeout",
            error=str(exc),
        )
