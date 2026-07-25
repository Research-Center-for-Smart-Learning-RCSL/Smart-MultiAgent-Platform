"""instruct executor — send instruction to a target agent (§15.5)."""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.workflow.application.executors.registry import register
from contexts.workflow.domain.claim_ttl import GATE_CLAIM_GRACE_S, initial_claim_ttl
from contexts.workflow.domain.models import (
    NodeSpec,
    NodeType,
    RunContext,
    StepOutcome,
    StepState,
)
from contexts.workflow.sel.template import interpolate


def _as_uuid(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except (ValueError, TypeError):
        return None


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

    # F-25: if this run was started by an inbound instruct, its chain rode in on
    # the trigger payload; continue it so an A->B->A chain trips the loop guard
    # instead of minting a fresh chain each hop.
    trigger = ctx.trigger_payload or {}
    inbound_chain_id = _as_uuid(trigger.get("chain_id"))
    inbound_parent_path = tuple(
        u for u in (_as_uuid(p) for p in (trigger.get("chain_path") or [])) if u is not None
    )
    # F-4: extend the trigger-causality chain with this workflow's id before the
    # INSTRUCT leaves the run, so an a2a_event trigger listening for `instruct`
    # on the target cannot re-fire a workflow already on the chain.
    out_trigger_depth = int(trigger.get("trigger_depth", 0) or 0) + 1
    out_trigger_path = [*(trigger.get("trigger_path") or []), str(ctx.workflow_id)]

    try:
        from contexts.orchestration.interfaces.facade import OrchestrationFacade

        facade = OrchestrationFacade(db)
        instruction = await facade.issue_instruct(
            issuer_agent_id=uuid.UUID(issuer_id),
            target_agent_id=uuid.UUID(target_id),
            payload={"instruction": rendered, "origin": "workflow"},
            workflow_run_id=ctx.run_id,
            chain_id=inbound_chain_id,
            parent_path=inbound_parent_path,
            trigger_depth=out_trigger_depth,
            trigger_path=out_trigger_path,
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
            from datetime import timedelta

            from shared_kernel.auth.clients import get_redis
            from shared_kernel.queue import enqueue

            timeout_seconds = int(config.get("completion_timeout_seconds", 120))
            redis = get_redis()
            await redis.set(
                f"wf:instruct:{instruction.id}",
                json.dumps({"run_id": str(ctx.run_id), "node_id": node.id}),
                ex=initial_claim_ttl(timeout_seconds, GATE_CLAIM_GRACE_S),
            )
            # Best-effort: if the deadline job can't be armed, the A2A path's
            # mark_timeout still resumes the run on a failed/absent reply. But an
            # unarmed deadline must not be silent (F-16 aggravating factor) — an
            # unarmed deadline is indistinguishable from one that hasn't fired yet.
            try:
                await enqueue(
                    "workflow_instruct_timeout",
                    str(instruction.id),
                    _defer_by=timedelta(seconds=timeout_seconds),
                )
            except Exception:
                logger.bind(
                    instruction_id=str(instruction.id),
                    run_id=str(ctx.run_id),
                    node_id=node.id,
                ).warning("instruct deadline arm failed; node parked without an active deadline")
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
