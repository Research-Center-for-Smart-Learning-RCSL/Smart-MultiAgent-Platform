"""approval_gate executor — block until approvers vote."""

from __future__ import annotations

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
from shared_kernel.realtime.pubsub import Publisher, workflow_channel


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
        from contexts.orchestration.domain.models import ApprovalGateConfig
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        facade = OrchestrationFacade(db)
        gate_config = ApprovalGateConfig(  # type: ignore[call-arg]
            mode=config["mode"],
            leader_agent_id=uuid.UUID(config["leader_agent_id"]),
            approver_agent_ids=[uuid.UUID(a) for a in config.get("approvers", [])],
            timeout_seconds=config.get("timeout_seconds", 1800),
        )

        approval = await facade.create_approval_gate(
            workflow_run_id=ctx.run_id,
            config=gate_config,
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
