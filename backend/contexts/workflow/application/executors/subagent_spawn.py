"""subagent_spawn executor — create child agent(s) under a parent (§15.6).

W10: when wait_for_all=True the run parks and a Redis callback key is registered so
the orchestration context can resume the run when the subagent task completes.
A configurable timeout (default 1 h) is also scheduled via the engine's
pending-enqueue mechanism.

Orchestration completion hook:
  When the spawned agent instance finishes, OrchestrationFacade should look up
  wf:subagent_callback:{instance_id} and call engine.resume_at_port with the
  stored run_id/node_id/port.
"""

from __future__ import annotations

import json
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)
from contexts.workflow.sel.template import interpolate
from contexts.workflow.application.executors.registry import register

logger = logging.getLogger(__name__)

_TIMEOUT_TASK = "workflow_subagent_timeout"


@register(NodeType.SUBAGENT_SPAWN)
async def execute(ctx: RunContext, node: NodeSpec, db: AsyncSession) -> StepOutcome:
    from shared_kernel.auth.clients import get_redis

    config = node.config
    parent_agent_id = config.get("parent_agent_id", "")
    task_template = config.get("task_template", "")
    output_variable = config.get("output_variable")
    max_alive = config.get("max_alive_simultaneously", 3)
    # W10: make the timeout configurable; default 1 h matches the Arq job_timeout.
    timeout_seconds = int(config.get("timeout_seconds", 3600))

    variables = {
        **ctx.variables,
        "__trigger__": ctx.trigger_payload,
        "__ctx__": {"run_id": str(ctx.run_id), "workflow_id": str(ctx.workflow_id)},
    }
    task_desc = interpolate(task_template, variables)

    try:
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        facade = OrchestrationFacade(db)
        instance = await facade.spawn_subagent(
            parent_instance_id=ctx.run_id,
            parent_agent_id=uuid.UUID(parent_agent_id),
            task_description=task_desc,
            max_concurrent=max_alive,
        )

        result = {"instance_id": str(instance.id)}
        if output_variable:
            ctx.variables[output_variable] = str(instance.id)

        if config.get("wait_for_all", True):
            # W10: register completion callback so orchestration can wake this run.
            redis = get_redis()
            callback_key = f"wf:subagent_callback:{instance.id}"
            await redis.set(
                callback_key,
                json.dumps({
                    "run_id": str(ctx.run_id),
                    "node_id": node.id,
                    "port": "success",
                }),
                ex=timeout_seconds + 60,
            )
            logger.info(
                "run %s: spawned subagent %s, waiting for completion (timeout=%ds)",
                ctx.run_id, instance.id, timeout_seconds,
            )
            return StepOutcome(
                state=StepState.RUNNING,
                output=result,
                port="success",
                park=True,
                timeout_ms=timeout_seconds * 1_000,
                timeout_task=_TIMEOUT_TASK,
            )

        return StepOutcome(
            state=StepState.SUCCEEDED,
            output=result,
            port="success",
        )

    except Exception as exc:
        return StepOutcome(
            state=StepState.FAILED,
            output={},
            port="failure",
            error=str(exc),
        )
