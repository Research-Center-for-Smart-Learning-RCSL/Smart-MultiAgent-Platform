"""instruct executor — send instruction to a target agent (§15.5)."""

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


@register(NodeType.INSTRUCT)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    config = node.config
    issuer_id = config.get("issuer_agent_id", "")
    target_id = config.get("target_agent_id", "")
    template = config.get("instruction_template", "")
    output_variable = config.get("output_variable")

    variables = {
        **ctx.variables,
        "__trigger__": ctx.trigger_payload,
        "__ctx__": {"run_id": str(ctx.run_id), "workflow_id": str(ctx.workflow_id)},
    }
    rendered = interpolate(template, variables)

    try:
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        facade = OrchestrationFacade(db)
        instruction = await facade.issue_instruct(
            issuer_agent_id=uuid.UUID(issuer_id),
            target_agent_id=uuid.UUID(target_id),
            payload={"instruction": rendered, "origin": "workflow"},
        )

        result_output = {"instruction_id": str(instruction.id)}

        if config.get("wait_for_completion", True):
            # Park until the instruction completes. Register the resume claim key
            # (instruction_id → run+node) so the A2A handler's mark_completed /
            # mark_timeout (K.3) can find this parked node and drive
            # resume_at_port (K.4). Without this the node parked forever. Arm a
            # deferred deadline job keyed by the same instruction_id so a target
            # that never answers eventually frees the run at the failure port —
            # deferred well past commit, mirroring the approval-gate timeout arm.
            import json
            from contextlib import suppress
            from datetime import timedelta

            from shared_kernel.auth.clients import get_redis
            from shared_kernel.queue import enqueue

            timeout_seconds = int(config.get("completion_timeout_seconds", 120))
            redis = get_redis()
            await redis.set(
                f"wf:instruct:{instruction.id}",
                json.dumps({"run_id": str(ctx.run_id), "node_id": node.id}),
                ex=timeout_seconds + 300,
            )
            # Best-effort: if the deadline job can't be armed, the A2A path's
            # mark_timeout still resumes the run on a failed/absent reply.
            with suppress(Exception):
                await enqueue(
                    "workflow_instruct_timeout",
                    str(instruction.id),
                    _defer_by=timedelta(seconds=timeout_seconds),
                )
            if output_variable:
                ctx.variables[output_variable] = str(instruction.id)
            return StepOutcome(
                state=StepState.RUNNING,
                output=result_output,
                port="success",
                park=True,
            )

        if output_variable:
            ctx.variables[output_variable] = str(instruction.id)
        return StepOutcome(
            state=StepState.SUCCEEDED,
            output=result_output,
            port="success",
        )

    except Exception as exc:
        return StepOutcome(
            state=StepState.FAILED,
            output={},
            port="failure",
            error=str(exc),
        )
